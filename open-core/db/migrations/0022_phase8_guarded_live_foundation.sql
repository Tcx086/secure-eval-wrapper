-- Phase 8A guarded live execution foundation.
-- Migrations 0001 through 0021 are immutable. PostgreSQL is the sole runtime authority.
-- Production writes, cancellations, transfers, withdrawals, borrowing, leverage, derivatives,
-- automatic flattening, and production FIX remain disabled and unimplemented.

CREATE TABLE IF NOT EXISTS execution.live_configuration_snapshots (
    configuration_snapshot_id uuid PRIMARY KEY,
    configuration_sha256 text NOT NULL UNIQUE CHECK (configuration_sha256 ~ '^[0-9a-f]{64}$'),
    provider text NOT NULL CHECK (provider = 'okx'),
    environment text NOT NULL CHECK (environment = 'production'),
    account_fingerprint text NOT NULL CHECK (length(account_fingerprint) <= 32),
    dry_run boolean NOT NULL CHECK (dry_run),
    read_only_preflight boolean NOT NULL CHECK (read_only_preflight),
    production_write_enabled boolean NOT NULL CHECK (NOT production_write_enabled),
    configuration_jsonb jsonb NOT NULL,
    record_sha256 text NOT NULL CHECK (record_sha256 ~ '^[0-9a-f]{64}$'),
    created_at_utc timestamptz NOT NULL
);

CREATE TABLE IF NOT EXISTS execution.live_credential_references (
    credential_reference_id uuid PRIMARY KEY,
    provider text NOT NULL CHECK (provider = 'okx'),
    alias text NOT NULL,
    source_type text NOT NULL CHECK (source_type IN ('environment','os_credential_store','injected_local')),
    account_fingerprint text NOT NULL CHECK (length(account_fingerprint) <= 32),
    loaded boolean NOT NULL,
    verified_at_utc timestamptz,
    permission_summary_jsonb jsonb NOT NULL,
    record_sha256 text NOT NULL CHECK (record_sha256 ~ '^[0-9a-f]{64}$'),
    created_at_utc timestamptz NOT NULL,
    UNIQUE(provider, alias, account_fingerprint, record_sha256)
);

CREATE TABLE IF NOT EXISTS execution.live_account_snapshots (
    account_snapshot_id uuid PRIMARY KEY,
    live_run_id uuid NOT NULL,
    account_fingerprint text NOT NULL CHECK (length(account_fingerprint) <= 32),
    fetched_at_utc timestamptz NOT NULL,
    venue_time_at_utc timestamptz NOT NULL,
    total_equity numeric NOT NULL CHECK (total_equity >= 0),
    available_equity numeric NOT NULL CHECK (available_equity >= 0),
    reserved_equity numeric NOT NULL CHECK (reserved_equity >= 0),
    open_order_count integer NOT NULL CHECK (open_order_count >= 0),
    account_mode text NOT NULL CHECK (account_mode = 'spot_cash'),
    snapshot_jsonb jsonb NOT NULL,
    record_sha256 text NOT NULL CHECK (record_sha256 ~ '^[0-9a-f]{64}$'),
    UNIQUE(live_run_id, fetched_at_utc, record_sha256)
);

CREATE TABLE IF NOT EXISTS execution.live_preflight_reports (
    preflight_report_id uuid PRIMARY KEY,
    live_run_id uuid NOT NULL,
    configuration_sha256 text NOT NULL CHECK (configuration_sha256 ~ '^[0-9a-f]{64}$'),
    implementation_sha256 text NOT NULL CHECK (implementation_sha256 ~ '^[0-9a-f]{64}$'),
    repository_commit_sha text NOT NULL,
    endpoint_catalog_sha256 text NOT NULL CHECK (endpoint_catalog_sha256 ~ '^[0-9a-f]{64}$'),
    credential_reference_sha256 text NOT NULL CHECK (credential_reference_sha256 ~ '^[0-9a-f]{64}$'),
    account_snapshot_sha256 text NOT NULL CHECK (account_snapshot_sha256 ~ '^[0-9a-f]{64}$'),
    evaluated_at_utc timestamptz NOT NULL,
    status text NOT NULL CHECK (status IN ('passed','blocked')),
    blockers_jsonb jsonb NOT NULL,
    warnings_jsonb jsonb NOT NULL,
    record_sha256 text NOT NULL CHECK (record_sha256 ~ '^[0-9a-f]{64}$'),
    UNIQUE(live_run_id, configuration_sha256, record_sha256)
);

