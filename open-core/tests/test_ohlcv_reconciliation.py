"""Offline tests for deterministic OHLCV cross-source reconciliation."""

from __future__ import annotations

import unittest
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from unittest.mock import patch
from uuid import NAMESPACE_URL, UUID, uuid5

from secure_eval_wrapper.data_collection import NormalizedBar, sha256_payload
from secure_eval_wrapper.data_validation import (
    CROSS_SOURCE_CLOSE_TIME_MISMATCH,
    CROSS_SOURCE_EXTRA_BAR,
    CROSS_SOURCE_MISSING_TIMESTAMP,
    CROSS_SOURCE_PRICE_MISMATCH,
    CROSS_SOURCE_VOLUME_MISMATCH,
    FindingPolicy,
    OhlcvReconciliationConfig,
    ValidationCheckStatus,
    reconcile_ohlcv_sources,
)


VALIDATION_RUN_ID = UUID("40000000-0000-0000-0000-000000000001")
FIXED_NOW = datetime(2026, 7, 9, 18, 0, tzinfo=timezone.utc)
WINDOW_START = datetime(2026, 1, 1, 0, 0, tzinfo=timezone.utc)
CHECK_ORDER = (
    CROSS_SOURCE_MISSING_TIMESTAMP,
    CROSS_SOURCE_PRICE_MISMATCH,
    CROSS_SOURCE_VOLUME_MISMATCH,
    CROSS_SOURCE_EXTRA_BAR,
    CROSS_SOURCE_CLOSE_TIME_MISMATCH,
)


def _bar(provider: str, minute: int) -> NormalizedBar:
    open_time = WINDOW_START + timedelta(minutes=minute)
    return NormalizedBar(
        bar_id=uuid5(NAMESPACE_URL, f"test-bar:{provider}:{minute}"),
        symbol="BTC-USDT",
        exchange=provider.upper(),
        timeframe="1m",
        bar_open_time_utc=open_time,
        open=Decimal("100.00"),
        high=Decimal("102.00"),
        low=Decimal("99.00"),
        close=Decimal("101.00"),
        volume=Decimal("100.00"),
        source_observation_ids=(
            uuid5(NAMESPACE_URL, f"test-observation:{provider}:{minute}"),
        ),
        bar_close_time_utc=open_time + timedelta(seconds=59, milliseconds=999),
        is_final=True,
        provenance={"provider_name": provider},
    )


def _datasets() -> dict[str, tuple[NormalizedBar, ...]]:
    return {
        "provider-a": (_bar("provider-a", 0), _bar("provider-a", 1)),
        "provider-b": (_bar("provider-b", 0), _bar("provider-b", 1)),
    }


def _result_by_type(reconciliation, check_type: str):
    return next(
        result
        for result in reconciliation.results
        if result.details["check_type"] == check_type
    )


