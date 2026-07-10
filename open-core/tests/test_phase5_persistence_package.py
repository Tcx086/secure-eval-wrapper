from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import tomllib
import unittest
from contextlib import contextmanager
from dataclasses import replace
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch
from uuid import UUID, uuid4

from secure_eval_wrapper.backtesting.cli import build_result, main as cli_main
from secure_eval_wrapper.data_collection.hashing import sha256_payload
from secure_eval_wrapper.data_collection.models import FundingIntervalSource, FundingRate, InstrumentType
from secure_eval_wrapper.storage.backtest_bundle import BacktestBundlePersistenceError, persist_backtest_bundle
from secure_eval_wrapper.storage.postgres.phase5_repositories import Phase5ConflictError, PostgresPhase5Repository
from secure_eval_wrapper.storage.postgres.phase5_rows import fill_row, order_intent_row

from test_phase5_execution import H, RUN, T0, bar, config, instrument, run_engine, signal

ROOT = Path(__file__).resolve().parents[2]
OPEN_CORE = ROOT / "open-core"


class FakeCursor:
    def __init__(self, connection):
        self.connection = connection
        self.rows = []
        self.description = None

    def execute(self, sql, params=()):
        self.connection.executions.append((sql, tuple(params)))
        if sql.startswith("INSERT"):
            self.rows = [self.connection.insert_result] if self.connection.insert_result is not None else []
        else:
            self.rows = [self.connection.select_result] if self.connection.select_result is not None else []

    def fetchone(self):
        return None if not self.rows else self.rows.pop(0)

    def fetchall(self):
        rows, self.rows = self.rows, []
        return rows

    def close(self): pass


class FakeConnection:
    def __init__(self, insert_result=None, select_result=None):
        self.insert_result = insert_result
        self.select_result = select_result
        self.executions = []
        self.commits = 0
        self.rollbacks = 0

    def cursor(self): return FakeCursor(self)
    def commit(self): self.commits += 1
    def rollback(self): self.rollbacks += 1


class MappingRepositoryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.result, _ = build_result(OPEN_CORE / "data" / "sample" / "crypto_ohlcv_sample.json")

    def test_complete_order_intent_and_fill_mappings(self):
        intent = order_intent_row(self.result.order_intents[0])
        fill = fill_row(self.result.fills[0])
        for name in ("run_id", "series_identity_sha256", "target_quantity", "current_quantity", "delta_quantity", "reference_price", "config_sha256", "data_sha256", "implementation_code_sha256", "record_sha256"):
            self.assertIn(name, intent)
        for name in ("order_intent_id", "base_price", "notional", "fee_amount", "slippage_amount", "slippage_bps", "fill_reason", "record_sha256"):
            self.assertIn(name, fill)

    def test_repository_construction_performs_no_io(self):
        connection = FakeConnection()
        PostgresPhase5Repository(connection)
        self.assertEqual(connection.executions, [])

    def test_parameterized_insert_and_database_selected_id(self):
        value = self.result.order_intents[0]
        connection = FakeConnection((value.order_intent_id, value.record_sha256))
        selected = PostgresPhase5Repository(connection).record_order_intent(value)
        self.assertEqual(selected, value.order_intent_id)
        sql, params = connection.executions[0]
        self.assertIn("%s", sql)
        self.assertNotIn(str(value.order_intent_id), sql)
        self.assertIn(value.order_intent_id, params)

    def test_same_identity_same_hash_retry_is_idempotent(self):
        value = self.result.order_intents[0]
        connection = FakeConnection(None, (value.order_intent_id, value.record_sha256))
        selected = PostgresPhase5Repository(connection).record_order_intent(value)
        self.assertEqual(selected, value.order_intent_id)

    def test_same_identity_different_hash_conflicts(self):
        value = self.result.order_intents[0]
        connection = FakeConnection(None, (value.order_intent_id, "f" * 64))
        with self.assertRaises(Phase5ConflictError):
            PostgresPhase5Repository(connection).record_order_intent(value)
        self.assertEqual(connection.rollbacks, 1)

    def test_half_open_event_read_is_ordered(self):
        connection = FakeConnection()
        repository = PostgresPhase5Repository(connection)
        repository.list_backtest_events(backtest_run_id=RUN, start_utc=T0, end_utc=T0 + timedelta(minutes=1))
        sql, params = connection.executions[0]
        self.assertIn("event_timestamp_utc >= %s", sql)
        self.assertIn("event_timestamp_utc < %s", sql)
        self.assertIn("ORDER BY event_timestamp_utc, event_priority, deterministic_sequence", sql)
        self.assertEqual(params, (RUN, T0, T0 + timedelta(minutes=1)))


class RecordingBundleRepository:
    METHODS = (
        "record_order_intent", "record_risk_decision", "record_order", "record_fill", "upsert_position",
        "record_position_snapshot", "record_funding_payment", "record_cash_ledger_entry",
        "record_account_snapshot", "record_backtest_event", "record_equity_curve_point", "record_backtest_metric",
    )

    def __init__(self, fail_at=None):
        self.fail_at = fail_at
        self.rows = []
        self.commits = 0
        self.rollbacks = 0

    @contextmanager
    def transaction(self):
        before = list(self.rows)
        try:
            yield self
        except Exception:
            self.rows = before
            self.rollbacks += 1
            raise
        else:
            self.commits += 1

    def record_backtest_run(self, value): self.rows.append(("run", value.run_id))


