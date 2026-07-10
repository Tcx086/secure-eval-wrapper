BEGIN;

-- Phase 5 second-audit repair. Migrations 0001 through 0009 remain immutable.
-- Nullable legacy columns are backfilled before new repository writes are constrained.

ALTER TABLE backtesting.backtest_runs
    ADD COLUMN IF NOT EXISTS account_ref TEXT,
    ADD COLUMN IF NOT EXISTS fee_currency TEXT,
    ADD COLUMN IF NOT EXISTS run_mode TEXT DEFAULT 'backtest',
    ADD COLUMN IF NOT EXISTS run_identity_version TEXT DEFAULT 'phase5-backtest-run-v2';

UPDATE backtesting.backtest_runs AS run
SET account_ref = COALESCE(
        run.account_ref,
        (SELECT snapshot.account_ref
         FROM execution.account_snapshots AS snapshot
         WHERE snapshot.backtest_run_id = run.backtest_run_id
         ORDER BY snapshot.snapshot_at_utc, snapshot.account_snapshot_id
         LIMIT 1),
        (SELECT position.account_ref
         FROM execution.positions AS position
         WHERE position.backtest_run_id = run.backtest_run_id
         ORDER BY position.position_id
         LIMIT 1),
        'public-simulation'
    ),
    fee_currency = COALESCE(run.fee_currency, run.base_currency),
    run_mode = COALESCE(run.run_mode, 'backtest'),
    run_identity_version = COALESCE(run.run_identity_version, 'phase5-backtest-run-v2');

ALTER TABLE backtesting.backtest_runs
    DROP CONSTRAINT IF EXISTS phase5_backtest_runs_fee_base_check,
    ADD CONSTRAINT phase5_backtest_runs_fee_base_check CHECK (
        fee_currency IS NULL OR (base_currency IS NOT NULL AND fee_currency = base_currency)
    ),
    DROP CONSTRAINT IF EXISTS phase5_backtest_runs_mode_check,
    ADD CONSTRAINT phase5_backtest_runs_mode_check CHECK (run_mode = 'backtest'),
    DROP CONSTRAINT IF EXISTS phase5_backtest_runs_complete_check,
    ADD CONSTRAINT phase5_backtest_runs_complete_check CHECK (
        record_sha256 IS NULL OR (
            initial_cash IS NOT NULL AND base_currency IS NOT NULL AND fee_currency IS NOT NULL AND
            account_ref IS NOT NULL AND run_identity_version = 'phase5-backtest-run-v2'
        )
    );

CREATE UNIQUE INDEX IF NOT EXISTS uq_phase5_backtest_run_base_currency
    ON backtesting.backtest_runs (backtest_run_id, base_currency);
CREATE UNIQUE INDEX IF NOT EXISTS uq_phase5_backtest_run_account_ref
    ON backtesting.backtest_runs (backtest_run_id, account_ref);

-- Option A from the audit: the configured simulation account identity is persisted end to end.
UPDATE execution.positions AS position
SET account_ref = run.account_ref
FROM backtesting.backtest_runs AS run
WHERE position.backtest_run_id = run.backtest_run_id
  AND position.account_ref IS DISTINCT FROM run.account_ref;

UPDATE execution.account_snapshots AS snapshot
SET account_ref = run.account_ref
FROM backtesting.backtest_runs AS run
WHERE snapshot.backtest_run_id = run.backtest_run_id
  AND snapshot.account_ref IS DISTINCT FROM run.account_ref;

ALTER TABLE execution.positions
    DROP CONSTRAINT IF EXISTS phase5_positions_run_account_fk,
    ADD CONSTRAINT phase5_positions_run_account_fk
        FOREIGN KEY (backtest_run_id, account_ref)
        REFERENCES backtesting.backtest_runs (backtest_run_id, account_ref)
        ON DELETE CASCADE NOT VALID;

ALTER TABLE execution.account_snapshots
    DROP CONSTRAINT IF EXISTS phase5_account_snapshots_run_account_fk,
    ADD CONSTRAINT phase5_account_snapshots_run_account_fk
        FOREIGN KEY (backtest_run_id, account_ref)
        REFERENCES backtesting.backtest_runs (backtest_run_id, account_ref)
        ON DELETE CASCADE NOT VALID;

