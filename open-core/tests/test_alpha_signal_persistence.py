"""Offline PostgreSQL repository and migration contract tests for Phase 3-4."""

from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from uuid import NAMESPACE_URL, UUID, uuid5

import secure_eval_wrapper.data_validation  # initialize existing package before PostgreSQL modules
from secure_eval_wrapper.alpha.examples import MomentumAlpha
from secure_eval_wrapper.alpha.models import AlphaRun, AlphaRunStatus, AlphaValue
from secure_eval_wrapper.signals.models import ComponentDisposition, SignalComponent, SignalDirection, SignalRun, SignalRunStatus, StandardizedSignal
from secure_eval_wrapper.storage.postgres.alpha_signal_mappers import (
    alpha_definition_to_row,
    alpha_run_to_row,
    alpha_value_to_row,
    signal_component_to_row,
    signal_run_to_row,
    standardized_signal_to_row,
)
from secure_eval_wrapper.storage.postgres.alpha_signal_repositories import (
    AlphaSignalConflictError,
    PostgresAlphaRepository,
    PostgresSignalRepository,
)


START = datetime(2026, 1, 1, tzinfo=timezone.utc)
NOW = datetime(2026, 7, 10, tzinfo=timezone.utc)
RUN_ID = UUID("71000000-0000-0000-0000-000000000001")
SIGNAL_RUN_ID = UUID("71000000-0000-0000-0000-000000000002")


class ReplayCursor:
    def __init__(self, rows=()):
        self.rows = list(rows)
        self.executions = []
        self.description = None

    def execute(self, sql, params):
        self.executions.append((sql, params))

    def fetchone(self):
        return self.rows.pop(0) if self.rows else None

    def fetchall(self):
        return ()

    def close(self):
        return None


class ReplayConnection:
    def __init__(self, rows=()):
        self.cursor_instance = ReplayCursor(rows)
        self.commits = 0
        self.rollbacks = 0

    def cursor(self):
        return self.cursor_instance

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1


def alpha_run():
    definition = MomentumAlpha().definition
    return AlphaRun(
        alpha_run_id=RUN_ID,
        alpha_id=definition.alpha_id,
        alpha_name=definition.name,
        alpha_version=definition.version,
        symbols=("BTC-USDT",),
        window_start_utc=START,
        window_end_utc=START + timedelta(hours=1),
        dataset_refs=("synthetic",),
        input_data_sha256="a" * 64,
        config_sha256="b" * 64,
        implementation_sha256=definition.implementation_sha256,
        started_at_utc=NOW,
        completed_at_utc=NOW,
        status=AlphaRunStatus.COMPLETED,
        output_count=1,
        rejected_count=0,
        skipped_count=0,
        metadata={"research": True},
    )


def alpha_value():
    run = alpha_run()
    return AlphaValue(
        alpha_value_id=UUID("71000000-0000-0000-0000-000000000003"),
        alpha_id=run.alpha_id,
        alpha_name=run.alpha_name,
        alpha_version=run.alpha_version,
        alpha_run_id=run.alpha_run_id,
        symbol="BTC-USDT",
        timestamp_utc=START,
        raw_score=Decimal("0.1"),
        warmup_complete=True,
        valid=True,
        horizon="next_observation_research_input",
        source_observation_ids=(UUID("71000000-0000-0000-0000-000000000004"),),
        dataset_sha256="a" * 64,
        config_sha256="b" * 64,
        implementation_sha256=run.implementation_sha256,
        provenance={"point_in_time_safe": True},
    )


def signal_run():
    return SignalRun(
        signal_run_id=SIGNAL_RUN_ID,
        alpha_run_ids=(RUN_ID,),
        symbol_universe=("BTC-USDT",),
        window_start_utc=START,
        window_end_utc=START + timedelta(hours=1),
        ranking_config={"method": "dense"},
        threshold_config={"policy": "absolute"},
        combination_config={"mode": "single_alpha"},
        config_sha256="d" * 64,
        code_sha256="e" * 64,
        data_sha256="f" * 64,
        status=SignalRunStatus.COMPLETED,
        output_count=1,
        long_count=1,
        short_count=0,
        flat_count=0,
        skipped_count=0,
        failure_count=0,
        started_at_utc=NOW,
        completed_at_utc=NOW,
        metadata={"execution_output": False},
    )


