"""Offline ranking, thresholding, combination, confidence, and pipeline tests."""

from __future__ import annotations

import unittest
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from uuid import NAMESPACE_URL, UUID, uuid5

from secure_eval_wrapper.alpha.models import AlphaRun, AlphaRunStatus, AlphaValue
from secure_eval_wrapper.signals import (
    AbsoluteThreshold,
    CombinationConfig,
    ConfidenceConfig,
    InsufficientCoveragePolicy,
    PercentileThreshold,
    RankMethod,
    RankOrder,
    RankingConfig,
    SignalDirection,
    SignalPipeline,
    SignalPipelineRequest,
    TopBottomNThreshold,
    WeightingMode,
    apply_threshold_policy,
    combine_thresholded_values,
    rank_alpha_values,
    score_confidence,
)


START = datetime(2026, 1, 1, tzinfo=timezone.utc)
NOW = datetime(2026, 7, 10, tzinfo=timezone.utc)
DATA_HASH = "a" * 64
CONFIG_HASH = "b" * 64
CODE_HASH = "c" * 64


def alpha_id(name):
    return uuid5(NAMESPACE_URL, f"alpha:{name}")


def run_id(name):
    return uuid5(NAMESPACE_URL, f"run:{name}")


def value(name, symbol, score, *, minute=0, valid=True, warmup=True):
    aid = alpha_id(name)
    rid = run_id(name)
    timestamp = START + timedelta(minutes=minute)
    return AlphaValue(
        alpha_value_id=uuid5(NAMESPACE_URL, f"value:{name}:{symbol}:{minute}"),
        alpha_id=aid,
        alpha_name=name,
        alpha_version="1.0.0",
        alpha_run_id=rid,
        symbol=symbol,
        timestamp_utc=timestamp,
        raw_score=Decimal(str(score)) if valid else None,
        warmup_complete=warmup,
        valid=valid,
        horizon="next_observation_research_input",
        source_observation_ids=(uuid5(NAMESPACE_URL, f"source:{name}:{symbol}:{minute}"),),
        dataset_sha256=DATA_HASH,
        config_sha256=CONFIG_HASH,
        implementation_sha256=CODE_HASH,
        provenance={"synthetic": True},
    )


def alpha_run(name):
    return AlphaRun(
        alpha_run_id=run_id(name),
        alpha_id=alpha_id(name),
        alpha_name=name,
        alpha_version="1.0.0",
        symbols=("BTC-USDT", "ETH-USDT", "SOL-USDT"),
        window_start_utc=START,
        window_end_utc=START + timedelta(hours=1),
        dataset_refs=("synthetic",),
        input_data_sha256=DATA_HASH,
        config_sha256=CONFIG_HASH,
        implementation_sha256=CODE_HASH,
        started_at_utc=NOW,
        completed_at_utc=NOW,
        status=AlphaRunStatus.COMPLETED,
        output_count=3,
        rejected_count=0,
        skipped_count=0,
        metadata={},
    )


class RankingTests(unittest.TestCase):
    def test_ranking_is_order_independent_and_timestamp_scoped(self):
        values = (
            value("a", "BTC-USDT", "3"),
            value("a", "ETH-USDT", "1"),
            value("a", "BTC-USDT", "-100", minute=1),
            value("a", "ETH-USDT", "100", minute=1),
        )
        config = RankingConfig(RankOrder.DESCENDING, RankMethod.ORDINAL)
        first = rank_alpha_values(values, config)
        second = rank_alpha_values(tuple(reversed(values)), config)
        self.assertEqual(first, second)
        at_zero = [item for item in first if item.alpha_value.timestamp_utc == START]
        self.assertEqual([(item.alpha_value.symbol, item.rank) for item in at_zero], [("BTC-USDT", 1), ("ETH-USDT", 2)])

    def test_dense_ties_are_stable_and_symbol_tiebreak_is_deterministic(self):
        ranked = rank_alpha_values(
            (value("a", "ETH-USDT", "2"), value("a", "BTC-USDT", "2"), value("a", "SOL-USDT", "1")),
            RankingConfig(method=RankMethod.DENSE),
        )
        self.assertEqual([(item.alpha_value.symbol, item.rank) for item in ranked], [("BTC-USDT", 1), ("ETH-USDT", 1), ("SOL-USDT", 2)])

    def test_invalid_missing_and_one_symbol_behavior(self):
        ranked = rank_alpha_values((value("a", "BTC-USDT", "1"), value("a", "ETH-USDT", "0", valid=False, warmup=False)), RankingConfig())
        self.assertEqual(len(ranked), 1)
        self.assertEqual(ranked[0].percentile, Decimal("0.5"))
        self.assertEqual(ranked[0].normalized_score, Decimal(1))

    def test_ascending_ranking(self):
        ranked = rank_alpha_values((value("a", "BTC-USDT", "3"), value("a", "ETH-USDT", "1")), RankingConfig(order=RankOrder.ASCENDING, method=RankMethod.ORDINAL))
        self.assertEqual(next(item.rank for item in ranked if item.alpha_value.symbol == "ETH-USDT"), 1)


