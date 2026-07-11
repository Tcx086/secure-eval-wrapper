BEGIN;

-- Phase 6 first independent audit repairs. Migrations 0001 through 0013 remain immutable.

ALTER TABLE monitoring.incidents
    ADD COLUMN check_name TEXT;
UPDATE monitoring.incidents SET check_name = reason_code WHERE check_name IS NULL;
ALTER TABLE monitoring.incidents ALTER COLUMN check_name SET NOT NULL;
CREATE INDEX idx_phase6_incidents_check_evidence
    ON monitoring.incidents (category, component, check_name, monitored_identity, state);

ALTER TABLE monitoring.fix_messages
    DROP CONSTRAINT IF EXISTS fix_messages_fix_session_id_direction_msg_seq_num_key,
    ALTER COLUMN msg_type DROP NOT NULL,
    ALTER COLUMN msg_seq_num DROP NOT NULL,
    ALTER COLUMN sending_time_utc DROP NOT NULL,
    ALTER COLUMN body_length DROP NOT NULL,
    ALTER COLUMN checksum DROP NOT NULL,
    ALTER COLUMN business_identity_sha256 DROP NOT NULL,
    ADD COLUMN rejection_code TEXT,
    ADD COLUMN replay_identity_sha256 CHAR(64),
    ADD CONSTRAINT phase6_fix_message_validation_shape_check CHECK (
        (validation_status = 'valid' AND msg_type IS NOT NULL AND msg_seq_num IS NOT NULL
         AND sending_time_utc IS NOT NULL AND body_length IS NOT NULL AND checksum IS NOT NULL
         AND business_identity_sha256 IS NOT NULL AND rejection_reason IS NULL)
        OR
        (validation_status = 'rejected' AND rejection_reason IS NOT NULL AND rejection_code IS NOT NULL)
    ),
    ADD CONSTRAINT phase6_fix_replay_hash_check CHECK (
        replay_identity_sha256 IS NULL OR replay_identity_sha256 ~ '^[0-9a-f]{64}$'
    );
UPDATE monitoring.fix_messages
SET replay_identity_sha256 = business_identity_sha256
WHERE validation_status = 'valid' AND replay_identity_sha256 IS NULL;
CREATE UNIQUE INDEX uq_phase6_fix_valid_sequence
    ON monitoring.fix_messages (fix_session_id, direction, msg_seq_num)
    WHERE validation_status = 'valid';
CREATE UNIQUE INDEX uq_phase6_fix_rejected_observation
    ON monitoring.fix_messages (fix_session_id, direction, raw_message_sha256, rejection_code)
    WHERE validation_status = 'rejected';
CREATE INDEX idx_phase6_fix_replay_identity
    ON monitoring.fix_messages (fix_session_id, replay_identity_sha256)
    WHERE replay_identity_sha256 IS NOT NULL;

ALTER TABLE monitoring.fix_session_events
    ADD COLUMN transition_sequence BIGINT,
    ADD COLUMN previous_event_sha256 CHAR(64),
    ADD CONSTRAINT phase6_fix_event_previous_hash_check CHECK (
        previous_event_sha256 IS NULL OR previous_event_sha256 ~ '^[0-9a-f]{64}$'
    );
WITH numbered AS (
    SELECT fix_session_event_id,
           row_number() OVER (PARTITION BY fix_session_id ORDER BY event_time_utc, sequence_number, fix_session_event_id) - 1 AS ordinal
    FROM monitoring.fix_session_events
    WHERE fix_session_id IS NOT NULL
)
UPDATE monitoring.fix_session_events AS event
SET transition_sequence = numbered.ordinal
FROM numbered
WHERE event.fix_session_event_id = numbered.fix_session_event_id;
UPDATE monitoring.fix_session_events SET transition_sequence = 0 WHERE transition_sequence IS NULL;
ALTER TABLE monitoring.fix_session_events
    ALTER COLUMN transition_sequence SET NOT NULL,
    ADD CONSTRAINT phase6_fix_event_transition_sequence_nonnegative CHECK (transition_sequence >= 0),
    ADD CONSTRAINT uq_phase6_fix_event_transition_sequence UNIQUE (fix_session_id, transition_sequence),
    ADD CONSTRAINT uq_phase6_fix_event_session_identity UNIQUE (fix_session_id, fix_session_event_id);