CREATE TABLE IF NOT EXISTS execution.live_preflight_checks (
    preflight_check_id uuid PRIMARY KEY,
    preflight_report_id uuid NOT NULL REFERENCES execution.live_preflight_reports(preflight_report_id) ON DELETE RESTRICT,
    check_name text NOT NULL,
    passed boolean NOT NULL,
    required boolean NOT NULL,
    evaluated_at_utc timestamptz NOT NULL,
    source_timestamp_utc timestamptz,
    explanation text NOT NULL,
    evidence_sha256 text NOT NULL CHECK (evidence_sha256 ~ '^[0-9a-f]{64}$'),
    record_sha256 text NOT NULL CHECK (record_sha256 ~ '^[0-9a-f]{64}$'),
    UNIQUE(preflight_report_id, check_name)
);

CREATE TABLE IF NOT EXISTS execution.live_approvals (
    approval_id uuid PRIMARY KEY,
    live_run_id uuid NOT NULL,
    preflight_report_id uuid NOT NULL REFERENCES execution.live_preflight_reports(preflight_report_id) ON DELETE RESTRICT,
    configuration_sha256 text NOT NULL CHECK (configuration_sha256 ~ '^[0-9a-f]{64}$'),
    account_fingerprint text NOT NULL CHECK (length(account_fingerprint) <= 32),
    provider text NOT NULL CHECK (provider = 'okx'),
    environment text NOT NULL CHECK (environment = 'production'),
    manifest_sha256 text NOT NULL CHECK (manifest_sha256 ~ '^[0-9a-f]{64}$'),
    confirmation_challenge_sha256 text NOT NULL CHECK (confirmation_challenge_sha256 ~ '^[0-9a-f]{64}$'),
    maximum_total_approved_notional numeric NOT NULL CHECK (maximum_total_approved_notional > 0),
    consumed_notional numeric NOT NULL DEFAULT 0 CHECK (consumed_notional >= 0 AND consumed_notional <= maximum_total_approved_notional),
    created_at_utc timestamptz NOT NULL,
    expires_at_utc timestamptz NOT NULL CHECK (expires_at_utc > created_at_utc),
    approving_actor text NOT NULL,
    nonce text NOT NULL,
    approval_jsonb jsonb NOT NULL,
    record_sha256 text NOT NULL CHECK (record_sha256 ~ '^[0-9a-f]{64}$'),
    UNIQUE(live_run_id, nonce)
);

CREATE TABLE IF NOT EXISTS execution.live_run_manifests (
    manifest_id uuid PRIMARY KEY,
    live_run_id uuid NOT NULL UNIQUE,
    approval_id uuid NOT NULL REFERENCES execution.live_approvals(approval_id) ON DELETE RESTRICT,
    preflight_report_id uuid NOT NULL REFERENCES execution.live_preflight_reports(preflight_report_id) ON DELETE RESTRICT,
    initial_account_snapshot_id uuid NOT NULL REFERENCES execution.live_account_snapshots(account_snapshot_id) ON DELETE RESTRICT,
    configuration_sha256 text NOT NULL CHECK (configuration_sha256 ~ '^[0-9a-f]{64}$'),
    implementation_sha256 text NOT NULL CHECK (implementation_sha256 ~ '^[0-9a-f]{64}$'),
    repository_commit_sha text NOT NULL,
    endpoint_catalog_sha256 text NOT NULL CHECK (endpoint_catalog_sha256 ~ '^[0-9a-f]{64}$'),
    credential_reference_sha256 text NOT NULL CHECK (credential_reference_sha256 ~ '^[0-9a-f]{64}$'),
    manifest_sha256 text NOT NULL CHECK (manifest_sha256 ~ '^[0-9a-f]{64}$'),
    dry_run boolean NOT NULL CHECK (dry_run),
    production_write_enabled boolean NOT NULL CHECK (NOT production_write_enabled),
    manifest_jsonb jsonb NOT NULL,
    record_sha256 text NOT NULL CHECK (record_sha256 ~ '^[0-9a-f]{64}$'),
    created_at_utc timestamptz NOT NULL
);

