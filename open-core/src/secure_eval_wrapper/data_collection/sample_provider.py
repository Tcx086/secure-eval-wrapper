"""Offline-only market-data provider backed by a public-safe sample fixture."""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping, Sequence
from datetime import datetime, timezone
from pathlib import Path
from types import MappingProxyType
from uuid import NAMESPACE_URL, uuid5

from secure_eval_wrapper.data_collection.hashing import sha256_observation_source
from secure_eval_wrapper.data_collection.models import (
    CollectionStatus,
    DataRequest,
    MarketDataType,
    ProviderCapabilityStatus,
    ProviderSpec,
    RawObservation,
)
from secure_eval_wrapper.data_collection.providers import MarketDataProvider
from secure_eval_wrapper.data_collection.symbols import normalize_symbol
from secure_eval_wrapper.data_collection.time_utils import (
    coerce_utc_datetime,
    require_utc_datetime,
)


SAMPLE_DATA_ROOT = Path(__file__).resolve().parents[3] / "data" / "sample"
DEFAULT_OHLCV_FIXTURE = "crypto_ohlcv_sample.json"

SAMPLE_PROVIDER_SPEC = ProviderSpec(
    name="sample_file",
    display_name="Offline Sample File",
    exchange_name="Synthetic Exchange",
    capabilities=MappingProxyType(
        {
            MarketDataType.OHLCV: ProviderCapabilityStatus.IMPLEMENTED,
            MarketDataType.TRADES: ProviderCapabilityStatus.UNKNOWN,
            MarketDataType.FUNDING_RATES: ProviderCapabilityStatus.UNKNOWN,
            MarketDataType.INSTRUMENTS: ProviderCapabilityStatus.UNKNOWN,
        }
    ),
)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _utc_isoformat(value: datetime | None) -> str | None:
    if value is None:
        return None
    return require_utc_datetime(value).isoformat().replace("+00:00", "Z")


