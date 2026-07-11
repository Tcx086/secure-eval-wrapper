BEGIN;

-- Phase 6 second independent audit repairs. Migrations 0001 through 0014 remain immutable.
--
-- Every session-linked event now carries the complete mutable projection snapshot. Legacy
-- events cannot be reconstructed perfectly, so their snapshot columns are conservatively
-- backfilled and every existing session receives one deterministic, exact migration snapshot
-- event before the projection/event authority triggers become active.

ALTER TABLE monitoring.fix_session_events
    ADD COLUMN projected_next_inbound_seq_num BIGINT,
    ADD COLUMN projected_next_outbound_seq_num BIGINT,
    ADD COLUMN projected_last_inbound_at_utc TIMESTAMPTZ,
    ADD COLUMN projected_last_outbound_at_utc TIMESTAMPTZ,
    ADD COLUMN projected_pending_test_request_id TEXT,
    ADD COLUMN projected_pending_test_deadline_at_utc TIMESTAMPTZ,
    ADD COLUMN projected_test_request_grace_expired BOOLEAN;

ALTER TABLE monitoring.fix_sessions
    ADD COLUMN last_transition_sequence BIGINT,
    ADD COLUMN authoritative_event_sha256 CHAR(64),
    ADD CONSTRAINT phase6_fix_session_tail_sequence_check CHECK (
        last_transition_sequence IS NULL OR last_transition_sequence >= 0
    ),
    ADD CONSTRAINT phase6_fix_session_authoritative_hash_check CHECK (
        authoritative_event_sha256 IS NULL
        OR authoritative_event_sha256 ~ '^[0-9a-f]{64}$'
    );

-- Normalize session-linked legacy rows before repairing their pairwise chain. A missing
-- legacy record hash is replaced with an explicitly migration-scoped deterministic hash.
UPDATE monitoring.fix_session_events AS event
SET new_state = COALESCE(event.new_state, session.state),
    prior_state = COALESCE(event.prior_state, event.new_state, session.state),
    reason_code = COALESCE(NULLIF(event.reason_code, ''), 'migration_legacy_event'),
    record_sha256 = COALESCE(
        event.record_sha256,
        encode(sha256(convert_to(concat_ws('|',
            'phase6-0015-legacy-event-v1',
            event.fix_session_event_id::TEXT,
            event.fix_session_id::TEXT,
            event.transition_sequence::TEXT,
            event.event_type,
            to_char(
                event.event_time_utc AT TIME ZONE 'UTC',
                'YYYY-MM-DD"T"HH24:MI:SS.US"Z"'
            )
        ), 'UTF8')), 'hex')
    ),
    projected_next_inbound_seq_num = session.next_inbound_seq_num,
    projected_next_outbound_seq_num = session.next_outbound_seq_num,
    projected_last_inbound_at_utc = session.last_inbound_at_utc,
    projected_last_outbound_at_utc = session.last_outbound_at_utc,
    projected_pending_test_request_id = CASE
        WHEN COALESCE(event.new_state, session.state) = 'test_request_pending'
        THEN COALESCE(
            session.pending_test_request_id,
            'MIGRATED-' || event.fix_session_event_id::TEXT
        )
        ELSE NULL
    END,
    projected_pending_test_deadline_at_utc = CASE
        WHEN COALESCE(event.new_state, session.state) = 'test_request_pending'
        THEN COALESCE(
            session.pending_test_deadline_at_utc,
            event.event_time_utc,
            session.updated_at_utc
        )
        ELSE NULL
    END,
    projected_test_request_grace_expired = CASE
        WHEN COALESCE(event.new_state, session.state) = 'test_request_pending'
        THEN session.test_request_grace_expired
        ELSE FALSE
    END
FROM monitoring.fix_sessions AS session
WHERE event.fix_session_id = session.fix_session_id;

-- Rows from the original generic monitoring skeleton may not belong to a normalized session.
-- Keep them upgradeable without allowing them to act as session authority.
UPDATE monitoring.fix_session_events
SET projected_next_inbound_seq_num = COALESCE(projected_next_inbound_seq_num, 1),
    projected_next_outbound_seq_num = COALESCE(projected_next_outbound_seq_num, 1),
    projected_test_request_grace_expired = COALESCE(
        projected_test_request_grace_expired,
        FALSE
    )
WHERE fix_session_id IS NULL;

