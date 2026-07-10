"""Fourth independent Phase 5 audit: run-scoped final projection regressions."""

from __future__ import annotations

import hashlib
import os
import unittest
from contextlib import contextmanager
from dataclasses import replace
from decimal import Decimal
from uuid import NAMESPACE_URL, UUID, uuid5

from secure_eval_wrapper.backtesting.cli import _persist_upstream_signals
from secure_eval_wrapper.data_collection.models import InstrumentType
from secure_eval_wrapper.execution.models import OrderStatus, OrderType
from secure_eval_wrapper.storage.backtest_bundle import BacktestBundlePersistenceError, persist_backtest_bundle
from secure_eval_wrapper.storage.postgres.phase5_repositories import PostgresPhase5Repository

from test_phase5_execution import bar, config, run_engine, signal


def _signals(name: str, *, kind=InstrumentType.SPOT, final_direction: str | None = None):
    run_id = uuid5(NAMESPACE_URL, f"phase5-fourth-audit-signal-run:{name}")
    first = replace(
        signal(1, "long", kind=kind),
        signal_id=uuid5(NAMESPACE_URL, f"phase5-fourth-audit-signal:{name}:long"),
        signal_run_id=run_id,
    )
    if final_direction is None:
        return (first,)
    final = replace(
        signal(5, final_direction, kind=kind),
        signal_id=uuid5(NAMESPACE_URL, f"phase5-fourth-audit-signal:{name}:{final_direction}"),
        signal_run_id=run_id,
    )
    return first, final


def expired_then_filled_results():
    bars_a = [
        bar(0, "100", "101", "99", "100"),
        bar(1, "100", "101", "99", "100"),
        bar(2, "100", "101", "99", "100"),
        bar(3, "100", "101", "99", "100"),
    ]
    bars_b = bars_a + [
        bar(5, "100", "101", "89", "90"),
        bar(6, "90", "91", "89", "90"),
    ]
    signals = _signals("order")
    configuration = config(order_type=OrderType.LIMIT, limit="1000")
    return run_engine(bars_a, signals, configuration=configuration), run_engine(bars_b, signals, configuration=configuration), signals


def long_then_flat_results():
    bars_a = [
        bar(0, "100", "101", "99", "100"),
        bar(1, "100", "101", "99", "100"),
        bar(2, "100", "101", "99", "100"),
        bar(3, "100", "101", "99", "100"),
    ]
    bars_b = bars_a + [
        bar(5, "100", "101", "99", "100"),
        bar(6, "100", "101", "99", "100"),
        bar(7, "100", "101", "99", "100"),
    ]
    signals = _signals("flat", final_direction="flat")
    return run_engine(bars_a, signals[:1]), run_engine(bars_b, signals), signals


def long_then_reversed_results():
    kind = InstrumentType.PERPETUAL_SWAP
    bars_a = [
        bar(0, "100", "101", "99", "100", kind=kind),
        bar(1, "100", "101", "99", "100", kind=kind),
        bar(2, "100", "101", "99", "100", kind=kind),
        bar(3, "100", "101", "99", "100", kind=kind),
    ]
    bars_b = bars_a + [
        bar(5, "100", "101", "99", "100", kind=kind),
        bar(6, "100", "101", "99", "100", kind=kind),
        bar(7, "100", "101", "99", "100", kind=kind),
    ]
    signals = _signals("reverse", kind=kind, final_direction="short")
    return run_engine(bars_a, signals[:1]), run_engine(bars_b, signals), signals


def _signatures(rows, id_name):
    return {(getattr(row, id_name), row.record_sha256) for row in rows}


