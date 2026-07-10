BEGIN;

-- Phase 6 monitoring, strictly simulated FIX, and Phase 5 final valuation repair.
-- Migrations 0001 through 0012 remain immutable.

ALTER TABLE backtesting.backtest_position_states
    ADD COLUMN valuation_at_utc TIMESTAMPTZ,
    ADD COLUMN mark_source TEXT,
    ADD COLUMN stale_mark_age_seconds NUMERIC(38, 9),
    ADD COLUMN valuation_status TEXT,
    ADD COLUMN source_position_snapshot_id UUID;

WITH latest_snapshot AS (
    SELECT DISTINCT ON (membership.backtest_run_id, snapshot.position_id)
        membership.backtest_run_id,
        snapshot.position_id,
        snapshot.position_snapshot_id,
        snapshot.snapshot_at_utc,
        snapshot.quantity,
        snapshot.average_entry_price,
        snapshot.realized_pnl,
        snapshot.mark_price,
        snapshot.unrealized_pnl,
        snapshot.mark_source,
        snapshot.stale_mark_age_seconds
    FROM backtesting.backtest_run_memberships AS membership
    JOIN execution.position_snapshots AS snapshot
      ON snapshot.position_snapshot_id = membership.position_snapshot_id
    WHERE membership.record_type = 'position_snapshot'
    ORDER BY membership.backtest_run_id, snapshot.position_id,
             snapshot.snapshot_at_utc DESC, snapshot.logical_sequence DESC,
             snapshot.position_snapshot_id DESC
)
UPDATE backtesting.backtest_position_states AS state
SET quantity = latest.quantity,
    average_entry_price = latest.average_entry_price,
    realized_pnl = latest.realized_pnl,
    mark_price = latest.mark_price,
    unrealized_pnl = CASE WHEN latest.quantity <> 0 AND latest.mark_price IS NULL
                          THEN 0 ELSE latest.unrealized_pnl END,
    valuation_at_utc = latest.snapshot_at_utc,
    mark_source = latest.mark_source,
    stale_mark_age_seconds = latest.stale_mark_age_seconds,
    valuation_status = CASE WHEN latest.quantity = 0 THEN 'flat'
                            WHEN latest.mark_price IS NOT NULL THEN 'marked'
                            ELSE 'unmarked' END,
    source_position_snapshot_id = latest.position_snapshot_id
FROM latest_snapshot AS latest
WHERE state.backtest_run_id = latest.backtest_run_id
  AND state.position_id = latest.position_id;

UPDATE backtesting.backtest_position_states
SET valuation_at_utc = COALESCE(valuation_at_utc, updated_at_utc),
    valuation_status = COALESCE(
        valuation_status,
        CASE WHEN quantity = 0 THEN 'flat'
             WHEN mark_price IS NOT NULL THEN 'marked'
             ELSE 'unmarked' END
    );

UPDATE backtesting.backtest_position_states
SET final_record_sha256 = encode(sha256(convert_to(concat_ws('|',
    'phase5-final-position-valuation-v1', backtest_run_id::TEXT, position_id::TEXT,
    account_ref, series_identity_sha256, deterministic_ordinal::TEXT, accounting_mode,
    quantity::TEXT, average_entry_price::TEXT, realized_pnl::TEXT, unrealized_pnl::TEXT,
    array_to_string(source_fill_ids, ','), updated_at_utc::TEXT, mark_price::TEXT,
    valuation_at_utc::TEXT, mark_source, stale_mark_age_seconds::TEXT,
    valuation_status, source_position_snapshot_id::TEXT, config_sha256
), 'UTF8')), 'hex');

ALTER TABLE backtesting.backtest_position_states
    ALTER COLUMN valuation_at_utc SET NOT NULL,
    ALTER COLUMN valuation_status SET NOT NULL,
    ADD CONSTRAINT phase6_position_valuation_status_check
        CHECK (valuation_status IN ('marked', 'flat', 'unmarked')),
    ADD CONSTRAINT phase6_position_valuation_consistency_check CHECK (
        (valuation_status = 'marked' AND quantity <> 0 AND mark_price IS NOT NULL) OR
        (valuation_status = 'flat' AND quantity = 0) OR
        (valuation_status = 'unmarked' AND quantity <> 0 AND mark_price IS NULL)
    ),
    ADD CONSTRAINT phase6_position_stale_age_check
        CHECK (stale_mark_age_seconds IS NULL OR stale_mark_age_seconds >= 0);