class SampleProvider(MarketDataProvider):
    """Read synthetic OHLCV observations from ``open-core/data/sample`` only."""

    def __init__(
        self,
        *,
        fixture_name: str = DEFAULT_OHLCV_FIXTURE,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._fixture_path = self._resolve_fixture_path(fixture_name)
        self._clock = _utc_now if clock is None else clock

    @property
    def spec(self) -> ProviderSpec:
        """Return metadata for the offline-only sample provider."""

        return SAMPLE_PROVIDER_SPEC

    def fetch_ohlcv(self, request: DataRequest) -> Sequence[RawObservation]:
        """Read matching OHLCV records from the configured local JSON fixture."""

        start_at_utc, end_at_utc, requested_symbols = self._validate_ohlcv_request(request)
        fixture = self._load_fixture()
        fixture_timeframe = self._required_text(fixture, "timeframe")
        if request.timeframe is not None and request.timeframe != fixture_timeframe:
            return ()

        source_endpoint = f"sample-file:{self._fixture_path.name}"
        request_parameters: Mapping[str, object] = {
            "provider_name": self.spec.name,
            "source_endpoint": source_endpoint,
            "data_type": request.data_type.value,
            "symbols": sorted(requested_symbols),
            "timeframe": request.timeframe,
            "start_at_utc": _utc_isoformat(start_at_utc),
            "end_at_utc": _utc_isoformat(end_at_utc),
            "limit": request.limit,
            "parameters": dict(request.parameters),
        }
        request_timestamp_utc = require_utc_datetime(
            self._clock(),
            field_name="sample provider clock",
        )

        bars = fixture.get("bars")
        if not isinstance(bars, list):
            raise ValueError("sample fixture 'bars' must be a list")

        observations: list[RawObservation] = []
        for item in bars:
            if not isinstance(item, dict):
                raise ValueError("each sample OHLCV bar must be a JSON object")
            payload = dict(item)
            raw_symbol = self._required_text(payload, "symbol")
            normalized_symbol = normalize_symbol(raw_symbol)
            if normalized_symbol not in requested_symbols:
                continue

            provider_timestamp = self._required_text(payload, "open_time_utc")
            observed_at_utc = coerce_utc_datetime(
                provider_timestamp,
                field_name="sample OHLCV open_time_utc",
            )
            if start_at_utc is not None and observed_at_utc < start_at_utc:
                continue
            if end_at_utc is not None and observed_at_utc >= end_at_utc:
                continue

            source_sha256 = sha256_observation_source(
                payload=payload,
                request_metadata=request_parameters,
            )
            observation_id = uuid5(
                NAMESPACE_URL,
                f"{request.collection_run_id}:{source_sha256}",
            )
            observations.append(
                RawObservation(
                    observation_id=observation_id,
                    collection_run_id=request.collection_run_id,
                    provider_name=self.spec.name,
                    exchange_name=self.spec.exchange_name,
                    source_endpoint=source_endpoint,
                    request_parameters=request_parameters,
                    request_timestamp_utc=request_timestamp_utc,
                    ingested_at_utc=request_timestamp_utc,
                    data_type=MarketDataType.OHLCV,
                    payload=payload,
                    source_sha256=source_sha256,
                    collection_status=CollectionStatus.SUCCEEDED,
                    raw_symbol=raw_symbol,
                    normalized_symbol=normalized_symbol,
                    timeframe=fixture_timeframe,
                    observed_at_utc=observed_at_utc,
                    provider_timestamp=provider_timestamp,
                )
            )
            if request.limit is not None and len(observations) >= request.limit:
                break

        return tuple(observations)

    def fetch_trades(self, request: DataRequest) -> Sequence[RawObservation]:
        """Indicate that no offline public-trade fixture is available."""

        raise NotImplementedError("offline trade fixture is not available")

    def fetch_funding_rates(self, request: DataRequest) -> Sequence[RawObservation]:
        """Indicate that no offline funding-rate fixture is available."""

        raise NotImplementedError("offline funding-rate fixture is not available")

    def fetch_instruments(self, request: DataRequest) -> Sequence[RawObservation]:
        """Indicate that no offline instrument fixture is available."""

        raise NotImplementedError("offline instrument fixture is not available")

    @staticmethod
    def _resolve_fixture_path(fixture_name: str) -> Path:
        if not isinstance(fixture_name, str) or not fixture_name.strip():
            raise ValueError("fixture_name must be a non-empty filename")
        if fixture_name != Path(fixture_name).name or "/" in fixture_name or "\\" in fixture_name:
            raise ValueError("fixture_name must not contain a directory path")
        if Path(fixture_name).suffix.lower() != ".json":
            raise ValueError("sample provider supports JSON fixtures only")

        sample_root = SAMPLE_DATA_ROOT.resolve()
        fixture_path = (sample_root / fixture_name).resolve()
        if fixture_path.parent != sample_root:
            raise ValueError("fixture must resolve directly under open-core/data/sample")
        return fixture_path

    def _load_fixture(self) -> Mapping[str, object]:
        with self._fixture_path.open("r", encoding="utf-8") as handle:
            fixture = json.load(handle)
        if not isinstance(fixture, dict):
            raise ValueError("sample fixture must be a JSON object")
        if fixture.get("classification") != "synthetic_public_safe":
            raise ValueError("sample fixture must be classified synthetic_public_safe")
        if fixture.get("data_type") != MarketDataType.OHLCV.value:
            raise ValueError("sample fixture data_type must be ohlcv")
        return fixture

    @staticmethod
    def _required_text(payload: Mapping[str, object], key: str) -> str:
        value = payload.get(key)
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"sample fixture '{key}' must be a non-empty string")
        return value

    def _validate_ohlcv_request(
        self,
        request: DataRequest,
    ) -> tuple[datetime | None, datetime | None, frozenset[str]]:
        if request.provider_name != self.spec.name:
            raise ValueError(f"request provider_name must be '{self.spec.name}'")
        if request.data_type is not MarketDataType.OHLCV:
            raise ValueError("fetch_ohlcv requires an OHLCV DataRequest")
        if not request.symbols:
            raise ValueError("sample OHLCV request must include at least one symbol")
        requested_symbols = frozenset(normalize_symbol(symbol) for symbol in request.symbols)

        start_at_utc = (
            None
            if request.start_at_utc is None
            else require_utc_datetime(request.start_at_utc, field_name="request start_at_utc")
        )
        end_at_utc = (
            None
            if request.end_at_utc is None
            else require_utc_datetime(request.end_at_utc, field_name="request end_at_utc")
        )
        if start_at_utc is not None and end_at_utc is not None and end_at_utc < start_at_utc:
            raise ValueError("request end_at_utc must be greater than or equal to start_at_utc")
        if request.limit is not None and request.limit <= 0:
            raise ValueError("request limit must be positive")
        return start_at_utc, end_at_utc, requested_symbols
