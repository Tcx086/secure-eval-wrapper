"""Catalog verification for Phase 5 second-audit tables, constraints, and orphan counts."""

from __future__ import annotations

import importlib
import json
import os

TABLE_COLUMNS = {
    "execution.risk_decisions": {"risk_decision_id", "order_intent_id", "order_id", "stage", "decision_status", "record_sha256"},
    "execution.position_snapshots": {"position_snapshot_id", "position_id", "account_ref", "source_fill_id", "source_event_id", "logical_sequence", "snapshot_kind", "mark_source", "series_identity_sha256", "record_sha256"},
    "execution.funding_payments": {"funding_payment_id", "funding_rate_id", "cash_flow", "funding_interval", "record_sha256"},
    "execution.cash_ledger_entries": {"cash_ledger_entry_id", "ledger_sequence", "entry_type", "amount", "balance_after", "currency", "record_sha256"},
    "backtesting.backtest_runs": {"backtest_run_id", "account_ref", "base_currency", "fee_currency", "run_mode", "run_identity_version", "implementation_code_sha256", "record_sha256"},
    "backtesting.backtest_events": {"backtest_event_id", "deterministic_sequence", "event_priority", "event_sha256", "record_sha256"},
}
INDEXES = {
    "uq_phase5_order_intents_signal_series_time", "uq_phase5_orders_intent",
    "uq_phase5_fills_order", "uq_phase5_positions_series", "uq_phase5_risk_logical",
    "uq_phase5_funding_payment_logical", "idx_phase5_backtest_events_order",
    "uq_phase5_position_snapshots_logical", "idx_phase5_position_snapshots_source",
    "uq_phase5_cash_ledger_logical", "idx_phase5_cash_ledger_source",
    "uq_phase5_backtest_run_base_currency", "uq_phase5_backtest_run_account_ref",
    "uq_phase5_fill_fee_currency",
}
CONSTRAINTS = {
    "phase5_backtest_runs_fee_base_check", "phase5_backtest_runs_mode_check",
    "phase5_backtest_runs_complete_check", "phase5_backtest_runs_data_sha256_check",
    "phase5_backtest_runs_implementation_sha256_check", "phase5_backtest_runs_record_sha256_check",
    "phase5_positions_run_account_fk", "phase5_account_snapshots_run_account_fk",
    "phase5_position_snapshots_run_account_fk", "phase5_position_snapshots_kind_check",
    "phase5_position_snapshots_mark_source_check", "phase5_position_snapshots_mark_provenance_check",
    "phase5_position_snapshots_sequence_check", "phase5_cash_ledger_sequence_check",
    "phase5_fills_fee_base_fk", "phase5_cash_ledger_base_currency_fk",
    "phase5_cash_ledger_fill_currency_fk", "phase5_funding_settlement_base_fk",
    "phase5_risk_order_lineage_check", "phase5_order_intents_implementation_sha256_check",
    "phase5_orders_record_sha256_check", "phase5_fills_record_sha256_check",
    "phase5_positions_record_sha256_check", "phase5_account_snapshots_record_sha256_check",
    "phase5_backtest_metrics_record_sha256_check", "phase5_equity_curves_record_sha256_check",
    "phase5_backtest_events_series_sha256_check",
}


def connect():
    psycopg = importlib.import_module("psycopg")
    return psycopg.connect(host=os.environ["POSTGRES_HOST"], port=int(os.environ["POSTGRES_PORT"]), dbname=os.environ["POSTGRES_DB"], user=os.environ["POSTGRES_USER"], password=os.environ["POSTGRES_PASSWORD"], sslmode=os.environ.get("POSTGRES_SSLMODE", "disable"))