CREATE INDEX idx_phase6_position_states_valuation
    ON backtesting.backtest_position_states
    (backtest_run_id, valuation_status, valuation_at_utc, deterministic_ordinal);

CREATE TABLE IF NOT EXISTS monitoring.monitoring_runs (
    monitoring_run_id UUID PRIMARY KEY,
    as_of_utc TIMESTAMPTZ NOT NULL,
    monitored_identity TEXT NOT NULL,
    monitored_run_id UUID,
    configuration_sha256 CHAR(64) NOT NULL CHECK (configuration_sha256 ~ '^[0-9a-f]{64}$'),
    stable_input_sha256 CHAR(64) NOT NULL CHECK (stable_input_sha256 ~ '^[0-9a-f]{64}$'),
    implementation_code_sha256 CHAR(64) NOT NULL CHECK (implementation_code_sha256 ~ '^[0-9a-f]{64}$'),
    repository_commit_sha TEXT NOT NULL,
    overall_status TEXT NOT NULL CHECK (overall_status IN ('healthy','degraded','unhealthy','unknown')),
    parent_ids UUID[] NOT NULL DEFAULT ARRAY[]::UUID[],
    public_provenance_jsonb JSONB NOT NULL DEFAULT '{}'::JSONB,
    record_sha256 CHAR(64) NOT NULL CHECK (record_sha256 ~ '^[0-9a-f]{64}$'),
    created_at_utc TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (monitored_identity, as_of_utc, configuration_sha256, stable_input_sha256)
);

CREATE TABLE IF NOT EXISTS monitoring.health_check_results (
    health_check_result_id UUID PRIMARY KEY,
    monitoring_run_id UUID NOT NULL REFERENCES monitoring.monitoring_runs ON DELETE CASCADE,
    evaluation_at_utc TIMESTAMPTZ NOT NULL,
    category TEXT NOT NULL CHECK (category IN ('data','signal','execution','risk','system','fix_session')),
    component TEXT NOT NULL,
    check_name TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('passed','warning','failed','unknown')),
    health_status TEXT NOT NULL CHECK (health_status IN ('healthy','degraded','unhealthy','unknown')),
    severity TEXT NOT NULL CHECK (severity IN ('info','warning','error','critical')),
    reason_code TEXT NOT NULL,
    explanation TEXT NOT NULL,
    observed_value_jsonb JSONB,
    configured_threshold_jsonb JSONB,
    configuration_sha256 CHAR(64) NOT NULL CHECK (configuration_sha256 ~ '^[0-9a-f]{64}$'),
    stable_input_sha256 CHAR(64) NOT NULL CHECK (stable_input_sha256 ~ '^[0-9a-f]{64}$'),
    implementation_code_sha256 CHAR(64) NOT NULL CHECK (implementation_code_sha256 ~ '^[0-9a-f]{64}$'),
    repository_commit_sha TEXT NOT NULL,
    parent_ids UUID[] NOT NULL DEFAULT ARRAY[]::UUID[],
    record_sha256 CHAR(64) NOT NULL CHECK (record_sha256 ~ '^[0-9a-f]{64}$'),
    created_at_utc TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (monitoring_run_id, category, component, check_name)
);
CREATE INDEX idx_phase6_checks_component_time
    ON monitoring.health_check_results (category, component, evaluation_at_utc, health_check_result_id);

