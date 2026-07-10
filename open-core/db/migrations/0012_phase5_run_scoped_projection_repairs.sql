BEGIN;

-- Phase 5 fourth-audit repair. Migrations 0001 through 0011 remain immutable.
-- Orders and positions retain stable execution-lineage rows. Horizon-dependent final
-- state is stored only in complete-backtest-run projections.
CREATE TABLE IF NOT EXISTS backtesting.backtest_order_states (
    backtest_run_id UUID NOT NULL
        CONSTRAINT phase5_order_states_run_fk
        REFERENCES backtesting.backtest_runs (backtest_run_id) ON DELETE CASCADE,
    order_id UUID NOT NULL
        CONSTRAINT phase5_order_states_order_fk
        REFERENCES execution.orders (order_id) ON DELETE RESTRICT,
    deterministic_ordinal BIGINT NOT NULL
        CONSTRAINT phase5_order_states_ordinal_check CHECK (deterministic_ordinal >= 0),
    order_status TEXT NOT NULL
        CONSTRAINT phase5_order_states_status_check CHECK (order_status IN (
            'submitted', 'acknowledged', 'partially_filled', 'filled',
            'cancelled', 'rejected', 'expired'
        )),
    triggered_at_utc TIMESTAMPTZ,
    activation_reason TEXT,
    reject_reason TEXT,
    state_provenance_jsonb JSONB NOT NULL DEFAULT '{}'::JSONB,
    final_record_sha256 CHAR(64) NOT NULL
        CONSTRAINT phase5_order_states_hash_check CHECK (final_record_sha256 ~ '^[0-9a-f]{64}$'),
    created_at_utc TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT pk_phase5_order_states PRIMARY KEY (backtest_run_id, order_id),
    CONSTRAINT uq_phase5_order_states_ordinal UNIQUE (backtest_run_id, deterministic_ordinal)
);

CREATE INDEX idx_phase5_order_states_order
    ON backtesting.backtest_order_states (order_id, backtest_run_id);
CREATE INDEX idx_phase5_order_states_status
    ON backtesting.backtest_order_states (backtest_run_id, order_status, deterministic_ordinal);

CREATE TABLE IF NOT EXISTS backtesting.backtest_position_states (
    backtest_run_id UUID NOT NULL
        CONSTRAINT phase5_position_states_run_fk
        REFERENCES backtesting.backtest_runs (backtest_run_id) ON DELETE CASCADE,
    position_id UUID NOT NULL
        CONSTRAINT phase5_position_states_position_fk
        REFERENCES execution.positions (position_id) ON DELETE RESTRICT,
    account_ref TEXT NOT NULL,
    series_identity_sha256 CHAR(64) NOT NULL
        CONSTRAINT phase5_position_states_series_hash_check CHECK (series_identity_sha256 ~ '^[0-9a-f]{64}$'),
    deterministic_ordinal BIGINT NOT NULL
        CONSTRAINT phase5_position_states_ordinal_check CHECK (deterministic_ordinal >= 0),
    accounting_mode TEXT NOT NULL
        CONSTRAINT phase5_position_states_accounting_check CHECK (accounting_mode IN ('spot', 'linear_perpetual')),
    quantity NUMERIC(38, 18) NOT NULL,
    average_entry_price NUMERIC(38, 18),
    realized_pnl NUMERIC(38, 18) NOT NULL,
    unrealized_pnl NUMERIC(38, 18) NOT NULL,
    source_fill_ids UUID[] NOT NULL DEFAULT ARRAY[]::UUID[],
    updated_at_utc TIMESTAMPTZ NOT NULL,
    mark_price NUMERIC(38, 18),
    config_sha256 CHAR(64) NOT NULL
        CONSTRAINT phase5_position_states_config_hash_check CHECK (config_sha256 ~ '^[0-9a-f]{64}$'),
    final_record_sha256 CHAR(64) NOT NULL
        CONSTRAINT phase5_position_states_hash_check CHECK (final_record_sha256 ~ '^[0-9a-f]{64}$'),
    created_at_utc TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT pk_phase5_position_states PRIMARY KEY (backtest_run_id, position_id),
    CONSTRAINT uq_phase5_position_states_series UNIQUE (backtest_run_id, account_ref, series_identity_sha256),
    CONSTRAINT uq_phase5_position_states_ordinal UNIQUE (backtest_run_id, deterministic_ordinal),
    CONSTRAINT phase5_position_states_quantity_check CHECK (
        (quantity = 0 AND average_entry_price IS NULL) OR
        (quantity <> 0 AND average_entry_price > 0)
    ),
    CONSTRAINT phase5_position_states_spot_check CHECK (accounting_mode <> 'spot' OR quantity >= 0)
);

