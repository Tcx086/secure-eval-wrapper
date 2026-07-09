"""Verify the local PostgreSQL schema foundation.

The script reads connection settings from environment variables, optionally loading a local `.env`
file first. It performs metadata-only catalog checks and never inserts sample data.
"""

from __future__ import annotations

import argparse
import hashlib
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Protocol


REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_ENV_FILE = REPO_ROOT / ".env"
DEFAULT_MIGRATIONS_DIR = REPO_ROOT / "open-core" / "db" / "migrations"

REQUIRED_SCHEMAS = (
    "audit",
    "market_data",
    "data_quality",
    "alpha",
    "signals",
    "execution",
    "backtesting",
    "monitoring",
)

REQUIRED_TABLES: dict[str, tuple[str, ...]] = {
    "audit": (
        "run_manifests",
        "artifacts",
        "schema_migrations",
    ),
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
}

REQUIRED_COLUMNS: dict[tuple[str, str], tuple[str, ...]] = {
    ("audit", "schema_migrations"): (
        "migration_id",
        "filename",
        "sha256",
        "applied_at_utc",
        "description",
    ),
    ("audit", "run_manifests"): (
        "run_id",
        "run_mode",
        "data_sha256",
        "config_sha256",
        "code_sha256",
        "artifact_sha256",
        "seed",
        "storage_ref",
        "manifest_jsonb",
        "created_at_utc",
    ),
    ("audit", "artifacts"): (
        "artifact_id",
        "run_id",
        "artifact_type",
        "classification",
        "path_uri",
        "artifact_sha256",
        "redaction_status",
        "metadata_jsonb",
        "created_at_utc",
    ),
    ("market_data", "raw_source_observations"): (
        "observation_id",
        "source_provider",
        "source_exchange",
        "source_endpoint",
        "symbol_raw",
        "symbol_normalized",
        "timeframe",
        "observed_at_utc",
        "ingested_at_utc",
        "payload_jsonb",
        "source_sha256",
        "collection_run_id",
    ),
    ("market_data", "validated_bars"): (
        "bar_id",
        "symbol",
        "exchange",
        "timeframe",
        "bar_open_time_utc",
        "open",
        "high",
        "low",
        "close",
        "volume",
        "validation_status",
        "validation_report_id",
        "source_observation_ids",
    ),
    ("market_data", "validated_trades"): (
        "trade_id",
        "provider_trade_id",
        "symbol",
        "exchange",
        "traded_at_utc",
        "price",
        "quantity",
        "side",
        "validation_status",
        "validation_report_id",
        "source_observation_ids",
    ),
    ("market_data", "funding_rates"): (
        "funding_rate_id",
        "symbol",
        "exchange",
        "funding_time_utc",
        "rate",
        "validation_status",
        "validation_report_id",
        "source_observation_ids",
    ),
    ("market_data", "instruments"): (
        "instrument_id",
        "symbol",
        "exchange",
        "base_asset",
        "quote_asset",
        "instrument_type",
        "status",
    ),
    ("data_quality", "data_quality_checks"): (
        "check_id",
        "validation_run_id",
        "check_type",
        "severity",
        "status",
        "details_jsonb",
    ),
    ("data_quality", "validation_reports"): (
        "validation_report_id",
        "validation_run_id",
        "dataset_ref",
        "accepted_count",
        "rejected_count",
        "warning_count",
        "status",
        "report_sha256",
    ),
    ("alpha", "alpha_registry"): (
        "alpha_id",
        "alpha_name",
        "description",
        "public_example",
        "status",
    ),
    ("signals", "signal_runs"): (
        "signal_run_id",
        "run_id",
        "dataset_ref",
        "config_sha256",
        "code_sha256",
        "seed",
        "status",
    ),
    ("signals", "signals"): (
        "signal_id",
        "signal_run_id",
        "alpha_id",
        "symbol",
        "timestamp_utc",
        "direction",
        "score",
        "confidence",
        "horizon",
    ),
    ("execution", "order_intents"): (
        "order_intent_id",
        "signal_id",
        "run_id",
        "symbol",
        "side",
        "order_type",
        "quantity",
        "intent_status",
    ),
    ("execution", "orders"): (
        "order_id",
        "order_intent_id",
        "broker_order_ref",
        "run_id",
        "symbol",
        "side",
        "order_type",
        "order_status",
    ),
    ("execution", "fills"): (
        "fill_id",
        "order_id",
        "broker_fill_ref",
        "symbol",
        "side",
        "filled_at_utc",
        "price",
        "quantity",
        "fee_amount",
    ),
    ("execution", "positions"): (
        "position_id",
        "run_id",
        "account_ref",
        "symbol",
        "quantity",
        "average_entry_price",
    ),
    ("execution", "account_snapshots"): (
        "account_snapshot_id",
        "run_id",
        "account_ref",
        "snapshot_at_utc",
        "equity",
        "cash",
        "classification",
    ),
    ("backtesting", "backtest_runs"): (
        "backtest_run_id",
        "run_id",
        "signal_run_id",
        "execution_model_sha256",
        "config_sha256",
        "status",
    ),
    ("backtesting", "backtest_metrics"): (
        "backtest_metric_id",
        "backtest_run_id",
        "metric_name",
        "metric_value",
        "metric_unit",
    ),
    ("backtesting", "equity_curves"): (
        "equity_curve_id",
        "backtest_run_id",
        "timestamp_utc",
        "equity",
        "cash",
        "drawdown",
        "exposure",
    ),
    ("backtesting", "stress_results"): (
        "stress_result_id",
        "backtest_run_id",
        "scenario_name",
        "metric_name",
        "metric_value",
    ),
    ("monitoring", "monitoring_events"): (
        "monitoring_event_id",
        "run_id",
        "event_category",
        "severity",
        "event_time_utc",
        "message",
    ),
    ("monitoring", "fix_session_events"): (
        "fix_session_event_id",
        "run_id",
        "session_id",
        "event_type",
        "sequence_number",
        "simulated",
    ),
    ("monitoring", "risk_events"): (
        "risk_event_id",
        "run_id",
        "event_time_utc",
        "risk_type",
        "severity",
    ),
}