def main():
    connection = connect()
    counts = {}
    orphans = {}
    consistency = {}
    try:
        with connection.cursor() as cursor:
            for qualified, required in TABLE_COLUMNS.items():
                schema, table = qualified.split(".")
                cursor.execute("SELECT column_name FROM information_schema.columns WHERE table_schema = %s AND table_name = %s", (schema, table))
                columns = {row[0] for row in cursor.fetchall()}
                missing = required - columns
                if missing:
                    raise RuntimeError(f"{qualified} missing columns: {sorted(missing)}")
                cursor.execute(f"SELECT count(*) FROM {qualified}")
                counts[qualified] = cursor.fetchone()[0]
            cursor.execute("SELECT indexname FROM pg_indexes WHERE schemaname IN ('execution', 'backtesting')")
            missing_indexes = INDEXES - {row[0] for row in cursor.fetchall()}
            if missing_indexes:
                raise RuntimeError("missing Phase 5 indexes: " + ", ".join(sorted(missing_indexes)))
            cursor.execute("""
                SELECT conname, convalidated
                FROM pg_constraint
                WHERE connamespace IN ('execution'::regnamespace, 'backtesting'::regnamespace)
            """)
            constraints = {row[0]: row[1] for row in cursor.fetchall()}
            missing_constraints = CONSTRAINTS - set(constraints)
            if missing_constraints:
                raise RuntimeError("missing Phase 5 constraints: " + ", ".join(sorted(missing_constraints)))
            unvalidated = sorted(name for name in CONSTRAINTS if not constraints[name])
            if unvalidated:
                raise RuntimeError("unvalidated Phase 5 constraints: " + ", ".join(unvalidated))
            orphan_queries = {
                "orders_without_intents": "SELECT count(*) FROM execution.orders child LEFT JOIN execution.order_intents parent ON parent.order_intent_id=child.order_intent_id WHERE parent.order_intent_id IS NULL",
                "fills_without_orders": "SELECT count(*) FROM execution.fills child LEFT JOIN execution.orders parent ON parent.order_id=child.order_id WHERE parent.order_id IS NULL",
                "snapshots_without_positions": "SELECT count(*) FROM execution.position_snapshots child LEFT JOIN execution.positions parent ON parent.position_id=child.position_id WHERE parent.position_id IS NULL",
                "events_without_runs": "SELECT count(*) FROM backtesting.backtest_events child LEFT JOIN backtesting.backtest_runs parent ON parent.backtest_run_id=child.backtest_run_id WHERE parent.backtest_run_id IS NULL",
                "prefill_without_orders": "SELECT count(*) FROM execution.risk_decisions WHERE stage='pre_fill' AND order_id IS NULL",
            }
            for name, sql in orphan_queries.items():
                cursor.execute(sql)
                orphans[name] = cursor.fetchone()[0]
            if any(orphans.values()):
                raise RuntimeError("orphaned Phase 5 rows detected")
            consistency_queries = {
                "run_fee_base_mismatch": "SELECT count(*) FROM backtesting.backtest_runs WHERE fee_currency IS DISTINCT FROM base_currency AND record_sha256 IS NOT NULL",
                "fill_fee_base_mismatch": "SELECT count(*) FROM execution.fills fill JOIN backtesting.backtest_runs run ON run.backtest_run_id=fill.backtest_run_id WHERE fill.fee_asset IS DISTINCT FROM run.base_currency",
                "ledger_base_mismatch": "SELECT count(*) FROM execution.cash_ledger_entries entry JOIN backtesting.backtest_runs run ON run.backtest_run_id=entry.backtest_run_id WHERE entry.currency IS DISTINCT FROM run.base_currency",
                "ledger_fill_currency_mismatch": "SELECT count(*) FROM execution.cash_ledger_entries entry JOIN execution.fills fill ON fill.fill_id=entry.fill_id WHERE entry.currency IS DISTINCT FROM fill.fee_asset",
                "position_account_mismatch": "SELECT count(*) FROM execution.positions position JOIN backtesting.backtest_runs run ON run.backtest_run_id=position.backtest_run_id WHERE position.account_ref IS DISTINCT FROM run.account_ref",
                "account_snapshot_mismatch": "SELECT count(*) FROM execution.account_snapshots snapshot JOIN backtesting.backtest_runs run ON run.backtest_run_id=snapshot.backtest_run_id WHERE snapshot.account_ref IS DISTINCT FROM run.account_ref",
            }
            for name, sql in consistency_queries.items():
                cursor.execute(sql)
                consistency[name] = cursor.fetchone()[0]
            if any(consistency.values()):
                raise RuntimeError("Phase 5 currency/account consistency check failed")
    finally:
        connection.close()
    print(json.dumps({"status": "ok", "table_counts": counts, "orphans": orphans, "consistency": consistency, "required_index_count": len(INDEXES), "required_constraint_count": len(CONSTRAINTS)}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
