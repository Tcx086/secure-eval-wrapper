"""Catalog verification for Phase 5 tables, columns, indexes, and orphan counts."""

from __future__ import annotations

import importlib
import json
import os

TABLE_COLUMNS = {
    "execution.risk_decisions": {"risk_decision_id", "order_intent_id", "stage", "decision_status", "record_sha256"},
    "execution.position_snapshots": {"position_snapshot_id", "position_id", "source_fill_id", "series_identity_sha256", "record_sha256"},
    "execution.funding_payments": {"funding_payment_id", "funding_rate_id", "cash_flow", "funding_interval", "record_sha256"},
    "execution.cash_ledger_entries": {"cash_ledger_entry_id", "entry_type", "amount", "balance_after", "record_sha256"},
    "backtesting.backtest_events": {"backtest_event_id", "deterministic_sequence", "event_priority", "event_sha256", "record_sha256"},
}
INDEXES = {"uq_phase5_order_intents_signal_series_time", "uq_phase5_orders_intent", "uq_phase5_fills_order", "uq_phase5_positions_series", "uq_phase5_risk_logical", "uq_phase5_funding_payment_logical", "idx_phase5_backtest_events_order"}


def connect():
    psycopg = importlib.import_module("psycopg")
    return psycopg.connect(host=os.environ["POSTGRES_HOST"], port=int(os.environ["POSTGRES_PORT"]), dbname=os.environ["POSTGRES_DB"], user=os.environ["POSTGRES_USER"], password=os.environ["POSTGRES_PASSWORD"], sslmode=os.environ.get("POSTGRES_SSLMODE", "disable"))


def main():
    connection = connect()
    counts = {}
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
            orphan_queries = {
                "orders_without_intents": "SELECT count(*) FROM execution.orders child LEFT JOIN execution.order_intents parent ON parent.order_intent_id=child.order_intent_id WHERE parent.order_intent_id IS NULL",
                "fills_without_orders": "SELECT count(*) FROM execution.fills child LEFT JOIN execution.orders parent ON parent.order_id=child.order_id WHERE parent.order_id IS NULL",
                "snapshots_without_positions": "SELECT count(*) FROM execution.position_snapshots child LEFT JOIN execution.positions parent ON parent.position_id=child.position_id WHERE parent.position_id IS NULL",
                "events_without_runs": "SELECT count(*) FROM backtesting.backtest_events child LEFT JOIN backtesting.backtest_runs parent ON parent.backtest_run_id=child.backtest_run_id WHERE parent.backtest_run_id IS NULL",
            }
            orphans = {}
            for name, sql in orphan_queries.items():
                cursor.execute(sql); orphans[name] = cursor.fetchone()[0]
            if any(orphans.values()):
                raise RuntimeError("orphaned Phase 5 rows detected")
    finally:
        connection.close()
    print(json.dumps({"status": "ok", "table_counts": counts, "orphans": orphans}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
