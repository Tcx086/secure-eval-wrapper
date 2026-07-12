-- Phase 7 durable paper recovery audit repair. Migrations 0001 through 0016 remain immutable.
-- PostgreSQL remains the sole operational authority; external calls never run inside a transaction.

CREATE TABLE IF NOT EXISTS execution.paper_configuration_snapshots (
    configuration_sha256 text PRIMARY KEY CHECK (configuration_sha256 ~ '^[0-9a-f]{64}$'),
    provider text NOT NULL CHECK (provider IN ('internal','okx_demo')),
    environment text NOT NULL CHECK (environment IN ('paper_internal','paper_exchange_sandbox')),
    account_reference text NOT NULL,
    configuration_jsonb jsonb NOT NULL,
    created_at_utc timestamptz NOT NULL,
    record_sha256 text NOT NULL CHECK (record_sha256 ~ '^[0-9a-f]{64}$'),
    CHECK ((provider='internal' AND environment='paper_internal') OR (provider='okx_demo' AND environment='paper_exchange_sandbox'))
);

-- Preserve existing public-safe Phase 7 rows during a seeded 0016 -> 0017 upgrade.
INSERT INTO execution.paper_configuration_snapshots (
    configuration_sha256,provider,environment,account_reference,configuration_jsonb,created_at_utc,record_sha256
)
SELECT DISTINCT ON (r.configuration_sha256)
    r.configuration_sha256,r.provider,r.environment,r.account_reference,
    jsonb_build_object(
        'legacy_phase7_snapshot',true,
        'allowed_instruments',COALESCE(m.allowed_instruments_jsonb,'[]'::jsonb),
        'risk_limits',COALESCE(m.risk_limits_jsonb,'{}'::jsonb)
    ),r.started_at_utc,r.configuration_sha256
FROM execution.paper_runs r
LEFT JOIN execution.paper_run_manifests m ON m.paper_run_id=r.paper_run_id
ORDER BY r.configuration_sha256,r.started_at_utc
ON CONFLICT (configuration_sha256) DO NOTHING;

ALTER TABLE execution.paper_runs
    ADD CONSTRAINT phase7_paper_run_configuration_snapshot_fk
    FOREIGN KEY (configuration_sha256)
    REFERENCES execution.paper_configuration_snapshots(configuration_sha256)
    DEFERRABLE INITIALLY DEFERRED;

CREATE TABLE IF NOT EXISTS execution.paper_approval_state_events (
    approval_event_id uuid PRIMARY KEY,
    approval_id uuid NOT NULL REFERENCES execution.paper_approvals(approval_id),
    paper_run_id uuid NOT NULL REFERENCES execution.paper_runs(paper_run_id) ON DELETE CASCADE,
    prior_state text,
    next_state text NOT NULL CHECK (next_state IN ('valid','consumed','expired','revoked')),
    occurred_at_utc timestamptz NOT NULL,
    reason_code text NOT NULL,
    binding_sha256 text NOT NULL CHECK (binding_sha256 ~ '^[0-9a-f]{64}$'),
    record_sha256 text NOT NULL CHECK (record_sha256 ~ '^[0-9a-f]{64}$'),
    UNIQUE (approval_id,next_state,record_sha256)
);

CREATE TABLE IF NOT EXISTS execution.paper_run_risk_state (
    paper_run_id uuid PRIMARY KEY REFERENCES execution.paper_runs(paper_run_id) ON DELETE CASCADE,
    version bigint NOT NULL CHECK (version >= 0),
    trading_day date NOT NULL,
    daily_submitted_notional numeric NOT NULL DEFAULT 0 CHECK (daily_submitted_notional >= 0),
    approval_submitted_notional numeric NOT NULL DEFAULT 0 CHECK (approval_submitted_notional >= 0),
    open_order_count integer NOT NULL DEFAULT 0 CHECK (open_order_count >= 0),
    orders_in_current_minute integer NOT NULL DEFAULT 0 CHECK (orders_in_current_minute >= 0),
    cancellations_in_current_minute integer NOT NULL DEFAULT 0 CHECK (cancellations_in_current_minute >= 0),
    rate_window_started_at_utc timestamptz NOT NULL,
    consecutive_transport_failures integer NOT NULL DEFAULT 0 CHECK (consecutive_transport_failures >= 0),
    initial_equity numeric NOT NULL CHECK (initial_equity >= 0),
    current_equity numeric NOT NULL CHECK (current_equity >= 0),
    high_watermark_equity numeric NOT NULL CHECK (high_watermark_equity >= 0),
    daily_realized_pnl numeric NOT NULL DEFAULT 0,
    latest_market_data_at_utc timestamptz,
    latest_account_snapshot_at_utc timestamptz,
    latest_reconciliation_at_utc timestamptz,
    latest_reconciliation_status text CHECK (latest_reconciliation_status IS NULL OR latest_reconciliation_status IN ('reconciled','warning','blocked','unknown')),
    venue_clock_skew_seconds numeric,
    lifecycle_sequence bigint NOT NULL DEFAULT 0 CHECK (lifecycle_sequence >= 0),
    updated_at_utc timestamptz NOT NULL,
    record_sha256 text NOT NULL CHECK (record_sha256 ~ '^[0-9a-f]{64}$')
);

