"""Offline tests for Phase 2C OHLCV normalization and validation."""

from __future__ import annotations

import re
import unittest
from dataclasses import replace
from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import patch
from uuid import UUID

from secure_eval_wrapper.data_collection import (
    DataRequest,
    MarketDataType,
    SampleProvider,
    normalize_ohlcv_observations,
)
from secure_eval_wrapper.data_validation import (
    DUPLICATED_TIMESTAMPS,
    INVALID_OHLC_RELATIONSHIP,
    INVALID_VOLUME,
    MISSING_BARS,
    NON_MONOTONIC_TIMESTAMPS,
    PARTIAL_CANDLE,
    FindingPolicy,
    OhlcvValidationConfig,
    QuarantineReason,
    ValidationCheckStatus,
    ValidationStatus,
    map_quarantine_reasons,
    validate_ohlcv_bars,
)


COLLECTION_RUN_ID = UUID("20000000-0000-0000-0000-000000000001")
VALIDATION_RUN_ID = UUID("20000000-0000-0000-0000-000000000002")
FIXED_NOW = datetime(2026, 7, 9, 14, 0, tzinfo=timezone.utc)
LATER_NOW = datetime(2026, 7, 9, 15, 0, tzinfo=timezone.utc)
SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")


def _result_by_type(report: object, check_type: str) -> object:
    for result in report.results:  # type: ignore[attr-defined]
        if result.details["check_type"] == check_type:
            return result
    raise AssertionError(f"missing validation result for {check_type}")


