-- Phase 7 third independent audit repair. Migrations 0001 through 0017 remain immutable.
-- PostgreSQL is the sole authority for recovery ownership, market evidence, and open-order accounting.

ALTER TABLE execution.paper_dispatch_outbox
    ADD COLUMN IF NOT EXISTS claim_lease_expires_at_utc timestamptz,
    ADD COLUMN IF NOT EXISTS recovery_claim_token uuid,
    ADD COLUMN IF NOT EXISTS recovery_claimed_by text,
    ADD COLUMN IF NOT EXISTS recovery_claimed_at_utc timestamptz,
    ADD COLUMN IF NOT EXISTS recovery_lease_expires_at_utc timestamptz,
    ADD COLUMN IF NOT EXISTS recovery_generation integer NOT NULL DEFAULT 0 CHECK (recovery_generation >= 0);

ALTER TABLE execution.paper_cancel_outbox
    ADD COLUMN IF NOT EXISTS claim_lease_expires_at_utc timestamptz,
    ADD COLUMN IF NOT EXISTS recovery_claim_token uuid,
    ADD COLUMN IF NOT EXISTS recovery_claimed_by text,
    ADD COLUMN IF NOT EXISTS recovery_claimed_at_utc timestamptz,
    ADD COLUMN IF NOT EXISTS recovery_lease_expires_at_utc timestamptz,
    ADD COLUMN IF NOT EXISTS recovery_generation integer NOT NULL DEFAULT 0 CHECK (recovery_generation >= 0);

ALTER TABLE execution.paper_order_submissions
    ADD COLUMN IF NOT EXISTS counted_open boolean NOT NULL DEFAULT false,
    ADD COLUMN IF NOT EXISTS open_counted_at_utc timestamptz,
    ADD COLUMN IF NOT EXISTS open_closed_at_utc timestamptz,
    ADD COLUMN IF NOT EXISTS open_close_cause_id uuid;
UPDATE execution.paper_order_submissions
SET counted_open=true,open_counted_at_utc=COALESCE(open_counted_at_utc,submitted_at_utc)
WHERE state IN ('prepared','dispatch_claimed','submitted','pending_ack','acknowledged','partially_filled','cancel_requested','cancel_pending','cancel_unknown','submission_unknown','pending_recovery');

UPDATE execution.paper_dispatch_outbox
SET claim_lease_expires_at_utc=COALESCE(claim_lease_expires_at_utc,claimed_at_utc + interval '30 seconds')
WHERE state='dispatch_claimed' AND claimed_at_utc IS NOT NULL;
UPDATE execution.paper_cancel_outbox
SET claim_lease_expires_at_utc=COALESCE(claim_lease_expires_at_utc,claimed_at_utc + interval '30 seconds')
WHERE state='cancel_claimed' AND claimed_at_utc IS NOT NULL;

DO $$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname='phase7_submission_open_count_transition_check') THEN
        ALTER TABLE execution.paper_order_submissions ADD CONSTRAINT phase7_submission_open_count_transition_check CHECK (
            (counted_open AND open_counted_at_utc IS NOT NULL AND open_closed_at_utc IS NULL)
            OR (NOT counted_open AND (open_counted_at_utc IS NULL OR open_closed_at_utc IS NOT NULL))
        );
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname='phase7_dispatch_claim_lease_check') THEN
        ALTER TABLE execution.paper_dispatch_outbox ADD CONSTRAINT phase7_dispatch_claim_lease_check CHECK (
            state<>'dispatch_claimed' OR (claim_token IS NOT NULL AND claimed_by IS NOT NULL AND claimed_at_utc IS NOT NULL AND claim_lease_expires_at_utc>claimed_at_utc)
        );
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname='phase7_cancel_claim_lease_check') THEN
        ALTER TABLE execution.paper_cancel_outbox ADD CONSTRAINT phase7_cancel_claim_lease_check CHECK (
            state<>'cancel_claimed' OR (claim_token IS NOT NULL AND claimed_by IS NOT NULL AND claimed_at_utc IS NOT NULL AND claim_lease_expires_at_utc>claimed_at_utc)
        );
    END IF;
END $$;
CREATE TABLE IF NOT EXISTS execution.paper_order_budget_events (
    order_budget_event_id uuid PRIMARY KEY,
    paper_run_id uuid NOT NULL REFERENCES execution.paper_runs(paper_run_id) ON DELETE CASCADE,
    submission_id uuid NOT NULL REFERENCES execution.paper_order_submissions(submission_id),
    event_type text NOT NULL CHECK (event_type IN ('order_budget_opened','order_budget_closed')),
    occurred_at_utc timestamptz NOT NULL,
    cause_id uuid NOT NULL,
    prior_counted_open boolean NOT NULL,
    next_counted_open boolean NOT NULL,
    worker_id text,
    record_sha256 text NOT NULL CHECK (record_sha256 ~ '^[0-9a-f]{64}$'),
    UNIQUE (submission_id,event_type,cause_id),
    CHECK ((event_type='order_budget_opened' AND NOT prior_counted_open AND next_counted_open)
        OR (event_type='order_budget_closed' AND prior_counted_open AND NOT next_counted_open))
);

