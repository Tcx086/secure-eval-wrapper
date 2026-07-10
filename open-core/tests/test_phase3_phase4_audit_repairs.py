"""Mandatory regression proofs for the Phase 3-4 independent audit repair."""

from __future__ import annotations

import hashlib
import re
import unittest
from contextlib import contextmanager
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from uuid import NAMESPACE_URL, UUID, uuid5

from secure_eval_wrapper.alpha import (
    AlphaDataSet,
    AlphaEngine,
    AlphaEvaluationRequest,
    PointInTimeSeries,
    SeriesIdentity,
    bar_available_at_utc,
    build_public_alpha_registry,
)
from secure_eval_wrapper.alpha.examples import FundingRateContrarianAlpha, MeanReversionAlpha
from secure_eval_wrapper.alpha.examples.public import _implementation_hash
from secure_eval_wrapper.alpha.input_validation import prepare_point_in_time_series
from secure_eval_wrapper.data_collection.models import (
    FundingIntervalSource,
    FundingRate,
    InstrumentKey,
    InstrumentType,
    NormalizedBar,
)
from secure_eval_wrapper.signals import (
    AbsoluteThreshold,
    RankOrder,
    RankingConfig,
    SignalDirection,
    SignalPipeline,
    SignalPipelineRequest,
    TopBottomNThreshold,
    TopBottomOverlapPolicy,
    apply_threshold_policy,
    rank_alpha_values,
)
import secure_eval_wrapper.data_validation  # initialize existing persistence package before mapper import
from secure_eval_wrapper.storage.alpha_signal_bundle import AlphaSignalBundlePersistenceError, persist_alpha_signal_bundle
from secure_eval_wrapper.storage.postgres.alpha_signal_mappers import alpha_value_to_row, signal_component_to_row, standardized_signal_to_row
from secure_eval_wrapper.storage.postgres.mappers import normalized_bar_from_row, normalized_bar_to_row

START = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)
NOW = datetime(2026, 7, 10, tzinfo=timezone.utc)
REPORT = UUID("78000000-0000-0000-0000-000000000001")


def instrument(exchange: str, *, timeframe: str = "1m", instrument_type: InstrumentType = InstrumentType.SPOT) -> InstrumentKey:
    derivative = instrument_type is not InstrumentType.SPOT
    return InstrumentKey(
        provider_name=exchange.lower(),
        exchange_name=exchange,
        provider_instrument_id=f"BTC-USDT-{instrument_type.value}",
        base_asset="BTC",
        quote_asset="USDT",
        canonical_symbol="BTC-USDT",
        instrument_type=instrument_type,
        settlement_asset="USDT" if derivative else None,
    )


def bar(
    index: int,
    close: str,
    *,
    exchange: str = "Binance",
    timeframe: str = "1m",
    source_label: str = "source",
    final: bool | None = True,
    close_time: datetime | None | object = ...,
    instrument_type: InstrumentType = InstrumentType.SPOT,
) -> NormalizedBar:
    opened = START + timedelta(minutes=index)
    available = opened + timedelta(minutes=1) if close_time is ... else close_time
    value = Decimal(close)
    return NormalizedBar(
        bar_id=uuid5(NAMESPACE_URL, f"audit-bar:{exchange}:{timeframe}:{instrument_type.value}:{index}:{source_label}"),
        symbol="BTC-USDT",
        exchange=exchange,
        timeframe=timeframe,
        bar_open_time_utc=opened,
        open=value,
        high=value + Decimal(1),
        low=max(Decimal(0), value - Decimal(1)),
        close=value,
        volume=Decimal(100 + index),
        source_observation_ids=(uuid5(NAMESPACE_URL, f"audit-observation:{source_label}:{index}"),),
        bar_close_time_utc=available,
        is_final=final,
        provenance={"provider_name": exchange.lower(), "classification": "synthetic_public_safe"},
        instrument_key=instrument(exchange, timeframe=timeframe, instrument_type=instrument_type),
    )


def dataset(records) -> AlphaDataSet:
    return AlphaDataSet(tuple(records), "accepted", (REPORT,), "audit-regression")