class FourthAuditDomainTests(unittest.TestCase):
    def test_final_order_state_is_projection_but_lineage_is_stable(self):
        run_a, run_b, _ = expired_then_filled_results()
        self.assertEqual(run_a.orders[0].order_id, run_b.orders[0].order_id)
        self.assertEqual(run_a.orders[0].status, OrderStatus.EXPIRED)
        self.assertEqual(run_b.orders[0].status, OrderStatus.FILLED)
        self.assertNotEqual(run_a.orders[0].record_sha256, run_b.orders[0].record_sha256)
        self.assertEqual(_signatures(run_a.order_intents, "order_intent_id"), _signatures(run_b.order_intents, "order_intent_id"))
        expired_events = [row for row in run_a.events if row.event_type.value == "order_expired"]
        self.assertEqual(len(expired_events), 1)
        self.assertNotIn(expired_events[0].execution_event_id, {row.execution_event_id for row in run_b.events})

    def test_final_positions_are_run_scoped_while_history_is_immutable(self):
        for factory, expected in ((long_then_flat_results, Decimal("0")), (long_then_reversed_results, Decimal("-1"))):
            with self.subTest(factory=factory.__name__):
                run_a, run_b, _ = factory()
                self.assertEqual(run_a.positions[0].position_id, run_b.positions[0].position_id)
                self.assertEqual(run_a.positions[0].quantity, Decimal("1"))
                self.assertEqual(run_b.positions[0].quantity, expected)
                self.assertNotEqual(run_a.positions[0].record_sha256, run_b.positions[0].record_sha256)
                self.assertTrue(_signatures(run_a.fills, "fill_id").issubset(_signatures(run_b.fills, "fill_id")))
                self.assertTrue(_signatures(run_a.position_snapshots, "position_snapshot_id").issubset(_signatures(run_b.position_snapshots, "position_snapshot_id")))


