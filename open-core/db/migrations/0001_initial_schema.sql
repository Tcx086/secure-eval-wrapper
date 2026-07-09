BEGIN;

CREATE SCHEMA IF NOT EXISTS audit;
CREATE SCHEMA IF NOT EXISTS market_data;
CREATE SCHEMA IF NOT EXISTS data_quality;
CREATE SCHEMA IF NOT EXISTS alpha;
CREATE SCHEMA IF NOT EXISTS signals;
CREATE SCHEMA IF NOT EXISTS execution;
CREATE SCHEMA IF NOT EXISTS backtesting;
CREATE SCHEMA IF NOT EXISTS monitoring;

COMMENT ON SCHEMA audit IS 'Run manifests, artifact records, hashes, and redaction metadata.';
COMMENT ON SCHEMA market_data IS 'Raw and validated crypto market data storage.';
COMMENT ON SCHEMA data_quality IS 'Data validation reports and check results.';
COMMENT ON SCHEMA alpha IS 'Public alpha registry metadata.';
COMMENT ON SCHEMA signals IS 'Signal generation run metadata and standardized signals.';
COMMENT ON SCHEMA execution IS 'Shared execution contract storage for intents, orders, fills, and state.';
COMMENT ON SCHEMA backtesting IS 'Backtest run metadata, metrics, equity curves, and stress results.';
COMMENT ON SCHEMA monitoring IS 'Simulated monitoring and FIX-style session events.';

