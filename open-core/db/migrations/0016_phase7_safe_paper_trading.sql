-- Phase 7 safe paper trading. Migrations 0001 through 0015 remain immutable.
-- Paper records are separate from Phase 5 simulation and exclude credential material.

CREATE TABLE IF NOT EXISTS execution.paper_credential_references (
    credential_reference_sha256 text PRIMARY KEY CHECK (credential_reference_sha256 ~ '^[0-9a-f]{64}$'),
    provider text NOT NULL CHECK (provider IN ('internal','okx_demo')),
    alias text NOT NULL,
    source_type text NOT NULL CHECK (source_type IN ('environment','injected_test')),
    public_key_fingerprint text NOT NULL CHECK (public_key_fingerprint ~ '^[0-9a-f]{12,32}$'),
    loaded boolean NOT NULL DEFAULT false,
    verified_at_utc timestamptz,
    permissions_summary_jsonb jsonb NOT NULL DEFAULT '[]'::jsonb,
    record_sha256 text NOT NULL CHECK (record_sha256 ~ '^[0-9a-f]{64}$'),
    UNIQUE (provider, alias, public_key_fingerprint)
);

CREATE TABLE IF NOT EXISTS execution.paper_runs (
    paper_run_id uuid PRIMARY KEY,
    provider text NOT NULL CHECK (provider IN ('internal','okx_demo')),
    environment text NOT NULL CHECK (environment IN ('paper_internal','paper_exchange_sandbox')),
    account_reference text NOT NULL,
    state text NOT NULL CHECK (state IN ('created','approved','running','paused','completed','failed','killed')),
    configuration_sha256 text NOT NULL CHECK (configuration_sha256 ~ '^[0-9a-f]{64}$'),
    manifest_id uuid UNIQUE,
    started_at_utc timestamptz NOT NULL,
    updated_at_utc timestamptz NOT NULL,
    ended_at_utc timestamptz,
    summary_jsonb jsonb NOT NULL DEFAULT '{}'::jsonb,
    record_sha256 text NOT NULL CHECK (record_sha256 ~ '^[0-9a-f]{64}$'),
    CHECK (updated_at_utc >= started_at_utc),
    CHECK (ended_at_utc IS NULL OR ended_at_utc >= started_at_utc),
    CHECK ((provider='internal' AND environment='paper_internal') OR (provider='okx_demo' AND environment='paper_exchange_sandbox'))
);

CREATE TABLE IF NOT EXISTS execution.paper_account_snapshots (
    snapshot_id uuid PRIMARY KEY,
    paper_run_id uuid NOT NULL REFERENCES execution.paper_runs(paper_run_id) ON DELETE CASCADE,
    account_reference text NOT NULL,
    status text NOT NULL CHECK (status IN ('fresh','stale','incomplete')),
    fetched_at_utc timestamptz NOT NULL,
    venue_as_of_utc timestamptz NOT NULL,
    account_mode text NOT NULL,
    venue_sequence bigint CHECK (venue_sequence IS NULL OR venue_sequence >= 0),
    source text NOT NULL,
    record_sha256 text NOT NULL CHECK (record_sha256 ~ '^[0-9a-f]{64}$'),
    CHECK (venue_as_of_utc <= fetched_at_utc),
    UNIQUE (paper_run_id, record_sha256)
);

CREATE TABLE IF NOT EXISTS execution.paper_balance_snapshots (
    snapshot_id uuid NOT NULL REFERENCES execution.paper_account_snapshots(snapshot_id) ON DELETE CASCADE,
    currency text NOT NULL,
    total numeric NOT NULL CHECK (total >= 0),
    available numeric NOT NULL CHECK (available >= 0),
    reserved numeric NOT NULL CHECK (reserved >= 0),
    record_sha256 text NOT NULL CHECK (record_sha256 ~ '^[0-9a-f]{64}$'),
    PRIMARY KEY (snapshot_id, currency),
    CHECK (available + reserved <= total)
);