-- Immutable position-snapshot identity is independent of record content.
ALTER TABLE execution.position_snapshots
    ADD COLUMN IF NOT EXISTS account_ref TEXT,
    ADD COLUMN IF NOT EXISTS snapshot_kind TEXT,
    ADD COLUMN IF NOT EXISTS mark_source TEXT,
    ADD COLUMN IF NOT EXISTS source_event_id UUID,
    ADD COLUMN IF NOT EXISTS logical_sequence BIGINT;

UPDATE execution.position_snapshots AS snapshot
SET account_ref = position.account_ref
FROM execution.positions AS position
WHERE position.position_id = snapshot.position_id
  AND snapshot.account_ref IS NULL;

UPDATE execution.position_snapshots
SET snapshot_kind = CASE WHEN source_fill_id IS NULL THEN 'bar_close_mark' ELSE 'fill' END
WHERE snapshot_kind IS NULL;

UPDATE execution.position_snapshots
SET mark_source = CASE
    WHEN mark_price IS NULL THEN NULL
    ELSE 'bar_close'
END
WHERE mark_source IS NULL;

UPDATE execution.position_snapshots AS snapshot
SET source_event_id = COALESCE(
    (SELECT event.backtest_event_id
     FROM backtesting.backtest_events AS event
     WHERE event.backtest_run_id = snapshot.backtest_run_id
       AND event.event_timestamp_utc = snapshot.snapshot_at_utc
       AND event.event_type = CASE WHEN snapshot.source_fill_id IS NULL THEN 'mark_update' ELSE 'fill' END
       AND (snapshot.source_fill_id IS NULL OR event.parent_record_id = snapshot.source_fill_id)
     ORDER BY event.deterministic_sequence
     LIMIT 1),
    (SELECT event.backtest_event_id
     FROM backtesting.backtest_events AS event
     WHERE event.backtest_run_id = snapshot.backtest_run_id
       AND event.parent_record_id = snapshot.position_snapshot_id
     ORDER BY event.deterministic_sequence
     LIMIT 1),
    snapshot.position_snapshot_id
)
WHERE snapshot.source_event_id IS NULL;

WITH ranked AS (
    SELECT position_snapshot_id,
           row_number() OVER (
               PARTITION BY run_id
               ORDER BY snapshot_at_utc, position_id, position_snapshot_id
           ) - 1 AS sequence_value
    FROM execution.position_snapshots
)
UPDATE execution.position_snapshots AS snapshot
SET logical_sequence = ranked.sequence_value
FROM ranked
WHERE ranked.position_snapshot_id = snapshot.position_snapshot_id
  AND snapshot.logical_sequence IS NULL;

ALTER TABLE execution.position_snapshots
    ALTER COLUMN account_ref SET NOT NULL,
    ALTER COLUMN snapshot_kind SET NOT NULL,
    ALTER COLUMN source_event_id SET NOT NULL,
    ALTER COLUMN logical_sequence SET NOT NULL,
    DROP CONSTRAINT IF EXISTS position_snapshots_position_id_snapshot_at_utc_record_sha_key,
    DROP CONSTRAINT IF EXISTS phase5_position_snapshots_kind_check,
    ADD CONSTRAINT phase5_position_snapshots_kind_check CHECK (
        (snapshot_kind = 'fill' AND source_fill_id IS NOT NULL) OR
        (snapshot_kind = 'bar_open_mark' AND source_fill_id IS NULL AND mark_source = 'bar_open') OR
        (snapshot_kind = 'bar_close_mark' AND source_fill_id IS NULL AND mark_source = 'bar_close')
    ),
    DROP CONSTRAINT IF EXISTS phase5_position_snapshots_mark_source_check,
    ADD CONSTRAINT phase5_position_snapshots_mark_source_check CHECK (
        mark_source IS NULL OR mark_source IN ('bar_open', 'bar_close')
    ),
    DROP CONSTRAINT IF EXISTS phase5_position_snapshots_mark_provenance_check,
    ADD CONSTRAINT phase5_position_snapshots_mark_provenance_check CHECK (
        mark_price IS NULL OR mark_source IS NOT NULL
    ),
    DROP CONSTRAINT IF EXISTS phase5_position_snapshots_sequence_check,
    ADD CONSTRAINT phase5_position_snapshots_sequence_check CHECK (logical_sequence >= 0),
    DROP CONSTRAINT IF EXISTS phase5_position_snapshots_run_account_fk,
    ADD CONSTRAINT phase5_position_snapshots_run_account_fk
        FOREIGN KEY (backtest_run_id, account_ref)
        REFERENCES backtesting.backtest_runs (backtest_run_id, account_ref)
        ON DELETE CASCADE NOT VALID;