CREATE TABLE IF NOT EXISTS monitoring.health_snapshots (
    health_snapshot_id UUID PRIMARY KEY,
    monitoring_run_id UUID NOT NULL REFERENCES monitoring.monitoring_runs ON DELETE CASCADE,
    evaluation_at_utc TIMESTAMPTZ NOT NULL,
    category TEXT,
    component TEXT NOT NULL,
    health_status TEXT NOT NULL CHECK (health_status IN ('healthy','degraded','unhealthy','unknown')),
    causing_check_ids UUID[] NOT NULL DEFAULT ARRAY[]::UUID[],
    reason_code TEXT NOT NULL,
    explanation TEXT NOT NULL,
    configuration_sha256 CHAR(64) NOT NULL CHECK (configuration_sha256 ~ '^[0-9a-f]{64}$'),
    stable_input_sha256 CHAR(64) NOT NULL CHECK (stable_input_sha256 ~ '^[0-9a-f]{64}$'),
    implementation_code_sha256 CHAR(64) NOT NULL CHECK (implementation_code_sha256 ~ '^[0-9a-f]{64}$'),
    repository_commit_sha TEXT NOT NULL,
    parent_ids UUID[] NOT NULL DEFAULT ARRAY[]::UUID[],
    record_sha256 CHAR(64) NOT NULL CHECK (record_sha256 ~ '^[0-9a-f]{64}$'),
    created_at_utc TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (monitoring_run_id, component)
);
CREATE INDEX idx_phase6_snapshots_component_time
    ON monitoring.health_snapshots (component, evaluation_at_utc, health_snapshot_id);