def evaluate(records, *, alpha_name="momentum", parameters=None, as_of=None):
    data = dataset(records)
    request = AlphaEvaluationRequest(
        evaluation_run_id=uuid5(NAMESPACE_URL, "audit-stable-alpha-run"),
        alpha_name=alpha_name,
        symbols=("BTC-USDT",),
        window_start_utc=START,
        window_end_utc=START + timedelta(hours=4),
        dataset_refs=(data.dataset_ref,),
        dataset_sha256=data.dataset_sha256,
        parameters=parameters or {"lookback": 2},
        as_of_utc=as_of,
    )
    return AlphaEngine(build_public_alpha_registry(), clock=lambda: NOW).evaluate(request, data)


def signal_result(alpha_result):
    request = SignalPipelineRequest(
        signal_run_id=uuid5(NAMESPACE_URL, "audit-stable-signal-run"),
        alpha_run_ids=(alpha_result.run.alpha_run_id,),
        symbol_universe=("BTC-USDT",),
        window_start_utc=START,
        window_end_utc=START + timedelta(hours=4),
        ranking_config=RankingConfig(),
        threshold_policy=AbsoluteThreshold(Decimal("0.01"), Decimal("-0.01")),
    )
    return SignalPipeline(clock=lambda: NOW).run(request, (alpha_result.run,), alpha_result.values)


def historical_signature(result, cutoff):
    return tuple(
        (value.timestamp_utc, value.raw_score, value.alpha_value_id, value.eligible_input_sha256, value.record_sha256)
        for value in result.values if value.timestamp_utc <= cutoff
    )

def historical_signal_signature(alpha_result, cutoff):
    result = signal_result(alpha_result)
    return tuple(
        (signal.timestamp_utc, signal.raw_score, signal.signal_id, signal.data_sha256, signal.record_sha256)
        for signal in result.signals if signal.timestamp_utc <= cutoff
    )