CREATE TABLE IF NOT EXISTS execution.paper_position_snapshots (
    snapshot_id uuid NOT NULL REFERENCES execution.paper_account_snapshots(snapshot_id) ON DELETE CASCADE,
    series_identity_sha256 text NOT NULL CHECK (series_identity_sha256 ~ '^[0-9a-f]{64}$'),
    instrument_id text NOT NULL,
    accounting_mode text NOT NULL CHECK (accounting_mode IN ('spot','linear_perpetual')),
    quantity numeric NOT NULL,
    average_entry_price numeric CHECK (average_entry_price IS NULL OR average_entry_price > 0),
    realized_pnl numeric NOT NULL,
    funding numeric NOT NULL,
    record_sha256 text NOT NULL CHECK (record_sha256 ~ '^[0-9a-f]{64}$'),
    PRIMARY KEY (snapshot_id, series_identity_sha256),
    CHECK ((quantity <> 0) OR average_entry_price IS NULL),
    CHECK ((accounting_mode <> 'spot') OR quantity >= 0)
);

CREATE TABLE IF NOT EXISTS execution.paper_open_order_snapshots (
    snapshot_id uuid NOT NULL REFERENCES execution.paper_account_snapshots(snapshot_id) ON DELETE CASCADE,
    client_order_id text NOT NULL,
    venue_order_id text,
    order_state text NOT NULL,
    record_sha256 text NOT NULL CHECK (record_sha256 ~ '^[0-9a-f]{64}$'),
    PRIMARY KEY (snapshot_id, client_order_id)
);

CREATE TABLE IF NOT EXISTS execution.paper_preflight_reports (
    report_id uuid PRIMARY KEY,
    paper_run_id uuid NOT NULL REFERENCES execution.paper_runs(paper_run_id) ON DELETE CASCADE,
    evaluated_at_utc timestamptz NOT NULL,
    status text NOT NULL CHECK (status IN ('passed','failed')),
    configuration_sha256 text NOT NULL CHECK (configuration_sha256 ~ '^[0-9a-f]{64}$'),
    account_snapshot_sha256 text NOT NULL CHECK (account_snapshot_sha256 ~ '^[0-9a-f]{64}$'),
    implementation_sha256 text NOT NULL CHECK (implementation_sha256 ~ '^[0-9a-f]{64}$'),
    endpoint_catalog_sha256 text NOT NULL CHECK (endpoint_catalog_sha256 ~ '^[0-9a-f]{64}$'),
    credential_reference_sha256 text NOT NULL REFERENCES execution.paper_credential_references(credential_reference_sha256),
    blockers_jsonb jsonb NOT NULL DEFAULT '[]'::jsonb,
    warnings_jsonb jsonb NOT NULL DEFAULT '[]'::jsonb,
    record_sha256 text NOT NULL CHECK (record_sha256 ~ '^[0-9a-f]{64}$'),
    UNIQUE (paper_run_id, configuration_sha256, account_snapshot_sha256),
    UNIQUE (report_id, paper_run_id, configuration_sha256, account_snapshot_sha256, credential_reference_sha256)
);

CREATE TABLE IF NOT EXISTS execution.paper_preflight_checks (
    check_id uuid PRIMARY KEY,
    report_id uuid NOT NULL REFERENCES execution.paper_preflight_reports(report_id) ON DELETE CASCADE,
    check_name text NOT NULL,
    status text NOT NULL CHECK (status IN ('passed','failed')),
    required boolean NOT NULL,
    reason_code text NOT NULL,
    explanation text NOT NULL,
    checked_at_utc timestamptz NOT NULL,
    evidence_sha256 text NOT NULL CHECK (evidence_sha256 ~ '^[0-9a-f]{64}$'),
    record_sha256 text NOT NULL CHECK (record_sha256 ~ '^[0-9a-f]{64}$'),
    UNIQUE (report_id, check_name)
);

