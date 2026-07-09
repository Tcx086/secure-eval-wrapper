"""Offline tests for the Binance Spot public OHLCV provider adapter."""

from __future__ import annotations

import json
import re
import unittest
from dataclasses import replace
from datetime import datetime, timezone
from unittest.mock import patch
from uuid import UUID

from secure_eval_wrapper.data_collection import (
    BinanceSpotOhlcvProvider,
    DataRequest,
    HttpRequest,
    HttpResponse,
    MarketDataType,
    RawObservation,
    TransportError,
    normalize_ohlcv_observations,
    sha256_observation_source,
)
from secure_eval_wrapper.data_validation import ValidationStatus, validate_ohlcv_bars


COLLECTION_RUN_ID = UUID("30000000-0000-0000-0000-000000000001")
VALIDATION_RUN_ID = UUID("30000000-0000-0000-0000-000000000002")
FIXED_NOW = datetime(2026, 7, 9, 16, 0, tzinfo=timezone.utc)
WINDOW_START = datetime(2026, 1, 1, 0, 0, tzinfo=timezone.utc)
WINDOW_END = datetime(2026, 1, 1, 0, 2, tzinfo=timezone.utc)
SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")


def _kline(open_time_ms: int, close_time_ms: int) -> list[object]:
    return [
        open_time_ms,
        "100.00",
        "102.00",
        "99.00",
        "101.00",
        "12.50000000",
        close_time_ms,
        "1262.50000000",
        42,
        "6.00000000",
        "606.00000000",
        "0",
    ]


KLINES = [
    _kline(1_767_225_600_000, 1_767_225_659_999),
    _kline(1_767_225_660_000, 1_767_225_719_999),
]


class FakeTransport:
    def __init__(self, response: HttpResponse) -> None:
        self.response = response
        self.requests: list[HttpRequest] = []

    def send(self, request: HttpRequest) -> HttpResponse:
        self.requests.append(request)
        return self.response


def _response(payload: object = KLINES, *, status: int = 200) -> HttpResponse:
    return HttpResponse(
        status=status,
        body_bytes=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )


def _request(**changes: object) -> DataRequest:
    request = DataRequest(
        collection_run_id=COLLECTION_RUN_ID,
        provider_name="binance",
        data_type=MarketDataType.OHLCV,
        symbols=("BTC-USDT",),
        timeframe="1m",
        start_at_utc=WINDOW_START,
        end_at_utc=WINDOW_END,
        limit=2,
    )
    return replace(request, **changes)


