"""Binance Spot public OHLCV adapter for ``GET /api/v3/klines`` only."""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping, Sequence
from datetime import datetime, timedelta, timezone
from types import MappingProxyType
from uuid import NAMESPACE_URL, uuid5

from secure_eval_wrapper.data_collection.hashing import sha256_observation_source
from secure_eval_wrapper.data_collection.http_transport import (
    HttpRequest,
    HttpTransport,
    TransportError,
    UrlLibHttpTransport,
)
from secure_eval_wrapper.data_collection.models import (
    CollectionStatus,
    DataRequest,
    MarketDataType,
    ProviderCapabilityStatus,
    ProviderSpec,
    RawObservation,
)
from secure_eval_wrapper.data_collection.providers import MarketDataProvider
from secure_eval_wrapper.data_collection.symbols import normalize_symbol, split_base_quote
from secure_eval_wrapper.data_collection.time_utils import require_utc_datetime


BINANCE_SPOT_BASE_URL = "https://api.binance.com"
BINANCE_KLINES_PATH = "/api/v3/klines"
BINANCE_KLINES_SOURCE_ENDPOINT = "binance-spot:/api/v3/klines"
BINANCE_MAX_KLINES_LIMIT = 1000

BINANCE_SPOT_OHLCV_SPEC = ProviderSpec(
    name="binance",
    display_name="Binance",
    exchange_name="Binance",
    capabilities=MappingProxyType(
        {
            MarketDataType.OHLCV: ProviderCapabilityStatus.IMPLEMENTED,
            MarketDataType.TRADES: ProviderCapabilityStatus.PLANNED,
            MarketDataType.FUNDING_RATES: ProviderCapabilityStatus.PLANNED,
            MarketDataType.INSTRUMENTS: ProviderCapabilityStatus.PLANNED,
        }
    ),
)

_EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)
_SUPPORTED_INTERVALS = frozenset(
    {
        "1s",
        "1m",
        "3m",
        "5m",
        "15m",
        "30m",
        "1h",
        "2h",
        "4h",
        "6h",
        "8h",
        "12h",
        "1d",
        "3d",
        "1w",
        "1M",
    }
)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _epoch_microseconds(value: datetime, *, field_name: str) -> int:
    value = require_utc_datetime(value, field_name=field_name)
    delta = value - _EPOCH
    microseconds = (
        (delta.days * 86_400 + delta.seconds) * 1_000_000
        + delta.microseconds
    )
    if microseconds < 0:
        raise ValueError(f"{field_name} must not precede the Unix epoch")
    return microseconds


def _inclusive_start_milliseconds(value: datetime, *, field_name: str) -> int:
    microseconds = _epoch_microseconds(value, field_name=field_name)
    return (microseconds + 999) // 1000


def _exclusive_end_to_inclusive_milliseconds(
    value: datetime,
    *,
    field_name: str,
) -> int:
    microseconds = _epoch_microseconds(value, field_name=field_name)
    if microseconds == 0:
        raise ValueError(f"{field_name} must be later than the Unix epoch")
    return (microseconds - 1) // 1000


def _milliseconds_to_utc(value: object, *, field_name: str) -> datetime:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"Binance kline {field_name} must be a non-negative integer")
    return _EPOCH + timedelta(milliseconds=value)


def _utc_isoformat(value: datetime) -> str:
    return require_utc_datetime(value).isoformat(timespec="milliseconds").replace(
        "+00:00",
        "Z",
    )


