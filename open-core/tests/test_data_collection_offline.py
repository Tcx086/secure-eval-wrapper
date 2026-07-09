"""Offline tests for hashing, normalization, and the sample-file provider."""

from __future__ import annotations

import re
import unittest
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from types import MappingProxyType
from unittest.mock import patch
from uuid import UUID

from secure_eval_wrapper.data_collection.hashing import (
    canonical_json_dumps,
    sha256_observation_source,
    sha256_payload,
)
from secure_eval_wrapper.data_collection.models import (
    DataRequest,
    MarketDataType,
    ProviderCapabilityStatus,
    RawObservation,
)
from secure_eval_wrapper.data_collection.sample_provider import SampleProvider
from secure_eval_wrapper.data_collection.symbols import normalize_symbol, split_base_quote
from secure_eval_wrapper.data_collection.time_utils import (
    coerce_utc_datetime,
    require_utc_datetime,
)


RUN_ID = UUID("10000000-0000-0000-0000-000000000001")
FIXED_NOW = datetime(2026, 7, 9, 12, 0, tzinfo=timezone.utc)
SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")


class HashingTests(unittest.TestCase):
    def test_canonical_json_and_hash_are_deterministic_across_key_order(self) -> None:
        first = {"b": 2, "a": {"y": 4, "x": 3}}
        second = {"a": {"x": 3, "y": 4}, "b": 2}

        self.assertEqual(canonical_json_dumps(first), canonical_json_dumps(second))
        self.assertEqual(sha256_payload(first), sha256_payload(second))
        self.assertRegex(sha256_payload(first), SHA256_PATTERN)

    def test_observation_source_hash_includes_stable_request_metadata(self) -> None:
        payload = {"symbol": "BTC/USDT", "close": "100.75"}
        first = sha256_observation_source(
            payload=payload,
            request_metadata={"timeframe": "1m", "symbol": "BTC-USDT"},
        )
        second = sha256_observation_source(
            payload=dict(reversed(tuple(payload.items()))),
            request_metadata=MappingProxyType(
                {"symbol": "BTC-USDT", "timeframe": "1m"}
            ),
        )

        self.assertEqual(first, second)
        self.assertRegex(first, SHA256_PATTERN)


class TimeUtilityTests(unittest.TestCase):
    def test_require_utc_rejects_naive_and_non_utc_values(self) -> None:
        with self.assertRaisesRegex(ValueError, "naive datetime"):
            require_utc_datetime(datetime(2026, 1, 1))
        with self.assertRaisesRegex(ValueError, "zero UTC offset"):
            require_utc_datetime(
                datetime(2026, 1, 1, tzinfo=timezone(timedelta(hours=8)))
            )

    def test_coerce_converts_aware_values_and_requires_naive_opt_in(self) -> None:
        converted = coerce_utc_datetime("2026-01-01T08:00:00+08:00")
        self.assertEqual(converted, datetime(2026, 1, 1, tzinfo=timezone.utc))

        with self.assertRaisesRegex(ValueError, "timezone"):
            coerce_utc_datetime("2026-01-01T00:00:00")
        opted_in = coerce_utc_datetime(
            "2026-01-01T00:00:00",
            assume_naive_utc=True,
        )
        self.assertEqual(opted_in.tzinfo, timezone.utc)


class SymbolUtilityTests(unittest.TestCase):
    def test_simple_pairs_are_normalized_and_split(self) -> None:
        self.assertEqual(normalize_symbol("btc/usdt"), "BTC-USDT")
        self.assertEqual(normalize_symbol("ETH_USDC"), "ETH-USDC")
        self.assertEqual(split_base_quote("sol-usdt"), ("SOL", "USDT"))

    def test_ambiguous_or_multi_part_symbols_are_rejected(self) -> None:
        for symbol in ("BTCUSDT", "BTC-USDT-SWAP", "BTC/USDT:USDT", ""):
            with self.subTest(symbol=symbol):
                with self.assertRaises(ValueError):
                    normalize_symbol(symbol)


class SampleProviderTests(unittest.TestCase):
    def setUp(self) -> None:
        self.provider = SampleProvider(clock=lambda: FIXED_NOW)
        self.request = DataRequest(
            collection_run_id=RUN_ID,
            provider_name="sample_file",
            data_type=MarketDataType.OHLCV,
            symbols=("btc/usdt",),
            timeframe="1m",
            start_at_utc=datetime(2026, 1, 1, 0, 0, tzinfo=timezone.utc),
            end_at_utc=datetime(2026, 1, 1, 0, 2, tzinfo=timezone.utc),
        )

    def test_fetch_ohlcv_produces_hashed_raw_observations_without_network(self) -> None:
        with patch("socket.socket", side_effect=AssertionError("network access attempted")):
            observations = self.provider.fetch_ohlcv(self.request)

        self.assertEqual(len(observations), 2)
        self.assertTrue(all(isinstance(item, RawObservation) for item in observations))
        self.assertEqual(
            self.provider.spec.capabilities[MarketDataType.OHLCV],
            ProviderCapabilityStatus.IMPLEMENTED,
        )
        for observation in observations:
            self.assertEqual(observation.normalized_symbol, "BTC-USDT")
            self.assertEqual(observation.request_timestamp_utc.tzinfo, timezone.utc)
            self.assertEqual(observation.observed_at_utc.tzinfo, timezone.utc)
            self.assertRegex(observation.source_sha256, SHA256_PATTERN)
            self.assertEqual(
                observation.source_sha256,
                sha256_observation_source(
                    payload=observation.payload,
                    request_metadata=observation.request_parameters,
                ),
            )

    def test_fetch_ohlcv_rejects_naive_request_boundaries(self) -> None:
        naive_request = replace(
            self.request,
            start_at_utc=datetime(2026, 1, 1, 0, 0),
        )
        with self.assertRaisesRegex(ValueError, "naive datetime"):
            self.provider.fetch_ohlcv(naive_request)

    def test_fixture_paths_cannot_escape_sample_directory(self) -> None:
        with self.assertRaisesRegex(ValueError, "directory path"):
            SampleProvider(fixture_name="../crypto_ohlcv_sample.json")

    def test_unavailable_fixture_types_are_explicit(self) -> None:
        for method in (
            self.provider.fetch_trades,
            self.provider.fetch_funding_rates,
            self.provider.fetch_instruments,
        ):
            with self.subTest(method=method.__name__):
                with self.assertRaises(NotImplementedError):
                    method(self.request)


if __name__ == "__main__":
    unittest.main()
