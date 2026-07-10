"""Cross-platform PostgreSQL-only migration runner used by CI and local validation."""

from __future__ import annotations

import argparse
import hashlib
import importlib
import os
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MIGRATIONS = ROOT / "open-core" / "db" / "migrations"


def _driver():
    try:
        return importlib.import_module("psycopg")
    except ImportError as exc:
        raise RuntimeError("migration application requires the optional postgres package extra") from exc


def _config(database=None):
    required = ("POSTGRES_HOST", "POSTGRES_PORT", "POSTGRES_DB", "POSTGRES_USER", "POSTGRES_PASSWORD")
    missing = [name for name in required if not os.environ.get(name)]
    if missing:
        raise RuntimeError("missing PostgreSQL environment variables: " + ", ".join(missing))
    return {"host": os.environ["POSTGRES_HOST"], "port": int(os.environ["POSTGRES_PORT"]), "dbname": database or os.environ["POSTGRES_DB"], "user": os.environ["POSTGRES_USER"], "password": os.environ["POSTGRES_PASSWORD"], "sslmode": os.environ.get("POSTGRES_SSLMODE", "disable")}


def create_database(name: str) -> None:
    if not re.fullmatch(r"[a-z][a-z0-9_]{0,62}", name):
        raise ValueError("test database name must be a conservative PostgreSQL identifier")
    psycopg = _driver()
    from psycopg import sql
    connection = psycopg.connect(**_config("postgres"), autocommit=True)
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1 FROM pg_database WHERE datname = %s", (name,))
            if cursor.fetchone() is None:
                cursor.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
    finally:
        connection.close()