def signal():
    definition = MomentumAlpha().definition
    return StandardizedSignal(
        signal_id=UUID("71000000-0000-0000-0000-000000000005"),
        signal_run_id=SIGNAL_RUN_ID,
        alpha_ids_versions=(f"{definition.alpha_id}@{definition.version}",),
        alpha_run_ids=(RUN_ID,),
        symbol="BTC-USDT",
        timestamp_utc=START,
        direction=SignalDirection.LONG,
        raw_score=Decimal("0.1"),
        normalized_score=Decimal(1),
        rank=1,
        percentile=Decimal("0.5"),
        confidence=Decimal("0.8"),
        horizon="next_observation_research_input",
        source_alpha_value_ids=(alpha_value().alpha_value_id,),
        config_sha256="d" * 64,
        data_sha256="f" * 64,
        code_sha256="e" * 64,
        provenance={"contributions": ({"alpha_id": definition.alpha_id},), "research_output_only": True},
    )


def signal_component():
    definition = MomentumAlpha().definition
    return SignalComponent(
        signal_component_id=UUID("71000000-0000-0000-0000-000000000006"),
        signal_id=signal().signal_id,
        alpha_value_id=alpha_value().alpha_value_id,
        alpha_id=definition.alpha_id,
        raw_value=Decimal("0.1"),
        normalized_value=Decimal(1),
        configured_weight=Decimal(1),
        effective_weight=Decimal(1),
        signed_contribution=Decimal(1),
        component_disposition=ComponentDisposition.CONTRIBUTED,
        public_metadata={"classification": "synthetic_public_safe"},
    )

class AlphaSignalMappingTests(unittest.TestCase):
    def test_mappings_preserve_hashes_and_research_only_fields(self):
        definition_row = alpha_definition_to_row(MomentumAlpha().definition)
        run_row = alpha_run_to_row(alpha_run())
        value_row = alpha_value_to_row(alpha_value())
        signal_run_row = signal_run_to_row(signal_run())
        signal_row = standardized_signal_to_row(signal())
        component_row = signal_component_to_row(signal_component())
        self.assertEqual(definition_row["implementation_sha256"], MomentumAlpha().definition.implementation_sha256)
        self.assertEqual(value_row["content_sha256"], alpha_value().content_sha256)
        self.assertEqual(signal_run_row["run_id"], SIGNAL_RUN_ID)
        self.assertEqual(signal_row["source_alpha_value_ids"], [alpha_value().alpha_value_id])
        self.assertEqual(component_row["component_sha256"], signal_component().component_sha256)
        for forbidden in ("quantity", "leverage", "account_id", "broker_order_ref"):
            self.assertNotIn(forbidden, signal_row)
        self.assertRegex(run_row["content_sha256"], r"^[0-9a-f]{64}$")


