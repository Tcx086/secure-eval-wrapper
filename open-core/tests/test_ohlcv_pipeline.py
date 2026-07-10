"""Offline end-to-end tests for the provider-neutral public OHLCV pipeline."""

from __future__ import annotations

import json
import unittest
from contextlib import contextmanager
from datetime import datetime, timezone
from unittest.mock import patch
from uuid import UUID

from secure_eval_wrapper.data_collection import (
    BinanceSpotOhlcvProvider,
    CollectionStatus,
    HttpRequest,
    HttpResponse,
    OkxPublicOhlcvProvider,
)
from secure_eval_wrapper.data_pipeline import (
    OhlcvPipeline,
    OhlcvPipelineError,
    OhlcvPipelineRequest,
    PipelineStatus,
)
from secure_eval_wrapper.data_validation import ValidationStatus


COLLECTION_RUN_ID = UUID("70000000-0000-0000-0000-000000000001")
VALIDATION_RUN_ID = UUID("70000000-0000-0000-0000-000000000002")
FIXED_NOW = datetime(2026, 7, 9, 22, 0, tzinfo=timezone.utc)
WINDOW_START = datetime(2026, 1, 1, 0, 0, tzinfo=timezone.utc)
WINDOW_END = datetime(2026, 1, 1, 0, 2, tzinfo=timezone.utc)


def _binance_kline(
    open_time_ms: int,
    close_time_ms: int,
    *,
    open_value: str = "100.00",
    high: str = "102.00",
    low: str = "99.00",
    close: str = "101.00",
) -> list[object]:
    return [
        open_time_ms,
        open_value,
        high,
        low,
        close,
        "12.50000000",
        close_time_ms,
        "1262.50000000",
        42,
        "6.00000000",
        "606.00000000",
        "0",
    ]


def _okx_candle(
    open_time_ms: int,
    *,
    open_value: str = "100.00",
    high: str = "102.00",
    low: str = "99.00",
    close: str = "101.00",
) -> list[str]:
    return [
        str(open_time_ms),
        open_value,
        high,
        low,
        close,
        "12.50000000",
        "1262.50000000",
        "1262.50000000",
        "1",
    ]


BINANCE_BARS = [
    _binance_kline(1_767_225_600_000, 1_767_225_659_999),
    _binance_kline(1_767_225_660_000, 1_767_225_719_999),
]
OKX_BARS = [
    _okx_candle(1_767_225_660_000),
    _okx_candle(1_767_225_600_000),
]


class FakeTransport:
    def __init__(self, payload: object, *, status: int = 200) -> None:
        self.response = HttpResponse(
            status=status,
            body_bytes=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
        )
        self.requests: list[HttpRequest] = []

    def send(self, request: HttpRequest) -> HttpResponse:
        self.requests.append(request)
        return self.response


def _providers(
    *,
    binance_bars: object = BINANCE_BARS,
    okx_bars: object = OKX_BARS,
    binance_status: int = 200,
):
    binance_transport = FakeTransport(binance_bars, status=binance_status)
    okx_transport = FakeTransport({"code": "0", "msg": "", "data": okx_bars})
    providers = (
        BinanceSpotOhlcvProvider(
            transport=binance_transport,
            clock=lambda: FIXED_NOW,
        ),
        OkxPublicOhlcvProvider(
            transport=okx_transport,
            clock=lambda: FIXED_NOW,
        ),
    )
    return providers, binance_transport, okx_transport


def _request(**changes: object) -> OhlcvPipelineRequest:
    values = {
        "collection_run_id": COLLECTION_RUN_ID,
        "validation_run_id": VALIDATION_RUN_ID,
        "provider_names": ("binance", "okx"),
        "symbol": "BTC-USDT",
        "timeframe": "1m",
        "start_at_utc": WINDOW_START,
        "end_at_utc": WINDOW_END,
        "limit": 2,
        "max_pages": 2,
    }
    values.update(changes)
    return OhlcvPipelineRequest(**values)