def _method(name):
    def write(self, value):
        if self.fail_at == name:
            raise RuntimeError(f"injected {name} failure")
        self.rows.append((name, getattr(value, "record_sha256", None)))
    return write


for _name in RecordingBundleRepository.METHODS:
    setattr(RecordingBundleRepository, _name, _method(_name))


class BundleRollbackTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        key, _ = instrument(InstrumentType.PERPETUAL_SWAP)
        rate = FundingRate(uuid4(), "BTC-USDT", "fixture-x", T0 + timedelta(minutes=2), __import__("decimal").Decimal("0.001"), (uuid4(),), "1h", FundingIntervalSource.METADATA_REPORTED, instrument_key=key)
        cls.result = run_engine(
            [bar(0, "100", "101", "99", "100", kind=InstrumentType.PERPETUAL_SWAP), bar(1, "100", "101", "99", "100", kind=InstrumentType.PERPETUAL_SWAP), bar(2, "100", "101", "99", "100", kind=InstrumentType.PERPETUAL_SWAP)],
            [signal(1, "long", kind=InstrumentType.PERPETUAL_SWAP)], funding=[rate],
        )
        assert cls.result.funding_payments

    def test_successful_bundle_commits_once(self):
        repository = RecordingBundleRepository()
        summary = persist_backtest_bundle(repository, self.result)
        self.assertEqual(repository.commits, 1)
        self.assertEqual(summary.fills, 1)
        self.assertEqual(summary.funding_payments, 1)

    def test_every_required_child_failure_rolls_back_every_row(self):
        for name in RecordingBundleRepository.METHODS:
            with self.subTest(failure=name):
                repository = RecordingBundleRepository(name)
                with self.assertRaises(BacktestBundlePersistenceError):
                    persist_backtest_bundle(repository, self.result)
                self.assertEqual(repository.rows, [])
                self.assertEqual(repository.rollbacks, 1)


class PackageCliCiBoundaryTests(unittest.TestCase):
    def test_pyproject_package_and_entry_points(self):
        config_data = tomllib.loads((OPEN_CORE / "pyproject.toml").read_text(encoding="utf-8"))
        self.assertEqual(config_data["tool"]["setuptools"]["package-dir"], {"": "src"})
        self.assertIn("secure-eval-backtest", config_data["project"]["scripts"])
        self.assertIn("postgres", config_data["project"]["optional-dependencies"])
        self.assertEqual(config_data["project"]["dependencies"], [])

    def test_fixture_cli_is_socket_free_and_does_not_import_driver(self):
        sys.modules.pop("psycopg", None)
        with patch("socket.socket", side_effect=AssertionError("socket attempted")):
            with patch("builtins.print") as output:
                self.assertEqual(cli_main([]), 0)
        summary = json.loads(output.call_args.args[0])
        self.assertEqual(summary["persistence"], "disabled")
        self.assertEqual(summary["classification"], "synthetic_public_safe")
        self.assertNotIn("psycopg", sys.modules)

    def test_persistence_requires_both_gates(self):
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(RuntimeError): cli_main(["--persist"])

    def test_ci_has_expected_jobs_permissions_and_postgres_16(self):
        workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
        self.assertIn("contents: read", workflow)
        self.assertIn("package-unit:", workflow)
        self.assertIn("postgres-integration:", workflow)
        self.assertIn("boundary:", workflow)
        self.assertIn("postgres:16-alpine", workflow)
        self.assertIn("windows-latest", workflow)

    def test_no_paper_live_or_sqlite_runtime_implementation(self):
        combined = "\n".join(path.read_text(encoding="utf-8", errors="ignore") for path in (OPEN_CORE / "src" / "secure_eval_wrapper").rglob("*.py"))
        self.assertNotRegex(combined, r"class\s+(?:PaperBroker|LiveBroker)\b")
        self.assertNotRegex(combined, r"(^|\n)\s*(?:import sqlite3|from sqlite3)")
        self.assertNotIn("authenticated exchange", combined.lower())

    def test_migrations_0001_through_0008_are_unchanged(self):
        expected = {
            "0001_initial_schema.sql": "598486e6af2eed4559564593adc0b66deff9e21ea91dbda560980c208a2950c5",
            "0002_schema_migrations.sql": "87147ba7efd798e6f93b1e219ef79e6bc2a66c2c7a24ff699a28bd498eb7c0c8",
            "0003_data_quality_quarantine.sql": "d0b32a72ad98a9d1361bfa57770a9b7d58ae2323816e8b3d77c3d05f66b35a9a",
            "0004_reconciliation_persistence.sql": "efe77fa89b25f90dea3f49a70b22b8cc376c434333abbff6fd17cc9eb75fd7ba",
            "0005_trade_funding_instrument_hardening.sql": "b18d66f37df55923a1e1cfba709784de55ab90d0c5ff250b8d683dc6029f9d48",
            "0006_phase2_final_hardening.sql": "af507329f29e63ab260317b879da5e82917aafd7368d692b343a09ccafdace5d",
            "0007_alpha_signal_library.sql": "f9ff354e0a7f319cf82a04ce13eaceeae12c99d903f0a5b683275903f53c5a59",
            "0008_phase3_phase4_audit_repairs.sql": "950a08f2f9c8620b85640d132604c93ae4a03f4111aa9326f0654df369bc320c",
        }
        import hashlib
        for name, digest in expected.items():
            self.assertEqual(hashlib.sha256((OPEN_CORE / "db" / "migrations" / name).read_bytes()).hexdigest(), digest)


if __name__ == "__main__":
    unittest.main()
