"""Binance Spot public trades and instrument metadata through injectable HTTP."""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping, Sequence
from datetime import datetime, timedelta, timezone
from types import MappingProxyType
from uuid import NAMESPACE_URL, uuid5

from secure_eval_wrapper.data_collection.binance_spot import (
    BINANCE_KLINES_PATH,
    BINANCE_SPOT_BASE_URL,
    BinanceSpotOhlcvProvider,
)
from secure_eval_wrapper.data_collection.hashing import sha256_observation_source
from secure_eval_wrapper.data_collection.http_transport import HttpRequest, HttpTransport, TransportError
from secure_eval_wrapper.data_collection.instruments import spot_instrument_key
from secure_eval_wrapper.data_collection.models import (
    CollectionStatus,
    DataRequest,
    InstrumentKey,
    MarketDataType,
    ProviderCapabilityStatus,
    ProviderSpec,
    RawObservation,
)
from secure_eval_wrapper.data_collection.symbols import normalize_symbol, split_base_quote
from secure_eval_wrapper.data_collection.time_utils import require_utc_datetime


BINANCE_AGG_TRADES_PATH = "/api/v3/aggTrades"
BINANCE_EXCHANGE_INFO_PATH = "/api/v3/exchangeInfo"
BINANCE_AGG_TRADES_SOURCE_ENDPOINT = "binance-spot:/api/v3/aggTrades"
BINANCE_EXCHANGE_INFO_SOURCE_ENDPOINT = "binance-spot:/api/v3/exchangeInfo"
BINANCE_MAX_TRADE_LIMIT = 1000
BINANCE_MAX_PAGE_GUARD = 1000

BINANCE_SPOT_PUBLIC_SPEC = ProviderSpec(
    name="binance",
    display_name="Binance Spot",
    exchange_name="Binance",
    capabilities=MappingProxyType(
        {
            MarketDataType.OHLCV: ProviderCapabilityStatus.IMPLEMENTED,
            MarketDataType.TRADES: ProviderCapabilityStatus.IMPLEMENTED,
            MarketDataType.FUNDING_RATES: ProviderCapabilityStatus.PLANNED,
            MarketDataType.INSTRUMENTS: ProviderCapabilityStatus.IMPLEMENTED,
        }
    ),
)

_EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)


def _epoch_microseconds(value: datetime, *, field_name: str) -> int:
    value = require_utc_datetime(value, field_name=field_name)
    delta = value - _EPOCH
    result = (delta.days * 86400 + delta.seconds) * 1_000_000 + delta.microseconds
    if result < 0:
        raise ValueError(f"{field_name} must not precede the Unix epoch")
    return result


def _inclusive_start_ms(value: datetime) -> int:
    return (_epoch_microseconds(value, field_name="start_at_utc") + 999) // 1000


def _inclusive_end_ms(value: datetime) -> int:
    micros = _epoch_microseconds(value, field_name="end_at_utc")
    if micros == 0:
        raise ValueError("end_at_utc must follow the Unix epoch")
    return (micros - 1) // 1000


def _millis_to_utc(value: object, *, field_name: str) -> datetime:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{field_name} must be a non-negative integer millisecond timestamp")
    return _EPOCH + timedelta(milliseconds=value)


def _iso(value: datetime) -> str:
    return require_utc_datetime(value).isoformat(timespec="milliseconds").replace("+00:00", "Z")


