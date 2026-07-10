"""Offline reconciliation mapping, repository, transaction, and migration tests."""

from __future__ import annotations

import importlib.util
import re
import sys
import unittest
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from uuid import NAMESPACE_URL, UUID, uuid5

from secure_eval_wrapper.data_collection import NormalizedBar
from secure_eval_wrapper.data_validation import (
    persist_reconciliation_result,
    reconcile_ohlcv_sources,
)
from secure_eval_wrapper.storage.postgres.reconciliation_mappers import (
    reconciliation_check_result_to_row,
    reconciliation_result_to_row,
)
from secure_eval_wrapper.storage.postgres.reconciliation_repositories import (
    PostgresReconciliationRepository,
)


VALIDATION_RUN_ID = UUID("60000000-0000-0000-0000-000000000001")
EXISTING_RECONCILIATION_ID = UUID("60000000-0000-0000-0000-000000000099")
FIXED_NOW = datetime(2026, 7, 9, 21, 0, tzinfo=timezone.utc)
WINDOW_START = datetime(2026, 1, 1, tzinfo=timezone.utc)
SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
REPO_ROOT = Path(__file__).resolve().parents[2]


def _bar(provider: str, minute: int) -> NormalizedBar:
    open_time = WINDOW_START + timedelta(minutes=minute)
    return NormalizedBar(
        bar_id=uuid5(NAMESPACE_URL, f"persistence-bar:{provider}:{minute}"),
        symbol="BTC-USDT",
        exchange=provider.upper(),
        timeframe="1m",
        bar_open_time_utc=open_time,
        open=Decimal("100"),
        high=Decimal("102"),
        low=Decimal("99"),
        close=Decimal("101"),
        volume=Decimal("10"),
        source_observation_ids=(
            uuid5(NAMESPACE_URL, f"persistence-observation:{provider}:{minute}"),
        ),
        bar_close_time_utc=open_time + timedelta(seconds=59, milliseconds=999),
        is_final=True,
        provenance={"provider_name": provider},
    )


def _reconciliation():
    return reconcile_ohlcv_sources(
        validation_run_id=VALIDATION_RUN_ID,
        datasets_by_provider={
            "binance": (_bar("binance", 0), _bar("binance", 1)),
            "okx": (_bar("okx", 0), _bar("okx", 1)),
        },
        clock=lambda: FIXED_NOW,
    )


class FakeAtomicRepository:
    def __init__(self, *, fail_child: bool = False) -> None:
        self.fail_child = fail_child
        self.results: list[dict[str, object]] = []
        self.checks: list[dict[str, object]] = []
        self.commits = 0
        self.rollbacks = 0

    @contextmanager
    def transaction(self):
        try:
            yield self
        except Exception:
            self.rollbacks += 1
            raise
        else:
            self.commits += 1

    def record_reconciliation_result(self, row):
        self.results.append(row)
        return EXISTING_RECONCILIATION_ID

    def record_reconciliation_check_result(self, row):
        if self.fail_child:
            raise RuntimeError("synthetic child failure")
        self.checks.append(row)
        return row["result_id"]


class NoConnectConnection:
    def __init__(self) -> None:
        self.cursor_calls = 0

    def cursor(self):
        self.cursor_calls += 1
        raise AssertionError("repository construction touched the connection")


class RecordingCursor:
    def __init__(self, row=None) -> None:
        self.row = row
        self.sql = ""
        self.params = ()
        self.description = None

    def execute(self, sql, params):
        self.sql = sql
        self.params = params

    def fetchone(self):
        return self.row

    def fetchall(self):
        return ()

    def close(self):
        return None


class RecordingConnection:
    def __init__(self, row=None) -> None:
        self.cursor_instance = RecordingCursor(row)
        self.commits = 0
        self.rollbacks = 0

    def cursor(self):
        return self.cursor_instance

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1