CREATE INDEX idx_phase5_position_states_position
    ON backtesting.backtest_position_states (position_id, backtest_run_id);
CREATE INDEX idx_phase5_position_states_series
    ON backtesting.backtest_position_states (backtest_run_id, account_ref, series_identity_sha256);

-- Seeded 0011 installations carry the final state in the lineage tables and link it
-- through membership. Preserve that public-safe state before normalizing the lineage.
INSERT INTO backtesting.backtest_order_states (
    backtest_run_id, order_id, deterministic_ordinal, order_status,
    triggered_at_utc, activation_reason, reject_reason,
    state_provenance_jsonb, final_record_sha256
)
SELECT membership.backtest_run_id, child.order_id, membership.deterministic_ordinal,
       child.order_status, child.triggered_at_utc, child.activation_reason,
       child.reject_reason, child.provenance_jsonb, COALESCE(
           child.record_sha256,
           encode(sha256(convert_to(concat_ws('|',
               'phase5-legacy-order-state-v1', child.order_id::TEXT, child.order_status,
               child.triggered_at_utc::TEXT, child.activation_reason, child.reject_reason
           ), 'UTF8')), 'hex')
       )
FROM backtesting.backtest_run_memberships AS membership
JOIN execution.orders AS child ON child.order_id = membership.order_id
WHERE membership.record_type = 'order';

INSERT INTO backtesting.backtest_position_states (
    backtest_run_id, position_id, account_ref, series_identity_sha256,
    deterministic_ordinal, accounting_mode, quantity, average_entry_price,
    realized_pnl, unrealized_pnl, source_fill_ids, updated_at_utc,
    mark_price, config_sha256, final_record_sha256
)
SELECT membership.backtest_run_id, child.position_id, child.account_ref,
       COALESCE(child.series_identity_sha256, encode(sha256(convert_to(
           'phase5-legacy-position-series-v1|' || child.position_id::TEXT, 'UTF8'
       )), 'hex')), membership.deterministic_ordinal,
       COALESCE(child.accounting_mode, CASE WHEN child.quantity < 0 THEN 'linear_perpetual' ELSE 'spot' END), child.quantity, child.average_entry_price,
       child.realized_pnl, child.unrealized_pnl, child.source_fill_ids,
       child.updated_at_utc, child.mark_price, COALESCE(
           child.config_sha256,
           encode(sha256(convert_to('phase5-legacy-position-config-v1|' || child.position_id::TEXT, 'UTF8')), 'hex')
       ), COALESCE(
           child.record_sha256,
           encode(sha256(convert_to(concat_ws('|',
               'phase5-legacy-position-state-v1', child.position_id::TEXT, child.quantity::TEXT,
               child.average_entry_price::TEXT, child.realized_pnl::TEXT, child.updated_at_utc::TEXT
           ), 'UTF8')), 'hex')
       )
FROM backtesting.backtest_run_memberships AS membership
JOIN execution.positions AS child ON child.position_id = membership.position_id
WHERE membership.record_type = 'position';

-- Stable lineage hashes deliberately exclude every requested-horizon outcome.
UPDATE execution.orders
SET order_status = 'submitted',
    reject_reason = NULL,
    triggered_at_utc = NULL,
    activation_reason = NULL,
    provenance_jsonb = '{}'::JSONB,
    record_sha256 = encode(sha256(convert_to(
        'phase5-order-lineage-v1|' || order_id::TEXT, 'UTF8'
    )), 'hex')
