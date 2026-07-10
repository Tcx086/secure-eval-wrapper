"""Binance USDⓈ-M public funding history and derivative instruments."""

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
from secure_eval_wrapper.data_collection.instruments import perpetual_instrument_key
from secure_eval_wrapper.data_collection.models import (
    CollectionStatus,
    DataRequest,
    FundingIntervalSource,
    InstrumentKey,
    InstrumentType,
    MarketDataType,
    ProviderCapabilityStatus,
    ProviderSpec,
    RawObservation,
)
from secure_eval_wrapper.data_collection.providers import MarketDataProvider
from secure_eval_wrapper.data_collection.time_utils import require_utc_datetime


BINANCE_USDM_BASE_URL = "https://fapi.binance.com"
BINANCE_USDM_FUNDING_PATH = "/fapi/v1/fundingRate"
BINANCE_USDM_EXCHANGE_INFO_PATH = "/fapi/v1/exchangeInfo"
BINANCE_USDM_FUNDING_INFO_PATH = "/fapi/v1/fundingInfo"
BINANCE_USDM_FUNDING_SOURCE_ENDPOINT = "binance-usdm:/fapi/v1/fundingRate"
BINANCE_USDM_EXCHANGE_INFO_SOURCE_ENDPOINT = "binance-usdm:/fapi/v1/exchangeInfo"
BINANCE_USDM_FUNDING_INFO_SOURCE_ENDPOINT = "binance-usdm:/fapi/v1/fundingInfo"
BINANCE_USDM_MAX_LIMIT = 1000
BINANCE_USDM_MAX_PAGE_GUARD = 1000

BINANCE_USDM_PUBLIC_SPEC = ProviderSpec(
    name="binance_usdm",
    display_name="Binance USDⓈ-M",
    exchange_name="Binance",
    capabilities=MappingProxyType(
        {
            MarketDataType.OHLCV: ProviderCapabilityStatus.PLANNED,
            MarketDataType.TRADES: ProviderCapabilityStatus.PLANNED,
            MarketDataType.FUNDING_RATES: ProviderCapabilityStatus.IMPLEMENTED,
            MarketDataType.INSTRUMENTS: ProviderCapabilityStatus.IMPLEMENTED,
        }
    ),
)

_EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _ms(value: datetime, *, exclusive_end: bool = False) -> int:
    value = require_utc_datetime(value)
    delta = value - _EPOCH
    micros = (delta.days * 86400 + delta.seconds) * 1_000_000 + delta.microseconds
    if micros < 0:
        raise ValueError("time boundary must not precede the Unix epoch")
    if exclusive_end:
        if micros == 0:
            raise ValueError("exclusive end must follow the Unix epoch")
        return (micros - 1) // 1000
    return (micros + 999) // 1000


def _from_ms(value: object, *, field_name: str) -> datetime:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{field_name} must be a non-negative integer millisecond timestamp")
    return _EPOCH + timedelta(milliseconds=value)


def _iso(value: datetime) -> str:
    return require_utc_datetime(value).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def binance_usdm_instrument_key(
    provider_instrument_id: str,
    *,
    base_asset: str,
    quote_asset: str,
    settlement_asset: str,
) -> InstrumentKey:
    return perpetual_instrument_key(
        provider_name="binance_usdm",
        exchange_name="Binance",
        provider_instrument_id=provider_instrument_id,
        base_asset=base_asset,
        quote_asset=quote_asset,
        settlement_asset=settlement_asset,
        contract_type="linear_perpetual",
        margin_type=f"{settlement_asset.upper()}-margined",
    )