CREATE TABLE IF NOT EXISTS execution.paper_runtime_risk_decisions (
    runtime_risk_decision_id uuid PRIMARY KEY,
    paper_run_id uuid NOT NULL REFERENCES execution.paper_runs(paper_run_id) ON DELETE CASCADE,
    submission_id uuid NOT NULL UNIQUE REFERENCES execution.paper_order_submissions(submission_id),
    order_intent_id uuid NOT NULL,
    accepted_pre_submit_risk_sha256 text NOT NULL CHECK (accepted_pre_submit_risk_sha256 ~ '^[0-9a-f]{64}$'),
    decision_status text NOT NULL CHECK (decision_status IN ('accepted','blocked')),
    reason_codes_jsonb jsonb NOT NULL,
    evaluated_limits_jsonb jsonb NOT NULL,
    persisted_state_jsonb jsonb NOT NULL,
    evidence_jsonb jsonb NOT NULL,
    decided_at_utc timestamptz NOT NULL,
    record_sha256 text NOT NULL CHECK (record_sha256 ~ '^[0-9a-f]{64}$'),
    UNIQUE (paper_run_id,order_intent_id,record_sha256)
);

CREATE TABLE IF NOT EXISTS execution.paper_reservations (
    reservation_id uuid PRIMARY KEY,
    paper_run_id uuid NOT NULL REFERENCES execution.paper_runs(paper_run_id) ON DELETE CASCADE,
    submission_id uuid NOT NULL UNIQUE REFERENCES execution.paper_order_submissions(submission_id),
    client_order_id text NOT NULL,
    currency text NOT NULL,
    original_amount numeric NOT NULL CHECK (original_amount > 0),
    remaining_amount numeric NOT NULL CHECK (remaining_amount >= 0 AND remaining_amount <= original_amount),
    original_quantity numeric NOT NULL CHECK (original_quantity > 0),
    remaining_quantity numeric NOT NULL CHECK (remaining_quantity >= 0 AND remaining_quantity <= original_quantity),
    state text NOT NULL CHECK (state IN ('open','consumed','released')),
    created_at_utc timestamptz NOT NULL,
    updated_at_utc timestamptz NOT NULL,
    version bigint NOT NULL CHECK (version >= 0),
    economics_sha256 text NOT NULL CHECK (economics_sha256 ~ '^[0-9a-f]{64}$'),
    record_sha256 text NOT NULL CHECK (record_sha256 ~ '^[0-9a-f]{64}$'),
    UNIQUE (paper_run_id,client_order_id),
    CHECK ((state='open' AND remaining_amount > 0 AND remaining_quantity > 0) OR (state<>'open' AND remaining_amount=0 AND remaining_quantity=0))
);

CREATE TABLE IF NOT EXISTS execution.paper_reservation_events (
    reservation_event_id uuid PRIMARY KEY,
    reservation_id uuid NOT NULL REFERENCES execution.paper_reservations(reservation_id) ON DELETE CASCADE,
    paper_run_id uuid NOT NULL REFERENCES execution.paper_runs(paper_run_id) ON DELETE CASCADE,
    submission_id uuid NOT NULL REFERENCES execution.paper_order_submissions(submission_id),
    event_type text NOT NULL CHECK (event_type IN ('reserved','reduced','consumed','released')),
    amount_delta numeric NOT NULL,
    quantity_delta numeric NOT NULL,
    occurred_at_utc timestamptz NOT NULL,
    cause_id uuid,
    record_sha256 text NOT NULL CHECK (record_sha256 ~ '^[0-9a-f]{64}$'),
    UNIQUE (reservation_id,event_type,cause_id,record_sha256)
);