-- Migration 0014 assigned deterministic ordinals but could not reconstruct legacy previous
-- hashes. Repair both the hash pointer and prior-state link in ordinal order.
WITH ordered_events AS (
    SELECT fix_session_event_id,
           transition_sequence,
           lag(record_sha256) OVER (
               PARTITION BY fix_session_id
               ORDER BY transition_sequence
           ) AS expected_previous_event_sha256,
           lag(new_state) OVER (
               PARTITION BY fix_session_id
               ORDER BY transition_sequence
           ) AS expected_prior_state
    FROM monitoring.fix_session_events
    WHERE fix_session_id IS NOT NULL
)
UPDATE monitoring.fix_session_events AS event
SET previous_event_sha256 = CASE
        WHEN ordered.transition_sequence = 0 THEN NULL
        ELSE ordered.expected_previous_event_sha256
    END,
    prior_state = CASE
        WHEN ordered.transition_sequence = 0
        THEN COALESCE(event.prior_state, event.new_state)
        ELSE ordered.expected_prior_state
    END
FROM ordered_events AS ordered
WHERE event.fix_session_event_id = ordered.fix_session_event_id;

-- Establish a fully accurate authority boundary for every upgraded session, including a
-- session that had no events at all. The synthetic UUID and hash are deterministic and
-- migration-scoped; they do not claim to be runtime FixSessionEvent identities.
WITH authoritative_tails AS (
    SELECT DISTINCT ON (fix_session_id)
           fix_session_id,
           transition_sequence,
           new_state,
           record_sha256
    FROM monitoring.fix_session_events
    WHERE fix_session_id IS NOT NULL
    ORDER BY fix_session_id, transition_sequence DESC
),
snapshot_material AS (
    SELECT session.*,
           tail.new_state AS prior_tail_state,
           tail.record_sha256 AS prior_tail_sha256,
           COALESCE(tail.transition_sequence + 1, 0) AS snapshot_sequence,
           encode(sha256(convert_to(concat_ws('|',
               'phase6-0015-authoritative-snapshot-identity-v1',
               session.fix_session_id::TEXT,
               COALESCE(tail.transition_sequence + 1, 0)::TEXT,
               session.record_sha256
           ), 'UTF8')), 'hex') AS identity_sha256
    FROM monitoring.fix_sessions AS session
    LEFT JOIN authoritative_tails AS tail
      ON tail.fix_session_id = session.fix_session_id
),
identified_snapshots AS (
    SELECT snapshot.*,
           (
               substr(identity_sha256, 1, 8) || '-' ||
               substr(identity_sha256, 9, 4) || '-5' ||
               substr(identity_sha256, 14, 3) || '-8' ||
               substr(identity_sha256, 18, 3) || '-' ||
               substr(identity_sha256, 21, 12)
           )::UUID AS snapshot_event_id
    FROM snapshot_material AS snapshot
),
hashed_snapshots AS (
    SELECT snapshot.*,
           encode(sha256(convert_to(concat_ws('|',
               'phase6-0015-authoritative-snapshot-record-v1',
               snapshot.snapshot_event_id::TEXT,
               snapshot.fix_session_id::TEXT,
               snapshot.snapshot_sequence::TEXT,
               COALESCE(snapshot.prior_tail_sha256, ''),
               COALESCE(snapshot.prior_tail_state, snapshot.state),
               snapshot.state,
               snapshot.next_inbound_seq_num::TEXT,
               snapshot.next_outbound_seq_num::TEXT,
               COALESCE(to_char(
                   snapshot.last_inbound_at_utc AT TIME ZONE 'UTC',
                   'YYYY-MM-DD"T"HH24:MI:SS.US"Z"'
               ), ''),
               COALESCE(to_char(
                   snapshot.last_outbound_at_utc AT TIME ZONE 'UTC',
                   'YYYY-MM-DD"T"HH24:MI:SS.US"Z"'
               ), ''),
               COALESCE(snapshot.pending_test_request_id, ''),
               COALESCE(to_char(
                   snapshot.pending_test_deadline_at_utc AT TIME ZONE 'UTC',
                   'YYYY-MM-DD"T"HH24:MI:SS.US"Z"'
               ), ''),
               snapshot.test_request_grace_expired::TEXT
           ), 'UTF8')), 'hex') AS snapshot_record_sha256
    FROM identified_snapshots AS snapshot
)
INSERT INTO monitoring.fix_session_events (
    fix_session_event_id,
    run_id,
    session_id,
    event_type,
    sequence_number,
    event_time_utc,
    message_type,
    payload_jsonb,
    simulated,
    fix_session_id,
    prior_state,
    new_state,
    reason_code,
    parent_message_id,
    record_sha256,
    transition_sequence,
    previous_event_sha256,
    projected_next_inbound_seq_num,
    projected_next_outbound_seq_num,
    projected_last_inbound_at_utc,
    projected_last_outbound_at_utc,
    projected_pending_test_request_id,
    projected_pending_test_deadline_at_utc,
    projected_test_request_grace_expired,
    created_at_utc
)
SELECT snapshot_event_id,
       NULL,
       session_key,
       'state_transition',
       NULL,
       updated_at_utc,
       NULL,
       jsonb_build_object(
           'reason_code', 'migration_authoritative_projection_snapshot',
           'migration', '0015'
       ),
       TRUE,
       fix_session_id,
       COALESCE(prior_tail_state, state),
       state,
       'migration_authoritative_projection_snapshot',
       NULL,
       snapshot_record_sha256,
       snapshot_sequence,
       prior_tail_sha256,
       next_inbound_seq_num,
       next_outbound_seq_num,
       last_inbound_at_utc,
       last_outbound_at_utc,
       pending_test_request_id,
       pending_test_deadline_at_utc,
       test_request_grace_expired,
       updated_at_utc
