"""Third independent Phase 5 audit: normalized complete-run membership regressions."""

from __future__ import annotations

import os
import unittest
from contextlib import contextmanager
from dataclasses import replace
from datetime import timedelta
from uuid import NAMESPACE_URL, UUID, uuid5

from secure_eval_wrapper.backtesting.cli import _persist_upstream_signals
from secure_eval_wrapper.data_collection.hashing import sha256_payload
from secure_eval_wrapper.storage.backtest_bundle import (
    BacktestBundlePersistenceError,
    persist_backtest_bundle,
)
from secure_eval_wrapper.storage.postgres.phase5_repositories import PostgresPhase5Repository

from test_phase5_execution import T0, bar, config, run_engine, signal


def overlapping_results():
    bars_a = [
        bar(0, "100", "101", "99", "100"),
        bar(1, "100", "101", "99", "100"),
        bar(2, "110", "111", "109", "110"),
        bar(3, "115", "116", "114", "115"),
    ]
    bars_b = bars_a + [
        bar(5, "120", "121", "119", "120"),
        bar(6, "125", "126", "124", "125"),
    ]
    signals = (signal(1, "long"),)
    return run_engine(bars_a, signals), run_engine(bars_b, signals), signals


def record_signatures(result):
    return {
        "order_intents": {(row.order_intent_id, row.record_sha256) for row in result.order_intents},
        "risk_decisions": {(row.risk_decision_id, row.record_sha256) for row in result.risk_decisions},
        "orders": {(row.order_id, row.record_sha256) for row in result.orders},
        "fills": {(row.fill_id, row.record_sha256) for row in result.fills},
        "positions": {(row.position_id, row.record_sha256) for row in result.positions},
        "position_snapshots": {(row.position_snapshot_id, row.record_sha256) for row in result.position_snapshots},
        "cash_ledger_entries": {(row.cash_ledger_entry_id, row.record_sha256) for row in result.cash_ledger_entries},
        "funding_payments": {(row.funding_payment_id, row.record_sha256) for row in result.funding_payments},
        "account_snapshots": {(row.account_snapshot_id, row.record_sha256) for row in result.account_snapshots},
        "events": {(row.execution_event_id, row.record_sha256) for row in result.events},
        "equity_points": {(row.equity_curve_id, row.record_sha256) for row in result.equity_curve},
    }


class ThirdAuditIdentityTests(unittest.TestCase):
    def test_short_and_extended_runs_share_only_immutable_history(self):
        run_a, run_b, _ = overlapping_results()
        self.assertNotEqual(run_a.run.backtest_run_id, run_b.run.backtest_run_id)
        self.assertEqual(run_a.run.run_id, run_b.run.run_id)
        signatures_a = record_signatures(run_a)
        signatures_b = record_signatures(run_b)
        for record_type, rows_a in signatures_a.items():
            with self.subTest(record_type=record_type):
                self.assertTrue(rows_a.issubset(signatures_b[record_type]))
        self.assertTrue(signatures_b["events"] - signatures_a["events"])
        self.assertTrue(signatures_b["equity_points"] - signatures_a["equity_points"])

    def test_metrics_are_scoped_to_complete_run_and_can_differ(self):
        run_a, run_b, _ = overlapping_results()
        self.assertTrue(all(row.backtest_run_id == run_a.run.backtest_run_id for row in run_a.metric_records))
        self.assertTrue(all(row.backtest_run_id == run_b.run.backtest_run_id for row in run_b.metric_records))
        self.assertTrue(
            {row.backtest_metric_id for row in run_a.metric_records}.isdisjoint(
                row.backtest_metric_id for row in run_b.metric_records
            )
        )
        metrics_a = {row.name: row.record_sha256 for row in run_a.metric_records}
        metrics_b = {row.name: row.record_sha256 for row in run_b.metric_records}
        self.assertNotEqual(metrics_a["final_equity"], metrics_b["final_equity"])

    def test_configuration_implementation_and_unrelated_runs_have_separate_lineages(self):
        run_a, _, _ = overlapping_results()
        bars = [bar(0, "100", "101", "99", "100"), bar(1, "100", "101", "99", "100"), bar(2, "110", "111", "109", "110")]
        signals = [signal(1, "long")]
        changed_config = run_engine(bars, signals, configuration=config(initial="2000"))
        changed_impl = run_engine(bars, signals, implementation_code_sha256=sha256_payload({"phase5_impl": "other"}))
        self.assertNotEqual(run_a.run.run_id, changed_config.run.run_id)
        self.assertNotEqual(run_a.run.run_id, changed_impl.run.run_id)

        unrelated_signal = replace(
            signal(1, "long", provider="other", exchange="other-x", provider_id="ETH-USDT"),
            signal_id=uuid5(NAMESPACE_URL, "unrelated-signal"),
            signal_run_id=uuid5(NAMESPACE_URL, "unrelated-signal-run"),
        )
        unrelated = run_engine(
            [
                bar(0, "50", "51", "49", "50", provider="other", exchange="other-x", provider_id="ETH-USDT"),
                bar(1, "50", "51", "49", "50", provider="other", exchange="other-x", provider_id="ETH-USDT"),
                bar(2, "55", "56", "54", "55", provider="other", exchange="other-x", provider_id="ETH-USDT"),
            ],
            [unrelated_signal],
        )
        self.assertNotEqual(run_a.run.run_id, unrelated.run.run_id)
        for record_type in ("fills", "events", "equity_points", "cash_ledger_entries"):
            self.assertTrue(record_signatures(run_a)[record_type].isdisjoint(record_signatures(unrelated)[record_type]))