class OfflineOhlcvReconciliationTests(unittest.TestCase):
    def test_identical_datasets_pass_without_network(self) -> None:
        with patch("socket.socket", side_effect=AssertionError("network access attempted")):
            result = reconcile_ohlcv_sources(
                validation_run_id=VALIDATION_RUN_ID,
                datasets_by_provider=_datasets(),
                clock=lambda: FIXED_NOW,
            )

        self.assertEqual(result.status, ValidationCheckStatus.PASSED)
        self.assertEqual(result.provider_names, ("provider-a", "provider-b"))
        self.assertEqual(result.symbol, "BTC-USDT")
        self.assertEqual(result.timeframe, "1m")
        self.assertEqual(
            result.metrics,
            {
                "provider_count": 2,
                "timestamp_count": 2,
                "compared_bar_count": 2,
                "missing_count": 0,
                "price_mismatch_count": 0,
                "volume_mismatch_count": 0,
                "extra_bar_count": 0,
                "close_time_mismatch_count": 0,
            },
        )
        self.assertTrue(
            all(item.status is ValidationCheckStatus.PASSED for item in result.results)
        )
        self.assertEqual(result.created_at_utc.tzinfo, timezone.utc)

    def test_missing_timestamp_obeys_warning_and_reject_policies(self) -> None:
        datasets = _datasets()
        datasets["provider-b"] = datasets["provider-b"][:1]

        warning = reconcile_ohlcv_sources(
            validation_run_id=VALIDATION_RUN_ID,
            datasets_by_provider=datasets,
            clock=lambda: FIXED_NOW,
        )
        rejected = reconcile_ohlcv_sources(
            validation_run_id=VALIDATION_RUN_ID,
            datasets_by_provider=datasets,
            config=OhlcvReconciliationConfig(
                missing_timestamp_policy=FindingPolicy.REJECT,
            ),
            clock=lambda: FIXED_NOW,
        )

        warning_result = _result_by_type(warning, CROSS_SOURCE_MISSING_TIMESTAMP)
        rejected_result = _result_by_type(rejected, CROSS_SOURCE_MISSING_TIMESTAMP)
        self.assertEqual(warning_result.status, ValidationCheckStatus.WARNING)
        self.assertEqual(rejected_result.status, ValidationCheckStatus.FAILED)
        self.assertEqual(rejected.status, ValidationCheckStatus.FAILED)
        self.assertEqual(warning.metrics["missing_count"], 1)
        finding = warning_result.details["findings"][0]
        self.assertEqual(finding["missing_providers"], ("provider-b",))
        self.assertEqual(finding["bar_open_time_utc"].tzinfo, timezone.utc)
        self.assertEqual(len(warning_result.affected_observation_ids), 1)

    def test_price_mismatch_and_ids_are_deterministic(self) -> None:
        datasets = _datasets()
        datasets["provider-b"] = (
            replace(
                datasets["provider-b"][0],
                open=Decimal("110"),
                high=Decimal("112"),
                low=Decimal("109"),
                close=Decimal("111"),
            ),
            datasets["provider-b"][1],
        )
        config = OhlcvReconciliationConfig(
            price_absolute_tolerance=Decimal("0.01"),
            price_relative_tolerance_bps=Decimal("10"),
        )

        first = reconcile_ohlcv_sources(
            validation_run_id=VALIDATION_RUN_ID,
            datasets_by_provider=datasets,
            config=config,
            clock=lambda: FIXED_NOW,
        )
        second = reconcile_ohlcv_sources(
            validation_run_id=VALIDATION_RUN_ID,
            datasets_by_provider={
                "provider-b": tuple(reversed(datasets["provider-b"])),
                "provider-a": tuple(reversed(datasets["provider-a"])),
            },
            config=config,
            clock=lambda: FIXED_NOW,
        )

        self.assertEqual(first, second)
        self.assertEqual(first.metrics["price_mismatch_count"], 1)
        mismatch = _result_by_type(first, CROSS_SOURCE_PRICE_MISMATCH)
        self.assertEqual(mismatch.status, ValidationCheckStatus.WARNING)
        comparisons = mismatch.details["findings"][0]["comparisons"]
        self.assertEqual(tuple(item["field"] for item in comparisons), (
            "open",
            "high",
            "low",
            "close",
        ))
        self.assertEqual(len(mismatch.affected_observation_ids), 2)

    def test_volume_and_close_time_mismatches_are_reported(self) -> None:
        datasets = _datasets()
        datasets["provider-b"] = (
            replace(
                datasets["provider-b"][0],
                volume=Decimal("200"),
                bar_close_time_utc=datasets["provider-b"][0].bar_close_time_utc
                + timedelta(milliseconds=1),
            ),
            datasets["provider-b"][1],
        )
        result = reconcile_ohlcv_sources(
            validation_run_id=VALIDATION_RUN_ID,
            datasets_by_provider=datasets,
            config=OhlcvReconciliationConfig(
                volume_relative_tolerance_bps=Decimal("100"),
            ),
            clock=lambda: FIXED_NOW,
        )

        self.assertEqual(result.metrics["volume_mismatch_count"], 1)
        self.assertEqual(result.metrics["close_time_mismatch_count"], 1)
        self.assertEqual(
            _result_by_type(result, CROSS_SOURCE_VOLUME_MISMATCH).status,
            ValidationCheckStatus.WARNING,
        )
        close_time_result = _result_by_type(
            result,
            CROSS_SOURCE_CLOSE_TIME_MISMATCH,
        )
        comparison = close_time_result.details["findings"][0]["comparisons"][0]
        self.assertEqual(comparison["left_provider"], "provider-a")
        self.assertEqual(comparison["right_provider"], "provider-b")

    def test_provider_specific_extra_bar_is_detected(self) -> None:
        datasets = _datasets()
        datasets["provider-a"] = (*datasets["provider-a"], _bar("provider-a", 2))

        result = reconcile_ohlcv_sources(
            validation_run_id=VALIDATION_RUN_ID,
            datasets_by_provider=datasets,
            clock=lambda: FIXED_NOW,
        )

        self.assertEqual(result.metrics["extra_bar_count"], 1)
        extra = _result_by_type(result, CROSS_SOURCE_EXTRA_BAR)
        self.assertEqual(extra.status, ValidationCheckStatus.WARNING)
        self.assertEqual(
            extra.details["findings"][0]["extra_provider"],
            "provider-a",
        )

    def test_non_utc_and_mixed_logical_datasets_are_rejected(self) -> None:
        base = _datasets()
        cases = (
            {
                **base,
                "provider-b": (
                    replace(
                        base["provider-b"][0],
                        bar_open_time_utc=datetime(2026, 1, 1),
                    ),
                ),
            },
            {
                **base,
                "provider-b": (
                    replace(base["provider-b"][0], symbol="ETH-USDT"),
                ),
            },
            {
                **base,
                "provider-b": (
                    replace(base["provider-b"][0], timeframe="5m"),
                ),
            },
        )
        expected_messages = ("timezone-aware UTC", "mixed symbols", "mixed timeframes")
        for datasets, message in zip(cases, expected_messages):
            with self.subTest(message=message):
                with self.assertRaisesRegex(ValueError, message):
                    reconcile_ohlcv_sources(
                        validation_run_id=VALIDATION_RUN_ID,
                        datasets_by_provider=datasets,
                        clock=lambda: FIXED_NOW,
                    )

    def test_result_order_and_config_hashing_are_stable(self) -> None:
        config = OhlcvReconciliationConfig()
        first = reconcile_ohlcv_sources(
            validation_run_id=VALIDATION_RUN_ID,
            datasets_by_provider=_datasets(),
            config=config,
            clock=lambda: FIXED_NOW,
        )
        second = reconcile_ohlcv_sources(
            validation_run_id=VALIDATION_RUN_ID,
            datasets_by_provider=_datasets(),
            config=config,
            clock=lambda: FIXED_NOW + timedelta(hours=1),
        )

        self.assertEqual(
            tuple(item.details["check_type"] for item in first.results),
            CHECK_ORDER,
        )
        self.assertEqual(first.reconciliation_id, second.reconciliation_id)
        self.assertEqual(
            tuple(item.result_id for item in first.results),
            tuple(item.result_id for item in second.results),
        )
        self.assertRegex(sha256_payload(dict(config.as_mapping())), r"^[0-9a-f]{64}$")


if __name__ == "__main__":
    unittest.main()