FROM hashed_snapshots;

WITH authoritative_tails AS (
    SELECT DISTINCT ON (fix_session_id)
           fix_session_id,
           fix_session_event_id,
           transition_sequence,
           record_sha256
    FROM monitoring.fix_session_events
    WHERE fix_session_id IS NOT NULL
    ORDER BY fix_session_id, transition_sequence DESC
)
UPDATE monitoring.fix_sessions AS session
SET last_transition_event_id = tail.fix_session_event_id,
    last_transition_sequence = tail.transition_sequence,
    authoritative_event_sha256 = tail.record_sha256,
    state_version = session.state_version + 1,
    previous_state_hash = session.record_sha256
FROM authoritative_tails AS tail
WHERE tail.fix_session_id = session.fix_session_id;

-- The 0014 tail foreign key is initially deferred. Settle its queued checks before the
-- following ALTER TABLE statements, then restore deferred mode for runtime authority checks.
SET CONSTRAINTS ALL IMMEDIATE;
SET CONSTRAINTS ALL DEFERRED;

ALTER TABLE monitoring.fix_session_events
    ALTER COLUMN projected_next_inbound_seq_num SET NOT NULL,
    ALTER COLUMN projected_next_outbound_seq_num SET NOT NULL,
    ALTER COLUMN projected_test_request_grace_expired SET NOT NULL,
    ADD CONSTRAINT phase6_fix_event_projected_sequence_check CHECK (
        projected_next_inbound_seq_num > 0
        AND projected_next_outbound_seq_num > 0
    ),
    ADD CONSTRAINT phase6_fix_event_projected_pending_check CHECK (
        fix_session_id IS NULL
        OR (
            new_state = 'test_request_pending'
            AND projected_pending_test_request_id IS NOT NULL
            AND projected_pending_test_deadline_at_utc IS NOT NULL
        )
        OR (
            new_state IS NOT NULL
            AND new_state <> 'test_request_pending'
            AND projected_pending_test_request_id IS NULL
            AND projected_pending_test_deadline_at_utc IS NULL
            AND projected_test_request_grace_expired = FALSE
        )
    );

ALTER TABLE monitoring.fix_sessions
    ALTER COLUMN last_transition_event_id SET NOT NULL,
    ALTER COLUMN last_transition_sequence SET NOT NULL,
    ALTER COLUMN authoritative_event_sha256 SET NOT NULL;

ALTER TABLE monitoring.fix_messages
    ADD CONSTRAINT uq_phase6_fix_message_occurrence_parent
    UNIQUE (fix_session_id, fix_message_id, direction, validation_status);