ALTER TABLE execution.paper_order_submissions
    DROP CONSTRAINT IF EXISTS paper_order_submissions_state_check;
ALTER TABLE execution.paper_order_submissions
    ADD CONSTRAINT paper_order_submissions_state_check CHECK (
        state IN ('prepared','dispatch_claimed','submitted','pending_ack','acknowledged','partially_filled','filled','cancel_requested','cancel_pending','cancel_unknown','cancelled','rejected','expired','submission_unknown','pending_recovery')
    );

ALTER TABLE execution.paper_reconciliation_differences
    DROP CONSTRAINT IF EXISTS paper_reconciliation_differences_difference_type_check;
ALTER TABLE execution.paper_reconciliation_differences
    ADD CONSTRAINT paper_reconciliation_differences_difference_type_check CHECK (difference_type IN (
        'local_order_missing_at_venue','venue_order_missing_locally','order_status_mismatch','venue_order_id_mismatch',
        'quantity_mismatch','fill_missing_locally','fill_missing_at_venue','duplicate_fill','balance_mismatch',
        'balance_availability_mismatch','reservation_mismatch','position_mismatch','realized_pnl_mismatch','fee_mismatch',
        'currency_mismatch','stale_venue_snapshot','stale_local_snapshot','sequence_gap','unknown_submission','cancel_pending',
        'unsupported_venue_field','account_mode_mismatch'
    ));
CREATE TABLE IF NOT EXISTS execution.paper_dispatch_outbox (
    dispatch_id uuid PRIMARY KEY,
    paper_run_id uuid NOT NULL REFERENCES execution.paper_runs(paper_run_id) ON DELETE CASCADE,
    submission_id uuid NOT NULL UNIQUE REFERENCES execution.paper_order_submissions(submission_id),
    client_order_id text NOT NULL,
    idempotency_key text NOT NULL,
    economics_sha256 text NOT NULL CHECK (economics_sha256 ~ '^[0-9a-f]{64}$'),
    state text NOT NULL CHECK (state IN ('prepared','dispatch_claimed','acknowledged','explicitly_rejected','unknown','recovered')),
    eligible_at_utc timestamptz NOT NULL,
    claimed_at_utc timestamptz,
    claim_token uuid,
    claimed_by text,
    attempt_count integer NOT NULL DEFAULT 0 CHECK (attempt_count >= 0),
    last_outcome_at_utc timestamptz,
    venue_order_id text,
    updated_at_utc timestamptz NOT NULL,
    record_sha256 text NOT NULL CHECK (record_sha256 ~ '^[0-9a-f]{64}$'),
    UNIQUE (paper_run_id,client_order_id),
    UNIQUE (paper_run_id,idempotency_key),
    CHECK ((state='prepared' AND claim_token IS NULL AND claimed_at_utc IS NULL AND claimed_by IS NULL) OR state<>'prepared'),
    CHECK ((state='dispatch_claimed' AND claim_token IS NOT NULL AND claimed_at_utc IS NOT NULL AND claimed_by IS NOT NULL) OR state<>'dispatch_claimed')
);

CREATE TABLE IF NOT EXISTS execution.paper_dispatch_events (
    dispatch_event_id uuid PRIMARY KEY,
    dispatch_id uuid NOT NULL REFERENCES execution.paper_dispatch_outbox(dispatch_id) ON DELETE CASCADE,
    paper_run_id uuid NOT NULL REFERENCES execution.paper_runs(paper_run_id) ON DELETE CASCADE,
    submission_id uuid NOT NULL REFERENCES execution.paper_order_submissions(submission_id),
    event_type text NOT NULL CHECK (event_type IN ('prepared','claimed','acknowledged','explicitly_rejected','unknown','recovered','cancel_requested','cancel_claimed','cancel_confirmed','cancel_unknown')),
    occurred_at_utc timestamptz NOT NULL,
    claim_token uuid,
    worker_id text,
    transport_classification text,
    evidence_sha256 text CHECK (evidence_sha256 IS NULL OR evidence_sha256 ~ '^[0-9a-f]{64}$'),
    record_sha256 text NOT NULL CHECK (record_sha256 ~ '^[0-9a-f]{64}$'),
    UNIQUE (dispatch_id,event_type,claim_token,record_sha256)
);

