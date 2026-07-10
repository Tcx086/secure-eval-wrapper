BEGIN;

-- Phase 5 strengthens the original execution/backtesting skeleton without rewriting history.
-- Nullable additions keep seeded 0008 installations upgradeable; all Phase 5 repositories write
-- complete values and protect logical identity with stable hashes.

ALTER TABLE execution.order_intents
    ADD COLUMN IF NOT EXISTS backtest_run_id UUID REFERENCES backtesting.backtest_runs (backtest_run_id) ON DELETE CASCADE,
    ADD COLUMN IF NOT EXISTS provider_name TEXT,
    ADD COLUMN IF NOT EXISTS exchange_name TEXT,
    ADD COLUMN IF NOT EXISTS provider_instrument_id TEXT,
    ADD COLUMN IF NOT EXISTS canonical_symbol TEXT,
    ADD COLUMN IF NOT EXISTS instrument_type TEXT,
    ADD COLUMN IF NOT EXISTS timeframe TEXT,
    ADD COLUMN IF NOT EXISTS settlement_asset TEXT,
    ADD COLUMN IF NOT EXISTS series_identity_sha256 CHAR(64),
    ADD COLUMN IF NOT EXISTS event_timestamp_utc TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS execution_mode TEXT DEFAULT 'backtest',
    ADD COLUMN IF NOT EXISTS accounting_mode TEXT,
    ADD COLUMN IF NOT EXISTS target_quantity NUMERIC(38, 18),
    ADD COLUMN IF NOT EXISTS current_quantity NUMERIC(38, 18),
    ADD COLUMN IF NOT EXISTS delta_quantity NUMERIC(38, 18),
    ADD COLUMN IF NOT EXISTS reference_price NUMERIC(38, 18),
    ADD COLUMN IF NOT EXISTS stop_price NUMERIC(38, 18),
    ADD COLUMN IF NOT EXISTS time_in_force TEXT DEFAULT 'gtc',
    ADD COLUMN IF NOT EXISTS config_sha256 CHAR(64),
    ADD COLUMN IF NOT EXISTS data_sha256 CHAR(64),
    ADD COLUMN IF NOT EXISTS implementation_code_sha256 CHAR(64),
    ADD COLUMN IF NOT EXISTS repository_commit_sha TEXT,
    ADD COLUMN IF NOT EXISTS record_sha256 CHAR(64),
    ADD COLUMN IF NOT EXISTS parent_ids UUID[] NOT NULL DEFAULT ARRAY[]::UUID[],
    ADD COLUMN IF NOT EXISTS provenance_jsonb JSONB NOT NULL DEFAULT '{}'::JSONB;

ALTER TABLE execution.orders
    ADD COLUMN IF NOT EXISTS backtest_run_id UUID REFERENCES backtesting.backtest_runs (backtest_run_id) ON DELETE CASCADE,
    ADD COLUMN IF NOT EXISTS provider_name TEXT,
    ADD COLUMN IF NOT EXISTS exchange_name TEXT,
    ADD COLUMN IF NOT EXISTS provider_instrument_id TEXT,
    ADD COLUMN IF NOT EXISTS canonical_symbol TEXT,
    ADD COLUMN IF NOT EXISTS instrument_type TEXT,
    ADD COLUMN IF NOT EXISTS timeframe TEXT,
    ADD COLUMN IF NOT EXISTS settlement_asset TEXT,
    ADD COLUMN IF NOT EXISTS series_identity_sha256 CHAR(64),
    ADD COLUMN IF NOT EXISTS quantity NUMERIC(38, 18),
    ADD COLUMN IF NOT EXISTS limit_price NUMERIC(38, 18),
    ADD COLUMN IF NOT EXISTS stop_price NUMERIC(38, 18),
    ADD COLUMN IF NOT EXISTS accounting_mode TEXT,
    ADD COLUMN IF NOT EXISTS time_in_force TEXT DEFAULT 'gtc',
    ADD COLUMN IF NOT EXISTS triggered_at_utc TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS activation_reason TEXT,
    ADD COLUMN IF NOT EXISTS config_sha256 CHAR(64),
    ADD COLUMN IF NOT EXISTS record_sha256 CHAR(64),
    ADD COLUMN IF NOT EXISTS parent_ids UUID[] NOT NULL DEFAULT ARRAY[]::UUID[],
    ADD COLUMN IF NOT EXISTS provenance_jsonb JSONB NOT NULL DEFAULT '{}'::JSONB;