CREATE TABLE IF NOT EXISTS monitoring.fix_rejection_occurrences (
    fix_rejection_occurrence_id UUID PRIMARY KEY,
    fix_message_id UUID NOT NULL,
    fix_session_id UUID NOT NULL,
    direction TEXT NOT NULL
        CONSTRAINT phase6_fix_rejection_occurrence_direction_check
        CHECK (direction IN ('inbound', 'outbound')),
    validation_status TEXT NOT NULL DEFAULT 'rejected'
        CONSTRAINT phase6_fix_rejection_occurrence_parent_status_check
        CHECK (validation_status = 'rejected'),
    processing_time_utc TIMESTAMPTZ NOT NULL,
    record_sha256 CHAR(64) NOT NULL
        CONSTRAINT phase6_fix_rejection_occurrence_hash_check
        CHECK (record_sha256 ~ '^[0-9a-f]{64}$'),
    created_at_utc TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT fk_phase6_fix_rejection_occurrence_observation
        FOREIGN KEY (fix_session_id, fix_message_id, direction, validation_status)
        REFERENCES monitoring.fix_messages (
            fix_session_id, fix_message_id, direction, validation_status
        )
        ON DELETE CASCADE,
    CONSTRAINT uq_phase6_fix_rejection_logical_occurrence
        UNIQUE (fix_message_id, processing_time_utc)
);

CREATE INDEX idx_phase6_fix_rejection_occurrence_history
    ON monitoring.fix_rejection_occurrences (
        fix_session_id, processing_time_utc, fix_rejection_occurrence_id
    );

-- Preserve the 0014 projection transition checks while making session identity and timeout
-- configuration immutable. Runtime timeout state belongs to the seven event snapshot fields;
-- changing its configuration requires a new session identity, not a projection UPDATE.
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
    IF NEW.session_key IS DISTINCT FROM OLD.session_key
       OR NEW.sender_comp_id IS DISTINCT FROM OLD.sender_comp_id
       OR NEW.target_comp_id IS DISTINCT FROM OLD.target_comp_id
       OR NEW.heartbeat_interval_seconds IS DISTINCT FROM OLD.heartbeat_interval_seconds
       OR NEW.test_request_grace_seconds IS DISTINCT FROM OLD.test_request_grace_seconds
       OR NEW.disconnect_timeout_seconds IS DISTINCT FROM OLD.disconnect_timeout_seconds
       OR NEW.configuration_sha256 IS DISTINCT FROM OLD.configuration_sha256
       OR NEW.simulated IS DISTINCT FROM OLD.simulated THEN
        RAISE EXCEPTION 'FIX session identity and timeout configuration are immutable';
    END IF;
    IF NEW.state_version <> OLD.state_version + 1 THEN
        RAISE EXCEPTION 'stale FIX session projection version: expected %, got %', OLD.state_version + 1, NEW.state_version;
    END IF;
    IF NEW.previous_state_hash IS DISTINCT FROM OLD.record_sha256 THEN
        RAISE EXCEPTION 'stale FIX session projection hash';
    END IF;
    IF NEW.next_inbound_seq_num < OLD.next_inbound_seq_num
       OR NEW.next_outbound_seq_num < OLD.next_outbound_seq_num THEN
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

-- Extend the 0014 append-only validator with full projection snapshots and monotonic session
-- sequence guarantees. The existing 0014 trigger continues to call this replaced function.
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

    IF NEW.new_state IS NULL
       OR NEW.record_sha256 IS NULL
       OR NEW.projected_next_inbound_seq_num IS NULL
       OR NEW.projected_next_outbound_seq_num IS NULL
       OR NEW.projected_test_request_grace_expired IS NULL THEN
        RAISE EXCEPTION 'session-linked FIX event requires a complete projection snapshot';
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
        IF NEW.projected_next_inbound_seq_num < prior_event.projected_next_inbound_seq_num
           OR NEW.projected_next_outbound_seq_num < prior_event.projected_next_outbound_seq_num THEN
            RAISE EXCEPTION 'FIX session event projected sequence numbers may not decrease';
        END IF;
    END IF;

    RETURN NEW;
END;
$$;

CREATE OR REPLACE FUNCTION monitoring.assert_fix_session_projection_authority(
    target_fix_session_id UUID
)
RETURNS void
LANGUAGE plpgsql
AS $$
DECLARE
    projection monitoring.fix_sessions%ROWTYPE;
    tail monitoring.fix_session_events%ROWTYPE;
    event_count BIGINT;
    minimum_sequence BIGINT;
    maximum_sequence BIGINT;
    chain_break_count BIGINT;
