from __future__ import annotations

import os
import unittest
from contextlib import contextmanager
from dataclasses import replace
from datetime import timedelta
from decimal import Decimal
from uuid import uuid4

from secure_eval_wrapper.backtesting.cli import _persist_upstream_signals
from secure_eval_wrapper.data_collection.hashing import sha256_payload
from secure_eval_wrapper.data_collection.models import FundingIntervalSource, FundingRate, InstrumentType
from secure_eval_wrapper.storage.backtest_bundle import BacktestBundlePersistenceError, persist_backtest_bundle
from secure_eval_wrapper.storage.postgres.phase5_repositories import Phase5ConflictError, PostgresPhase5Repository

from test_phase5_execution import T0, bar, config, instrument, run_engine, signal


@unittest.skipUnless(os.environ.get("RUN_POSTGRES_INTEGRATION", "").lower() == "true", "real PostgreSQL integration is explicitly gated")
class RealPostgresPhase5IntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import psycopg
        cls.connection = psycopg.connect(host=os.environ["POSTGRES_HOST"], port=int(os.environ["POSTGRES_PORT"]), dbname=os.environ["POSTGRES_DB"], user=os.environ["POSTGRES_USER"], password=os.environ["POSTGRES_PASSWORD"], sslmode=os.environ.get("POSTGRES_SSLMODE", "disable"))
        key, _ = instrument(InstrumentType.PERPETUAL_SWAP)
        cls.rate = FundingRate(uuid4(), "BTC-USDT", "fixture-x", T0 + timedelta(minutes=2), Decimal("0.001"), (uuid4(),), "1h", FundingIntervalSource.METADATA_REPORTED, instrument_key=key)
        cls.signals = (signal(1, "long", kind=InstrumentType.PERPETUAL_SWAP), signal(2, "flat", kind=InstrumentType.PERPETUAL_SWAP))
        cls.result = run_engine(
            [bar(0, "100", "101", "99", "100", kind=InstrumentType.PERPETUAL_SWAP), bar(1, "100", "101", "99", "100", kind=InstrumentType.PERPETUAL_SWAP), bar(2, "110", "111", "109", "110", kind=InstrumentType.PERPETUAL_SWAP)],
            cls.signals, funding=[cls.rate], configuration=config(fees="10"),
        )
        if not cls.result.funding_payments:
            raise AssertionError("integration fixture must exercise funding persistence")
        with cls.connection.cursor() as cursor:
            cursor.execute("DELETE FROM signals.signals WHERE signal_run_id=%s", (cls.signals[0].signal_run_id,))
            cursor.execute("DELETE FROM signals.signal_runs WHERE signal_run_id=%s", (cls.signals[0].signal_run_id,))
        cls.connection.commit()
        _persist_upstream_signals(cls.connection, cls.signals)
        with cls.connection.cursor() as cursor:
            cursor.execute("DELETE FROM market_data.funding_rates WHERE provider_name = %s AND provider_instrument_id = %s AND instrument_type = %s AND funding_time_utc = %s", (key.provider_name, key.provider_instrument_id, key.instrument_type.value, cls.rate.funding_time_utc))
            cursor.execute("""
                INSERT INTO market_data.funding_rates (
                    funding_rate_id, symbol, exchange, funding_interval, funding_time_utc, rate,
                    validation_status, source_observation_ids, provenance_jsonb, provider_name,
                    provider_instrument_id, instrument_type, settlement_asset, record_sha256
                ) VALUES (%s,%s,%s,%s,%s,%s,'accepted',%s,'{}'::jsonb,%s,%s,%s,%s,%s)
                ON CONFLICT (provider_name, provider_instrument_id, instrument_type, funding_time_utc) DO NOTHING
            """, (cls.rate.funding_rate_id, cls.rate.symbol, cls.rate.exchange, cls.rate.funding_interval, cls.rate.funding_time_utc, cls.rate.rate, list(cls.rate.source_observation_ids), key.provider_name, key.provider_instrument_id, key.instrument_type.value, key.settlement_asset, sha256_payload({"funding_rate_id": cls.rate.funding_rate_id})))
        cls.connection.commit()
        cls._delete_bundle()

    @classmethod
    def tearDownClass(cls):
        cls._delete_bundle()
        cls.connection.close()

    @classmethod
    def _delete_bundle(cls):
        tables = (
            "backtesting.backtest_metrics", "backtesting.equity_curves", "backtesting.backtest_events",
            "execution.cash_ledger_entries", "execution.position_snapshots", "execution.funding_payments",
            "execution.account_snapshots", "execution.fills", "execution.orders", "execution.risk_decisions",
            "execution.positions", "execution.order_intents", "backtesting.backtest_runs",
        )
        with cls.connection.cursor() as cursor:
            for table in tables:
                column = "backtest_run_id" if table.startswith("backtesting.") else "run_id"
                if table == "backtesting.backtest_runs": column = "backtest_run_id"
                identity = cls.result.run.backtest_run_id if column == "backtest_run_id" else cls.result.run.run_id
                cursor.execute(f"DELETE FROM {table} WHERE {column} = %s", (identity,))
        cls.connection.commit()

    @classmethod
    def _counts(cls):
        queries = {
            "backtest_runs": "SELECT count(*) FROM backtesting.backtest_runs WHERE backtest_run_id=%s",
            "order_intents": "SELECT count(*) FROM execution.order_intents WHERE run_id=%s",
            "risk_decisions": "SELECT count(*) FROM execution.risk_decisions WHERE run_id=%s",
            "orders": "SELECT count(*) FROM execution.orders WHERE run_id=%s",
            "fills": "SELECT count(*) FROM execution.fills WHERE run_id=%s",
            "cash_ledger": "SELECT count(*) FROM execution.cash_ledger_entries WHERE run_id=%s",
            "funding": "SELECT count(*) FROM execution.funding_payments WHERE run_id=%s",
            "position_snapshots": "SELECT count(*) FROM execution.position_snapshots WHERE run_id=%s",
            "account_snapshots": "SELECT count(*) FROM execution.account_snapshots WHERE run_id=%s",
            "events": "SELECT count(*) FROM backtesting.backtest_events WHERE backtest_run_id=%s",
            "equity": "SELECT count(*) FROM backtesting.equity_curves WHERE backtest_run_id=%s",
            "metrics": "SELECT count(*) FROM backtesting.backtest_metrics WHERE backtest_run_id=%s",
        }
        values = {}
        with cls.connection.cursor() as cursor:
            for name, sql in queries.items():
                identity = cls.result.run.backtest_run_id if "backtest_run_id" in sql else cls.result.run.run_id
                cursor.execute(sql, (identity,)); values[name] = cursor.fetchone()[0]
        return values

    def test_real_full_bundle_writes_reads_and_idempotency(self):
        repository = PostgresPhase5Repository(self.connection)
        summary = persist_backtest_bundle(repository, self.result)
        counts = self._counts()
        self.assertEqual(counts["order_intents"], summary.order_intents)
        self.assertEqual(counts["fills"], summary.fills)
        self.assertEqual(counts["funding"], summary.funding_payments)
        self.assertEqual(counts["events"], summary.events)
        self.assertEqual(counts["metrics"], summary.metrics)
        persist_backtest_bundle(repository, self.result)
        self.assertEqual(self._counts(), counts)
        rows = repository.list_fills(order_id=self.result.orders[0].order_id)
        self.assertEqual(len(rows), 1)

    def test_real_same_identity_different_hash_conflicts_for_every_writer(self):
        repository = PostgresPhase5Repository(self.connection)
        persist_backtest_bundle(repository, self.result)
        matrix = (
            ("backtesting.backtest_runs", "backtest_run_id", self.result.run.backtest_run_id, "record_backtest_run", self.result.run),
            ("execution.order_intents", "order_intent_id", self.result.order_intents[0].order_intent_id, "record_order_intent", self.result.order_intents[0]),
            ("execution.risk_decisions", "risk_decision_id", self.result.risk_decisions[0].risk_decision_id, "record_risk_decision", self.result.risk_decisions[0]),
            ("execution.orders", "order_id", self.result.orders[0].order_id, "record_order", self.result.orders[0]),
            ("execution.fills", "fill_id", self.result.fills[0].fill_id, "record_fill", self.result.fills[0]),
            ("execution.positions", "position_id", self.result.positions[0].position_id, "upsert_position", self.result.positions[0]),
            ("execution.position_snapshots", "position_snapshot_id", self.result.position_snapshots[0].position_snapshot_id, "record_position_snapshot", self.result.position_snapshots[0]),
            ("execution.funding_payments", "funding_payment_id", self.result.funding_payments[0].funding_payment_id, "record_funding_payment", self.result.funding_payments[0]),
            ("execution.cash_ledger_entries", "cash_ledger_entry_id", self.result.cash_ledger_entries[0].cash_ledger_entry_id, "record_cash_ledger_entry", self.result.cash_ledger_entries[0]),
            ("execution.account_snapshots", "account_snapshot_id", self.result.account_snapshots[0].account_snapshot_id, "record_account_snapshot", self.result.account_snapshots[0]),
            ("backtesting.backtest_events", "backtest_event_id", self.result.events[0].execution_event_id, "record_backtest_event", self.result.events[0]),
            ("backtesting.equity_curves", "equity_curve_id", self.result.equity_curve[0].equity_curve_id, "record_equity_curve_point", self.result.equity_curve[0]),
            ("backtesting.backtest_metrics", "backtest_metric_id", self.result.metric_records[0].backtest_metric_id, "record_backtest_metric", self.result.metric_records[0]),
        )
        for table, id_column, identity, method, value in matrix:
            with self.subTest(method=method):
                with self.connection.cursor() as cursor:
                    cursor.execute(f"UPDATE {table} SET record_sha256=%s WHERE {id_column}=%s", ("f" * 64, identity))
                self.connection.commit()
                with self.assertRaises(Phase5ConflictError):
                    getattr(repository, method)(value)
                self.connection.rollback()
                with self.connection.cursor() as cursor:
                    cursor.execute(f"UPDATE {table} SET record_sha256=%s WHERE {id_column}=%s", (value.record_sha256, identity))
                self.connection.commit()

    def test_real_snapshot_and_ledger_logical_conflicts_and_legitimate_rows(self):
        repository = PostgresPhase5Repository(self.connection)
        persist_backtest_bundle(repository, self.result)
        snapshot = next(row for row in self.result.position_snapshots if row.mark_price is not None)
        conflicting_snapshot = replace(snapshot, mark_price=snapshot.mark_price + 1, position_snapshot_id=None)
        self.assertEqual(conflicting_snapshot.position_snapshot_id, snapshot.position_snapshot_id)
        with self.assertRaises(Phase5ConflictError):
            repository.record_position_snapshot(conflicting_snapshot)
        self.connection.rollback()
        ledger = self.result.cash_ledger_entries[0]
        conflicting_ledger = replace(ledger, amount=ledger.amount + 1, balance_after=ledger.balance_after + 1, cash_ledger_entry_id=None)
        self.assertEqual(conflicting_ledger.cash_ledger_entry_id, ledger.cash_ledger_entry_id)
        with self.assertRaises(Phase5ConflictError):
            repository.record_cash_ledger_entry(conflicting_ledger)
        self.connection.rollback()
        with self.connection.cursor() as cursor:
            cursor.execute("SELECT count(*) FROM execution.position_snapshots WHERE run_id=%s GROUP BY snapshot_at_utc HAVING count(DISTINCT snapshot_kind) > 1", (self.result.run.run_id,))
            self.assertTrue(cursor.fetchall())
            cursor.execute("SELECT count(*) FROM execution.cash_ledger_entries WHERE run_id=%s AND fill_id IS NOT NULL GROUP BY fill_id HAVING count(DISTINCT entry_type) > 1", (self.result.run.run_id,))
            self.assertTrue(cursor.fetchall())

    def test_real_hash_currency_account_and_risk_lineage_constraints(self):
        repository = PostgresPhase5Repository(self.connection)
        persist_backtest_bundle(repository, self.result)
        with self.connection.cursor() as cursor:
            for digest in ("z" * 64, "A" * 64):
                with self.subTest(digest=digest[:1]):
                    with self.assertRaises(Exception):
                        cursor.execute("INSERT INTO backtesting.backtest_runs (backtest_run_id, run_id, status, implementation_code_sha256) VALUES (%s,%s,'completed',%s)", (uuid4(), uuid4(), digest))
                    self.connection.rollback()
            valid_id = uuid4()
            cursor.execute("INSERT INTO backtesting.backtest_runs (backtest_run_id, run_id, status, implementation_code_sha256) VALUES (%s,%s,'completed',%s)", (valid_id, uuid4(), "a" * 64))
            cursor.execute("DELETE FROM backtesting.backtest_runs WHERE backtest_run_id=%s", (valid_id,))
        self.connection.commit()
        fill = self.result.fills[0]
        with self.connection.cursor() as cursor:
            with self.assertRaises(Exception):
                cursor.execute("UPDATE execution.fills SET fee_asset='USD' WHERE fill_id=%s", (fill.fill_id,))
        self.connection.rollback()
        with self.connection.cursor() as cursor:
            with self.assertRaises(Exception):
                cursor.execute("UPDATE execution.positions SET account_ref='different-account' WHERE position_id=%s", (self.result.positions[0].position_id,))
        self.connection.rollback()
        with self.connection.cursor() as cursor:
            cursor.execute("SELECT order_id, order_intent_id = ANY(parent_ids), order_id = ANY(parent_ids) FROM execution.risk_decisions WHERE run_id=%s AND stage='pre_fill'", (self.result.run.run_id,))
            rows = cursor.fetchall()
        self.assertTrue(rows)
        self.assertTrue(all(order_id is not None and has_intent and has_order for order_id, has_intent, has_order in rows))

    def test_real_failure_at_every_child_leaves_zero_bundle_rows(self):
        methods = (
            "record_order_intent", "record_risk_decision", "record_order", "record_fill",
            "record_cash_ledger_entry", "record_funding_payment", "upsert_position",
            "record_position_snapshot", "record_account_snapshot", "record_backtest_event",
            "record_equity_curve_point", "record_backtest_metric",
        )
        base = PostgresPhase5Repository(self.connection)
        for method in methods:
            with self.subTest(failure=method):
                self._delete_bundle()
                wrapper = _FailingRepository(base, method)
                with self.assertRaises(BacktestBundlePersistenceError):
                    persist_backtest_bundle(wrapper, self.result)
                self.assertTrue(all(value == 0 for value in self._counts().values()), self._counts())


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
        def fail(value):
            raise RuntimeError(f"injected real PostgreSQL failure at {name}")
        return fail


if __name__ == "__main__":
    unittest.main()