ALTER TABLE monitoring.fix_sessions
    ADD COLUMN test_request_grace_seconds NUMERIC(38,9),
    ADD COLUMN disconnect_timeout_seconds NUMERIC(38,9),
    ADD COLUMN pending_test_deadline_at_utc TIMESTAMPTZ,
    ADD COLUMN test_request_grace_expired BOOLEAN NOT NULL DEFAULT FALSE,
    ADD COLUMN state_version BIGINT NOT NULL DEFAULT 0,
    ADD COLUMN previous_state_hash CHAR(64),
    ADD COLUMN last_transition_event_id UUID,
    ADD CONSTRAINT phase6_fix_session_state_version_check CHECK (state_version >= 0),
    ADD CONSTRAINT phase6_fix_session_previous_hash_check CHECK (
        previous_state_hash IS NULL OR previous_state_hash ~ '^[0-9a-f]{64}$'
    ),
    ADD CONSTRAINT phase6_fix_session_timeout_config_check CHECK (
        test_request_grace_seconds IS NULL OR
        (test_request_grace_seconds > 0 AND disconnect_timeout_seconds >= test_request_grace_seconds)
    );
UPDATE monitoring.fix_sessions
SET test_request_grace_seconds = 10,
    disconnect_timeout_seconds = 60
WHERE test_request_grace_seconds IS NULL;
UPDATE monitoring.fix_sessions
SET pending_test_request_id = CASE WHEN state = 'test_request_pending' THEN COALESCE(pending_test_request_id, 'MIGRATED-PENDING') ELSE NULL END,
    pending_test_deadline_at_utc = CASE WHEN state = 'test_request_pending' THEN COALESCE(pending_test_deadline_at_utc, updated_at_utc + INTERVAL '10 seconds') ELSE NULL END,
    test_request_grace_expired = FALSE;
ALTER TABLE monitoring.fix_sessions
    ALTER COLUMN test_request_grace_seconds SET NOT NULL,
    ALTER COLUMN disconnect_timeout_seconds SET NOT NULL,
    ADD CONSTRAINT phase6_fix_session_pending_deadline_check CHECK (
        (state = 'test_request_pending' AND pending_test_request_id IS NOT NULL
         AND pending_test_deadline_at_utc IS NOT NULL)
        OR
        (state <> 'test_request_pending' AND pending_test_request_id IS NULL
         AND pending_test_deadline_at_utc IS NULL AND test_request_grace_expired = FALSE)
    );
ALTER TABLE monitoring.fix_sessions
    ADD CONSTRAINT fk_phase6_fix_session_last_transition
    FOREIGN KEY (fix_session_id, last_transition_event_id)
    REFERENCES monitoring.fix_session_events (fix_session_id, fix_session_event_id)
    DEFERRABLE INITIALLY DEFERRED;

CREATE OR REPLACE FUNCTION monitoring.validate_fix_session_event_append()
RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
    prior_event monitoring.fix_session_events%ROWTYPE;