CREATE TABLE IF NOT EXISTS execution.paper_approvals (
    approval_id uuid PRIMARY KEY,
    paper_run_id uuid NOT NULL REFERENCES execution.paper_runs(paper_run_id) ON DELETE CASCADE,
    preflight_report_id uuid NOT NULL REFERENCES execution.paper_preflight_reports(report_id),
    configuration_sha256 text NOT NULL CHECK (configuration_sha256 ~ '^[0-9a-f]{64}$'),
    account_snapshot_sha256 text NOT NULL CHECK (account_snapshot_sha256 ~ '^[0-9a-f]{64}$'),
    credential_reference_sha256 text NOT NULL REFERENCES execution.paper_credential_references(credential_reference_sha256),
    provider text NOT NULL CHECK (provider IN ('internal','okx_demo')),
    environment text NOT NULL CHECK (environment IN ('paper_internal','paper_exchange_sandbox')),
    allowed_instruments_jsonb jsonb NOT NULL,
    maximum_approved_total_notional numeric NOT NULL CHECK (maximum_approved_total_notional > 0),
    created_at_utc timestamptz NOT NULL,
    expires_at_utc timestamptz NOT NULL,
    approving_actor text NOT NULL,
    approval_nonce text NOT NULL,
    state text NOT NULL CHECK (state IN ('valid','consumed','expired','revoked')),
    record_sha256 text NOT NULL CHECK (record_sha256 ~ '^[0-9a-f]{64}$'),
    CHECK (expires_at_utc > created_at_utc),
    CHECK ((provider='internal' AND environment='paper_internal') OR (provider='okx_demo' AND environment='paper_exchange_sandbox')),
    UNIQUE (paper_run_id, approval_nonce),
    UNIQUE (approval_id, paper_run_id, configuration_sha256, account_snapshot_sha256, credential_reference_sha256, provider, environment),
    FOREIGN KEY (preflight_report_id, paper_run_id, configuration_sha256, account_snapshot_sha256, credential_reference_sha256)
        REFERENCES execution.paper_preflight_reports (report_id, paper_run_id, configuration_sha256, account_snapshot_sha256, credential_reference_sha256)
);

CREATE TABLE IF NOT EXISTS execution.paper_run_manifests (
    manifest_id uuid PRIMARY KEY,
    paper_run_id uuid NOT NULL UNIQUE REFERENCES execution.paper_runs(paper_run_id) ON DELETE CASCADE,
    provider text NOT NULL CHECK (provider IN ('internal','okx_demo')),
    environment text NOT NULL CHECK (environment IN ('paper_internal','paper_exchange_sandbox')),
    account_reference text NOT NULL,
    implementation_sha256 text NOT NULL CHECK (implementation_sha256 ~ '^[0-9a-f]{64}$'),
    repository_commit_sha text NOT NULL,
    configuration_sha256 text NOT NULL CHECK (configuration_sha256 ~ '^[0-9a-f]{64}$'),
    endpoint_catalog_sha256 text NOT NULL CHECK (endpoint_catalog_sha256 ~ '^[0-9a-f]{64}$'),
    preflight_report_id uuid NOT NULL REFERENCES execution.paper_preflight_reports(report_id),
    approval_id uuid NOT NULL REFERENCES execution.paper_approvals(approval_id),
    initial_account_snapshot_id uuid NOT NULL REFERENCES execution.paper_account_snapshots(snapshot_id),
    initial_account_snapshot_sha256 text NOT NULL CHECK (initial_account_snapshot_sha256 ~ '^[0-9a-f]{64}$'),
    credential_reference_sha256 text NOT NULL REFERENCES execution.paper_credential_references(credential_reference_sha256),
    strategy_run_reference text NOT NULL,
    allowed_instruments_jsonb jsonb NOT NULL,
    risk_limits_jsonb jsonb NOT NULL,
    start_at_utc timestamptz NOT NULL,
    expected_maximum_duration_seconds integer NOT NULL CHECK (expected_maximum_duration_seconds > 0),
    persistence_required boolean NOT NULL,
    kill_switch_configuration_jsonb jsonb NOT NULL,
    parent_ids uuid[] NOT NULL,
    manifest_sha256 text NOT NULL CHECK (manifest_sha256 ~ '^[0-9a-f]{64}$'),
    CHECK ((provider='internal' AND environment='paper_internal') OR (provider='okx_demo' AND environment='paper_exchange_sandbox')),
    UNIQUE (paper_run_id, manifest_sha256),
    FOREIGN KEY (preflight_report_id, paper_run_id, configuration_sha256, initial_account_snapshot_sha256, credential_reference_sha256)
        REFERENCES execution.paper_preflight_reports (report_id, paper_run_id, configuration_sha256, account_snapshot_sha256, credential_reference_sha256),
    FOREIGN KEY (approval_id, paper_run_id, configuration_sha256, initial_account_snapshot_sha256, credential_reference_sha256, provider, environment)
        REFERENCES execution.paper_approvals (approval_id, paper_run_id, configuration_sha256, account_snapshot_sha256, credential_reference_sha256, provider, environment)
);