CREATE TABLE IF NOT EXISTS execution.live_runs (
    live_run_id uuid PRIMARY KEY,
    manifest_id uuid NOT NULL UNIQUE REFERENCES execution.live_run_manifests(manifest_id) ON DELETE RESTRICT,
    state text NOT NULL CHECK (state IN ('created','preflight_passed','dry_run_running','dry_run_completed','blocked','stopped')),
    dry_run boolean NOT NULL CHECK (dry_run),
    production_write_enabled boolean NOT NULL CHECK (NOT production_write_enabled),
    started_at_utc timestamptz,
    completed_at_utc timestamptz,
    record_sha256 text NOT NULL CHECK (record_sha256 ~ '^[0-9a-f]{64}$'),
    version integer NOT NULL DEFAULT 0 CHECK (version >= 0)
);

CREATE TABLE IF NOT EXISTS execution.live_kill_switches (
    kill_switch_id uuid PRIMARY KEY,
    live_run_id uuid NOT NULL UNIQUE,
    state text NOT NULL CHECK (state IN ('armed','triggered','cancellation_in_progress','cancellation_ambiguous','stopped','reset_pending','reset')),
    reason text,
    evidence_sha256 text NOT NULL CHECK (evidence_sha256 ~ '^[0-9a-f]{64}$'),
    requires_fresh_preflight boolean NOT NULL DEFAULT false,
    requires_new_approval boolean NOT NULL DEFAULT false,
    updated_at_utc timestamptz NOT NULL,
    record_sha256 text NOT NULL CHECK (record_sha256 ~ '^[0-9a-f]{64}$'),
    version integer NOT NULL DEFAULT 0 CHECK (version >= 0)
);

CREATE TABLE IF NOT EXISTS execution.live_kill_events (
    kill_event_id uuid PRIMARY KEY,
    kill_switch_id uuid NOT NULL REFERENCES execution.live_kill_switches(kill_switch_id) ON DELETE RESTRICT,
    prior_state text,
    new_state text NOT NULL,
    reason text NOT NULL,
    evidence_jsonb jsonb NOT NULL,
    occurred_at_utc timestamptz NOT NULL,
    record_sha256 text NOT NULL CHECK (record_sha256 ~ '^[0-9a-f]{64}$'),
    UNIQUE(kill_switch_id, occurred_at_utc, record_sha256)
);

CREATE TABLE IF NOT EXISTS execution.live_order_intents (
    order_intent_id uuid PRIMARY KEY,
    live_run_id uuid NOT NULL,
    manifest_id uuid NOT NULL REFERENCES execution.live_run_manifests(manifest_id) ON DELETE RESTRICT,
    client_order_id text NOT NULL UNIQUE,
    instrument_id text NOT NULL,
    side text NOT NULL CHECK (side IN ('buy','sell')),
    order_type text NOT NULL CHECK (order_type = 'limit'),
    accounting_mode text NOT NULL CHECK (accounting_mode = 'spot'),
    quantity numeric NOT NULL CHECK (quantity > 0),
    limit_price numeric NOT NULL CHECK (limit_price > 0),
    reference_price numeric NOT NULL CHECK (reference_price > 0),
    market_evidence_id uuid NOT NULL,
    market_evidence_sha256 text NOT NULL CHECK (market_evidence_sha256 ~ '^[0-9a-f]{64}$'),
    instrument_metadata_sha256 text NOT NULL CHECK (instrument_metadata_sha256 ~ '^[0-9a-f]{64}$'),
    account_snapshot_sha256 text NOT NULL CHECK (account_snapshot_sha256 ~ '^[0-9a-f]{64}$'),
    reconciliation_sha256 text NOT NULL CHECK (reconciliation_sha256 ~ '^[0-9a-f]{64}$'),
    economic_sha256 text NOT NULL CHECK (economic_sha256 ~ '^[0-9a-f]{64}$'),
    state text NOT NULL CHECK (state IN ('dry_run_prepared','dry_run_blocked','dry_run_suppressed','pending_recovery')),
    created_at_utc timestamptz NOT NULL,
    record_sha256 text NOT NULL CHECK (record_sha256 ~ '^[0-9a-f]{64}$')
);