CREATE UNIQUE INDEX IF NOT EXISTS uq_phase5_position_snapshots_logical
    ON execution.position_snapshots (
        run_id, account_ref, position_id, snapshot_at_utc,
        snapshot_kind, source_event_id, logical_sequence
    );
CREATE INDEX IF NOT EXISTS idx_phase5_position_snapshots_source
    ON execution.position_snapshots (source_event_id, source_fill_id, snapshot_kind);

-- Cash-ledger identity uses a deterministic per-run sequence, never record_sha256.
ALTER TABLE execution.cash_ledger_entries
    ADD COLUMN IF NOT EXISTS ledger_sequence BIGINT;

WITH ranked AS (
    SELECT cash_ledger_entry_id,
           row_number() OVER (
               PARTITION BY run_id
               ORDER BY event_timestamp_utc, created_at_utc, cash_ledger_entry_id
           ) - 1 AS sequence_value
    FROM execution.cash_ledger_entries
)
UPDATE execution.cash_ledger_entries AS entry
SET ledger_sequence = ranked.sequence_value
FROM ranked
WHERE ranked.cash_ledger_entry_id = entry.cash_ledger_entry_id
  AND entry.ledger_sequence IS NULL;

ALTER TABLE execution.cash_ledger_entries
    ALTER COLUMN ledger_sequence SET NOT NULL,
    DROP CONSTRAINT IF EXISTS cash_ledger_entries_run_id_record_sha256_key,
    DROP CONSTRAINT IF EXISTS phase5_cash_ledger_sequence_check,
    ADD CONSTRAINT phase5_cash_ledger_sequence_check CHECK (ledger_sequence >= 0);

CREATE UNIQUE INDEX IF NOT EXISTS uq_phase5_cash_ledger_logical
    ON execution.cash_ledger_entries (run_id, ledger_sequence);
CREATE INDEX IF NOT EXISTS idx_phase5_cash_ledger_source
    ON execution.cash_ledger_entries (run_id, event_timestamp_utc, entry_type, fill_id, funding_payment_id);

-- Fill fees, fee-ledger rows, and perpetual settlement stay in the run base currency.
CREATE UNIQUE INDEX IF NOT EXISTS uq_phase5_fill_fee_currency
    ON execution.fills (fill_id, fee_asset);

ALTER TABLE execution.fills
    DROP CONSTRAINT IF EXISTS phase5_fills_fee_base_fk,
    ADD CONSTRAINT phase5_fills_fee_base_fk
        FOREIGN KEY (backtest_run_id, fee_asset)
        REFERENCES backtesting.backtest_runs (backtest_run_id, base_currency)
        ON DELETE CASCADE NOT VALID;

ALTER TABLE execution.cash_ledger_entries
    DROP CONSTRAINT IF EXISTS phase5_cash_ledger_base_currency_fk,
    ADD CONSTRAINT phase5_cash_ledger_base_currency_fk
        FOREIGN KEY (backtest_run_id, currency)
        REFERENCES backtesting.backtest_runs (backtest_run_id, base_currency)
        ON DELETE CASCADE NOT VALID,
    DROP CONSTRAINT IF EXISTS phase5_cash_ledger_fill_currency_fk,
    ADD CONSTRAINT phase5_cash_ledger_fill_currency_fk
        FOREIGN KEY (fill_id, currency)
        REFERENCES execution.fills (fill_id, fee_asset)
        ON DELETE CASCADE NOT VALID;