ALTER TABLE execution.fills
    ADD COLUMN IF NOT EXISTS run_id UUID,
    ADD COLUMN IF NOT EXISTS backtest_run_id UUID REFERENCES backtesting.backtest_runs (backtest_run_id) ON DELETE CASCADE,
    ADD COLUMN IF NOT EXISTS order_intent_id UUID REFERENCES execution.order_intents (order_intent_id),
    ADD COLUMN IF NOT EXISTS provider_name TEXT,
    ADD COLUMN IF NOT EXISTS exchange_name TEXT,
    ADD COLUMN IF NOT EXISTS provider_instrument_id TEXT,
    ADD COLUMN IF NOT EXISTS canonical_symbol TEXT,
    ADD COLUMN IF NOT EXISTS instrument_type TEXT,
    ADD COLUMN IF NOT EXISTS timeframe TEXT,
    ADD COLUMN IF NOT EXISTS settlement_asset TEXT,
    ADD COLUMN IF NOT EXISTS series_identity_sha256 CHAR(64),
    ADD COLUMN IF NOT EXISTS accounting_mode TEXT,
    ADD COLUMN IF NOT EXISTS base_price NUMERIC(38, 18),
    ADD COLUMN IF NOT EXISTS notional NUMERIC(38, 18),
    ADD COLUMN IF NOT EXISTS slippage_amount NUMERIC(38, 18) DEFAULT 0,
    ADD COLUMN IF NOT EXISTS slippage_bps NUMERIC(20, 10) DEFAULT 0,
    ADD COLUMN IF NOT EXISTS fill_reason TEXT,
    ADD COLUMN IF NOT EXISTS config_sha256 CHAR(64),
    ADD COLUMN IF NOT EXISTS record_sha256 CHAR(64),
    ADD COLUMN IF NOT EXISTS parent_ids UUID[] NOT NULL DEFAULT ARRAY[]::UUID[],
    ADD COLUMN IF NOT EXISTS provenance_jsonb JSONB NOT NULL DEFAULT '{}'::JSONB;

ALTER TABLE execution.positions
    DROP CONSTRAINT IF EXISTS positions_run_id_account_ref_symbol_key;

ALTER TABLE execution.positions
    ADD COLUMN IF NOT EXISTS backtest_run_id UUID REFERENCES backtesting.backtest_runs (backtest_run_id) ON DELETE CASCADE,
    ADD COLUMN IF NOT EXISTS provider_name TEXT,
    ADD COLUMN IF NOT EXISTS exchange_name TEXT,
    ADD COLUMN IF NOT EXISTS provider_instrument_id TEXT,
    ADD COLUMN IF NOT EXISTS canonical_symbol TEXT,
    ADD COLUMN IF NOT EXISTS instrument_type TEXT,
    ADD COLUMN IF NOT EXISTS timeframe TEXT,
    ADD COLUMN IF NOT EXISTS settlement_asset TEXT,
    ADD COLUMN IF NOT EXISTS series_identity_sha256 CHAR(64),
    ADD COLUMN IF NOT EXISTS accounting_mode TEXT,
    ADD COLUMN IF NOT EXISTS mark_price NUMERIC(38, 18),
    ADD COLUMN IF NOT EXISTS config_sha256 CHAR(64),
    ADD COLUMN IF NOT EXISTS record_sha256 CHAR(64);

ALTER TABLE execution.account_snapshots
    DROP CONSTRAINT IF EXISTS account_snapshots_equity_check;