REQUIRED_INDEXES = (
    ("audit", "schema_migrations", "idx_schema_migrations_sha256"),
    ("audit", "schema_migrations", "idx_schema_migrations_applied_at"),
    ("market_data", "raw_source_observations", "idx_raw_source_observations_provider_time"),
    ("market_data", "raw_source_observations", "idx_raw_source_observations_collection_run"),
    ("market_data", "raw_source_observations", "idx_raw_source_observations_source_sha256"),
    ("market_data", "validated_bars", "idx_validated_bars_symbol_time"),
    ("market_data", "validated_trades", "idx_validated_trades_symbol_time"),
    ("market_data", "funding_rates", "idx_funding_rates_symbol_time"),
    ("data_quality", "data_quality_checks", "idx_data_quality_checks_validation_run"),
    ("data_quality", "validation_reports", "idx_validation_reports_validation_run"),
    ("signals", "signal_runs", "idx_signal_runs_run_id"),
    ("signals", "signals", "idx_signals_run_symbol_time"),
    ("execution", "order_intents", "idx_order_intents_run_id"),
    ("execution", "orders", "idx_orders_run_status"),
    ("execution", "orders", "idx_orders_broker_order_ref"),
    ("execution", "fills", "idx_fills_order_id"),
    ("execution", "fills", "idx_fills_broker_fill_ref"),
    ("backtesting", "backtest_runs", "idx_backtest_runs_run_id"),
    ("backtesting", "equity_curves", "idx_equity_curves_run_time"),
    ("monitoring", "monitoring_events", "idx_monitoring_events_run_time"),
    ("monitoring", "fix_session_events", "idx_fix_session_events_session_time"),
    ("monitoring", "risk_events", "idx_risk_events_run_time"),
    ("audit", "artifacts", "idx_artifacts_run_id"),
    ("audit", "artifacts", "idx_artifacts_classification"),
)