ALTER TABLE execution.paper_runs
    ADD CONSTRAINT phase7_paper_runs_manifest_fk
    FOREIGN KEY (manifest_id) REFERENCES execution.paper_run_manifests(manifest_id)
    DEFERRABLE INITIALLY DEFERRED;

CREATE TABLE IF NOT EXISTS execution.paper_order_submissions (
    submission_id uuid PRIMARY KEY,
    paper_run_id uuid NOT NULL REFERENCES execution.paper_runs(paper_run_id) ON DELETE CASCADE,
    manifest_id uuid NOT NULL REFERENCES execution.paper_run_manifests(manifest_id),
    approval_id uuid NOT NULL REFERENCES execution.paper_approvals(approval_id),
    order_intent_id uuid NOT NULL,
    client_order_id text NOT NULL,
    idempotency_key text NOT NULL,
    series_identity_sha256 text NOT NULL CHECK (series_identity_sha256 ~ '^[0-9a-f]{64}$'),
    instrument_id text NOT NULL,
    side text NOT NULL CHECK (side IN ('buy','sell')),
    order_type text NOT NULL CHECK (order_type IN ('market','limit','stop','stop_limit')),
    time_in_force text NOT NULL CHECK (time_in_force IN ('gtc','ioc')),
    accounting_mode text NOT NULL CHECK (accounting_mode IN ('spot','linear_perpetual')),
    quantity numeric NOT NULL CHECK (quantity > 0),
    reference_price numeric NOT NULL CHECK (reference_price > 0),
    submitted_notional numeric NOT NULL CHECK (submitted_notional > 0),
    limit_price numeric CHECK (limit_price IS NULL OR limit_price > 0),
    stop_price numeric CHECK (stop_price IS NULL OR stop_price > 0),
    submitted_at_utc timestamptz NOT NULL,
    state text NOT NULL CHECK (state IN ('submitted','pending_ack','acknowledged','partially_filled','filled','cancel_pending','cancelled','rejected','expired','submission_unknown','pending_recovery')),
    economics_sha256 text NOT NULL CHECK (economics_sha256 ~ '^[0-9a-f]{64}$'),
    pre_submit_risk_sha256 text NOT NULL CHECK (pre_submit_risk_sha256 ~ '^[0-9a-f]{64}$'),
    record_sha256 text NOT NULL CHECK (record_sha256 ~ '^[0-9a-f]{64}$'),
    UNIQUE (paper_run_id, client_order_id),
    UNIQUE (paper_run_id, idempotency_key),
    UNIQUE (manifest_id, order_intent_id)
);