WHERE EXISTS (
    SELECT 1 FROM backtesting.backtest_order_states AS state
    WHERE state.order_id = execution.orders.order_id
);

UPDATE execution.positions
SET quantity = 0,
    average_entry_price = NULL,
    realized_pnl = 0,
    unrealized_pnl = 0,
    source_fill_ids = ARRAY[]::UUID[],
    updated_at_utc = '1970-01-01T00:00:00Z'::TIMESTAMPTZ,
    mark_price = NULL,
    record_sha256 = encode(sha256(convert_to(
        'phase5-position-lineage-v1|' || position_id::TEXT, 'UTF8'
    )), 'hex')
WHERE EXISTS (
    SELECT 1 FROM backtesting.backtest_position_states AS state
    WHERE state.position_id = execution.positions.position_id
);

-- Final orders and positions are no longer immutable membership types.
DELETE FROM backtesting.backtest_run_memberships
WHERE record_type IN ('order', 'position');

DROP INDEX IF EXISTS backtesting.idx_phase5_run_memberships_order;
DROP INDEX IF EXISTS backtesting.idx_phase5_run_memberships_position;

ALTER TABLE backtesting.backtest_run_memberships
    DROP CONSTRAINT phase5_run_memberships_type_check,
    DROP CONSTRAINT phase5_run_memberships_one_record_check,
    DROP CONSTRAINT phase5_run_memberships_typed_record_check,
    DROP CONSTRAINT phase5_run_memberships_order_id_fk,
    DROP CONSTRAINT phase5_run_memberships_position_id_fk,
    DROP COLUMN order_id,
    DROP COLUMN position_id,
    ADD CONSTRAINT phase5_run_memberships_type_check CHECK (record_type IN (
        'order_intent', 'risk_decision', 'fill', 'position_snapshot',
        'funding_payment', 'cash_ledger_entry', 'account_snapshot',
        'backtest_event', 'equity_curve'
    )),
    ADD CONSTRAINT phase5_run_memberships_one_record_check CHECK (
        num_nonnulls(
            order_intent_id, risk_decision_id, fill_id, position_snapshot_id,
            funding_payment_id, cash_ledger_entry_id, account_snapshot_id,
            backtest_event_id, equity_curve_id
        ) = 1
    ),
    ADD CONSTRAINT phase5_run_memberships_typed_record_check CHECK (
        (record_type = 'order_intent' AND order_intent_id = record_id) OR
        (record_type = 'risk_decision' AND risk_decision_id = record_id) OR
        (record_type = 'fill' AND fill_id = record_id) OR
        (record_type = 'position_snapshot' AND position_snapshot_id = record_id) OR
        (record_type = 'funding_payment' AND funding_payment_id = record_id) OR
        (record_type = 'cash_ledger_entry' AND cash_ledger_entry_id = record_id) OR
        (record_type = 'account_snapshot' AND account_snapshot_id = record_id) OR
        (record_type = 'backtest_event' AND backtest_event_id = record_id) OR
        (record_type = 'equity_curve' AND equity_curve_id = record_id)
    );

COMMENT ON TABLE backtesting.backtest_order_states IS
    'Complete-run-scoped final order projections. Requested-horizon expiry is state of one backtest run, not immutable order lineage.';
COMMENT ON TABLE backtesting.backtest_position_states IS
    'Complete-run-scoped final position projections keyed by complete backtest run and stable position lineage.';
COMMENT ON TABLE backtesting.backtest_run_memberships IS
    'Authoritative complete-run membership for immutable Phase 5 economic and event records; final order and position projections are excluded.';
COMMENT ON COLUMN execution.orders.record_sha256 IS
    'Stable order-lineage hash; complete-run final state hashes are stored in backtesting.backtest_order_states.';
COMMENT ON COLUMN execution.positions.record_sha256 IS
    'Stable position-lineage hash; complete-run final state hashes are stored in backtesting.backtest_position_states.';

COMMIT;