CREATE TABLE IF NOT EXISTS execution.live_runtime_risk_decisions (
    risk_decision_id uuid PRIMARY KEY,
    order_intent_id uuid NOT NULL UNIQUE REFERENCES execution.live_order_intents(order_intent_id) ON DELETE RESTRICT,
    accepted boolean NOT NULL,
    reasons_jsonb jsonb NOT NULL,
    market_evidence_price numeric NOT NULL CHECK (market_evidence_price > 0),
    risk_reference_price numeric NOT NULL CHECK (risk_reference_price > 0),
    worst_case_order_price numeric NOT NULL CHECK (worst_case_order_price > 0),
    risk_notional numeric NOT NULL CHECK (risk_notional > 0),
    reservation_notional numeric NOT NULL CHECK (reservation_notional > 0),
    price_deviation_bps numeric NOT NULL CHECK (price_deviation_bps >= 0),
    price_source_sha256 text NOT NULL CHECK (price_source_sha256 ~ '^[0-9a-f]{64}$'),
    calculator_version text NOT NULL,
    decided_at_utc timestamptz NOT NULL,
    record_sha256 text NOT NULL CHECK (record_sha256 ~ '^[0-9a-f]{64}$')
);

CREATE TABLE IF NOT EXISTS execution.live_reservations (
    reservation_id uuid PRIMARY KEY,
    order_intent_id uuid NOT NULL UNIQUE REFERENCES execution.live_order_intents(order_intent_id) ON DELETE RESTRICT,
    currency text NOT NULL,
    amount numeric NOT NULL CHECK (amount > 0),
    risk_notional numeric NOT NULL CHECK (risk_notional > 0),
    state text NOT NULL CHECK (state IN ('projected','released','consumed')),
    dry_run boolean NOT NULL CHECK (dry_run),
    created_at_utc timestamptz NOT NULL,
    updated_at_utc timestamptz NOT NULL,
    record_sha256 text NOT NULL CHECK (record_sha256 ~ '^[0-9a-f]{64}$'),
    version integer NOT NULL DEFAULT 0 CHECK (version >= 0)
);

CREATE TABLE IF NOT EXISTS execution.live_dispatch_outbox (
    dispatch_outbox_id uuid PRIMARY KEY,
    order_intent_id uuid NOT NULL UNIQUE REFERENCES execution.live_order_intents(order_intent_id) ON DELETE RESTRICT,
    client_order_id text NOT NULL UNIQUE,
    state text NOT NULL CHECK (state IN ('dry_run_prepared','dry_run_suppressed','pending_recovery')),
    provider_request_sha256 text NOT NULL CHECK (provider_request_sha256 ~ '^[0-9a-f]{64}$'),
    request_jsonb jsonb NOT NULL,
    worker_identity text,
    claim_token uuid,
    lease_expires_at_utc timestamptz,
    recovery_generation integer NOT NULL DEFAULT 0 CHECK (recovery_generation >= 0),
    recovery_claim_token uuid,
    recovery_worker_identity text,
    recovery_lease_expires_at_utc timestamptz,
    created_at_utc timestamptz NOT NULL,
    updated_at_utc timestamptz NOT NULL,
    suppressed_at_utc timestamptz,
    record_sha256 text NOT NULL CHECK (record_sha256 ~ '^[0-9a-f]{64}$'),
    version integer NOT NULL DEFAULT 0 CHECK (version >= 0),
    CHECK ((claim_token IS NULL) = (worker_identity IS NULL)),
    CHECK ((recovery_claim_token IS NULL) = (recovery_worker_identity IS NULL))
);

