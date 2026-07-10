"""Fully offline tests for the OKX V5 public OHLCV provider."""

from __future__ import annotations

import json
import re
import unittest
from dataclasses import replace
from datetime import datetime, timezone
from unittest.mock import patch
from uuid import UUID

from secure_eval_wrapper.data_collection import (
    DataRequest,
    HttpRequest,
    HttpResponse,
    MarketDataType,
    OkxPublicOhlcvProvider,
    TransportError,
    normalize_ohlcv_observations,
    sha256_observation_source,
)
from secure_eval_wrapper.data_validation import ValidationStatus, validate_ohlcv_bars


COLLECTION_RUN_ID = UUID("50000000-0000-0000-0000-000000000001")
VALIDATION_RUN_ID = UUID("50000000-0000-0000-0000-000000000002")
FIXED_NOW = datetime(2026, 7, 9, 20, 0, tzinfo=timezone.utc)
WINDOW_START = datetime(2026, 1, 1, 0, 0, tzinfo=timezone.utc)
WINDOW_END = datetime(2026, 1, 1, 0, 2, tzinfo=timezone.utc)
SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")


def _candle(open_time_ms: int, *, confirm: str = "1") -> list[str]:
    return [
        str(open_time_ms),
        "100.00",
        "102.00",
        "99.00",
        "101.00",
        "12.50000000",
        "1262.50000000",
        "1262.50000000",
        confirm,
    ]


CANDLES = [
    _candle(1_767_225_660_000),
    _candle(1_767_225_600_000),
]


class FakeTransport:
    def __init__(self, responses: HttpResponse | list[HttpResponse]) -> None:
        self.responses = responses if isinstance(responses, list) else [responses]
        self.requests: list[HttpRequest] = []

    def send(self, request: HttpRequest) -> HttpResponse:
        self.requests.append(request)
        index = min(len(self.requests) - 1, len(self.responses) - 1)
        return self.responses[index]


def _response(payload: object | None = None, *, status: int = 200) -> HttpResponse:
    envelope = {"code": "0", "msg": "", "data": CANDLES} if payload is None else payload
    return HttpResponse(
        status=status,
        body_bytes=json.dumps(envelope).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )


def _request(**changes: object) -> DataRequest:
    request = DataRequest(
        collection_run_id=COLLECTION_RUN_ID,
        provider_name="okx",
        data_type=MarketDataType.OHLCV,
        symbols=("BTC-USDT",),
        timeframe="1m",
        start_at_utc=WINDOW_START,
        end_at_utc=WINDOW_END,
        limit=2,
    )
    return replace(request, **changes)


