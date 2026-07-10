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
        "quarantine_decisions",
        "reconciliation_results",
        "reconciliation_check_results",
    ),
    "alpha": (
        "alpha_registry",
        "alpha_runs",
        "alpha_values",
    ),
    "signals": (
        "signal_runs",
        "signals",
        "signal_components",
    ),
    "execution": (
        "order_intents",
        "orders",
        "fills",
        "positions",
        "account_snapshots",
        "risk_decisions",
        "position_snapshots",
        "funding_payments",
        "cash_ledger_entries",
    ),
    "backtesting": (
        "backtest_runs",
        "backtest_metrics",
        "equity_curves",
        "stress_results",
        "backtest_events",
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
        "data_type",
        "provider_instrument_id",
        "instrument_type",
    ),
    ("market_data", "validated_bars"): (
        "bar_id",
        "symbol",
        "exchange",
        "timeframe",
        "bar_open_time_utc",
        "bar_close_time_utc",
        "is_final",
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
        "provider_name",
        "provider_instrument_id",
        "instrument_type",
        "symbol",
        "exchange",
        "traded_at_utc",
        "price",
        "quantity",
        "quote_quantity",
        "side",
        "provider_sequence",
        "record_sha256",
        "validation_status",
        "validation_report_id",
        "source_observation_ids",
    ),
    ("market_data", "funding_rates"): (
        "funding_rate_id",
        "provider_name",
        "provider_instrument_id",
        "instrument_type",
        "settlement_asset",
        "symbol",
        "exchange",
        "funding_time_utc",
        "rate",
        "record_sha256",
        "validation_status",
        "validation_report_id",
        "source_observation_ids",
    ),
    ("market_data", "instruments"): (
        "instrument_id",
        "provider_name",
        "provider_instrument_id",
        "symbol",
        "canonical_display_symbol",
        "exchange",
        "base_asset",
        "quote_asset",
        "settlement_asset",
        "instrument_type",
        "contract_type",
        "margin_type",
        "status",
        "tick_size",
        "quantity_step",
        "minimum_quantity",
        "minimum_notional",
        "contract_value",
        "contract_multiplier",
        "margin_asset",
        "listing_at_utc",
        "expiry_at_utc",
        "funding_interval",
        "metadata_sha256",
        "validation_status",
        "validation_report_id",
        "source_observation_ids",
        "provenance_jsonb",
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
    ("data_quality", "quarantine_decisions"): (
        "quarantine_id",
        "validation_report_id",
        "validation_run_id",
        "observation_id",
        "quarantine_reason",
        "symbol",
        "exchange",
        "timeframe",
        "source_sha256",
        "details_jsonb",
        "created_at_utc",
    ),
    ("data_quality", "reconciliation_results"): (
        "reconciliation_id",
        "validation_run_id",
        "data_type",
        "symbol",
        "timeframe",
        "provider_names",
        "window_start_utc",
        "window_end_utc",
        "status",
        "config_sha256",
        "dataset_sha256",
        "result_sha256",
        "metrics_jsonb",
        "created_at_utc",
    ),
    ("data_quality", "reconciliation_check_results"): (
        "result_id",
        "reconciliation_id",
        "validation_run_id",
        "check_id",
        "check_type",
        "status",
        "severity",
        "affected_observation_ids",
        "details_jsonb",
        "created_at_utc",
    ),
    ("alpha", "alpha_registry"): (
        "alpha_id",
        "alpha_name",
        "alpha_version",
        "description",
        "category",
        "required_data_types",
        "required_fields",
        "parameter_schema_jsonb",
        "default_parameters_jsonb",
        "minimum_warmup",
        "output_semantics",
        "horizon",
        "public_example",
        "status",
        "implementation_sha256",
        "formula_sha256",
        "implementation_code_sha256",
        "repository_commit_sha",
        "content_sha256",
    ),
    ("alpha", "alpha_runs"): (
        "alpha_run_id",
        "alpha_id",
        "alpha_name",
        "alpha_version",
        "symbol_set",
        "series_identity_sha256_set",
        "window_start_utc",
        "window_end_utc",
        "dataset_refs",
        "input_data_sha256",
        "config_sha256",
        "implementation_sha256",
        "formula_sha256",
        "implementation_code_sha256",
        "repository_commit_sha",
        "content_sha256",
        "status",
        "output_count",
        "rejected_count",
        "skipped_count",
    ),
    ("alpha", "alpha_values"): (
        "alpha_value_id",
        "alpha_run_id",
        "alpha_id",
        "alpha_name",
        "alpha_version",
        "symbol",
        "provider_name",
        "exchange",
        "provider_instrument_id",
        "canonical_symbol",
        "instrument_type",
        "timeframe",
        "settlement_asset",
        "series_identity_sha256",
        "timestamp_utc",
        "as_of_utc",
        "lookback_start_utc",
        "lookback_end_utc",
        "raw_score",
        "warmup_complete",
        "valid",
        "evaluation_status",
        "reason_code",
        "reason_message",
        "horizon",
        "source_observation_ids",
        "dataset_sha256",
        "eligible_input_sha256",
        "config_sha256",
        "implementation_sha256",
        "formula_sha256",
        "implementation_code_sha256",
        "repository_commit_sha",
        "record_sha256",
        "content_sha256",
        "provenance_jsonb",
    ),
    ("signals", "signal_runs"): (
        "signal_run_id",
        "run_id",
        "dataset_ref",
        "alpha_run_ids",
        "symbol_universe",
        "series_identity_sha256_set",
        "window_start_utc",
        "window_end_utc",
        "ranking_config_jsonb",
        "threshold_config_jsonb",
        "combination_config_jsonb",
        "config_sha256",
        "code_sha256",
        "data_sha256",
        "formula_sha256",
        "implementation_code_sha256",
        "repository_commit_sha",
        "overlap_policy",
        "overlap_resolution_reason",
        "status",
        "output_count",
        "long_count",
        "short_count",
        "flat_count",
        "skipped_count",
        "failure_count",
        "content_sha256",
    ),
    ("signals", "signals"): (
        "signal_id",
        "signal_run_id",
        "alpha_id",
        "alpha_ids_versions",
        "alpha_run_ids",
        "symbol",
        "provider_name",
        "exchange",
        "provider_instrument_id",
        "canonical_symbol",
        "instrument_type",
        "timeframe",
        "settlement_asset",
        "series_identity_sha256",
        "timestamp_utc",
        "direction",
        "score",
        "raw_score",
        "normalized_score",
        "rank",
        "percentile",
        "confidence",
        "horizon",
        "source_alpha_value_ids",
        "config_sha256",
        "data_sha256",
        "code_sha256",
        "formula_sha256",
        "implementation_code_sha256",
        "repository_commit_sha",
        "overlap_policy",
        "resolution_reason",
        "record_sha256",
        "content_sha256",
    ),
    ("signals", "signal_components"): (
        "signal_component_id",
        "signal_id",
        "alpha_value_id",
        "alpha_id",
        "raw_value",
        "normalized_value",
        "configured_weight",
        "effective_weight",
        "signed_contribution",
        "component_disposition",
        "resolution_reason",
        "component_sha256",
        "public_metadata_jsonb",
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
    ("market_data", "validated_bars", "idx_validated_bars_close_availability"),
    ("market_data", "validated_trades", "idx_validated_trades_symbol_time"),
    ("market_data", "funding_rates", "idx_funding_rates_symbol_time"),
    ("market_data", "validated_trades", "idx_validated_trades_provider_instrument_time"),
    ("market_data", "funding_rates", "idx_funding_rates_provider_instrument_time"),
    ("market_data", "instruments", "idx_instruments_provider_identity"),
    ("market_data", "instruments", "idx_instruments_canonical_type"),
    ("data_quality", "data_quality_checks", "idx_data_quality_checks_validation_run"),
    ("data_quality", "validation_reports", "idx_validation_reports_validation_run"),
    ("data_quality", "quarantine_decisions", "idx_quarantine_decisions_validation_report"),
    ("data_quality", "quarantine_decisions", "idx_quarantine_decisions_validation_run"),
    ("data_quality", "quarantine_decisions", "idx_quarantine_decisions_observation"),
    ("data_quality", "quarantine_decisions", "idx_quarantine_decisions_reason"),
    ("data_quality", "reconciliation_results", "idx_reconciliation_results_validation_run"),
    ("data_quality", "reconciliation_results", "idx_reconciliation_results_symbol_window"),
    ("data_quality", "reconciliation_results", "idx_reconciliation_results_status"),
    ("data_quality", "reconciliation_results", "idx_reconciliation_results_providers"),
    ("data_quality", "reconciliation_check_results", "idx_reconciliation_check_results_reconciliation"),
    ("data_quality", "reconciliation_check_results", "idx_reconciliation_check_results_validation_run"),
    ("data_quality", "reconciliation_check_results", "idx_reconciliation_check_results_check_type"),
    ("data_quality", "reconciliation_check_results", "idx_reconciliation_check_results_status"),
    ("alpha", "alpha_registry", "idx_alpha_registry_category_status"),
    ("alpha", "alpha_runs", "idx_alpha_runs_alpha_window"),
    ("alpha", "alpha_values", "idx_alpha_values_run_symbol_time"),
    ("alpha", "alpha_values", "idx_alpha_values_symbol_time"),
    ("alpha", "alpha_values", "idx_alpha_values_series_time"),
    ("signals", "signal_runs", "idx_signal_runs_run_id"),
    ("signals", "signal_runs", "idx_signal_runs_window"),
    ("signals", "signals", "idx_signals_timestamp_alpha"),
    ("signals", "signals", "idx_signals_run_symbol_time"),
    ("signals", "signals", "idx_signals_series_time"),
    ("signals", "signal_components", "idx_signal_components_signal"),
    ("signals", "signal_components", "idx_signal_components_alpha_value"),
    ("execution", "order_intents", "idx_order_intents_run_id"),
    ("execution", "orders", "idx_orders_run_status"),
    ("execution", "orders", "idx_orders_broker_order_ref"),
    ("execution", "fills", "idx_fills_order_id"),
    ("execution", "fills", "idx_fills_broker_fill_ref"),
    ("execution", "order_intents", "uq_phase5_order_intents_signal_series_time"),
    ("execution", "orders", "uq_phase5_orders_intent"),
    ("execution", "fills", "uq_phase5_fills_order"),
    ("execution", "positions", "uq_phase5_positions_series"),
    ("execution", "risk_decisions", "uq_phase5_risk_logical"),
    ("execution", "funding_payments", "uq_phase5_funding_payment_logical"),
    ("backtesting", "backtest_events", "idx_phase5_backtest_events_order"),
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
    (
        "market_data",
        "instruments",
        ("provider_name", "provider_instrument_id", "instrument_type", "metadata_sha256"),
    ),
    ("market_data", "validated_bars", ("symbol", "exchange", "timeframe", "bar_open_time_utc")),
    (
        "market_data",
        "validated_trades",
        ("provider_name", "provider_instrument_id", "provider_trade_id"),
    ),
    (
        "market_data",
        "funding_rates",
        ("provider_name", "provider_instrument_id", "instrument_type", "funding_time_utc"),
    ),
    ("data_quality", "validation_reports", ("validation_run_id", "dataset_ref")),
    (
        "data_quality",
        "reconciliation_results",
        (
            "validation_run_id",
            "data_type",
            "symbol",
            "timeframe",
            "config_sha256",
            "dataset_sha256",
        ),
    ),
    (
        "data_quality",
        "reconciliation_check_results",
        ("reconciliation_id", "check_id"),
    ),
    ("alpha", "alpha_registry", ("alpha_name", "alpha_version")),
    ("alpha", "alpha_values", ("alpha_run_id", "series_identity_sha256", "timestamp_utc", "horizon")),
    ("signals", "signals", ("signal_run_id", "series_identity_sha256", "timestamp_utc", "horizon")),
    ("signals", "signal_components", ("signal_id", "alpha_value_id")),
    ("execution", "account_snapshots", ("run_id", "account_ref", "snapshot_at_utc")),
    ("backtesting", "backtest_metrics", ("backtest_run_id", "metric_name")),
    ("backtesting", "equity_curves", ("backtest_run_id", "timestamp_utc")),
    ("backtesting", "stress_results", ("backtest_run_id", "scenario_name", "metric_name")),
)

REQUIRED_FOREIGN_KEYS = (
    (
        "market_data",
        "validated_bars",
        ("validation_report_id",),
        "data_quality",
        "validation_reports",
        ("validation_report_id",),
    ),
    (
        "market_data",
        "validated_trades",
        ("validation_report_id",),
        "data_quality",
        "validation_reports",
        ("validation_report_id",),
    ),
    (
        "market_data",
        "funding_rates",
        ("validation_report_id",),
        "data_quality",
        "validation_reports",
        ("validation_report_id",),
    ),
    (
        "market_data",
        "instruments",
        ("validation_report_id",),
        "data_quality",
        "validation_reports",
        ("validation_report_id",),
    ),
    (
        "data_quality",
        "quarantine_decisions",
        ("validation_report_id",),
        "data_quality",
        "validation_reports",
        ("validation_report_id",),
    ),
    (
        "alpha",
        "alpha_runs",
        ("alpha_id",),
        "alpha",
        "alpha_registry",
        ("alpha_id",),
    ),
    (
        "alpha",
        "alpha_values",
        ("alpha_run_id",),
        "alpha",
        "alpha_runs",
        ("alpha_run_id",),
    ),
    (
        "alpha",
        "alpha_values",
        ("alpha_id",),
        "alpha",
        "alpha_registry",
        ("alpha_id",),
    ),
    (
        "signals",
        "signals",
        ("signal_run_id",),
        "signals",
        "signal_runs",
        ("signal_run_id",),
    ),
    (
        "signals",
        "signals",
        ("alpha_id",),
        "alpha",
        "alpha_registry",
        ("alpha_id",),
    ),
    (
        "signals", "signal_components", ("signal_id",),
        "signals", "signals", ("signal_id",),
    ),
    (
        "signals", "signal_components", ("alpha_value_id",),
        "alpha", "alpha_values", ("alpha_value_id",),
    ),
    (
        "signals", "signal_components", ("alpha_id",),
        "alpha", "alpha_registry", ("alpha_id",),
    ),
)
REQUIRED_CHECK_CONSTRAINTS = (
    ("alpha", "alpha_registry", "chk_alpha_registry_minimum_warmup", True),
    ("alpha", "alpha_registry", "chk_alpha_registry_implementation_sha256", True),
    ("alpha", "alpha_registry", "chk_alpha_registry_content_sha256", True),
    ("alpha", "alpha_values", "chk_alpha_values_valid_score", True),
    ("market_data", "validated_bars", "chk_validated_bars_close_after_open", True),
    ("alpha", "alpha_registry", "chk_alpha_registry_formula_sha256", True),
    ("alpha", "alpha_registry", "chk_alpha_registry_code_sha256", True),
    ("alpha", "alpha_values", "chk_alpha_values_series_sha256", True),
    ("alpha", "alpha_values", "chk_alpha_values_eligible_sha256", True),
    ("alpha", "alpha_values", "chk_alpha_values_evaluation_status", True),
    ("alpha", "alpha_values", "chk_alpha_values_as_of_timestamp", True),
    ("alpha", "alpha_values", "chk_alpha_values_record_sha256", True),
    ("alpha", "alpha_values", "chk_alpha_values_identity_nonempty", True),
    ("alpha", "alpha_values", "chk_alpha_values_lookback", True),
    ("signals", "signal_runs", "chk_signal_runs_phase4_status", True),
    ("signals", "signal_runs", "chk_signal_runs_phase4_window", True),
    ("signals", "signals", "chk_signals_normalized_score", True),
    ("signals", "signals", "chk_signals_percentile", True),
    ("signals", "signals", "chk_signals_rank", True),
    ("signals", "signal_runs", "chk_signal_runs_formula_sha256", True),
    ("signals", "signal_runs", "chk_signal_runs_code_sha256", True),
    ("signals", "signals", "chk_signals_series_sha256", True),
    ("signals", "signals", "chk_signals_record_sha256", True),
    ("signals", "signals", "chk_signals_identity_nonempty", True),
    ("signals", "signals", "chk_signals_formula_sha256", True),
    ("signals", "signals", "chk_signals_code_sha256", True),
    ("signals", "signal_components", "chk_signal_components_sha256", True),
    ("signals", "signal_components", "chk_signal_components_disposition", True),
    (
        "market_data",
        "instruments",
        "chk_instruments_phase2_types",
        True,
    ),
    (
        "market_data",
        "validated_trades",
        "chk_validated_trades_phase2_identity_required",
        False,
    ),
    (
        "market_data",
        "funding_rates",
        "chk_funding_rates_phase2_identity_required",
        False,
    ),
    (
        "market_data",
        "instruments",
        "chk_instruments_phase2_identity_required",
        False,
    ),
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
ALLOWED_DATA_MIGRATIONS = {
    "0006_phase2_final_hardening.sql": (
        r"\bUPDATE\s+market_data\.instruments\s+SET\s+instrument_type\s*=\s*CASE\b.*?\bWHERE\s+instrument_type\s+IN\s*\(\s*'perpetual'\s*,\s*'future'\s*\)\s*;",
    ),
    "0008_phase3_phase4_audit_repairs.sql": (
        r"\bUPDATE\s+alpha\.alpha_registry\s+SET\b.*?\bWHERE\b.*?;",
        r"\bUPDATE\s+alpha\.alpha_runs\s+SET\b.*?\bWHERE\b.*?;",
        r"\bUPDATE\s+alpha\.alpha_values\s+SET\b.*?\bWHERE\b.*?;",
        r"\bUPDATE\s+signals\.signal_runs\s+SET\b.*?;",
        r"\bUPDATE\s+signals\.signals\s+SET\b.*?\bWHERE\b.*?;",
    ),
}


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
    """Hash canonical LF bytes so migration identity is checkout-platform invariant."""

    return hashlib.sha256(path.read_bytes().replace(b"\r\n", b"\n")).hexdigest()


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
        allowed_updates = ALLOWED_DATA_MIGRATIONS.get(migration.filename, ())
        update_statements = re.findall(
            r"\bUPDATE\b.*?;",
            stripped_sql,
            flags=re.IGNORECASE | re.DOTALL,
        )
        if allowed_updates and (
            len(update_statements) != len(allowed_updates)
            or any(
                re.fullmatch(pattern, statement.strip(), flags=re.IGNORECASE | re.DOTALL) is None
                for pattern, statement in zip(allowed_updates, update_statements)
            )
        ):
            fail(f"{migration.filename} contains an unapproved data migration statement")
        for pattern in UNSAFE_SQL_PATTERNS:
            if pattern == r"\bUPDATE\b" and allowed_updates:
                continue
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


def fetch_check_constraints(
    client: CatalogClient,
) -> set[tuple[str, str, str, bool]]:
    rows = client.query(
        f"""
        SELECT
            namespace.nspname,
            table_class.relname,
            constraint_info.conname,
            CASE WHEN constraint_info.convalidated THEN 'true' ELSE 'false' END
        FROM pg_constraint AS constraint_info
        JOIN pg_class AS table_class
            ON table_class.oid = constraint_info.conrelid
        JOIN pg_namespace AS namespace
            ON namespace.oid = table_class.relnamespace
        WHERE constraint_info.contype = 'c'
          AND namespace.nspname IN ({sql_string_list(REQUIRED_TABLES)})
        """
    )
    return {
        (
            str(row[0]),
            str(row[1]),
            str(row[2]),
            str(row[3]).lower() == "true",
        )
        for row in rows
    }


def verify_check_constraints(client: CatalogClient) -> None:
    existing = fetch_check_constraints(client)
    missing = sorted(set(REQUIRED_CHECK_CONSTRAINTS) - existing)
    if missing:
        formatted = ", ".join(
            f"{schema}.{table}.{name} (validated={validated})"
            for schema, table, name, validated in missing
        )
        fail("required check constraints are missing from PostgreSQL: " + formatted)
    print(f"OK: found {len(REQUIRED_CHECK_CONSTRAINTS)} required PostgreSQL check constraints")

def fetch_foreign_keys(client: CatalogClient) -> set[
    tuple[str, str, tuple[str, ...], str, str, tuple[str, ...]]
]:
    rows = client.query(
        f"""
        SELECT
            source_ns.nspname,
            source_table.relname,
            string_agg(source_column.attname, ',' ORDER BY source_keys.ordinality),
            target_ns.nspname,
            target_table.relname,
            string_agg(target_column.attname, ',' ORDER BY source_keys.ordinality)
        FROM pg_constraint AS constraint_info
        JOIN pg_class AS source_table
            ON source_table.oid = constraint_info.conrelid
        JOIN pg_namespace AS source_ns
            ON source_ns.oid = source_table.relnamespace
        JOIN pg_class AS target_table
            ON target_table.oid = constraint_info.confrelid
        JOIN pg_namespace AS target_ns
            ON target_ns.oid = target_table.relnamespace
        JOIN unnest(constraint_info.conkey) WITH ORDINALITY
            AS source_keys(attnum, ordinality) ON TRUE
        JOIN unnest(constraint_info.confkey) WITH ORDINALITY
            AS target_keys(attnum, ordinality)
            ON target_keys.ordinality = source_keys.ordinality
        JOIN pg_attribute AS source_column
            ON source_column.attrelid = source_table.oid
           AND source_column.attnum = source_keys.attnum
        JOIN pg_attribute AS target_column
            ON target_column.attrelid = target_table.oid
           AND target_column.attnum = target_keys.attnum
        WHERE constraint_info.contype = 'f'
          AND source_ns.nspname IN ({sql_string_list(REQUIRED_TABLES)})
        GROUP BY
            source_ns.nspname,
            source_table.relname,
            target_ns.nspname,
            target_table.relname,
            constraint_info.conname
        """
    )
    return {
        (
            str(row[0]),
            str(row[1]),
            tuple(str(row[2]).split(",")),
            str(row[3]),
            str(row[4]),
            tuple(str(row[5]).split(",")),
        )
        for row in rows
    }


def verify_foreign_keys(client: CatalogClient) -> None:
    existing = fetch_foreign_keys(client)
    missing = sorted(set(REQUIRED_FOREIGN_KEYS) - existing)
    if missing:
        formatted = ", ".join(
            f"{source_schema}.{source_table}({', '.join(source_columns)}) -> "
            f"{target_schema}.{target_table}({', '.join(target_columns)})"
            for (
                source_schema,
                source_table,
                source_columns,
                target_schema,
                target_table,
                target_columns,
            ) in missing
        )
        fail("required foreign keys are missing from PostgreSQL: " + formatted)
    print(f"OK: found {len(REQUIRED_FOREIGN_KEYS)} required PostgreSQL foreign keys")

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
        verify_check_constraints(client)
        verify_foreign_keys(client)
        verify_migration_records(client, migrations)
    finally:
        client.close()


if __name__ == "__main__":
    main()
