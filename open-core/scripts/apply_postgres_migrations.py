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


def apply(*, database=None, first=None, through=None, seed_legacy=False) -> None:
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
            digest = hashlib.sha256(content).hexdigest()
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
    finally:
        connection.close()


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--database")
    parser.add_argument("--create-database", action="store_true")
    parser.add_argument("--from-migration")
    parser.add_argument("--through")
    parser.add_argument("--seed-legacy", action="store_true")
    args = parser.parse_args(argv)
    if args.create_database:
        if not args.database:
            parser.error("--create-database requires --database")
        create_database(args.database)
    apply(database=args.database, first=args.from_migration, through=args.through, seed_legacy=args.seed_legacy)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