class BinanceSpotPublicProvider(BinanceSpotOhlcvProvider):
    """Public Binance Spot OHLCV, aggregate trades, and instruments."""

    def __init__(
        self,
        *,
        transport: HttpTransport | None = None,
        timeout: float = 10.0,
        max_pages: int = 20,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        if isinstance(max_pages, bool) or not isinstance(max_pages, int) or not 1 <= max_pages <= BINANCE_MAX_PAGE_GUARD:
            raise ValueError(f"Binance max_pages must be between 1 and {BINANCE_MAX_PAGE_GUARD}")
        super().__init__(transport=transport, timeout=timeout, clock=clock)
        self._max_pages = max_pages

    @property
    def spec(self) -> ProviderSpec:
        return BINANCE_SPOT_PUBLIC_SPEC

    def _build_public_request(
        self,
        path: str,
        query_params: Mapping[str, str | int],
    ) -> HttpRequest:
        if path not in {
            BINANCE_KLINES_PATH,
            BINANCE_AGG_TRADES_PATH,
            BINANCE_EXCHANGE_INFO_PATH,
        }:
            raise ValueError("Binance Spot public provider rejected a non-allowlisted path")
        return HttpRequest(
            method="GET",
            url=f"{BINANCE_SPOT_BASE_URL}{path}",
            query_params=query_params,
            timeout=self._timeout,
            headers={},
        )

    def fetch_trades(self, request: DataRequest) -> Sequence[RawObservation]:
        symbol, provider_symbol, key, start, end, limit, max_pages = self._validate_trade_request(request)
        base_query: dict[str, str | int] = {
            "symbol": provider_symbol,
            "startTime": _inclusive_start_ms(start),
            "endTime": _inclusive_end_ms(end),
            "limit": limit,
        }
        observations: list[RawObservation] = []
        seen_ids: set[int] = set()
        requested_cursors: set[int] = set()
        cursor: int | None = None

        for page_number in range(1, max_pages + 1):
            query = dict(base_query)
            if cursor is not None:
                query.pop("startTime", None)
                query.pop("endTime", None)
                query["fromId"] = cursor
            request_time = require_utc_datetime(self._clock(), field_name="Binance request clock")
            response = self._transport.send(self._build_public_request(BINANCE_AGG_TRADES_PATH, query))
            ingested = require_utc_datetime(self._clock(), field_name="Binance ingestion clock")
            rows = self._decode_list(response, endpoint=BINANCE_AGG_TRADES_PATH)
            if not rows:
                break
            page_ids: list[int] = []
            page_times: list[datetime] = []
            for position, row in enumerate(rows):
                payload, trade_id, traded_at = self._parse_aggregate_trade(
                    row, symbol=symbol, position=position
                )
                page_ids.append(trade_id)
                page_times.append(traded_at)
                if trade_id in seen_ids:
                    raise ValueError("Binance aggregate-trade pagination returned a duplicate ID")
                seen_ids.add(trade_id)
                if traded_at < start or traded_at >= end:
                    continue
                params: Mapping[str, object] = dict(query)
                digest = sha256_observation_source(payload=payload, request_metadata=params)
                observations.append(
                    RawObservation(
                        observation_id=uuid5(NAMESPACE_URL, f"{request.collection_run_id}:{digest}"),
                        collection_run_id=request.collection_run_id,
                        provider_name=self.spec.name,
                        exchange_name=self.spec.exchange_name,
                        source_endpoint=BINANCE_AGG_TRADES_SOURCE_ENDPOINT,
                        request_parameters=params,
                        request_timestamp_utc=request_time,
                        ingested_at_utc=ingested,
                        data_type=MarketDataType.TRADES,
                        payload=payload,
                        source_sha256=digest,
                        collection_status=CollectionStatus.SUCCEEDED,
                        raw_symbol=provider_symbol,
                        normalized_symbol=symbol,
                        observed_at_utc=traded_at,
                        provider_timestamp=str(row["T"]) if isinstance(row, Mapping) else None,
                        instrument_key=key,
                    )
                )
            next_cursor = max(page_ids) + 1
            if len(rows) < limit or max(page_times) >= end:
                break
            if cursor is not None and next_cursor <= cursor or next_cursor in requested_cursors:
                raise ValueError("Binance aggregate-trade pagination cursor did not advance")
            if page_number == max_pages:
                raise ValueError("Binance aggregate-trade pagination exceeded max_pages")
            requested_cursors.add(next_cursor)
            cursor = next_cursor
        return tuple(sorted(observations, key=lambda item: (item.observed_at_utc, str(item.observation_id))))

    def fetch_instruments(self, request: DataRequest) -> Sequence[RawObservation]:
        keys = self._spot_keys(request)
        provider_ids = tuple(key.provider_instrument_id for key in keys)
        query: dict[str, str | int]
        if len(provider_ids) == 1:
            query = {"symbol": provider_ids[0]}
        else:
            query = {"symbols": json.dumps(provider_ids, separators=(",", ":"))}
        request_time = require_utc_datetime(self._clock(), field_name="Binance request clock")
        response = self._transport.send(self._build_public_request(BINANCE_EXCHANGE_INFO_PATH, query))
        ingested = require_utc_datetime(self._clock(), field_name="Binance ingestion clock")
        if response.status != 200:
            raise TransportError(f"Binance exchangeInfo returned HTTP {response.status}")
        try:
            envelope = json.loads(response.body_text)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError("Binance exchangeInfo response must be valid UTF-8 JSON") from exc
        if not isinstance(envelope, Mapping) or not isinstance(envelope.get("symbols"), list):
            raise ValueError("Binance exchangeInfo response must contain a symbols list")
        key_by_id = {key.provider_instrument_id: key for key in keys}
        observations: list[RawObservation] = []
        seen: set[str] = set()
        for position, item in enumerate(envelope["symbols"]):
            if not isinstance(item, Mapping):
                raise ValueError(f"Binance exchangeInfo symbol {position} must be an object")
            provider_id = item.get("symbol")
            if provider_id not in key_by_id:
                continue
            if provider_id in seen:
                raise ValueError("Binance exchangeInfo returned a duplicate requested symbol")
            seen.add(str(provider_id))
            key = key_by_id[str(provider_id)]
            payload = self._parse_spot_instrument(item, key)
            digest = sha256_observation_source(payload=payload, request_metadata=query)
            observations.append(
                RawObservation(
                    observation_id=uuid5(NAMESPACE_URL, f"{request.collection_run_id}:{digest}"),
                    collection_run_id=request.collection_run_id,
                    provider_name=self.spec.name,
                    exchange_name=self.spec.exchange_name,
                    source_endpoint=BINANCE_EXCHANGE_INFO_SOURCE_ENDPOINT,
                    request_parameters=dict(query),
                    request_timestamp_utc=request_time,
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
        missing = sorted(set(provider_ids) - seen)
        if missing:
            raise ValueError("Binance exchangeInfo omitted requested symbols: " + ", ".join(missing))
        return tuple(observations)

    @staticmethod
    def _decode_list(response, *, endpoint: str) -> list[object]:
        if response.status != 200:
            raise TransportError(f"Binance public request returned HTTP {response.status} from {endpoint}")
        try:
            decoded = json.loads(response.body_text)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError("Binance public response must be valid UTF-8 JSON") from exc
        if not isinstance(decoded, list):
            raise ValueError("Binance public response must be a JSON list")
        return decoded

    def _validate_trade_request(
        self, request: DataRequest
    ) -> tuple[str, str, InstrumentKey, datetime, datetime, int, int]:
        if not isinstance(request, DataRequest):
            raise TypeError("request must be a DataRequest")
        if request.provider_name != self.spec.name or request.data_type is not MarketDataType.TRADES:
            raise ValueError("fetch_trades requires a Binance trades DataRequest")
        if len(request.symbols) != 1 or request.parameters or request.instruments:
            raise ValueError("Binance trade requests require one spot symbol and no extra parameters")
        symbol = normalize_symbol(request.symbols[0])
        base, quote = split_base_quote(symbol)
        provider_symbol = f"{base}{quote}"
        if request.start_at_utc is None or request.end_at_utc is None:
            raise ValueError("Binance trade requests require explicit UTC start and end")
        start = require_utc_datetime(request.start_at_utc, field_name="start_at_utc")
        end = require_utc_datetime(request.end_at_utc, field_name="end_at_utc")
        if end <= start:
            raise ValueError("end_at_utc must be later than start_at_utc")
        limit = 500 if request.limit is None else request.limit
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= BINANCE_MAX_TRADE_LIMIT:
            raise ValueError("Binance trade limit must be between 1 and 1000")
        requested_pages = self._max_pages if request.max_pages is None else request.max_pages
        if isinstance(requested_pages, bool) or not isinstance(requested_pages, int) or not 1 <= requested_pages <= BINANCE_MAX_PAGE_GUARD:
            raise ValueError("Binance max_pages must be between 1 and 1000")
        key = spot_instrument_key(
            provider_name=self.spec.name,
            exchange_name=self.spec.exchange_name,
            provider_instrument_id=provider_symbol,
            symbol=symbol,
        )
        return symbol, provider_symbol, key, start, end, limit, min(self._max_pages, requested_pages)

    def _spot_keys(self, request: DataRequest) -> tuple[InstrumentKey, ...]:
        if not isinstance(request, DataRequest):
            raise TypeError("request must be a DataRequest")
        if request.provider_name != self.spec.name or request.data_type is not MarketDataType.INSTRUMENTS:
            raise ValueError("fetch_instruments requires a Binance instruments DataRequest")
        if request.parameters or request.start_at_utc is not None or request.end_at_utc is not None:
            raise ValueError("Binance instrument requests do not accept time windows or extra parameters")
        if request.instruments:
            keys = tuple(request.instruments)
            if any(key.instrument_type.value != "spot" or key.provider_name != self.spec.name for key in keys):
                raise ValueError("Binance Spot instruments require Binance spot InstrumentKey values")
        else:
            if not request.symbols:
                raise ValueError("Binance instrument request must include explicit symbols")
            keys = tuple(
                spot_instrument_key(
                    provider_name=self.spec.name,
                    exchange_name=self.spec.exchange_name,
                    provider_instrument_id="".join(split_base_quote(normalize_symbol(symbol))),
                    symbol=symbol,
                )
                for symbol in request.symbols
            )
        if request.limit is not None and len(keys) > request.limit:
            raise ValueError("requested instrument subset exceeds the bounded request limit")
        if len({key.provider_instrument_id for key in keys}) != len(keys):
            raise ValueError("requested Binance instruments must be unique")
        return tuple(sorted(keys, key=lambda key: key.provider_instrument_id))

    @staticmethod
    def _parse_aggregate_trade(
        row: object, *, symbol: str, position: int
    ) -> tuple[Mapping[str, object], int, datetime]:
        if not isinstance(row, Mapping):
            raise ValueError(f"Binance aggregate trade {position} must be an object")
        required = ("a", "p", "q", "f", "l", "T", "m")
        if any(name not in row for name in required):
            raise ValueError(f"Binance aggregate trade {position} is missing required fields")
        if any(isinstance(row[name], bool) or not isinstance(row[name], int) for name in ("a", "f", "l", "T")):
            raise ValueError(f"Binance aggregate trade {position} IDs and time must be integers")
        if not isinstance(row["m"], bool):
            raise ValueError(f"Binance aggregate trade {position} buyer-maker flag must be boolean")
        for name in ("p", "q"):
            if not isinstance(row[name], str) or not row[name].strip():
                raise ValueError(f"Binance aggregate trade {position} {name} must be a string")
        traded_at = _millis_to_utc(row["T"], field_name="trade time")
        side = "sell" if row["m"] else "buy"
        payload: Mapping[str, object] = {
            "provider_payload": dict(row),
            "symbol": symbol,
            "provider_instrument_id": None,
            "provider_trade_id": str(row["a"]),
            "provider_sequence": row["a"],
            "first_provider_trade_id": str(row["f"]),
            "last_provider_trade_id": str(row["l"]),
            "traded_at_utc": _iso(traded_at),
            "price": row["p"],
            "quantity": row["q"],
            "quote_quantity": None,
            "buyer_is_maker": row["m"],
            "side": side,
        }
        return payload, row["a"], traded_at

    @staticmethod
    def _parse_spot_instrument(item: Mapping[str, object], key: InstrumentKey) -> Mapping[str, object]:
        filters = item.get("filters")
        if not isinstance(filters, list):
            raise ValueError("Binance spot instrument filters must be a list")
        by_type = {
            str(value.get("filterType")): value
            for value in filters
            if isinstance(value, Mapping) and isinstance(value.get("filterType"), str)
        }
        price_filter = by_type.get("PRICE_FILTER", {})
        lot_filter = by_type.get("LOT_SIZE", {})
        notional_filter = by_type.get("NOTIONAL", by_type.get("MIN_NOTIONAL", {}))
        status = item.get("status")
        if not isinstance(status, str):
            raise ValueError("Binance spot instrument status must be a string")
        return {
            "provider_payload": dict(item),
            "status": status,
            "price_precision": item.get("quoteAssetPrecision"),
            "quantity_precision": item.get("baseAssetPrecision"),
            "tick_size": price_filter.get("tickSize"),
            "quantity_step": lot_filter.get("stepSize"),
            "minimum_quantity": lot_filter.get("minQty"),
            "minimum_notional": notional_filter.get("minNotional"),
            "contract_value": None,
            "contract_multiplier": None,
            "margin_asset": None,
            "margin_type": None,
            "listing_at_utc": None,
            "expiry_at_utc": None,
            "funding_interval": None,
            "metadata": {"filters": filters, "permissions": item.get("permissionSets", item.get("permissions"))},
        }


__all__ = [
    "BINANCE_AGG_TRADES_PATH",
    "BINANCE_EXCHANGE_INFO_PATH",
    "BINANCE_SPOT_PUBLIC_SPEC",
    "BinanceSpotPublicProvider",
]
