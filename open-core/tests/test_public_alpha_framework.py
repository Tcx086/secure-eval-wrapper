"""Offline unit and anti-lookahead tests for the public alpha framework."""

from __future__ import annotations

import unittest
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from unittest.mock import patch
from uuid import NAMESPACE_URL, UUID, uuid5

from secure_eval_wrapper.alpha import (
    AlphaDataSet,
    AlphaEngine,
    AlphaEvaluationRequest,
    AlphaRegistryError,
    PointInTimeSeries,
    PublicAlphaRegistry,
    build_public_alpha_registry,
)
from secure_eval_wrapper.alpha.examples import (
    BreakoutAlpha,
    FundingRateContrarianAlpha,
    MeanReversionAlpha,
    MomentumAlpha,
    MovingAverageCrossoverAlpha,
    PriceVolumeDivergenceAlpha,
    PriorRangeClosePositionAlpha,
    RollingRangeExpansionAlpha,
    ShortTermReturnReversalAlpha,
    SignedVolumePressureAlpha,
    VolatilityAdjustedMomentumAlpha,
)
from secure_eval_wrapper.alpha.models import AlphaComputationPoint
from secure_eval_wrapper.data_collection.models import (
    FundingIntervalSource,
    FundingRate,
    InstrumentKey,
    InstrumentType,
    NormalizedBar,
)


START = datetime(2026, 1, 1, tzinfo=timezone.utc)
NOW = datetime(2026, 7, 10, tzinfo=timezone.utc)
REPORT_ID = UUID("70000000-0000-0000-0000-000000000001")


def bars(symbol="BTC-USDT", closes=("10", "11", "12", "13", "14", "15", "16", "17"), *, timeframe="1m"):
    output = []
    for index, close_text in enumerate(closes):
        close = Decimal(close_text)
        output.append(
            NormalizedBar(
                bar_id=uuid5(NAMESPACE_URL, f"bar:{symbol}:{index}:{close}"),
                symbol=symbol,
                exchange="synthetic",
                timeframe=timeframe,
                bar_open_time_utc=START + timedelta(minutes=index),
                open=close - Decimal("0.2"),
                high=close + Decimal("0.5"),
                low=close - Decimal("0.5"),
                close=close,
                volume=Decimal(100 + index * 10),
                source_observation_ids=(uuid5(NAMESPACE_URL, f"obs:{symbol}:{index}:{close}"),),
                bar_close_time_utc=START + timedelta(minutes=index + 1),
                is_final=True,
                provenance={"classification": "synthetic_public_safe"},
            )
        )
    return tuple(output)


def funding_rates():
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
    return tuple(
        FundingRate(
            funding_rate_id=uuid5(NAMESPACE_URL, f"funding:{index}"),
            symbol="BTC-USDT-SWAP",
            exchange="synthetic",
            funding_time_utc=START + timedelta(hours=8 * index),
            rate=rate,
            source_observation_ids=(uuid5(NAMESPACE_URL, f"funding-obs:{index}"),),
            funding_interval="8h",
            funding_interval_source=FundingIntervalSource.PROVIDER_REPORTED,
            instrument_key=key,
        )
        for index, rate in enumerate((Decimal("0.001"), Decimal("-0.002"), Decimal("0")))
    )


def dataset(records):
    return AlphaDataSet(tuple(records), "accepted", (REPORT_ID,), "synthetic-alpha-test")


def request(alpha_name, data, symbols=("BTC-USDT",), parameters=None, *, run_label="a"):
    return AlphaEvaluationRequest(
        evaluation_run_id=uuid5(NAMESPACE_URL, f"alpha-run:{run_label}"),
        alpha_name=alpha_name,
        symbols=tuple(symbols),
        window_start_utc=min(item.bar_open_time_utc if isinstance(item, NormalizedBar) else item.funding_time_utc for item in data.records),
        window_end_utc=max(item.bar_open_time_utc if isinstance(item, NormalizedBar) else item.funding_time_utc for item in data.records) + timedelta(days=1),
        dataset_refs=(data.dataset_ref,),
        dataset_sha256=data.dataset_sha256,
        parameters=parameters or {},
    )


def scores(alpha, records, parameters):
    series = PointInTimeSeries(records)
    validated = alpha.validate_parameters(parameters)
    return alpha.evaluate(series, validated)