class PoisonRepository:
    def transaction(self):
        raise AssertionError("persistence-disabled pipeline touched the database")


class FakePipelineRepository:
    def __init__(self) -> None:
        self.raw = []
        self.reports = []
        self.validation_checks = []
        self.bars = []
        self.quarantine = []
        self.reconciliations = []
        self.reconciliation_checks = []
        self.transactions = 0
        self.commits = 0
        self.rollbacks = 0

    @contextmanager
    def transaction(self):
        self.transactions += 1
        try:
            yield self
        except Exception:
            self.rollbacks += 1
            raise
        else:
            self.commits += 1

    def record_raw_source_observation(self, row):
        self.raw.append(row)
        return row["observation_id"]

    def record_validation_report(self, row):
        self.reports.append(row)
        return row["validation_report_id"]

    def record_data_quality_check(self, row):
        self.validation_checks.append(row)
        return row["check_id"]

    def record_validated_bar(self, row):
        self.bars.append(row)
        return row["bar_id"]

    def record_quarantine_decision(self, row):
        self.quarantine.append(row)
        return row["quarantine_id"]

    def record_reconciliation_result(self, row):
        self.reconciliations.append(row)
        return row["reconciliation_id"]

    def record_reconciliation_check_result(self, row):
        self.reconciliation_checks.append(row)
        return row["result_id"]


