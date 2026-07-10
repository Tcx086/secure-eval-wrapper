"""OKX V5 public spot OHLCV adapter for historical candlesticks only."""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping, Sequence
from datetime import datetime, timedelta, timezone
from types import MappingProxyType
from urllib.parse import urlparse
from uuid import NAMESPACE_URL, uuid5

from secure_eval_wrapper.data_collection.hashing import sha256_observation_source
from secure_eval_wrapper.data_collection.http_transport import (
    HttpRequest,
    HttpResponse,
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


OKX_PUBLIC_BASE_URL = "https://openapi.okx.com"
OKX_HISTORY_CANDLES_PATH = "/api/v5/market/history-candles"
OKX_HISTORY_CANDLES_SOURCE_ENDPOINT = "okx-v5:/api/v5/market/history-candles"
OKX_MAX_CANDLES_LIMIT = 300
OKX_DEFAULT_CANDLES_LIMIT = 100
OKX_MAX_PAGE_GUARD = 1_000

OKX_PUBLIC_OHLCV_SPEC = ProviderSpec(
    name="okx",
    display_name="OKX",
    exchange_name="OKX",
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
_ALLOWED_API_HOSTS = frozenset(
    {
        "openapi.okx.com",
        "us.okx.com",
        "eea.okx.com",
    }
)
_TIMEFRAME_TO_BAR = MappingProxyType(
    {
        "1m": "1m",
        "3m": "3m",
        "5m": "5m",
        "15m": "15m",
        "30m": "30m",
        "1h": "1H",
        "2h": "2H",
        "4h": "4H",
        "6h": "6Hutc",
        "12h": "12Hutc",
        "1d": "1Dutc",
        "3d": "3Dutc",
        "1w": "1Wutc",
    }
)
_TIMEFRAME_DELTAS = MappingProxyType(
    {
        "1m": timedelta(minutes=1),
        "3m": timedelta(minutes=3),
        "5m": timedelta(minutes=5),
        "15m": timedelta(minutes=15),
        "30m": timedelta(minutes=30),
        "1h": timedelta(hours=1),
        "2h": timedelta(hours=2),
        "4h": timedelta(hours=4),
        "6h": timedelta(hours=6),
        "12h": timedelta(hours=12),
        "1d": timedelta(days=1),
        "3d": timedelta(days=3),
        "1w": timedelta(weeks=1),
    }
)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _epoch_microseconds(value: datetime, *, field_name: str) -> int:
    value = require_utc_datetime(value, field_name=field_name)
    delta = value - _EPOCH
    microseconds = (delta.days * 86_400 + delta.seconds) * 1_000_000 + delta.microseconds
    if microseconds < 0:
        raise ValueError(f"{field_name} must not precede the Unix epoch")
    return microseconds


def _ceiling_epoch_milliseconds(value: datetime, *, field_name: str) -> int:
    microseconds = _epoch_microseconds(value, field_name=field_name)
    return (microseconds + 999) // 1_000


def _milliseconds_to_utc(value: object, *, position: int) -> tuple[datetime, int]:
    if not isinstance(value, str) or not value.isdigit():
        raise ValueError(
            f"OKX candle at position {position} timestamp must be a non-negative millisecond string"
        )
    milliseconds = int(value)
    return _EPOCH + timedelta(milliseconds=milliseconds), milliseconds


def _utc_isoformat(value: datetime) -> str:
    return require_utc_datetime(value).isoformat(timespec="milliseconds").replace(
        "+00:00",
        "Z",
    )


def _validate_base_url(base_url: str) -> str:
    if not isinstance(base_url, str) or not base_url.strip():
        raise ValueError("OKX public base URL must be a non-empty string")
    normalized = base_url.strip().rstrip("/")
    parsed = urlparse(normalized)
    if (
        parsed.scheme != "https"
        or parsed.hostname not in _ALLOWED_API_HOSTS
        or parsed.path not in ("", "/")
        or parsed.params
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("OKX public base URL must be an approved HTTPS OKX API domain")
    return normalized


class OkxPublicOhlcvProvider(MarketDataProvider):
    """Fetch OKX V5 public historical candles through an injectable transport."""

    def __init__(
        self,
        *,
        transport: HttpTransport | None = None,
        base_url: str = OKX_PUBLIC_BASE_URL,
        timeout: float = 10.0,
        max_pages: int = 20,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        if (
            isinstance(timeout, bool)
            or not isinstance(timeout, (int, float))
            or timeout <= 0
        ):
            raise ValueError("OKX HTTP timeout must be positive")
        if (
            isinstance(max_pages, bool)
            or not isinstance(max_pages, int)
            or max_pages <= 0
            or max_pages > OKX_MAX_PAGE_GUARD
        ):
            raise ValueError(
                f"OKX max_pages must be between 1 and {OKX_MAX_PAGE_GUARD}"
            )
        self._transport = UrlLibHttpTransport() if transport is None else transport
        self._base_url = _validate_base_url(base_url)
        self._timeout = timeout
        self._max_pages = max_pages
        self._clock = _utc_now if clock is None else clock

    @property
    def spec(self) -> ProviderSpec:
        """Return public-only OKX capability metadata."""

        return OKX_PUBLIC_OHLCV_SPEC

    def fetch_ohlcv(self, request: DataRequest) -> Sequence[RawObservation]:
        """Fetch a bounded half-open UTC window from the OKX history endpoint."""

        (
            normalized_symbol,
            provider_symbol,
            start_at_utc,
            end_at_utc,
            page_limit,
            max_pages,
            base_query,
        ) = self._validate_ohlcv_request(request)
        cursor = _ceiling_epoch_milliseconds(
            end_at_utc,
            field_name="request end_at_utc",
        )
        requested_cursors = {cursor}
        observations_by_timestamp: dict[int, RawObservation] = {}

        for page_number in range(1, max_pages + 1):
            query_params = {**base_query, "after": str(cursor)}
            http_request = self._build_public_request(
                OKX_HISTORY_CANDLES_PATH,
                query_params,
            )
            request_timestamp_utc = require_utc_datetime(
                self._clock(),
                field_name="OKX provider request clock",
            )
            response = self._transport.send(http_request)
            ingested_at_utc = require_utc_datetime(
                self._clock(),
                field_name="OKX provider ingestion clock",
            )
            candles = self._decode_response(response)
            if not candles:
                break

            page_timestamps: list[tuple[datetime, int]] = []
            for position, raw_candle in enumerate(candles):
                payload, observed_at_utc, provider_timestamp, timestamp_ms = (
                    self._parse_candle(
                        raw_candle,
                        normalized_symbol=normalized_symbol,
                        timeframe=request.timeframe,
                        position=position,
                    )
                )
                page_timestamps.append((observed_at_utc, timestamp_ms))
                if observed_at_utc < start_at_utc or observed_at_utc >= end_at_utc:
                    continue
                if timestamp_ms in observations_by_timestamp:
                    raise ValueError(
                        "OKX candle pagination returned a duplicate timestamp across pages"
                    )

                request_parameters: Mapping[str, object] = dict(query_params)
                source_sha256 = sha256_observation_source(
                    payload=payload,
                    request_metadata=request_parameters,
                )
                observations_by_timestamp[timestamp_ms] = RawObservation(
                    observation_id=uuid5(
                        NAMESPACE_URL,
                        f"{request.collection_run_id}:{source_sha256}",
                    ),
                    collection_run_id=request.collection_run_id,
                    provider_name=self.spec.name,
                    exchange_name=self.spec.exchange_name,
                    source_endpoint=OKX_HISTORY_CANDLES_SOURCE_ENDPOINT,
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

            oldest_time, next_cursor = min(page_timestamps, key=lambda item: item[1])
            if next_cursor >= cursor or next_cursor in requested_cursors:
                raise ValueError("OKX candle pagination cursor did not advance")
            if oldest_time <= start_at_utc or len(candles) < page_limit:
                break
            if page_number == max_pages:
                raise ValueError(
                    "OKX candle pagination exceeded max_pages before reaching start_at_utc"
                )
            requested_cursors.add(next_cursor)
            cursor = next_cursor

        return tuple(
            observations_by_timestamp[timestamp]
            for timestamp in sorted(observations_by_timestamp)
        )

    def fetch_trades(self, request: DataRequest) -> Sequence[RawObservation]:
        raise NotImplementedError("OKX public trade collection is not implemented")

    def fetch_funding_rates(self, request: DataRequest) -> Sequence[RawObservation]:
        raise NotImplementedError("OKX funding-rate collection is not implemented")

    def fetch_instruments(self, request: DataRequest) -> Sequence[RawObservation]:
        raise NotImplementedError("OKX instrument collection is not implemented")

    def _build_public_request(
        self,
        path: str,
        query_params: Mapping[str, str | int],
    ) -> HttpRequest:
        if path != OKX_HISTORY_CANDLES_PATH:
            raise ValueError(
                "OKX OHLCV adapter permits only the public V5 history-candles path"
            )
        return HttpRequest(
            method="GET",
            url=f"{self._base_url}{path}",
            query_params=query_params,
            timeout=self._timeout,
            headers={},
        )

    def _validate_ohlcv_request(
        self,
        request: DataRequest,
    ) -> tuple[str, str, datetime, datetime, int, int, Mapping[str, str | int]]:
        if not isinstance(request, DataRequest):
            raise TypeError("request must be a DataRequest")
        if request.provider_name != self.spec.name:
            raise ValueError(f"request provider_name must be '{self.spec.name}'")
        if request.data_type is not MarketDataType.OHLCV:
            raise ValueError("fetch_ohlcv requires an OHLCV DataRequest")
        if len(request.symbols) != 1:
            raise ValueError("OKX candle requests require exactly one canonical symbol")

        normalized_symbol = normalize_symbol(request.symbols[0])
        base_asset, quote_asset = split_base_quote(normalized_symbol)
        provider_symbol = f"{base_asset}-{quote_asset}"
        if request.timeframe not in _TIMEFRAME_TO_BAR:
            raise ValueError("request timeframe must be a supported OKX UTC candle interval")
        if request.parameters:
            raise ValueError("OKX public OHLCV requests do not accept extra parameters")
        if request.start_at_utc is None or request.end_at_utc is None:
            raise ValueError("OKX history requests require explicit UTC start and end boundaries")
        start_at_utc = require_utc_datetime(
            request.start_at_utc,
            field_name="request start_at_utc",
        )
        end_at_utc = require_utc_datetime(
            request.end_at_utc,
            field_name="request end_at_utc",
        )
        if end_at_utc <= start_at_utc:
            raise ValueError("request end_at_utc must be later than start_at_utc")

        page_limit = OKX_DEFAULT_CANDLES_LIMIT if request.limit is None else request.limit
        if (
            isinstance(page_limit, bool)
            or not isinstance(page_limit, int)
            or page_limit <= 0
        ):
            raise ValueError("request limit must be positive")
        if page_limit > OKX_MAX_CANDLES_LIMIT:
            raise ValueError(
                f"OKX candle request limit must not exceed {OKX_MAX_CANDLES_LIMIT}"
            )

        if request.max_pages is not None and (
            isinstance(request.max_pages, bool)
            or not isinstance(request.max_pages, int)
            or request.max_pages <= 0
            or request.max_pages > OKX_MAX_PAGE_GUARD
        ):
            raise ValueError(
                f"request max_pages must be between 1 and {OKX_MAX_PAGE_GUARD}"
            )
        max_pages = (
            self._max_pages
            if request.max_pages is None
            else min(self._max_pages, request.max_pages)
        )

        query_params: Mapping[str, str | int] = {
            "instId": provider_symbol,
            "bar": _TIMEFRAME_TO_BAR[request.timeframe],
            "limit": str(page_limit),
        }
        return (
            normalized_symbol,
            provider_symbol,
            start_at_utc,
            end_at_utc,
            page_limit,
            max_pages,
            query_params,
        )

    @staticmethod
    def _decode_response(response: HttpResponse) -> list[object]:
        if not isinstance(response, HttpResponse):
            raise TypeError("OKX transport must return an HttpResponse")
        if response.status != 200:
            raise TransportError(
                "OKX public history-candles request returned "
                f"HTTP {response.status} from {OKX_HISTORY_CANDLES_PATH}"
            )
        try:
            decoded = json.loads(response.body_text)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError("OKX candle response must be valid UTF-8 JSON") from exc
        if not isinstance(decoded, Mapping):
            raise ValueError("OKX candle response must be a JSON object")
        code = decoded.get("code")
        message = decoded.get("msg")
        data = decoded.get("data")
        if code != "0":
            raise ValueError(f"OKX candle response returned result code {code!r}: {message!r}")
        if not isinstance(message, str):
            raise ValueError("OKX candle response msg must be a string")
        if not isinstance(data, list):
            raise ValueError("OKX candle response data must be a list")
        return data

    @staticmethod
    def _parse_candle(
        raw_candle: object,
        *,
        normalized_symbol: str,
        timeframe: str,
        position: int,
    ) -> tuple[Mapping[str, object], datetime, str, int]:
        if not isinstance(raw_candle, list) or len(raw_candle) != 9:
            raise ValueError(
                f"OKX candle at position {position} must be a 9-element list"
            )
        open_time_utc, timestamp_ms = _milliseconds_to_utc(
            raw_candle[0],
            position=position,
        )
        numeric_names = (
            "open",
            "high",
            "low",
            "close",
            "volume",
            "volume_currency",
            "volume_quote_currency",
        )
        numeric_values: dict[str, str] = {}
        for index, name in enumerate(numeric_names, start=1):
            value = raw_candle[index]
            if not isinstance(value, str) or not value.strip():
                raise ValueError(
                    f"OKX candle at position {position} {name} must be a string"
                )
            numeric_values[name] = value
        confirm = raw_candle[8]
        if confirm not in ("0", "1"):
            raise ValueError(
                f"OKX candle at position {position} confirm must be '0' or '1'"
            )
        close_time_utc = open_time_utc + _TIMEFRAME_DELTAS[timeframe] - timedelta(
            milliseconds=1
        )
        payload: Mapping[str, object] = {
            "provider_payload": list(raw_candle),
            "symbol": normalized_symbol,
            "timeframe": timeframe,
            "open_time_utc": _utc_isoformat(open_time_utc),
            "close_time_utc": _utc_isoformat(close_time_utc),
            "open": numeric_values["open"],
            "high": numeric_values["high"],
            "low": numeric_values["low"],
            "close": numeric_values["close"],
            "volume": numeric_values["volume"],
            "is_final": confirm == "1",
        }
        return payload, open_time_utc, str(raw_candle[0]), timestamp_ms


__all__ = [
    "OKX_DEFAULT_CANDLES_LIMIT",
    "OKX_HISTORY_CANDLES_PATH",
    "OKX_HISTORY_CANDLES_SOURCE_ENDPOINT",
    "OKX_MAX_CANDLES_LIMIT",
    "OKX_PUBLIC_BASE_URL",
    "OKX_PUBLIC_OHLCV_SPEC",
    "OkxPublicOhlcvProvider",
]