class AlphaRegistryContractTests(unittest.TestCase):
    def test_default_registry_is_stable_public_and_complete(self):
        first = build_public_alpha_registry().definitions()
        second = build_public_alpha_registry().definitions()
        self.assertEqual(first, second)
        self.assertEqual(len(first), 11)
        self.assertTrue(all(item.public_example for item in first))
        self.assertEqual(tuple(item.name for item in first), tuple(sorted(item.name for item in first)))
        self.assertIn("funding", build_public_alpha_registry().categories())

    def test_duplicate_registration_is_rejected(self):
        registry = PublicAlphaRegistry()
        registry.register(MomentumAlpha())
        with self.assertRaisesRegex(AlphaRegistryError, "duplicate"):
            registry.register(MomentumAlpha())

    def test_implementation_hash_conflict_is_rejected(self):
        registry = PublicAlphaRegistry()
        original = MomentumAlpha()
        registry.register(original)

        class Conflicting(MomentumAlpha):
            DEFINITION = replace(original.definition, implementation_sha256="f" * 64)

        with self.assertRaisesRegex(AlphaRegistryError, "hash conflict"):
            registry.register(Conflicting())

    def test_resolve_version_and_unknown(self):
        registry = build_public_alpha_registry()
        self.assertEqual(registry.resolve("momentum").definition.version, "1.0.0")
        with self.assertRaises(AlphaRegistryError):
            registry.resolve("momentum", "9.9.9")

    def test_invalid_parameter_combinations_are_rejected(self):
        with self.assertRaisesRegex(ValueError, "less than"):
            MovingAverageCrossoverAlpha().validate_parameters({"short_window": 5, "long_window": 2})
        with self.assertRaisesRegex(ValueError, "unknown"):
            MomentumAlpha().validate_parameters({"secret_parameter": 1})


class AlphaCalculationTests(unittest.TestCase):
    def test_momentum_exact_decimal(self):
        points = scores(MomentumAlpha(), bars(closes=("10", "11", "12")), {"lookback": 2})
        self.assertFalse(points[1].warmup_complete)
        self.assertEqual(points[2].raw_score, Decimal("0.2"))

    def test_moving_average_crossover_exact_decimal(self):
        points = scores(MovingAverageCrossoverAlpha(), bars(closes=("1", "2", "3")), {"short_window": 2, "long_window": 3})
        self.assertEqual(points[-1].raw_score, Decimal("2.5") / Decimal("2") - 1)

    def test_breakout_excludes_current_bar(self):
        records = bars(closes=("10", "10", "20"))
        points = scores(BreakoutAlpha(), records, {"lookback": 2})
        self.assertGreater(points[-1].raw_score, 0)
        self.assertTrue(points[-1].provenance["current_bar_excluded"])

    def test_mean_reversion_trailing_z_score_and_zero_variance(self):
        points = scores(MeanReversionAlpha(), bars(closes=("1", "2", "3")), {"window": 3})
        self.assertLess(points[-1].raw_score, 0)
        flat = scores(MeanReversionAlpha(), bars(closes=("2", "2", "2")), {"window": 3})
        self.assertEqual(flat[-1].raw_score, Decimal(0))
        self.assertTrue(flat[-1].valid)

    def test_formulaic_examples_produce_finite_outputs(self):
        implementations = (
            ShortTermReturnReversalAlpha(),
            PriorRangeClosePositionAlpha(),
            VolatilityAdjustedMomentumAlpha(),
            PriceVolumeDivergenceAlpha(),
            RollingRangeExpansionAlpha(),
            SignedVolumePressureAlpha(),
        )
        for implementation in implementations:
            points = scores(implementation, bars(), {})
            valid = [item for item in points if item.valid]
            self.assertTrue(valid, implementation.definition.name)
            self.assertTrue(all(item.raw_score is not None and item.raw_score.is_finite() for item in valid))

    def test_funding_positive_negative_and_zero(self):
        points = scores(FundingRateContrarianAlpha(), funding_rates(), {})
        self.assertEqual(tuple(item.raw_score for item in points), (Decimal("-0.001"), Decimal("0.002"), Decimal("0")))

    def test_input_order_is_deterministically_sorted(self):
        ordered = bars()
        shuffled = tuple(reversed(ordered))
        self.assertEqual(
            tuple(item.raw_score for item in scores(MomentumAlpha(), ordered, {"lookback": 2})),
            tuple(item.raw_score for item in scores(MomentumAlpha(), shuffled, {"lookback": 2})),
        )

    def test_nonfinite_output_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "finite"):
            AlphaComputationPoint(START, Decimal("NaN"), True, True, (), (), {})


