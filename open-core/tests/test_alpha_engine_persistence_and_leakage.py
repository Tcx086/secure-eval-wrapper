"""Additional alpha transaction, partial-status, and funding leakage tests."""

from __future__ import annotations

import unittest
from contextlib import contextmanager
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from uuid import NAMESPACE_URL, UUID, uuid5

from secure_eval_wrapper.alpha import AlphaDataSet, AlphaEngine, AlphaEvaluationRequest, build_public_alpha_registry
from secure_eval_wrapper.data_collection.models import (
    FundingIntervalSource,
    FundingRate,
    InstrumentKey,
    InstrumentType,
    NormalizedBar,
)


START = datetime(2026, 1, 1, tzinfo=timezone.utc)
NOW = datetime(2026, 7, 10, tzinfo=timezone.utc)
REPORT = UUID("72000000-0000-0000-0000-000000000001")


def bar(index, close):
    timestamp = START + timedelta(minutes=index)
    return NormalizedBar(
        bar_id=uuid5(NAMESPACE_URL, f"persistence-bar:{index}"),
        symbol="BTC-USDT",
        exchange="synthetic",
        timeframe="1m",
        bar_open_time_utc=timestamp,
        open=Decimal(close),
        high=Decimal(close) + 1,
        low=Decimal(close) - 1,
        close=Decimal(close),
        volume=Decimal(100),
        source_observation_ids=(uuid5(NAMESPACE_URL, f"persistence-source:{index}"),),
        is_final=True,
    )


def make_request(data, *, persistence=False, fail_fast=True, symbols=("BTC-USDT",)):
    return AlphaEvaluationRequest(
        evaluation_run_id=uuid5(NAMESPACE_URL, f"persistence-run:{persistence}:{fail_fast}:{symbols}"),
        alpha_name="momentum",
        symbols=symbols,
        window_start_utc=START,
        window_end_utc=START + timedelta(hours=1),
        dataset_refs=(data.dataset_ref,),
        dataset_sha256=data.dataset_sha256,
        parameters={"lookback": 2},
        persistence_enabled=persistence,
        fail_fast=fail_fast,
    )


class FakeAlphaRepository:
    def __init__(self, fail_value=False):
        self.transactions = 0
        self.definitions = []
        self.runs = []
        self.values = []
        self.fail_value = fail_value

    @contextmanager
    def transaction(self):
        self.transactions += 1
        try:
            yield self
        except Exception:
            self.definitions.clear()
            self.runs.clear()
            self.values.clear()
            raise

    def register_alpha(self, definition):
        self.definitions.append(definition)
        return definition.alpha_id

    def record_alpha_run(self, run):
        self.runs.append(run)
        return run.alpha_run_id

    def record_alpha_value(self, value):
        if self.fail_value:
            raise RuntimeError("alpha child write failed")
        self.values.append(value)
        return value.alpha_value_id


class AlphaEnginePersistenceTests(unittest.TestCase):
    def test_persistence_uses_one_outer_transaction(self):
        data = AlphaDataSet(tuple(bar(index, str(10 + index)) for index in range(5)), "accepted", (REPORT,), "transaction-test")
        repository = FakeAlphaRepository()
        result = AlphaEngine(build_public_alpha_registry(), repository=repository, clock=lambda: NOW).evaluate(make_request(data, persistence=True), data)
        self.assertEqual(repository.transactions, 1)
        self.assertEqual(len(repository.definitions), 1)
        self.assertEqual(len(repository.runs), 1)
        self.assertEqual(len(repository.values), len(result.values))

    def test_child_failure_rolls_back_and_is_not_reported_complete(self):
        data = AlphaDataSet(tuple(bar(index, str(10 + index)) for index in range(5)), "accepted", (REPORT,), "rollback-test")
        repository = FakeAlphaRepository(fail_value=True)
        with self.assertRaisesRegex(Exception, "persistence"):
            AlphaEngine(build_public_alpha_registry(), repository=repository, clock=lambda: NOW).evaluate(make_request(data, persistence=True), data)
        self.assertEqual(repository.definitions, [])
        self.assertEqual(repository.runs, [])
        self.assertEqual(repository.values, [])

    def test_non_fail_fast_missing_symbol_is_partial_with_typed_failure(self):
        data = AlphaDataSet(tuple(bar(index, str(10 + index)) for index in range(5)), "accepted", (REPORT,), "partial-test")
        request = make_request(data, fail_fast=False, symbols=("BTC-USDT", "ETH-USDT"))
        result = AlphaEngine(build_public_alpha_registry(), clock=lambda: NOW).evaluate(request, data)
        self.assertEqual(result.run.status.value, "partial")
        self.assertEqual(result.failures[0].symbol, "ETH-USDT")
        self.assertTrue(result.values)


class FundingLeakageTests(unittest.TestCase):
    def test_future_funding_observation_does_not_change_prior_scores(self):
        key = InstrumentKey(
            provider_name="synthetic",
            exchange_name="synthetic",
            provider_instrument_id="BTC-USDT-SWAP",
            base_asset="BTC",
            quote_asset="USDT",
            settlement_asset="USDT",
            instrument_type=InstrumentType.PERPETUAL_SWAP,
            canonical_symbol="BTC-USDT-SWAP",
        )
        records = tuple(
            FundingRate(
                funding_rate_id=uuid5(NAMESPACE_URL, f"funding-leak:{index}"),
                symbol="BTC-USDT-SWAP",
                exchange="synthetic",
                funding_time_utc=START + timedelta(hours=8 * index),
                rate=rate,
                source_observation_ids=(uuid5(NAMESPACE_URL, f"funding-leak-source:{index}"),),
                funding_interval="8h",
                funding_interval_source=FundingIntervalSource.PROVIDER_REPORTED,
                instrument_key=key,
            )
            for index, rate in enumerate((Decimal("0.001"), Decimal("-0.001"), Decimal("999")))
        )
        registry = build_public_alpha_registry()
        first_data = AlphaDataSet(records[:2], "accepted", (REPORT,), "funding-base")
        second_data = AlphaDataSet(records, "accepted", (REPORT,), "funding-future")
        def evaluate(data, label):
            request = AlphaEvaluationRequest(
                evaluation_run_id=uuid5(NAMESPACE_URL, label),
                alpha_name="funding_rate_contrarian",
                symbols=("BTC-USDT-SWAP",),
                window_start_utc=START,
                window_end_utc=START + timedelta(days=2),
                dataset_refs=(data.dataset_ref,),
                dataset_sha256=data.dataset_sha256,
            )
            return AlphaEngine(registry, clock=lambda: NOW).evaluate(request, data)
        first = evaluate(first_data, "funding-first")
        second = evaluate(second_data, "funding-second")
        cutoff = records[1].funding_time_utc
        self.assertEqual(
            [(item.timestamp_utc, item.raw_score) for item in first.values],
            [(item.timestamp_utc, item.raw_score) for item in second.values if item.timestamp_utc <= cutoff],
        )


if __name__ == "__main__":
    unittest.main()
