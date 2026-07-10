"""Fixture-default, socket-free public Phase 5 backtest demonstration."""

from __future__ import annotations

import argparse
import importlib
import json
import os
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from uuid import NAMESPACE_URL, uuid5

from secure_eval_wrapper.alpha.identity import SeriesIdentity
from secure_eval_wrapper.backtesting.engine import BacktestEngine
from secure_eval_wrapper.backtesting.models import BacktestConfiguration, BacktestRequest
from secure_eval_wrapper.data_collection.hashing import sha256_payload
from secure_eval_wrapper.data_collection.models import InstrumentKey, InstrumentType, NormalizedBar
from secure_eval_wrapper.execution.models import FeeConfiguration, RiskLimitConfiguration, SlippageConfiguration
from secure_eval_wrapper.execution.sizing import SizingConfiguration, SizingMode
from secure_eval_wrapper.signals.models import SignalDirection, SignalRun, SignalRunStatus, StandardizedSignal

DEFAULT_FIXTURE = Path(__file__).resolve().parents[3] / "data" / "sample" / "crypto_ohlcv_sample.json"
IMPLEMENTATION_SHA256 = sha256_payload({"component": "phase5-public-backtest-demo", "version": "1.0.0"})


def _utc(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() != timedelta(0):
        raise ValueError("fixture timestamps must be UTC")
    return parsed.astimezone(timezone.utc)


def load_fixture(path: Path) -> tuple[NormalizedBar, ...]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("classification") != "synthetic_public_safe":
        raise ValueError("backtest demo accepts synthetic_public_safe fixtures only")
    provider = str(payload["provider"])
    exchange = str(payload["exchange"])
    timeframe = str(payload["timeframe"])
    key = InstrumentKey(provider, exchange, "BTC-USDT", "BTC", "USDT", InstrumentType.SPOT, "BTC-USDT")
    rows = []
    for raw in payload["bars"]:
        opened = _utc(raw["open_time_utc"])
        stable = {name: raw[name] for name in ("symbol", "open_time_utc", "open", "high", "low", "close", "volume")}
        source_id = uuid5(NAMESPACE_URL, f"phase5-demo-source:{sha256_payload(stable)}")
        rows.append(NormalizedBar(
            uuid5(NAMESPACE_URL, f"phase5-demo-bar:{sha256_payload(stable)}"), "BTC-USDT", exchange,
            timeframe, opened, Decimal(raw["open"]), Decimal(raw["high"]), Decimal(raw["low"]),
            Decimal(raw["close"]), Decimal(raw["volume"]), (source_id,), opened + timedelta(minutes=1),
            True, {"provider_name": provider, "provider_instrument_id": "BTC-USDT", "instrument_type": "spot", "classification": "synthetic_public_safe"}, key,
        ))
    return tuple(rows)


def demo_signals(bars: tuple[NormalizedBar, ...]) -> tuple[StandardizedSignal, ...]:
    identity = SeriesIdentity("sample_file", "Synthetic Exchange", "BTC-USDT", "BTC-USDT", InstrumentType.SPOT, "1m")
    signal_run_id = uuid5(NAMESPACE_URL, "phase5-public-demo-signal-run")
    directions = (SignalDirection.LONG, SignalDirection.FLAT)
    values = []
    for bar, direction in zip(bars[:2], directions):
        timestamp = bar.bar_close_time_utc
        data_hash = sha256_payload({"series_identity_sha256": identity.series_identity_sha256, "bar_open_time_utc": bar.bar_open_time_utc, "close": bar.close})
        values.append(StandardizedSignal(
            uuid5(NAMESPACE_URL, f"phase5-public-demo-signal:{identity.series_identity_sha256}:{timestamp}:{direction.value}"),
            signal_run_id, ("public_fixture_decision:1.0.0",), (uuid5(NAMESPACE_URL, "phase5-public-demo-alpha-run"),),
            identity.canonical_symbol, timestamp, direction, Decimal(1) if direction is SignalDirection.LONG else Decimal(0),
            Decimal(1) if direction is SignalDirection.LONG else Decimal(0), None, None,
            Decimal("0.5") if direction is SignalDirection.LONG else Decimal(0), "1m",
            (uuid5(NAMESPACE_URL, f"phase5-public-demo-alpha-value:{timestamp}"),), sha256_payload({"demo": "signal-config"}),
            data_hash, IMPLEMENTATION_SHA256, {"classification": "synthetic_public_safe"}, identity,
            repository_commit_sha="source-tree",
        ))
    return tuple(values)


def _persist_upstream_signals(connection, signals) -> None:
    from secure_eval_wrapper.storage.postgres.alpha_signal_repositories import PostgresSignalRepository

    started = min(item.timestamp_utc for item in signals)
    completed = max(item.timestamp_utc for item in signals)
    run = SignalRun(
        signals[0].signal_run_id, signals[0].alpha_run_ids, ("BTC-USDT",), started - timedelta(minutes=1),
        completed + timedelta(microseconds=1), {"method": "fixture"}, {"method": "fixture"},
        {"method": "fixture"}, sha256_payload({"demo": "signal-config"}), IMPLEMENTATION_SHA256,
        sha256_payload(tuple(item.data_sha256 for item in signals)), SignalRunStatus.COMPLETED,
        len(signals), 1, 0, 1, 0, 0, started, completed,
        {"classification": "synthetic_public_safe"}, (signals[0].series_identity.series_identity_sha256,),
        implementation_code_sha256=IMPLEMENTATION_SHA256, repository_commit_sha="source-tree",
    )
    repository = PostgresSignalRepository(connection)
    repository.record_signal_run(run)
    for value in signals:
        repository.record_signal(value)


def _connect_postgres():
    from secure_eval_wrapper.storage.postgres.connection import build_connection_kwargs

    try:
        driver = importlib.import_module("psycopg")
    except ImportError as exc:
        raise RuntimeError("PostgreSQL persistence requires the optional postgres package extra") from exc
    return driver.connect(**build_connection_kwargs())


def build_result(fixture: Path):
    bars = load_fixture(fixture)
    signals = demo_signals(bars)
    configuration = BacktestConfiguration(
        Decimal("10000"), "USDT", SizingConfiguration(SizingMode.FIXED_NOTIONAL, Decimal("1000"), Decimal("0.0001")),
        fees=FeeConfiguration(Decimal("1"), Decimal("2"), "USDT"),
        slippage=SlippageConfiguration(Decimal("5")),
        risk_limits=RiskLimitConfiguration(max_order_notional=Decimal("2000"), max_position_notional_per_series=Decimal("2000"), max_gross_exposure=Decimal("3000")),
    )
    request = BacktestRequest(None, bars, signals, (), configuration, IMPLEMENTATION_SHA256, "source-tree", signals[0].signal_run_id, {"classification": "synthetic_public_safe"})
    return BacktestEngine().run(request), signals


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description="Run the offline public-safe Phase 5 backtest fixture.")
    parser.add_argument("--fixture", type=Path, default=DEFAULT_FIXTURE)
    parser.add_argument("--persist", action="store_true", help="persist only when ENABLE_POSTGRES_PERSISTENCE=true")
    return parser.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)
    result, signals = build_result(args.fixture)
    persistence_status = "disabled"
    if args.persist:
        if os.environ.get("ENABLE_POSTGRES_PERSISTENCE", "").lower() != "true":
            raise RuntimeError("--persist requires ENABLE_POSTGRES_PERSISTENCE=true; no fallback storage exists")
        from secure_eval_wrapper.storage.backtest_bundle import persist_backtest_bundle
        from secure_eval_wrapper.storage.postgres.phase5_repositories import PostgresPhase5Repository

        connection = _connect_postgres()
        try:
            _persist_upstream_signals(connection, signals)
            persist_backtest_bundle(PostgresPhase5Repository(connection), result)
        finally:
            connection.close()
        persistence_status = "postgresql"
    summary = {
        "classification": "synthetic_public_safe", "run_id": str(result.run.backtest_run_id), "execution_lineage_id": str(result.run.run_id),
        "config_sha256": result.run.config_sha256, "data_sha256": result.run.data_sha256,
        "record_sha256": result.run.record_sha256, "initial_equity": str(result.metrics.initial_cash),
        "final_equity": str(result.metrics.final_equity), "net_pnl": str(result.metrics.net_pnl),
        "total_fees": str(result.metrics.total_fees), "total_funding": str(result.metrics.total_funding),
        "maximum_drawdown_amount": str(result.metrics.maximum_drawdown_amount),
        "order_count": result.metrics.order_count, "fill_count": result.metrics.fill_count,
        "reject_count": result.metrics.reject_count, "final_open_positions": result.metrics.final_open_position_count,
        "persistence": persistence_status, "limitations": "bar-level deterministic simulation; no profitability claim",
    }
    print(json.dumps(summary, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