BEGIN
    SELECT * INTO projection
    FROM monitoring.fix_sessions
    WHERE fix_session_id = target_fix_session_id;

    IF NOT FOUND THEN
        RETURN;
    END IF;

    SELECT * INTO tail
    FROM monitoring.fix_session_events
    WHERE fix_session_id = target_fix_session_id
    ORDER BY transition_sequence DESC
    LIMIT 1;

    IF NOT FOUND THEN
        RAISE EXCEPTION
            'FIX session projection % has no authoritative session event',
            target_fix_session_id;
    END IF;

    SELECT count(*), min(transition_sequence), max(transition_sequence)
    INTO event_count, minimum_sequence, maximum_sequence
    FROM monitoring.fix_session_events
    WHERE fix_session_id = target_fix_session_id;

    IF minimum_sequence <> 0 OR event_count <> maximum_sequence + 1 THEN
        RAISE EXCEPTION
            'FIX session % event ordinals are not contiguous',
            target_fix_session_id;
    END IF;

    SELECT count(*)
    INTO chain_break_count
    FROM (
        SELECT transition_sequence,
               previous_event_sha256,
               prior_state,
               lag(record_sha256) OVER (ORDER BY transition_sequence) AS expected_previous_hash,
               lag(new_state) OVER (ORDER BY transition_sequence) AS expected_prior_state
        FROM monitoring.fix_session_events
        WHERE fix_session_id = target_fix_session_id
    ) AS chain
    WHERE (
        transition_sequence = 0
        AND previous_event_sha256 IS NOT NULL
    ) OR (
        transition_sequence > 0
        AND (
            previous_event_sha256 IS DISTINCT FROM expected_previous_hash
            OR prior_state IS DISTINCT FROM expected_prior_state
        )
    );

    IF chain_break_count <> 0 THEN
        RAISE EXCEPTION
            'FIX session % event hash/state chain is broken',
            target_fix_session_id;
    END IF;

    IF projection.last_transition_event_id IS DISTINCT FROM tail.fix_session_event_id THEN
        RAISE EXCEPTION
            'FIX session projection % does not identify authoritative event tail',
            target_fix_session_id;
    END IF;
    IF projection.last_transition_sequence IS DISTINCT FROM tail.transition_sequence THEN
        RAISE EXCEPTION
            'FIX session projection % tail sequence differs from authoritative event tail',
            target_fix_session_id;
    END IF;
    IF projection.authoritative_event_sha256 IS DISTINCT FROM tail.record_sha256 THEN
        RAISE EXCEPTION
            'FIX session projection % authoritative hash differs from event tail',
            target_fix_session_id;
    END IF;
    IF projection.state IS DISTINCT FROM tail.new_state THEN
        RAISE EXCEPTION
            'FIX session projection % state differs from authoritative event tail',
            target_fix_session_id;
    END IF;
    IF projection.next_inbound_seq_num IS DISTINCT FROM tail.projected_next_inbound_seq_num
       OR projection.next_outbound_seq_num IS DISTINCT FROM tail.projected_next_outbound_seq_num
       OR projection.last_inbound_at_utc IS DISTINCT FROM tail.projected_last_inbound_at_utc
       OR projection.last_outbound_at_utc IS DISTINCT FROM tail.projected_last_outbound_at_utc
       OR projection.pending_test_request_id IS DISTINCT FROM tail.projected_pending_test_request_id
       OR projection.pending_test_deadline_at_utc IS DISTINCT FROM tail.projected_pending_test_deadline_at_utc
       OR projection.test_request_grace_expired IS DISTINCT FROM tail.projected_test_request_grace_expired THEN
        RAISE EXCEPTION
            'FIX session projection % differs from authoritative event snapshot',
            target_fix_session_id;
    END IF;
END;
$$;

CREATE OR REPLACE FUNCTION monitoring.validate_fix_session_projection_authority()
RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
    authorized_event_count BIGINT;