class PointInTimeControlTests(unittest.TestCase):
    def test_duplicate_timestamps_rejected(self):
        records = bars(closes=("1", "2"))
        with self.assertRaisesRegex(ValueError, "duplicate"):
            PointInTimeSeries((records[0], replace(records[1], bar_open_time_utc=records[0].bar_open_time_utc)))

    def test_mixed_symbols_and_timeframes_rejected(self):
        with self.assertRaisesRegex(ValueError, "one symbol"):
            PointInTimeSeries((bars("BTC-USDT")[0], bars("ETH-USDT")[1]))
        with self.assertRaisesRegex(ValueError, "timeframes"):
            PointInTimeSeries((bars(timeframe="1m")[0], bars(timeframe="5m")[1]))

    def test_nonfinal_and_naive_timestamps_rejected(self):
        with self.assertRaisesRegex(ValueError, "non-final"):
            PointInTimeSeries((replace(bars()[0], is_final=False),))
        with self.assertRaisesRegex(ValueError, "timezone-aware UTC"):
            PointInTimeSeries((replace(bars()[0], bar_open_time_utc=datetime(2026, 1, 1)),))

    def test_validation_gate_required(self):
        with self.assertRaisesRegex(ValueError, "accepted"):
            AlphaDataSet(bars(), "rejected", (REPORT_ID,), "bad")

    def test_funding_requires_grounded_interval(self):
        record = replace(funding_rates()[0], funding_interval=None, funding_interval_source=FundingIntervalSource.UNAVAILABLE)
        with self.assertRaisesRegex(ValueError, "grounded"):
            PointInTimeSeries((record,))


class AntiLookaheadEngineTests(unittest.TestCase):
    def _run(self, name, records, parameters, label):
        data = dataset(records)
        engine = AlphaEngine(build_public_alpha_registry(), clock=lambda: NOW)
        return engine.evaluate(request(name, data, parameters=parameters, run_label=label), data)

    def test_append_extreme_future_bar_does_not_change_history(self):
        base = bars(closes=("10", "11", "12", "13", "14", "15"))
        future = replace(bars(closes=("1000000",))[0], bar_id=uuid5(NAMESPACE_URL, "future"), bar_open_time_utc=START + timedelta(minutes=6), source_observation_ids=(uuid5(NAMESPACE_URL, "future-obs"),))
        first = self._run("momentum", base, {"lookback": 2}, "future-a")
        second = self._run("momentum", (*base, future), {"lookback": 2}, "future-b")
        cutoff = base[-1].bar_open_time_utc
        self.assertEqual(
            [(item.timestamp_utc, item.raw_score) for item in first.values if item.timestamp_utc <= cutoff],
            [(item.timestamp_utc, item.raw_score) for item in second.values if item.timestamp_utc <= cutoff],
        )

    def test_mutating_data_after_t_does_not_change_outputs_at_or_before_t(self):
        base = bars()
        cutoff = base[4].bar_open_time_utc
        changed = tuple(replace(item, close=Decimal("99999"), high=Decimal("100000")) if item.bar_open_time_utc > cutoff else item for item in base)
        first = self._run("trailing_mean_reversion", base, {"window": 3}, "mutate-a")
        second = self._run("trailing_mean_reversion", changed, {"window": 3}, "mutate-b")
        self.assertEqual(
            [(item.timestamp_utc, item.raw_score) for item in first.values if item.timestamp_utc <= cutoff],
            [(item.timestamp_utc, item.raw_score) for item in second.values if item.timestamp_utc <= cutoff],
        )

    def test_engine_outputs_deterministic_ids_hashes_and_explicit_warmup(self):
        data = dataset(bars())
        engine = AlphaEngine(build_public_alpha_registry(), clock=lambda: NOW)
        req = request("momentum", data, parameters={"lookback": 3}, run_label="deterministic")
        first = engine.evaluate(req, data)
        second = engine.evaluate(req, data)
        self.assertEqual(first, second)
        self.assertEqual(first.run.skipped_count, 3)
        self.assertTrue(all(not item.valid for item in first.values[:3]))
        self.assertTrue(all(item.valid for item in first.values[3:]))

    def test_dataset_hash_mismatch_fails_fast(self):
        data = dataset(bars())
        bad = replace(request("momentum", data), dataset_sha256="f" * 64)
        with self.assertRaisesRegex(Exception, "dataset_sha256"):
            AlphaEngine(build_public_alpha_registry(), clock=lambda: NOW).evaluate(bad, data)

    def test_persistence_disabled_does_not_touch_repository(self):
        class NoTouch:
            def __getattr__(self, name):
                raise AssertionError(f"repository touched: {name}")

        data = dataset(bars())
        result = AlphaEngine(build_public_alpha_registry(), repository=NoTouch(), clock=lambda: NOW).evaluate(request("momentum", data), data)
        self.assertTrue(result.values)

    def test_import_has_no_database_or_network_activity(self):
        with patch("socket.socket", side_effect=AssertionError("network attempted")):
            registry = build_public_alpha_registry()
        self.assertTrue(registry.definitions())


if __name__ == "__main__":
    unittest.main()

