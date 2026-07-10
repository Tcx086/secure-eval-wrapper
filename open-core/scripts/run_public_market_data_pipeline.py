"""Safe fixture-default CLI for the complete Phase 2 public data vertical."""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlparse
from uuid import uuid4


OPEN_CORE_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(OPEN_CORE_ROOT / "src"))

from secure_eval_wrapper.data_collection import (  # noqa: E402
    BinanceSpotPublicProvider,
    BinanceUsdmPublicProvider,
    HttpRequest,
    HttpResponse,
    OkxPublicProvider,
    binance_usdm_instrument_key,
    okx_spot_instrument_key,
    okx_swap_instrument_key,
    spot_instrument_key,
)
from secure_eval_wrapper.data_pipeline import (  # noqa: E402
    FundingRatePipeline,
    FundingRatePipelineRequest,
    InstrumentMetadataPipeline,
    InstrumentMetadataPipelineRequest,
    OhlcvPipeline,
    OhlcvPipelineRequest,
    TradePipeline,
    TradePipelineRequest,
)
from secure_eval_wrapper.storage.postgres import build_connection_kwargs, load_postgres_config  # noqa: E402
from secure_eval_wrapper.storage.postgres.reconciliation_repositories import (  # noqa: E402
    PostgresOhlcvPipelineRepository,
)


PUBLIC_NETWORK_FLAG = "ENABLE_PUBLIC_NETWORK_SMOKE"
POSTGRES_PERSISTENCE_FLAG = "ENABLE_POSTGRES_PERSISTENCE"
FIXTURE_PATH = OPEN_CORE_ROOT / "data" / "sample" / "public_market_data_bundle_sample.json"
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_OFFLINE_NOW = datetime(2026, 7, 9, 23, 0, tzinfo=timezone.utc)
_TRADE_START = datetime(2026, 1, 1, 0, 0, tzinfo=timezone.utc)
_TRADE_END = datetime(2026, 1, 1, 0, 1, tzinfo=timezone.utc)
_FUNDING_START = datetime(2025, 12, 31, 23, 0, tzinfo=timezone.utc)
_FUNDING_END = datetime(2026, 1, 1, 9, 0, tzinfo=timezone.utc)