class ThresholdTests(unittest.TestCase):
    def setUp(self):
        self.ranked = rank_alpha_values(
            (value("a", "BTC-USDT", "2"), value("a", "ETH-USDT", "0"), value("a", "SOL-USDT", "-2")),
            RankingConfig(method=RankMethod.ORDINAL),
        )

    def test_absolute_threshold_includes_exact_boundaries_and_flat(self):
        outputs = apply_threshold_policy(self.ranked, AbsoluteThreshold(Decimal("2"), Decimal("-2")))
        directions = {item.ranked.alpha_value.symbol: item.direction for item in outputs}
        self.assertEqual(directions, {"BTC-USDT": SignalDirection.LONG, "ETH-USDT": SignalDirection.FLAT, "SOL-USDT": SignalDirection.SHORT})

    def test_percentile_threshold(self):
        outputs = apply_threshold_policy(self.ranked, PercentileThreshold(Decimal("0.75"), Decimal("0.25")))
        directions = {item.ranked.alpha_value.symbol: item.direction for item in outputs}
        self.assertEqual(directions["BTC-USDT"], SignalDirection.LONG)
        self.assertEqual(directions["SOL-USDT"], SignalDirection.SHORT)

    def test_top_bottom_n_and_small_universe_overlap(self):
        outputs = apply_threshold_policy(self.ranked, TopBottomNThreshold(1, 1))
        self.assertEqual(sum(item.direction is SignalDirection.LONG for item in outputs), 1)
        one = apply_threshold_policy(self.ranked[:1], TopBottomNThreshold(2, 2))
        self.assertEqual(one[0].direction, SignalDirection.FLAT)

    def test_invalid_threshold_configs(self):
        with self.assertRaises(ValueError):
            AbsoluteThreshold(Decimal(0), Decimal("-1"))
        with self.assertRaises(ValueError):
            PercentileThreshold(Decimal("0.2"), Decimal("0.8"))
        with self.assertRaises(ValueError):
            TopBottomNThreshold(0, 0)


def components(scores, directions):
    values = tuple(value(name, "BTC-USDT", score) for name, score in scores.items())
    ranked = rank_alpha_values(values, RankingConfig())
    thresholded = apply_threshold_policy(ranked, AbsoluteThreshold(Decimal("0.1"), Decimal("-0.1")))
    if directions is None:
        return thresholded
    return tuple(type(item)(item.ranked, directions[item.ranked.alpha_value.alpha_name], item.threshold_config_sha256) for item in thresholded)