CREATE TABLE IF NOT EXISTS execution.paper_market_data_evidence (
    market_evidence_id uuid PRIMARY KEY,
    paper_run_id uuid NOT NULL REFERENCES execution.paper_runs(paper_run_id) ON DELETE CASCADE,
    series_identity_sha256 text NOT NULL CHECK (series_identity_sha256 ~ '^[0-9a-f]{64}$'),
    series_identity_jsonb jsonb NOT NULL,
    provider text NOT NULL,
    instrument text NOT NULL,
    event_type text NOT NULL,
    observation_id text NOT NULL,
    observed_at_utc timestamptz NOT NULL,
    available_at_utc timestamptz NOT NULL,
    is_final boolean NOT NULL,
    validation_status text NOT NULL CHECK (validation_status IN ('accepted','quarantined','rejected')),
    source_sha256 text NOT NULL CHECK (source_sha256 ~ '^[0-9a-f]{64}$'),
    observation_sha256 text NOT NULL CHECK (observation_sha256 ~ '^[0-9a-f]{64}$'),
    evidence_sha256 text NOT NULL CHECK (evidence_sha256 ~ '^[0-9a-f]{64}$'),
    recorded_at_utc timestamptz NOT NULL,
    UNIQUE (paper_run_id,series_identity_sha256,provider,instrument,event_type,observation_id),
    CHECK (available_at_utc >= observed_at_utc)
);

ALTER TABLE execution.paper_runtime_risk_decisions
    ADD COLUMN IF NOT EXISTS market_evidence_id uuid REFERENCES execution.paper_market_data_evidence(market_evidence_id),
    ADD COLUMN IF NOT EXISTS market_evidence_sha256 text CHECK (market_evidence_sha256 IS NULL OR market_evidence_sha256 ~ '^[0-9a-f]{64}$');

ALTER TABLE execution.paper_run_risk_state
    ADD COLUMN IF NOT EXISTS latest_market_evidence_id uuid REFERENCES execution.paper_market_data_evidence(market_evidence_id),
    ADD COLUMN IF NOT EXISTS latest_market_evidence_sha256 text CHECK (latest_market_evidence_sha256 IS NULL OR latest_market_evidence_sha256 ~ '^[0-9a-f]{64}$');

CREATE TABLE IF NOT EXISTS execution.paper_reconciliation_bundles (
    reconciliation_bundle_id uuid PRIMARY KEY,
    paper_run_id uuid NOT NULL REFERENCES execution.paper_runs(paper_run_id) ON DELETE CASCADE,
    reconciliation_id uuid NOT NULL UNIQUE REFERENCES execution.paper_reconciliations(reconciliation_id) ON DELETE CASCADE,
    local_snapshot_id uuid NOT NULL REFERENCES execution.paper_account_snapshots(snapshot_id),
    venue_snapshot_id uuid NOT NULL REFERENCES execution.paper_account_snapshots(snapshot_id),
    evaluated_at_utc timestamptz NOT NULL,
    local_order_hashes_jsonb jsonb NOT NULL,
    venue_order_hashes_jsonb jsonb NOT NULL,
    local_fill_hashes_jsonb jsonb NOT NULL,
    venue_fill_hashes_jsonb jsonb NOT NULL,
    monitoring_evidence_jsonb jsonb NOT NULL,
    kill_evidence_jsonb jsonb NOT NULL,
    record_sha256 text NOT NULL CHECK (record_sha256 ~ '^[0-9a-f]{64}$')
);

CREATE OR REPLACE FUNCTION execution.phase7_guard_dispatch_recovery_update()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF ROW(NEW.paper_run_id,NEW.submission_id,NEW.client_order_id,NEW.idempotency_key,NEW.economics_sha256,NEW.eligible_at_utc)
       IS DISTINCT FROM ROW(OLD.paper_run_id,OLD.submission_id,OLD.client_order_id,OLD.idempotency_key,OLD.economics_sha256,OLD.eligible_at_utc) THEN
        RAISE EXCEPTION 'paper dispatch economic identity is immutable';
    END IF;
    IF NEW.recovery_generation < OLD.recovery_generation THEN
        RAISE EXCEPTION 'paper dispatch recovery generation cannot decrease';
    END IF;
    IF OLD.state <> NEW.state AND NOT (
        (OLD.state='prepared' AND NEW.state='dispatch_claimed') OR
        (OLD.state='dispatch_claimed' AND NEW.state IN ('acknowledged','explicitly_rejected','unknown','recovered')) OR
        (OLD.state='unknown' AND NEW.state IN ('unknown','recovered'))
    ) THEN RAISE EXCEPTION 'invalid paper dispatch transition % -> %',OLD.state,NEW.state; END IF;
    RETURN NEW;
