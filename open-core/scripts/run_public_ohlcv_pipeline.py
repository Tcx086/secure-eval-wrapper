"""Safe CLI for the public Binance + OKX OHLCV pipeline."""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4


OPEN_CORE_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(OPEN_CORE_ROOT / "src"))

from secure_eval_wrapper.data_collection import (  # noqa: E402
    BinanceSpotOhlcvProvider,
    HttpRequest,
    HttpResponse,
    OkxPublicOhlcvProvider,
)
from secure_eval_wrapper.data_pipeline import (  # noqa: E402
    OhlcvPipeline,
    OhlcvPipelineRequest,
)
from secure_eval_wrapper.storage.postgres import (  # noqa: E402
    build_connection_kwargs,
    load_postgres_config,
)
from secure_eval_wrapper.storage.postgres.reconciliation_repositories import (  # noqa: E402
    PostgresOhlcvPipelineRepository,
)


PUBLIC_NETWORK_FLAG = "ENABLE_PUBLIC_NETWORK_SMOKE"
POSTGRES_PERSISTENCE_FLAG = "ENABLE_POSTGRES_PERSISTENCE"
_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_OFFLINE_NOW = datetime(2026, 7, 9, 23, 0, tzinfo=timezone.utc)
_OFFLINE_START = datetime(2026, 1, 1, 0, 0, tzinfo=timezone.utc)
_OFFLINE_END = datetime(2026, 1, 1, 0, 2, tzinfo=timezone.utc)


def _enabled(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() == "true"


class _FixtureTransport:
    def __init__(self, payload: object) -> None:
        self._response = HttpResponse(
            status=200,
            body_bytes=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
        )

    def send(self, request: HttpRequest) -> HttpResponse:
        return self._response


def _offline_providers():
    binance_payload = [
        [
            1_767_225_600_000,
            "100.00",
            "102.00",
            "99.00",
            "101.00",
            "12.50000000",
            1_767_225_659_999,
            "1262.50000000",
            42,
            "6.00000000",
            "606.00000000",
            "0",
        ],
        [
            1_767_225_660_000,
            "101.00",
            "103.00",
            "100.00",
            "102.00",
            "13.00000000",
            1_767_225_719_999,
            "1326.00000000",
            44,
            "6.50000000",
            "663.00000000",
            "0",
        ],
    ]
    okx_payload = {
        "code": "0",
        "msg": "",
        "data": [
            [
                "1767225660000",
                "101.00",
                "103.00",
                "100.00",
                "102.00",
                "13.00000000",
                "1326.00000000",
                "1326.00000000",
                "1",
            ],
            [
                "1767225600000",
                "100.00",
                "102.00",
                "99.00",
                "101.00",
                "12.50000000",
                "1262.50000000",
                "1262.50000000",
                "1",
            ],
        ],
    }
    return (
        BinanceSpotOhlcvProvider(
            transport=_FixtureTransport(binance_payload),
            clock=lambda: _OFFLINE_NOW,
        ),
        OkxPublicOhlcvProvider(
            transport=_FixtureTransport(okx_payload),
            max_pages=1,
            clock=lambda: _OFFLINE_NOW,
        ),
    )


def _public_network_providers():
    okx_base_url = os.environ.get("OKX_PUBLIC_BASE_URL", "").strip()
    okx_options = {"max_pages": 1}
    if okx_base_url:
        okx_options["base_url"] = okx_base_url
    return (
        BinanceSpotOhlcvProvider(),
        OkxPublicOhlcvProvider(**okx_options),
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
            raise RuntimeError(
                "PostgreSQL persistence requires psycopg or psycopg2; no fallback storage exists"
            ) from exc


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run the bounded public Binance + OKX OHLCV data pipeline.",
    )
    parser.add_argument(
        "--mode",
        choices=("offline-fixture", "public-network"),
        default="offline-fixture",
    )
    parser.add_argument(
        "--persist",
        action="store_true",
        help="Persist only when ENABLE_POSTGRES_PERSISTENCE=true is also set.",
    )
    return parser


def _print_summary(result) -> None:
    print(
        "OHLCV pipeline summary: "
        f"status={result.status.value} symbol={result.symbol} "
        f"timeframe={result.timeframe} persistence={result.persistence is not None}"
    )
    for outcome in result.outcomes:
        validation_status = (
            outcome.validation_report.status.value
            if outcome.validation_report is not None
            else "not_run"
        )
        hashes_valid = bool(outcome.observations) and all(
            _SHA256_PATTERN.fullmatch(item.source_sha256) is not None
            for item in outcome.observations
        )
        error_type = outcome.error.error_type if outcome.error is not None else "none"
        print(
            f"provider={outcome.provider_name} status={outcome.status.value} "
            f"observations={len(outcome.observations)} "
            f"validation={validation_status} hashes_valid={hashes_valid} "
            f"error={error_type}"
        )
    reconciliation_status = (
        result.reconciliation.status.value
        if result.reconciliation is not None
        else "not_run"
    )
    reconciliation_hash_valid = (
        result.reconciliation is not None
        and _SHA256_PATTERN.fullmatch(result.reconciliation.result_sha256) is not None
    )
    print(
        f"reconciliation={reconciliation_status} "
        f"result_hash_valid={reconciliation_hash_valid}"
    )


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.mode == "public-network" and not _enabled(PUBLIC_NETWORK_FLAG):
        print(
            f"Public-network mode is disabled; set {PUBLIC_NETWORK_FLAG}=true explicitly."
        )
        return 2
    if args.persist and not _enabled(POSTGRES_PERSISTENCE_FLAG):
        print(
            f"PostgreSQL persistence is disabled; set {POSTGRES_PERSISTENCE_FLAG}=true "
            "and pass --persist."
        )
        return 2

    connection = None
    try:
        repository = None
        if args.persist:
            connection = _connect_postgres()
            repository = PostgresOhlcvPipelineRepository(connection)

        if args.mode == "offline-fixture":
            providers = _offline_providers()
            start_at_utc = _OFFLINE_START
            end_at_utc = _OFFLINE_END
            pipeline_clock = lambda: _OFFLINE_NOW
        else:
            providers = _public_network_providers()
            now_utc = datetime.now(timezone.utc)
            end_at_utc = now_utc.replace(second=0, microsecond=0) - timedelta(minutes=1)
            start_at_utc = end_at_utc - timedelta(minutes=2)
            pipeline_clock = None

        result = OhlcvPipeline(
            providers,
            repository=repository,
            clock=pipeline_clock,
        ).run(
            OhlcvPipelineRequest(
                collection_run_id=uuid4(),
                validation_run_id=uuid4(),
                provider_names=("binance", "okx"),
                symbol="BTC-USDT",
                timeframe="1m",
                start_at_utc=start_at_utc,
                end_at_utc=end_at_utc,
                limit=2,
                max_pages=1,
                persistence_enabled=args.persist,
                fail_fast=False,
            )
        )
        _print_summary(result)
        return 0 if result.status.value in ("succeeded", "partial") else 1
    finally:
        if connection is not None:
            connection.close()


if __name__ == "__main__":
    raise SystemExit(main())