class CombinationConfidenceTests(unittest.TestCase):
    def test_equal_weight_full_agreement(self):
        outcome = combine_thresholded_values(components({"a": "1", "b": "2"}, None), CombinationConfig(expected_alpha_ids=("a@1.0.0", "b@1.0.0")))
        self.assertEqual(outcome.direction, SignalDirection.LONG)
        self.assertEqual(outcome.coverage_ratio, Decimal(1))
        self.assertEqual(outcome.agreement_ratio, Decimal(1))
        self.assertFalse(outcome.conflict)

    def test_explicit_weights_and_conflict_are_preserved(self):
        direction_map = {"a": SignalDirection.LONG, "b": SignalDirection.SHORT}
        outcome = combine_thresholded_values(
            components({"a": "1", "b": "1"}, direction_map),
            CombinationConfig(
                weighting=WeightingMode.STATIC,
                static_weights={"a@1.0.0": Decimal(2), "b@1.0.0": Decimal(1)},
                expected_alpha_ids=("a@1.0.0", "b@1.0.0"),
            ),
        )
        self.assertEqual(outcome.direction, SignalDirection.LONG)
        self.assertTrue(outcome.conflict)
        self.assertEqual(len(outcome.contributions), 2)

    def test_exact_tie_is_flat(self):
        direction_map = {"a": SignalDirection.LONG, "b": SignalDirection.SHORT}
        outcome = combine_thresholded_values(components({"a": "1", "b": "1"}, direction_map), CombinationConfig(expected_alpha_ids=("a@1.0.0", "b@1.0.0")))
        self.assertEqual(outcome.normalized_score, Decimal(0))
        self.assertEqual(outcome.direction, SignalDirection.FLAT)

    def test_insufficient_coverage_flat_and_skip(self):
        values = components({"a": "1"}, None)
        flat = combine_thresholded_values(values, CombinationConfig(expected_alpha_ids=("a@1.0.0", "b@1.0.0"), minimum_coverage_ratio=Decimal(1)))
        skipped = combine_thresholded_values(values, CombinationConfig(expected_alpha_ids=("a@1.0.0", "b@1.0.0"), minimum_coverage_ratio=Decimal(1), insufficient_coverage_policy=InsufficientCoveragePolicy.SKIP))
        self.assertEqual(flat.direction, SignalDirection.FLAT)
        self.assertTrue(skipped.skipped)

    def test_normalized_score_weighting_is_deterministic(self):
        config = CombinationConfig(weighting=WeightingMode.NORMALIZED_SCORE, expected_alpha_ids=("a@1.0.0", "b@1.0.0"))
        first = combine_thresholded_values(components({"a": "1", "b": "2"}, None), config)
        second = combine_thresholded_values(tuple(reversed(components({"a": "1", "b": "2"}, None))), config)
        self.assertEqual(first, second)

    def test_confidence_is_bounded_and_changes_with_agreement_and_coverage(self):
        config = ConfidenceConfig()
        full = combine_thresholded_values(components({"a": "1", "b": "1"}, None), CombinationConfig(expected_alpha_ids=("a@1.0.0", "b@1.0.0")))
        conflict = combine_thresholded_values(components({"a": "1", "b": "0.5"}, {"a": SignalDirection.LONG, "b": SignalDirection.SHORT}), CombinationConfig(expected_alpha_ids=("a@1.0.0", "b@1.0.0")))
        full_score = score_confidence(full, config, decision_threshold=Decimal(0))
        conflict_score = score_confidence(conflict, config, decision_threshold=Decimal(0))
        self.assertTrue(Decimal(0) <= conflict_score <= full_score <= Decimal(1))
        insufficient = combine_thresholded_values(components({"a": "1"}, None), CombinationConfig(expected_alpha_ids=("a@1.0.0", "b@1.0.0")))
        self.assertEqual(score_confidence(insufficient, config, decision_threshold=Decimal(0)), Decimal(0))

    def test_score_exactly_at_decision_threshold_is_long_and_bounded(self):
        outcome = combine_thresholded_values(components({"a": "0.5"}, None), CombinationConfig(expected_alpha_ids=("a@1.0.0",), decision_threshold=Decimal(1)))
        self.assertEqual(outcome.direction, SignalDirection.LONG)
        self.assertTrue(Decimal(0) <= score_confidence(outcome, ConfidenceConfig(), decision_threshold=Decimal(1)) <= Decimal(1))


class FakeRepository:
    def __init__(self, fail_child=False):
        self.transactions = 0
        self.runs = []
        self.signals = []
        self.fail_child = fail_child

    @contextmanager
    def transaction(self):
        self.transactions += 1
        try:
            yield self
        except Exception:
            self.runs.clear()
            self.signals.clear()
            raise

    def record_signal_run(self, run):
        self.runs.append(run)
        return run.signal_run_id

    def record_signal(self, signal):
        if self.fail_child:
            raise RuntimeError("child failed")
        self.signals.append(signal)
        return signal.signal_id