ALTER TABLE execution.funding_payments
    DROP CONSTRAINT IF EXISTS phase5_funding_settlement_base_fk,
    ADD CONSTRAINT phase5_funding_settlement_base_fk
        FOREIGN KEY (backtest_run_id, settlement_asset)
        REFERENCES backtesting.backtest_runs (backtest_run_id, base_currency)
        ON DELETE CASCADE NOT VALID;

-- A pre-fill decision is explicitly descended from both intent and simulated order.
UPDATE execution.risk_decisions AS decision
SET order_id = simulated_order.order_id
FROM execution.orders AS simulated_order
WHERE decision.stage = 'pre_fill'
  AND decision.order_id IS NULL
  AND simulated_order.order_intent_id = decision.order_intent_id;

UPDATE execution.risk_decisions
SET parent_ids = CASE
        WHEN NOT (order_intent_id = ANY(parent_ids)) THEN array_append(parent_ids, order_intent_id)
        ELSE parent_ids
    END;

UPDATE execution.risk_decisions
SET parent_ids = array_append(parent_ids, order_id)
WHERE stage = 'pre_fill'
  AND order_id IS NOT NULL
  AND NOT (order_id = ANY(parent_ids));

ALTER TABLE execution.risk_decisions
    DROP CONSTRAINT IF EXISTS risk_decisions_order_id_fkey,
    ADD CONSTRAINT risk_decisions_order_id_fkey
        FOREIGN KEY (order_id) REFERENCES execution.orders (order_id) ON DELETE CASCADE,
    DROP CONSTRAINT IF EXISTS phase5_risk_order_lineage_check,
    ADD CONSTRAINT phase5_risk_order_lineage_check CHECK (
        (stage = 'pre_submit' AND order_id IS NULL AND order_intent_id = ANY(parent_ids)) OR
        (stage = 'pre_fill' AND order_id IS NOT NULL AND order_intent_id = ANY(parent_ids) AND order_id = ANY(parent_ids))
    ) NOT VALID;

-- Canonical lowercase hexadecimal SHA-256 constraints for every Phase 5 extension column.
ALTER TABLE execution.order_intents
    ADD CONSTRAINT phase5_order_intents_series_sha256_check CHECK (series_identity_sha256 IS NULL OR series_identity_sha256 ~ '^[0-9a-f]{64}$') NOT VALID,
    ADD CONSTRAINT phase5_order_intents_config_sha256_check CHECK (config_sha256 IS NULL OR config_sha256 ~ '^[0-9a-f]{64}$') NOT VALID,
    ADD CONSTRAINT phase5_order_intents_data_sha256_check CHECK (data_sha256 IS NULL OR data_sha256 ~ '^[0-9a-f]{64}$') NOT VALID,
    ADD CONSTRAINT phase5_order_intents_implementation_sha256_check CHECK (implementation_code_sha256 IS NULL OR implementation_code_sha256 ~ '^[0-9a-f]{64}$') NOT VALID,
    ADD CONSTRAINT phase5_order_intents_record_sha256_check CHECK (record_sha256 IS NULL OR record_sha256 ~ '^[0-9a-f]{64}$') NOT VALID;

ALTER TABLE execution.orders
    ADD CONSTRAINT phase5_orders_series_sha256_check CHECK (series_identity_sha256 IS NULL OR series_identity_sha256 ~ '^[0-9a-f]{64}$') NOT VALID,
    ADD CONSTRAINT phase5_orders_config_sha256_check CHECK (config_sha256 IS NULL OR config_sha256 ~ '^[0-9a-f]{64}$') NOT VALID,
    ADD CONSTRAINT phase5_orders_record_sha256_check CHECK (record_sha256 IS NULL OR record_sha256 ~ '^[0-9a-f]{64}$') NOT VALID;

ALTER TABLE execution.fills
    ADD CONSTRAINT phase5_fills_series_sha256_check CHECK (series_identity_sha256 IS NULL OR series_identity_sha256 ~ '^[0-9a-f]{64}$') NOT VALID,
    ADD CONSTRAINT phase5_fills_config_sha256_check CHECK (config_sha256 IS NULL OR config_sha256 ~ '^[0-9a-f]{64}$') NOT VALID,
    ADD CONSTRAINT phase5_fills_record_sha256_check CHECK (record_sha256 IS NULL OR record_sha256 ~ '^[0-9a-f]{64}$') NOT VALID;

