"""Offline provider tests for public trades, funding, and instruments."""

from __future__ import annotations

import json
import unittest
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch
from uuid import UUID

from secure_eval_wrapper.data_collection import (
    BinanceSpotPublicProvider,
    BinanceUsdmPublicProvider,
    DataRequest,
    HttpRequest,
    HttpResponse,
    MarketDataType,
    OkxPublicProvider,
    binance_usdm_instrument_key,
    normalize_funding_rate_observations,
    normalize_instrument_observations,
    normalize_trade_observations,
    okx_spot_instrument_key,
    okx_swap_instrument_key,
    sha256_observation_source,
    spot_instrument_key,
)


ROOT = Path(__file__).resolve().parents[2]
FIXTURE = json.loads(
    (ROOT / "open-core/data/sample/public_market_data_bundle_sample.json").read_text(
        encoding="utf-8-sig"
    )
)["responses"]
RUN_ID = UUID("81000000-0000-0000-0000-000000000001")
NOW = datetime(2026, 7, 9, 23, 0, tzinfo=timezone.utc)
START = datetime(2026, 1, 1, 0, 0, tzinfo=timezone.utc)
END = datetime(2026, 1, 1, 0, 1, tzinfo=timezone.utc)
FUNDING_START = datetime(2025, 12, 31, 23, 0, tzinfo=timezone.utc)
FUNDING_END = datetime(2026, 1, 1, 9, 0, tzinfo=timezone.utc)


class QueueTransport:
    def __init__(self, payloads):
        self.payloads = list(payloads) if isinstance(payloads, tuple) else [payloads]
        self.requests: list[HttpRequest] = []

    def send(self, request):
        self.requests.append(request)
        payload = self.payloads[min(len(self.requests) - 1, len(self.payloads) - 1)]
        return HttpResponse(200, json.dumps(payload).encode(), {})


class PublicTradeProviderTests(unittest.TestCase):
    def test_binance_aggregate_trades_are_public_hashed_and_side_mapped(self):
        transport = QueueTransport(FIXTURE["binance_spot_agg_trades"])
        provider = BinanceSpotPublicProvider(
            transport=transport, max_pages=1, clock=lambda: NOW
        )
        request = DataRequest(
            collection_run_id=RUN_ID,
            provider_name="binance",
            data_type=MarketDataType.TRADES,
            symbols=("BTC-USDT",),
            start_at_utc=START,
            end_at_utc=END,
            limit=3,
            max_pages=1,
        )
        with patch("socket.socket", side_effect=AssertionError("network attempted")):
            observations = provider.fetch_trades(request)
            trades = normalize_trade_observations(observations)

        sent = transport.requests[0]
        self.assertEqual(sent.url, "https://api.binance.com/api/v3/aggTrades")
        self.assertEqual(sent.headers, {})
        self.assertEqual(
            dict(sent.query_params),
            {
                "symbol": "BTCUSDT",
                "startTime": 1767225600000,
                "endTime": 1767225659999,
                "limit": 3,
            },
        )
        self.assertEqual(tuple(item.provider_trade_id for item in trades), ("1001", "1002"))
        self.assertEqual(tuple(item.side.value for item in trades), ("buy", "sell"))
        self.assertEqual(trades[1].first_provider_trade_id, "2002")
        self.assertEqual(trades[1].last_provider_trade_id, "2003")
        for item in observations:
            self.assertEqual(
                item.source_sha256,
                sha256_observation_source(
                    payload=item.payload,
                    request_metadata=item.request_parameters,
                ),
            )

    def test_binance_pagination_uses_from_id_only_after_first_page(self):
        first = FIXTURE["binance_spot_agg_trades"][:1]
        second = [
            {
                "a": 1002,
                "p": "101",
                "q": "0.5",
                "f": 2002,
                "l": 2002,
                "T": 1767225601000,
                "m": False,
                "M": True,
            }
        ]
        provider = BinanceSpotPublicProvider(
            transport=QueueTransport((first, second, [])),
            max_pages=3,
            clock=lambda: NOW,
        )
        request = DataRequest(
            RUN_ID,
            "binance",
            MarketDataType.TRADES,
            ("BTC-USDT",),
            start_at_utc=START,
            end_at_utc=END,
            limit=1,
            max_pages=3,
        )
        observations = provider.fetch_trades(request)
        self.assertEqual(len(observations), 2)
        self.assertNotIn("fromId", provider._transport.requests[0].query_params)
        self.assertNotIn("startTime", provider._transport.requests[1].query_params)
        self.assertNotIn("endTime", provider._transport.requests[1].query_params)
        self.assertEqual(provider._transport.requests[1].query_params["fromId"], 1002)

    def test_okx_history_trades_use_timestamp_cursor_and_taker_side(self):
        transport = QueueTransport(FIXTURE["okx_spot_history_trades"])
        provider = OkxPublicProvider(
            transport=transport, max_pages=1, clock=lambda: NOW
        )
        request = DataRequest(
            RUN_ID,
            "okx",
            MarketDataType.TRADES,
            ("BTC-USDT",),
            start_at_utc=START,
            end_at_utc=END,
            limit=3,
            max_pages=1,
        )
        observations = provider.fetch_trades(request)
        trades = normalize_trade_observations(observations)
        sent = transport.requests[0]
        self.assertEqual(sent.url, "https://openapi.okx.com/api/v5/market/history-trades")
        self.assertEqual(sent.query_params["type"], "2")
        self.assertEqual(sent.query_params["after"], "1767225660000")
        self.assertEqual(sent.headers, {})
        self.assertEqual(tuple(item.provider_trade_id for item in trades), ("3001", "3002"))
        self.assertEqual(tuple(item.side.value for item in trades), ("buy", "sell"))

    def test_trade_request_guards_reject_ambiguous_symbols_and_private_paths(self):
        binance = BinanceSpotPublicProvider(
            transport=QueueTransport([]), clock=lambda: NOW
        )
        bad = DataRequest(
            RUN_ID,
            "binance",
            MarketDataType.TRADES,
            ("BTCUSDT",),
            start_at_utc=START,
            end_at_utc=END,
        )
        with self.assertRaises(ValueError):
            binance.fetch_trades(bad)
        with self.assertRaises(ValueError):
            binance._build_public_request("/api/v3/account", {})

        okx = OkxPublicProvider(transport=QueueTransport({}), clock=lambda: NOW)
        with self.assertRaises(ValueError):
            okx._build_public_request("/api/v5/account/balance", {})