class ReconciliationPersistenceTests(unittest.TestCase):
    def test_mapping_preserves_ids_hashes_metrics_and_findings(self) -> None:
        result = _reconciliation()
        row = reconciliation_result_to_row(result)
        check_row = reconciliation_check_result_to_row(
            result.results[0],
            reconciliation_id=result.reconciliation_id,
        )

        self.assertEqual(row["reconciliation_id"], result.reconciliation_id)
        self.assertEqual(row["validation_run_id"], result.validation_run_id)
        self.assertEqual(row["provider_names"], ["binance", "okx"])
        self.assertEqual(row["metrics_jsonb"], dict(result.metrics))
        self.assertEqual(row["config_sha256"], result.config_sha256)
        self.assertEqual(row["dataset_sha256"], result.dataset_sha256)
        self.assertEqual(row["result_sha256"], result.result_sha256)
        self.assertRegex(result.config_sha256, SHA256_PATTERN)
        self.assertRegex(result.dataset_sha256, SHA256_PATTERN)
        self.assertRegex(result.result_sha256, SHA256_PATTERN)
        self.assertEqual(check_row["check_id"], result.results[0].check_id)
        self.assertEqual(
            check_row["affected_observation_ids"],
            list(result.results[0].affected_observation_ids),
        )
        self.assertEqual(
            check_row["details_jsonb"]["findings"],
            result.results[0].details["findings"],
        )

    def test_summary_and_children_persist_atomically_using_database_id(self) -> None:
        repository = FakeAtomicRepository()
        result = _reconciliation()

        summary = persist_reconciliation_result(result, repository=repository)

        self.assertEqual(repository.commits, 1)
        self.assertEqual(repository.rollbacks, 0)
        self.assertEqual(summary.reconciliation_id, EXISTING_RECONCILIATION_ID)
        self.assertEqual(len(repository.checks), len(result.results))
        self.assertTrue(
            all(
                row["reconciliation_id"] == EXISTING_RECONCILIATION_ID
                for row in repository.checks
            )
        )

    def test_child_failure_rolls_back_transaction(self) -> None:
        repository = FakeAtomicRepository(fail_child=True)
        with self.assertRaisesRegex(RuntimeError, "synthetic child failure"):
            persist_reconciliation_result(_reconciliation(), repository=repository)
        self.assertEqual(repository.commits, 0)
        self.assertEqual(repository.rollbacks, 1)

    def test_repository_constructor_performs_no_connection_activity(self) -> None:
        connection = NoConnectConnection()
        PostgresReconciliationRepository(connection)
        self.assertEqual(connection.cursor_calls, 0)

    def test_conflicts_return_actual_database_ids_with_parameterized_sql(self) -> None:
        connection = RecordingConnection((EXISTING_RECONCILIATION_ID,))
        repository = PostgresReconciliationRepository(connection)
        result = _reconciliation()

        returned = repository.record_reconciliation_result(
            reconciliation_result_to_row(result)
        )

        self.assertEqual(returned, EXISTING_RECONCILIATION_ID)
        self.assertIn("ON CONFLICT", connection.cursor_instance.sql)
        self.assertIn("RETURNING reconciliation_id", connection.cursor_instance.sql)
        self.assertIn("%s", connection.cursor_instance.sql)
        self.assertNotIn("BTC-USDT", connection.cursor_instance.sql)
        self.assertIn("BTC-USDT", connection.cursor_instance.params)
        self.assertEqual(connection.commits, 1)

        connection.cursor_instance = RecordingCursor(
            (result.results[0].result_id,)
        )
        returned_check = repository.record_reconciliation_check_result(
            reconciliation_check_result_to_row(
                result.results[0],
                reconciliation_id=EXISTING_RECONCILIATION_ID,
            )
        )
        self.assertEqual(returned_check, result.results[0].result_id)
        self.assertIn("RETURNING result_id", connection.cursor_instance.sql)
        self.assertIn("%s", connection.cursor_instance.sql)

    def test_filtered_list_query_is_parameterized(self) -> None:
        connection = RecordingConnection()
        repository = PostgresReconciliationRepository(connection)
        repository.list_reconciliation_results(
            validation_run_id=VALIDATION_RUN_ID,
            symbol="BTC-USDT",
            status="passed",
        )
        self.assertNotIn("BTC-USDT", connection.cursor_instance.sql)
        self.assertEqual(
            connection.cursor_instance.params,
            (VALIDATION_RUN_ID, "BTC-USDT", "passed"),
        )

    def test_migration_and_verifier_cover_required_catalog_objects(self) -> None:
        verifier_path = REPO_ROOT / "open-core" / "scripts" / "verify_postgres_schema.py"
        spec = importlib.util.spec_from_file_location("phase2_verifier", verifier_path)
        assert spec is not None and spec.loader is not None
        verifier = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = verifier
        spec.loader.exec_module(verifier)

        migrations = verifier.discover_migrations(
            REPO_ROOT / "open-core" / "db" / "migrations"
        )
        verifier.inspect_migrations(migrations)
        self.assertEqual(migrations[-1].filename, "0013_phase6_monitoring_simulated_fix.sql")
        self.assertIn("reconciliation_results", verifier.REQUIRED_TABLES["data_quality"])
        self.assertIn(
            "reconciliation_check_results",
            verifier.REQUIRED_TABLES["data_quality"],
        )
        self.assertIn(
            ("data_quality", "reconciliation_results"),
            verifier.REQUIRED_COLUMNS,
        )
        self.assertIn(
            (
                "market_data",
                "validated_trades",
                "chk_validated_trades_phase2_identity_required",
                False,
            ),
            verifier.REQUIRED_CHECK_CONSTRAINTS,
        )
        self.assertIn(
            (
                "market_data",
                "funding_rates",
                "chk_funding_rates_phase2_identity_required",
                False,
            ),
            verifier.REQUIRED_CHECK_CONSTRAINTS,
        )
        self.assertIn(
            (
                "data_quality",
                "reconciliation_check_results",
                "idx_reconciliation_check_results_reconciliation",
            ),
            verifier.REQUIRED_INDEXES,
        )


if __name__ == "__main__":
    unittest.main()
