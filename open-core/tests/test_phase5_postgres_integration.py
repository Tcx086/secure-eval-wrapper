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

from test_phase5_execution import T0, bar, instrument, run_engine, signal


@unittest.skipUnless(os.environ.get("RUN_POSTGRES_INTEGRATION", "").lower() == "true", "real PostgreSQL integration is explicitly gated")
class RealPostgresPhase5IntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import psycopg
        cls.connection = psycopg.connect(host=os.environ["POSTGRES_HOST"], port=int(os.environ["POSTGRES_PORT"]), dbname=os.environ["POSTGRES_DB"], user=os.environ["POSTGRES_USER"], password=os.environ["POSTGRES_PASSWORD"], sslmode=os.environ.get("POSTGRES_SSLMODE", "disable"))
        key, _ = instrument(InstrumentType.PERPETUAL_SWAP)
        cls.rate = FundingRate(uuid4(), "BTC-USDT", "fixture-x", T0 + timedelta(minutes=2), Decimal("0.001"), (uuid4(),), "1h", FundingIntervalSource.METADATA_REPORTED, instrument_key=key)
        cls.signals = (signal(1, "long", kind=InstrumentType.PERPETUAL_SWAP),)
        cls.result = run_engine(
            [bar(0, "100", "101", "99", "100", kind=InstrumentType.PERPETUAL_SWAP), bar(1, "100", "101", "99", "100", kind=InstrumentType.PERPETUAL_SWAP), bar(2, "100", "101", "99", "100", kind=InstrumentType.PERPETUAL_SWAP)],
            cls.signals, funding=[cls.rate],
        )
        if not cls.result.funding_payments:
            raise AssertionError("integration fixture must exercise funding persistence")
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
                cursor.execute(f"DELETE FROM {table} WHERE {column} = %s", (cls.result.run.run_id,))
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
                cursor.execute(sql, (cls.result.run.run_id,)); values[name] = cursor.fetchone()[0]
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

    def test_real_same_identity_different_hash_conflicts(self):
        repository = PostgresPhase5Repository(self.connection)
        persist_backtest_bundle(repository, self.result)
        metric = self.result.metric_records[0]
        conflicting = replace(metric, value=(metric.value or Decimal(0)) + 1)
        with self.assertRaises(Phase5ConflictError): repository.record_backtest_metric(conflicting)
        self.connection.rollback()

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