class BinanceSpotOhlcvProvider(MarketDataProvider):
    """Fetch public Binance Spot klines through an injectable HTTP transport."""

    def __init__(
        self,
        *,
        transport: HttpTransport | None = None,
        timeout: float = 10.0,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        if (
            isinstance(timeout, bool)
            or not isinstance(timeout, (int, float))
            or timeout <= 0
        ):
            raise ValueError("Binance HTTP timeout must be positive")
        self._transport = UrlLibHttpTransport() if transport is None else transport
        self._timeout = timeout
        self._clock = _utc_now if clock is None else clock

    @property
    def spec(self) -> ProviderSpec:
        """Return public-only Binance Spot capability metadata."""

        return BINANCE_SPOT_OHLCV_SPEC

    def fetch_ohlcv(self, request: DataRequest) -> Sequence[RawObservation]:
        """Fetch and parse public Binance Spot klines for one conservative symbol."""

        (
            normalized_symbol,
            provider_symbol,
            start_at_utc,
            end_at_utc,
            query_params,
        ) = self._validate_ohlcv_request(request)
        http_request = self._build_public_request(BINANCE_KLINES_PATH, query_params)
        request_timestamp_utc = require_utc_datetime(
            self._clock(),
            field_name="Binance provider request clock",
        )
        response = self._transport.send(http_request)
        ingested_at_utc = require_utc_datetime(
            self._clock(),
            field_name="Binance provider ingestion clock",
        )
        if response.status != 200:
            raise TransportError(
                "Binance public kline request returned "
                f"HTTP {response.status} from {BINANCE_KLINES_PATH}"
            )

        try:
            decoded = json.loads(response.body_text)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError("Binance kline response must be valid UTF-8 JSON") from exc
        if not isinstance(decoded, list):
            raise ValueError("Binance kline response must be a JSON list")

        request_parameters: Mapping[str, object] = dict(query_params)
        observations: list[RawObservation] = []
        for position, raw_kline in enumerate(decoded):
            payload, observed_at_utc, provider_timestamp = self._parse_kline(
                raw_kline,
                normalized_symbol=normalized_symbol,
                position=position,
            )
            if start_at_utc is not None and observed_at_utc < start_at_utc:
                continue
            if end_at_utc is not None and observed_at_utc >= end_at_utc:
                continue

            source_sha256 = sha256_observation_source(
                payload=payload,
                request_metadata=request_parameters,
            )
            observations.append(
                RawObservation(
                    observation_id=uuid5(
                        NAMESPACE_URL,
                        f"{request.collection_run_id}:{source_sha256}",
                    ),
                    collection_run_id=request.collection_run_id,
                    provider_name=self.spec.name,
                    exchange_name=self.spec.exchange_name,
                    source_endpoint=BINANCE_KLINES_SOURCE_ENDPOINT,
                    request_parameters=request_parameters,
                    request_timestamp_utc=request_timestamp_utc,
                    ingested_at_utc=ingested_at_utc,
                    data_type=MarketDataType.OHLCV,
                    payload=payload,
                    source_sha256=source_sha256,
                    collection_status=CollectionStatus.SUCCEEDED,
                    raw_symbol=provider_symbol,
                    normalized_symbol=normalized_symbol,
                    timeframe=request.timeframe,
                    observed_at_utc=observed_at_utc,
                    provider_timestamp=provider_timestamp,
                )
            )
        return tuple(observations)

    def fetch_trades(self, request: DataRequest) -> Sequence[RawObservation]:
        """Keep public-trade collection outside Phase 2E."""

        raise NotImplementedError("Binance public trade collection is not implemented")

    def fetch_funding_rates(self, request: DataRequest) -> Sequence[RawObservation]:
        """Keep funding-rate collection outside Phase 2E."""

        raise NotImplementedError("Binance funding-rate collection is not implemented")

    def fetch_instruments(self, request: DataRequest) -> Sequence[RawObservation]:
        """Keep instrument collection outside Phase 2E."""

        raise NotImplementedError("Binance instrument collection is not implemented")

    def _build_public_request(
        self,
        path: str,
        query_params: Mapping[str, str | int],
    ) -> HttpRequest:
        if path != BINANCE_KLINES_PATH:
            raise ValueError(
                "Binance Spot OHLCV adapter permits only the public /api/v3/klines path"
            )
        return HttpRequest(
            method="GET",
            url=f"{BINANCE_SPOT_BASE_URL}{path}",
            query_params=query_params,
            timeout=self._timeout,
            headers={},
        )

    def _validate_ohlcv_request(
        self,
        request: DataRequest,
    ) -> tuple[
        str,
        str,
        datetime | None,
        datetime | None,
        Mapping[str, str | int],
    ]:
        if not isinstance(request, DataRequest):
            raise TypeError("request must be a DataRequest")
        if request.provider_name != self.spec.name:
            raise ValueError(f"request provider_name must be '{self.spec.name}'")
        if request.data_type is not MarketDataType.OHLCV:
            raise ValueError("fetch_ohlcv requires an OHLCV DataRequest")
        if len(request.symbols) != 1:
            raise ValueError("Binance kline requests require exactly one canonical symbol")

        normalized_symbol = normalize_symbol(request.symbols[0])
        base_asset, quote_asset = split_base_quote(normalized_symbol)
        provider_symbol = f"{base_asset}{quote_asset}"

        if not isinstance(request.timeframe, str) or request.timeframe not in _SUPPORTED_INTERVALS:
            raise ValueError("request timeframe must be a supported Binance kline interval")
        if request.parameters:
            raise ValueError(
                "Binance Phase 2E OHLCV requests do not accept additional query parameters"
            )
        if request.limit is not None:
            if (
                isinstance(request.limit, bool)
                or not isinstance(request.limit, int)
                or request.limit <= 0
            ):
                raise ValueError("request limit must be positive")
            if request.limit > BINANCE_MAX_KLINES_LIMIT:
                raise ValueError("Binance kline request limit must not exceed 1000")

        start_at_utc = (
            None
            if request.start_at_utc is None
            else require_utc_datetime(
                request.start_at_utc,
                field_name="request start_at_utc",
            )
        )
        end_at_utc = (
            None
            if request.end_at_utc is None
            else require_utc_datetime(
                request.end_at_utc,
                field_name="request end_at_utc",
            )
        )
        if (
            start_at_utc is not None
            and end_at_utc is not None
            and end_at_utc <= start_at_utc
        ):
            raise ValueError("request end_at_utc must be later than start_at_utc")

        query_params: dict[str, str | int] = {
            "symbol": provider_symbol,
            "interval": request.timeframe,
        }
        if start_at_utc is not None:
            query_params["startTime"] = _inclusive_start_milliseconds(
                start_at_utc,
                field_name="request start_at_utc",
            )
        if end_at_utc is not None:
            query_params["endTime"] = _exclusive_end_to_inclusive_milliseconds(
                end_at_utc,
                field_name="request end_at_utc",
            )
        if request.limit is not None:
            query_params["limit"] = request.limit
        return (
            normalized_symbol,
            provider_symbol,
            start_at_utc,
            end_at_utc,
            query_params,
        )

    @staticmethod
    def _parse_kline(
        raw_kline: object,
        *,
        normalized_symbol: str,
        position: int,
    ) -> tuple[Mapping[str, object], datetime, str]:
        if not isinstance(raw_kline, list) or len(raw_kline) != 12:
            raise ValueError(
                f"Binance kline at position {position} must be a 12-element list"
            )
        open_time_utc = _milliseconds_to_utc(
            raw_kline[0],
            field_name="open time",
        )
        close_time_utc = _milliseconds_to_utc(
            raw_kline[6],
            field_name="close time",
        )
        if close_time_utc < open_time_utc:
            raise ValueError(
                f"Binance kline at position {position} closes before it opens"
            )

        numeric_names = ("open", "high", "low", "close", "volume")
        numeric_values: dict[str, str] = {}
        for index, name in enumerate(numeric_names, start=1):
            value = raw_kline[index]
            if not isinstance(value, str) or not value.strip():
                raise ValueError(
                    f"Binance kline at position {position} {name} must be a string"
                )
            numeric_values[name] = value

        provider_timestamp = str(raw_kline[0])
        payload: Mapping[str, object] = {
            "provider_payload": list(raw_kline),
            "symbol": normalized_symbol,
            "open_time_utc": _utc_isoformat(open_time_utc),
            "close_time_utc": _utc_isoformat(close_time_utc),
            **numeric_values,
        }
        return payload, open_time_utc, provider_timestamp


__all__ = [
    "BINANCE_KLINES_PATH",
    "BINANCE_KLINES_SOURCE_ENDPOINT",
    "BINANCE_MAX_KLINES_LIMIT",
    "BINANCE_SPOT_BASE_URL",
    "BINANCE_SPOT_OHLCV_SPEC",
    "BinanceSpotOhlcvProvider",
]