class AvailabilityAndIdentityTests(unittest.TestCase):
    def test_bar_is_unavailable_before_close_and_declared_as_of_is_output_timestamp(self):
        record = bar(0, "10")
        series = PointInTimeSeries((record,))
        with self.assertRaisesRegex(ValueError, "no records are available"):
            series.eligible_as_of(record.bar_open_time_utc)
        result = evaluate((record,), parameters={"lookback": 1}, as_of=record.bar_close_time_utc)
        self.assertEqual(result.values[0].timestamp_utc, record.bar_close_time_utc)
        self.assertEqual(result.values[0].as_of_utc, record.bar_close_time_utc)

    def test_non_final_bar_is_never_eligible(self):
        final = bar(0, "10")
        partial = bar(1, "11", final=False)
        data = dataset((final, partial))
        self.assertRegex(data.dataset_sha256, r"^[0-9a-f]{64}$")
        series = prepare_point_in_time_series(data, required_data_type="ohlcv", series_identity=PointInTimeSeries((final,)).series_identity)
        self.assertEqual(series.records, (final,))
        result = evaluate((final, partial), parameters={"lookback": 1})
        self.assertNotIn(partial.bar_close_time_utc, {item.timestamp_utc for item in result.values})
        with self.assertRaisesRegex(ValueError, "non-final"):
            PointInTimeSeries((partial,))

    def test_legacy_close_derivation_supports_only_fixed_duration_timeframes(self):
        legacy = bar(0, "10", close_time=None, timeframe="5m")
        self.assertEqual(bar_available_at_utc(legacy), legacy.bar_open_time_utc + timedelta(minutes=5))
        unsupported = replace(legacy, timeframe="1M")
        with self.assertRaisesRegex(ValueError, "unsupported or calendar-dependent"):
            bar_available_at_utc(unsupported)

    def test_naive_persisted_close_time_fails(self):
        with self.assertRaisesRegex(ValueError, "timezone-aware UTC"):
            bar_available_at_utc(bar(0, "10", close_time=datetime(2026, 1, 1, 12, 1)))

    def test_validated_bar_write_and_read_mappings_preserve_close_and_finality(self):
        record = bar(0, "10")
        row = normalized_bar_to_row(record, validation_report_id=REPORT)
        self.assertEqual(row["bar_close_time_utc"], record.bar_close_time_utc)
        self.assertIs(row["is_final"], True)
        restored = normalized_bar_from_row(row)
        self.assertEqual(restored.bar_close_time_utc, record.bar_close_time_utc)
        self.assertIs(restored.is_final, True)
    def test_same_symbol_on_two_exchanges_is_two_series(self):
        result = evaluate((bar(0, "10", exchange="Binance"), bar(0, "10", exchange="OKX")), parameters={"lookback": 1})
        hashes = {item.series_identity.series_identity_sha256 for item in result.values}
        self.assertEqual(len(hashes), 2)
        self.assertEqual({item.series_identity.exchange for item in result.values}, {"Binance", "OKX"})

    def test_same_symbol_exchanges_remain_separate_through_signal_generation(self):
        records = tuple(bar(i, str(10 + i), exchange=exchange) for exchange in ("Binance", "OKX") for i in range(4))
        alpha = evaluate(records)
        signals = signal_result(alpha).signals
        final_time = START + timedelta(minutes=4)
        final = [item for item in signals if item.timestamp_utc == final_time]
        self.assertEqual(len(final), 2)
        self.assertEqual({item.series_identity.exchange for item in final}, {"Binance", "OKX"})
        self.assertEqual(len({item.signal_id for item in final}), 2)
    def test_same_symbol_on_two_timeframes_is_two_series(self):
        records = (bar(0, "10", timeframe="1m"), bar(0, "10", timeframe="5m"))
        result = evaluate(records, parameters={"lookback": 1})
        self.assertEqual({item.series_identity.timeframe for item in result.values}, {"1m", "5m"})
        self.assertEqual(len({item.series_identity.series_identity_sha256 for item in result.values}), 2)

    def test_spot_and_perpetual_series_identities_never_collide(self):
        spot = PointInTimeSeries((bar(0, "10", instrument_type=InstrumentType.SPOT),)).series_identity
        perpetual = PointInTimeSeries((bar(0, "10", instrument_type=InstrumentType.PERPETUAL_SWAP),)).series_identity
        self.assertNotEqual(spot.series_identity_sha256, perpetual.series_identity_sha256)
        self.assertIsNone(spot.settlement_asset)
        self.assertEqual(perpetual.settlement_asset, "USDT")

    def test_identity_columns_are_complete_in_alpha_and_signal_rows(self):
        alpha = evaluate(tuple(bar(i, str(10 + i)) for i in range(4)))
        emitted = next(item for item in alpha.values if item.valid)
        signal = signal_result(alpha)
        alpha_row = alpha_value_to_row(emitted)
        signal_row = standardized_signal_to_row(signal.signals[-1])
        fields = ("provider_name", "exchange", "provider_instrument_id", "canonical_symbol", "instrument_type", "timeframe", "series_identity_sha256")
        self.assertTrue(all(alpha_row[field] for field in fields))
        self.assertTrue(all(signal_row[field] for field in fields))
        self.assertEqual(alpha_row["series_identity_sha256"], signal_row["series_identity_sha256"])