REQUIRED_UNIQUE_CONSTRAINTS = (
    ("audit", "schema_migrations", ("filename",)),
    ("market_data", "instruments", ("symbol", "exchange")),
    ("market_data", "validated_bars", ("symbol", "exchange", "timeframe", "bar_open_time_utc")),
    ("market_data", "funding_rates", ("symbol", "exchange", "funding_time_utc")),
    ("data_quality", "validation_reports", ("validation_run_id", "dataset_ref")),
    ("alpha", "alpha_registry", ("alpha_name",)),
    ("execution", "positions", ("run_id", "account_ref", "symbol")),
    ("execution", "account_snapshots", ("run_id", "account_ref", "snapshot_at_utc")),
    ("backtesting", "backtest_metrics", ("backtest_run_id", "metric_name")),
    ("backtesting", "equity_curves", ("backtest_run_id", "timestamp_utc")),
    ("backtesting", "stress_results", ("backtest_run_id", "scenario_name", "metric_name")),
)

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


class CatalogClient(Protocol):
    def query(self, sql: str) -> list[tuple[object, ...]]:
        """Return rows for a metadata-only catalog query."""

    def close(self) -> None:
        """Release any catalog query resources."""


class DbApiCatalogClient:
    def __init__(self, connection) -> None:
        self.connection = connection

    def query(self, sql: str) -> list[tuple[object, ...]]:
        with self.connection.cursor() as cursor:
            cursor.execute(sql)
            return [tuple(row) for row in cursor.fetchall()]

    def close(self) -> None:
        self.connection.close()


class DockerPsqlCatalogClient:
    def __init__(self, *, container: str, user: str, database: str) -> None:
        self.container = container
        self.user = user
        self.database = database

    def query(self, sql: str) -> list[tuple[object, ...]]:
        completed = subprocess.run(
            [
                "docker",
                "exec",
                "-i",
                self.container,
                "psql",
                f"--username={self.user}",
                f"--dbname={self.database}",
                "--no-align",
                "--tuples-only",
                "--field-separator=\t",
                "--set=ON_ERROR_STOP=1",
                "--command",
                sql,
            ],
            check=False,
            capture_output=True,
            text=True,
        )
        if completed.returncode != 0:
            fail("docker psql catalog query failed: " + completed.stderr.strip())
        return [tuple(line.split("\t")) for line in completed.stdout.splitlines() if line]

    def close(self) -> None:
        return None


def sql_literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def sql_string_list(values: Iterable[str]) -> str:
    return ", ".join(sql_literal(value) for value in values)

@dataclass(frozen=True)
class MigrationFile:
    migration_id: str
    filename: str
    path: Path
    sha256: str


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


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def discover_migrations(migrations_dir: Path) -> list[MigrationFile]:
    if not migrations_dir.exists():
        fail(f"migrations directory not found: {migrations_dir}")

    migrations = [
        MigrationFile(
            migration_id=path.stem,
            filename=path.name,
            path=path,
            sha256=sha256_file(path),
        )
        for path in sorted(migrations_dir.glob("*.sql"))
    ]
    if not migrations:
        fail(f"no SQL migrations found in: {migrations_dir}")
    return migrations