ALTER TABLE execution.account_snapshots
    ADD COLUMN IF NOT EXISTS backtest_run_id UUID REFERENCES backtesting.backtest_runs (backtest_run_id) ON DELETE CASCADE,
    ADD COLUMN IF NOT EXISTS gross_exposure NUMERIC(38, 18),
    ADD COLUMN IF NOT EXISTS net_exposure NUMERIC(38, 18),
    ADD COLUMN IF NOT EXISTS realized_pnl NUMERIC(38, 18),
    ADD COLUMN IF NOT EXISTS unrealized_pnl NUMERIC(38, 18),
    ADD COLUMN IF NOT EXISTS total_fees NUMERIC(38, 18),
    ADD COLUMN IF NOT EXISTS total_funding NUMERIC(38, 18),
    ADD COLUMN IF NOT EXISTS stale_mark_count INTEGER DEFAULT 0,
    ADD COLUMN IF NOT EXISTS config_sha256 CHAR(64),
    ADD COLUMN IF NOT EXISTS record_sha256 CHAR(64),
    ADD COLUMN IF NOT EXISTS parent_ids UUID[] NOT NULL DEFAULT ARRAY[]::UUID[];

CREATE TABLE IF NOT EXISTS execution.risk_decisions (
    risk_decision_id UUID PRIMARY KEY,
    run_id UUID NOT NULL,
    backtest_run_id UUID REFERENCES backtesting.backtest_runs (backtest_run_id) ON DELETE CASCADE,
    order_intent_id UUID NOT NULL REFERENCES execution.order_intents (order_intent_id) ON DELETE CASCADE,
    order_id UUID REFERENCES execution.orders (order_id) ON DELETE SET NULL,
    provider_name TEXT NOT NULL,
    exchange_name TEXT NOT NULL,
    provider_instrument_id TEXT NOT NULL,
    canonical_symbol TEXT NOT NULL,
    instrument_type TEXT NOT NULL,
    timeframe TEXT NOT NULL,
    settlement_asset TEXT,
    series_identity_sha256 CHAR(64) NOT NULL CHECK (series_identity_sha256 ~ '^[0-9a-f]{64}$'),
    decision_timestamp_utc TIMESTAMPTZ NOT NULL,
    stage TEXT NOT NULL CHECK (stage IN ('pre_submit', 'pre_fill')),
    decision_status TEXT NOT NULL CHECK (decision_status IN ('accepted', 'blocked')),
    relevant_limit TEXT,
    observed_value NUMERIC(38, 18),
    configured_limit NUMERIC(38, 18),
    reason_code TEXT NOT NULL,
    explanation TEXT NOT NULL,
    config_sha256 CHAR(64) NOT NULL CHECK (config_sha256 ~ '^[0-9a-f]{64}$'),
    record_sha256 CHAR(64) NOT NULL CHECK (record_sha256 ~ '^[0-9a-f]{64}$'),
    parent_ids UUID[] NOT NULL DEFAULT ARRAY[]::UUID[],
    provenance_jsonb JSONB NOT NULL DEFAULT '{}'::JSONB,
    created_at_utc TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (order_intent_id, stage, decision_timestamp_utc, record_sha256)
);

CREATE TABLE IF NOT EXISTS execution.position_snapshots (
    position_snapshot_id UUID PRIMARY KEY,
    run_id UUID NOT NULL,
    backtest_run_id UUID REFERENCES backtesting.backtest_runs (backtest_run_id) ON DELETE CASCADE,
    position_id UUID NOT NULL REFERENCES execution.positions (position_id) ON DELETE CASCADE,
    source_fill_id UUID REFERENCES execution.fills (fill_id) ON DELETE SET NULL,
    provider_name TEXT NOT NULL,
    exchange_name TEXT NOT NULL,
    provider_instrument_id TEXT NOT NULL,
    canonical_symbol TEXT NOT NULL,
    instrument_type TEXT NOT NULL,
    timeframe TEXT NOT NULL,
    settlement_asset TEXT,
    series_identity_sha256 CHAR(64) NOT NULL CHECK (series_identity_sha256 ~ '^[0-9a-f]{64}$'),
    accounting_mode TEXT NOT NULL CHECK (accounting_mode IN ('spot', 'linear_perpetual')),
    snapshot_at_utc TIMESTAMPTZ NOT NULL,
    quantity NUMERIC(38, 18) NOT NULL,
    average_entry_price NUMERIC(38, 18),
    mark_price NUMERIC(38, 18),
    realized_pnl NUMERIC(38, 18) NOT NULL,
    unrealized_pnl NUMERIC(38, 18) NOT NULL,
    stale_mark_age_seconds NUMERIC(38, 9),
    config_sha256 CHAR(64) NOT NULL CHECK (config_sha256 ~ '^[0-9a-f]{64}$'),
    record_sha256 CHAR(64) NOT NULL CHECK (record_sha256 ~ '^[0-9a-f]{64}$'),
    parent_ids UUID[] NOT NULL DEFAULT ARRAY[]::UUID[],
    created_at_utc TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (position_id, snapshot_at_utc, record_sha256),
    CHECK ((quantity = 0 AND average_entry_price IS NULL) OR (quantity <> 0 AND average_entry_price > 0))
);

