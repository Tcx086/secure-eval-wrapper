"""Phase 2D offline persistence tests using public synthetic fixtures and fakes."""

from __future__ import annotations

import unittest
from contextlib import contextmanager
from dataclasses import replace
from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import patch
from uuid import UUID

from secure_eval_wrapper.data_collection import (
    DataRequest,
    MarketDataType,
    SampleProvider,
    normalize_ohlcv_observations,
)
from secure_eval_wrapper.data_validation import (
    INVALID_OHLC_RELATIONSHIP,
    validate_ohlcv_bars,
    persist_offline_ohlcv_validation_flow,
)
from secure_eval_wrapper.storage.postgres.mappers import (
    normalized_bar_to_row,
    raw_observation_to_row,
    validation_report_to_row,
)
from secure_eval_wrapper.storage.postgres.repositories import (
    PostgresDataQualityRepository,
    PostgresMarketDataRepository,
    PostgresOfflineValidationRepository,
)


COLLECTION_RUN_ID = UUID("30000000-0000-0000-0000-000000000001")
VALIDATION_RUN_ID = UUID("30000000-0000-0000-0000-000000000002")
FIXED_NOW = datetime(2026, 7, 9, 14, 0, tzinfo=timezone.utc)


class FakePersistenceRepository:
    def __init__(self) -> None:
        self.raw = []
        self.reports = []
        self.checks = []
        self.bars = []
        self.quarantine = []
        self.transactions = 0

    @contextmanager
    def transaction(self):
        self.transactions += 1
        yield self

    def record_raw_source_observation(self, row):
        self.raw.append(row)
        return row["observation_id"]

    def record_validation_report(self, row):
        self.reports.append(row)
        return row["validation_report_id"]

    def record_data_quality_check(self, row):
        self.checks.append(row)
        return row["check_id"]

    def record_validated_bar(self, row):
        self.bars.append(row)
        return row["bar_id"]

    def record_quarantine_decision(self, row):
        self.quarantine.append(row)
        return row["quarantine_id"]


class NoConnectConnection:
    def __init__(self) -> None:
        self.cursor_calls = 0

    def cursor(self):
        self.cursor_calls += 1
        raise AssertionError("connection opened during repository import/initialization")


class ReturningCursor:
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


class ReturningConnection:
    def __init__(self, row=None) -> None:
        self.cursor_instance = ReturningCursor(row)
        self.commits = 0

    def cursor(self):
        return self.cursor_instance

    def commit(self):
        self.commits += 1

    def rollback(self):
        return None


class Phase2DPersistenceTests(unittest.TestCase):
    def setUp(self) -> None:
        provider = SampleProvider(clock=lambda: FIXED_NOW)
        request = DataRequest(
            collection_run_id=COLLECTION_RUN_ID,
            provider_name="sample_file",
            data_type=MarketDataType.OHLCV,
            symbols=("btc/usdt",),
            timeframe="1m",
        )
        with patch("socket.socket", side_effect=AssertionError("network access attempted")):
            self.observations = provider.fetch_ohlcv(request)
            self.bars = normalize_ohlcv_observations(self.observations)
        self.report = validate_ohlcv_bars(
            validation_run_id=VALIDATION_RUN_ID,
            dataset_ref="synthetic-btc-usdt-1m",
            bars=self.bars,
            clock=lambda: FIXED_NOW,
        )

    def test_accepted_flow_persists_raw_report_checks_and_bars(self) -> None:
        repository = FakePersistenceRepository()
        summary = persist_offline_ohlcv_validation_flow(
            self.observations,
            self.bars,
            self.report,
            repository=repository,
        )

        self.assertEqual(len(repository.raw), 3)
        self.assertEqual(len(repository.checks), len(self.report.results))
        self.assertEqual(len(repository.bars), 3)
        self.assertEqual(repository.quarantine, [])
        self.assertEqual(summary.accepted_bar_ids, tuple(bar.bar_id for bar in self.bars))
        self.assertEqual(repository.reports[0]["report_sha256"], self.report.report_sha256)
        self.assertEqual(
            {row["source_sha256"] for row in repository.raw},
            set(self.report.source_hashes),
        )
        self.assertEqual(repository.transactions, 1)

    def test_rejected_observation_is_quarantined_and_other_bars_promoted(self) -> None:
        invalid = replace(self.bars[1], high=Decimal("99"))
        report = validate_ohlcv_bars(
            validation_run_id=VALIDATION_RUN_ID,
            dataset_ref="synthetic-btc-usdt-1m",
            bars=(self.bars[0], invalid, self.bars[2]),
            clock=lambda: FIXED_NOW,
        )
        repository = FakePersistenceRepository()
        summary = persist_offline_ohlcv_validation_flow(
            self.observations,
            (self.bars[0], invalid, self.bars[2]),
            report,
            repository=repository,
        )

        self.assertEqual(len(repository.bars), 2)
        self.assertEqual(len(repository.quarantine), 1)
        self.assertEqual(repository.quarantine[0]["observation_id"], self.observations[1].observation_id)
        self.assertEqual(repository.quarantine[0]["quarantine_reason"], INVALID_OHLC_RELATIONSHIP)
        self.assertEqual(len(summary.quarantine_decision_ids), 1)

    def test_mapping_utilities_preserve_domain_identity_and_hashes(self) -> None:
        raw_row = raw_observation_to_row(self.observations[0])
        bar_row = normalized_bar_to_row(
            self.bars[0], validation_report_id=self.report.validation_report_id
        )
        report_row = validation_report_to_row(self.report)
        self.assertEqual(raw_row["observation_id"], self.observations[0].observation_id)
        self.assertEqual(raw_row["source_sha256"], self.observations[0].source_sha256)
        self.assertEqual(bar_row["source_observation_ids"], [self.observations[0].observation_id])
        self.assertEqual(report_row["report_sha256"], self.report.report_sha256)
        self.assertEqual(
            report_row["report_jsonb"]["tolerance_config_sha256"],
            self.report.tolerance_config_sha256,
        )

    def test_repository_constructor_does_not_use_connection(self) -> None:
        connection = NoConnectConnection()
        PostgresOfflineValidationRepository(connection)
        self.assertEqual(connection.cursor_calls, 0)

    def test_report_conflict_returns_existing_database_id(self) -> None:
        existing_id = UUID("30000000-0000-0000-0000-000000000099")
        connection = ReturningConnection((existing_id,))
        repository = PostgresDataQualityRepository(connection)
        row = validation_report_to_row(self.report)
        row["validation_report_id"] = UUID("30000000-0000-0000-0000-000000000098")

        returned_id = repository.record_validation_report(row)

        self.assertEqual(returned_id, existing_id)
        self.assertIn("RETURNING validation_report_id", connection.cursor_instance.sql)
        self.assertEqual(connection.commits, 1)

    def test_validated_bar_query_uses_end_exclusive_window(self) -> None:
        connection = ReturningConnection()
        repository = PostgresMarketDataRepository(connection)

        repository.list_validated_bars(
            symbol="BTC-USDT",
            exchange="sample",
            timeframe="1m",
            start_utc=FIXED_NOW,
            end_utc=FIXED_NOW,
        )

        sql = connection.cursor_instance.sql
        self.assertIn("bar_open_time_utc >= %s AND bar_open_time_utc < %s", sql)
        self.assertNotIn("bar_open_time_utc <= %s", sql)


if __name__ == "__main__":
    unittest.main()