ALTER TABLE execution.positions
    ADD CONSTRAINT phase5_positions_series_sha256_check CHECK (series_identity_sha256 IS NULL OR series_identity_sha256 ~ '^[0-9a-f]{64}$') NOT VALID,
    ADD CONSTRAINT phase5_positions_config_sha256_check CHECK (config_sha256 IS NULL OR config_sha256 ~ '^[0-9a-f]{64}$') NOT VALID,
    ADD CONSTRAINT phase5_positions_record_sha256_check CHECK (record_sha256 IS NULL OR record_sha256 ~ '^[0-9a-f]{64}$') NOT VALID;

ALTER TABLE execution.account_snapshots
    ADD CONSTRAINT phase5_account_snapshots_config_sha256_check CHECK (config_sha256 IS NULL OR config_sha256 ~ '^[0-9a-f]{64}$') NOT VALID,
    ADD CONSTRAINT phase5_account_snapshots_record_sha256_check CHECK (record_sha256 IS NULL OR record_sha256 ~ '^[0-9a-f]{64}$') NOT VALID;

ALTER TABLE execution.cash_ledger_entries
    ADD CONSTRAINT phase5_cash_ledger_series_sha256_check CHECK (series_identity_sha256 IS NULL OR series_identity_sha256 ~ '^[0-9a-f]{64}$') NOT VALID;

ALTER TABLE backtesting.backtest_runs
    ADD CONSTRAINT phase5_backtest_runs_data_sha256_check CHECK (data_sha256 IS NULL OR data_sha256 ~ '^[0-9a-f]{64}$') NOT VALID,
    ADD CONSTRAINT phase5_backtest_runs_implementation_sha256_check CHECK (implementation_code_sha256 IS NULL OR implementation_code_sha256 ~ '^[0-9a-f]{64}$') NOT VALID,
    ADD CONSTRAINT phase5_backtest_runs_record_sha256_check CHECK (record_sha256 IS NULL OR record_sha256 ~ '^[0-9a-f]{64}$') NOT VALID;

ALTER TABLE backtesting.backtest_metrics
    ADD CONSTRAINT phase5_backtest_metrics_config_sha256_check CHECK (config_sha256 IS NULL OR config_sha256 ~ '^[0-9a-f]{64}$') NOT VALID,
    ADD CONSTRAINT phase5_backtest_metrics_record_sha256_check CHECK (record_sha256 IS NULL OR record_sha256 ~ '^[0-9a-f]{64}$') NOT VALID;

ALTER TABLE backtesting.equity_curves
    ADD CONSTRAINT phase5_equity_curves_config_sha256_check CHECK (config_sha256 IS NULL OR config_sha256 ~ '^[0-9a-f]{64}$') NOT VALID,
    ADD CONSTRAINT phase5_equity_curves_record_sha256_check CHECK (record_sha256 IS NULL OR record_sha256 ~ '^[0-9a-f]{64}$') NOT VALID;

ALTER TABLE backtesting.backtest_events
    ADD CONSTRAINT phase5_backtest_events_series_sha256_check CHECK (series_identity_sha256 IS NULL OR series_identity_sha256 ~ '^[0-9a-f]{64}$') NOT VALID;

-- Validate upgrade-safe checks and cross-table constraints after all backfills.
ALTER TABLE execution.positions VALIDATE CONSTRAINT phase5_positions_run_account_fk;
ALTER TABLE execution.account_snapshots VALIDATE CONSTRAINT phase5_account_snapshots_run_account_fk;
ALTER TABLE execution.position_snapshots VALIDATE CONSTRAINT phase5_position_snapshots_run_account_fk;
ALTER TABLE execution.fills VALIDATE CONSTRAINT phase5_fills_fee_base_fk;
ALTER TABLE execution.cash_ledger_entries VALIDATE CONSTRAINT phase5_cash_ledger_base_currency_fk;
ALTER TABLE execution.cash_ledger_entries VALIDATE CONSTRAINT phase5_cash_ledger_fill_currency_fk;
ALTER TABLE execution.funding_payments VALIDATE CONSTRAINT phase5_funding_settlement_base_fk;
ALTER TABLE execution.risk_decisions VALIDATE CONSTRAINT phase5_risk_order_lineage_check;