class OkxPublicOhlcvProviderTests(unittest.TestCase):
    def test_builds_only_official_public_v5_request_without_auth_headers(self) -> None:
        transport = FakeTransport(_response())
        provider = OkxPublicOhlcvProvider(
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
        self.assertEqual(
            sent.url,
            "https://openapi.okx.com/api/v5/market/history-candles",
        )
        self.assertEqual(
            dict(sent.query_params),
            {
                "instId": "BTC-USDT",
                "bar": "1m",
                "limit": "2",
                "after": "1767225720000",
            },
        )
        self.assertEqual(sent.timeout, 3.5)
        self.assertEqual(sent.headers, {})
        self.assertFalse(any("access" in name.lower() for name in sent.headers))

    def test_parses_final_candles_chronologically_with_deterministic_hashes(self) -> None:
        provider = OkxPublicOhlcvProvider(
            transport=FakeTransport(_response()),
            clock=lambda: FIXED_NOW,
        )

        first = provider.fetch_ohlcv(_request())
        second = provider.fetch_ohlcv(_request())

        self.assertEqual(first, second)
        self.assertEqual(
            tuple(item.provider_timestamp for item in first),
            ("1767225600000", "1767225660000"),
        )
        for observation in first:
            self.assertEqual(observation.provider_name, "okx")
            self.assertEqual(observation.exchange_name, "OKX")
            self.assertEqual(
                observation.source_endpoint,
                "okx-v5:/api/v5/market/history-candles",
            )
            self.assertEqual(observation.raw_symbol, "BTC-USDT")
            self.assertEqual(observation.normalized_symbol, "BTC-USDT")
            self.assertEqual(observation.payload["symbol"], "BTC-USDT")
            self.assertIsInstance(observation.payload["provider_payload"], list)
            self.assertIsInstance(observation.payload["open"], str)
            self.assertIsInstance(observation.payload["volume"], str)
            self.assertIs(observation.payload["is_final"], True)
            self.assertEqual(observation.observed_at_utc.tzinfo, timezone.utc)
            self.assertRegex(observation.source_sha256, SHA256_PATTERN)
            self.assertEqual(
                observation.source_sha256,
                sha256_observation_source(
                    payload=observation.payload,
                    request_metadata=observation.request_parameters,
                ),
            )

    def test_invalid_envelope_result_code_and_candle_shape_fail(self) -> None:
        cases = (
            (_response([]), "JSON object"),
            (_response({"code": "51000", "msg": "bad", "data": []}), "result code"),
            (_response({"code": "0", "msg": "", "data": [["1", "2"]]}), "9-element"),
        )
        for response, message in cases:
            with self.subTest(message=message):
                provider = OkxPublicOhlcvProvider(
                    transport=FakeTransport(response),
                    clock=lambda: FIXED_NOW,
                )
                with self.assertRaisesRegex(ValueError, message):
                    provider.fetch_ohlcv(_request())

        provider = OkxPublicOhlcvProvider(
            transport=FakeTransport(_response(status=429)),
            clock=lambda: FIXED_NOW,
        )
        with self.assertRaisesRegex(TransportError, "HTTP 429"):
            provider.fetch_ohlcv(_request())

    def test_utc_half_open_and_request_guards(self) -> None:
        boundary = _candle(1_767_225_720_000)
        provider = OkxPublicOhlcvProvider(
            transport=FakeTransport(
                _response({"code": "0", "msg": "", "data": [boundary, *CANDLES]})
            ),
            clock=lambda: FIXED_NOW,
        )
        observations = provider.fetch_ohlcv(_request(limit=3))
        self.assertEqual(len(observations), 2)
        self.assertTrue(all(item.observed_at_utc < WINDOW_END for item in observations))

        guarded = (
            _request(symbols=("BTCUSDT",)),
            _request(start_at_utc=datetime(2026, 1, 1)),
            _request(end_at_utc=None),
            _request(timeframe="8h"),
            _request(limit=301),
            _request(parameters={"apiKey": "not-allowed"}),
        )
        for request in guarded:
            with self.subTest(request=request):
                with self.assertRaises(ValueError):
                    provider.fetch_ohlcv(request)

    def test_pagination_terminates_and_detects_nonprogress(self) -> None:
        start = datetime(2026, 1, 1, 0, 0, tzinfo=timezone.utc)
        end = datetime(2026, 1, 1, 0, 4, tzinfo=timezone.utc)
        pages = [
            _response(
                {
                    "code": "0",
                    "msg": "",
                    "data": [_candle(1_767_225_780_000), _candle(1_767_225_720_000)],
                }
            ),
            _response({"code": "0", "msg": "", "data": CANDLES}),
        ]
        transport = FakeTransport(pages)
        provider = OkxPublicOhlcvProvider(
            transport=transport,
            max_pages=2,
            clock=lambda: FIXED_NOW,
        )
        observations = provider.fetch_ohlcv(
            _request(start_at_utc=start, end_at_utc=end)
        )
        self.assertEqual(len(observations), 4)
        self.assertEqual(transport.requests[1].query_params["after"], "1767225720000")

        nonprogress = OkxPublicOhlcvProvider(
            transport=FakeTransport(
                _response(
                    {
                        "code": "0",
                        "msg": "",
                        "data": [_candle(1_767_225_780_000), _candle(1_767_225_720_000)],
                    }
                )
            ),
            clock=lambda: FIXED_NOW,
        )
        with self.assertRaisesRegex(ValueError, "cursor did not advance"):
            nonprogress.fetch_ohlcv(_request())

        bounded = OkxPublicOhlcvProvider(
            transport=FakeTransport(pages[0]),
            max_pages=1,
            clock=lambda: FIXED_NOW,
        )
        with self.assertRaisesRegex(ValueError, "exceeded max_pages"):
            bounded.fetch_ohlcv(_request(start_at_utc=start, end_at_utc=end))

    def test_output_normalizes_and_validates_with_socket_blocked(self) -> None:
        provider = OkxPublicOhlcvProvider(
            transport=FakeTransport(_response()),
            clock=lambda: FIXED_NOW,
        )
        with patch("socket.socket", side_effect=AssertionError("network access attempted")):
            observations = provider.fetch_ohlcv(_request())
            bars = normalize_ohlcv_observations(observations)
            report = validate_ohlcv_bars(
                validation_run_id=VALIDATION_RUN_ID,
                dataset_ref="okx-public-offline-fixture",
                bars=bars,
                clock=lambda: FIXED_NOW,
            )

        self.assertEqual(report.status, ValidationStatus.ACCEPTED)
        self.assertEqual(tuple(bar.exchange for bar in bars), ("OKX", "OKX"))
        self.assertTrue(all(bar.is_final is True for bar in bars))

    def test_unimplemented_capabilities_and_private_paths_are_unavailable(self) -> None:
        provider = OkxPublicOhlcvProvider(
            transport=FakeTransport(_response()),
            clock=lambda: FIXED_NOW,
        )
        with self.assertRaisesRegex(ValueError, "only the public"):
            provider._build_public_request("/api/v5/account/balance", {})
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