def inspect_migrations(migrations: list[MigrationFile]) -> None:
    combined_sql = ""
    for migration in migrations:
        sql = migration.path.read_text(encoding="utf-8")
        stripped_sql = strip_sql_comments(sql)
        for pattern in UNSAFE_SQL_PATTERNS:
            if re.search(pattern, stripped_sql, flags=re.IGNORECASE):
                fail(
                    f"{migration.filename} contains unsafe statement matching pattern: {pattern}"
                )
        combined_sql += "\n" + stripped_sql

    missing_schemas = [
        schema
        for schema in REQUIRED_SCHEMAS
        if not re.search(
            r"\bCREATE\s+SCHEMA\s+IF\s+NOT\s+EXISTS\s+" + re.escape(schema) + r"\b",
            combined_sql,
            flags=re.IGNORECASE,
        )
    ]
    if missing_schemas:
        fail("migrations are missing required schema definitions: " + ", ".join(missing_schemas))

    missing_tables: list[str] = []
    for schema, tables in REQUIRED_TABLES.items():
        for table in tables:
            create_table_pattern = (
                r"\bCREATE\s+TABLE\s+IF\s+NOT\s+EXISTS\s+"
                + re.escape(f"{schema}.{table}")
                + r"\b"
            )
            if not re.search(create_table_pattern, combined_sql, flags=re.IGNORECASE):
                missing_tables.append(f"{schema}.{table}")

    if missing_tables:
        fail("migrations are missing required table definitions: " + ", ".join(missing_tables))

    print(f"OK: inspected {len(migrations)} migration file(s) safely")
    for migration in migrations:
        print(f"OK: {migration.filename} sha256={migration.sha256}")


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
    require_env(("POSTGRES_HOST", "POSTGRES_PORT", "POSTGRES_DB", "POSTGRES_USER", "POSTGRES_PASSWORD"))
    return {
        "host": os.environ["POSTGRES_HOST"],
        "port": int(os.environ["POSTGRES_PORT"]),
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


def fetch_existing_schemas(client: CatalogClient) -> set[str]:
    rows = client.query(
        f"""
        SELECT schema_name
        FROM information_schema.schemata
        WHERE schema_name IN ({sql_string_list(REQUIRED_SCHEMAS)})
        """
    )
    return {str(row[0]) for row in rows}


def fetch_existing_tables(client: CatalogClient) -> set[tuple[str, str]]:
    rows = client.query(
        f"""
        SELECT table_schema, table_name
        FROM information_schema.tables
        WHERE table_type = 'BASE TABLE'
          AND table_schema IN ({sql_string_list(REQUIRED_TABLES)})
        """
    )
    return {(str(row[0]), str(row[1])) for row in rows}


def fetch_existing_columns(client: CatalogClient) -> set[tuple[str, str, str]]:
    rows = client.query(
        f"""
        SELECT table_schema, table_name, column_name
        FROM information_schema.columns
        WHERE table_schema IN ({sql_string_list(REQUIRED_TABLES)})
        """
    )
    return {(str(row[0]), str(row[1]), str(row[2])) for row in rows}


def fetch_existing_indexes(client: CatalogClient) -> set[tuple[str, str, str]]:
    rows = client.query(
        f"""
        SELECT schemaname, tablename, indexname
        FROM pg_indexes
        WHERE schemaname IN ({sql_string_list(REQUIRED_TABLES)})
        """
    )
    return {(str(row[0]), str(row[1]), str(row[2])) for row in rows}


def fetch_unique_constraints(client: CatalogClient) -> set[tuple[str, str, tuple[str, ...]]]:
    rows = client.query(
        f"""
        SELECT
            namespace.nspname AS schema_name,
            table_class.relname AS table_name,
            string_agg(attribute.attname, ',' ORDER BY columns.ordinality) AS column_names
        FROM pg_constraint AS constraint_info
        JOIN pg_class AS table_class
            ON table_class.oid = constraint_info.conrelid
        JOIN pg_namespace AS namespace
            ON namespace.oid = table_class.relnamespace
        JOIN unnest(constraint_info.conkey) WITH ORDINALITY AS columns(attnum, ordinality)
            ON TRUE
        JOIN pg_attribute AS attribute
            ON attribute.attrelid = table_class.oid
           AND attribute.attnum = columns.attnum
        WHERE constraint_info.contype = 'u'
          AND namespace.nspname IN ({sql_string_list(REQUIRED_TABLES)})
        GROUP BY namespace.nspname, table_class.relname, constraint_info.conname
        """
    )
    return {
        (str(row[0]), str(row[1]), tuple(str(row[2]).split(",")))
        for row in rows
    }


def verify_required_schemas(client: CatalogClient) -> None:
    existing = fetch_existing_schemas(client)
    missing = sorted(set(REQUIRED_SCHEMAS) - existing)
    if missing:
        fail("required schemas are missing from PostgreSQL: " + ", ".join(missing))

    print(f"OK: found {len(REQUIRED_SCHEMAS)} required PostgreSQL schemas")


def verify_required_tables(client: CatalogClient) -> None:
    existing = fetch_existing_tables(client)
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


def verify_required_columns(client: CatalogClient) -> None:
    existing = fetch_existing_columns(client)
    required = {
        (schema, table, column)
        for (schema, table), columns in REQUIRED_COLUMNS.items()
        for column in columns
    }
    missing = sorted(required - existing)
    if missing:
        formatted = ", ".join(f"{schema}.{table}.{column}" for schema, table, column in missing)
        fail("required columns are missing from PostgreSQL: " + formatted)

    print(f"OK: found {len(required)} required PostgreSQL columns")


def verify_required_indexes(client: CatalogClient) -> None:
    existing = fetch_existing_indexes(client)
    missing = sorted(set(REQUIRED_INDEXES) - existing)
    if missing:
        formatted = ", ".join(f"{schema}.{table}.{index}" for schema, table, index in missing)
        fail("required indexes are missing from PostgreSQL: " + formatted)

    print(f"OK: found {len(REQUIRED_INDEXES)} required PostgreSQL indexes")


def verify_unique_constraints(client: CatalogClient) -> None:
    existing = fetch_unique_constraints(client)
    missing = sorted(set(REQUIRED_UNIQUE_CONSTRAINTS) - existing)
    if missing:
        formatted = ", ".join(
            f"{schema}.{table}({', '.join(columns)})"
            for schema, table, columns in missing
        )
        fail("required unique constraints are missing from PostgreSQL: " + formatted)

    print(f"OK: found {len(REQUIRED_UNIQUE_CONSTRAINTS)} required PostgreSQL unique constraints")


def verify_migration_records(client: CatalogClient, migrations: list[MigrationFile]) -> None:
    rows = client.query(
        """
        SELECT migration_id, filename, sha256, description
        FROM audit.schema_migrations
        """
    )
    records = {
        str(row[0]): {"filename": str(row[1]), "sha256": str(row[2]), "description": str(row[3])}
        for row in rows
    }

    missing: list[str] = []
    mismatched: list[str] = []
    for migration in migrations:
        record = records.get(migration.migration_id)
        if record is None:
            missing.append(migration.migration_id)
            continue
        if record["filename"] != migration.filename or record["sha256"] != migration.sha256:
            mismatched.append(migration.migration_id)
        if not record["description"]:
            mismatched.append(migration.migration_id + " (empty description)")

    if missing:
        fail("schema migration metadata rows are missing: " + ", ".join(missing))
    if mismatched:
        fail("schema migration metadata rows do not match local files: " + ", ".join(mismatched))

    print(f"OK: migration metadata matches {len(migrations)} local migration file(s)")


def build_catalog_client(config: dict[str, object], docker_container: str | None) -> CatalogClient:
    if docker_container:
        print(
            "Connecting to PostgreSQL "
            f"{config['user']}@{docker_container}/{config['dbname']} with docker psql"
        )
        return DockerPsqlCatalogClient(
            container=docker_container,
            user=str(config["user"]),
            database=str(config["dbname"]),
        )

    driver_name, driver = import_postgres_driver()
    print(
        "Connecting to PostgreSQL "
        f"{config['user']}@{config['host']}:{config['port']}/{config['dbname']} "
        f"with {driver_name}"
    )
    return DbApiCatalogClient(connect(driver_name, driver, config))

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Verify PostgreSQL schema metadata.")
    parser.add_argument("--env-file", type=Path, default=DEFAULT_ENV_FILE)
    parser.add_argument("--migrations-dir", type=Path, default=DEFAULT_MIGRATIONS_DIR)
    parser.add_argument("--connect-timeout", type=int, default=5)
    parser.add_argument("--docker-container", default=os.environ.get("POSTGRES_VERIFY_DOCKER_CONTAINER"))
    parser.add_argument(
        "--migration-only",
        action="store_true",
        help="inspect migration files without connecting to PostgreSQL",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    load_env_file(args.env_file)
    migrations = discover_migrations(args.migrations_dir)
    inspect_migrations(migrations)

    if args.migration_only:
        print("OK: migration-only verification completed")
        return

    config = postgres_config(args.connect_timeout)
    client = build_catalog_client(config, args.docker_container)
    try:
        verify_required_schemas(client)
        verify_required_tables(client)
        verify_required_columns(client)
        verify_required_indexes(client)
        verify_unique_constraints(client)
        verify_migration_records(client, migrations)
    finally:
        client.close()


if __name__ == "__main__":
    main()