class FundingAndInstrumentProviderTests(unittest.TestCase):
    def setUp(self):
        self.binance_key = binance_usdm_instrument_key(
            "BTCUSDT",
            base_asset="BTC",
            quote_asset="USDT",
            settlement_asset="USDT",
        )
        self.okx_swap = okx_swap_instrument_key(
            "BTC-USDT-SWAP", settlement_asset="USDT"
        )

    def test_binance_funding_is_ascending_and_preserves_mark_price(self):
        transport = QueueTransport(FIXTURE["binance_usdm_funding"])
        provider = BinanceUsdmPublicProvider(
            transport=transport, max_pages=1, clock=lambda: NOW
        )
        request = DataRequest(
            RUN_ID,
            "binance_usdm",
            MarketDataType.FUNDING_RATES,
            (),
            start_at_utc=FUNDING_START,
            end_at_utc=FUNDING_END,
            limit=3,
            max_pages=1,
            instruments=(self.binance_key,),
        )
        observations = provider.fetch_funding_rates(request)
        rates = normalize_funding_rate_observations(observations)
        sent = transport.requests[0]
        self.assertEqual(sent.url, "https://fapi.binance.com/fapi/v1/fundingRate")
        self.assertEqual(sent.headers, {})
        self.assertEqual(tuple(str(item.rate) for item in rates), ("0.0001", "-0.0002"))
        self.assertEqual(str(rates[0].mark_price), "100.50")
        self.assertEqual(rates[0].instrument_key.instrument_type.value, "perpetual_swap")

    def test_binance_funding_pagination_advances_one_millisecond(self):
        first, second = FIXTURE["binance_usdm_funding"]
        transport = QueueTransport(([first], [second], []))
        provider = BinanceUsdmPublicProvider(
            transport=transport, max_pages=3, clock=lambda: NOW
        )
        request = DataRequest(
            RUN_ID,
            "binance_usdm",
            MarketDataType.FUNDING_RATES,
            (),
            start_at_utc=FUNDING_START,
            end_at_utc=FUNDING_END,
            limit=1,
            max_pages=3,
            instruments=(self.binance_key,),
        )
        observations = provider.fetch_funding_rates(request)
        self.assertEqual(len(observations), 2)
        self.assertEqual(
            transport.requests[1].query_params["startTime"],
            first["fundingTime"] + 1,
        )
        self.assertEqual(
            transport.requests[2].query_params["startTime"],
            second["fundingTime"] + 1,
        )
    def test_okx_funding_uses_realized_rate_and_preserves_predicted_rate(self):
        transport = QueueTransport(FIXTURE["okx_swap_funding"])
        provider = OkxPublicProvider(
            transport=transport, max_pages=1, clock=lambda: NOW
        )
        request = DataRequest(
            RUN_ID,
            "okx",
            MarketDataType.FUNDING_RATES,
            (),
            start_at_utc=FUNDING_START,
            end_at_utc=FUNDING_END,
            limit=3,
            max_pages=1,
            instruments=(self.okx_swap,),
        )
        rates = normalize_funding_rate_observations(
            provider.fetch_funding_rates(request)
        )
        self.assertEqual(tuple(str(item.rate) for item in rates), ("0.0001", "-0.0002"))
        self.assertEqual(tuple(str(item.predicted_rate) for item in rates), ("0.0001", "0.0000"))
        self.assertEqual(transport.requests[0].headers, {})

    def test_all_four_instrument_scopes_normalize_without_identity_collision(self):
        binance_spot = BinanceSpotPublicProvider(
            transport=QueueTransport(FIXTURE["binance_spot_exchange_info"]),
            clock=lambda: NOW,
        )
        binance_usdm = BinanceUsdmPublicProvider(
            transport=QueueTransport(FIXTURE["binance_usdm_exchange_info"]),
            clock=lambda: NOW,
        )
        okx_transport = QueueTransport(
            (FIXTURE["okx_swap_instruments"], FIXTURE["okx_spot_instruments"])
        )
        okx = OkxPublicProvider(transport=okx_transport, clock=lambda: NOW)
        binance_spot_key = spot_instrument_key(
            provider_name="binance",
            exchange_name="Binance",
            provider_instrument_id="BTCUSDT",
            symbol="BTC-USDT",
        )
        okx_spot = okx_spot_instrument_key("BTC-USDT")

        observations = (
            *binance_spot.fetch_instruments(
                DataRequest(RUN_ID, "binance", MarketDataType.INSTRUMENTS, (), limit=1, instruments=(binance_spot_key,))
            ),
            *binance_usdm.fetch_instruments(
                DataRequest(RUN_ID, "binance_usdm", MarketDataType.INSTRUMENTS, (), limit=1, instruments=(self.binance_key,))
            ),
            *okx.fetch_instruments(
                DataRequest(RUN_ID, "okx", MarketDataType.INSTRUMENTS, (), limit=2, instruments=(okx_spot, self.okx_swap))
            ),
        )
        instruments = normalize_instrument_observations(observations)
        self.assertEqual(len(instruments), 4)
        spot_symbols = {item.symbol for item in instruments if item.instrument_type.value == "spot"}
        swap_symbols = {item.symbol for item in instruments if item.instrument_type.value == "perpetual_swap"}
        self.assertEqual(spot_symbols, {"BTC-USDT"})
        self.assertEqual(swap_symbols, {"BTC-USDT:USDT:PERPETUAL_SWAP"})
        self.assertEqual(len({item.instrument_key.identity_sha256 for item in instruments}), 4)
        self.assertTrue(all(item.tick_size > 0 for item in instruments))
        self.assertTrue(all(request.headers == {} for request in okx_transport.requests))

    def test_funding_requires_explicit_derivative_identity(self):
        provider = BinanceUsdmPublicProvider(
            transport=QueueTransport([]), clock=lambda: NOW
        )
        request = DataRequest(
            RUN_ID,
            "binance_usdm",
            MarketDataType.FUNDING_RATES,
            ("BTC-USDT",),
            start_at_utc=FUNDING_START,
            end_at_utc=FUNDING_END,
        )
        with self.assertRaisesRegex(ValueError, "InstrumentKey"):
            provider.fetch_funding_rates(request)


if __name__ == "__main__":
    unittest.main()