class OhlcvPipelineTests(unittest.TestCase):
    def test_binance_and_okx_complete_successful_reconciliation_offline(self) -> None:
        providers, _, _ = _providers()
        pipeline = OhlcvPipeline(providers, clock=lambda: FIXED_NOW)

        with patch("socket.socket", side_effect=AssertionError("network access attempted")):
            result = pipeline.run(_request())

        self.assertEqual(result.status, PipelineStatus.SUCCEEDED)
        self.assertEqual(result.provider_names, ("binance", "okx"))
        self.assertEqual(tuple(item.provider_name for item in result.outcomes), result.provider_names)
        self.assertTrue(all(len(item.observations) == 2 for item in result.outcomes))
        self.assertTrue(
            all(
                item.validation_report is not None
                and item.validation_report.status is ValidationStatus.ACCEPTED
                for item in result.outcomes
            )
        )
        self.assertIsNotNone(result.reconciliation)
        assert result.reconciliation is not None
        self.assertEqual(result.reconciliation.metrics["price_mismatch_count"], 0)
        self.assertEqual(result.reconciliation.metrics["missing_count"], 0)
        self.assertEqual(result.errors, ())

    def test_partial_mode_records_provider_failure_and_skips_reconciliation(self) -> None:
        providers, _, _ = _providers(binance_status=500)
        result = OhlcvPipeline(providers, clock=lambda: FIXED_NOW).run(_request())

        self.assertEqual(result.status, PipelineStatus.PARTIAL)
        self.assertEqual(len(result.errors), 1)
        self.assertEqual(result.errors[0].provider_name, "binance")
        self.assertEqual(result.errors[0].stage.value, "collection")
        self.assertEqual(result.outcomes[1].provider_name, "okx")
        self.assertEqual(result.outcomes[1].status, CollectionStatus.SUCCEEDED)
        self.assertIsNone(result.reconciliation)

    def test_fail_fast_stops_before_next_provider(self) -> None:
        providers, _, okx_transport = _providers(binance_status=500)
        pipeline = OhlcvPipeline(providers, clock=lambda: FIXED_NOW)

        with self.assertRaises(OhlcvPipelineError) as raised:
            pipeline.run(_request(fail_fast=True))

        self.assertEqual(raised.exception.failure.provider_name, "binance")
        self.assertEqual(len(raised.exception.outcomes), 1)
        self.assertEqual(okx_transport.requests, [])

    def test_validation_failure_is_explicit_and_ready_for_quarantine(self) -> None:
        invalid_binance = [
            _binance_kline(
                1_767_225_600_000,
                1_767_225_659_999,
                high="98.00",
            ),
            BINANCE_BARS[1],
        ]
        providers, _, _ = _providers(binance_bars=invalid_binance)
        repository = FakePipelineRepository()
        result = OhlcvPipeline(
            providers,
            repository=repository,
            clock=lambda: FIXED_NOW,
        ).run(_request(persistence_enabled=True))

        binance = result.outcomes[0]
        self.assertIsNotNone(binance.validation_report)
        assert binance.validation_report is not None
        self.assertEqual(binance.validation_report.status, ValidationStatus.REJECTED)
        self.assertEqual(binance.validation_status, ValidationStatus.REJECTED)
        self.assertEqual(result.status, PipelineStatus.PARTIAL)
        self.assertEqual(binance.rejected_bar_count, 1)
        self.assertEqual(len(binance.bars), 2)
        self.assertEqual(len(binance.accepted_bars), 1)
        self.assertTrue(binance.eligible_for_reconciliation)
        invalid_observation_id = binance.observations[0].observation_id
        accepted_source_ids = {
            observation_id
            for bar in binance.accepted_bars
            for observation_id in bar.source_observation_ids
        }
        self.assertNotIn(invalid_observation_id, accepted_source_ids)
        self.assertEqual(len(repository.quarantine), 1)
        self.assertEqual(len(repository.bars), 3)
        self.assertIsNotNone(result.reconciliation)
        assert result.reconciliation is not None
        self.assertEqual(result.reconciliation.metrics["price_mismatch_count"], 0)
        self.assertGreater(
            result.reconciliation.metrics["missing_count"]
            + result.reconciliation.metrics["extra_bar_count"],
            0,
        )
        affected_ids = {
            observation_id
            for finding in result.reconciliation.results
            for observation_id in finding.affected_observation_ids
        }
        self.assertNotIn(invalid_observation_id, affected_ids)
        self.assertNotIn(str(invalid_observation_id), str(result.reconciliation.results))

    def test_all_rejected_provider_is_ineligible_and_reconciliation_is_skipped(self) -> None:
        invalid_binance = [
            _binance_kline(
                1_767_225_600_000,
                1_767_225_659_999,
                high="98.00",
            ),
            _binance_kline(
                1_767_225_660_000,
                1_767_225_719_999,
                high="98.00",
            ),
        ]
        providers, _, _ = _providers(binance_bars=invalid_binance)

        result = OhlcvPipeline(providers, clock=lambda: FIXED_NOW).run(_request())

        self.assertEqual(result.status, PipelineStatus.PARTIAL)
        self.assertEqual(result.outcomes[0].accepted_bars, ())
        self.assertEqual(result.outcomes[0].rejected_bar_count, 2)
        self.assertFalse(result.outcomes[0].eligible_for_reconciliation)
        self.assertTrue(result.outcomes[1].eligible_for_reconciliation)
        self.assertIsNone(result.reconciliation)

    def test_pipeline_fails_when_all_providers_have_zero_accepted_bars(self) -> None:
        invalid_binance = [
            _binance_kline(
                1_767_225_600_000,
                1_767_225_659_999,
                high="98.00",
            ),
            _binance_kline(
                1_767_225_660_000,
                1_767_225_719_999,
                high="98.00",
            ),
        ]
        invalid_okx = [
            _okx_candle(1_767_225_660_000, high="98.00"),
            _okx_candle(1_767_225_600_000, high="98.00"),
        ]
        providers, _, _ = _providers(
            binance_bars=invalid_binance,
            okx_bars=invalid_okx,
        )

        result = OhlcvPipeline(providers, clock=lambda: FIXED_NOW).run(_request())

        self.assertEqual(result.status, PipelineStatus.FAILED)
        self.assertTrue(all(not item.accepted_bars for item in result.outcomes))
        self.assertTrue(
            all(not item.eligible_for_reconciliation for item in result.outcomes)
        )
        self.assertIsNone(result.reconciliation)

    def test_warning_only_validation_remains_usable_and_succeeds(self) -> None:
        warning_binance = [
            _binance_kline(1_767_225_600_000, 1_767_225_659_999),
            _binance_kline(1_767_225_720_000, 1_767_225_779_999),
        ]
        warning_okx = [
            _okx_candle(1_767_225_720_000),
            _okx_candle(1_767_225_600_000),
        ]
        providers, _, _ = _providers(
            binance_bars=warning_binance,
            okx_bars=warning_okx,
        )
        warning_end = datetime(2026, 1, 1, 0, 3, tzinfo=timezone.utc)

        result = OhlcvPipeline(providers, clock=lambda: FIXED_NOW).run(
            _request(end_at_utc=warning_end)
        )

        self.assertEqual(result.status, PipelineStatus.SUCCEEDED)
        self.assertTrue(
            all(
                item.validation_status is ValidationStatus.ACCEPTED_WITH_WARNINGS
                for item in result.outcomes
            )
        )
        self.assertTrue(all(item.eligible_for_reconciliation for item in result.outcomes))
        self.assertTrue(all(item.rejected_bar_count == 0 for item in result.outcomes))
        self.assertIsNotNone(result.reconciliation)

    def test_reconciliation_mismatch_is_returned(self) -> None:
        mismatched_okx = [
            _okx_candle(
                1_767_225_660_000,
                open_value="110",
                high="112",
                low="109",
                close="111",
            ),
            OKX_BARS[1],
        ]
        providers, _, _ = _providers(okx_bars=mismatched_okx)
        result = OhlcvPipeline(providers, clock=lambda: FIXED_NOW).run(_request())

        self.assertIsNotNone(result.reconciliation)
        assert result.reconciliation is not None
        self.assertEqual(result.reconciliation.metrics["price_mismatch_count"], 1)
        self.assertEqual(result.reconciliation.status.value, "warning")

    def test_persistence_disabled_performs_no_database_work(self) -> None:
        providers, _, _ = _providers()
        result = OhlcvPipeline(
            providers,
            repository=PoisonRepository(),
            clock=lambda: FIXED_NOW,
        ).run(_request(persistence_enabled=False))
        self.assertIsNone(result.persistence)

    def test_persistence_enabled_uses_one_outer_transaction(self) -> None:
        providers, _, _ = _providers()
        repository = FakePipelineRepository()
        result = OhlcvPipeline(
            providers,
            repository=repository,
            clock=lambda: FIXED_NOW,
        ).run(_request(persistence_enabled=True))

        self.assertEqual(repository.transactions, 1)
        self.assertEqual(repository.commits, 1)
        self.assertEqual(repository.rollbacks, 0)
        self.assertEqual(len(repository.raw), 4)
        self.assertEqual(len(repository.reports), 2)
        self.assertEqual(len(repository.bars), 4)
        self.assertEqual(len(repository.reconciliations), 1)
        self.assertEqual(len(repository.reconciliation_checks), 5)
        self.assertIsNotNone(result.persistence)
        assert result.persistence is not None
        self.assertEqual(
            tuple(name for name, _ in result.persistence.provider_summaries),
            ("binance", "okx"),
        )

    def test_input_provider_order_does_not_change_deterministic_result(self) -> None:
        providers_a, _, _ = _providers()
        providers_b, _, _ = _providers()
        first = OhlcvPipeline(providers_a, clock=lambda: FIXED_NOW).run(
            _request(provider_names=("okx", "binance"))
        )
        second = OhlcvPipeline(
            tuple(reversed(providers_b)),
            clock=lambda: FIXED_NOW,
        ).run(_request(provider_names=("binance", "okx")))
        self.assertEqual(first, second)


if __name__ == "__main__":
    unittest.main()