class SignalPipelineTests(unittest.TestCase):
    def _request(self, names, **kwargs):
        return SignalPipelineRequest(
            signal_run_id=uuid5(NAMESPACE_URL, "signal-run:" + ":".join(names)),
            alpha_run_ids=tuple(run_id(name) for name in names),
            symbol_universe=("BTC-USDT", "ETH-USDT"),
            window_start_utc=START,
            window_end_utc=START + timedelta(hours=1),
            ranking_config=RankingConfig(method=RankMethod.ORDINAL),
            threshold_policy=AbsoluteThreshold(Decimal("0.1"), Decimal("-0.1")),
            **kwargs,
        )

    def test_single_alpha_pipeline(self):
        run = alpha_run("a")
        values = (value("a", "BTC-USDT", "1"), value("a", "ETH-USDT", "-1"))
        result = SignalPipeline(clock=lambda: NOW).run(self._request(("a",)), (run,), values)
        self.assertEqual(result.run.output_count, 2)
        self.assertEqual((result.run.long_count, result.run.short_count), (1, 1))
        self.assertTrue(all(item.provenance["research_output_only"] for item in result.signals))

    def test_multi_alpha_combination_and_cross_sectional_point_in_time(self):
        runs = (alpha_run("a"), alpha_run("b"))
        values = (
            value("a", "BTC-USDT", "1"), value("a", "ETH-USDT", "-1"),
            value("b", "BTC-USDT", "1"), value("b", "ETH-USDT", "-1"),
        )
        request = self._request(("a", "b"), combination_config=CombinationConfig())
        result = SignalPipeline(clock=lambda: NOW).run(request, runs, tuple(reversed(values)))
        self.assertEqual(len(result.signals), 2)
        self.assertEqual(next(item.direction for item in result.signals if item.symbol == "BTC-USDT"), SignalDirection.LONG)
        self.assertEqual(len(result.signals[0].source_alpha_value_ids), 2)

    def test_missing_alpha_coverage_becomes_flat(self):
        runs = (alpha_run("a"), alpha_run("b"))
        values = (value("a", "BTC-USDT", "1"), value("b", "ETH-USDT", "1"))
        result = SignalPipeline(clock=lambda: NOW).run(self._request(("a", "b"), combination_config=CombinationConfig()), runs, values)
        self.assertTrue(all(item.direction is SignalDirection.FLAT for item in result.signals))
        self.assertTrue(all(item.provenance["insufficient_coverage"] for item in result.signals))

    def test_persistence_disabled_never_touches_repository(self):
        class NoTouch:
            def __getattr__(self, name):
                raise AssertionError(name)
        result = SignalPipeline(repository=NoTouch(), clock=lambda: NOW).run(self._request(("a",)), (alpha_run("a"),), (value("a", "BTC-USDT", "1"),))
        self.assertEqual(result.run.output_count, 1)

    def test_persistence_uses_one_outer_transaction(self):
        repository = FakeRepository()
        req = self._request(("a",), persistence_enabled=True)
        result = SignalPipeline(repository=repository, clock=lambda: NOW).run(req, (alpha_run("a"),), (value("a", "BTC-USDT", "1"),))
        self.assertEqual(repository.transactions, 1)
        self.assertEqual(len(repository.runs), 1)
        self.assertEqual(len(repository.signals), len(result.signals))

    def test_child_persistence_failure_rolls_back_and_is_typed(self):
        repository = FakeRepository(fail_child=True)
        req = self._request(("a",), persistence_enabled=True)
        with self.assertRaisesRegex(Exception, "persistence"):
            SignalPipeline(repository=repository, clock=lambda: NOW).run(req, (alpha_run("a"),), (value("a", "BTC-USDT", "1"),))
        self.assertEqual(repository.runs, [])
        self.assertEqual(repository.signals, [])


if __name__ == "__main__":
    unittest.main()
