"""Verify the local PostgreSQL schema foundation.

The script reads connection settings from environment variables, optionally loading a local `.env`
file first. It performs metadata-only checks and never inserts sample data.
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path
from typing import Iterable


REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_ENV_FILE = REPO_ROOT / ".env"
DEFAULT_MIGRATION = REPO_ROOT / "open-core" / "db" / "migrations" / "0001_initial_schema.sql"

REQUIRED_TABLES: dict[str, tuple[str, ...]] = {
    "market_data": (
        "raw_source_observations",
        "validated_bars",
        "validated_trades",
        "funding_rates",
        "instruments",
    ),
    "data_quality": (
        "data_quality_checks",
        "validation_reports",
    ),
    "alpha": (
        "alpha_registry",
    ),
    "signals": (
        "signal_runs",
        "signals",
    ),
    "execution": (
        "order_intents",
        "orders",
        "fills",
        "positions",
        "account_snapshots",
    ),
    "backtesting": (
        "backtest_runs",
        "backtest_metrics",
        "equity_curves",
        "stress_results",
    ),
    "monitoring": (
        "monitoring_events",
        "fix_session_events",
        "risk_events",
    ),
    "audit": (
        "run_manifests",
        "artifacts",
    ),
}

UNSAFE_SQL_PATTERNS = (
    r"\bDROP\s+DATABASE\b",
    r"\bDROP\s+SCHEMA\b",
    r"\bDROP\s+TABLE\b",
    r"\bTRUNCATE\b",
    r"\bDELETE\s+FROM\b",
    r"\bUPDATE\b",
    r"\bINSERT\s+INTO\b",
    r"\bCOPY\b",
    r"\\COPY\b",
    r"\bCREATE\s+EXTENSION\b",
    r"\bALTER\s+SYSTEM\b",
    r"\bCREATE\s+ROLE\b",
    r"\bCREATE\s+USER\b",
    r"\bGRANT\b",
    r"\bREVOKE\b",
)


def fail(message: str) -> None:
    print(f"ERROR: {message}", file=sys.stderr)
    raise SystemExit(1)


def load_env_file(env_file: Path) -> None:
    if not env_file.exists():
        return

    for raw_line in env_file.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key:
            os.environ.setdefault(key, value)


def strip_sql_comments(sql: str) -> str:
    without_block_comments = re.sub(r"/\*.*?\*/", " ", sql, flags=re.DOTALL)
    return re.sub(r"--.*?$", " ", without_block_comments, flags=re.MULTILINE)


def inspect_migration(migration_path: Path) -> None:
    if not migration_path.exists():
        fail(f"migration file not found: {migration_path}")

    sql = migration_path.read_text(encoding="utf-8")
    stripped_sql = strip_sql_comments(sql)

    for pattern in UNSAFE_SQL_PATTERNS:
        if re.search(pattern, stripped_sql, flags=re.IGNORECASE):
            fail(f"migration contains unsafe statement matching pattern: {pattern}")

    missing_definitions: list[str] = []
    for schema, tables in REQUIRED_TABLES.items():
        for table in tables:
            create_table_pattern = (
                r"\bCREATE\s+TABLE\s+IF\s+NOT\s+EXISTS\s+"
                + re.escape(f"{schema}.{table}")
                + r"\b"
            )
            if not re.search(create_table_pattern, stripped_sql, flags=re.IGNORECASE):
                missing_definitions.append(f"{schema}.{table}")

    if missing_definitions:
        fail("migration is missing required table definitions: " + ", ".join(missing_definitions))

    print(f"OK: inspected migration safely at {migration_path}")


def import_postgres_driver():
    try:
        import psycopg  # type: ignore

        return "psycopg", psycopg
    except ImportError:
        pass

    try:
        import psycopg2  # type: ignore

        return "psycopg2", psycopg2
    except ImportError:
        fail(
            "PostgreSQL driver not found. Install psycopg or psycopg2 in your local "
            "development environment before running schema verification."
        )


def require_env(names: Iterable[str]) -> None:
    missing = [name for name in names if not os.environ.get(name)]
    if missing:
        fail(
            "missing required PostgreSQL environment variables: "
            + ", ".join(missing)
            + ". Create .env from .env.example or export them before running."
        )


def postgres_config(timeout_seconds: int) -> dict[str, object]:
    require_env(("POSTGRES_DB", "POSTGRES_USER", "POSTGRES_PASSWORD"))
    return {
        "host": os.environ.get("POSTGRES_HOST", "127.0.0.1"),
        "port": int(os.environ.get("POSTGRES_PORT", "5432")),
        "dbname": os.environ["POSTGRES_DB"],
        "user": os.environ["POSTGRES_USER"],
        "password": os.environ["POSTGRES_PASSWORD"],
        "sslmode": os.environ.get("POSTGRES_SSLMODE", "disable"),
        "connect_timeout": timeout_seconds,
    }


def connect(driver_name: str, driver, config: dict[str, object]):
    try:
        return driver.connect(**config)
    except Exception as exc:  # pragma: no cover - exercised in local environments
        host = config["host"]
        port = config["port"]
        dbname = config["dbname"]
        user = config["user"]
        fail(
            "PostgreSQL is unavailable or rejected the connection "
            f"for {user}@{host}:{port}/{dbname}: {exc}"
        )


def fetch_existing_tables(connection) -> set[tuple[str, str]]:
    schemas = tuple(REQUIRED_TABLES)
    placeholders = ", ".join(["%s"] * len(schemas))
    query = f"""
        SELECT table_schema, table_name
        FROM information_schema.tables
        WHERE table_type = 'BASE TABLE'
          AND table_schema IN ({placeholders})
    """
    with connection.cursor() as cursor:
        cursor.execute(query, schemas)
        return {(row[0], row[1]) for row in cursor.fetchall()}


def verify_required_tables(connection) -> None:
    existing = fetch_existing_tables(connection)
    required = {
        (schema, table)
        for schema, tables in REQUIRED_TABLES.items()
        for table in tables
    }
    missing = sorted(required - existing)
    if missing:
        formatted = ", ".join(f"{schema}.{table}" for schema, table in missing)
        fail("required tables are missing from PostgreSQL: " + formatted)

    print(f"OK: found {len(required)} required PostgreSQL tables")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Verify PostgreSQL schema metadata.")
    parser.add_argument("--env-file", type=Path, default=DEFAULT_ENV_FILE)
    parser.add_argument("--migration", type=Path, default=DEFAULT_MIGRATION)
    parser.add_argument("--connect-timeout", type=int, default=5)
    parser.add_argument(
        "--migration-only",
        action="store_true",
        help="inspect the migration file without connecting to PostgreSQL",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    load_env_file(args.env_file)
    inspect_migration(args.migration)

    if args.migration_only:
        print("OK: migration-only verification completed")
        return

    driver_name, driver = import_postgres_driver()
    config = postgres_config(args.connect_timeout)
    print(
        "Connecting to PostgreSQL "
        f"{config['user']}@{config['host']}:{config['port']}/{config['dbname']} "
        f"with {driver_name}"
    )
    connection = connect(driver_name, driver, config)
    try:
        verify_required_tables(connection)
    finally:
        connection.close()


if __name__ == "__main__":
    main()