def bootstrap(connection) -> None:
    with connection.cursor() as cursor:
        cursor.execute("CREATE SCHEMA IF NOT EXISTS audit")
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS audit.schema_migrations (
                migration_id TEXT PRIMARY KEY,
                filename TEXT NOT NULL UNIQUE,
                sha256 CHAR(64) NOT NULL CHECK (sha256 ~ '^[0-9a-f]{64}$'),
                applied_at_utc TIMESTAMPTZ NOT NULL DEFAULT now(),
                description TEXT NOT NULL
            )
        """)


def apply(*, database=None, first=None, through=None, seed_legacy=False, seed_phase5=False) -> None:
    psycopg = _driver()
    connection = psycopg.connect(**_config(database), autocommit=True)
    try:
        bootstrap(connection)
        files = sorted(MIGRATIONS.glob("[0-9][0-9][0-9][0-9]_*.sql"))
        for path in files:
            migration_key = path.name[:4]
            migration_id = path.stem
            if first and migration_key < first:
                continue
            if through and migration_key > through:
                continue
            content = path.read_bytes()
            digest = hashlib.sha256(content.replace(b"\r\n", b"\n")).hexdigest()
            with connection.cursor() as cursor:
                cursor.execute("SELECT filename, sha256 FROM audit.schema_migrations WHERE migration_id = %s", (migration_id,))
                existing = cursor.fetchone()
                if existing is not None:
                    if (existing[0], existing[1]) != (path.name, digest):
                        raise RuntimeError(f"migration hash conflict for {migration_id}")
                    continue
                cursor.execute(content.decode("utf-8-sig"))
                description = path.stem.split("_", 1)[1].replace("_", " ")
                cursor.execute("INSERT INTO audit.schema_migrations (migration_id, filename, sha256, description) VALUES (%s, %s, %s, %s)", (migration_id, path.name, digest, description))
            print(f"OK: applied {path.name} sha256={digest}")
        if seed_legacy:
            with connection.cursor() as cursor:
                cursor.execute("INSERT INTO backtesting.backtest_runs (backtest_run_id, run_id, status, metadata_jsonb) VALUES (%s, %s, 'completed', '{}'::jsonb) ON CONFLICT DO NOTHING", ("00000000-0000-0000-0000-000000000851", "00000000-0000-0000-0000-000000000852"))
                cursor.execute("INSERT INTO execution.order_intents (order_intent_id, run_id, symbol, side, order_type, quantity, intent_status) VALUES (%s, %s, 'BTC-USDT', 'buy', 'market', 1, 'created') ON CONFLICT DO NOTHING", ("00000000-0000-0000-0000-000000000853", "00000000-0000-0000-0000-000000000852"))
                cursor.execute("INSERT INTO execution.positions (position_id, run_id, account_ref, symbol, quantity) VALUES (%s, %s, 'simulation', 'BTC-USDT', 0) ON CONFLICT DO NOTHING", ("00000000-0000-0000-0000-000000000854", "00000000-0000-0000-0000-000000000852"))
            print("OK: seeded public-safe legacy 0008 execution/backtest rows")
        if seed_phase5:
            h = {name: character * 64 for name, character in {
                "series": "1", "config": "2", "data": "3", "implementation": "4",
                "run": "5", "intent": "6", "order": "7", "position": "8",
                "snapshot": "9", "risk": "a", "ledger": "b", "account": "c",
            }.items()}
            ids = {name: f"00000000-0000-0000-0000-0000000008{suffix}" for name, suffix in {
                "run": "61", "lineage": "62", "intent": "63", "order": "64",
                "position": "65", "snapshot": "66", "risk": "67", "ledger": "68", "account": "69",
            }.items()}
            timestamp = "2025-01-01T00:00:00+00:00"
            with connection.cursor() as cursor:
                cursor.execute("""
                    INSERT INTO backtesting.backtest_runs (
                        backtest_run_id, run_id, execution_model_sha256, config_sha256, status,
                        metadata_jsonb, started_at_utc, completed_at_utc, initial_cash, base_currency,
                        data_sha256, implementation_code_sha256, repository_commit_sha, record_sha256
                    ) VALUES (%s,%s,%s,%s,'completed','{}'::jsonb,%s,%s,1000,'USDT',%s,%s,'seeded-0009',%s)
                """, (ids["run"], ids["lineage"], h["config"], h["config"], timestamp, timestamp, h["data"], h["implementation"], h["run"]))
                cursor.execute("""
                    INSERT INTO execution.order_intents (
                        order_intent_id, run_id, backtest_run_id, symbol, side, order_type, quantity,
                        intent_status, provider_name, exchange_name, provider_instrument_id,
                        canonical_symbol, instrument_type, timeframe, settlement_asset,
                        series_identity_sha256, event_timestamp_utc, execution_mode, accounting_mode,
                        target_quantity, current_quantity, delta_quantity, reference_price, time_in_force,
                        config_sha256, data_sha256, implementation_code_sha256, repository_commit_sha,
                        record_sha256, parent_ids, provenance_jsonb
                    ) VALUES (%s,%s,%s,'BTC-USDT','buy','market',1,'submitted','seed','seed-x','BTC-USDT',
                        'BTC-USDT','perpetual_swap','1m','USDT',%s,%s,'backtest','linear_perpetual',
                        1,0,1,100,'gtc',%s,%s,%s,'seeded-0009',%s,ARRAY[]::uuid[],'{}'::jsonb)
                """, (ids["intent"], ids["lineage"], ids["run"], h["series"], timestamp, h["config"], h["data"], h["implementation"], h["intent"]))
                cursor.execute("""
                    INSERT INTO execution.orders (
                        order_id, order_intent_id, broker_order_ref, run_id, backtest_run_id, symbol,
                        side, order_type, order_status, submitted_at_utc, acknowledged_at_utc,
                        broker_payload_jsonb, provider_name, exchange_name, provider_instrument_id,
                        canonical_symbol, instrument_type, timeframe, settlement_asset,
                        series_identity_sha256, quantity, accounting_mode, time_in_force,
                        config_sha256, record_sha256, parent_ids, provenance_jsonb
                    ) VALUES (%s,%s,'seed-order',%s,%s,'BTC-USDT','buy','market','acknowledged',%s,%s,
                        '{}'::jsonb,'seed','seed-x','BTC-USDT','BTC-USDT','perpetual_swap','1m','USDT',
                        %s,1,'linear_perpetual','gtc',%s,%s,ARRAY[%s]::uuid[],'{}'::jsonb)
                """, (ids["order"], ids["intent"], ids["lineage"], ids["run"], timestamp, timestamp, h["series"], h["config"], h["order"], ids["intent"]))
                cursor.execute("""
                    INSERT INTO execution.risk_decisions (
                        risk_decision_id, run_id, backtest_run_id, order_intent_id, order_id,
                        provider_name, exchange_name, provider_instrument_id, canonical_symbol,
                        instrument_type, timeframe, settlement_asset, series_identity_sha256,
                        decision_timestamp_utc, stage, decision_status, reason_code, explanation,
                        config_sha256, record_sha256, parent_ids, provenance_jsonb
                    ) VALUES (%s,%s,%s,%s,NULL,'seed','seed-x','BTC-USDT','BTC-USDT','perpetual_swap',
                        '1m','USDT',%s,%s,'pre_fill','accepted','accepted','seeded accepted',%s,%s,
                        ARRAY[%s]::uuid[],'{}'::jsonb)
                """, (ids["risk"], ids["lineage"], ids["run"], ids["intent"], h["series"], timestamp, h["config"], h["risk"], ids["intent"]))
                cursor.execute("""
                    INSERT INTO execution.positions (
                        position_id, run_id, backtest_run_id, account_ref, symbol, quantity,
                        average_entry_price, realized_pnl, unrealized_pnl, source_fill_ids,
                        updated_at_utc, provider_name, exchange_name, provider_instrument_id,
                        canonical_symbol, instrument_type, timeframe, settlement_asset,
                        series_identity_sha256, accounting_mode, mark_price, config_sha256, record_sha256
                    ) VALUES (%s,%s,%s,'public-simulation','BTC-USDT',1,100,0,5,ARRAY[]::uuid[],%s,
                        'seed','seed-x','BTC-USDT','BTC-USDT','perpetual_swap','1m','USDT',%s,
                        'linear_perpetual',105,%s,%s)
                """, (ids["position"], ids["lineage"], ids["run"], timestamp, h["series"], h["config"], h["position"]))
                cursor.execute("""
                    INSERT INTO execution.position_snapshots (
                        position_snapshot_id, run_id, backtest_run_id, position_id, source_fill_id,
                        provider_name, exchange_name, provider_instrument_id, canonical_symbol,
                        instrument_type, timeframe, settlement_asset, series_identity_sha256,
                        accounting_mode, snapshot_at_utc, quantity, average_entry_price, mark_price,
                        realized_pnl, unrealized_pnl, stale_mark_age_seconds, config_sha256,
                        record_sha256, parent_ids
                    ) VALUES (%s,%s,%s,%s,NULL,'seed','seed-x','BTC-USDT','BTC-USDT','perpetual_swap',
                        '1m','USDT',%s,'linear_perpetual',%s,1,100,105,0,5,0,%s,%s,ARRAY[%s]::uuid[])
                """, (ids["snapshot"], ids["lineage"], ids["run"], ids["position"], h["series"], timestamp, h["config"], h["snapshot"], ids["position"]))
                cursor.execute("""
                    INSERT INTO execution.cash_ledger_entries (
                        cash_ledger_entry_id, run_id, backtest_run_id, event_timestamp_utc,
                        entry_type, amount, balance_after, currency, config_sha256, record_sha256
                    ) VALUES (%s,%s,%s,%s,'initial_cash',1000,1000,'USDT',%s,%s)
                """, (ids["ledger"], ids["lineage"], ids["run"], timestamp, h["config"], h["ledger"]))
                cursor.execute("""
                    INSERT INTO execution.account_snapshots (
                        account_snapshot_id, run_id, backtest_run_id, account_ref, snapshot_at_utc,
                        equity, cash, margin_used, balances_jsonb, classification, gross_exposure,
                        net_exposure, realized_pnl, unrealized_pnl, total_fees, total_funding,
                        stale_mark_count, config_sha256, record_sha256
                    ) VALUES (%s,%s,%s,'public-simulation',%s,1005,1000,0,'{}'::jsonb,
                        'public_synthetic',105,105,0,5,0,0,0,%s,%s)
                """, (ids["account"], ids["lineage"], ids["run"], timestamp, h["config"], h["account"]))
            print("OK: seeded public-safe complete Phase 5 rows at migration 0009")
    finally:
        connection.close()


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--database")
    parser.add_argument("--create-database", action="store_true")
    parser.add_argument("--from-migration")
    parser.add_argument("--through")
    parser.add_argument("--seed-legacy", action="store_true")
    parser.add_argument("--seed-phase5", action="store_true")
    args = parser.parse_args(argv)
    if args.create_database:
        if not args.database:
            parser.error("--create-database requires --database")
        create_database(args.database)
    apply(database=args.database, first=args.from_migration, through=args.through, seed_legacy=args.seed_legacy, seed_phase5=args.seed_phase5)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
