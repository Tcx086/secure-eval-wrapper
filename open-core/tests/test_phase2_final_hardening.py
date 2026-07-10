"""Offline regression tests for Phase 2 final hardening."""

from __future__ import annotations

import json
import unittest
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from uuid import UUID

from secure_eval_wrapper.data_collection import (
    BinanceSpotPublicProvider,
    BinanceUsdmPublicProvider,
    DataRequest,
    FundingIntervalSource,
    HttpRequest,
    HttpResponse,
    MarketDataType,
    binance_usdm_instrument_key,
    normalize_funding_rate_observations,
    normalize_trade_observations,
)
from secure_eval_wrapper.data_validation import validate_funding_rates
from secure_eval_wrapper.data_validation.funding import (
    FUNDING_INTERVAL_MISMATCH,
    FUNDING_TIMESTAMP_GAP,
)
from secure_eval_wrapper.data_validation.models import ValidationCheckStatus
from secure_eval_wrapper.storage.postgres.mappers import (
    funding_rate_to_row,
    normalized_trade_to_row,
)
from secure_eval_wrapper.storage.postgres.repositories import PostgresMarketDataRepository


ROOT = Path(__file__).resolve().parents[2]
RESPONSES = json.loads(
    (ROOT / "open-core/data/sample/public_market_data_bundle_sample.json").read_text(
        encoding="utf-8"
    )
)["responses"]
TRADE_START = datetime(2026, 1, 1, tzinfo=timezone.utc)
TRADE_END = TRADE_START + timedelta(minutes=1)
FUNDING_START = TRADE_START - timedelta(hours=1)
FUNDING_END = TRADE_START + timedelta(hours=25)
NOW_A = datetime(2026, 7, 9, 20, 0, tzinfo=timezone.utc)
NOW_B = datetime(2026, 7, 10, 1, 0, tzinfo=timezone.utc)
RUN_A = UUID("85000000-0000-0000-0000-000000000001")
RUN_B = UUID("85000000-0000-0000-0000-000000000002")
REPORT_A = UUID("85000000-0000-0000-0000-000000000003")
REPORT_B = UUID("85000000-0000-0000-0000-000000000004")
USDM_KEY = binance_usdm_instrument_key(
    "BTCUSDT", base_asset="BTC", quote_asset="USDT", settlement_asset="USDT"
)


class QueueTransport:
    def __init__(self, *payloads: object) -> None:
        self.payloads = list(payloads)
        self.requests: list[HttpRequest] = []

    def send(self, request: HttpRequest) -> HttpResponse:
        self.requests.append(request)
        if not self.payloads:
            raise AssertionError(f"unexpected transport request: {request.url}")
        return HttpResponse(200, json.dumps(self.payloads.pop(0)).encode(), {})


class ReplayCursor:
    def __init__(self, rows: list[object]) -> None:
        self.rows = rows
        self.description = None

    def execute(self, sql, params) -> None:
        self.last = (sql, params)

    def fetchone(self):
        return self.rows.pop(0) if self.rows else None

    def close(self) -> None:
        return None


class ReplayConnection:
    def __init__(self, rows: list[object]) -> None:
        self.cursor_instance = ReplayCursor(rows)
        self.commits = 0
        self.rollbacks = 0

    def cursor(self):
        return self.cursor_instance

    def commit(self) -> None:
        self.commits += 1

    def rollback(self) -> None:
        self.rollbacks += 1


def _trade(run_id: UUID, now: datetime):
    provider = BinanceSpotPublicProvider(
        transport=QueueTransport(RESPONSES["binance_spot_agg_trades"]),
        max_pages=1,
        clock=lambda: now,
    )
    observations = provider.fetch_trades(
        DataRequest(
            run_id,
            "binance",
            MarketDataType.TRADES,
            ("BTC-USDT",),
            start_at_utc=TRADE_START,
            end_at_utc=TRADE_END,
            limit=3,
            max_pages=1,
        )
    )
    return observations, normalize_trade_observations(observations)


def _funding(
    run_id: UUID,
    now: datetime,
    *,
    info: object,
    rows: object,
):
    provider = BinanceUsdmPublicProvider(
        transport=QueueTransport(info, rows),
        max_pages=1,
        clock=lambda: now,
    )
    observations = provider.fetch_funding_rates(
        DataRequest(
            run_id,
            "binance_usdm",
            MarketDataType.FUNDING_RATES,
            (),
            start_at_utc=FUNDING_START,
            end_at_utc=FUNDING_END,
            limit=10,
            max_pages=1,
            instruments=(USDM_KEY,),
        )
    )
    return observations, normalize_funding_rate_observations(observations)


