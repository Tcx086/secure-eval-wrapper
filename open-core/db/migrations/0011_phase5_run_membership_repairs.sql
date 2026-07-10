BEGIN;

-- Phase 5 third-audit repair. Migrations 0001 through 0010 remain immutable.
-- Economic rows keep stable lineage-derived identities. This table is the authoritative
-- many-to-many mapping from a complete deterministic backtest run to the exact records
-- included in that run. deterministic_ordinal reconstructs the run without future unions.
CREATE TABLE IF NOT EXISTS backtesting.backtest_run_memberships (
    backtest_run_id UUID NOT NULL
        CONSTRAINT phase5_run_memberships_run_fk
        REFERENCES backtesting.backtest_runs (backtest_run_id) ON DELETE CASCADE,
    record_type TEXT NOT NULL CONSTRAINT phase5_run_memberships_type_check CHECK (record_type IN (
        'order_intent', 'risk_decision', 'order', 'fill', 'position',
        'position_snapshot', 'funding_payment', 'cash_ledger_entry',
        'account_snapshot', 'backtest_event', 'equity_curve'
    )),
    record_id UUID NOT NULL,
    deterministic_ordinal BIGINT NOT NULL CONSTRAINT phase5_run_memberships_ordinal_check CHECK (deterministic_ordinal >= 0),
    order_intent_id UUID CONSTRAINT phase5_run_memberships_order_intent_id_fk REFERENCES execution.order_intents (order_intent_id) ON DELETE RESTRICT,
    risk_decision_id UUID CONSTRAINT phase5_run_memberships_risk_decision_id_fk REFERENCES execution.risk_decisions (risk_decision_id) ON DELETE RESTRICT,
    order_id UUID CONSTRAINT phase5_run_memberships_order_id_fk REFERENCES execution.orders (order_id) ON DELETE RESTRICT,
    fill_id UUID CONSTRAINT phase5_run_memberships_fill_id_fk REFERENCES execution.fills (fill_id) ON DELETE RESTRICT,
    position_id UUID CONSTRAINT phase5_run_memberships_position_id_fk REFERENCES execution.positions (position_id) ON DELETE RESTRICT,
    position_snapshot_id UUID CONSTRAINT phase5_run_memberships_position_snapshot_id_fk REFERENCES execution.position_snapshots (position_snapshot_id) ON DELETE RESTRICT,
    funding_payment_id UUID CONSTRAINT phase5_run_memberships_funding_payment_id_fk REFERENCES execution.funding_payments (funding_payment_id) ON DELETE RESTRICT,
    cash_ledger_entry_id UUID CONSTRAINT phase5_run_memberships_cash_ledger_entry_id_fk REFERENCES execution.cash_ledger_entries (cash_ledger_entry_id) ON DELETE RESTRICT,
    account_snapshot_id UUID CONSTRAINT phase5_run_memberships_account_snapshot_id_fk REFERENCES execution.account_snapshots (account_snapshot_id) ON DELETE RESTRICT,
    backtest_event_id UUID CONSTRAINT phase5_run_memberships_backtest_event_id_fk REFERENCES backtesting.backtest_events (backtest_event_id) ON DELETE RESTRICT,
    equity_curve_id UUID CONSTRAINT phase5_run_memberships_equity_curve_id_fk REFERENCES backtesting.equity_curves (equity_curve_id) ON DELETE RESTRICT,
    created_at_utc TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT pk_phase5_run_memberships PRIMARY KEY (backtest_run_id, record_type, record_id),
    CONSTRAINT uq_phase5_run_memberships_ordinal UNIQUE (backtest_run_id, record_type, deterministic_ordinal),
    CONSTRAINT phase5_run_memberships_one_record_check CHECK (
        num_nonnulls(
            order_intent_id, risk_decision_id, order_id, fill_id, position_id,
            position_snapshot_id, funding_payment_id, cash_ledger_entry_id,
            account_snapshot_id, backtest_event_id, equity_curve_id
        ) = 1
    ),
    CONSTRAINT phase5_run_memberships_typed_record_check CHECK (
        (record_type = 'order_intent' AND order_intent_id = record_id) OR
        (record_type = 'risk_decision' AND risk_decision_id = record_id) OR
        (record_type = 'order' AND order_id = record_id) OR
        (record_type = 'fill' AND fill_id = record_id) OR
        (record_type = 'position' AND position_id = record_id) OR
        (record_type = 'position_snapshot' AND position_snapshot_id = record_id) OR
        (record_type = 'funding_payment' AND funding_payment_id = record_id) OR
        (record_type = 'cash_ledger_entry' AND cash_ledger_entry_id = record_id) OR
        (record_type = 'account_snapshot' AND account_snapshot_id = record_id) OR
        (record_type = 'backtest_event' AND backtest_event_id = record_id) OR
        (record_type = 'equity_curve' AND equity_curve_id = record_id)
    )
);