CREATE TABLE IF NOT EXISTS execution.paper_cancel_outbox (
    cancel_id uuid PRIMARY KEY,
    dispatch_id uuid NOT NULL REFERENCES execution.paper_dispatch_outbox(dispatch_id) ON DELETE CASCADE,
    paper_run_id uuid NOT NULL REFERENCES execution.paper_runs(paper_run_id) ON DELETE CASCADE,
    submission_id uuid NOT NULL REFERENCES execution.paper_order_submissions(submission_id),
    client_order_id text NOT NULL,
    state text NOT NULL CHECK (state IN ('cancel_requested','cancel_claimed','cancel_confirmed','cancel_unknown')),
    requested_at_utc timestamptz NOT NULL,
    claimed_at_utc timestamptz,
    claim_token uuid,
    claimed_by text,
    attempt_count integer NOT NULL DEFAULT 0 CHECK (attempt_count >= 0),
    updated_at_utc timestamptz NOT NULL,
    record_sha256 text NOT NULL CHECK (record_sha256 ~ '^[0-9a-f]{64}$'),
    UNIQUE (dispatch_id),
    UNIQUE (paper_run_id,client_order_id)
);

CREATE TABLE IF NOT EXISTS execution.paper_account_balance_projection (
    paper_run_id uuid NOT NULL REFERENCES execution.paper_runs(paper_run_id) ON DELETE CASCADE,
    currency text NOT NULL,
    total numeric NOT NULL CHECK (total >= 0),
    version bigint NOT NULL CHECK (version >= 0),
    updated_at_utc timestamptz NOT NULL,
    source_fill_id uuid REFERENCES execution.paper_fills(fill_id),
    record_sha256 text NOT NULL CHECK (record_sha256 ~ '^[0-9a-f]{64}$'),
    PRIMARY KEY (paper_run_id,currency)
);

CREATE TABLE IF NOT EXISTS execution.paper_account_position_projection (
    paper_run_id uuid NOT NULL REFERENCES execution.paper_runs(paper_run_id) ON DELETE CASCADE,
    series_identity_sha256 text NOT NULL CHECK (series_identity_sha256 ~ '^[0-9a-f]{64}$'),
    instrument_id text NOT NULL,
    series_identity_jsonb jsonb NOT NULL,
    accounting_mode text NOT NULL CHECK (accounting_mode IN ('spot','linear_perpetual')),
    quantity numeric NOT NULL,
    average_entry_price numeric CHECK (average_entry_price IS NULL OR average_entry_price > 0),
    realized_pnl numeric NOT NULL,
    funding numeric NOT NULL,
    version bigint NOT NULL CHECK (version >= 0),
    updated_at_utc timestamptz NOT NULL,
    source_fill_id uuid REFERENCES execution.paper_fills(fill_id),
    record_sha256 text NOT NULL CHECK (record_sha256 ~ '^[0-9a-f]{64}$'),
    PRIMARY KEY (paper_run_id,series_identity_sha256),
    CHECK ((quantity <> 0) OR average_entry_price IS NULL),
    CHECK ((accounting_mode <> 'spot') OR quantity >= 0)
);

CREATE OR REPLACE FUNCTION execution.phase7_reject_immutable_change()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'immutable Phase 7 record % cannot be %', TG_TABLE_NAME, TG_OP;
END;
$$;

CREATE OR REPLACE FUNCTION execution.phase7_guard_approval_update()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF ROW(NEW.paper_run_id,NEW.preflight_report_id,NEW.configuration_sha256,NEW.account_snapshot_sha256,
           NEW.credential_reference_sha256,NEW.provider,NEW.environment,NEW.allowed_instruments_jsonb,
           NEW.maximum_approved_total_notional,NEW.created_at_utc,NEW.expires_at_utc,NEW.approving_actor,
           NEW.approval_nonce)
       IS DISTINCT FROM
       ROW(OLD.paper_run_id,OLD.preflight_report_id,OLD.configuration_sha256,OLD.account_snapshot_sha256,
           OLD.credential_reference_sha256,OLD.provider,OLD.environment,OLD.allowed_instruments_jsonb,
           OLD.maximum_approved_total_notional,OLD.created_at_utc,OLD.expires_at_utc,OLD.approving_actor,
           OLD.approval_nonce) THEN
        RAISE EXCEPTION 'paper approval binding is immutable';
    END IF;
    IF OLD.state <> NEW.state AND NOT (
        OLD.state='valid' AND NEW.state IN ('consumed','expired','revoked')
    ) THEN
        RAISE EXCEPTION 'invalid paper approval state transition % -> %', OLD.state, NEW.state;
    END IF;
    IF OLD.state = NEW.state AND OLD.record_sha256 IS DISTINCT FROM NEW.record_sha256 THEN
        RAISE EXCEPTION 'paper approval record hash may change only with a controlled state transition';
    END IF;
    RETURN NEW;
