"""Fixture-default, socket-free public alpha-to-signal demonstration."""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from uuid import NAMESPACE_URL, uuid5


OPEN_CORE_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(OPEN_CORE_ROOT / "src"))

from secure_eval_wrapper.alpha import (  # noqa: E402
    AlphaDataSet,
    AlphaEngine,
    AlphaEvaluationRequest,
    build_public_alpha_registry,
)
from secure_eval_wrapper.data_collection.models import (  # noqa: E402
    FundingIntervalSource,
    FundingRate,
    InstrumentKey,
    InstrumentType,
    NormalizedBar,
)
from secure_eval_wrapper.signals import (  # noqa: E402
    AbsoluteThreshold,
    CombinationConfig,
    RankingConfig,
    SignalDirection,
    SignalPipeline,
    SignalPipelineRequest,
)


POSTGRES_PERSISTENCE_FLAG = "ENABLE_POSTGRES_PERSISTENCE"
FIXTURE_PATH = OPEN_CORE_ROOT / "data" / "sample" / "public_alpha_signal_sample.json"
OFFLINE_CLOCK = datetime(2026, 7, 10, 4, 0, tzinfo=timezone.utc)
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


def _parse_time(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _load_dataset() -> AlphaDataSet:
    fixture = json.loads(FIXTURE_PATH.read_text(encoding="utf-8-sig"))
    if fixture.get("classification") != "synthetic_public_safe":
        raise RuntimeError("alpha fixture must be classified synthetic_public_safe")
    if any(fixture.get(key) for key in ("contains_private_strategy_data", "contains_real_trade_logs", "contains_real_account_data", "downloaded_provider_output")):
        raise RuntimeError("alpha fixture classification flags are not public-safe")
    records = []
    timeframe = str(fixture["timeframe"])
    for index, row in enumerate(fixture["ohlcv"]):
        timestamp = _parse_time(row["timestamp_utc"])
        symbol = str(row["symbol"])
        records.append(
            NormalizedBar(
                bar_id=uuid5(NAMESPACE_URL, f"public-alpha-fixture:bar:{symbol}:{timestamp.isoformat()}"),
                symbol=symbol,
                exchange="synthetic_public_fixture",
                timeframe=timeframe,
                bar_open_time_utc=timestamp,
                open=Decimal(row["open"]),
                high=Decimal(row["high"]),
                low=Decimal(row["low"]),
                close=Decimal(row["close"]),
                volume=Decimal(row["volume"]),
                source_observation_ids=(uuid5(NAMESPACE_URL, f"public-alpha-fixture:ohlcv-source:{index}"),),
                bar_close_time_utc=timestamp + timedelta(hours=1),
                is_final=bool(row["is_final"]),
                provenance={"classification": "synthetic_public_safe", "fixture": FIXTURE_PATH.name},
            )
        )
    for index, row in enumerate(fixture["funding_rates"]):
        timestamp = _parse_time(row["timestamp_utc"])
        symbol = str(row["symbol"])
        key = InstrumentKey(
            provider_name="synthetic_public_fixture",
            exchange_name="synthetic_public_fixture",
            provider_instrument_id=str(row["provider_instrument_id"]),
            base_asset=str(row["base_asset"]),
            quote_asset="USDT",
            settlement_asset="USDT",
            instrument_type=InstrumentType.PERPETUAL_SWAP,
            canonical_symbol=symbol,
        )
        records.append(
            FundingRate(
                funding_rate_id=uuid5(NAMESPACE_URL, f"public-alpha-fixture:funding:{symbol}:{timestamp.isoformat()}"),
                symbol=symbol,
                exchange="synthetic_public_fixture",
                funding_time_utc=timestamp,
                rate=Decimal(row["rate"]),
                source_observation_ids=(uuid5(NAMESPACE_URL, f"public-alpha-fixture:funding-source:{index}"),),
                funding_interval=str(row["funding_interval"]),
                funding_interval_source=FundingIntervalSource(row["funding_interval_source"]),
                instrument_key=key,
                provider_instrument_id=key.provider_instrument_id,
                provenance={"classification": "synthetic_public_safe", "fixture": FIXTURE_PATH.name},
            )
        )
    return AlphaDataSet(
        records=tuple(records),
        validation_status="accepted",
        validation_report_ids=(uuid5(NAMESPACE_URL, "public-alpha-fixture:validation-report"),),
        dataset_ref="synthetic-public-alpha-signal-v1",
    )


def _connect_postgres():
    from secure_eval_wrapper.storage.postgres.config import load_postgres_config
    from secure_eval_wrapper.storage.postgres.connection import build_connection_kwargs
    kwargs = build_connection_kwargs(load_postgres_config())
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
    parser = argparse.ArgumentParser(description="Run the offline public alpha-to-signal fixture pipeline.")
    parser.add_argument("--persist", action="store_true", help="Persist only when ENABLE_POSTGRES_PERSISTENCE=true.")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.persist and os.environ.get(POSTGRES_PERSISTENCE_FLAG, "").strip().lower() != "true":
        print(f"PostgreSQL persistence is disabled; set {POSTGRES_PERSISTENCE_FLAG}=true and pass --persist.")
        return 2
    connection = None
    try:
        repository = None
        if args.persist:
            import secure_eval_wrapper.data_validation  # initialize existing persistence package
            from secure_eval_wrapper.storage.postgres.alpha_signal_repositories import PostgresAlphaSignalRepository
            connection = _connect_postgres()
            repository = PostgresAlphaSignalRepository(connection)
        dataset = _load_dataset()
        registry = build_public_alpha_registry()
        engine = AlphaEngine(registry, repository=repository, clock=lambda: OFFLINE_CLOCK)
        results = {}
        all_records = dataset.records
        for definition in registry.definitions():
            is_funding = definition.required_data_types == ("funding_rates",)
            symbols = ("BTC-USDT-SWAP", "ETH-USDT-SWAP") if is_funding else ("BTC-USDT", "ETH-USDT")
            eligible_times = [
                item.funding_time_utc if isinstance(item, FundingRate) else item.bar_open_time_utc
                for item in all_records
                if item.symbol in symbols
            ]
            alpha_request = AlphaEvaluationRequest(
                evaluation_run_id=uuid5(NAMESPACE_URL, f"public-alpha-cli:{definition.name}:{dataset.dataset_sha256}"),
                alpha_name=definition.name,
                alpha_version=definition.version,
                symbols=symbols,
                window_start_utc=min(eligible_times),
                window_end_utc=max(eligible_times) + timedelta(hours=8 if is_funding else 1),
                dataset_refs=(dataset.dataset_ref,),
                dataset_sha256=dataset.dataset_sha256,
                parameters=definition.default_parameters,
                code_sha256=definition.implementation_code_sha256,
                persistence_enabled=False,
            )
            result = engine.evaluate(alpha_request, dataset)
            results[definition.name] = result
            print(
                f"alpha={definition.name}@{definition.version} status={result.run.status.value} "
                f"valid={result.run.output_count} warmup_skipped={result.run.skipped_count} "
                f"rejected={result.run.rejected_count} hashes_valid={bool(_SHA256.fullmatch(result.run.config_sha256))}"
            )

        signal_results = []
        for name, alpha_result in results.items():
            is_funding = name == "funding_rate_contrarian"
            threshold = AbsoluteThreshold(Decimal("0.00005"), Decimal("-0.00005")) if is_funding else AbsoluteThreshold(Decimal("0.01"), Decimal("-0.01"))
            signal_request = SignalPipelineRequest(
                signal_run_id=uuid5(NAMESPACE_URL, f"public-signal-cli:single:{name}:{dataset.dataset_sha256}"),
                alpha_run_ids=(alpha_result.run.alpha_run_id,),
                symbol_universe=alpha_result.run.symbols,
                window_start_utc=alpha_result.run.window_start_utc,
                window_end_utc=alpha_result.run.window_end_utc,
                ranking_config=RankingConfig(),
                threshold_policy=threshold,
                persistence_enabled=False,
            )
            signal_result = SignalPipeline(repository=repository, clock=lambda: OFFLINE_CLOCK).run(signal_request, (alpha_result.run,), alpha_result.values)
            signal_results.append(signal_result)
            print(
                f"signals={name} status={signal_result.run.status.value} count={signal_result.run.output_count} "
                f"long={signal_result.run.long_count} short={signal_result.run.short_count} flat={signal_result.run.flat_count}"
            )

        combined_names = ("momentum", "moving_average_crossover", "prior_channel_breakout", "trailing_mean_reversion")
        combined_runs = tuple(results[name].run for name in combined_names)
        combined_values = tuple(value for name in combined_names for value in results[name].values)
        combined_request = SignalPipelineRequest(
            signal_run_id=uuid5(NAMESPACE_URL, f"public-signal-cli:combined:{dataset.dataset_sha256}"),
            alpha_run_ids=tuple(item.alpha_run_id for item in combined_runs),
            symbol_universe=("BTC-USDT", "ETH-USDT"),
            window_start_utc=min(item.window_start_utc for item in combined_runs),
            window_end_utc=max(item.window_end_utc for item in combined_runs),
            ranking_config=RankingConfig(),
            threshold_policy=AbsoluteThreshold(Decimal("0.01"), Decimal("-0.01")),
            combination_config=CombinationConfig(minimum_contributors=3, minimum_coverage_ratio=Decimal("0.75")),
            persistence_enabled=False,
        )
        combined = SignalPipeline(repository=repository, clock=lambda: OFFLINE_CLOCK).run(combined_request, combined_runs, combined_values)
        signal_results.append(combined)
        if args.persist:
            from secure_eval_wrapper.storage.alpha_signal_bundle import persist_alpha_signal_bundle
            persist_alpha_signal_bundle(
                repository,
                definitions=registry.definitions(),
                alpha_results=tuple(results.values()),
                signal_results=tuple(signal_results),
            )
        print(
            f"signals=combined status={combined.run.status.value} count={combined.run.output_count} "
            f"long={combined.run.long_count} short={combined.run.short_count} flat={combined.run.flat_count} "
            f"persistence={args.persist} config_hash_valid={bool(_SHA256.fullmatch(combined.run.config_sha256))}"
        )
        return 0
    finally:
        if connection is not None:
            connection.close()


if __name__ == "__main__":
    raise SystemExit(main())