@unittest.skipUnless(
    os.environ.get("RUN_POSTGRES_INTEGRATION", "").lower() == "true",
    "real PostgreSQL integration is explicitly gated",
)
class FourthAuditPostgresTests(unittest.TestCase):
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
        cls.order_a, cls.order_b, order_signals = expired_then_filled_results()
        cls.flat_a, cls.flat_b, flat_signals = long_then_flat_results()
        cls.reverse_a, cls.reverse_b, reverse_signals = long_then_reversed_results()
        cls.scenarios = (
            (cls.order_a, cls.order_b),
            (cls.flat_a, cls.flat_b),
            (cls.reverse_a, cls.reverse_b),
        )
        for signals in (order_signals, flat_signals, reverse_signals):
            with cls.connection.cursor() as cursor:
                cursor.execute("DELETE FROM signals.signals WHERE signal_run_id=%s", (signals[0].signal_run_id,))
                cursor.execute("DELETE FROM signals.signal_runs WHERE signal_run_id=%s", (signals[0].signal_run_id,))
            cls.connection.commit()
            _persist_upstream_signals(cls.connection, signals)

    @classmethod
    def tearDownClass(cls):
        cls._delete_all_runs()
        cls.connection.close()

    def setUp(self):
        self._delete_all_runs()

    @classmethod
    def _delete_all_runs(cls):
        repository = PostgresPhase5Repository(cls.connection)
        for pair in cls.scenarios:
            for result in pair:
                repository.delete_backtest_run(backtest_run_id=result.run.backtest_run_id)

    def _assert_no_orphans(self):
        with self.connection.cursor() as cursor:
            cursor.execute(
                "SELECT count(*) FROM backtesting.backtest_run_memberships membership "
                "LEFT JOIN backtesting.backtest_runs run USING (backtest_run_id) "
                "WHERE run.backtest_run_id IS NULL"
            )
            self.assertEqual(cursor.fetchone()[0], 0)
            for table in ("backtest_order_states", "backtest_position_states"):
                cursor.execute(
                    f"SELECT count(*) FROM backtesting.{table} child "
                    "LEFT JOIN backtesting.backtest_runs run USING (backtest_run_id) "
                    "WHERE run.backtest_run_id IS NULL"
                )
                self.assertEqual(cursor.fetchone()[0], 0)

    def test_expired_a_and_filled_b_coexist_reconstruct_and_replay(self):
        repository = PostgresPhase5Repository(self.connection)
        summary_a = persist_backtest_bundle(repository, self.order_a)
        summary_b = persist_backtest_bundle(repository, self.order_b)
        for _ in range(2):
            persist_backtest_bundle(repository, self.order_a)
            persist_backtest_bundle(repository, self.order_b)
        bundle_a = repository.get_backtest_bundle(backtest_run_id=self.order_a.run.backtest_run_id)
        bundle_b = repository.get_backtest_bundle(backtest_run_id=self.order_b.run.backtest_run_id)
        self.assertEqual(bundle_a["orders"][0]["order_status"], "expired")
        self.assertEqual(bundle_b["orders"][0]["order_status"], "filled")
        self.assertEqual(bundle_a["orders"][0]["record_sha256"], self.order_a.orders[0].record_sha256)
        self.assertEqual(bundle_b["orders"][0]["record_sha256"], self.order_b.orders[0].record_sha256)
        self.assertEqual(bundle_a["orders"][0]["order_id"], bundle_b["orders"][0]["order_id"])
        with self.connection.cursor() as cursor:
            cursor.execute("SELECT count(*) FROM backtesting.backtest_run_memberships WHERE record_type IN ('order','position')")
            self.assertEqual(cursor.fetchone()[0], 0)
            cursor.execute("SELECT count(*) FROM backtesting.backtest_order_states WHERE order_id=%s", (self.order_a.orders[0].order_id,))
            self.assertEqual(cursor.fetchone()[0], 2)
            cursor.execute("SELECT record_sha256 FROM execution.orders WHERE order_id=%s", (self.order_a.orders[0].order_id,))
            lineage_hash = cursor.fetchone()[0]
            self.assertEqual(
                lineage_hash,
                hashlib.sha256(f"phase5-order-lineage-v1|{self.order_a.orders[0].order_id}".encode("utf-8")).hexdigest(),
            )
            cursor.execute("SELECT count(*) FROM backtesting.backtest_run_memberships WHERE backtest_run_id=%s", (self.order_a.run.backtest_run_id,))
            self.assertEqual(cursor.fetchone()[0], summary_a.memberships)
            cursor.execute("SELECT count(*) FROM backtesting.backtest_run_memberships WHERE backtest_run_id=%s", (self.order_b.run.backtest_run_id,))
            self.assertEqual(cursor.fetchone()[0], summary_b.memberships)
        self._assert_no_orphans()

    def test_long_a_flat_b_isolated_and_final_deletion_collects_lineage(self):
        repository = PostgresPhase5Repository(self.connection)
        persist_backtest_bundle(repository, self.flat_a)
        persist_backtest_bundle(repository, self.flat_b)
        bundle_a = repository.get_backtest_bundle(backtest_run_id=self.flat_a.run.backtest_run_id)
        bundle_b = repository.get_backtest_bundle(backtest_run_id=self.flat_b.run.backtest_run_id)
        self.assertEqual(bundle_a["positions"][0]["quantity"], Decimal("1"))
        self.assertEqual(bundle_b["positions"][0]["quantity"], Decimal("0"))
        self.assertEqual(bundle_a["positions"][0]["record_sha256"], self.flat_a.positions[0].record_sha256)
        self.assertEqual(bundle_b["positions"][0]["record_sha256"], self.flat_b.positions[0].record_sha256)
        a_fill_ids = {row["fill_id"] for row in bundle_a["fills"]}
        b_fill_ids = {row["fill_id"] for row in bundle_b["fills"]}
        self.assertTrue(a_fill_ids < b_fill_ids)
        self.assertEqual(a_fill_ids, {row.fill_id for row in self.flat_a.fills})
        a_snapshot_ids = {row["position_snapshot_id"] for row in bundle_a["position_snapshots"]}
        b_snapshot_ids = {row["position_snapshot_id"] for row in bundle_b["position_snapshots"]}
        self.assertTrue(a_snapshot_ids < b_snapshot_ids)
        repository.delete_backtest_run(backtest_run_id=self.flat_a.run.backtest_run_id)
        self.assertEqual(repository.get_backtest_bundle(backtest_run_id=self.flat_b.run.backtest_run_id)["positions"][0]["quantity"], Decimal("0"))
        repository.delete_backtest_run(backtest_run_id=self.flat_b.run.backtest_run_id)
        with self.connection.cursor() as cursor:
            cursor.execute("SELECT count(*) FROM execution.positions WHERE position_id=%s", (self.flat_a.positions[0].position_id,))
            self.assertEqual(cursor.fetchone()[0], 0)
            cursor.execute("SELECT count(*) FROM execution.fills WHERE fill_id=ANY(%s)", (list(a_fill_ids),))
            self.assertEqual(cursor.fetchone()[0], 0)
        self._assert_no_orphans()

    def test_long_a_reversed_b_and_delete_b_preserves_a(self):
        repository = PostgresPhase5Repository(self.connection)
        persist_backtest_bundle(repository, self.reverse_a)
        persist_backtest_bundle(repository, self.reverse_b)
        bundle_a = repository.get_backtest_bundle(backtest_run_id=self.reverse_a.run.backtest_run_id)
        bundle_b = repository.get_backtest_bundle(backtest_run_id=self.reverse_b.run.backtest_run_id)
        self.assertEqual(bundle_a["positions"][0]["quantity"], Decimal("1"))
        self.assertEqual(bundle_b["positions"][0]["quantity"], Decimal("-1"))
        shared_fill = self.reverse_a.fills[0]
        self.assertEqual(
            next(row["record_sha256"] for row in bundle_a["fills"] if row["fill_id"] == shared_fill.fill_id),
            next(row["record_sha256"] for row in bundle_b["fills"] if row["fill_id"] == shared_fill.fill_id),
        )
        b_only_fill = next(row.fill_id for row in self.reverse_b.fills if row.fill_id != shared_fill.fill_id)
        repository.delete_backtest_run(backtest_run_id=self.reverse_b.run.backtest_run_id)
        self.assertEqual(repository.get_backtest_bundle(backtest_run_id=self.reverse_a.run.backtest_run_id)["positions"][0]["quantity"], Decimal("1"))
        with self.connection.cursor() as cursor:
            cursor.execute("SELECT count(*) FROM execution.fills WHERE fill_id=%s", (shared_fill.fill_id,))
            self.assertEqual(cursor.fetchone()[0], 1)
            cursor.execute("SELECT count(*) FROM execution.fills WHERE fill_id=%s", (b_only_fill,))
            self.assertEqual(cursor.fetchone()[0], 0)
        self._assert_no_orphans()

    def test_same_run_projection_hash_conflicts_but_other_run_does_not(self):
        repository = PostgresPhase5Repository(self.connection)
        persist_backtest_bundle(repository, self.order_a)
        persist_backtest_bundle(repository, self.order_b)
        with self.connection.cursor() as cursor:
            cursor.execute(
                "UPDATE backtesting.backtest_order_states SET final_record_sha256=%s "
                "WHERE backtest_run_id=%s AND order_id=%s",
                ("f" * 64, self.order_a.run.backtest_run_id, self.order_a.orders[0].order_id),
            )
        self.connection.commit()
        with self.assertRaises(BacktestBundlePersistenceError):
            persist_backtest_bundle(repository, self.order_a)
        with self.connection.cursor() as cursor:
            cursor.execute(
                "UPDATE backtesting.backtest_order_states SET final_record_sha256=%s "
                "WHERE backtest_run_id=%s AND order_id=%s",
                (self.order_a.orders[0].record_sha256, self.order_a.run.backtest_run_id, self.order_a.orders[0].order_id),
            )
        self.connection.commit()
        persist_backtest_bundle(repository, self.order_b)
        persist_backtest_bundle(repository, self.flat_a)
        persist_backtest_bundle(repository, self.flat_b)
        with self.connection.cursor() as cursor:
            cursor.execute(
                "UPDATE backtesting.backtest_position_states SET final_record_sha256=%s "
                "WHERE backtest_run_id=%s AND position_id=%s",
                ("e" * 64, self.flat_a.run.backtest_run_id, self.flat_a.positions[0].position_id),
            )
        self.connection.commit()
        with self.assertRaises(BacktestBundlePersistenceError):
            persist_backtest_bundle(repository, self.flat_a)
        with self.connection.cursor() as cursor:
            cursor.execute(
                "UPDATE backtesting.backtest_position_states SET final_record_sha256=%s "
                "WHERE backtest_run_id=%s AND position_id=%s",
                (self.flat_a.positions[0].record_sha256, self.flat_a.run.backtest_run_id, self.flat_a.positions[0].position_id),
            )
        self.connection.commit()
        persist_backtest_bundle(repository, self.flat_b)

    def test_bundle_failure_rolls_back_projections_memberships_metrics_and_rows(self):
        repository = PostgresPhase5Repository(self.connection)
        failing = _FailingRepository(repository, "record_backtest_metric")
        with self.assertRaises(BacktestBundlePersistenceError):
            persist_backtest_bundle(failing, self.flat_b)
        self.assertIsNone(repository.get_backtest_bundle(backtest_run_id=self.flat_b.run.backtest_run_id))
        with self.connection.cursor() as cursor:
            for table in ("backtest_order_states", "backtest_position_states", "backtest_run_memberships", "backtest_metrics"):
                cursor.execute(f"SELECT count(*) FROM backtesting.{table} WHERE backtest_run_id=%s", (self.flat_b.run.backtest_run_id,))
                self.assertEqual(cursor.fetchone()[0], 0)
            cursor.execute("SELECT count(*) FROM execution.order_intents WHERE run_id=%s", (self.flat_b.run.run_id,))
            self.assertEqual(cursor.fetchone()[0], 0)
            cursor.execute("SELECT count(*) FROM execution.fills WHERE run_id=%s", (self.flat_b.run.run_id,))
            self.assertEqual(cursor.fetchone()[0], 0)
        self._assert_no_orphans()


class _FailingRepository:
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
            raise RuntimeError(f"injected fourth-audit failure at {name}")

        return fail


if __name__ == "__main__":
    unittest.main()