END;
$$;

CREATE OR REPLACE FUNCTION execution.phase7_guard_submission_update()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF ROW(NEW.paper_run_id,NEW.manifest_id,NEW.approval_id,NEW.order_intent_id,NEW.client_order_id,
           NEW.idempotency_key,NEW.series_identity_sha256,NEW.instrument_id,NEW.side,NEW.order_type,
           NEW.time_in_force,NEW.accounting_mode,NEW.quantity,NEW.reference_price,NEW.submitted_notional,
           NEW.limit_price,NEW.stop_price,NEW.submitted_at_utc,NEW.economics_sha256,NEW.pre_submit_risk_sha256)
       IS DISTINCT FROM
       ROW(OLD.paper_run_id,OLD.manifest_id,OLD.approval_id,OLD.order_intent_id,OLD.client_order_id,
           OLD.idempotency_key,OLD.series_identity_sha256,OLD.instrument_id,OLD.side,OLD.order_type,
           OLD.time_in_force,OLD.accounting_mode,OLD.quantity,OLD.reference_price,OLD.submitted_notional,
           OLD.limit_price,OLD.stop_price,OLD.submitted_at_utc,OLD.economics_sha256,OLD.pre_submit_risk_sha256) THEN
        RAISE EXCEPTION 'paper submission economics and control bindings are immutable';
    END IF;
    RETURN NEW;
END;
$$;

CREATE OR REPLACE FUNCTION execution.phase7_guard_dispatch_update()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF ROW(NEW.paper_run_id,NEW.submission_id,NEW.client_order_id,NEW.idempotency_key,NEW.economics_sha256,NEW.eligible_at_utc)
       IS DISTINCT FROM ROW(OLD.paper_run_id,OLD.submission_id,OLD.client_order_id,OLD.idempotency_key,OLD.economics_sha256,OLD.eligible_at_utc) THEN
        RAISE EXCEPTION 'paper dispatch economic identity is immutable';
    END IF;
    RETURN NEW;
END;
$$;
CREATE OR REPLACE FUNCTION execution.phase7_guard_reservation_update()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF ROW(NEW.paper_run_id,NEW.submission_id,NEW.client_order_id,NEW.currency,NEW.original_amount,NEW.original_quantity,NEW.created_at_utc,NEW.economics_sha256)
       IS DISTINCT FROM ROW(OLD.paper_run_id,OLD.submission_id,OLD.client_order_id,OLD.currency,OLD.original_amount,OLD.original_quantity,OLD.created_at_utc,OLD.economics_sha256) THEN
        RAISE EXCEPTION 'paper reservation economic identity is immutable';
    END IF;
    RETURN NEW;
END;
$$;
CREATE OR REPLACE FUNCTION execution.phase7_guard_cancel_update()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF ROW(NEW.dispatch_id,NEW.paper_run_id,NEW.submission_id,NEW.client_order_id,NEW.requested_at_utc)
       IS DISTINCT FROM ROW(OLD.dispatch_id,OLD.paper_run_id,OLD.submission_id,OLD.client_order_id,OLD.requested_at_utc) THEN
        RAISE EXCEPTION 'paper cancellation identity is immutable';
    END IF;
    RETURN NEW;
END;
$$;
CREATE OR REPLACE FUNCTION execution.phase7_guard_terminal_run()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF OLD.state IN ('completed','failed','killed') AND NEW IS DISTINCT FROM OLD THEN
        RAISE EXCEPTION 'terminal paper run cannot be changed';
    END IF;
    RETURN NEW;
END;
$$;

