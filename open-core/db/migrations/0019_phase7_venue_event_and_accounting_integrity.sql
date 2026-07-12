-- Phase 7 fourth independent audit repair. Migrations 0001 through 0018 remain immutable.
-- PostgreSQL is the authority for internal venue events, order observations, recovery, and expiry.

CREATE TABLE IF NOT EXISTS execution.paper_internal_venue_sequences (
    paper_run_id uuid PRIMARY KEY REFERENCES execution.paper_runs(paper_run_id) ON DELETE CASCADE,
    last_sequence bigint NOT NULL DEFAULT 0 CHECK (last_sequence >= 0),
    updated_at_utc timestamptz NOT NULL,
    record_sha256 text NOT NULL CHECK (record_sha256 ~ '^[0-9a-f]{64}$')
);

CREATE TABLE IF NOT EXISTS execution.paper_internal_venue_commands (
    command_id uuid PRIMARY KEY,
    paper_run_id uuid NOT NULL REFERENCES execution.paper_runs(paper_run_id) ON DELETE CASCADE,
    submission_id uuid REFERENCES execution.paper_order_submissions(submission_id),
    client_order_id text,
    command_type text NOT NULL CHECK (command_type IN ('submit','acknowledge','reject','expire','cancel_request','cancel_confirm','fill','market_event','fault')),
    idempotency_key text NOT NULL,
    command_at_utc timestamptz NOT NULL,
    payload_jsonb jsonb NOT NULL,
    parent_command_id uuid REFERENCES execution.paper_internal_venue_commands(command_id),
    record_sha256 text NOT NULL CHECK (record_sha256 ~ '^[0-9a-f]{64}$'),
    UNIQUE (paper_run_id,idempotency_key)
);

CREATE TABLE IF NOT EXISTS execution.paper_internal_venue_events (
    internal_venue_event_id uuid PRIMARY KEY,
    paper_run_id uuid NOT NULL REFERENCES execution.paper_runs(paper_run_id) ON DELETE CASCADE,
    command_id uuid NOT NULL REFERENCES execution.paper_internal_venue_commands(command_id),
    submission_id uuid REFERENCES execution.paper_order_submissions(submission_id),
    client_order_id text,
    venue_sequence bigint NOT NULL CHECK (venue_sequence > 0),
    event_type text NOT NULL CHECK (event_type IN ('submitted','acknowledged','duplicate_ack','rejected','expired','cancel_pending','cancel_timeout','cancelled','fill','duplicate_fill','fill_delayed','duplicate_submission','fault','market_event')),
    occurred_at_utc timestamptz NOT NULL,
    details_jsonb jsonb NOT NULL,
    record_sha256 text NOT NULL CHECK (record_sha256 ~ '^[0-9a-f]{64}$'),
    UNIQUE (paper_run_id,venue_sequence),
    UNIQUE (command_id,event_type)
);

CREATE TABLE IF NOT EXISTS execution.paper_venue_order_observations (
    venue_order_observation_id uuid PRIMARY KEY,
    paper_run_id uuid NOT NULL REFERENCES execution.paper_runs(paper_run_id) ON DELETE CASCADE,
    submission_id uuid NOT NULL REFERENCES execution.paper_order_submissions(submission_id),
    client_order_id text NOT NULL,
    venue_order_id text NOT NULL,
    venue_sequence bigint NOT NULL CHECK (venue_sequence >= 0),
    state text NOT NULL CHECK (state IN ('pending_ack','acknowledged','partially_filled','filled','cancel_pending','cancelled','rejected','expired','unknown_pending_recovery')),
    original_quantity numeric NOT NULL CHECK (original_quantity > 0),
    cumulative_filled_quantity numeric NOT NULL CHECK (cumulative_filled_quantity >= 0 AND cumulative_filled_quantity <= original_quantity),
    remaining_quantity numeric NOT NULL CHECK (remaining_quantity >= 0 AND remaining_quantity <= original_quantity),
    average_fill_price numeric CHECK (average_fill_price IS NULL OR average_fill_price > 0),
    venue_created_at_utc timestamptz NOT NULL,
    venue_updated_at_utc timestamptz NOT NULL,
    first_observed_at_utc timestamptz NOT NULL,
    observation_source text NOT NULL,
    query_id uuid,
    internal_venue_event_id uuid REFERENCES execution.paper_internal_venue_events(internal_venue_event_id),
    economics_sha256 text NOT NULL CHECK (economics_sha256 ~ '^[0-9a-f]{64}$'),
    venue_record_sha256 text NOT NULL CHECK (venue_record_sha256 ~ '^[0-9a-f]{64}$'),
    evidence_sha256 text NOT NULL CHECK (evidence_sha256 ~ '^[0-9a-f]{64}$'),
    record_sha256 text NOT NULL CHECK (record_sha256 ~ '^[0-9a-f]{64}$'),
    UNIQUE (submission_id,venue_sequence)
);