CREATE INDEX idx_phase5_run_memberships_record
    ON backtesting.backtest_run_memberships (record_type, record_id, backtest_run_id);
CREATE INDEX idx_phase5_run_memberships_order_intent
    ON backtesting.backtest_run_memberships (order_intent_id) WHERE order_intent_id IS NOT NULL;
CREATE INDEX idx_phase5_run_memberships_risk_decision
    ON backtesting.backtest_run_memberships (risk_decision_id) WHERE risk_decision_id IS NOT NULL;
CREATE INDEX idx_phase5_run_memberships_order
    ON backtesting.backtest_run_memberships (order_id) WHERE order_id IS NOT NULL;
CREATE INDEX idx_phase5_run_memberships_fill
    ON backtesting.backtest_run_memberships (fill_id) WHERE fill_id IS NOT NULL;
CREATE INDEX idx_phase5_run_memberships_position
    ON backtesting.backtest_run_memberships (position_id) WHERE position_id IS NOT NULL;
CREATE INDEX idx_phase5_run_memberships_position_snapshot
    ON backtesting.backtest_run_memberships (position_snapshot_id) WHERE position_snapshot_id IS NOT NULL;
CREATE INDEX idx_phase5_run_memberships_funding_payment
    ON backtesting.backtest_run_memberships (funding_payment_id) WHERE funding_payment_id IS NOT NULL;
CREATE INDEX idx_phase5_run_memberships_cash_ledger
    ON backtesting.backtest_run_memberships (cash_ledger_entry_id) WHERE cash_ledger_entry_id IS NOT NULL;
CREATE INDEX idx_phase5_run_memberships_account_snapshot
    ON backtesting.backtest_run_memberships (account_snapshot_id) WHERE account_snapshot_id IS NOT NULL;
CREATE INDEX idx_phase5_run_memberships_backtest_event
    ON backtesting.backtest_run_memberships (backtest_event_id) WHERE backtest_event_id IS NOT NULL;
CREATE INDEX idx_phase5_run_memberships_equity_curve
    ON backtesting.backtest_run_memberships (equity_curve_id) WHERE equity_curve_id IS NOT NULL;

-- Backfill every existing Phase 5 owner link as complete-run membership.
INSERT INTO backtesting.backtest_run_memberships (
    backtest_run_id, record_type, record_id, deterministic_ordinal, order_intent_id
)
SELECT backtest_run_id, 'order_intent', order_intent_id,
       row_number() OVER (PARTITION BY backtest_run_id ORDER BY event_timestamp_utc, order_intent_id) - 1,
       order_intent_id
FROM execution.order_intents
WHERE backtest_run_id IS NOT NULL;

INSERT INTO backtesting.backtest_run_memberships (
    backtest_run_id, record_type, record_id, deterministic_ordinal, risk_decision_id
)
SELECT backtest_run_id, 'risk_decision', risk_decision_id,
       row_number() OVER (PARTITION BY backtest_run_id ORDER BY decision_timestamp_utc, stage, risk_decision_id) - 1,
       risk_decision_id
FROM execution.risk_decisions
WHERE backtest_run_id IS NOT NULL;

INSERT INTO backtesting.backtest_run_memberships (
    backtest_run_id, record_type, record_id, deterministic_ordinal, order_id
)
SELECT backtest_run_id, 'order', order_id,
       row_number() OVER (PARTITION BY backtest_run_id ORDER BY submitted_at_utc, order_id) - 1,
       order_id
FROM execution.orders
WHERE backtest_run_id IS NOT NULL;

INSERT INTO backtesting.backtest_run_memberships (
    backtest_run_id, record_type, record_id, deterministic_ordinal, fill_id
)
SELECT backtest_run_id, 'fill', fill_id,
       row_number() OVER (PARTITION BY backtest_run_id ORDER BY filled_at_utc, fill_id) - 1,
       fill_id
FROM execution.fills
WHERE backtest_run_id IS NOT NULL;

INSERT INTO backtesting.backtest_run_memberships (
    backtest_run_id, record_type, record_id, deterministic_ordinal, position_id
)
SELECT backtest_run_id, 'position', position_id,
       row_number() OVER (PARTITION BY backtest_run_id ORDER BY series_identity_sha256, position_id) - 1,
       position_id
FROM execution.positions
WHERE backtest_run_id IS NOT NULL;

INSERT INTO backtesting.backtest_run_memberships (
    backtest_run_id, record_type, record_id, deterministic_ordinal, position_snapshot_id
)
SELECT backtest_run_id, 'position_snapshot', position_snapshot_id,
       row_number() OVER (
           PARTITION BY backtest_run_id
           ORDER BY snapshot_at_utc, logical_sequence, position_snapshot_id
       ) - 1,
       position_snapshot_id