class BinanceUsdmPublicProvider(MarketDataProvider):
    """Public-only Binance USDⓈ-M component."""

    def __init__(
        self,
        *,
        transport: HttpTransport | None = None,
        timeout: float = 10.0,
        max_pages: int = 20,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or timeout <= 0:
            raise ValueError("Binance USDⓈ-M timeout must be positive")
        if isinstance(max_pages, bool) or not isinstance(max_pages, int) or not 1 <= max_pages <= BINANCE_USDM_MAX_PAGE_GUARD:
            raise ValueError("Binance USDⓈ-M max_pages must be between 1 and 1000")
        self._transport = UrlLibHttpTransport() if transport is None else transport
        self._timeout = timeout
        self._max_pages = max_pages
        self._clock = _utc_now if clock is None else clock

    @property
    def spec(self) -> ProviderSpec:
        return BINANCE_USDM_PUBLIC_SPEC

    def fetch_ohlcv(self, request: DataRequest) -> Sequence[RawObservation]:
        raise NotImplementedError("Binance USDⓈ-M OHLCV is outside this public component")

    def fetch_trades(self, request: DataRequest) -> Sequence[RawObservation]:
        raise NotImplementedError("Binance USDⓈ-M trades are outside this milestone")

    def fetch_funding_rates(self, request: DataRequest) -> Sequence[RawObservation]:
        key, start, end, limit, max_pages = self._funding_request(request)
        funding_interval, interval_source, interval_metadata = self._fetch_funding_interval(key)
        start_ms = _ms(start)
        end_ms = _ms(end, exclusive_end=True)
        observations: list[RawObservation] = []
        seen_times: set[int] = set()
        cursors: set[int] = {start_ms}
        cursor = start_ms

        for page_number in range(1, max_pages + 1):
            query: dict[str, str | int] = {
                "symbol": key.provider_instrument_id,
                "startTime": cursor,
                "endTime": end_ms,
                "limit": limit,
            }
            requested = require_utc_datetime(self._clock(), field_name="Binance USDⓈ-M request clock")
            response = self._transport.send(self._request(BINANCE_USDM_FUNDING_PATH, query))
            ingested = require_utc_datetime(self._clock(), field_name="Binance USDⓈ-M ingestion clock")
            rows = self._decode_list(response, BINANCE_USDM_FUNDING_PATH)
            if not rows:
                break
            page_times: list[int] = []
            for position, row in enumerate(rows):
                payload, timestamp, timestamp_ms = self._parse_funding(
                    row, key, position, funding_interval, interval_source, interval_metadata
                )
                page_times.append(timestamp_ms)
                if timestamp_ms in seen_times:
                    raise ValueError("Binance funding pagination returned a duplicate timestamp")
                seen_times.add(timestamp_ms)
                if timestamp < start or timestamp >= end:
                    continue
                digest = sha256_observation_source(payload=payload, request_metadata=query)
                observations.append(
                    RawObservation(
                        observation_id=uuid5(NAMESPACE_URL, f"{request.collection_run_id}:{digest}"),
                        collection_run_id=request.collection_run_id,
                        provider_name=self.spec.name,
                        exchange_name=self.spec.exchange_name,
                        source_endpoint=BINANCE_USDM_FUNDING_SOURCE_ENDPOINT,
                        request_parameters=dict(query),
                        request_timestamp_utc=requested,
                        ingested_at_utc=ingested,
                        data_type=MarketDataType.FUNDING_RATES,
                        payload=payload,
                        source_sha256=digest,
                        collection_status=CollectionStatus.SUCCEEDED,
                        raw_symbol=key.provider_instrument_id,
                        normalized_symbol=key.canonical_symbol,
                        observed_at_utc=timestamp,
                        provider_timestamp=str(timestamp_ms),
                        instrument_key=key,
                    )
                )
            next_cursor = max(page_times) + 1
            if len(rows) < limit or next_cursor > end_ms:
                break
            if next_cursor <= cursor or next_cursor in cursors:
                raise ValueError("Binance funding pagination cursor did not advance")
            if page_number == max_pages:
                raise ValueError("Binance funding pagination exceeded max_pages")
            cursors.add(next_cursor)
            cursor = next_cursor
        return tuple(sorted(observations, key=lambda item: item.observed_at_utc))

    def fetch_instruments(self, request: DataRequest) -> Sequence[RawObservation]:
        keys = self._instrument_request(request)
        requested_ids = {key.provider_instrument_id for key in keys}
        requested = require_utc_datetime(self._clock(), field_name="Binance USDⓈ-M request clock")
        response = self._transport.send(self._request(BINANCE_USDM_EXCHANGE_INFO_PATH, {}))
        ingested = require_utc_datetime(self._clock(), field_name="Binance USDⓈ-M ingestion clock")
        if response.status != 200:
            raise TransportError(f"Binance USDⓈ-M exchangeInfo returned HTTP {response.status}")
        try:
            envelope = json.loads(response.body_text)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError("Binance USDⓈ-M exchangeInfo must be valid UTF-8 JSON") from exc
        if not isinstance(envelope, Mapping) or not isinstance(envelope.get("symbols"), list):
            raise ValueError("Binance USDⓈ-M exchangeInfo must contain a symbols list")
        key_by_id = {key.provider_instrument_id: key for key in keys}
        observations: list[RawObservation] = []
        seen: set[str] = set()
        for position, item in enumerate(envelope["symbols"]):
            if not isinstance(item, Mapping):
                raise ValueError(f"Binance USDⓈ-M symbol {position} must be an object")
            provider_id = item.get("symbol")
            if provider_id not in requested_ids:
                continue
            key = key_by_id[str(provider_id)]
            if provider_id in seen:
                raise ValueError("Binance USDⓈ-M exchangeInfo returned a duplicate symbol")
            seen.add(str(provider_id))
            payload = self._parse_instrument(item, key)
            digest = sha256_observation_source(payload=payload, request_metadata={})
            observations.append(
                RawObservation(
                    observation_id=uuid5(NAMESPACE_URL, f"{request.collection_run_id}:{digest}"),
                    collection_run_id=request.collection_run_id,
                    provider_name=self.spec.name,
                    exchange_name=self.spec.exchange_name,
                    source_endpoint=BINANCE_USDM_EXCHANGE_INFO_SOURCE_ENDPOINT,
                    request_parameters={},
                    request_timestamp_utc=requested,
                    ingested_at_utc=ingested,
                    data_type=MarketDataType.INSTRUMENTS,
                    payload=payload,
                    source_sha256=digest,
                    collection_status=CollectionStatus.SUCCEEDED,
                    raw_symbol=key.provider_instrument_id,
                    normalized_symbol=key.canonical_symbol,
                    observed_at_utc=ingested,
                    instrument_key=key,
                )
            )
        missing = sorted(requested_ids - seen)
        if missing:
            raise ValueError("Binance USDⓈ-M exchangeInfo omitted requested instruments: " + ", ".join(missing))
        return tuple(observations)

    def _fetch_funding_interval(
        self,
        key: InstrumentKey,
    ) -> tuple[str | None, FundingIntervalSource, Mapping[str, object]]:
        response = self._transport.send(self._request(BINANCE_USDM_FUNDING_INFO_PATH, {}))
        rows = self._decode_list(response, BINANCE_USDM_FUNDING_INFO_PATH)
        matches = [
            row for row in rows
            if isinstance(row, Mapping) and row.get("symbol") == key.provider_instrument_id
        ]
        if len(matches) > 1:
            raise ValueError("Binance fundingInfo returned a duplicate symbol")
        if not matches:
            return None, FundingIntervalSource.UNAVAILABLE, {
                "source_endpoint": BINANCE_USDM_FUNDING_INFO_SOURCE_ENDPOINT,
                "reason": "symbol_not_returned_by_adjustment_endpoint",
            }
        hours = matches[0].get("fundingIntervalHours")
        if isinstance(hours, bool) or not isinstance(hours, int) or hours <= 0:
            raise ValueError("Binance fundingIntervalHours must be a positive integer")
        return f"{hours}h", FundingIntervalSource.PROVIDER_REPORTED, {
            "source_endpoint": BINANCE_USDM_FUNDING_INFO_SOURCE_ENDPOINT,
            "funding_interval_hours": hours,
        }
    def _request(self, path: str, query: Mapping[str, str | int]) -> HttpRequest:
        if path not in {
            BINANCE_USDM_FUNDING_PATH,
            BINANCE_USDM_EXCHANGE_INFO_PATH,
            BINANCE_USDM_FUNDING_INFO_PATH,
        }:
            raise ValueError("Binance USDⓈ-M provider rejected a non-allowlisted path")
        return HttpRequest(
            method="GET",
            url=f"{BINANCE_USDM_BASE_URL}{path}",
            query_params=query,
            timeout=self._timeout,
            headers={},
        )

    @staticmethod
    def _decode_list(response, endpoint: str) -> list[object]:
        if response.status != 200:
            raise TransportError(f"Binance USDⓈ-M request returned HTTP {response.status} from {endpoint}")
        try:
            data = json.loads(response.body_text)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError("Binance USDⓈ-M response must be valid UTF-8 JSON") from exc
        if not isinstance(data, list):
            raise ValueError("Binance USDⓈ-M response must be a JSON list")
        return data

    def _funding_request(
        self, request: DataRequest
    ) -> tuple[InstrumentKey, datetime, datetime, int, int]:
        if not isinstance(request, DataRequest):
            raise TypeError("request must be a DataRequest")
        if request.provider_name != self.spec.name or request.data_type is not MarketDataType.FUNDING_RATES:
            raise ValueError("fetch_funding_rates requires a Binance USDⓈ-M request")
        if len(request.instruments) != 1 or request.symbols or request.parameters:
            raise ValueError("Binance funding requires one explicit InstrumentKey and no symbol guessing")
        key = request.instruments[0]
        if key.provider_name != self.spec.name or key.instrument_type is not InstrumentType.PERPETUAL_SWAP:
            raise ValueError("Binance funding requires a Binance USDⓈ-M perpetual InstrumentKey")
        if request.start_at_utc is None or request.end_at_utc is None:
            raise ValueError("Binance funding requires explicit UTC start and end")
        start = require_utc_datetime(request.start_at_utc, field_name="start_at_utc")
        end = require_utc_datetime(request.end_at_utc, field_name="end_at_utc")
        if end <= start:
            raise ValueError("end_at_utc must be later than start_at_utc")
        limit = 100 if request.limit is None else request.limit
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= BINANCE_USDM_MAX_LIMIT:
            raise ValueError("Binance funding limit must be between 1 and 1000")
        pages = self._max_pages if request.max_pages is None else request.max_pages
        if isinstance(pages, bool) or not isinstance(pages, int) or not 1 <= pages <= BINANCE_USDM_MAX_PAGE_GUARD:
            raise ValueError("Binance funding max_pages must be between 1 and 1000")
        return key, start, end, limit, min(pages, self._max_pages)

    def _instrument_request(self, request: DataRequest) -> tuple[InstrumentKey, ...]:
        if request.provider_name != self.spec.name or request.data_type is not MarketDataType.INSTRUMENTS:
            raise ValueError("fetch_instruments requires a Binance USDⓈ-M instruments request")
        if not request.instruments or request.symbols or request.parameters:
            raise ValueError("Binance USDⓈ-M instruments require explicit InstrumentKey values")
        keys = tuple(request.instruments)
        if any(
            key.provider_name != self.spec.name
            or key.instrument_type is not InstrumentType.PERPETUAL_SWAP
            for key in keys
        ):
            raise ValueError("Binance USDⓈ-M instrument identities must be perpetual swaps")
        if request.limit is not None and len(keys) > request.limit:
            raise ValueError("requested instrument subset exceeds the bounded limit")
        if len({key.provider_instrument_id for key in keys}) != len(keys):
            raise ValueError("requested Binance USDⓈ-M instruments must be unique")
        return tuple(sorted(keys, key=lambda item: item.provider_instrument_id))

    @staticmethod
    def _parse_funding(
        row: object,
        key: InstrumentKey,
        position: int,
        funding_interval: str | None,
        interval_source: FundingIntervalSource,
        interval_metadata: Mapping[str, object],
    ) -> tuple[Mapping[str, object], datetime, int]:
        if not isinstance(row, Mapping):
            raise ValueError(f"Binance funding row {position} must be an object")
        if row.get("symbol") != key.provider_instrument_id:
            raise ValueError("Binance funding row provider instrument mismatch")
        if not isinstance(row.get("fundingRate"), str) or not row["fundingRate"].strip():
            raise ValueError("Binance fundingRate must be a string")
        timestamp = _from_ms(row.get("fundingTime"), field_name="fundingTime")
        mark = row.get("markPrice")
        if mark is not None and (not isinstance(mark, str) or not mark.strip()):
            raise ValueError("Binance markPrice must be a string when present")
        return {
            "provider_payload": dict(row),
            "funding_time_utc": _iso(timestamp),
            "rate": row["fundingRate"],
            "predicted_rate": None,
            "mark_price": mark,
            "index_price": None,
            "funding_interval": funding_interval,
            "funding_interval_source": interval_source.value,
            "funding_interval_metadata": dict(interval_metadata),
        }, timestamp, int(row["fundingTime"])

    @staticmethod
    def _parse_instrument(item: Mapping[str, object], key: InstrumentKey) -> Mapping[str, object]:
        if item.get("contractType") != "PERPETUAL":
            raise ValueError("requested Binance USDⓈ-M instrument is not perpetual")
        if item.get("baseAsset") != key.base_asset or item.get("quoteAsset") != key.quote_asset:
            raise ValueError("Binance USDⓈ-M instrument asset mapping mismatch")
        filters = item.get("filters")
        if not isinstance(filters, list):
            raise ValueError("Binance USDⓈ-M filters must be a list")
        by_type = {
            str(value.get("filterType")): value
            for value in filters
            if isinstance(value, Mapping) and isinstance(value.get("filterType"), str)
        }
        price_filter = by_type.get("PRICE_FILTER", {})
        lot_filter = by_type.get("LOT_SIZE", {})
        min_notional = by_type.get("MIN_NOTIONAL", {})
        onboard = item.get("onboardDate")
        delivery = item.get("deliveryDate")
        listing = _iso(_from_ms(onboard, field_name="onboardDate")) if isinstance(onboard, int) and onboard > 0 else None
        expiry = None
        if isinstance(delivery, int) and 0 < delivery < 4_000_000_000_000:
            expiry = _iso(_from_ms(delivery, field_name="deliveryDate"))
        return {
            "provider_payload": dict(item),
            "status": item.get("status"),
            "price_precision": item.get("pricePrecision"),
            "quantity_precision": item.get("quantityPrecision"),
            "tick_size": price_filter.get("tickSize"),
            "quantity_step": lot_filter.get("stepSize"),
            "minimum_quantity": lot_filter.get("minQty"),
            "minimum_notional": min_notional.get("notional"),
            "contract_value": None,
            "contract_multiplier": "1",
            "margin_asset": item.get("marginAsset"),
            "margin_type": key.margin_type,
            "listing_at_utc": listing,
            "expiry_at_utc": expiry,
            "funding_interval": None,
            "metadata": {
                "filters": filters,
                "contract_type": item.get("contractType"),
                "pair": item.get("pair"),
                "underlying_type": item.get("underlyingType"),
            },
        }


__all__ = [
    "BINANCE_USDM_EXCHANGE_INFO_PATH",
    "BINANCE_USDM_FUNDING_INFO_PATH",
    "BINANCE_USDM_FUNDING_PATH",
    "BINANCE_USDM_PUBLIC_SPEC",
    "BinanceUsdmPublicProvider",
    "binance_usdm_instrument_key",
]