@unittest.skipUnless(
    os.environ.get("RUN_POSTGRES_INTEGRATION", "").lower() == "true",
    "real PostgreSQL integration is explicitly gated",
)
class ThirdAuditPostgresTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import psycopg

        cls.connection = psycopg.connect(
            host=os.environ["POSTGRES_HOST"],
            port=int(os.environ["POSTGRES_PORT"]),
            dbname=os.environ["POSTGRES_DB"],
            user=os.environ["POSTGRES_USER"],
            password=os.environ["POSTGRES_PASSWORD"],
            sslmode=os.environ.get("POSTGRES_SSLMODE", "disable"),
        )
        cls.run_a, cls.run_b, cls.signals = overlapping_results()
        _persist_upstream_signals(cls.connection, cls.signals)

    @classmethod
    def tearDownClass(cls):
        cls._delete_runs()
        cls.connection.close()

    def setUp(self):
        self._delete_runs()

    @classmethod
    def _delete_runs(cls):
        repository = PostgresPhase5Repository(cls.connection)
        for result in (cls.run_a, cls.run_b):
            repository.delete_backtest_run(backtest_run_id=result.run.backtest_run_id)

    def _membership_count(self, backtest_run_id: UUID) -> int:
        with self.connection.cursor() as cursor:
            cursor.execute(
                "SELECT count(*) FROM backtesting.backtest_run_memberships WHERE backtest_run_id=%s",
                (backtest_run_id,),
            )
            return cursor.fetchone()[0]

    def _assert_no_orphan_memberships(self):
        with self.connection.cursor() as cursor:
            cursor.execute(
                "SELECT count(*) FROM backtesting.backtest_run_memberships membership "
                "LEFT JOIN backtesting.backtest_runs run USING (backtest_run_id) "
                "WHERE run.backtest_run_id IS NULL"
            )
            self.assertEqual(cursor.fetchone()[0], 0)

    def test_overlapping_runs_coexist_replay_and_reconstruct_exactly(self):
        repository = PostgresPhase5Repository(self.connection)
        summary_a = persist_backtest_bundle(repository, self.run_a)
        summary_b = persist_backtest_bundle(repository, self.run_b)
        bundle_a = repository.get_backtest_bundle(backtest_run_id=self.run_a.run.backtest_run_id)
        bundle_b = repository.get_backtest_bundle(backtest_run_id=self.run_b.run.backtest_run_id)
        self.assertIsNotNone(bundle_a)
        self.assertIsNotNone(bundle_b)
        self.assertEqual(len(bundle_a["events"]), len(self.run_a.events))
        self.assertEqual(len(bundle_b["events"]), len(self.run_b.events))
        self.assertEqual(len(bundle_a["equity_points"]), len(self.run_a.equity_curve))
        self.assertEqual(len(bundle_b["equity_points"]), len(self.run_b.equity_curve))
        for bundle, expected_run_id in (
            (bundle_a, self.run_a.run.backtest_run_id),
            (bundle_b, self.run_b.run.backtest_run_id),
        ):
            for collection_name in ("events", "equity_points", "fills", "account_snapshots"):
                rows = bundle[collection_name]
                self.assertTrue(all(row["membership_backtest_run_id"] == expected_run_id for row in rows))
                self.assertEqual([row["membership_ordinal"] for row in rows], list(range(len(rows))))
        a_event_ids = {row["backtest_event_id"] for row in bundle_a["events"]}
        b_event_ids = {row["backtest_event_id"] for row in bundle_b["events"]}
        self.assertTrue(a_event_ids.issubset(b_event_ids))
        self.assertTrue(b_event_ids - a_event_ids)
        self.assertEqual(
            {row["backtest_event_id"] for row in bundle_a["events"]},
            {row.execution_event_id for row in self.run_a.events},
        )
        self.assertEqual(len(bundle_a["metrics"]), len(self.run_a.metric_records))
        self.assertEqual(len(bundle_b["metrics"]), len(self.run_b.metric_records))
        self.assertNotEqual(
            next(row["metric_value"] for row in bundle_a["metrics"] if row["metric_name"] == "final_equity"),
            next(row["metric_value"] for row in bundle_b["metrics"] if row["metric_name"] == "final_equity"),
        )
        persist_backtest_bundle(repository, self.run_a)
        persist_backtest_bundle(repository, self.run_b)
        self.assertEqual(self._membership_count(self.run_a.run.backtest_run_id), summary_a.memberships)
        self.assertEqual(self._membership_count(self.run_b.run.backtest_run_id), summary_b.memberships)

    def test_delete_short_run_preserves_extended_shared_records(self):
        repository = PostgresPhase5Repository(self.connection)
        persist_backtest_bundle(repository, self.run_a)
        persist_backtest_bundle(repository, self.run_b)
        shared_fill_id = self.run_a.fills[0].fill_id
        repository.delete_backtest_run(backtest_run_id=self.run_a.run.backtest_run_id)
        self.assertIsNone(repository.get_backtest_bundle(backtest_run_id=self.run_a.run.backtest_run_id))
        self.assertIsNotNone(repository.get_backtest_bundle(backtest_run_id=self.run_b.run.backtest_run_id))
        with self.connection.cursor() as cursor:
            cursor.execute("SELECT count(*) FROM execution.fills WHERE fill_id=%s", (shared_fill_id,))
            self.assertEqual(cursor.fetchone()[0], 1)
        self.assertEqual(self._membership_count(self.run_a.run.backtest_run_id), 0)
        self._assert_no_orphan_memberships()
        repository.delete_backtest_run(backtest_run_id=self.run_b.run.backtest_run_id)
        with self.connection.cursor() as cursor:
            cursor.execute("SELECT count(*) FROM execution.fills WHERE fill_id=%s", (shared_fill_id,))
            self.assertEqual(cursor.fetchone()[0], 0)
        self._assert_no_orphan_memberships()

    def test_delete_extended_run_does_not_corrupt_short_run(self):
        repository = PostgresPhase5Repository(self.connection)
        persist_backtest_bundle(repository, self.run_a)
        persist_backtest_bundle(repository, self.run_b)
        b_only_event_id = next(
            row.execution_event_id
            for row in self.run_b.events
            if row.execution_event_id not in {event.execution_event_id for event in self.run_a.events}
        )
        repository.delete_backtest_run(backtest_run_id=self.run_b.run.backtest_run_id)
        bundle_a = repository.get_backtest_bundle(backtest_run_id=self.run_a.run.backtest_run_id)
        self.assertEqual(len(bundle_a["events"]), len(self.run_a.events))
        with self.connection.cursor() as cursor:
            cursor.execute("SELECT count(*) FROM backtesting.backtest_events WHERE backtest_event_id=%s", (b_only_event_id,))
            self.assertEqual(cursor.fetchone()[0], 0)
        self.assertEqual(self._membership_count(self.run_b.run.backtest_run_id), 0)
        self._assert_no_orphan_memberships()

    def test_child_hash_conflict_and_membership_rollback_are_atomic(self):
        repository = PostgresPhase5Repository(self.connection)
        persist_backtest_bundle(repository, self.run_a)
        fill = self.run_a.fills[0]
        with self.connection.cursor() as cursor:
            cursor.execute(
                "UPDATE execution.fills SET record_sha256=%s WHERE fill_id=%s",
                ("f" * 64, fill.fill_id),
            )
        self.connection.commit()
        with self.assertRaises(BacktestBundlePersistenceError):
            persist_backtest_bundle(repository, self.run_a)
        with self.connection.cursor() as cursor:
            cursor.execute(
                "UPDATE execution.fills SET record_sha256=%s WHERE fill_id=%s",
                (fill.record_sha256, fill.fill_id),
            )
        self.connection.commit()
        repository.delete_backtest_run(backtest_run_id=self.run_a.run.backtest_run_id)

        unrelated = run_engine(
            [bar(0, "100", "101", "99", "100"), bar(1, "100", "101", "99", "100"), bar(2, "110", "111", "109", "110")],
            [signal(1, "long")],
            configuration=config(initial="3000"),
        )
        failing = _FailAfterMemberships(repository, "record_backtest_event")
        with self.assertRaises(BacktestBundlePersistenceError):
            persist_backtest_bundle(failing, unrelated)
        self.assertIsNone(repository.get_backtest_bundle(backtest_run_id=unrelated.run.backtest_run_id))
        self.assertEqual(self._membership_count(unrelated.run.backtest_run_id), 0)
        with self.connection.cursor() as cursor:
            cursor.execute("SELECT count(*) FROM execution.order_intents WHERE run_id=%s", (unrelated.run.run_id,))
            self.assertEqual(cursor.fetchone()[0], 0)
        self._assert_no_orphan_memberships()


class _FailAfterMemberships:
    def __init__(self, base, failure):
        self.base = base
        self.failure = failure

    @contextmanager
    def transaction(self):
        with self.base.transaction():
            yield self

    def __getattr__(self, name):
        target = getattr(self.base, name)
        if name != self.failure:
            return target

        def fail(value, **kwargs):
            raise RuntimeError(f"injected failure after membership writes at {name}")

        return fail


if __name__ == "__main__":
    unittest.main()