class OfflineOhlcvNormalizationValidationTests(unittest.TestCase):
    def setUp(self) -> None:
        provider = SampleProvider(clock=lambda: FIXED_NOW)
        request = DataRequest(
            collection_run_id=COLLECTION_RUN_ID,
            provider_name="sample_file",
            data_type=MarketDataType.OHLCV,
            symbols=("btc/usdt",),
            timeframe="1m",
        )
        with patch("socket.socket", side_effect=AssertionError("network access attempted")):
            self.observations = provider.fetch_ohlcv(request)
            self.bars = normalize_ohlcv_observations(self.observations)

    def _validate(
        self,
        bars: object | None = None,
        *,
        config: OhlcvValidationConfig | None = None,
        clock: object | None = None,
    ) -> object:
        return validate_ohlcv_bars(
            validation_run_id=VALIDATION_RUN_ID,
            dataset_ref="synthetic-btc-usdt-1m",
            bars=self.bars if bars is None else bars,  # type: ignore[arg-type]
            config=config,
            clock=(lambda: FIXED_NOW) if clock is None else clock,  # type: ignore[arg-type]
        )

    def test_valid_fixture_normalizes_and_passes(self) -> None:
        report = self._validate()

        self.assertEqual(len(self.bars), 3)
        self.assertEqual(self.bars[0].symbol, "BTC-USDT")
        self.assertEqual(self.bars[0].open, Decimal("100.00"))
        self.assertEqual(self.bars[0].bar_open_time_utc.tzinfo, timezone.utc)
        self.assertEqual(
            self.bars[0].source_observation_ids,
            (self.observations[0].observation_id,),
        )
        self.assertEqual(
            self.bars[0].provenance["source_sha256"],
            self.observations[0].source_sha256,
        )
        self.assertEqual(report.status, ValidationStatus.ACCEPTED)
        self.assertEqual((report.accepted_count, report.rejected_count), (3, 0))
        self.assertEqual(report.warning_count, 0)
        self.assertTrue(
            all(result.status is ValidationCheckStatus.PASSED for result in report.results)
        )
        self.assertEqual(report.source_hashes, tuple(sorted(report.source_hashes)))
        self.assertRegex(report.tolerance_config_sha256, SHA256_PATTERN)
        self.assertRegex(report.report_sha256, SHA256_PATTERN)

    def test_invalid_ohlc_fails_and_maps_quarantine_reason(self) -> None:
        invalid = replace(self.bars[1], high=Decimal("99"))
        report = self._validate((self.bars[0], invalid, self.bars[2]))

        result = _result_by_type(report, INVALID_OHLC_RELATIONSHIP)
        self.assertEqual(result.status, ValidationCheckStatus.FAILED)
        self.assertEqual(report.status, ValidationStatus.REJECTED)
        self.assertEqual((report.accepted_count, report.rejected_count), (2, 1))
        self.assertEqual(
            map_quarantine_reasons(report)[self.bars[1].source_observation_ids[0]],
            QuarantineReason.INVALID_OHLC_RELATIONSHIP,
        )

    def test_duplicate_timestamp_fails(self) -> None:
        duplicate = replace(
            self.bars[1],
            bar_open_time_utc=self.bars[0].bar_open_time_utc,
        )
        report = self._validate((self.bars[0], duplicate, self.bars[2]))

        result = _result_by_type(report, DUPLICATED_TIMESTAMPS)
        self.assertEqual(result.status, ValidationCheckStatus.FAILED)
        self.assertEqual(report.status, ValidationStatus.REJECTED)
        reasons = map_quarantine_reasons(report)
        self.assertEqual(
            reasons[self.bars[0].source_observation_ids[0]],
            QuarantineReason.DUPLICATE_RECORD,
        )
        self.assertEqual(
            reasons[self.bars[1].source_observation_ids[0]],
            QuarantineReason.DUPLICATE_RECORD,
        )

    def test_missing_bar_warning_and_reject_policies_are_deterministic(self) -> None:
        bars_with_gap = (self.bars[0], self.bars[2])
        warning_report = self._validate(bars_with_gap)
        reject_report = self._validate(
            bars_with_gap,
            config=OhlcvValidationConfig(
                missing_bar_policy=FindingPolicy.REJECT,
            ),
        )

        warning_result = _result_by_type(warning_report, MISSING_BARS)
        self.assertEqual(warning_result.status, ValidationCheckStatus.WARNING)
        self.assertEqual(warning_result.details["missing_count"], 1)
        self.assertEqual(warning_report.status, ValidationStatus.ACCEPTED_WITH_WARNINGS)
        self.assertEqual(
            (warning_report.accepted_count, warning_report.rejected_count),
            (2, 0),
        )

        reject_result = _result_by_type(reject_report, MISSING_BARS)
        self.assertEqual(reject_result.status, ValidationCheckStatus.FAILED)
        self.assertEqual(reject_report.status, ValidationStatus.REJECTED)
        self.assertEqual((reject_report.accepted_count, reject_report.rejected_count), (0, 2))
        self.assertEqual(
            set(map_quarantine_reasons(reject_report).values()),
            {QuarantineReason.MISSING_REQUIRED_DATA},
        )

    def test_other_required_checks_handle_order_volume_and_partial_flags(self) -> None:
        non_monotonic = self._validate((self.bars[1], self.bars[0], self.bars[2]))
        negative_volume = self._validate(
            (replace(self.bars[0], volume=Decimal("-1")), *self.bars[1:])
        )
        partial = self._validate(
            (replace(self.bars[0], is_final=False), *self.bars[1:])
        )

        self.assertEqual(
            _result_by_type(non_monotonic, NON_MONOTONIC_TIMESTAMPS).status,
            ValidationCheckStatus.FAILED,
        )
        self.assertEqual(
            _result_by_type(negative_volume, INVALID_VOLUME).status,
            ValidationCheckStatus.FAILED,
        )
        self.assertEqual(
            _result_by_type(partial, PARTIAL_CANDLE).status,
            ValidationCheckStatus.FAILED,
        )

    def test_report_hash_is_stable_across_creation_times(self) -> None:
        first = self._validate(clock=lambda: FIXED_NOW)
        second = self._validate(clock=lambda: LATER_NOW)

        self.assertNotEqual(first.created_at_utc, second.created_at_utc)
        self.assertEqual(first.validation_report_id, second.validation_report_id)
        self.assertEqual(first.tolerance_config_sha256, second.tolerance_config_sha256)
        self.assertEqual(first.report_sha256, second.report_sha256)


if __name__ == "__main__":
    unittest.main()