CREATE TABLE IF NOT EXISTS execution.paper_orders (
    paper_order_record_id uuid PRIMARY KEY,
    paper_run_id uuid NOT NULL REFERENCES execution.paper_runs(paper_run_id) ON DELETE CASCADE,
    submission_id uuid NOT NULL REFERENCES execution.paper_order_submissions(submission_id),
    client_order_id text NOT NULL,
    venue_order_id text NOT NULL,
    state text NOT NULL CHECK (state IN ('pending_ack','acknowledged','partially_filled','filled','cancel_pending','cancelled','rejected','expired','unknown_pending_recovery')),
    original_quantity numeric NOT NULL CHECK (original_quantity > 0),
    cumulative_filled_quantity numeric NOT NULL CHECK (cumulative_filled_quantity >= 0 AND cumulative_filled_quantity <= original_quantity),
    remaining_quantity numeric NOT NULL CHECK (remaining_quantity >= 0 AND remaining_quantity = original_quantity - cumulative_filled_quantity),
    average_fill_price numeric CHECK (average_fill_price IS NULL OR average_fill_price > 0),
    venue_sequence bigint NOT NULL CHECK (venue_sequence >= 0),
    created_at_utc timestamptz NOT NULL,
    updated_at_utc timestamptz NOT NULL,
    economics_sha256 text NOT NULL CHECK (economics_sha256 ~ '^[0-9a-f]{64}$'),
    operational_request_id text,
    reject_reason text,
    record_sha256 text NOT NULL CHECK (record_sha256 ~ '^[0-9a-f]{64}$'),
    CHECK (updated_at_utc >= created_at_utc),
    UNIQUE (paper_run_id, client_order_id, venue_sequence),
    UNIQUE (paper_run_id, venue_order_id, venue_sequence)
);

CREATE TABLE IF NOT EXISTS execution.paper_order_events (
    paper_order_event_id uuid PRIMARY KEY,
    paper_run_id uuid NOT NULL REFERENCES execution.paper_runs(paper_run_id) ON DELETE CASCADE,
    submission_id uuid NOT NULL REFERENCES execution.paper_order_submissions(submission_id),
    client_order_id text NOT NULL,
    event_type text NOT NULL,
    event_at_utc timestamptz NOT NULL,
    venue_sequence bigint CHECK (venue_sequence IS NULL OR venue_sequence >= 0),
    details_jsonb jsonb NOT NULL DEFAULT '{}'::jsonb,
    parent_ids uuid[] NOT NULL,
    record_sha256 text NOT NULL CHECK (record_sha256 ~ '^[0-9a-f]{64}$'),
    UNIQUE (paper_run_id, client_order_id, event_type, venue_sequence, record_sha256)
);

CREATE TABLE IF NOT EXISTS execution.paper_fills (
    fill_id uuid PRIMARY KEY,
    paper_run_id uuid NOT NULL REFERENCES execution.paper_runs(paper_run_id) ON DELETE CASCADE,
    submission_id uuid NOT NULL REFERENCES execution.paper_order_submissions(submission_id),
    client_order_id text NOT NULL,
    venue_order_id text NOT NULL,
    venue_fill_id text NOT NULL,
    series_identity_sha256 text NOT NULL CHECK (series_identity_sha256 ~ '^[0-9a-f]{64}$'),
    side text NOT NULL CHECK (side IN ('buy','sell')),
    accounting_mode text NOT NULL CHECK (accounting_mode IN ('spot','linear_perpetual')),
    quantity numeric NOT NULL CHECK (quantity > 0),
    price numeric NOT NULL CHECK (price > 0),
    fee_amount numeric NOT NULL CHECK (fee_amount >= 0),
    fee_currency text NOT NULL,
    filled_at_utc timestamptz NOT NULL,
    venue_sequence bigint NOT NULL CHECK (venue_sequence >= 0),
    environment text NOT NULL CHECK (environment IN ('paper_internal','paper_exchange_sandbox')),
    accounting_applied boolean NOT NULL CHECK (accounting_applied),
    record_sha256 text NOT NULL CHECK (record_sha256 ~ '^[0-9a-f]{64}$'),
    UNIQUE (paper_run_id, venue_fill_id),
    UNIQUE (paper_run_id, venue_order_id, venue_sequence)
);