CREATE TABLE IF NOT EXISTS execution.live_dispatch_events (
    dispatch_event_id uuid PRIMARY KEY,
    dispatch_outbox_id uuid NOT NULL REFERENCES execution.live_dispatch_outbox(dispatch_outbox_id) ON DELETE RESTRICT,
    event_type text NOT NULL CHECK (event_type IN ('prepared','claimed','write_suppressed','recovery_claimed','observation_persisted')),
    event_jsonb jsonb NOT NULL,
    occurred_at_utc timestamptz NOT NULL,
    record_sha256 text NOT NULL CHECK (record_sha256 ~ '^[0-9a-f]{64}$'),
    UNIQUE(dispatch_outbox_id, event_type, record_sha256)
);

CREATE TABLE IF NOT EXISTS execution.live_cancel_outbox (
    cancel_outbox_id uuid PRIMARY KEY,
    live_run_id uuid NOT NULL,
    order_intent_id uuid NOT NULL REFERENCES execution.live_order_intents(order_intent_id) ON DELETE RESTRICT,
    client_order_id text NOT NULL,
    state text NOT NULL CHECK (state IN ('dry_run_prepared','dry_run_suppressed','pending_recovery')),
    provider_request_sha256 text NOT NULL CHECK (provider_request_sha256 ~ '^[0-9a-f]{64}$'),
    request_jsonb jsonb NOT NULL,
    worker_identity text,
    claim_token uuid,
    lease_expires_at_utc timestamptz,
    recovery_generation integer NOT NULL DEFAULT 0 CHECK (recovery_generation >= 0),
    created_at_utc timestamptz NOT NULL,
    updated_at_utc timestamptz NOT NULL,
    record_sha256 text NOT NULL CHECK (record_sha256 ~ '^[0-9a-f]{64}$')
);

CREATE TABLE IF NOT EXISTS execution.live_transport_attempts (
    transport_attempt_id uuid PRIMARY KEY,
    live_run_id uuid NOT NULL,
    order_intent_id uuid,
    operation text NOT NULL,
    provider_request_sha256 text NOT NULL CHECK (provider_request_sha256 ~ '^[0-9a-f]{64}$'),
    provider_response_sha256 text CHECK (provider_response_sha256 IS NULL OR provider_response_sha256 ~ '^[0-9a-f]{64}$'),
    result text NOT NULL CHECK (result IN ('read_succeeded','read_failed','write_suppressed','ambiguous')),
    external_write_attempted boolean NOT NULL CHECK (NOT external_write_attempted),
    successful_write boolean NOT NULL CHECK (NOT successful_write),
    attempted_at_utc timestamptz NOT NULL,
    record_sha256 text NOT NULL CHECK (record_sha256 ~ '^[0-9a-f]{64}$')
);

CREATE TABLE IF NOT EXISTS execution.live_order_observations (
    order_observation_id uuid PRIMARY KEY,
    live_run_id uuid NOT NULL,
    order_intent_id uuid,
    client_order_id text NOT NULL,
    provider_order_id text,
    provider_state text,
    observed_at_utc timestamptz NOT NULL,
    observation_jsonb jsonb NOT NULL,
    provider_response_sha256 text NOT NULL CHECK (provider_response_sha256 ~ '^[0-9a-f]{64}$'),
    record_sha256 text NOT NULL CHECK (record_sha256 ~ '^[0-9a-f]{64}$'),
    UNIQUE(live_run_id, client_order_id, provider_response_sha256)
);

CREATE TABLE IF NOT EXISTS execution.live_order_projections (
    order_intent_id uuid PRIMARY KEY REFERENCES execution.live_order_intents(order_intent_id) ON DELETE RESTRICT,
    live_run_id uuid NOT NULL,
    state text NOT NULL CHECK (state IN ('dry_run_prepared','dry_run_blocked','dry_run_suppressed','pending_recovery')),
    filled_quantity numeric NOT NULL DEFAULT 0 CHECK (filled_quantity >= 0),
    fees numeric NOT NULL DEFAULT 0 CHECK (fees >= 0),
    latest_observation_id uuid REFERENCES execution.live_order_observations(order_observation_id) ON DELETE RESTRICT,
    updated_at_utc timestamptz NOT NULL,
    record_sha256 text NOT NULL CHECK (record_sha256 ~ '^[0-9a-f]{64}$'),
    version integer NOT NULL DEFAULT 0 CHECK (version >= 0)
);