CREATE TABLE IF NOT EXISTS execution.funding_payments (
    funding_payment_id UUID PRIMARY KEY,
    run_id UUID NOT NULL,
    backtest_run_id UUID REFERENCES backtesting.backtest_runs (backtest_run_id) ON DELETE CASCADE,
    funding_rate_id UUID NOT NULL REFERENCES market_data.funding_rates (funding_rate_id),
    provider_name TEXT NOT NULL,
    exchange_name TEXT NOT NULL,
    provider_instrument_id TEXT NOT NULL,
    canonical_symbol TEXT NOT NULL,
    instrument_type TEXT NOT NULL CHECK (instrument_type = 'perpetual_swap'),
    timeframe TEXT NOT NULL,
    settlement_asset TEXT NOT NULL,
    series_identity_sha256 CHAR(64) NOT NULL CHECK (series_identity_sha256 ~ '^[0-9a-f]{64}$'),
    funding_timestamp_utc TIMESTAMPTZ NOT NULL,
    signed_quantity NUMERIC(38, 18) NOT NULL,
    mark_price NUMERIC(38, 18) NOT NULL CHECK (mark_price > 0),
    funding_rate NUMERIC(38, 18) NOT NULL,
    cash_flow NUMERIC(38, 18) NOT NULL,
    funding_interval TEXT NOT NULL,
    funding_interval_source TEXT NOT NULL,
    source_observation_ids UUID[] NOT NULL DEFAULT ARRAY[]::UUID[],
    config_sha256 CHAR(64) NOT NULL CHECK (config_sha256 ~ '^[0-9a-f]{64}$'),
    record_sha256 CHAR(64) NOT NULL CHECK (record_sha256 ~ '^[0-9a-f]{64}$'),
    parent_ids UUID[] NOT NULL DEFAULT ARRAY[]::UUID[],
    provenance_jsonb JSONB NOT NULL DEFAULT '{}'::JSONB,
    created_at_utc TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (run_id, funding_rate_id, series_identity_sha256, record_sha256)
);

CREATE TABLE IF NOT EXISTS execution.cash_ledger_entries (
    cash_ledger_entry_id UUID PRIMARY KEY,
    run_id UUID NOT NULL,
    backtest_run_id UUID REFERENCES backtesting.backtest_runs (backtest_run_id) ON DELETE CASCADE,
    event_timestamp_utc TIMESTAMPTZ NOT NULL,
    entry_type TEXT NOT NULL CHECK (entry_type IN ('initial_cash', 'spot_notional', 'realized_pnl', 'fee', 'funding')),
    amount NUMERIC(38, 18) NOT NULL,
    balance_after NUMERIC(38, 18) NOT NULL,
    currency TEXT NOT NULL,
    series_identity_sha256 CHAR(64),
    fill_id UUID REFERENCES execution.fills (fill_id) ON DELETE SET NULL,
    funding_payment_id UUID REFERENCES execution.funding_payments (funding_payment_id) ON DELETE SET NULL,
    config_sha256 CHAR(64) NOT NULL CHECK (config_sha256 ~ '^[0-9a-f]{64}$'),
    record_sha256 CHAR(64) NOT NULL CHECK (record_sha256 ~ '^[0-9a-f]{64}$'),
    parent_ids UUID[] NOT NULL DEFAULT ARRAY[]::UUID[],
    created_at_utc TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (run_id, record_sha256),
    CHECK (entry_type <> 'fee' OR amount <= 0)
);