ALTER TABLE execution.order_intents VALIDATE CONSTRAINT phase5_order_intents_series_sha256_check;
ALTER TABLE execution.order_intents VALIDATE CONSTRAINT phase5_order_intents_config_sha256_check;
ALTER TABLE execution.order_intents VALIDATE CONSTRAINT phase5_order_intents_data_sha256_check;
ALTER TABLE execution.order_intents VALIDATE CONSTRAINT phase5_order_intents_implementation_sha256_check;
ALTER TABLE execution.order_intents VALIDATE CONSTRAINT phase5_order_intents_record_sha256_check;
ALTER TABLE execution.orders VALIDATE CONSTRAINT phase5_orders_series_sha256_check;
ALTER TABLE execution.orders VALIDATE CONSTRAINT phase5_orders_config_sha256_check;
ALTER TABLE execution.orders VALIDATE CONSTRAINT phase5_orders_record_sha256_check;
ALTER TABLE execution.fills VALIDATE CONSTRAINT phase5_fills_series_sha256_check;
ALTER TABLE execution.fills VALIDATE CONSTRAINT phase5_fills_config_sha256_check;
ALTER TABLE execution.fills VALIDATE CONSTRAINT phase5_fills_record_sha256_check;
ALTER TABLE execution.positions VALIDATE CONSTRAINT phase5_positions_series_sha256_check;
ALTER TABLE execution.positions VALIDATE CONSTRAINT phase5_positions_config_sha256_check;
ALTER TABLE execution.positions VALIDATE CONSTRAINT phase5_positions_record_sha256_check;
ALTER TABLE execution.account_snapshots VALIDATE CONSTRAINT phase5_account_snapshots_config_sha256_check;
ALTER TABLE execution.account_snapshots VALIDATE CONSTRAINT phase5_account_snapshots_record_sha256_check;
ALTER TABLE execution.cash_ledger_entries VALIDATE CONSTRAINT phase5_cash_ledger_series_sha256_check;
ALTER TABLE backtesting.backtest_runs VALIDATE CONSTRAINT phase5_backtest_runs_data_sha256_check;
ALTER TABLE backtesting.backtest_runs VALIDATE CONSTRAINT phase5_backtest_runs_implementation_sha256_check;
ALTER TABLE backtesting.backtest_runs VALIDATE CONSTRAINT phase5_backtest_runs_record_sha256_check;
ALTER TABLE backtesting.backtest_metrics VALIDATE CONSTRAINT phase5_backtest_metrics_config_sha256_check;
ALTER TABLE backtesting.backtest_metrics VALIDATE CONSTRAINT phase5_backtest_metrics_record_sha256_check;
ALTER TABLE backtesting.equity_curves VALIDATE CONSTRAINT phase5_equity_curves_config_sha256_check;
ALTER TABLE backtesting.equity_curves VALIDATE CONSTRAINT phase5_equity_curves_record_sha256_check;
ALTER TABLE backtesting.backtest_events VALIDATE CONSTRAINT phase5_backtest_events_series_sha256_check;

COMMENT ON COLUMN execution.position_snapshots.snapshot_kind IS 'Logical immutable snapshot type: fill, bar_open_mark, or bar_close_mark.';
COMMENT ON COLUMN execution.position_snapshots.source_event_id IS 'Deterministic audit event that caused the snapshot; legacy fallback uses the immutable snapshot ID.';
COMMENT ON COLUMN execution.position_snapshots.logical_sequence IS 'Deterministic run-local source sequence used in logical identity independently of content hash.';
COMMENT ON COLUMN execution.cash_ledger_entries.ledger_sequence IS 'Deterministic run-local logical identity component; record_sha256 is content only.';
COMMENT ON COLUMN backtesting.backtest_runs.account_ref IS 'Configured public simulation account propagated to positions and account snapshots.';
COMMENT ON COLUMN backtesting.backtest_runs.fee_currency IS 'Must equal base_currency; Phase 5 performs no FX conversion.';
COMMENT ON COLUMN execution.risk_decisions.order_id IS 'Required simulated-order lineage for pre-fill decisions and null for pre-submit decisions.';

COMMIT;