CREATE TABLE IF NOT EXISTS execution.live_fill_observations (
    fill_observation_id uuid PRIMARY KEY,
    live_run_id uuid NOT NULL,
    order_intent_id uuid,
    provider_fill_id text NOT NULL,
    provider_order_id text,
    client_order_id text,
    quantity numeric NOT NULL CHECK (quantity > 0),
    price numeric NOT NULL CHECK (price > 0),
    fee numeric NOT NULL CHECK (fee >= 0),
    fee_currency text NOT NULL,
    observed_at_utc timestamptz NOT NULL,
    provider_response_sha256 text NOT NULL CHECK (provider_response_sha256 ~ '^[0-9a-f]{64}$'),
    record_sha256 text NOT NULL CHECK (record_sha256 ~ '^[0-9a-f]{64}$'),
    UNIQUE(live_run_id, provider_fill_id)
);

CREATE TABLE IF NOT EXISTS execution.live_reconciliations (
    reconciliation_id uuid PRIMARY KEY,
    live_run_id uuid NOT NULL,
    status text NOT NULL CHECK (status IN ('reconciled','blocked','unknown')),
    input_bundle_sha256 text NOT NULL CHECK (input_bundle_sha256 ~ '^[0-9a-f]{64}$'),
    exact_input_jsonb jsonb NOT NULL,
    evaluated_at_utc timestamptz NOT NULL,
    record_sha256 text NOT NULL CHECK (record_sha256 ~ '^[0-9a-f]{64}$'),
    UNIQUE(live_run_id, input_bundle_sha256)
);

CREATE TABLE IF NOT EXISTS execution.live_reconciliation_differences (
    reconciliation_difference_id uuid PRIMARY KEY,
    reconciliation_id uuid NOT NULL REFERENCES execution.live_reconciliations(reconciliation_id) ON DELETE RESTRICT,
    field_name text NOT NULL,
    material boolean NOT NULL,
    local_value_jsonb jsonb,
    venue_value_jsonb jsonb,
    record_sha256 text NOT NULL CHECK (record_sha256 ~ '^[0-9a-f]{64}$'),
    UNIQUE(reconciliation_id, field_name, record_sha256)
);

CREATE TABLE IF NOT EXISTS execution.live_recovery_records (
    recovery_record_id uuid PRIMARY KEY,
    live_run_id uuid NOT NULL,
    order_intent_id uuid,
    client_order_id text NOT NULL,
    generation integer NOT NULL CHECK (generation >= 1),
    worker_identity text NOT NULL,
    claim_token uuid NOT NULL,
    lease_expires_at_utc timestamptz NOT NULL,
    query_first boolean NOT NULL CHECK (query_first),
    observation_bundle_sha256 text CHECK (observation_bundle_sha256 IS NULL OR observation_bundle_sha256 ~ '^[0-9a-f]{64}$'),
    state text NOT NULL CHECK (state IN ('claimed','observed','resolved','ambiguous')),
    created_at_utc timestamptz NOT NULL,
    updated_at_utc timestamptz NOT NULL,
    record_sha256 text NOT NULL CHECK (record_sha256 ~ '^[0-9a-f]{64}$'),
    UNIQUE(live_run_id, client_order_id, generation)
);

CREATE TABLE IF NOT EXISTS execution.live_lifecycle_events (
    lifecycle_event_id uuid PRIMARY KEY,
    live_run_id uuid NOT NULL,
    sequence integer NOT NULL CHECK (sequence >= 0),
    event_type text NOT NULL,
    event_jsonb jsonb NOT NULL,
    occurred_at_utc timestamptz NOT NULL,
    parent_evidence_ids uuid[] NOT NULL DEFAULT '{}',
    record_sha256 text NOT NULL CHECK (record_sha256 ~ '^[0-9a-f]{64}$'),
    UNIQUE(live_run_id, sequence)
);