def _enabled(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() == "true"


def _response(payload: object) -> HttpResponse:
    return HttpResponse(
        status=200,
        body_bytes=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )


class _FixtureTransport:
    def __init__(self, responses: dict[str, object], *, provider: str) -> None:
        self._responses = responses
        self._provider = provider

    def send(self, request: HttpRequest) -> HttpResponse:
        path = urlparse(request.url).path
        if self._provider == "binance_spot":
            if path == "/api/v3/klines":
                return _response([
                    [1767225600000, "100", "102", "99", "101", "12.5", 1767225659999, "0", 1, "0", "0", "0"],
                    [1767225660000, "101", "103", "100", "102", "13", 1767225719999, "0", 1, "0", "0", "0"],
                ])
            if path == "/api/v3/aggTrades":
                return _response(self._responses["binance_spot_agg_trades"])
            if path == "/api/v3/exchangeInfo":
                return _response(self._responses["binance_spot_exchange_info"])
        if self._provider == "binance_usdm":
            if path == "/fapi/v1/fundingRate":
                return _response(self._responses["binance_usdm_funding"])
            if path == "/fapi/v1/exchangeInfo":
                return _response(self._responses["binance_usdm_exchange_info"])
        if self._provider == "okx":
            if path == "/api/v5/market/history-candles":
                return _response({
                    "code": "0",
                    "msg": "",
                    "data": [
                        ["1767225660000", "101", "103", "100", "102", "13", "13", "1326", "1"],
                        ["1767225600000", "100", "102", "99", "101", "12.5", "12.5", "1262.5", "1"],
                    ],
                })
            if path == "/api/v5/market/history-trades":
                return _response(self._responses["okx_spot_history_trades"])
            if path == "/api/v5/public/funding-rate-history":
                return _response(self._responses["okx_swap_funding"])
            if path == "/api/v5/public/instruments":
                key = "okx_spot_instruments" if request.query_params.get("instType") == "SPOT" else "okx_swap_instruments"
                return _response(self._responses[key])
        raise AssertionError(f"offline fixture has no route for {request.url}")


def _load_fixture() -> dict[str, object]:
    fixture = json.loads(FIXTURE_PATH.read_text(encoding="utf-8-sig"))
    if fixture.get("classification") != "synthetic_public_safe":
        raise RuntimeError("offline fixture must be classified synthetic_public_safe")
    responses = fixture.get("responses")
    if not isinstance(responses, dict):
        raise RuntimeError("offline fixture responses must be an object")
    return responses


def _providers(mode: str):
    if mode == "public-network":
        return BinanceSpotPublicProvider(max_pages=10), BinanceUsdmPublicProvider(max_pages=10), OkxPublicProvider(max_pages=10)
    responses = _load_fixture()
    return (
        BinanceSpotPublicProvider(
            transport=_FixtureTransport(responses, provider="binance_spot"),
            max_pages=1,
            clock=lambda: _OFFLINE_NOW,
        ),
        BinanceUsdmPublicProvider(
            transport=_FixtureTransport(responses, provider="binance_usdm"),
            max_pages=1,
            clock=lambda: _OFFLINE_NOW,
        ),
        OkxPublicProvider(
            transport=_FixtureTransport(responses, provider="okx"),
            max_pages=1,
            clock=lambda: _OFFLINE_NOW,
        ),
    )


def _connect_postgres():
    config = load_postgres_config()
    kwargs = build_connection_kwargs(config)
    try:
        import psycopg  # type: ignore
        return psycopg.connect(**kwargs)
    except ImportError:
        try:
            import psycopg2  # type: ignore
            return psycopg2.connect(**kwargs)
        except ImportError as exc:
            raise RuntimeError("PostgreSQL persistence requires psycopg or psycopg2; no fallback exists") from exc


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run bounded public OHLCV, trade, funding, and instrument pipelines.")
    parser.add_argument("--mode", choices=("offline-fixture", "public-network"), default="offline-fixture")
    parser.add_argument("--persist", action="store_true")
    return parser


def _print_typed(label: str, result) -> None:
    print(f"data_type={label} pipeline_status={result.status.value} persistence={result.persistence is not None}")
    for outcome in result.outcomes:
        validation = outcome.validation_report.status.value if outcome.validation_report else "not_run"
        hashes_valid = bool(outcome.observations) and all(_SHA256.fullmatch(item.source_sha256) for item in outcome.observations)
        print(
            f"provider={outcome.provider_name} data_type={label} normalized={len(outcome.records)} "
            f"accepted={len(outcome.accepted_records)} rejected={outcome.rejected_count} "
            f"validation={validation} hashes_valid={bool(hashes_valid)}"
        )


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.mode == "public-network" and not _enabled(PUBLIC_NETWORK_FLAG):
        print(f"Public-network mode is disabled; set {PUBLIC_NETWORK_FLAG}=true explicitly.")
        return 2
    if args.persist and not _enabled(POSTGRES_PERSISTENCE_FLAG):
        print(f"PostgreSQL persistence is disabled; set {POSTGRES_PERSISTENCE_FLAG}=true and pass --persist.")
        return 2

    connection = None
    try:
        repository = None
        if args.persist:
            connection = _connect_postgres()
            repository = PostgresOhlcvPipelineRepository(connection)

        binance, binance_usdm, okx = _providers(args.mode)
        if args.mode == "offline-fixture":
            ohlcv_start = _TRADE_START
            ohlcv_end = datetime(2026, 1, 1, 0, 2, tzinfo=timezone.utc)
            trade_start, trade_end = _TRADE_START, _TRADE_END
            funding_start, funding_end = _FUNDING_START, _FUNDING_END
            trade_limit, trade_pages = 3, 1
            funding_limit, funding_pages = 3, 1
            clock = lambda: _OFFLINE_NOW
        else:
            now = datetime.now(timezone.utc)
            ohlcv_end = now.replace(second=0, microsecond=0) - timedelta(minutes=1)
            ohlcv_start = ohlcv_end - timedelta(minutes=2)
            trade_end = now
            trade_start = trade_end - timedelta(seconds=1)
            funding_end = now
            funding_start = now - timedelta(days=10)
            trade_limit, trade_pages = 100, 10
            funding_limit, funding_pages = 100, 3
            clock = None

        btc_spot_binance = spot_instrument_key(
            provider_name="binance",
            exchange_name="Binance",
            provider_instrument_id="BTCUSDT",
            symbol="BTC-USDT",
        )
        btc_usdm = binance_usdm_instrument_key(
            "BTCUSDT",
            base_asset="BTC",
            quote_asset="USDT",
            settlement_asset="USDT",
        )
        btc_spot_okx = okx_spot_instrument_key("BTC-USDT")
        btc_swap_okx = okx_swap_instrument_key("BTC-USDT-SWAP", settlement_asset="USDT")

        ohlcv = OhlcvPipeline((binance, okx), repository=repository, clock=clock).run(
            OhlcvPipelineRequest(
                collection_run_id=uuid4(),
                validation_run_id=uuid4(),
                provider_names=("binance", "okx"),
                symbol="BTC-USDT",
                timeframe="1m",
                start_at_utc=ohlcv_start,
                end_at_utc=ohlcv_end,
                limit=3,
                max_pages=1,
                persistence_enabled=args.persist,
            )
        )
        print(f"data_type=ohlcv pipeline_status={ohlcv.status.value} persistence={ohlcv.persistence is not None}")
        for outcome in ohlcv.outcomes:
            validation = outcome.validation_report.status.value if outcome.validation_report else "not_run"
            print(
                f"provider={outcome.provider_name} data_type=ohlcv normalized={len(outcome.bars)} "
                f"accepted={len(outcome.accepted_bars)} rejected={outcome.rejected_bar_count} validation={validation}"
            )

        trades = TradePipeline((binance, okx), repository=repository, clock=clock).run(
            TradePipelineRequest(
                collection_run_id=uuid4(),
                validation_run_id=uuid4(),
                provider_names=("binance", "okx"),
                symbol="BTC-USDT",
                start_at_utc=trade_start,
                end_at_utc=trade_end,
                limit=trade_limit,
                max_pages=trade_pages,
                persistence_enabled=args.persist,
            )
        )
        funding = FundingRatePipeline((binance_usdm, okx), repository=repository, clock=clock).run(
            FundingRatePipelineRequest(
                collection_run_id=uuid4(),
                validation_run_id=uuid4(),
                instruments_by_provider={"binance_usdm": btc_usdm, "okx": btc_swap_okx},
                start_at_utc=funding_start,
                end_at_utc=funding_end,
                limit=funding_limit,
                max_pages=funding_pages,
                persistence_enabled=args.persist,
            )
        )
        instruments = InstrumentMetadataPipeline((binance, binance_usdm, okx), repository=repository, clock=clock).run(
            InstrumentMetadataPipelineRequest(
                collection_run_id=uuid4(),
                validation_run_id=uuid4(),
                instruments_by_provider={
                    "binance": (btc_spot_binance,),
                    "binance_usdm": (btc_usdm,),
                    "okx": (btc_spot_okx, btc_swap_okx),
                },
                persistence_enabled=args.persist,
            )
        )
        _print_typed("trades", trades)
        _print_typed("funding_rates", funding)
        _print_typed("instruments", instruments)
        statuses = (ohlcv.status.value, trades.status.value, funding.status.value, instruments.status.value)
        return 0 if all(status in ("succeeded", "partial") for status in statuses) else 1
    finally:
        if connection is not None:
            connection.close()


if __name__ == "__main__":
    raise SystemExit(main())