CREATE OR REPLACE FUNCTION execution.phase7_guard_killed_switch()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF OLD.state='killed' AND NEW IS DISTINCT FROM OLD THEN
        RAISE EXCEPTION 'killed paper run cannot be reset';
    END IF;
    IF OLD.state='triggered' AND NEW.state NOT IN ('triggered','cancelling','killed') THEN
        RAISE EXCEPTION 'triggered kill switch cannot be re-armed or reset';
    END IF;
    IF OLD.state='cancelling' AND NEW.state NOT IN ('cancelling','killed') THEN
        RAISE EXCEPTION 'cancelling kill switch can only become killed';
    END IF;
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS phase7_manifest_immutable ON execution.paper_run_manifests;
CREATE TRIGGER phase7_manifest_immutable BEFORE UPDATE OR DELETE ON execution.paper_run_manifests
FOR EACH ROW EXECUTE FUNCTION execution.phase7_reject_immutable_change();
DROP TRIGGER IF EXISTS phase7_configuration_immutable ON execution.paper_configuration_snapshots;
CREATE TRIGGER phase7_configuration_immutable BEFORE UPDATE OR DELETE ON execution.paper_configuration_snapshots
FOR EACH ROW EXECUTE FUNCTION execution.phase7_reject_immutable_change();
DROP TRIGGER IF EXISTS phase7_approval_delete_immutable ON execution.paper_approvals;
CREATE TRIGGER phase7_approval_delete_immutable BEFORE DELETE ON execution.paper_approvals
FOR EACH ROW EXECUTE FUNCTION execution.phase7_reject_immutable_change();
DROP TRIGGER IF EXISTS phase7_approval_update_guard ON execution.paper_approvals;
CREATE TRIGGER phase7_approval_update_guard BEFORE UPDATE ON execution.paper_approvals
FOR EACH ROW EXECUTE FUNCTION execution.phase7_guard_approval_update();
DROP TRIGGER IF EXISTS phase7_submission_delete_immutable ON execution.paper_order_submissions;
CREATE TRIGGER phase7_submission_delete_immutable BEFORE DELETE ON execution.paper_order_submissions
FOR EACH ROW EXECUTE FUNCTION execution.phase7_reject_immutable_change();
DROP TRIGGER IF EXISTS phase7_submission_update_guard ON execution.paper_order_submissions;
CREATE TRIGGER phase7_submission_update_guard BEFORE UPDATE ON execution.paper_order_submissions
FOR EACH ROW EXECUTE FUNCTION execution.phase7_guard_submission_update();
DROP TRIGGER IF EXISTS phase7_dispatch_update_guard ON execution.paper_dispatch_outbox;
CREATE TRIGGER phase7_dispatch_update_guard BEFORE UPDATE ON execution.paper_dispatch_outbox
FOR EACH ROW EXECUTE FUNCTION execution.phase7_guard_dispatch_update();
DROP TRIGGER IF EXISTS phase7_reservation_update_guard ON execution.paper_reservations;
CREATE TRIGGER phase7_reservation_update_guard BEFORE UPDATE ON execution.paper_reservations
FOR EACH ROW EXECUTE FUNCTION execution.phase7_guard_reservation_update();
DROP TRIGGER IF EXISTS phase7_cancel_update_guard ON execution.paper_cancel_outbox;
CREATE TRIGGER phase7_cancel_update_guard BEFORE UPDATE ON execution.paper_cancel_outbox
FOR EACH ROW EXECUTE FUNCTION execution.phase7_guard_cancel_update();
DROP TRIGGER IF EXISTS phase7_runtime_risk_immutable ON execution.paper_runtime_risk_decisions;
CREATE TRIGGER phase7_runtime_risk_immutable BEFORE UPDATE OR DELETE ON execution.paper_runtime_risk_decisions
FOR EACH ROW EXECUTE FUNCTION execution.phase7_reject_immutable_change();
DROP TRIGGER IF EXISTS phase7_approval_events_append_only ON execution.paper_approval_state_events;
CREATE TRIGGER phase7_approval_events_append_only BEFORE UPDATE OR DELETE ON execution.paper_approval_state_events
FOR EACH ROW EXECUTE FUNCTION execution.phase7_reject_immutable_change();
DROP TRIGGER IF EXISTS phase7_reservation_events_append_only ON execution.paper_reservation_events;
CREATE TRIGGER phase7_reservation_events_append_only BEFORE UPDATE OR DELETE ON execution.paper_reservation_events
FOR EACH ROW EXECUTE FUNCTION execution.phase7_reject_immutable_change();
DROP TRIGGER IF EXISTS phase7_dispatch_events_append_only ON execution.paper_dispatch_events;
CREATE TRIGGER phase7_dispatch_events_append_only BEFORE UPDATE OR DELETE ON execution.paper_dispatch_events
FOR EACH ROW EXECUTE FUNCTION execution.phase7_reject_immutable_change();
DROP TRIGGER IF EXISTS phase7_orders_append_only ON execution.paper_orders;
CREATE TRIGGER phase7_orders_append_only BEFORE UPDATE OR DELETE ON execution.paper_orders
FOR EACH ROW EXECUTE FUNCTION execution.phase7_reject_immutable_change();
DROP TRIGGER IF EXISTS phase7_fills_append_only ON execution.paper_fills;
CREATE TRIGGER phase7_fills_append_only BEFORE UPDATE OR DELETE ON execution.paper_fills
FOR EACH ROW EXECUTE FUNCTION execution.phase7_reject_immutable_change();
DROP TRIGGER IF EXISTS phase7_run_terminal_guard ON execution.paper_runs;
CREATE TRIGGER phase7_run_terminal_guard BEFORE UPDATE ON execution.paper_runs
FOR EACH ROW EXECUTE FUNCTION execution.phase7_guard_terminal_run();
DROP TRIGGER IF EXISTS phase7_killed_switch_guard ON execution.paper_kill_switches;
CREATE TRIGGER phase7_killed_switch_guard BEFORE UPDATE ON execution.paper_kill_switches
FOR EACH ROW EXECUTE FUNCTION execution.phase7_guard_killed_switch();