FROM execution.position_snapshots
WHERE backtest_run_id IS NOT NULL;

INSERT INTO backtesting.backtest_run_memberships (
    backtest_run_id, record_type, record_id, deterministic_ordinal, funding_payment_id
)
SELECT backtest_run_id, 'funding_payment', funding_payment_id,
       row_number() OVER (PARTITION BY backtest_run_id ORDER BY funding_timestamp_utc, funding_payment_id) - 1,
       funding_payment_id
FROM execution.funding_payments
WHERE backtest_run_id IS NOT NULL;

INSERT INTO backtesting.backtest_run_memberships (
    backtest_run_id, record_type, record_id, deterministic_ordinal, cash_ledger_entry_id
)
SELECT backtest_run_id, 'cash_ledger_entry', cash_ledger_entry_id,
       row_number() OVER (
           PARTITION BY backtest_run_id
           ORDER BY event_timestamp_utc, ledger_sequence, cash_ledger_entry_id
       ) - 1,
       cash_ledger_entry_id
FROM execution.cash_ledger_entries
WHERE backtest_run_id IS NOT NULL;

INSERT INTO backtesting.backtest_run_memberships (
    backtest_run_id, record_type, record_id, deterministic_ordinal, account_snapshot_id
)
SELECT backtest_run_id, 'account_snapshot', account_snapshot_id,
       row_number() OVER (PARTITION BY backtest_run_id ORDER BY snapshot_at_utc, account_snapshot_id) - 1,
       account_snapshot_id
FROM execution.account_snapshots
WHERE backtest_run_id IS NOT NULL;

INSERT INTO backtesting.backtest_run_memberships (
    backtest_run_id, record_type, record_id, deterministic_ordinal, backtest_event_id
)
SELECT backtest_run_id, 'backtest_event', backtest_event_id,
       row_number() OVER (
           PARTITION BY backtest_run_id
           ORDER BY event_timestamp_utc, event_priority, deterministic_sequence, backtest_event_id
       ) - 1,
       backtest_event_id
FROM backtesting.backtest_events
WHERE backtest_run_id IS NOT NULL;

INSERT INTO backtesting.backtest_run_memberships (
    backtest_run_id, record_type, record_id, deterministic_ordinal, equity_curve_id
)
SELECT backtest_run_id, 'equity_curve', equity_curve_id,
       row_number() OVER (PARTITION BY backtest_run_id ORDER BY timestamp_utc, equity_curve_id) - 1,
       equity_curve_id
FROM backtesting.equity_curves
WHERE backtest_run_id IS NOT NULL;

-- Existing child run links become optional ownership hints. They are never used to
-- reconstruct a complete run, and deleting an owner must not cascade shared records.
ALTER TABLE execution.order_intents
    DROP CONSTRAINT IF EXISTS order_intents_backtest_run_id_fkey,
    ALTER COLUMN backtest_run_id DROP NOT NULL,
    ADD CONSTRAINT phase5_order_intents_owner_run_fk
        FOREIGN KEY (backtest_run_id) REFERENCES backtesting.backtest_runs (backtest_run_id)
        ON DELETE SET NULL;
ALTER TABLE execution.orders
    DROP CONSTRAINT IF EXISTS orders_backtest_run_id_fkey,
    ALTER COLUMN backtest_run_id DROP NOT NULL,
    ADD CONSTRAINT phase5_orders_owner_run_fk
        FOREIGN KEY (backtest_run_id) REFERENCES backtesting.backtest_runs (backtest_run_id)
        ON DELETE SET NULL;
ALTER TABLE execution.fills
    DROP CONSTRAINT IF EXISTS fills_backtest_run_id_fkey,
    DROP CONSTRAINT IF EXISTS phase5_fills_fee_base_fk,
    ALTER COLUMN backtest_run_id DROP NOT NULL,
    ADD CONSTRAINT phase5_fills_owner_run_fk
        FOREIGN KEY (backtest_run_id, fee_asset) REFERENCES backtesting.backtest_runs (backtest_run_id, base_currency)
        ON DELETE SET NULL (backtest_run_id);
ALTER TABLE execution.positions
    DROP CONSTRAINT IF EXISTS positions_backtest_run_id_fkey,
    DROP CONSTRAINT IF EXISTS phase5_positions_run_account_fk,
    ALTER COLUMN backtest_run_id DROP NOT NULL,
    ADD CONSTRAINT phase5_positions_owner_run_fk
        FOREIGN KEY (backtest_run_id, account_ref) REFERENCES backtesting.backtest_runs (backtest_run_id, account_ref)
        ON DELETE SET NULL (backtest_run_id);