CREATE TABLE IF NOT EXISTS execution.paper_order_projections (
    submission_id uuid PRIMARY KEY REFERENCES execution.paper_order_submissions(submission_id),
    paper_run_id uuid NOT NULL REFERENCES execution.paper_runs(paper_run_id) ON DELETE CASCADE,
    client_order_id text NOT NULL,
    venue_order_id text NOT NULL,
    latest_observation_id uuid NOT NULL REFERENCES execution.paper_venue_order_observations(venue_order_observation_id),
    venue_sequence bigint NOT NULL CHECK (venue_sequence >= 0),
    observed_state text NOT NULL,
    authority_state text NOT NULL CHECK (authority_state IN ('pending_ack','acknowledged','partially_filled','filled','cancel_pending','cancelled','rejected','expired','pending_recovery')),
    cumulative_filled_quantity numeric NOT NULL CHECK (cumulative_filled_quantity >= 0),
    average_fill_price numeric,
    terminal boolean NOT NULL,
    fill_application_complete boolean NOT NULL,
    blocked_reason text,
    version bigint NOT NULL CHECK (version >= 0),
    updated_at_utc timestamptz NOT NULL,
    economics_sha256 text NOT NULL CHECK (economics_sha256 ~ '^[0-9a-f]{64}$'),
    record_sha256 text NOT NULL CHECK (record_sha256 ~ '^[0-9a-f]{64}$'),
    UNIQUE (paper_run_id,client_order_id)
);

CREATE TABLE IF NOT EXISTS execution.paper_recovery_observation_bundles (
    recovery_observation_bundle_id uuid PRIMARY KEY,
    paper_run_id uuid NOT NULL REFERENCES execution.paper_runs(paper_run_id) ON DELETE CASCADE,
    submission_id uuid NOT NULL REFERENCES execution.paper_order_submissions(submission_id),
    query_id uuid NOT NULL,
    query_started_at_utc timestamptz NOT NULL,
    query_completed_at_utc timestamptz NOT NULL,
    queried_order_sha256 text,
    recent_order_hashes_jsonb jsonb NOT NULL,
    open_order_hashes_jsonb jsonb NOT NULL,
    fill_hashes_jsonb jsonb NOT NULL,
    balance_hashes_jsonb jsonb NOT NULL,
    position_hashes_jsonb jsonb NOT NULL,
    account_snapshot_sha256 text,
    fill_evidence_complete boolean NOT NULL,
    incompleteness_reason text,
    observation_hashes_jsonb jsonb NOT NULL,
    record_sha256 text NOT NULL CHECK (record_sha256 ~ '^[0-9a-f]{64}$'),
    UNIQUE (submission_id,query_id)
);

CREATE TABLE IF NOT EXISTS execution.paper_fill_recovery_lineage (
    fill_recovery_lineage_id uuid PRIMARY KEY,
    paper_run_id uuid NOT NULL REFERENCES execution.paper_runs(paper_run_id) ON DELETE CASCADE,
    submission_id uuid NOT NULL REFERENCES execution.paper_order_submissions(submission_id),
    fill_id uuid NOT NULL REFERENCES execution.paper_fills(fill_id),
    recovery_observation_bundle_id uuid REFERENCES execution.paper_recovery_observation_bundles(recovery_observation_bundle_id),
    venue_order_observation_id uuid REFERENCES execution.paper_venue_order_observations(venue_order_observation_id),
    reservation_amount_before numeric NOT NULL CHECK (reservation_amount_before >= 0),
    reservation_amount_after numeric NOT NULL CHECK (reservation_amount_after >= 0),
    reservation_quantity_before numeric NOT NULL CHECK (reservation_quantity_before >= 0),
    reservation_quantity_after numeric NOT NULL CHECK (reservation_quantity_after >= 0),
    applied_at_utc timestamptz NOT NULL,
    record_sha256 text NOT NULL CHECK (record_sha256 ~ '^[0-9a-f]{64}$'),
    UNIQUE (fill_id)
);