CREATE INDEX idx_phase7_configuration_account ON execution.paper_configuration_snapshots(provider,environment,account_reference);
CREATE INDEX idx_phase7_approval_events_run_time ON execution.paper_approval_state_events(paper_run_id,occurred_at_utc,approval_event_id);
CREATE INDEX idx_phase7_runtime_risk_run_time ON execution.paper_runtime_risk_decisions(paper_run_id,decided_at_utc,runtime_risk_decision_id);
CREATE INDEX idx_phase7_reservations_open ON execution.paper_reservations(paper_run_id,client_order_id) WHERE state='open';
CREATE INDEX idx_phase7_reservation_events_time ON execution.paper_reservation_events(paper_run_id,occurred_at_utc,reservation_event_id);
CREATE INDEX idx_phase7_dispatch_ready ON execution.paper_dispatch_outbox(state,eligible_at_utc,dispatch_id) WHERE state='prepared';
CREATE INDEX idx_phase7_dispatch_unknown ON execution.paper_dispatch_outbox(paper_run_id,updated_at_utc,dispatch_id) WHERE state IN ('dispatch_claimed','unknown');
CREATE INDEX idx_phase7_dispatch_events_time ON execution.paper_dispatch_events(paper_run_id,occurred_at_utc,dispatch_event_id);
CREATE INDEX idx_phase7_cancel_unresolved ON execution.paper_cancel_outbox(paper_run_id,updated_at_utc,cancel_id) WHERE state IN ('cancel_requested','cancel_claimed','cancel_unknown');
CREATE INDEX idx_phase7_balance_projection_run ON execution.paper_account_balance_projection(paper_run_id,currency);
CREATE INDEX idx_phase7_position_projection_run ON execution.paper_account_position_projection(paper_run_id,series_identity_sha256);

COMMENT ON TABLE execution.paper_configuration_snapshots IS 'Immutable complete public-safe Phase 7 configuration authority.';
COMMENT ON TABLE execution.paper_dispatch_outbox IS 'Durable-before-side-effect submission authority; a committed claim precedes every venue call.';
COMMENT ON TABLE execution.paper_dispatch_events IS 'Append-only dispatch and cancellation claim/outcome evidence.';
COMMENT ON TABLE execution.paper_reservations IS 'PostgreSQL-authoritative cash or inventory reservation projection.';
COMMENT ON TABLE execution.paper_runtime_risk_decisions IS 'Immutable full runtime limit decision computed while holding the run risk lock.';
COMMENT ON TABLE execution.paper_run_risk_state IS 'Locked PostgreSQL risk-budget and freshness projection reconstructed after process restart.';