CREATE TABLE IF NOT EXISTS audit.run_manifests (
    run_id UUID PRIMARY KEY,
    run_mode TEXT NOT NULL CHECK (run_mode IN (
        'research',
        'backtest',
        'simulation',
        'paper',
        'monitoring',
        'reporting',
        'unknown'
    )),
    data_sha256 CHAR(64) CHECK (data_sha256 IS NULL OR data_sha256 ~ '^[0-9a-f]{64}$'),
    config_sha256 CHAR(64) CHECK (config_sha256 IS NULL OR config_sha256 ~ '^[0-9a-f]{64}$'),
    code_sha256 CHAR(64) CHECK (code_sha256 IS NULL OR code_sha256 ~ '^[0-9a-f]{64}$'),
    artifact_sha256 CHAR(64) CHECK (artifact_sha256 IS NULL OR artifact_sha256 ~ '^[0-9a-f]{64}$'),
    seed BIGINT,
    storage_ref TEXT,
    manifest_jsonb JSONB NOT NULL DEFAULT '{}'::JSONB,
    created_at_utc TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS data_quality.validation_reports (
    validation_report_id UUID PRIMARY KEY,
    validation_run_id UUID NOT NULL,
    dataset_ref TEXT NOT NULL,
    accepted_count INTEGER NOT NULL DEFAULT 0 CHECK (accepted_count >= 0),
    rejected_count INTEGER NOT NULL DEFAULT 0 CHECK (rejected_count >= 0),
    warning_count INTEGER NOT NULL DEFAULT 0 CHECK (warning_count >= 0),
    status TEXT NOT NULL CHECK (status IN ('accepted', 'accepted_with_warnings', 'rejected', 'failed')),
    report_sha256 CHAR(64) CHECK (report_sha256 IS NULL OR report_sha256 ~ '^[0-9a-f]{64}$'),
    report_jsonb JSONB NOT NULL DEFAULT '{}'::JSONB,
    created_at_utc TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (validation_run_id, dataset_ref)
);

CREATE TABLE IF NOT EXISTS data_quality.data_quality_checks (
    check_id UUID PRIMARY KEY,
    validation_run_id UUID NOT NULL,
    check_type TEXT NOT NULL,
    severity TEXT NOT NULL CHECK (severity IN ('info', 'warning', 'error', 'critical')),
    symbol TEXT,
    timeframe TEXT,
    window_start_utc TIMESTAMPTZ,
    window_end_utc TIMESTAMPTZ,
    status TEXT NOT NULL CHECK (status IN ('passed', 'warning', 'failed', 'skipped')),
    details_jsonb JSONB NOT NULL DEFAULT '{}'::JSONB,
    created_at_utc TIMESTAMPTZ NOT NULL DEFAULT now(),
    CHECK (window_end_utc IS NULL OR window_start_utc IS NULL OR window_end_utc >= window_start_utc)
);

CREATE TABLE IF NOT EXISTS market_data.instruments (
    instrument_id UUID PRIMARY KEY,
    symbol TEXT NOT NULL,
    exchange TEXT NOT NULL,
    base_asset TEXT NOT NULL,
    quote_asset TEXT NOT NULL,
    instrument_type TEXT NOT NULL CHECK (instrument_type IN ('spot', 'perpetual', 'future', 'option', 'index')),
    status TEXT NOT NULL CHECK (status IN ('active', 'inactive', 'delisted', 'unknown')),
    price_precision INTEGER CHECK (price_precision IS NULL OR price_precision >= 0),
    quantity_precision INTEGER CHECK (quantity_precision IS NULL OR quantity_precision >= 0),
    metadata_jsonb JSONB NOT NULL DEFAULT '{}'::JSONB,
    first_seen_at_utc TIMESTAMPTZ,
    last_seen_at_utc TIMESTAMPTZ,
    created_at_utc TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at_utc TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (symbol, exchange),
    CHECK (last_seen_at_utc IS NULL OR first_seen_at_utc IS NULL OR last_seen_at_utc >= first_seen_at_utc)
);

CREATE TABLE IF NOT EXISTS market_data.raw_source_observations (
    observation_id UUID PRIMARY KEY,
    source_provider TEXT NOT NULL,
    source_exchange TEXT,
    source_endpoint TEXT NOT NULL,
    symbol_raw TEXT,
    symbol_normalized TEXT,
    timeframe TEXT,
    observed_at_utc TIMESTAMPTZ,
    ingested_at_utc TIMESTAMPTZ NOT NULL DEFAULT now(),
    payload_jsonb JSONB NOT NULL,
    source_sha256 CHAR(64) NOT NULL CHECK (source_sha256 ~ '^[0-9a-f]{64}$'),
    collection_run_id UUID,
    created_at_utc TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS market_data.validated_bars (
    bar_id UUID PRIMARY KEY,
    symbol TEXT NOT NULL,
    exchange TEXT NOT NULL,
    timeframe TEXT NOT NULL,
    bar_open_time_utc TIMESTAMPTZ NOT NULL,
    open NUMERIC(38, 18) NOT NULL CHECK (open >= 0),
    high NUMERIC(38, 18) NOT NULL CHECK (high >= 0),
    low NUMERIC(38, 18) NOT NULL CHECK (low >= 0),
    close NUMERIC(38, 18) NOT NULL CHECK (close >= 0),
    volume NUMERIC(38, 18) NOT NULL CHECK (volume >= 0),
    validation_status TEXT NOT NULL CHECK (validation_status IN ('accepted', 'accepted_with_warnings')),
    validation_report_id UUID REFERENCES data_quality.validation_reports (validation_report_id),
    source_observation_ids UUID[] NOT NULL DEFAULT ARRAY[]::UUID[],
    provenance_jsonb JSONB NOT NULL DEFAULT '{}'::JSONB,
    created_at_utc TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (symbol, exchange, timeframe, bar_open_time_utc),
    CHECK (high >= low),
    CHECK (open BETWEEN low AND high),
    CHECK (close BETWEEN low AND high)
);

CREATE TABLE IF NOT EXISTS market_data.validated_trades (
    trade_id UUID PRIMARY KEY,
    provider_trade_id TEXT,
    symbol TEXT NOT NULL,
    exchange TEXT NOT NULL,
    traded_at_utc TIMESTAMPTZ NOT NULL,
    price NUMERIC(38, 18) NOT NULL CHECK (price >= 0),
    quantity NUMERIC(38, 18) NOT NULL CHECK (quantity >= 0),
    side TEXT NOT NULL CHECK (side IN ('buy', 'sell', 'unknown')),
    validation_status TEXT NOT NULL CHECK (validation_status IN ('accepted', 'accepted_with_warnings')),
    validation_report_id UUID REFERENCES data_quality.validation_reports (validation_report_id),
    source_observation_ids UUID[] NOT NULL DEFAULT ARRAY[]::UUID[],
    provenance_jsonb JSONB NOT NULL DEFAULT '{}'::JSONB,
    created_at_utc TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS market_data.funding_rates (
    funding_rate_id UUID PRIMARY KEY,
    symbol TEXT NOT NULL,
    exchange TEXT NOT NULL,
    funding_interval TEXT,
    funding_time_utc TIMESTAMPTZ NOT NULL,
    rate NUMERIC(38, 18) NOT NULL,
    predicted_rate NUMERIC(38, 18),
    mark_price NUMERIC(38, 18) CHECK (mark_price IS NULL OR mark_price >= 0),
    index_price NUMERIC(38, 18) CHECK (index_price IS NULL OR index_price >= 0),
    validation_status TEXT NOT NULL CHECK (validation_status IN ('accepted', 'accepted_with_warnings')),
    validation_report_id UUID REFERENCES data_quality.validation_reports (validation_report_id),
    source_observation_ids UUID[] NOT NULL DEFAULT ARRAY[]::UUID[],
    provenance_jsonb JSONB NOT NULL DEFAULT '{}'::JSONB,
    created_at_utc TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (symbol, exchange, funding_time_utc)
);

CREATE TABLE IF NOT EXISTS alpha.alpha_registry (
    alpha_id UUID PRIMARY KEY,
    alpha_name TEXT NOT NULL UNIQUE,
    description TEXT NOT NULL,
    public_example BOOLEAN NOT NULL DEFAULT TRUE,
    status TEXT NOT NULL CHECK (status IN ('draft', 'active', 'deprecated')),
    created_at_utc TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at_utc TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS signals.signal_runs (
    signal_run_id UUID PRIMARY KEY,
    run_id UUID NOT NULL,
    dataset_ref TEXT NOT NULL,
    config_sha256 CHAR(64) CHECK (config_sha256 IS NULL OR config_sha256 ~ '^[0-9a-f]{64}$'),
    code_sha256 CHAR(64) CHECK (code_sha256 IS NULL OR code_sha256 ~ '^[0-9a-f]{64}$'),
    seed BIGINT,
    started_at_utc TIMESTAMPTZ NOT NULL DEFAULT now(),
    completed_at_utc TIMESTAMPTZ,
    status TEXT NOT NULL CHECK (status IN ('pending', 'running', 'completed', 'failed')),
    metadata_jsonb JSONB NOT NULL DEFAULT '{}'::JSONB,
    CHECK (completed_at_utc IS NULL OR completed_at_utc >= started_at_utc)
);

CREATE TABLE IF NOT EXISTS signals.signals (
    signal_id UUID PRIMARY KEY,
    signal_run_id UUID NOT NULL REFERENCES signals.signal_runs (signal_run_id) ON DELETE CASCADE,
    alpha_id UUID REFERENCES alpha.alpha_registry (alpha_id),
    symbol TEXT NOT NULL,
    timestamp_utc TIMESTAMPTZ NOT NULL,
    direction TEXT NOT NULL CHECK (direction IN ('long', 'short', 'flat')),
    score NUMERIC(38, 18) NOT NULL,
    confidence NUMERIC(10, 8) CHECK (confidence IS NULL OR (confidence >= 0 AND confidence <= 1)),
    horizon TEXT,
    provenance_jsonb JSONB NOT NULL DEFAULT '{}'::JSONB,
    created_at_utc TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS execution.order_intents (
    order_intent_id UUID PRIMARY KEY,
    signal_id UUID REFERENCES signals.signals (signal_id),
    run_id UUID NOT NULL,
    symbol TEXT NOT NULL,
    side TEXT NOT NULL CHECK (side IN ('buy', 'sell')),
    order_type TEXT NOT NULL CHECK (order_type IN ('market', 'limit', 'stop', 'stop_limit')),
    quantity NUMERIC(38, 18) NOT NULL CHECK (quantity > 0),
    limit_price NUMERIC(38, 18) CHECK (limit_price IS NULL OR limit_price >= 0),
    intent_status TEXT NOT NULL CHECK (intent_status IN ('created', 'blocked', 'submitted', 'cancelled')),
    risk_summary_jsonb JSONB NOT NULL DEFAULT '{}'::JSONB,
    created_at_utc TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS execution.orders (
    order_id UUID PRIMARY KEY,
    order_intent_id UUID NOT NULL REFERENCES execution.order_intents (order_intent_id),
    broker_order_ref TEXT,
    run_id UUID NOT NULL,
    symbol TEXT NOT NULL,
    side TEXT NOT NULL CHECK (side IN ('buy', 'sell')),
    order_type TEXT NOT NULL CHECK (order_type IN ('market', 'limit', 'stop', 'stop_limit')),
    order_status TEXT NOT NULL CHECK (order_status IN (
        'submitted',
        'acknowledged',
        'partially_filled',
        'filled',
        'cancelled',
        'rejected',
        'expired'
    )),
    reject_reason TEXT,
    submitted_at_utc TIMESTAMPTZ NOT NULL DEFAULT now(),
    acknowledged_at_utc TIMESTAMPTZ,
    broker_payload_jsonb JSONB NOT NULL DEFAULT '{}'::JSONB,
    CHECK (acknowledged_at_utc IS NULL OR acknowledged_at_utc >= submitted_at_utc)
);

CREATE TABLE IF NOT EXISTS execution.fills (
    fill_id UUID PRIMARY KEY,
    order_id UUID NOT NULL REFERENCES execution.orders (order_id),
    broker_fill_ref TEXT,
    symbol TEXT NOT NULL,
    side TEXT NOT NULL CHECK (side IN ('buy', 'sell')),
    filled_at_utc TIMESTAMPTZ NOT NULL,
    price NUMERIC(38, 18) NOT NULL CHECK (price >= 0),
    quantity NUMERIC(38, 18) NOT NULL CHECK (quantity > 0),
    fee_amount NUMERIC(38, 18) NOT NULL DEFAULT 0 CHECK (fee_amount >= 0),
    fee_asset TEXT,
    liquidity_flag TEXT CHECK (liquidity_flag IS NULL OR liquidity_flag IN ('maker', 'taker', 'unknown')),
    fill_payload_jsonb JSONB NOT NULL DEFAULT '{}'::JSONB,
    created_at_utc TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS execution.positions (
    position_id UUID PRIMARY KEY,
    run_id UUID NOT NULL,
    account_ref TEXT NOT NULL DEFAULT 'simulation',
    symbol TEXT NOT NULL,
    quantity NUMERIC(38, 18) NOT NULL DEFAULT 0,
    average_entry_price NUMERIC(38, 18) CHECK (average_entry_price IS NULL OR average_entry_price >= 0),
    realized_pnl NUMERIC(38, 18) NOT NULL DEFAULT 0,
    unrealized_pnl NUMERIC(38, 18) NOT NULL DEFAULT 0,
    source_fill_ids UUID[] NOT NULL DEFAULT ARRAY[]::UUID[],
    updated_at_utc TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (run_id, account_ref, symbol)
);

CREATE TABLE IF NOT EXISTS execution.account_snapshots (
    account_snapshot_id UUID PRIMARY KEY,
    run_id UUID NOT NULL,
    account_ref TEXT NOT NULL DEFAULT 'simulation',
    snapshot_at_utc TIMESTAMPTZ NOT NULL,
    equity NUMERIC(38, 18) CHECK (equity IS NULL OR equity >= 0),
    cash NUMERIC(38, 18),
    margin_used NUMERIC(38, 18) CHECK (margin_used IS NULL OR margin_used >= 0),
    balances_jsonb JSONB NOT NULL DEFAULT '{}'::JSONB,
    classification TEXT NOT NULL DEFAULT 'private_when_real' CHECK (
        classification IN ('public_synthetic', 'private_when_real', 'private_only')
    ),
    created_at_utc TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (run_id, account_ref, snapshot_at_utc)
);

CREATE TABLE IF NOT EXISTS backtesting.backtest_runs (
    backtest_run_id UUID PRIMARY KEY,
    run_id UUID NOT NULL,
    signal_run_id UUID REFERENCES signals.signal_runs (signal_run_id),
    execution_model_sha256 CHAR(64) CHECK (
        execution_model_sha256 IS NULL OR execution_model_sha256 ~ '^[0-9a-f]{64}$'
    ),
    config_sha256 CHAR(64) CHECK (config_sha256 IS NULL OR config_sha256 ~ '^[0-9a-f]{64}$'),
    started_at_utc TIMESTAMPTZ NOT NULL DEFAULT now(),
    completed_at_utc TIMESTAMPTZ,
    status TEXT NOT NULL CHECK (status IN ('pending', 'running', 'completed', 'failed')),
    metadata_jsonb JSONB NOT NULL DEFAULT '{}'::JSONB,
    CHECK (completed_at_utc IS NULL OR completed_at_utc >= started_at_utc)
);

CREATE TABLE IF NOT EXISTS backtesting.backtest_metrics (
    backtest_metric_id UUID PRIMARY KEY,
    backtest_run_id UUID NOT NULL REFERENCES backtesting.backtest_runs (backtest_run_id) ON DELETE CASCADE,
    metric_name TEXT NOT NULL,
    metric_value NUMERIC(38, 18),
    metric_unit TEXT,
    details_jsonb JSONB NOT NULL DEFAULT '{}'::JSONB,
    created_at_utc TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (backtest_run_id, metric_name)
);

CREATE TABLE IF NOT EXISTS backtesting.equity_curves (
    equity_curve_id UUID PRIMARY KEY,
    backtest_run_id UUID NOT NULL REFERENCES backtesting.backtest_runs (backtest_run_id) ON DELETE CASCADE,
    timestamp_utc TIMESTAMPTZ NOT NULL,
    equity NUMERIC(38, 18) NOT NULL CHECK (equity >= 0),
    cash NUMERIC(38, 18),
    drawdown NUMERIC(38, 18),
    exposure NUMERIC(38, 18),
    details_jsonb JSONB NOT NULL DEFAULT '{}'::JSONB,
    created_at_utc TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (backtest_run_id, timestamp_utc)
);

CREATE TABLE IF NOT EXISTS backtesting.stress_results (
    stress_result_id UUID PRIMARY KEY,
    backtest_run_id UUID NOT NULL REFERENCES backtesting.backtest_runs (backtest_run_id) ON DELETE CASCADE,
    scenario_name TEXT NOT NULL,
    metric_name TEXT NOT NULL,
    metric_value NUMERIC(38, 18),
    details_jsonb JSONB NOT NULL DEFAULT '{}'::JSONB,
    created_at_utc TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (backtest_run_id, scenario_name, metric_name)
);

CREATE TABLE IF NOT EXISTS monitoring.monitoring_events (
    monitoring_event_id UUID PRIMARY KEY,
    run_id UUID,
    event_category TEXT NOT NULL CHECK (event_category IN ('data', 'signal', 'execution', 'risk', 'system')),
    severity TEXT NOT NULL CHECK (severity IN ('info', 'warning', 'error', 'critical')),
    event_time_utc TIMESTAMPTZ NOT NULL DEFAULT now(),
    symbol TEXT,
    message TEXT NOT NULL,
    details_jsonb JSONB NOT NULL DEFAULT '{}'::JSONB,
    created_at_utc TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS monitoring.fix_session_events (
    fix_session_event_id UUID PRIMARY KEY,
    run_id UUID,
    session_id TEXT NOT NULL,
    event_type TEXT NOT NULL,
    sequence_number BIGINT CHECK (sequence_number IS NULL OR sequence_number >= 0),
    event_time_utc TIMESTAMPTZ NOT NULL DEFAULT now(),
    message_type TEXT,
    payload_jsonb JSONB NOT NULL DEFAULT '{}'::JSONB,
    simulated BOOLEAN NOT NULL DEFAULT TRUE CHECK (simulated = TRUE),
    created_at_utc TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS monitoring.risk_events (
    risk_event_id UUID PRIMARY KEY,
    run_id UUID,
    event_time_utc TIMESTAMPTZ NOT NULL DEFAULT now(),
    risk_type TEXT NOT NULL,
    severity TEXT NOT NULL CHECK (severity IN ('info', 'warning', 'error', 'critical')),
    symbol TEXT,
    limit_name TEXT,
    observed_value NUMERIC(38, 18),
    limit_value NUMERIC(38, 18),
    action_taken TEXT,
    details_jsonb JSONB NOT NULL DEFAULT '{}'::JSONB,
    created_at_utc TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS audit.artifacts (
    artifact_id UUID PRIMARY KEY,
    run_id UUID REFERENCES audit.run_manifests (run_id),
    artifact_type TEXT NOT NULL,
    classification TEXT NOT NULL CHECK (classification IN ('public_safe', 'redacted', 'local_only', 'private_only')),
    path_uri TEXT NOT NULL,
    artifact_sha256 CHAR(64) CHECK (artifact_sha256 IS NULL OR artifact_sha256 ~ '^[0-9a-f]{64}$'),
    redaction_status TEXT NOT NULL DEFAULT 'not_required' CHECK (
        redaction_status IN ('not_required', 'pending', 'redacted', 'blocked')
    ),
    metadata_jsonb JSONB NOT NULL DEFAULT '{}'::JSONB,
    created_at_utc TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_raw_source_observations_provider_time
    ON market_data.raw_source_observations (source_provider, observed_at_utc);
CREATE INDEX IF NOT EXISTS idx_raw_source_observations_collection_run
    ON market_data.raw_source_observations (collection_run_id);
CREATE UNIQUE INDEX IF NOT EXISTS idx_raw_source_observations_source_sha256
    ON market_data.raw_source_observations (source_sha256);

CREATE INDEX IF NOT EXISTS idx_validated_bars_symbol_time
    ON market_data.validated_bars (symbol, exchange, timeframe, bar_open_time_utc);
CREATE INDEX IF NOT EXISTS idx_validated_trades_symbol_time
    ON market_data.validated_trades (symbol, exchange, traded_at_utc);
CREATE INDEX IF NOT EXISTS idx_funding_rates_symbol_time
    ON market_data.funding_rates (symbol, exchange, funding_time_utc);

CREATE INDEX IF NOT EXISTS idx_data_quality_checks_validation_run
    ON data_quality.data_quality_checks (validation_run_id);
CREATE INDEX IF NOT EXISTS idx_validation_reports_validation_run
    ON data_quality.validation_reports (validation_run_id);

CREATE INDEX IF NOT EXISTS idx_signal_runs_run_id
    ON signals.signal_runs (run_id);
CREATE INDEX IF NOT EXISTS idx_signals_run_symbol_time
    ON signals.signals (signal_run_id, symbol, timestamp_utc);

CREATE INDEX IF NOT EXISTS idx_order_intents_run_id
    ON execution.order_intents (run_id);
CREATE INDEX IF NOT EXISTS idx_orders_run_status
    ON execution.orders (run_id, order_status);
CREATE UNIQUE INDEX IF NOT EXISTS idx_orders_broker_order_ref
    ON execution.orders (broker_order_ref)
    WHERE broker_order_ref IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_fills_order_id
    ON execution.fills (order_id);
CREATE UNIQUE INDEX IF NOT EXISTS idx_fills_broker_fill_ref
    ON execution.fills (broker_fill_ref)
    WHERE broker_fill_ref IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_backtest_runs_run_id
    ON backtesting.backtest_runs (run_id);
CREATE INDEX IF NOT EXISTS idx_equity_curves_run_time
    ON backtesting.equity_curves (backtest_run_id, timestamp_utc);

CREATE INDEX IF NOT EXISTS idx_monitoring_events_run_time
    ON monitoring.monitoring_events (run_id, event_time_utc);
CREATE INDEX IF NOT EXISTS idx_fix_session_events_session_time
    ON monitoring.fix_session_events (session_id, event_time_utc);
CREATE INDEX IF NOT EXISTS idx_risk_events_run_time
    ON monitoring.risk_events (run_id, event_time_utc);

CREATE INDEX IF NOT EXISTS idx_artifacts_run_id
    ON audit.artifacts (run_id);
CREATE INDEX IF NOT EXISTS idx_artifacts_classification
    ON audit.artifacts (classification);

COMMENT ON TABLE market_data.raw_source_observations IS 'Raw provider responses or normalized observations before validation.';
COMMENT ON TABLE market_data.validated_bars IS 'Accepted OHLCV bars after validation.';
COMMENT ON TABLE market_data.validated_trades IS 'Accepted trades after validation.';
COMMENT ON TABLE market_data.funding_rates IS 'Accepted funding rate observations after validation.';
COMMENT ON TABLE market_data.instruments IS 'Instrument metadata by symbol and exchange.';
COMMENT ON TABLE data_quality.data_quality_checks IS 'Individual data validation check results.';
COMMENT ON TABLE data_quality.validation_reports IS 'Validation report records that gate accepted datasets.';
COMMENT ON TABLE alpha.alpha_registry IS 'Public alpha catalog metadata.';
COMMENT ON TABLE signals.signal_runs IS 'Signal generation job metadata.';
COMMENT ON TABLE signals.signals IS 'Standardized signal outputs.';
COMMENT ON TABLE execution.order_intents IS 'Pre-broker order intent records.';
COMMENT ON TABLE execution.orders IS 'Broker acknowledgement, order status, and reject records.';
COMMENT ON TABLE execution.fills IS 'Execution fill records.';
COMMENT ON TABLE execution.positions IS 'Position state derived from fills.';
COMMENT ON TABLE execution.account_snapshots IS 'Account state snapshots, private-only when sourced from real accounts.';
COMMENT ON TABLE backtesting.backtest_runs IS 'Backtest run metadata.';
COMMENT ON TABLE backtesting.backtest_metrics IS 'Backtest aggregate metrics.';
COMMENT ON TABLE backtesting.equity_curves IS 'Backtest portfolio state by timestamp.';
COMMENT ON TABLE backtesting.stress_results IS 'Backtest scenario and stress outputs.';
COMMENT ON TABLE monitoring.monitoring_events IS 'Data, signal, execution, risk, and system health events.';
COMMENT ON TABLE monitoring.fix_session_events IS 'Simulated FIX-style monitoring events only.';
COMMENT ON TABLE monitoring.risk_events IS 'Risk limit and risk action events.';
COMMENT ON TABLE audit.run_manifests IS 'Run-level reproducibility metadata and hashes.';
COMMENT ON TABLE audit.artifacts IS 'Classified artifacts with hashes and redaction state.';

COMMIT;