class StableHashAndFormulaTests(unittest.TestCase):
    def setUp(self):
        self.base = tuple(bar(i, str(10 + i)) for i in range(6))
        self.cutoff = self.base[-1].bar_close_time_utc

    def test_future_append_preserves_historical_id_hash_and_score(self):
        future = bar(20, "999999")
        first = evaluate(self.base)
        second = evaluate((*self.base, future))
        self.assertEqual(historical_signature(first, self.cutoff), historical_signature(second, self.cutoff))
        self.assertEqual(historical_signal_signature(first, self.cutoff), historical_signal_signature(second, self.cutoff))

    def test_future_mutation_preserves_historical_id_hash_and_score(self):
        future = bar(20, "20")
        changed = replace(future, close=Decimal("999"), high=Decimal("1000"))
        first = evaluate((*self.base, future))
        second = evaluate((*self.base, changed))
        self.assertEqual(historical_signature(first, self.cutoff), historical_signature(second, self.cutoff))
        self.assertEqual(historical_signal_signature(first, self.cutoff), historical_signal_signature(second, self.cutoff))

    def test_future_deletion_preserves_historical_id_hash_and_score(self):
        future = bar(20, "20")
        first = evaluate((*self.base, future))
        second = evaluate(self.base)
        self.assertEqual(historical_signature(first, self.cutoff), historical_signature(second, self.cutoff))
        self.assertEqual(historical_signal_signature(first, self.cutoff), historical_signal_signature(second, self.cutoff))

    def test_recollection_with_new_source_ids_preserves_logical_hashes(self):
        recollected = tuple(replace(item, bar_id=uuid5(NAMESPACE_URL, f"recollected:{index}"), source_observation_ids=(uuid5(NAMESPACE_URL, f"new-source:{index}"),)) for index, item in enumerate(self.base))
        first = evaluate(self.base)
        second = evaluate(recollected)
        self.assertEqual(first.run.input_data_sha256, second.run.input_data_sha256)
        self.assertEqual(historical_signature(first, self.cutoff), historical_signature(second, self.cutoff))
        self.assertEqual(historical_signal_signature(first, self.cutoff), historical_signal_signature(second, self.cutoff))

    def test_future_data_preserves_historical_signal_ids_and_hashes(self):
        first = signal_result(evaluate(self.base))
        second = signal_result(evaluate((*self.base, bar(20, "999999"))))
        first_rows = tuple((item.timestamp_utc, item.signal_id, item.record_sha256, item.raw_score) for item in first.signals if item.timestamp_utc <= self.cutoff)
        second_rows = tuple((item.timestamp_utc, item.signal_id, item.record_sha256, item.raw_score) for item in second.signals if item.timestamp_utc <= self.cutoff)
        self.assertEqual(first_rows, second_rows)

    def test_future_funding_row_preserves_prior_ids_hashes_and_scores(self):
        key = InstrumentKey("synthetic", "Synthetic", "BTC-USDT-SWAP", "BTC", "USDT", InstrumentType.PERPETUAL_SWAP, "BTC-USDT", "USDT")
        def funding(index, rate):
            return FundingRate(
                funding_rate_id=uuid5(NAMESPACE_URL, f"funding-invariance:{index}"),
                symbol="BTC-USDT",
                exchange="Synthetic",
                funding_time_utc=START + timedelta(hours=index),
                rate=Decimal(rate),
                source_observation_ids=(uuid5(NAMESPACE_URL, f"funding-invariance-source:{index}"),),
                funding_interval="1h",
                funding_interval_source=FundingIntervalSource.METADATA_REPORTED,
                instrument_key=key,
            )
        base = tuple(funding(index, rate) for index, rate in enumerate(("0.001", "0.002", "-0.001")))
        first = evaluate(base, alpha_name="funding_rate_contrarian", parameters={"lookback": 2})
        second = evaluate((*base, funding(10, "999")), alpha_name="funding_rate_contrarian", parameters={"lookback": 2})
        self.assertEqual(historical_signature(first, base[-1].funding_time_utc), historical_signature(second, base[-1].funding_time_utc))
    def test_mean_reversion_uses_prior_only_population_window(self):
        records = (bar(0, "1"), bar(1, "2"), bar(2, "10"))
        points = MeanReversionAlpha().evaluate(PointInTimeSeries(records), {"window": 2})
        self.assertEqual(points[-1].raw_score, Decimal("-17"))
        self.assertTrue(points[-1].provenance["current_observation_excluded"])
        self.assertEqual(points[-1].provenance["standard_deviation"], "population")

    def test_mean_reversion_zero_variance_policy_is_explicit_zero(self):
        points = MeanReversionAlpha().evaluate(PointInTimeSeries((bar(0, "2"), bar(1, "2"), bar(2, "5"))), {"window": 2})
        self.assertEqual(points[-1].raw_score, Decimal(0))
        self.assertEqual(points[-1].provenance["zero_variance_score"], "0")

    def test_funding_alpha_is_realized_rolling_mean_with_warmup(self):
        key = InstrumentKey("synthetic", "Synthetic", "BTC-USDT-SWAP", "BTC", "USDT", InstrumentType.PERPETUAL_SWAP, "BTC-USDT-SWAP", "USDT")
        rates = tuple(FundingRate(
            funding_rate_id=uuid5(NAMESPACE_URL, f"audit-funding:{index}"),
            symbol="BTC-USDT-SWAP",
            exchange="Synthetic",
            funding_time_utc=START + timedelta(hours=index),
            rate=rate_value,
            source_observation_ids=(uuid5(NAMESPACE_URL, f"audit-funding-source:{index}"),),
            funding_interval=f"{index + 1}h",
            funding_interval_source=FundingIntervalSource.PROVIDER_REPORTED,
            predicted_rate=Decimal("999"),
            instrument_key=key,
        ) for index, rate_value in enumerate((Decimal("0.001"), Decimal("0.003"), Decimal("-0.002"))))
        points = FundingRateContrarianAlpha().evaluate(PointInTimeSeries(rates), {"lookback": 2})
        self.assertEqual(tuple(item.raw_score for item in points), (None, Decimal("-0.002"), Decimal("-0.0005")))
        self.assertEqual(points[0].status.value, "warmup")
        self.assertTrue(points[-1].provenance["realized_funding_only"])
        self.assertEqual(points[-1].provenance["funding_intervals"], ("2h", "3h"))

    def test_alpha_status_and_lineage_are_explicit(self):
        result = evaluate(self.base)
        self.assertEqual(result.values[0].status.value, "warmup")
        emitted = next(item for item in result.values if item.valid)
        self.assertEqual(emitted.status.value, "emitted")
        self.assertEqual(emitted.timestamp_utc, emitted.as_of_utc)
        self.assertIsNotNone(emitted.lookback_start_utc)
        self.assertIsNotNone(emitted.lookback_end_utc)
        self.assertRegex(emitted.eligible_input_sha256, r"^[0-9a-f]{64}$")
        self.assertRegex(emitted.record_sha256, r"^[0-9a-f]{64}$")

    def test_formula_and_implementation_code_hashes_are_distinct(self):
        definition = build_public_alpha_registry().resolve("momentum").definition
        self.assertNotEqual(definition.formula_sha256, definition.implementation_code_sha256)
        self.assertEqual(definition.implementation_code_sha256, _implementation_hash())
        alpha_root = Path(__file__).parents[1] / "src" / "secure_eval_wrapper" / "alpha"
        changed_digest = hashlib.sha256()
        for path in sorted(alpha_root.rglob("*.py"), key=lambda item: item.relative_to(alpha_root).as_posix()):
            relative = path.relative_to(alpha_root).as_posix()
            source = path.read_bytes()
            if relative == "examples/public.py":
                source = source.replace(b"current.close / previous.close", b"current.open / previous.close", 1)
            changed_digest.update(relative.encode("utf-8"))
            changed_digest.update(b"\0")
            changed_digest.update(source)
            changed_digest.update(b"\0")
        self.assertNotEqual(definition.implementation_code_sha256, changed_digest.hexdigest())