BEGIN
    IF TG_OP = 'UPDATE' THEN
        IF NEW.last_transition_sequence <= OLD.last_transition_sequence THEN
            RAISE EXCEPTION
                'changed FIX session projection requires a new session event';
        END IF;

        IF NEW.state IS DISTINCT FROM OLD.state
           AND NOT EXISTS (
               SELECT 1
               FROM monitoring.fix_session_events AS event
               WHERE event.fix_session_id = NEW.fix_session_id
                 AND event.transition_sequence > OLD.last_transition_sequence
                 AND event.transition_sequence <= NEW.last_transition_sequence
                 AND event.event_type IN (
                     'state_transition',
                     'sequence_gap',
                     'test_request_sent',
                     'heartbeat_received',
                     'connection_dropped'
                 )
                 AND event.prior_state IS DISTINCT FROM event.new_state
                 AND event.new_state = NEW.state
           ) THEN
            RAISE EXCEPTION
                'FIX session state change requires a matching state-changing event';
        END IF;

        IF NEW.next_inbound_seq_num IS DISTINCT FROM OLD.next_inbound_seq_num
           OR NEW.last_inbound_at_utc IS DISTINCT FROM OLD.last_inbound_at_utc THEN
            SELECT count(*)
            INTO authorized_event_count
            FROM monitoring.fix_session_events AS event
            JOIN monitoring.fix_messages AS message
              ON message.fix_session_id = event.fix_session_id
             AND message.fix_message_id = event.parent_message_id
             AND message.direction = 'inbound'
             AND message.validation_status = 'valid'
             AND message.msg_seq_num = event.sequence_number
            WHERE event.fix_session_id = NEW.fix_session_id
              AND event.transition_sequence > OLD.last_transition_sequence
              AND event.transition_sequence <= NEW.last_transition_sequence
              AND event.event_type = 'message_accepted'
              AND event.projected_next_inbound_seq_num = NEW.next_inbound_seq_num
              AND event.projected_last_inbound_at_utc IS NOT DISTINCT FROM NEW.last_inbound_at_utc;
            IF authorized_event_count = 0 THEN
                RAISE EXCEPTION
                    'inbound FIX projection change requires an accepted valid message event';
            END IF;
        END IF;

        IF NEW.next_outbound_seq_num IS DISTINCT FROM OLD.next_outbound_seq_num
           OR NEW.last_outbound_at_utc IS DISTINCT FROM OLD.last_outbound_at_utc THEN
            SELECT count(*)
            INTO authorized_event_count
            FROM monitoring.fix_session_events AS event
            JOIN monitoring.fix_messages AS message
              ON message.fix_session_id = event.fix_session_id
             AND message.fix_message_id = event.parent_message_id
             AND message.direction = 'outbound'
             AND message.validation_status = 'valid'
             AND message.msg_seq_num = event.sequence_number
            WHERE event.fix_session_id = NEW.fix_session_id
              AND event.transition_sequence > OLD.last_transition_sequence
              AND event.transition_sequence <= NEW.last_transition_sequence
              AND event.event_type = 'message_sent'
              AND event.projected_next_outbound_seq_num = NEW.next_outbound_seq_num
              AND event.projected_last_outbound_at_utc IS NOT DISTINCT FROM NEW.last_outbound_at_utc;
            IF authorized_event_count = 0 THEN
                RAISE EXCEPTION
                    'outbound FIX projection change requires a sent valid message event';
            END IF;
        END IF;

        IF (
            NEW.pending_test_request_id IS NOT NULL
            AND (
                NEW.pending_test_request_id IS DISTINCT FROM OLD.pending_test_request_id
                OR NEW.pending_test_deadline_at_utc IS DISTINCT FROM OLD.pending_test_deadline_at_utc
            )
        ) AND NOT EXISTS (
            SELECT 1
            FROM monitoring.fix_session_events AS event
            WHERE event.fix_session_id = NEW.fix_session_id
              AND event.transition_sequence > OLD.last_transition_sequence
              AND event.transition_sequence <= NEW.last_transition_sequence
              AND event.event_type = 'test_request_sent'
              AND event.projected_pending_test_request_id IS NOT DISTINCT FROM NEW.pending_test_request_id
              AND event.projected_pending_test_deadline_at_utc IS NOT DISTINCT FROM NEW.pending_test_deadline_at_utc
        ) THEN
            RAISE EXCEPTION
                'pending FIX TestRequest projection requires a TestRequest-sent event';
        END IF;

        IF OLD.pending_test_request_id IS NOT NULL
           AND NEW.pending_test_request_id IS NULL
           AND NOT EXISTS (
               SELECT 1
               FROM monitoring.fix_session_events AS event
               WHERE event.fix_session_id = NEW.fix_session_id
                 AND event.transition_sequence > OLD.last_transition_sequence
                 AND event.transition_sequence <= NEW.last_transition_sequence
                 AND event.event_type IN ('heartbeat_received', 'connection_dropped')
                 AND event.projected_pending_test_request_id IS NULL
                 AND event.projected_pending_test_deadline_at_utc IS NULL
                 AND event.projected_test_request_grace_expired = NEW.test_request_grace_expired
           ) THEN
            RAISE EXCEPTION
                'cleared FIX TestRequest projection requires heartbeat or disconnect evidence';
        END IF;

        IF NEW.test_request_grace_expired IS DISTINCT FROM OLD.test_request_grace_expired
           AND NOT EXISTS (
               SELECT 1
               FROM monitoring.fix_session_events AS event
               WHERE event.fix_session_id = NEW.fix_session_id
                 AND event.transition_sequence > OLD.last_transition_sequence
                 AND event.transition_sequence <= NEW.last_transition_sequence
                 AND (
                     (
                         NEW.test_request_grace_expired
                         AND event.event_type = 'test_request_grace_expired'
                     ) OR (
                         NOT NEW.test_request_grace_expired
                         AND event.event_type IN (
                             'test_request_sent',
                             'heartbeat_received',
                             'connection_dropped'
                         )
                     )
                 )
                 AND event.projected_test_request_grace_expired = NEW.test_request_grace_expired
           ) THEN
            RAISE EXCEPTION
                'FIX TestRequest grace change requires matching event evidence';
        END IF;
    END IF;

    PERFORM monitoring.assert_fix_session_projection_authority(NEW.fix_session_id);
    RETURN NEW;