class AlphaSignalRepositoryTests(unittest.TestCase):
    def test_constructor_has_no_database_activity(self):
        connection = ReplayConnection()
        PostgresAlphaRepository(connection)
        PostgresSignalRepository(connection)
        self.assertEqual(connection.cursor_instance.executions, [])

    def test_alpha_registry_idempotency_returns_database_selected_id(self):
        definition = MomentumAlpha().definition
        existing = UUID("71000000-0000-0000-0000-000000000099")
        connection = ReplayConnection((None, (existing, definition.implementation_sha256, definition.content_sha256)))
        returned = PostgresAlphaRepository(connection).register_alpha(definition)
        self.assertEqual(returned, existing)
        insert_sql, insert_params = connection.cursor_instance.executions[0]
        self.assertIn("ON CONFLICT (alpha_name, alpha_version) DO NOTHING", insert_sql)
        self.assertIn("%s", insert_sql)
        self.assertNotIn(definition.name, insert_sql)
        self.assertIn(definition.name, insert_params)

    def test_alpha_registry_hash_conflict_fails_and_rolls_back(self):
        definition = MomentumAlpha().definition
        connection = ReplayConnection((None, (definition.alpha_id, "f" * 64, definition.content_sha256)))
        with self.assertRaises(AlphaSignalConflictError):
            PostgresAlphaRepository(connection).register_alpha(definition)
        self.assertEqual(connection.rollbacks, 1)
        self.assertEqual(connection.commits, 0)

    def test_alpha_run_value_signal_run_and_signal_return_inserted_ids(self):
        cases = (
            (PostgresAlphaRepository, "record_alpha_run", alpha_run(), RUN_ID),
            (PostgresAlphaRepository, "record_alpha_value", alpha_value(), alpha_value().alpha_value_id),
            (PostgresSignalRepository, "record_signal_run", signal_run(), SIGNAL_RUN_ID),
            (PostgresSignalRepository, "record_signal", signal(), signal().signal_id),
            (PostgresSignalRepository, "record_signal_component", signal_component(), signal_component().signal_component_id),
        )
        for repository_type, method, domain, expected in cases:
            connection = ReplayConnection(((expected,),))
            returned = getattr(repository_type(connection), method)(domain)
            self.assertEqual(returned, expected)
            self.assertEqual(connection.commits, 1)

    def test_half_open_alpha_and_signal_reads(self):
        alpha_connection = ReplayConnection()
        PostgresAlphaRepository(alpha_connection).list_alpha_values(alpha_run_id=RUN_ID, start_utc=START, end_utc=START + timedelta(hours=1))
        alpha_sql = alpha_connection.cursor_instance.executions[0][0]
        self.assertIn("timestamp_utc >= %s AND timestamp_utc < %s", alpha_sql)
        signal_connection = ReplayConnection()
        PostgresSignalRepository(signal_connection).list_signals(signal_run_id=SIGNAL_RUN_ID, start_utc=START, end_utc=START + timedelta(hours=1))
        signal_sql = signal_connection.cursor_instance.executions[0][0]
        self.assertIn("timestamp_utc >= %s", signal_sql)
        self.assertIn("timestamp_utc < %s", signal_sql)
        component_connection = ReplayConnection()
        PostgresSignalRepository(component_connection).list_signal_components(signal_id=signal().signal_id)
        self.assertIn("signals.signal_components", component_connection.cursor_instance.executions[0][0])

    def test_transaction_rolls_back_child_failure(self):
        connection = ReplayConnection()
        repository = PostgresSignalRepository(connection)
        with self.assertRaisesRegex(RuntimeError, "child"):
            with repository.transaction():
                raise RuntimeError("child")
        self.assertEqual(connection.rollbacks, 1)
        self.assertEqual(connection.commits, 0)

    def test_migration_is_ordered_postgresql_only_and_preserves_old_files(self):
        root = __import__("pathlib").Path(__file__).resolve().parents[2]
        migration = (root / "open-core/db/migrations/0007_alpha_signal_library.sql").read_text(encoding="utf-8-sig").lower()
        self.assertIn("create table if not exists alpha.alpha_runs", migration)
        self.assertIn("create table if not exists alpha.alpha_values", migration)
        self.assertIn("on alpha.alpha_values", migration)
        self.assertNotIn("sqlite", migration)
        self.assertTrue((root / "open-core/db/migrations/0006_phase2_final_hardening.sql").exists())
        repair = (root / "open-core/db/migrations/0008_phase3_phase4_audit_repairs.sql").read_text(encoding="utf-8-sig").lower()
        self.assertIn("create table if not exists signals.signal_components", repair)
        self.assertIn("bar_close_time_utc", repair)
        self.assertNotIn("sqlite", repair)


if __name__ == "__main__":
    unittest.main()