class AverageRankAndOverlapTests(unittest.TestCase):
    @staticmethod
    def ranked(scores, order=RankOrder.DESCENDING):
        base_result = evaluate(tuple(bar(index, str(10 + index)) for index in range(4)))
        template = next(item for item in base_result.values if item.valid)
        values = tuple(replace(
            template,
            alpha_value_id=uuid5(NAMESPACE_URL, f"rank:{label}"),
            symbol=label,
            series_identity=SeriesIdentity(label.lower(), label, label, label, InstrumentType.SPOT, "1m"),
            raw_score=Decimal(score),
        ) for label, score in scores)
        return rank_alpha_values(values, RankingConfig(order=order))

    def test_two_way_tie_has_same_average_rank_and_percentile(self):
        ranked = self.ranked((("A", "3"), ("B", "3"), ("C", "1")))
        tied = [item for item in ranked if item.alpha_value.raw_score == 3]
        self.assertEqual({item.rank for item in tied}, {Decimal("1.5")})
        self.assertEqual(len({item.percentile for item in tied}), 1)

    def test_three_way_tie_has_same_average_rank(self):
        ranked = self.ranked((("A", "3"), ("B", "3"), ("C", "3"), ("D", "1")))
        self.assertEqual({item.rank for item in ranked if item.alpha_value.raw_score == 3}, {Decimal(2)})

    def test_ascending_and_descending_ties_preserve_equality(self):
        scores = (("A", "1"), ("B", "1"), ("C", "3"))
        ascending = self.ranked(scores, RankOrder.ASCENDING)
        descending = self.ranked(scores, RankOrder.DESCENDING)
        self.assertEqual({item.rank for item in ascending if item.alpha_value.raw_score == 1}, {Decimal("1.5")})
        self.assertEqual({item.rank for item in descending if item.alpha_value.raw_score == 1}, {Decimal("2.5")})

    def test_tied_top_and_bottom_boundaries_are_equal(self):
        ranked = self.ranked((("A", "3"), ("B", "3"), ("C", "1"), ("D", "1")))
        self.assertEqual({item.percentile for item in ranked if item.alpha_value.raw_score == 3}, {Decimal("0.8333333333333333333333333333")})
        self.assertEqual({item.percentile for item in ranked if item.alpha_value.raw_score == 1}, {Decimal("0.1666666666666666666666666667")})

    def test_one_member_universe_uses_midpoint_percentile(self):
        ranked = self.ranked((("A", "3"),))
        self.assertEqual((ranked[0].rank, ranked[0].percentile), (Decimal(1), Decimal("0.5")))

    def test_overlap_fail_policy_raises(self):
        ranked = self.ranked((("A", "2"),))
        with self.assertRaisesRegex(ValueError, "top_bottom_overlap"):
            apply_threshold_policy(ranked, TopBottomNThreshold(1, 1, TopBottomOverlapPolicy.FAIL))

    def test_overlap_skip_group_policy_emits_no_rows(self):
        ranked = self.ranked((("A", "2"),))
        self.assertEqual(apply_threshold_policy(ranked, TopBottomNThreshold(1, 1, TopBottomOverlapPolicy.SKIP_GROUP)), ())

    def test_overlap_force_flat_records_reason(self):
        ranked = self.ranked((("A", "2"),))
        output = apply_threshold_policy(ranked, TopBottomNThreshold(1, 1, TopBottomOverlapPolicy.FORCE_FLAT))
        self.assertEqual(output[0].direction, SignalDirection.FLAT)
        self.assertEqual(output[0].component_disposition.value, "overlap_forced_flat")
        self.assertIn("overlap", output[0].resolution_reason)