CREATE TABLE IF NOT EXISTS execution.live_pre_run_summaries (
    summary_id uuid PRIMARY KEY,
    live_run_id uuid NOT NULL UNIQUE,
    generated_at_utc timestamptz NOT NULL,
    public_summary_jsonb jsonb NOT NULL,
    evidence_ids uuid[] NOT NULL,
    record_sha256 text NOT NULL CHECK (record_sha256 ~ '^[0-9a-f]{64}$')
);

CREATE TABLE IF NOT EXISTS execution.live_post_run_summaries (
    summary_id uuid PRIMARY KEY,
    live_run_id uuid NOT NULL UNIQUE,
    generated_at_utc timestamptz NOT NULL,
    public_summary_jsonb jsonb NOT NULL,
    evidence_ids uuid[] NOT NULL,
    external_write_attempted boolean NOT NULL CHECK (NOT external_write_attempted),
    external_write_suppressed boolean NOT NULL,
    record_sha256 text NOT NULL CHECK (record_sha256 ~ '^[0-9a-f]{64}$')
);

CREATE INDEX IF NOT EXISTS idx_live_preflight_reports_run_time ON execution.live_preflight_reports(live_run_id, evaluated_at_utc DESC);
CREATE INDEX IF NOT EXISTS idx_live_account_snapshots_run_time ON execution.live_account_snapshots(live_run_id, fetched_at_utc DESC);
CREATE INDEX IF NOT EXISTS idx_live_intents_run_state ON execution.live_order_intents(live_run_id, state);
CREATE INDEX IF NOT EXISTS idx_live_dispatch_claimable ON execution.live_dispatch_outbox(state, lease_expires_at_utc, created_at_utc);
CREATE INDEX IF NOT EXISTS idx_live_cancel_claimable ON execution.live_cancel_outbox(state, lease_expires_at_utc, created_at_utc);
CREATE INDEX IF NOT EXISTS idx_live_observations_client_time ON execution.live_order_observations(live_run_id, client_order_id, observed_at_utc DESC);
CREATE INDEX IF NOT EXISTS idx_live_fills_run_time ON execution.live_fill_observations(live_run_id, observed_at_utc);
CREATE INDEX IF NOT EXISTS idx_live_recovery_claims ON execution.live_recovery_records(live_run_id, state, lease_expires_at_utc);
CREATE INDEX IF NOT EXISTS idx_live_lifecycle_sequence ON execution.live_lifecycle_events(live_run_id, sequence);

CREATE OR REPLACE FUNCTION execution.prevent_phase8a_state_regression()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF TG_TABLE_NAME = 'live_dispatch_outbox' AND OLD.state = 'dry_run_suppressed' AND NEW.state <> OLD.state THEN
        RAISE EXCEPTION 'suppressed live outbox cannot regress or become successful';
    END IF;
    IF TG_TABLE_NAME = 'live_reservations' AND OLD.state IN ('released','consumed') AND NEW.state = 'projected' THEN
        RAISE EXCEPTION 'closed live reservation cannot reopen';
    END IF;
    IF TG_TABLE_NAME = 'live_order_projections' AND OLD.state = 'dry_run_suppressed' AND NEW.state <> OLD.state THEN
        RAISE EXCEPTION 'suppressed live projection cannot become acknowledged or filled';
    END IF;
    IF NEW.version <> OLD.version + 1 THEN
        RAISE EXCEPTION 'live projection version must advance exactly once';
    END IF;
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS trg_live_dispatch_monotonic ON execution.live_dispatch_outbox;
CREATE TRIGGER trg_live_dispatch_monotonic BEFORE UPDATE ON execution.live_dispatch_outbox FOR EACH ROW EXECUTE FUNCTION execution.prevent_phase8a_state_regression();
DROP TRIGGER IF EXISTS trg_live_reservation_monotonic ON execution.live_reservations;
CREATE TRIGGER trg_live_reservation_monotonic BEFORE UPDATE ON execution.live_reservations FOR EACH ROW EXECUTE FUNCTION execution.prevent_phase8a_state_regression();
DROP TRIGGER IF EXISTS trg_live_projection_monotonic ON execution.live_order_projections;
CREATE TRIGGER trg_live_projection_monotonic BEFORE UPDATE ON execution.live_order_projections FOR EACH ROW EXECUTE FUNCTION execution.prevent_phase8a_state_regression();