CREATE TABLE IF NOT EXISTS execution.paper_expiry_outbox (
    expiry_id uuid PRIMARY KEY,
    paper_run_id uuid NOT NULL REFERENCES execution.paper_runs(paper_run_id) ON DELETE CASCADE,
    submission_id uuid NOT NULL REFERENCES execution.paper_order_submissions(submission_id),
    client_order_id text NOT NULL,
    state text NOT NULL CHECK (state IN ('expiry_requested','expiry_claimed','expiry_confirmed','expiry_unknown')),
    requested_at_utc timestamptz NOT NULL,
    claimed_at_utc timestamptz,
    claim_token uuid,
    claimed_by text,
    claim_lease_expires_at_utc timestamptz,
    recovery_claim_token uuid,
    recovery_claimed_by text,
    recovery_claimed_at_utc timestamptz,
    recovery_lease_expires_at_utc timestamptz,
    recovery_generation integer NOT NULL DEFAULT 0 CHECK (recovery_generation >= 0),
    updated_at_utc timestamptz NOT NULL,
    evidence_sha256 text,
    record_sha256 text NOT NULL CHECK (record_sha256 ~ '^[0-9a-f]{64}$'),
    UNIQUE (submission_id)
);

ALTER TABLE execution.paper_reconciliation_differences DROP CONSTRAINT IF EXISTS paper_reconciliation_differences_difference_type_check;
ALTER TABLE execution.paper_reconciliation_differences ADD CONSTRAINT paper_reconciliation_differences_difference_type_check CHECK (difference_type IN (
    'local_order_missing_at_venue','venue_order_missing_locally','order_status_mismatch','venue_order_id_mismatch','quantity_mismatch','fill_missing_locally','fill_missing_at_venue','duplicate_fill','balance_mismatch','balance_availability_mismatch','reservation_mismatch','position_mismatch','realized_pnl_mismatch','fee_mismatch','currency_mismatch','stale_venue_snapshot','stale_local_snapshot','sequence_gap','unknown_submission','cancel_pending','unsupported_venue_field','account_mode_mismatch','reservation_authority_mismatch','order_budget_mismatch','venue_event_sequence_mismatch','latest_observation_mismatch','fill_application_incomplete'
));
ALTER TABLE execution.paper_reservations
    ADD COLUMN IF NOT EXISTS reserve_price numeric,
    ADD COLUMN IF NOT EXISTS maximum_fee_bps numeric NOT NULL DEFAULT 10 CHECK (maximum_fee_bps >= 0),
    ADD COLUMN IF NOT EXISTS maximum_adverse_slippage_bps numeric NOT NULL DEFAULT 200 CHECK (maximum_adverse_slippage_bps >= 0),
    ADD COLUMN IF NOT EXISTS calculator_version text NOT NULL DEFAULT 'phase7-reservation-v1',
    ADD COLUMN IF NOT EXISTS spent_amount numeric NOT NULL DEFAULT 0 CHECK (spent_amount >= 0);
UPDATE execution.paper_reservations SET spent_amount=original_amount-remaining_amount WHERE spent_amount=0 AND remaining_amount<original_amount;

CREATE OR REPLACE FUNCTION execution.phase7_guard_order_projection_update()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF ROW(NEW.submission_id,NEW.paper_run_id,NEW.client_order_id,NEW.venue_order_id,NEW.economics_sha256)
       IS DISTINCT FROM ROW(OLD.submission_id,OLD.paper_run_id,OLD.client_order_id,OLD.venue_order_id,OLD.economics_sha256) THEN
        RAISE EXCEPTION 'paper order projection identity is immutable';
    END IF;
    IF NEW.venue_sequence < OLD.venue_sequence OR NEW.cumulative_filled_quantity < OLD.cumulative_filled_quantity THEN
        RAISE EXCEPTION 'paper order projection cannot regress';
    END IF;
    IF OLD.terminal AND (NOT NEW.terminal OR NEW.authority_state <> OLD.authority_state) THEN
        RAISE EXCEPTION 'terminal paper order projection cannot return to active state';
    END IF;
    RETURN NEW;
END;
$$;

CREATE OR REPLACE FUNCTION execution.phase7_guard_expiry_update()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF ROW(NEW.expiry_id,NEW.paper_run_id,NEW.submission_id,NEW.client_order_id,NEW.requested_at_utc)
       IS DISTINCT FROM ROW(OLD.expiry_id,OLD.paper_run_id,OLD.submission_id,OLD.client_order_id,OLD.requested_at_utc) THEN
        RAISE EXCEPTION 'paper expiry identity is immutable';
    END IF;
    IF NEW.recovery_generation < OLD.recovery_generation THEN RAISE EXCEPTION 'paper expiry recovery generation cannot decrease'; END IF;
    IF OLD.state <> NEW.state AND NOT (
        (OLD.state='expiry_requested' AND NEW.state='expiry_claimed') OR
        (OLD.state='expiry_claimed' AND NEW.state IN ('expiry_confirmed','expiry_unknown')) OR
        (OLD.state='expiry_unknown' AND NEW.state IN ('expiry_unknown','expiry_confirmed'))
    ) THEN RAISE EXCEPTION 'invalid paper expiry transition % -> %',OLD.state,NEW.state; END IF;
    RETURN NEW;
END;
$$;