def _check(report, check_type: str):
    return next(item for item in report.results if item.details["check_type"] == check_type)


class StableEventHashTests(unittest.TestCase):
    def test_trade_refetch_has_stable_id_hash_and_idempotent_database_retry(self):
        observations_a, trades_a = _trade(RUN_A, NOW_A)
        observations_b, trades_b = _trade(RUN_B, NOW_B)
        row_a = normalized_trade_to_row(trades_a[0], validation_report_id=REPORT_A)
        row_b = normalized_trade_to_row(trades_b[0], validation_report_id=REPORT_B)

        self.assertNotEqual(observations_a[0].observation_id, observations_b[0].observation_id)
        self.assertNotEqual(trades_a[0].provenance, trades_b[0].provenance)
        self.assertEqual(trades_a[0].trade_id, trades_b[0].trade_id)
        self.assertEqual(row_a["record_sha256"], row_b["record_sha256"])

        connection = ReplayConnection([
            (row_a["trade_id"], row_a["record_sha256"]),
            None,
            (row_a["trade_id"], row_a["record_sha256"]),
        ])
        repository = PostgresMarketDataRepository(connection)
        self.assertEqual(repository.record_validated_trade(row_a), row_a["trade_id"])
        self.assertEqual(repository.record_validated_trade(row_b), row_a["trade_id"])
        self.assertEqual(connection.rollbacks, 0)

    def test_trade_economic_changes_still_change_hash_and_conflict(self):
        _, trades = _trade(RUN_A, NOW_A)
        row = normalized_trade_to_row(trades[0], validation_report_id=REPORT_A)
        changed = normalized_trade_to_row(
            replace(trades[0], price=Decimal("999")),
            validation_report_id=REPORT_B,
        )
        changed_time = normalized_trade_to_row(
            replace(trades[0], traded_at_utc=trades[0].traded_at_utc + timedelta(seconds=1)),
            validation_report_id=REPORT_B,
        )
        self.assertNotEqual(row["record_sha256"], changed["record_sha256"])
        self.assertNotEqual(row["record_sha256"], changed_time["record_sha256"])
        connection = ReplayConnection([None, (row["trade_id"], row["record_sha256"])])
        with self.assertRaisesRegex(RuntimeError, "record_sha256 differs"):
            PostgresMarketDataRepository(connection).record_validated_trade(changed)

    def test_funding_refetch_has_stable_id_hash_and_idempotent_database_retry(self):
        info = RESPONSES["binance_usdm_funding_info"]
        rows = RESPONSES["binance_usdm_funding"]
        observations_a, rates_a = _funding(RUN_A, NOW_A, info=info, rows=rows)
        observations_b, rates_b = _funding(RUN_B, NOW_B, info=info, rows=rows)
        row_a = funding_rate_to_row(rates_a[0], validation_report_id=REPORT_A)
        row_b = funding_rate_to_row(rates_b[0], validation_report_id=REPORT_B)

        self.assertNotEqual(observations_a[0].observation_id, observations_b[0].observation_id)
        self.assertNotEqual(rates_a[0].provenance, rates_b[0].provenance)
        self.assertEqual(rates_a[0].funding_rate_id, rates_b[0].funding_rate_id)
        self.assertEqual(row_a["record_sha256"], row_b["record_sha256"])

        connection = ReplayConnection([
            (row_a["funding_rate_id"], row_a["record_sha256"]),
            None,
            (row_a["funding_rate_id"], row_a["record_sha256"]),
        ])
        repository = PostgresMarketDataRepository(connection)
        self.assertEqual(repository.record_funding_rate(row_a), row_a["funding_rate_id"])
        self.assertEqual(repository.record_funding_rate(row_b), row_a["funding_rate_id"])
        self.assertEqual(connection.rollbacks, 0)

    def test_funding_economic_changes_still_change_hash_and_conflict(self):
        _, rates = _funding(
            RUN_A,
            NOW_A,
            info=RESPONSES["binance_usdm_funding_info"],
            rows=RESPONSES["binance_usdm_funding"],
        )
        row = funding_rate_to_row(rates[0], validation_report_id=REPORT_A)
        changed = funding_rate_to_row(
            replace(rates[0], rate=Decimal("0.123")),
            validation_report_id=REPORT_B,
        )
        changed_time = funding_rate_to_row(
            replace(rates[0], funding_time_utc=rates[0].funding_time_utc + timedelta(hours=1)),
            validation_report_id=REPORT_B,
        )
        self.assertNotEqual(row["record_sha256"], changed["record_sha256"])
        self.assertNotEqual(row["record_sha256"], changed_time["record_sha256"])
        connection = ReplayConnection([None, (row["funding_rate_id"], row["record_sha256"])])
        with self.assertRaisesRegex(RuntimeError, "record_sha256 differs"):
            PostgresMarketDataRepository(connection).record_funding_rate(changed)