ALTER TABLE backtesting.backtest_runs
    ADD COLUMN IF NOT EXISTS initial_cash NUMERIC(38, 18),
    ADD COLUMN IF NOT EXISTS base_currency TEXT,
    ADD COLUMN IF NOT EXISTS data_sha256 CHAR(64),
    ADD COLUMN IF NOT EXISTS implementation_code_sha256 CHAR(64),
    ADD COLUMN IF NOT EXISTS repository_commit_sha TEXT,
    ADD COLUMN IF NOT EXISTS record_sha256 CHAR(64);

ALTER TABLE backtesting.backtest_metrics
    ADD COLUMN IF NOT EXISTS metric_status TEXT DEFAULT 'available',
    ADD COLUMN IF NOT EXISTS config_sha256 CHAR(64),
    ADD COLUMN IF NOT EXISTS record_sha256 CHAR(64);

ALTER TABLE backtesting.equity_curves
    DROP CONSTRAINT IF EXISTS equity_curves_equity_check;

ALTER TABLE backtesting.equity_curves
    ADD COLUMN IF NOT EXISTS drawdown_fraction NUMERIC(38, 18),
    ADD COLUMN IF NOT EXISTS gross_exposure NUMERIC(38, 18),
    ADD COLUMN IF NOT EXISTS net_exposure NUMERIC(38, 18),
    ADD COLUMN IF NOT EXISTS stale_mark_count INTEGER DEFAULT 0,
    ADD COLUMN IF NOT EXISTS config_sha256 CHAR(64),
    ADD COLUMN IF NOT EXISTS record_sha256 CHAR(64);