END;
$$;

CREATE OR REPLACE FUNCTION execution.phase7_guard_cancel_recovery_update()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF ROW(NEW.dispatch_id,NEW.paper_run_id,NEW.submission_id,NEW.client_order_id,NEW.requested_at_utc)
       IS DISTINCT FROM ROW(OLD.dispatch_id,OLD.paper_run_id,OLD.submission_id,OLD.client_order_id,OLD.requested_at_utc) THEN
        RAISE EXCEPTION 'paper cancellation identity is immutable';
    END IF;
    IF NEW.recovery_generation < OLD.recovery_generation THEN
        RAISE EXCEPTION 'paper cancellation recovery generation cannot decrease';
    END IF;
    IF OLD.state <> NEW.state AND NOT (
        (OLD.state='cancel_requested' AND NEW.state='cancel_claimed') OR
        (OLD.state='cancel_claimed' AND NEW.state IN ('cancel_confirmed','cancel_unknown')) OR
        (OLD.state='cancel_unknown' AND NEW.state IN ('cancel_unknown','cancel_confirmed'))
    ) THEN RAISE EXCEPTION 'invalid paper cancel transition % -> %',OLD.state,NEW.state; END IF;
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS phase7_dispatch_update_guard ON execution.paper_dispatch_outbox;
CREATE TRIGGER phase7_dispatch_update_guard BEFORE UPDATE ON execution.paper_dispatch_outbox
FOR EACH ROW EXECUTE FUNCTION execution.phase7_guard_dispatch_recovery_update();
DROP TRIGGER IF EXISTS phase7_cancel_update_guard ON execution.paper_cancel_outbox;
CREATE TRIGGER phase7_cancel_update_guard BEFORE UPDATE ON execution.paper_cancel_outbox
FOR EACH ROW EXECUTE FUNCTION execution.phase7_guard_cancel_recovery_update();

DROP TRIGGER IF EXISTS phase7_order_budget_events_append_only ON execution.paper_order_budget_events;
CREATE TRIGGER phase7_order_budget_events_append_only BEFORE UPDATE OR DELETE ON execution.paper_order_budget_events
FOR EACH ROW EXECUTE FUNCTION execution.phase7_reject_immutable_change();
DROP TRIGGER IF EXISTS phase7_market_evidence_immutable ON execution.paper_market_data_evidence;
CREATE TRIGGER phase7_market_evidence_immutable BEFORE UPDATE OR DELETE ON execution.paper_market_data_evidence
FOR EACH ROW EXECUTE FUNCTION execution.phase7_reject_immutable_change();
DROP TRIGGER IF EXISTS phase7_reconciliation_bundles_immutable ON execution.paper_reconciliation_bundles;
CREATE TRIGGER phase7_reconciliation_bundles_immutable BEFORE UPDATE OR DELETE ON execution.paper_reconciliation_bundles
FOR EACH ROW EXECUTE FUNCTION execution.phase7_reject_immutable_change();

CREATE INDEX IF NOT EXISTS idx_phase7_dispatch_prepared_recovery ON execution.paper_dispatch_outbox(eligible_at_utc,dispatch_id) WHERE state='prepared';
CREATE INDEX IF NOT EXISTS idx_phase7_dispatch_ambiguous_recovery ON execution.paper_dispatch_outbox(updated_at_utc,dispatch_id) WHERE state IN ('dispatch_claimed','unknown');
CREATE INDEX IF NOT EXISTS idx_phase7_cancel_requested_recovery ON execution.paper_cancel_outbox(requested_at_utc,cancel_id) WHERE state='cancel_requested';
CREATE INDEX IF NOT EXISTS idx_phase7_cancel_ambiguous_recovery ON execution.paper_cancel_outbox(updated_at_utc,cancel_id) WHERE state IN ('cancel_claimed','cancel_unknown');
CREATE INDEX IF NOT EXISTS idx_phase7_market_evidence_latest ON execution.paper_market_data_evidence(paper_run_id,series_identity_sha256,observed_at_utc DESC,market_evidence_id);
CREATE INDEX IF NOT EXISTS idx_phase7_order_budget_events_run ON execution.paper_order_budget_events(paper_run_id,occurred_at_utc,order_budget_event_id);
CREATE INDEX IF NOT EXISTS idx_phase7_reconciliation_bundle_run ON execution.paper_reconciliation_bundles(paper_run_id,evaluated_at_utc,reconciliation_bundle_id);

COMMENT ON TABLE execution.paper_market_data_evidence IS 'Immutable validated public-safe market observation authority; order timestamps never refresh it.';
COMMENT ON TABLE execution.paper_order_budget_events IS 'Append-only exactly-once open-order budget transition authority.';
COMMENT ON TABLE execution.paper_reconciliation_bundles IS 'Exact local/venue snapshots, orders, fills, and evidence evaluated together.';