ALTER TABLE execution.risk_decisions
    DROP CONSTRAINT IF EXISTS risk_decisions_backtest_run_id_fkey,
    ALTER COLUMN backtest_run_id DROP NOT NULL,
    ADD CONSTRAINT phase5_risk_decisions_owner_run_fk
        FOREIGN KEY (backtest_run_id) REFERENCES backtesting.backtest_runs (backtest_run_id)
        ON DELETE SET NULL;
ALTER TABLE execution.position_snapshots
    DROP CONSTRAINT IF EXISTS position_snapshots_backtest_run_id_fkey,
    DROP CONSTRAINT IF EXISTS phase5_position_snapshots_run_account_fk,
    ALTER COLUMN backtest_run_id DROP NOT NULL,
    ADD CONSTRAINT phase5_position_snapshots_owner_run_fk
        FOREIGN KEY (backtest_run_id, account_ref) REFERENCES backtesting.backtest_runs (backtest_run_id, account_ref)
        ON DELETE SET NULL (backtest_run_id);
ALTER TABLE execution.funding_payments
    DROP CONSTRAINT IF EXISTS funding_payments_backtest_run_id_fkey,
    DROP CONSTRAINT IF EXISTS phase5_funding_settlement_base_fk,
    ALTER COLUMN backtest_run_id DROP NOT NULL,
    ADD CONSTRAINT phase5_funding_payments_owner_run_fk
        FOREIGN KEY (backtest_run_id, settlement_asset) REFERENCES backtesting.backtest_runs (backtest_run_id, base_currency)
        ON DELETE SET NULL (backtest_run_id);
ALTER TABLE execution.cash_ledger_entries
    DROP CONSTRAINT IF EXISTS cash_ledger_entries_backtest_run_id_fkey,
    DROP CONSTRAINT IF EXISTS phase5_cash_ledger_base_currency_fk,
    ALTER COLUMN backtest_run_id DROP NOT NULL,
    ADD CONSTRAINT phase5_cash_ledger_owner_run_fk
        FOREIGN KEY (backtest_run_id, currency) REFERENCES backtesting.backtest_runs (backtest_run_id, base_currency)
        ON DELETE SET NULL (backtest_run_id);
ALTER TABLE execution.account_snapshots
    DROP CONSTRAINT IF EXISTS account_snapshots_backtest_run_id_fkey,
    DROP CONSTRAINT IF EXISTS phase5_account_snapshots_run_account_fk,
    ALTER COLUMN backtest_run_id DROP NOT NULL,
    ADD CONSTRAINT phase5_account_snapshots_owner_run_fk
        FOREIGN KEY (backtest_run_id, account_ref) REFERENCES backtesting.backtest_runs (backtest_run_id, account_ref)
        ON DELETE SET NULL (backtest_run_id);
ALTER TABLE backtesting.backtest_events
    DROP CONSTRAINT IF EXISTS backtest_events_backtest_run_id_fkey,
    ALTER COLUMN backtest_run_id DROP NOT NULL,
    ADD CONSTRAINT phase5_backtest_events_owner_run_fk
        FOREIGN KEY (backtest_run_id) REFERENCES backtesting.backtest_runs (backtest_run_id)
        ON DELETE SET NULL;
ALTER TABLE backtesting.equity_curves
    DROP CONSTRAINT IF EXISTS equity_curves_backtest_run_id_fkey,
    ALTER COLUMN backtest_run_id DROP NOT NULL,
    ADD CONSTRAINT phase5_equity_curves_owner_run_fk
        FOREIGN KEY (backtest_run_id) REFERENCES backtesting.backtest_runs (backtest_run_id)
        ON DELETE SET NULL;

COMMENT ON TABLE backtesting.backtest_run_memberships IS
    'Authoritative complete-run membership for immutable Phase 5 economic records; one record may belong to multiple deterministic backtest runs.';
COMMENT ON COLUMN backtesting.backtest_run_memberships.deterministic_ordinal IS
    'Zero-based deterministic order within one record type and one complete backtest run.';
COMMENT ON COLUMN execution.order_intents.backtest_run_id IS
    'Non-authoritative owner hint; complete-run inclusion is defined only by backtesting.backtest_run_memberships.';
COMMENT ON COLUMN backtesting.backtest_events.backtest_run_id IS
    'Non-authoritative owner hint; complete-run inclusion is defined only by backtesting.backtest_run_memberships.';
COMMENT ON COLUMN backtesting.equity_curves.backtest_run_id IS
    'Non-authoritative owner hint; complete-run inclusion is defined only by backtesting.backtest_run_memberships.';
COMMENT ON TABLE backtesting.backtest_metrics IS
    'Run-scoped aggregate metrics keyed by the complete deterministic backtest_run_id; metrics are not shared economic records.';

COMMIT;