CREATE TABLE IF NOT EXISTS execution.paper_fee_entries (
    fee_entry_id uuid PRIMARY KEY,
    fill_id uuid NOT NULL UNIQUE REFERENCES execution.paper_fills(fill_id) ON DELETE CASCADE,
    paper_run_id uuid NOT NULL REFERENCES execution.paper_runs(paper_run_id) ON DELETE CASCADE,
    amount numeric NOT NULL CHECK (amount >= 0),
    currency text NOT NULL,
    occurred_at_utc timestamptz NOT NULL,
    record_sha256 text NOT NULL CHECK (record_sha256 ~ '^[0-9a-f]{64}$')
);

CREATE TABLE IF NOT EXISTS execution.paper_reconciliations (
    reconciliation_id uuid PRIMARY KEY,
    paper_run_id uuid NOT NULL REFERENCES execution.paper_runs(paper_run_id) ON DELETE CASCADE,
    local_snapshot_id uuid NOT NULL REFERENCES execution.paper_account_snapshots(snapshot_id),
    venue_snapshot_id uuid NOT NULL REFERENCES execution.paper_account_snapshots(snapshot_id),
    reconciled_at_utc timestamptz NOT NULL,
    status text NOT NULL CHECK (status IN ('reconciled','warning','blocked','unknown')),
    local_sequence bigint CHECK (local_sequence IS NULL OR local_sequence >= 0),
    venue_sequence bigint CHECK (venue_sequence IS NULL OR venue_sequence >= 0),
    difference_count integer NOT NULL CHECK (difference_count >= 0),
    material_difference_count integer NOT NULL CHECK (material_difference_count >= 0 AND material_difference_count <= difference_count),
    record_sha256 text NOT NULL CHECK (record_sha256 ~ '^[0-9a-f]{64}$'),
    UNIQUE (paper_run_id, local_snapshot_id, venue_snapshot_id, reconciled_at_utc)
);

CREATE TABLE IF NOT EXISTS execution.paper_reconciliation_differences (
    difference_id uuid PRIMARY KEY,
    reconciliation_id uuid NOT NULL REFERENCES execution.paper_reconciliations(reconciliation_id) ON DELETE CASCADE,
    difference_type text NOT NULL CHECK (difference_type IN ('local_order_missing_at_venue','venue_order_missing_locally','order_status_mismatch','quantity_mismatch','fill_missing_locally','fill_missing_at_venue','duplicate_fill','balance_mismatch','position_mismatch','fee_mismatch','currency_mismatch','stale_venue_snapshot','stale_local_snapshot','sequence_gap','unknown_submission','unsupported_venue_field','account_mode_mismatch')),
    material boolean NOT NULL,
    identity text NOT NULL,
    local_value_jsonb jsonb,
    venue_value_jsonb jsonb,
    explanation text NOT NULL,
    monitoring_event_id uuid REFERENCES monitoring.monitoring_events(monitoring_event_id),
    record_sha256 text NOT NULL CHECK (record_sha256 ~ '^[0-9a-f]{64}$'),
    UNIQUE (reconciliation_id, difference_type, identity, record_sha256)
);

CREATE TABLE IF NOT EXISTS execution.paper_recovery_records (
    recovery_id uuid PRIMARY KEY,
    paper_run_id uuid NOT NULL REFERENCES execution.paper_runs(paper_run_id) ON DELETE CASCADE,
    submission_id uuid REFERENCES execution.paper_order_submissions(submission_id),
    started_at_utc timestamptz NOT NULL,
    completed_at_utc timestamptz,
    status text NOT NULL CHECK (status IN ('started','recovered','paused','killed','failed')),
    action text NOT NULL,
    explanation text NOT NULL,
    parent_ids uuid[] NOT NULL,
    record_sha256 text NOT NULL CHECK (record_sha256 ~ '^[0-9a-f]{64}$'),
    CHECK (completed_at_utc IS NULL OR completed_at_utc >= started_at_utc)
);