class SignalComponentAndBundleTests(unittest.TestCase):
    def setUp(self):
        self.alpha_result = evaluate(tuple(bar(i, str(10 + i)) for i in range(5)))
        self.signal_result = signal_result(self.alpha_result)
        self.definition = build_public_alpha_registry().resolve("momentum").definition

    def test_skip_overlap_policy_persists_run_resolution_reason(self):
        request = SignalPipelineRequest(
            signal_run_id=uuid5(NAMESPACE_URL, "audit-overlap-skip-run"),
            alpha_run_ids=(self.alpha_result.run.alpha_run_id,),
            symbol_universe=("BTC-USDT",),
            window_start_utc=START,
            window_end_utc=START + timedelta(hours=4),
            ranking_config=RankingConfig(),
            threshold_policy=TopBottomNThreshold(1, 1, TopBottomOverlapPolicy.SKIP_GROUP),
        )
        result = SignalPipeline(clock=lambda: NOW).run(request, (self.alpha_result.run,), self.alpha_result.values)
        self.assertEqual(result.signals, ())
        self.assertEqual(result.run.overlap_policy, "skip_group")
        self.assertEqual(result.run.overlap_resolution_reason, "top_bottom_overlap_skip_group")
        self.assertGreater(result.run.skipped_count, 0)
    def test_signal_components_have_valid_parent_lineage_and_hashes(self):
        signal_ids = {item.signal_id for item in self.signal_result.signals}
        alpha_value_ids = {item.alpha_value_id for item in self.alpha_result.values}
        self.assertEqual(len(self.signal_result.components), len(self.signal_result.signals))
        self.assertNotEqual(self.signal_result.run.formula_sha256, self.signal_result.run.implementation_code_sha256)
        for component in self.signal_result.components:
            self.assertIn(component.signal_id, signal_ids)
            self.assertIn(component.alpha_value_id, alpha_value_ids)
            self.assertRegex(component.component_sha256, r"^[0-9a-f]{64}$")
            row = signal_component_to_row(component)
            self.assertEqual(row["component_sha256"], component.component_sha256)

    def test_signal_component_hash_is_deterministic(self):
        first = self.signal_result.components[0]
        second = replace(first, public_metadata=dict(first.public_metadata))
        self.assertEqual(first.component_sha256, second.component_sha256)

    def test_bundle_rolls_back_on_later_alpha_value_failure(self):
        self._assert_bundle_rollback("later_alpha_value")

    def test_bundle_rolls_back_on_signal_run_failure(self):
        self._assert_bundle_rollback("signal_run")

    def test_bundle_rolls_back_on_signal_failure(self):
        self._assert_bundle_rollback("signal")

    def test_bundle_rolls_back_on_signal_component_failure(self):
        self._assert_bundle_rollback("signal_component")

    def test_bundle_success_persists_all_parent_and_child_rows_once(self):
        repository = BundleRepository()
        summary = persist_alpha_signal_bundle(
            repository,
            definitions=(self.definition,),
            alpha_results=(self.alpha_result,),
            signal_results=(self.signal_result,),
        )
        self.assertEqual(repository.transactions, 1)
        self.assertEqual(summary.alpha_value_count, len(self.alpha_result.values))
        self.assertEqual(summary.signal_component_count, len(self.signal_result.components))
        self.assertEqual(len(repository.components), len(self.signal_result.components))

    def _assert_bundle_rollback(self, stage):
        repository = BundleRepository(fail_stage=stage)
        with self.assertRaises(AlphaSignalBundlePersistenceError):
            persist_alpha_signal_bundle(
                repository,
                definitions=(self.definition,),
                alpha_results=(self.alpha_result,),
                signal_results=(self.signal_result,),
            )
        self.assertEqual(repository.all_rows(), ((), (), (), (), (), ()))