class FundingIntervalAdapterTests(unittest.TestCase):
    def _report(self, rates, label: str):
        return validate_funding_rates(
            validation_run_id=REPORT_A,
            dataset_ref=label,
            funding_rates=rates,
            window_start_utc=FUNDING_START,
            window_end_utc=FUNDING_END,
            clock=lambda: NOW_A,
        )

    def test_normal_consecutive_adapter_events_use_provider_reported_interval(self):
        _, rates = _funding(
            RUN_A,
            NOW_A,
            info=RESPONSES["binance_usdm_funding_info"],
            rows=RESPONSES["binance_usdm_funding"],
        )
        report = self._report(rates, "normal-interval")
        self.assertTrue(all(item.funding_interval == "8h" for item in rates))
        self.assertTrue(all(
            item.funding_interval_source is FundingIntervalSource.PROVIDER_REPORTED
            for item in rates
        ))
        self.assertEqual(_check(report, FUNDING_TIMESTAMP_GAP).status, ValidationCheckStatus.PASSED)
        self.assertEqual(_check(report, FUNDING_INTERVAL_MISMATCH).status, ValidationCheckStatus.PASSED)

    def test_missing_adapter_event_produces_grounded_gap_warning(self):
        rows = [
            {"symbol": "BTCUSDT", "fundingRate": "0.001", "fundingTime": 1767225600000},
            {"symbol": "BTCUSDT", "fundingRate": "0.001", "fundingTime": 1767283200000},
        ]
        _, rates = _funding(
            RUN_A,
            NOW_A,
            info=RESPONSES["binance_usdm_funding_info"],
            rows=rows,
        )
        report = self._report(rates, "missing-event")
        self.assertEqual(_check(report, FUNDING_TIMESTAMP_GAP).status, ValidationCheckStatus.WARNING)

    def test_provider_interval_change_is_visible_through_real_adapter_records(self):
        first_rows = [
            {"symbol": "BTCUSDT", "fundingRate": "0.001", "fundingTime": 1767225600000},
        ]
        second_rows = [
            {"symbol": "BTCUSDT", "fundingRate": "0.001", "fundingTime": 1767254400000},
        ]
        _, first = _funding(
            RUN_A,
            NOW_A,
            info=[{"symbol": "BTCUSDT", "fundingIntervalHours": 8}],
            rows=first_rows,
        )
        _, second = _funding(
            RUN_B,
            NOW_B,
            info=[{"symbol": "BTCUSDT", "fundingIntervalHours": 4}],
            rows=second_rows,
        )
        report = self._report((*first, *second), "interval-change")
        mismatch = _check(report, FUNDING_INTERVAL_MISMATCH)
        self.assertEqual(mismatch.status, ValidationCheckStatus.WARNING)
        self.assertEqual(
            (mismatch.details["findings"][0]["previous_interval"],
             mismatch.details["findings"][0]["current_interval"]),
            ("8h", "4h"),
        )

    def test_unavailable_interval_explicitly_skips_gap_checks(self):
        _, rates = _funding(
            RUN_A,
            NOW_A,
            info=[],
            rows=RESPONSES["binance_usdm_funding"],
        )
        report = self._report(rates, "unavailable-interval")
        self.assertTrue(all(item.funding_interval is None for item in rates))
        self.assertTrue(all(
            item.funding_interval_source is FundingIntervalSource.UNAVAILABLE
            for item in rates
        ))
        for check_type in (FUNDING_TIMESTAMP_GAP, FUNDING_INTERVAL_MISMATCH):
            result = _check(report, check_type)
            self.assertEqual(result.status, ValidationCheckStatus.SKIPPED)
            self.assertEqual(result.details["interval_availability"], "unavailable")


if __name__ == "__main__":
    unittest.main()