class BinanceSpotOhlcvProviderTests(unittest.TestCase):
    def test_builds_only_public_kline_request_without_api_key_header(self) -> None:
        transport = FakeTransport(_response())
        provider = BinanceSpotOhlcvProvider(
            transport=transport,
            timeout=3.5,
            clock=lambda: FIXED_NOW,
        )

        with patch("socket.socket", side_effect=AssertionError("network access attempted")):
            observations = provider.fetch_ohlcv(_request())

        self.assertEqual(len(observations), 2)
        self.assertEqual(len(transport.requests), 1)
        sent = transport.requests[0]
        self.assertEqual(sent.method, "GET")
        self.assertEqual(sent.url, "https://api.binance.com/api/v3/klines")
        self.assertEqual(
            dict(sent.query_params),
            {
                "symbol": "BTCUSDT",
                "interval": "1m",
                "startTime": 1_767_225_600_000,
                "endTime": 1_767_225_719_999,
                "limit": 2,
            },
        )
        self.assertEqual(sent.timeout, 3.5)
        self.assertFalse(any(name.lower() == "x-mbx-apikey" for name in sent.headers))

    def test_parses_raw_observations_and_hashes_deterministically(self) -> None:
        transport = FakeTransport(_response())
        provider = BinanceSpotOhlcvProvider(
            transport=transport,
            clock=lambda: FIXED_NOW,
        )

        first = provider.fetch_ohlcv(_request())
        second = provider.fetch_ohlcv(_request())

        self.assertEqual(first, second)
        for observation, provider_payload in zip(first, KLINES):
            self.assertIsInstance(observation, RawObservation)
            self.assertEqual(observation.provider_name, "binance")
            self.assertEqual(observation.exchange_name, "Binance")
            self.assertEqual(observation.source_endpoint, "binance-spot:/api/v3/klines")
            self.assertEqual(observation.raw_symbol, "BTCUSDT")
            self.assertEqual(observation.normalized_symbol, "BTC-USDT")
            self.assertEqual(observation.timeframe, "1m")
            self.assertEqual(observation.provider_timestamp, str(provider_payload[0]))
            self.assertEqual(observation.payload["provider_payload"], provider_payload)
            self.assertIsInstance(observation.payload["open"], str)
            self.assertIsInstance(observation.payload["volume"], str)
            self.assertEqual(observation.request_timestamp_utc.tzinfo, timezone.utc)
            self.assertEqual(observation.ingested_at_utc.tzinfo, timezone.utc)
            self.assertEqual(observation.observed_at_utc.tzinfo, timezone.utc)
            self.assertRegex(observation.source_sha256, SHA256_PATTERN)
            self.assertEqual(
                observation.source_sha256,
                sha256_observation_source(
                    payload=observation.payload,
                    request_metadata=observation.request_parameters,
                ),
            )

    def test_half_open_end_boundary_is_removed_from_query_and_results(self) -> None:
        boundary_kline = _kline(1_767_225_720_000, 1_767_225_779_999)
        transport = FakeTransport(_response([*KLINES, boundary_kline]))
        provider = BinanceSpotOhlcvProvider(
            transport=transport,
            clock=lambda: FIXED_NOW,
        )

        observations = provider.fetch_ohlcv(_request(limit=3))

        self.assertEqual(len(observations), 2)
        self.assertEqual(transport.requests[0].query_params["endTime"], 1_767_225_719_999)
        self.assertTrue(all(item.observed_at_utc < WINDOW_END for item in observations))

    def test_output_normalizes_and_validates_offline(self) -> None:
        provider = BinanceSpotOhlcvProvider(
            transport=FakeTransport(_response()),
            clock=lambda: FIXED_NOW,
        )

        with patch("socket.socket", side_effect=AssertionError("network access attempted")):
            observations = provider.fetch_ohlcv(_request())
            bars = normalize_ohlcv_observations(observations)
            report = validate_ohlcv_bars(
                validation_run_id=VALIDATION_RUN_ID,
                dataset_ref="binance-public-offline-fixture",
                bars=bars,
                clock=lambda: FIXED_NOW,
            )

        self.assertEqual(len(bars), 2)
        self.assertEqual(report.status, ValidationStatus.ACCEPTED)
        self.assertEqual(bars[0].symbol, "BTC-USDT")
        self.assertEqual(bars[0].exchange, "Binance")
        self.assertIsNone(bars[0].is_final)

    def test_invalid_tuple_shape_and_non_200_fail_clearly(self) -> None:
        malformed = BinanceSpotOhlcvProvider(
            transport=FakeTransport(_response([[1, "2"]])),
            clock=lambda: FIXED_NOW,
        )
        with self.assertRaisesRegex(ValueError, "12-element list"):
            malformed.fetch_ohlcv(_request())

        failed = BinanceSpotOhlcvProvider(
            transport=FakeTransport(_response({"code": -1}, status=429)),
            clock=lambda: FIXED_NOW,
        )
        with self.assertRaisesRegex(TransportError, "HTTP 429"):
            failed.fetch_ohlcv(_request())

    def test_request_guards_reject_ambiguous_time_and_excess_limit(self) -> None:
        provider = BinanceSpotOhlcvProvider(
            transport=FakeTransport(_response()),
            clock=lambda: FIXED_NOW,
        )
        guarded_requests = (
            _request(symbols=("BTCUSDT",)),
            _request(start_at_utc=datetime(2026, 1, 1)),
            _request(limit=1001),
            _request(parameters={"signature": "not-allowed"}),
        )
        for request in guarded_requests:
            with self.subTest(request=request):
                with self.assertRaises(ValueError):
                    provider.fetch_ohlcv(request)

    def test_private_paths_and_unimplemented_capabilities_are_unavailable(self) -> None:
        provider = BinanceSpotOhlcvProvider(
            transport=FakeTransport(_response()),
            clock=lambda: FIXED_NOW,
        )
        with self.assertRaisesRegex(ValueError, "only the public"):
            provider._build_public_request("/api/v3/account", {})
        for method in (
            provider.fetch_trades,
            provider.fetch_funding_rates,
            provider.fetch_instruments,
        ):
            with self.subTest(method=method.__name__):
                with self.assertRaises(NotImplementedError):
                    method(_request())


if __name__ == "__main__":
    unittest.main()