class BundleRepository:
    def __init__(self, fail_stage=None):
        self.fail_stage = fail_stage
        self.transactions = 0
        self.definitions = []
        self.alpha_runs = []
        self.alpha_values = []
        self.signal_runs = []
        self.signals = []
        self.components = []

    def all_rows(self):
        return tuple(tuple(items) for items in (self.definitions, self.alpha_runs, self.alpha_values, self.signal_runs, self.signals, self.components))

    @contextmanager
    def transaction(self):
        self.transactions += 1
        snapshot = self.all_rows()
        try:
            yield self
        except Exception:
            self.definitions[:], self.alpha_runs[:], self.alpha_values[:], self.signal_runs[:], self.signals[:], self.components[:] = snapshot
            raise

    def register_alpha(self, definition):
        self.definitions.append(definition)

    def record_alpha_run(self, run):
        self.alpha_runs.append(run)

    def record_alpha_value(self, value):
        if self.fail_stage == "later_alpha_value" and len(self.alpha_values) >= 1:
            raise RuntimeError("later alpha value failed")
        self.alpha_values.append(value)

    def record_signal_run(self, run):
        if self.fail_stage == "signal_run":
            raise RuntimeError("signal run failed")
        self.signal_runs.append(run)

    def record_signal(self, signal):
        if self.fail_stage == "signal":
            raise RuntimeError("signal failed")
        self.signals.append(signal)

    def record_signal_component(self, component):
        if self.fail_stage == "signal_component":
            raise RuntimeError("signal component failed")
        self.components.append(component)


if __name__ == "__main__":
    unittest.main()