CREATE TABLE IF NOT EXISTS backtesting.backtest_events (
    backtest_event_id UUID PRIMARY KEY,
    backtest_run_id UUID NOT NULL REFERENCES backtesting.backtest_runs (backtest_run_id) ON DELETE CASCADE,
    deterministic_sequence BIGINT NOT NULL CHECK (deterministic_sequence >= 0),
    event_timestamp_utc TIMESTAMPTZ NOT NULL,
    event_priority INTEGER NOT NULL CHECK (event_priority >= 0),
    event_type TEXT NOT NULL,
    provider_name TEXT,
    exchange_name TEXT,
    provider_instrument_id TEXT,
    canonical_symbol TEXT,
    instrument_type TEXT,
    timeframe TEXT,
    settlement_asset TEXT,
    series_identity_sha256 CHAR(64),
    parent_record_id UUID,
    event_sha256 CHAR(64) NOT NULL CHECK (event_sha256 ~ '^[0-9a-f]{64}$'),
    config_sha256 CHAR(64) NOT NULL CHECK (config_sha256 ~ '^[0-9a-f]{64}$'),
    record_sha256 CHAR(64) NOT NULL CHECK (record_sha256 ~ '^[0-9a-f]{64}$'),
    metadata_jsonb JSONB NOT NULL DEFAULT '{}'::JSONB,
    created_at_utc TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (backtest_run_id, deterministic_sequence),
    UNIQUE (backtest_run_id, event_sha256, deterministic_sequence),
    CHECK ((series_identity_sha256 IS NULL) = (provider_name IS NULL))
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_phase5_order_intents_logical
    ON execution.order_intents (run_id, record_sha256) WHERE record_sha256 IS NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS uq_phase5_order_intents_signal_series_time
    ON execution.order_intents (run_id, signal_id, series_identity_sha256, event_timestamp_utc)
    WHERE series_identity_sha256 IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_phase5_order_intents_series_time
    ON execution.order_intents (run_id, series_identity_sha256, event_timestamp_utc);
CREATE UNIQUE INDEX IF NOT EXISTS uq_phase5_orders_intent
    ON execution.orders (order_intent_id);
CREATE INDEX IF NOT EXISTS idx_phase5_orders_series_status
    ON execution.orders (run_id, series_identity_sha256, order_status);
CREATE UNIQUE INDEX IF NOT EXISTS uq_phase5_fills_record
    ON execution.fills (order_id, record_sha256) WHERE record_sha256 IS NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS uq_phase5_fills_order
    ON execution.fills (order_id);
CREATE INDEX IF NOT EXISTS idx_phase5_fills_series_time
    ON execution.fills (run_id, series_identity_sha256, filled_at_utc);
CREATE UNIQUE INDEX IF NOT EXISTS uq_phase5_positions_series
    ON execution.positions (run_id, account_ref, series_identity_sha256) WHERE series_identity_sha256 IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_phase5_risk_run_time
    ON execution.risk_decisions (run_id, decision_timestamp_utc, stage);
CREATE UNIQUE INDEX IF NOT EXISTS uq_phase5_risk_logical
    ON execution.risk_decisions (order_intent_id, stage, decision_timestamp_utc);
CREATE INDEX IF NOT EXISTS idx_phase5_position_snapshots_run_time
    ON execution.position_snapshots (run_id, snapshot_at_utc, series_identity_sha256);
CREATE INDEX IF NOT EXISTS idx_phase5_funding_payments_run_time
    ON execution.funding_payments (run_id, funding_timestamp_utc);
CREATE UNIQUE INDEX IF NOT EXISTS uq_phase5_funding_payment_logical
    ON execution.funding_payments (run_id, funding_rate_id, series_identity_sha256);
CREATE INDEX IF NOT EXISTS idx_phase5_cash_ledger_run_time
    ON execution.cash_ledger_entries (run_id, event_timestamp_utc, cash_ledger_entry_id);
CREATE UNIQUE INDEX IF NOT EXISTS uq_phase5_backtest_run_record
    ON backtesting.backtest_runs (record_sha256) WHERE record_sha256 IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_phase5_backtest_events_order
    ON backtesting.backtest_events (backtest_run_id, event_timestamp_utc, event_priority, deterministic_sequence);

ALTER TABLE execution.order_intents
    DROP CONSTRAINT IF EXISTS phase5_order_intents_hash_check,
    ADD CONSTRAINT phase5_order_intents_hash_check CHECK (
        record_sha256 IS NULL OR (
            series_identity_sha256 ~ '^[0-9a-f]{64}$' AND
            config_sha256 ~ '^[0-9a-f]{64}$' AND
            data_sha256 ~ '^[0-9a-f]{64}$' AND
            implementation_code_sha256 ~ '^[0-9a-f]{64}$' AND
            record_sha256 ~ '^[0-9a-f]{64}$'
        )
    ) NOT VALID;

ALTER TABLE execution.fills
    DROP CONSTRAINT IF EXISTS phase5_fills_values_check,
    ADD CONSTRAINT phase5_fills_values_check CHECK (
        record_sha256 IS NULL OR (
            base_price > 0 AND notional > 0 AND slippage_amount >= 0 AND slippage_bps >= 0 AND
            series_identity_sha256 ~ '^[0-9a-f]{64}$' AND record_sha256 ~ '^[0-9a-f]{64}$'
        )
    ) NOT VALID;

COMMENT ON TABLE execution.risk_decisions IS 'Auditable pre-submit and pre-fill deterministic risk decisions.';
COMMENT ON TABLE execution.position_snapshots IS 'Immutable position state after fills and point-in-time marks.';
COMMENT ON TABLE execution.funding_payments IS 'Realized linear-perpetual funding cash flows with grounded interval lineage.';
COMMENT ON TABLE execution.cash_ledger_entries IS 'Every simulated cash change, including initial cash, fees, funding, Spot notional, and realized PnL.';
COMMENT ON TABLE backtesting.backtest_events IS 'Deterministically ordered public-safe Phase 5 event audit log.';

COMMIT;
