"""Typed provider-neutral pipelines for trades, funding, and instruments."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from types import MappingProxyType
from uuid import UUID

from secure_eval_wrapper.data_collection.models import (
    DataRequest,
    FundingRate,
    InstrumentKey,
    InstrumentMetadata,
    InstrumentType,
    MarketDataType,
    NormalizedTrade,
)
from secure_eval_wrapper.data_collection.normalization_extended import (
    normalize_funding_rate_observations,
    normalize_instrument_observations,
    normalize_trade_observations,
)
from secure_eval_wrapper.data_collection.providers import MarketDataProvider
from secure_eval_wrapper.data_collection.symbols import normalize_symbol
from secure_eval_wrapper.data_collection.time_utils import require_utc_datetime
from secure_eval_wrapper.data_pipeline.typed_pipeline import (
    TypedMarketDataPipeline,
    TypedPipelineResult,
)
from secure_eval_wrapper.data_validation.funding import validate_funding_rates
from secure_eval_wrapper.data_validation.gating import (
    accepted_funding_rates,
    accepted_instruments,
    accepted_trades,
)
from secure_eval_wrapper.data_validation.instruments import validate_instruments
from secure_eval_wrapper.data_validation.market_persistence import (
    FundingPersistenceSummary,
    InstrumentPersistenceSummary,
    TradePersistenceSummary,
    persist_funding_validation_flow,
    persist_instrument_validation_flow,
    persist_trade_validation_flow,
)
from secure_eval_wrapper.data_validation.trades import validate_trades
from secure_eval_wrapper.storage.repositories.interfaces import InstrumentSnapshotReader


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(frozen=True)
class TradePipelineRequest:
    collection_run_id: UUID
    validation_run_id: UUID
    provider_names: tuple[str, ...]
    symbol: str
    start_at_utc: datetime
    end_at_utc: datetime
    limit: int = 100
    max_pages: int = 20
    persistence_enabled: bool = False
    fail_fast: bool = False

    def __post_init__(self) -> None:
        names = tuple(sorted(name.strip().lower() for name in self.provider_names))
        if not names or any(not name for name in names) or len(set(names)) != len(names):
            raise ValueError("trade provider_names must be non-empty and unique")
        object.__setattr__(self, "provider_names", names)
        object.__setattr__(self, "symbol", normalize_symbol(self.symbol))
        start = require_utc_datetime(self.start_at_utc, field_name="trade pipeline start")
        end = require_utc_datetime(self.end_at_utc, field_name="trade pipeline end")
        if end <= start:
            raise ValueError("trade pipeline end must be later than start")
        if isinstance(self.limit, bool) or not isinstance(self.limit, int) or not 1 <= self.limit <= 100:
            raise ValueError("trade pipeline limit must be between 1 and 100")
        if isinstance(self.max_pages, bool) or not isinstance(self.max_pages, int) or not 1 <= self.max_pages <= 1000:
            raise ValueError("trade pipeline max_pages must be between 1 and 1000")


class TradePipeline:
    def __init__(
        self,
        providers: Sequence[MarketDataProvider],
        *,
        repository: object | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._clock = _utc_now if clock is None else clock
        self._runner = TypedMarketDataPipeline[NormalizedTrade, TradePersistenceSummary](
            providers,
            data_type=MarketDataType.TRADES,
            fetch=lambda provider, request: provider.fetch_trades(request),
            normalize=normalize_trade_observations,
            validate=self._validate,
            gate=accepted_trades,
            persist=lambda observations, records, report, repository: persist_trade_validation_flow(
                observations,
                records,
                report,
                repository=repository,
                manage_transaction=False,
            ),
            repository=repository,
        )

    def _validate(self, validation_run_id, provider_name, records, request):
        return validate_trades(
            validation_run_id=validation_run_id,
            dataset_ref=(
                f"public-trades:{provider_name}:{request.symbols[0]}:"
                f"{request.start_at_utc.isoformat()}:{request.end_at_utc.isoformat()}"
            ),
            trades=records,
            window_start_utc=request.start_at_utc,
            window_end_utc=request.end_at_utc,
            clock=self._clock,
        )

    def run(self, request: TradePipelineRequest) -> TypedPipelineResult[NormalizedTrade, TradePersistenceSummary]:
        if not isinstance(request, TradePipelineRequest):
            raise TypeError("request must be a TradePipelineRequest")
        requests = {
            provider_name: DataRequest(
                collection_run_id=request.collection_run_id,
                provider_name=provider_name,
                data_type=MarketDataType.TRADES,
                symbols=(request.symbol,),
                start_at_utc=request.start_at_utc,
                end_at_utc=request.end_at_utc,
                limit=request.limit,
                max_pages=request.max_pages,
            )
            for provider_name in request.provider_names
        }
        return self._runner.run(
            collection_run_id=request.collection_run_id,
            validation_run_id=request.validation_run_id,
            requests_by_provider=requests,
            persistence_enabled=request.persistence_enabled,
            fail_fast=request.fail_fast,
        )


@dataclass(frozen=True)
class FundingRatePipelineRequest:
    collection_run_id: UUID
    validation_run_id: UUID
    instruments_by_provider: Mapping[str, InstrumentKey]
    start_at_utc: datetime
    end_at_utc: datetime
    limit: int = 100
    max_pages: int = 20
    persistence_enabled: bool = False
    fail_fast: bool = False

    def __post_init__(self) -> None:
        normalized = {
            name.strip().lower(): key
            for name, key in self.instruments_by_provider.items()
        }
        if not normalized or any(not name for name in normalized):
            raise ValueError("funding instruments_by_provider must not be empty")
        if any(key.provider_name != name for name, key in normalized.items()):
            raise ValueError("funding provider keys must match InstrumentKey provider_name")
        object.__setattr__(self, "instruments_by_provider", MappingProxyType(dict(sorted(normalized.items()))))
        start = require_utc_datetime(self.start_at_utc, field_name="funding pipeline start")
        end = require_utc_datetime(self.end_at_utc, field_name="funding pipeline end")
        if end <= start:
            raise ValueError("funding pipeline end must be later than start")
        if isinstance(self.limit, bool) or not isinstance(self.limit, int) or not 1 <= self.limit <= 400:
            raise ValueError("funding pipeline limit must be between 1 and 400")
        if isinstance(self.max_pages, bool) or not isinstance(self.max_pages, int) or not 1 <= self.max_pages <= 1000:
            raise ValueError("funding pipeline max_pages must be between 1 and 1000")


class FundingRatePipeline:
    def __init__(
        self,
        providers: Sequence[MarketDataProvider],
        *,
        repository: object | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._clock = _utc_now if clock is None else clock
        self._runner = TypedMarketDataPipeline[FundingRate, FundingPersistenceSummary](
            providers,
            data_type=MarketDataType.FUNDING_RATES,
            fetch=lambda provider, request: provider.fetch_funding_rates(request),
            normalize=normalize_funding_rate_observations,
            validate=self._validate,
            gate=accepted_funding_rates,
            persist=lambda observations, records, report, repository: persist_funding_validation_flow(
                observations,
                records,
                report,
                repository=repository,
                manage_transaction=False,
            ),
            repository=repository,
        )

    def _validate(self, validation_run_id, provider_name, records, request):
        key = request.instruments[0]
        return validate_funding_rates(
            validation_run_id=validation_run_id,
            dataset_ref=(
                f"public-funding:{provider_name}:{key.provider_instrument_id}:"
                f"{request.start_at_utc.isoformat()}:{request.end_at_utc.isoformat()}"
            ),
            funding_rates=records,
            window_start_utc=request.start_at_utc,
            window_end_utc=request.end_at_utc,
            clock=self._clock,
        )

    def run(self, request: FundingRatePipelineRequest) -> TypedPipelineResult[FundingRate, FundingPersistenceSummary]:
        if not isinstance(request, FundingRatePipelineRequest):
            raise TypeError("request must be a FundingRatePipelineRequest")
        requests = {
            provider_name: DataRequest(
                collection_run_id=request.collection_run_id,
                provider_name=provider_name,
                data_type=MarketDataType.FUNDING_RATES,
                symbols=(),
                instruments=(key,),
                start_at_utc=request.start_at_utc,
                end_at_utc=request.end_at_utc,
                limit=request.limit,
                max_pages=request.max_pages,
            )
            for provider_name, key in request.instruments_by_provider.items()
        }
        return self._runner.run(
            collection_run_id=request.collection_run_id,
            validation_run_id=request.validation_run_id,
            requests_by_provider=requests,
            persistence_enabled=request.persistence_enabled,
            fail_fast=request.fail_fast,
        )


class MappingInstrumentSnapshotReader:
    """Deterministic in-memory snapshot reader for offline pipelines and tests."""

    def __init__(
        self,
        snapshots: Mapping[tuple[str, str, str], InstrumentMetadata],
    ) -> None:
        normalized: dict[tuple[str, str, str], InstrumentMetadata] = {}
        for raw_key, snapshot in snapshots.items():
            if len(raw_key) != 3 or not isinstance(snapshot, InstrumentMetadata):
                raise TypeError("snapshot mapping requires three-part keys and InstrumentMetadata values")
            key = (
                raw_key[0].strip().lower(),
                raw_key[1].strip(),
                InstrumentType(raw_key[2]).value,
            )
            instrument_key = snapshot.instrument_key
            if instrument_key is None or key != (
                instrument_key.provider_name,
                instrument_key.provider_instrument_id,
                instrument_key.instrument_type.value,
            ):
                raise ValueError("snapshot mapping key conflicts with instrument identity")
            normalized[key] = snapshot
        self._snapshots = MappingProxyType(dict(sorted(normalized.items())))

    def get_instrument_snapshot(
        self,
        *,
        provider_name: str,
        provider_instrument_id: str,
        instrument_type: str,
    ) -> InstrumentMetadata | None:
        return self._snapshots.get((
            provider_name.strip().lower(),
            provider_instrument_id.strip(),
            InstrumentType(instrument_type).value,
        ))


@dataclass(frozen=True)
class InstrumentMetadataPipelineRequest:
    collection_run_id: UUID
    validation_run_id: UUID
    instruments_by_provider: Mapping[str, tuple[InstrumentKey, ...]]
    persistence_enabled: bool = False
    fail_fast: bool = False

    def __post_init__(self) -> None:
        normalized = {
            name.strip().lower(): tuple(keys)
            for name, keys in self.instruments_by_provider.items()
        }
        if not normalized or any(not name or not keys for name, keys in normalized.items()):
            raise ValueError("instrument requests require a non-empty bounded subset per provider")
        if any(
            key.provider_name != provider_name
            for provider_name, keys in normalized.items()
            for key in keys
        ):
            raise ValueError("instrument provider keys must match InstrumentKey provider_name")
        if any(
            len({key.provider_instrument_id for key in keys}) != len(keys)
            for keys in normalized.values()
        ):
            raise ValueError("instrument provider subsets must not contain duplicates")
        object.__setattr__(self, "instruments_by_provider", MappingProxyType(dict(sorted(normalized.items()))))


class InstrumentMetadataPipeline:
    def __init__(
        self,
        providers: Sequence[MarketDataProvider],
        *,
        repository: object | None = None,
        snapshot_reader: InstrumentSnapshotReader | None = None,
        previous_snapshots: Mapping[tuple[str, str, str], InstrumentMetadata] | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        if snapshot_reader is not None and previous_snapshots is not None:
            raise ValueError("provide snapshot_reader or previous_snapshots, not both")
        if previous_snapshots is not None:
            snapshot_reader = MappingInstrumentSnapshotReader(previous_snapshots)
        elif snapshot_reader is None and repository is not None and hasattr(
            repository, "get_instrument_snapshot"
        ):
            snapshot_reader = repository
        self._snapshot_reader = snapshot_reader
        self._clock = _utc_now if clock is None else clock
        self._runner = TypedMarketDataPipeline[InstrumentMetadata, InstrumentPersistenceSummary](
            providers,
            data_type=MarketDataType.INSTRUMENTS,
            fetch=lambda provider, request: provider.fetch_instruments(request),
            normalize=normalize_instrument_observations,
            validate=self._validate,
            gate=accepted_instruments,
            persist=lambda observations, records, report, repository: persist_instrument_validation_flow(
                observations,
                records,
                report,
                repository=repository,
                manage_transaction=False,
            ),
            repository=repository,
        )

    def _validate(self, validation_run_id, provider_name, records, request):
        identities = ",".join(key.provider_instrument_id for key in request.instruments)
        previous = []
        if self._snapshot_reader is not None:
            for key in request.instruments:
                snapshot = self._snapshot_reader.get_instrument_snapshot(
                    provider_name=key.provider_name,
                    provider_instrument_id=key.provider_instrument_id,
                    instrument_type=key.instrument_type.value,
                )
                if snapshot is not None:
                    previous.append(snapshot)
        return validate_instruments(
            validation_run_id=validation_run_id,
            dataset_ref=f"public-instruments:{provider_name}:{identities}",
            instruments=records,
            previous_instruments=tuple(previous),
            clock=self._clock,
        )

    def run(
        self,
        request: InstrumentMetadataPipelineRequest,
    ) -> TypedPipelineResult[InstrumentMetadata, InstrumentPersistenceSummary]:
        if not isinstance(request, InstrumentMetadataPipelineRequest):
            raise TypeError("request must be an InstrumentMetadataPipelineRequest")
        requests = {
            provider_name: DataRequest(
                collection_run_id=request.collection_run_id,
                provider_name=provider_name,
                data_type=MarketDataType.INSTRUMENTS,
                symbols=(),
                instruments=keys,
                limit=len(keys),
            )
            for provider_name, keys in request.instruments_by_provider.items()
        }
        return self._runner.run(
            collection_run_id=request.collection_run_id,
            validation_run_id=request.validation_run_id,
            requests_by_provider=requests,
            persistence_enabled=request.persistence_enabled,
            fail_fast=request.fail_fast,
        )


__all__ = [
    "FundingRatePipeline",
    "FundingRatePipelineRequest",
    "InstrumentMetadataPipeline",
    "MappingInstrumentSnapshotReader",
    "InstrumentMetadataPipelineRequest",
    "TradePipeline",
    "TradePipelineRequest",
]