CREATE TABLE IF NOT EXISTS monitoring.incidents (
    incident_id UUID PRIMARY KEY,
    category TEXT NOT NULL CHECK (category IN ('data','signal','execution','risk','system','fix_session')),
    component TEXT NOT NULL,
    reason_code TEXT NOT NULL,
    monitored_identity TEXT NOT NULL,
    state TEXT NOT NULL CHECK (state IN ('open','acknowledged','resolved')),
    severity TEXT NOT NULL CHECK (severity IN ('info','warning','error','critical')),
    episode_started_at_utc TIMESTAMPTZ NOT NULL,
    latest_at_utc TIMESTAMPTZ NOT NULL,
    resolved_at_utc TIMESTAMPTZ,
    occurrence_count BIGINT NOT NULL CHECK (occurrence_count > 0),
    configuration_sha256 CHAR(64) NOT NULL CHECK (configuration_sha256 ~ '^[0-9a-f]{64}$'),
    stable_input_sha256 CHAR(64) NOT NULL CHECK (stable_input_sha256 ~ '^[0-9a-f]{64}$'),
    record_sha256 CHAR(64) NOT NULL CHECK (record_sha256 ~ '^[0-9a-f]{64}$'),
    created_at_utc TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX uq_phase6_incident_active_episode
    ON monitoring.incidents (category, component, reason_code, monitored_identity)
    WHERE state IN ('open','acknowledged');

CREATE TABLE IF NOT EXISTS monitoring.incident_occurrences (
    incident_occurrence_id UUID PRIMARY KEY,
    incident_id UUID NOT NULL REFERENCES monitoring.incidents ON DELETE CASCADE,
    monitoring_run_id UUID NOT NULL REFERENCES monitoring.monitoring_runs ON DELETE CASCADE,
    health_check_result_id UUID NOT NULL REFERENCES monitoring.health_check_results ON DELETE CASCADE,
    occurred_at_utc TIMESTAMPTZ NOT NULL,
    record_sha256 CHAR(64) NOT NULL CHECK (record_sha256 ~ '^[0-9a-f]{64}$'),
    UNIQUE (incident_id, health_check_result_id)
);

CREATE TABLE IF NOT EXISTS monitoring.fix_sessions (
    fix_session_id UUID PRIMARY KEY,
    session_key TEXT NOT NULL UNIQUE,
    sender_comp_id TEXT NOT NULL,
    target_comp_id TEXT NOT NULL,
    state TEXT NOT NULL CHECK (state IN ('disconnected','logon_pending','established','test_request_pending','logout_pending','recovering','terminated')),
    next_inbound_seq_num BIGINT NOT NULL CHECK (next_inbound_seq_num > 0),
    next_outbound_seq_num BIGINT NOT NULL CHECK (next_outbound_seq_num > 0),
    heartbeat_interval_seconds NUMERIC(38,9) NOT NULL CHECK (heartbeat_interval_seconds > 0),
    last_inbound_at_utc TIMESTAMPTZ,
    last_outbound_at_utc TIMESTAMPTZ,
    pending_test_request_id TEXT,
    configuration_sha256 CHAR(64) NOT NULL CHECK (configuration_sha256 ~ '^[0-9a-f]{64}$'),
    record_sha256 CHAR(64) NOT NULL CHECK (record_sha256 ~ '^[0-9a-f]{64}$'),
    simulated BOOLEAN NOT NULL DEFAULT TRUE CHECK (simulated = TRUE),
    updated_at_utc TIMESTAMPTZ NOT NULL
);

CREATE TABLE IF NOT EXISTS monitoring.fix_messages (
    fix_message_id UUID PRIMARY KEY,
    fix_session_id UUID NOT NULL REFERENCES monitoring.fix_sessions ON DELETE CASCADE,
    direction TEXT NOT NULL CHECK (direction IN ('inbound','outbound')),
    msg_type TEXT NOT NULL,
    msg_seq_num BIGINT NOT NULL CHECK (msg_seq_num > 0),
    sending_time_utc TIMESTAMPTZ NOT NULL,
    processing_time_utc TIMESTAMPTZ NOT NULL,
    validation_status TEXT NOT NULL CHECK (validation_status IN ('valid','rejected')),
    rejection_reason TEXT,
    body_length BIGINT NOT NULL CHECK (body_length >= 0),
    checksum INTEGER NOT NULL CHECK (checksum BETWEEN 0 AND 255),
    business_identity_sha256 CHAR(64) NOT NULL CHECK (business_identity_sha256 ~ '^[0-9a-f]{64}$'),
    raw_message_sha256 CHAR(64) NOT NULL CHECK (raw_message_sha256 ~ '^[0-9a-f]{64}$'),
    parsed_fields_jsonb JSONB NOT NULL DEFAULT '{}'::JSONB,
    record_sha256 CHAR(64) NOT NULL CHECK (record_sha256 ~ '^[0-9a-f]{64}$'),
    UNIQUE (fix_session_id, direction, msg_seq_num)
);
CREATE INDEX idx_phase6_fix_messages_session_direction_seq
    ON monitoring.fix_messages (fix_session_id, direction, msg_seq_num);

CREATE TABLE IF NOT EXISTS monitoring.latency_samples (
    latency_sample_id UUID PRIMARY KEY,
    monitoring_run_id UUID REFERENCES monitoring.monitoring_runs ON DELETE CASCADE,
    fix_session_id UUID NOT NULL REFERENCES monitoring.fix_sessions ON DELETE CASCADE,
    fix_message_id UUID REFERENCES monitoring.fix_messages ON DELETE CASCADE,
    stage TEXT NOT NULL,
    simulated_start_utc TIMESTAMPTZ NOT NULL,
    simulated_end_utc TIMESTAMPTZ NOT NULL,
    duration_microseconds BIGINT NOT NULL CHECK (duration_microseconds >= 0),
    threshold_microseconds BIGINT CHECK (threshold_microseconds IS NULL OR threshold_microseconds >= 0),
    breached BOOLEAN NOT NULL,
    record_sha256 CHAR(64) NOT NULL CHECK (record_sha256 ~ '^[0-9a-f]{64}$'),
    UNIQUE (fix_session_id, fix_message_id, stage, simulated_start_utc)
);

CREATE TABLE IF NOT EXISTS monitoring.fix_order_links (
    fix_order_link_id UUID PRIMARY KEY,
    fix_session_id UUID NOT NULL REFERENCES monitoring.fix_sessions ON DELETE CASCADE,
    cl_ord_id TEXT NOT NULL,
    orig_cl_ord_id TEXT,
    order_intent_id UUID REFERENCES execution.order_intents ON DELETE RESTRICT,
    order_id UUID REFERENCES execution.orders ON DELETE RESTRICT,
    fill_id UUID REFERENCES execution.fills ON DELETE RESTRICT,
    execution_report_message_id UUID REFERENCES monitoring.fix_messages ON DELETE RESTRICT,
    business_identity_sha256 CHAR(64) NOT NULL CHECK (business_identity_sha256 ~ '^[0-9a-f]{64}$'),
    record_sha256 CHAR(64) NOT NULL CHECK (record_sha256 ~ '^[0-9a-f]{64}$'),
    UNIQUE (fix_session_id, cl_ord_id, fill_id, execution_report_message_id)
);
CREATE INDEX idx_phase6_fix_order_lifecycle ON monitoring.fix_order_links (fix_session_id, cl_ord_id);

CREATE TABLE IF NOT EXISTS monitoring.connection_faults (
    connection_fault_id UUID PRIMARY KEY,
    fix_session_id UUID NOT NULL REFERENCES monitoring.fix_sessions ON DELETE CASCADE,
    fault_type TEXT NOT NULL CHECK (fault_type IN ('drop_before_logon','drop_after_acknowledgement','drop_active_order','heartbeat_response_loss','duplicate_inbound','inbound_sequence_gap','delayed_outbound_report','reconnect_delay')),
    scheduled_at_utc TIMESTAMPTZ NOT NULL,
    activated_at_utc TIMESTAMPTZ,
    reason_code TEXT NOT NULL,
    configuration_jsonb JSONB NOT NULL DEFAULT '{}'::JSONB,
    record_sha256 CHAR(64) NOT NULL CHECK (record_sha256 ~ '^[0-9a-f]{64}$'),
    UNIQUE (fix_session_id, fault_type, scheduled_at_utc)
);
CREATE INDEX idx_phase6_faults_session_time ON monitoring.connection_faults (fix_session_id, scheduled_at_utc);

ALTER TABLE monitoring.monitoring_events
    DROP CONSTRAINT IF EXISTS monitoring_events_event_category_check,
    ADD COLUMN monitoring_run_id UUID REFERENCES monitoring.monitoring_runs ON DELETE CASCADE,
    ADD COLUMN component TEXT,
    ADD COLUMN event_type TEXT,
    ADD COLUMN reason_code TEXT,
    ADD COLUMN configuration_sha256 CHAR(64),
    ADD COLUMN stable_input_sha256 CHAR(64),
    ADD COLUMN implementation_code_sha256 CHAR(64),
    ADD COLUMN repository_commit_sha TEXT,
    ADD COLUMN parent_ids UUID[] NOT NULL DEFAULT ARRAY[]::UUID[],
    ADD COLUMN record_sha256 CHAR(64),
    ADD CONSTRAINT phase6_monitoring_event_category_check
        CHECK (event_category IN ('data','signal','execution','risk','system','fix_session')),
    ADD CONSTRAINT phase6_monitoring_event_hash_check
        CHECK (record_sha256 IS NULL OR record_sha256 ~ '^[0-9a-f]{64}$');
CREATE INDEX idx_phase6_monitoring_events_run_component_time
    ON monitoring.monitoring_events (monitoring_run_id, event_category, component, event_time_utc);
ALTER TABLE monitoring.fix_session_events
    ADD COLUMN fix_session_id UUID REFERENCES monitoring.fix_sessions ON DELETE CASCADE,
    ADD COLUMN prior_state TEXT,
    ADD COLUMN new_state TEXT,
    ADD COLUMN reason_code TEXT,
    ADD COLUMN parent_message_id UUID,
    ADD COLUMN record_sha256 CHAR(64),
    ADD CONSTRAINT phase6_fix_session_event_state_check CHECK (
        prior_state IS NULL OR prior_state IN ('disconnected','logon_pending','established','test_request_pending','logout_pending','recovering','terminated')
    ),
    ADD CONSTRAINT phase6_fix_session_event_new_state_check CHECK (
        new_state IS NULL OR new_state IN ('disconnected','logon_pending','established','test_request_pending','logout_pending','recovering','terminated')
    ),
    ADD CONSTRAINT phase6_fix_session_event_hash_check CHECK (record_sha256 IS NULL OR record_sha256 ~ '^[0-9a-f]{64}$');
CREATE INDEX idx_phase6_fix_session_events_session_time
    ON monitoring.fix_session_events (fix_session_id, event_time_utc, sequence_number);

COMMENT ON TABLE monitoring.fix_sessions IS 'Strictly simulated in-process FIX 4.4-compatible session state; never an external counterparty connection.';
COMMENT ON TABLE monitoring.fix_messages IS 'Public-safe parsed simulated FIX messages; no credentials or private broker payloads.';
COMMENT ON TABLE monitoring.latency_samples IS 'Deterministic simulated processing latency, not measured network latency.';
COMMENT ON COLUMN backtesting.backtest_position_states.valuation_status IS 'Final run-specific valuation: marked, flat, or unmarked.';

COMMIT;