END;
$$;

CREATE CONSTRAINT TRIGGER trg_phase6_fix_session_projection_authority
AFTER INSERT OR UPDATE ON monitoring.fix_sessions
DEFERRABLE INITIALLY DEFERRED
FOR EACH ROW
EXECUTE FUNCTION monitoring.validate_fix_session_projection_authority();

CREATE OR REPLACE FUNCTION monitoring.validate_fix_session_event_tail()
RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
    target_id UUID;
BEGIN
    target_id := CASE WHEN TG_OP = 'DELETE' THEN OLD.fix_session_id ELSE NEW.fix_session_id END;
    IF target_id IS NOT NULL THEN
        PERFORM monitoring.assert_fix_session_projection_authority(target_id);
    END IF;
    RETURN CASE WHEN TG_OP = 'DELETE' THEN OLD ELSE NEW END;
END;
$$;

CREATE CONSTRAINT TRIGGER trg_phase6_fix_session_event_tail
AFTER INSERT OR UPDATE OR DELETE ON monitoring.fix_session_events
DEFERRABLE INITIALLY DEFERRED
FOR EACH ROW
EXECUTE FUNCTION monitoring.validate_fix_session_event_tail();

CREATE OR REPLACE FUNCTION monitoring.protect_fix_session_event_immutability()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    IF TG_OP = 'DELETE'
       AND NOT EXISTS (
           SELECT 1
           FROM monitoring.fix_sessions
           WHERE fix_session_id = OLD.fix_session_id
       ) THEN
        RETURN OLD;
    END IF;
    RAISE EXCEPTION 'FIX session events are immutable';
END;
$$;

CREATE TRIGGER trg_phase6_protect_fix_session_event_immutability
BEFORE UPDATE OR DELETE ON monitoring.fix_session_events
FOR EACH ROW
EXECUTE FUNCTION monitoring.protect_fix_session_event_immutability();

-- Constraint triggers do not retroactively inspect existing rows. Validate every upgraded
-- session explicitly before committing the migration.
DO $$
DECLARE
    existing_session RECORD;
BEGIN
    FOR existing_session IN
        SELECT fix_session_id
        FROM monitoring.fix_sessions
        ORDER BY fix_session_id
    LOOP
        PERFORM monitoring.assert_fix_session_projection_authority(
            existing_session.fix_session_id
        );
    END LOOP;
END;
$$;

COMMENT ON COLUMN monitoring.fix_sessions.last_transition_sequence IS
    'Ordinal of the immutable authoritative FIX session-event tail.';
COMMENT ON COLUMN monitoring.fix_sessions.authoritative_event_sha256 IS
    'Record hash of the immutable authoritative FIX session-event tail.';
COMMENT ON COLUMN monitoring.fix_session_events.projected_next_inbound_seq_num IS
    'Complete inbound-sequence snapshot after this immutable event.';
COMMENT ON COLUMN monitoring.fix_session_events.projected_next_outbound_seq_num IS
    'Complete outbound-sequence snapshot after this immutable event.';
COMMENT ON TABLE monitoring.fix_rejection_occurrences IS
    'Timestamped deliveries of stable rejected FIX observations; repeated malformed input remains auditable.';

COMMIT;