BEGIN
    IF NEW.fix_session_id IS NULL THEN
        RETURN NEW;
    END IF;
    SELECT * INTO prior_event
    FROM monitoring.fix_session_events
    WHERE fix_session_id = NEW.fix_session_id
    ORDER BY transition_sequence DESC
    LIMIT 1;
    IF NOT FOUND THEN
        IF NEW.transition_sequence <> 0 OR NEW.previous_event_sha256 IS NOT NULL THEN
            RAISE EXCEPTION 'first FIX session event must use ordinal zero without previous hash';
        END IF;
    ELSE
        IF NEW.transition_sequence <> prior_event.transition_sequence + 1 THEN
            RAISE EXCEPTION 'FIX session event ordinal is not append-only';
        END IF;
        IF NEW.previous_event_sha256 IS DISTINCT FROM prior_event.record_sha256 THEN
            RAISE EXCEPTION 'FIX session event previous hash does not match authoritative tail';
        END IF;
        IF NEW.prior_state IS DISTINCT FROM prior_event.new_state THEN
            RAISE EXCEPTION 'FIX session event state does not continue authoritative tail';
        END IF;
    END IF;
    RETURN NEW;
END;
$$;
CREATE TRIGGER trg_phase6_validate_fix_session_event_append
BEFORE INSERT ON monitoring.fix_session_events
FOR EACH ROW EXECUTE FUNCTION monitoring.validate_fix_session_event_append();

CREATE OR REPLACE FUNCTION monitoring.validate_fix_session_projection()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    IF TG_OP = 'INSERT' THEN
        IF NEW.state_version <> 0 OR NEW.previous_state_hash IS NOT NULL THEN
            RAISE EXCEPTION 'new FIX session projection must start at version zero without previous hash';
        END IF;
        RETURN NEW;
    END IF;
    IF NEW.state_version <> OLD.state_version + 1 THEN
        RAISE EXCEPTION 'stale FIX session projection version: expected %, got %', OLD.state_version + 1, NEW.state_version;
    END IF;
    IF NEW.previous_state_hash IS DISTINCT FROM OLD.record_sha256 THEN
        RAISE EXCEPTION 'stale FIX session projection hash';
    END IF;
    IF NEW.next_inbound_seq_num < OLD.next_inbound_seq_num OR NEW.next_outbound_seq_num < OLD.next_outbound_seq_num THEN
        RAISE EXCEPTION 'FIX session sequence numbers may not decrease';
    END IF;
    IF NOT (
        NEW.state = OLD.state OR
        (OLD.state = 'disconnected' AND NEW.state IN ('logon_pending','established')) OR
        (OLD.state = 'logon_pending' AND NEW.state IN ('established','disconnected','terminated')) OR
        (OLD.state = 'established' AND NEW.state IN ('test_request_pending','logout_pending','recovering','disconnected','terminated')) OR
        (OLD.state = 'test_request_pending' AND NEW.state IN ('established','disconnected','terminated')) OR
        (OLD.state = 'logout_pending' AND NEW.state IN ('terminated','disconnected')) OR
        (OLD.state = 'recovering' AND NEW.state IN ('established','disconnected','terminated')) OR
        (OLD.state = 'terminated' AND NEW.state = 'logon_pending')
    ) THEN
        RAISE EXCEPTION 'illegal FIX session transition from % to %', OLD.state, NEW.state;
    END IF;
    RETURN NEW;
END;
$$;
DROP TRIGGER IF EXISTS trg_phase6_validate_fix_session_projection ON monitoring.fix_sessions;
CREATE TRIGGER trg_phase6_validate_fix_session_projection
BEFORE INSERT OR UPDATE ON monitoring.fix_sessions
FOR EACH ROW EXECUTE FUNCTION monitoring.validate_fix_session_projection();

CREATE INDEX idx_phase6_fix_session_projection_version
    ON monitoring.fix_sessions (fix_session_id, state_version, record_sha256);

COMMENT ON COLUMN monitoring.fix_sessions.state_version IS
    'Optimistic version of the current-state projection; immutable fix_session_events remain authoritative.';
COMMENT ON COLUMN monitoring.fix_messages.replay_identity_sha256 IS
    'Canonical supported-field identity excluding only PossDupFlag, current SendingTime, and OrigSendingTime.';
COMMENT ON COLUMN monitoring.fix_messages.rejection_code IS
    'Typed validation rejection code for auditable raw observations that did not enter session/economic processing.';

COMMIT;