CREATE TABLE IF NOT EXISTS execution.paper_kill_switches (
    kill_switch_id uuid PRIMARY KEY,
    paper_run_id uuid NOT NULL UNIQUE REFERENCES execution.paper_runs(paper_run_id) ON DELETE CASCADE,
    state text NOT NULL CHECK (state IN ('armed','triggered','cancelling','killed','reset_pending','reset')),
    reason text,
    updated_at_utc timestamptz NOT NULL,
    triggered_at_utc timestamptz,
    evidence_sha256 text CHECK (evidence_sha256 IS NULL OR evidence_sha256 ~ '^[0-9a-f]{64}$'),
    incident_id uuid REFERENCES monitoring.incidents(incident_id),
    record_sha256 text NOT NULL CHECK (record_sha256 ~ '^[0-9a-f]{64}$'),
    CHECK ((state IN ('armed','reset') AND reason IS NULL) OR (state NOT IN ('armed','reset') AND reason IS NOT NULL AND triggered_at_utc IS NOT NULL))
);

CREATE TABLE IF NOT EXISTS execution.paper_kill_switch_events (
    kill_switch_event_id uuid PRIMARY KEY,
    kill_switch_id uuid NOT NULL REFERENCES execution.paper_kill_switches(kill_switch_id) ON DELETE CASCADE,
    paper_run_id uuid NOT NULL REFERENCES execution.paper_runs(paper_run_id) ON DELETE CASCADE,
    prior_state text,
    next_state text NOT NULL CHECK (next_state IN ('armed','triggered','cancelling','killed','reset_pending','reset')),
    reason text,
    occurred_at_utc timestamptz NOT NULL,
    cancel_intent_client_order_id text,
    monitoring_incident_id uuid REFERENCES monitoring.incidents(incident_id),
    details_jsonb jsonb NOT NULL DEFAULT '{}'::jsonb,
    record_sha256 text NOT NULL CHECK (record_sha256 ~ '^[0-9a-f]{64}$')
);

CREATE TABLE IF NOT EXISTS execution.paper_rate_limit_events (
    rate_limit_event_id uuid PRIMARY KEY,
    paper_run_id uuid NOT NULL REFERENCES execution.paper_runs(paper_run_id) ON DELETE CASCADE,
    provider text NOT NULL,
    operation text NOT NULL,
    request_count integer NOT NULL CHECK (request_count >= 0),
    local_limit integer NOT NULL CHECK (local_limit > 0),
    reset_at_utc timestamptz,
    retry_after_seconds numeric CHECK (retry_after_seconds IS NULL OR retry_after_seconds >= 0),
    consecutive_failures integer NOT NULL CHECK (consecutive_failures >= 0),
    occurred_at_utc timestamptz NOT NULL,
    record_sha256 text NOT NULL CHECK (record_sha256 ~ '^[0-9a-f]{64}$')
);

CREATE TABLE IF NOT EXISTS execution.paper_transport_attempts (
    transport_attempt_id uuid PRIMARY KEY,
    paper_run_id uuid NOT NULL REFERENCES execution.paper_runs(paper_run_id) ON DELETE CASCADE,
    submission_id uuid REFERENCES execution.paper_order_submissions(submission_id),
    request_id uuid NOT NULL,
    request_type text NOT NULL,
    method text NOT NULL,
    approved_origin text NOT NULL,
    approved_path text NOT NULL,
    idempotency_key text,
    attempted_at_utc timestamptz NOT NULL,
    result_type text NOT NULL CHECK (result_type IN ('succeeded','rejected','timeout','unknown','rate_limited','authentication_failed','malformed')),
    status_code integer,
    response_sha256 text CHECK (response_sha256 IS NULL OR response_sha256 ~ '^[0-9a-f]{64}$'),
    retryable boolean NOT NULL,
    retry_ordinal integer NOT NULL CHECK (retry_ordinal >= 0),
    record_sha256 text NOT NULL CHECK (record_sha256 ~ '^[0-9a-f]{64}$'),
    UNIQUE (paper_run_id, request_id, retry_ordinal)
);