CREATE OR REPLACE FUNCTION execution.phase7_guard_reservation_update()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF ROW(NEW.paper_run_id,NEW.submission_id,NEW.client_order_id,NEW.currency,NEW.original_amount,NEW.original_quantity,NEW.created_at_utc,NEW.economics_sha256,NEW.reserve_price,NEW.maximum_fee_bps,NEW.maximum_adverse_slippage_bps,NEW.calculator_version)
       IS DISTINCT FROM ROW(OLD.paper_run_id,OLD.submission_id,OLD.client_order_id,OLD.currency,OLD.original_amount,OLD.original_quantity,OLD.created_at_utc,OLD.economics_sha256,OLD.reserve_price,OLD.maximum_fee_bps,OLD.maximum_adverse_slippage_bps,OLD.calculator_version) THEN
        RAISE EXCEPTION 'paper reservation economic identity is immutable';
    END IF;
    IF NEW.remaining_amount > OLD.remaining_amount OR NEW.remaining_quantity > OLD.remaining_quantity OR NEW.spent_amount < OLD.spent_amount THEN
        RAISE EXCEPTION 'paper reservation cannot regress';
    END IF;
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS phase7_reservation_update_guard ON execution.paper_reservations;
CREATE TRIGGER phase7_reservation_update_guard BEFORE UPDATE ON execution.paper_reservations FOR EACH ROW EXECUTE FUNCTION execution.phase7_guard_reservation_update();
DROP TRIGGER IF EXISTS phase7_order_projection_guard ON execution.paper_order_projections;
CREATE TRIGGER phase7_order_projection_guard BEFORE UPDATE ON execution.paper_order_projections FOR EACH ROW EXECUTE FUNCTION execution.phase7_guard_order_projection_update();
DROP TRIGGER IF EXISTS phase7_expiry_guard ON execution.paper_expiry_outbox;
CREATE TRIGGER phase7_expiry_guard BEFORE UPDATE ON execution.paper_expiry_outbox FOR EACH ROW EXECUTE FUNCTION execution.phase7_guard_expiry_update();

DO $$
DECLARE table_name text;
BEGIN
    FOREACH table_name IN ARRAY ARRAY['paper_internal_venue_commands','paper_internal_venue_events','paper_venue_order_observations','paper_recovery_observation_bundles','paper_fill_recovery_lineage'] LOOP
        EXECUTE format('DROP TRIGGER IF EXISTS phase7_%I_append_only ON execution.%I',table_name,table_name);
        EXECUTE format('CREATE TRIGGER phase7_%I_append_only BEFORE UPDATE OR DELETE ON execution.%I FOR EACH ROW EXECUTE FUNCTION execution.phase7_reject_immutable_change()',table_name,table_name);
    END LOOP;
END $$;

CREATE INDEX IF NOT EXISTS idx_phase7_internal_commands_pending ON execution.paper_internal_venue_commands(paper_run_id,command_at_utc,command_id);
CREATE INDEX IF NOT EXISTS idx_phase7_internal_events_replay ON execution.paper_internal_venue_events(paper_run_id,venue_sequence,internal_venue_event_id);
CREATE INDEX IF NOT EXISTS idx_phase7_order_observations_latest ON execution.paper_venue_order_observations(paper_run_id,client_order_id,venue_sequence DESC);
CREATE INDEX IF NOT EXISTS idx_phase7_order_projection_active ON execution.paper_order_projections(paper_run_id,updated_at_utc,submission_id) WHERE NOT terminal;
CREATE INDEX IF NOT EXISTS idx_phase7_recovery_bundles_run ON execution.paper_recovery_observation_bundles(paper_run_id,query_completed_at_utc,recovery_observation_bundle_id);
CREATE INDEX IF NOT EXISTS idx_phase7_expiry_unresolved ON execution.paper_expiry_outbox(paper_run_id,updated_at_utc,expiry_id) WHERE state IN ('expiry_requested','expiry_claimed','expiry_unknown');

COMMENT ON TABLE execution.paper_internal_venue_commands IS 'Durable-before-mutation internal paper venue command authority.';
COMMENT ON TABLE execution.paper_internal_venue_events IS 'Append-only crash-replayable internal venue event sequence allocated by PostgreSQL.';
COMMENT ON TABLE execution.paper_venue_order_observations IS 'Append-only authority for every asynchronous venue order-state observation.';
COMMENT ON TABLE execution.paper_order_projections IS 'Run-scoped latest order state advanced only by append-only observations.';
COMMENT ON TABLE execution.paper_recovery_observation_bundles IS 'Complete order, fill, fee, balance, position, snapshot, hash, and query-time recovery evidence.';
COMMENT ON TABLE execution.paper_expiry_outbox IS 'Durable expiry command, claim, unknown-outcome, and recovery authority.';