CREATE TABLE IF NOT EXISTS execution.paper_lifecycle_events (
    event_id uuid PRIMARY KEY,
    paper_run_id uuid NOT NULL REFERENCES execution.paper_runs(paper_run_id) ON DELETE CASCADE,
    event_type text NOT NULL,
    occurred_at_utc timestamptz NOT NULL,
    deterministic_sequence bigint NOT NULL CHECK (deterministic_sequence >= 0),
    details_jsonb jsonb NOT NULL DEFAULT '{}'::jsonb,
    parent_ids uuid[] NOT NULL,
    record_sha256 text NOT NULL CHECK (record_sha256 ~ '^[0-9a-f]{64}$'),
    UNIQUE (paper_run_id, deterministic_sequence)
);

CREATE INDEX idx_phase7_paper_runs_state_time ON execution.paper_runs (state, updated_at_utc, paper_run_id);
CREATE INDEX idx_phase7_paper_manifest_account ON execution.paper_run_manifests (provider, environment, account_reference, start_at_utc);
CREATE INDEX idx_phase7_paper_submissions_run_time ON execution.paper_order_submissions (paper_run_id, submitted_at_utc, submission_id);
CREATE INDEX idx_phase7_paper_submissions_unknown ON execution.paper_order_submissions (paper_run_id, submitted_at_utc) WHERE state IN ('submission_unknown','pending_recovery','pending_ack');
CREATE INDEX idx_phase7_paper_orders_open ON execution.paper_orders (paper_run_id, client_order_id, venue_sequence) WHERE state IN ('pending_ack','acknowledged','partially_filled','cancel_pending','unknown_pending_recovery');
CREATE INDEX idx_phase7_paper_fills_run_time ON execution.paper_fills (paper_run_id, filled_at_utc, fill_id);
CREATE INDEX idx_phase7_paper_snapshots_account_time ON execution.paper_account_snapshots (account_reference, fetched_at_utc, snapshot_id);
CREATE INDEX idx_phase7_paper_reconciliation_time ON execution.paper_reconciliations (paper_run_id, reconciled_at_utc, reconciliation_id);
CREATE INDEX idx_phase7_paper_differences_type ON execution.paper_reconciliation_differences (difference_type, material, reconciliation_id);
CREATE INDEX idx_phase7_paper_recovery_time ON execution.paper_recovery_records (paper_run_id, started_at_utc, recovery_id);
CREATE INDEX idx_phase7_paper_kill_events_time ON execution.paper_kill_switch_events (paper_run_id, occurred_at_utc, kill_switch_event_id);
CREATE INDEX idx_phase7_paper_transport_time ON execution.paper_transport_attempts (paper_run_id, attempted_at_utc, transport_attempt_id);
CREATE INDEX idx_phase7_paper_lifecycle_half_open ON execution.paper_lifecycle_events (paper_run_id, occurred_at_utc, deterministic_sequence);

COMMENT ON TABLE execution.paper_runs IS 'Phase 7 paper-only runs; live rows are forbidden by constraints.';
COMMENT ON TABLE execution.paper_run_manifests IS 'Immutable pre-submit manifest binding preflight, approval, snapshot, limits, endpoint catalog, and public-safe credential reference.';
COMMENT ON TABLE execution.paper_order_submissions IS 'Durable local logical submissions with stable idempotency keys; timeout is represented as submission_unknown.';
COMMENT ON TABLE execution.paper_orders IS 'Append-only venue order projections by venue sequence; acknowledgements never change accounting.';
COMMENT ON TABLE execution.paper_fills IS 'Venue-confirmed paper or official-sandbox fills applied atomically to paper accounting.';
COMMENT ON TABLE execution.paper_credential_references IS 'Public-safe aliases and public-key-ID fingerprints only; secrets are forbidden.';
COMMENT ON TABLE execution.paper_reconciliations IS 'Deterministic local/venue comparison; material differences are never silently overwritten.';
COMMENT ON TABLE execution.paper_kill_switches IS 'Persisted paper kill state; process restart does not reset it.';
COMMENT ON TABLE execution.paper_transport_attempts IS 'Public-safe bounded transport audit without headers, bodies, cookies, signatures, or credentials.';
