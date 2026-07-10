"""OKX V5 public OHLCV, trade, funding, and instrument components."""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping, Sequence
from datetime import datetime, timedelta, timezone
from types import MappingProxyType
from uuid import NAMESPACE_URL, uuid5

from secure_eval_wrapper.data_collection.hashing import sha256_observation_source
from secure_eval_wrapper.data_collection.http_transport import HttpRequest, HttpResponse, HttpTransport, TransportError
from secure_eval_wrapper.data_collection.instruments import perpetual_instrument_key, spot_instrument_key
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
from secure_eval_wrapper.data_collection.okx_public import (
    OKX_HISTORY_CANDLES_PATH,
    OkxPublicOhlcvProvider,
)
from secure_eval_wrapper.data_collection.symbols import normalize_symbol, split_base_quote
from secure_eval_wrapper.data_collection.time_utils import require_utc_datetime


OKX_HISTORY_TRADES_PATH = "/api/v5/market/history-trades"
OKX_FUNDING_HISTORY_PATH = "/api/v5/public/funding-rate-history"
OKX_CURRENT_FUNDING_PATH = "/api/v5/public/funding-rate"
OKX_INSTRUMENTS_PATH = "/api/v5/public/instruments"
OKX_HISTORY_TRADES_SOURCE_ENDPOINT = "okx-v5:/api/v5/market/history-trades"
OKX_FUNDING_HISTORY_SOURCE_ENDPOINT = "okx-v5:/api/v5/public/funding-rate-history"
OKX_CURRENT_FUNDING_SOURCE_ENDPOINT = "okx-v5:/api/v5/public/funding-rate"
OKX_INSTRUMENTS_SOURCE_ENDPOINT = "okx-v5:/api/v5/public/instruments"
OKX_MAX_HISTORY_TRADE_LIMIT = 100
OKX_MAX_FUNDING_LIMIT = 400
OKX_MAX_PAGE_GUARD = 1000

OKX_PUBLIC_SPEC = ProviderSpec(
    name="okx",
    display_name="OKX V5 Public",
    exchange_name="OKX",
    capabilities=MappingProxyType(
        {
            MarketDataType.OHLCV: ProviderCapabilityStatus.IMPLEMENTED,
            MarketDataType.TRADES: ProviderCapabilityStatus.IMPLEMENTED,
            MarketDataType.FUNDING_RATES: ProviderCapabilityStatus.IMPLEMENTED,
            MarketDataType.INSTRUMENTS: ProviderCapabilityStatus.IMPLEMENTED,
        }
    ),
)

_EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)


def _ms(value: datetime) -> int:
    value = require_utc_datetime(value)
    delta = value - _EPOCH
    micros = (delta.days * 86400 + delta.seconds) * 1_000_000 + delta.microseconds
    if micros < 0:
        raise ValueError("time boundary must not precede the Unix epoch")
    return (micros + 999) // 1000


def _from_ms(value: object, *, field_name: str) -> tuple[datetime, int]:
    if not isinstance(value, str) or not value.isdigit():
        raise ValueError(f"{field_name} must be a non-negative millisecond string")
    milliseconds = int(value)
    return _EPOCH + timedelta(milliseconds=milliseconds), milliseconds


def _iso(value: datetime) -> str:
    return require_utc_datetime(value).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _format_funding_interval(milliseconds: int) -> str:
    if milliseconds <= 0 or milliseconds % 60_000:
        raise ValueError("OKX funding interval must be a positive whole number of minutes")
    minutes = milliseconds // 60_000
    if minutes % 60 == 0:
        return f"{minutes // 60}h"
    return f"{minutes}m"


def okx_spot_instrument_key(symbol: str) -> InstrumentKey:
    normalized = normalize_symbol(symbol)
    return spot_instrument_key(
        provider_name="okx",
        exchange_name="OKX",
        provider_instrument_id=normalized,
        symbol=normalized,
    )


def okx_swap_instrument_key(
    provider_instrument_id: str,
    *,
    settlement_asset: str,
) -> InstrumentKey:
    parts = provider_instrument_id.strip().upper().split("-")
    if len(parts) != 3 or parts[2] != "SWAP":
        raise ValueError("OKX swap provider instrument ID must be BASE-QUOTE-SWAP")
    return perpetual_instrument_key(
        provider_name="okx",
        exchange_name="OKX",
        provider_instrument_id=provider_instrument_id.strip().upper(),
        base_asset=parts[0],
        quote_asset=parts[1],
        settlement_asset=settlement_asset,
        contract_type="perpetual_swap",
        margin_type=f"{settlement_asset.strip().upper()}-margined",
    )


class OkxPublicProvider(OkxPublicOhlcvProvider):
    """All approved Phase 2 OKX V5 public data types."""

    def __init__(
        self,
        *,
        transport: HttpTransport | None = None,
        base_url: str = "https://openapi.okx.com",
        timeout: float = 10.0,
        max_pages: int = 20,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        super().__init__(
            transport=transport,
            base_url=base_url,
            timeout=timeout,
            max_pages=max_pages,
            clock=clock,
        )

    @property
    def spec(self) -> ProviderSpec:
        return OKX_PUBLIC_SPEC

    def _build_public_request(
        self,
        path: str,
        query_params: Mapping[str, str | int],
    ) -> HttpRequest:
        if path not in {
            OKX_HISTORY_CANDLES_PATH,
            OKX_HISTORY_TRADES_PATH,
            OKX_FUNDING_HISTORY_PATH,
            OKX_CURRENT_FUNDING_PATH,
            OKX_INSTRUMENTS_PATH,
        }:
            raise ValueError("OKX public provider rejected a non-allowlisted V5 path")
        return HttpRequest(
            method="GET",
            url=f"{self._base_url}{path}",
            query_params=query_params,
            timeout=self._timeout,
            headers={},
        )

    def fetch_trades(self, request: DataRequest) -> Sequence[RawObservation]:
        symbol, key, start, end, limit, pages = self._trade_request(request)
        cursor = _ms(end)
        cursors = {cursor}
        observations: dict[str, RawObservation] = {}
        for page_number in range(1, pages + 1):
            query: dict[str, str | int] = {
                "instId": key.provider_instrument_id,
                "type": "2",
                "after": str(cursor),
                "limit": str(limit),
            }
            requested = require_utc_datetime(self._clock(), field_name="OKX request clock")
            response = self._transport.send(self._build_public_request(OKX_HISTORY_TRADES_PATH, query))
            ingested = require_utc_datetime(self._clock(), field_name="OKX ingestion clock")
            rows = self._decode_envelope(response, OKX_HISTORY_TRADES_PATH)
            if not rows:
                break
            page_times: list[tuple[datetime, int]] = []
            for position, row in enumerate(rows):
                payload, trade_id, traded_at, timestamp_ms = self._parse_trade(row, key, symbol, position)
                page_times.append((traded_at, timestamp_ms))
                if trade_id in observations:
                    raise ValueError("OKX trade pagination returned a duplicate trade ID")
                if traded_at < start or traded_at >= end:
                    continue
                digest = sha256_observation_source(payload=payload, request_metadata=query)
                observations[trade_id] = RawObservation(
                    observation_id=uuid5(NAMESPACE_URL, f"{request.collection_run_id}:{digest}"),
                    collection_run_id=request.collection_run_id,
                    provider_name=self.spec.name,
                    exchange_name=self.spec.exchange_name,
                    source_endpoint=OKX_HISTORY_TRADES_SOURCE_ENDPOINT,
                    request_parameters=dict(query),
                    request_timestamp_utc=requested,
                    ingested_at_utc=ingested,
                    data_type=MarketDataType.TRADES,
                    payload=payload,
                    source_sha256=digest,
                    collection_status=CollectionStatus.SUCCEEDED,
                    raw_symbol=key.provider_instrument_id,
                    normalized_symbol=symbol,
                    observed_at_utc=traded_at,
                    provider_timestamp=str(timestamp_ms),
                    instrument_key=key,
                )
            oldest_time, next_cursor = min(page_times, key=lambda item: item[1])
            if oldest_time <= start or len(rows) < limit:
                break
            if next_cursor >= cursor or next_cursor in cursors:
                raise ValueError("OKX trade pagination cursor did not advance")
            if page_number == pages:
                raise ValueError("OKX trade pagination exceeded max_pages")
            cursors.add(next_cursor)
            cursor = next_cursor
        return tuple(sorted(observations.values(), key=lambda item: (item.observed_at_utc, str(item.observation_id))))

    def fetch_funding_rates(self, request: DataRequest) -> Sequence[RawObservation]:
        key, start, end, limit, pages = self._funding_request(request)
        funding_interval, interval_source, interval_metadata = self._fetch_funding_interval(key)
        cursor = _ms(end)
        cursors = {cursor}
        observations: dict[int, RawObservation] = {}
        for page_number in range(1, pages + 1):
            query: dict[str, str | int] = {
                "instId": key.provider_instrument_id,
                "after": str(cursor),
                "limit": str(limit),
            }
            requested = require_utc_datetime(self._clock(), field_name="OKX request clock")
            response = self._transport.send(self._build_public_request(OKX_FUNDING_HISTORY_PATH, query))
            ingested = require_utc_datetime(self._clock(), field_name="OKX ingestion clock")
            rows = self._decode_envelope(response, OKX_FUNDING_HISTORY_PATH)
            if not rows:
                break
            page_times: list[tuple[datetime, int]] = []
            for position, row in enumerate(rows):
                payload, funding_at, timestamp_ms = self._parse_funding(
                    row, key, position, funding_interval, interval_source, interval_metadata
                )
                page_times.append((funding_at, timestamp_ms))
                if timestamp_ms in observations:
                    raise ValueError("OKX funding pagination returned a duplicate timestamp")
                if funding_at < start or funding_at >= end:
                    continue
                digest = sha256_observation_source(payload=payload, request_metadata=query)
                observations[timestamp_ms] = RawObservation(
                    observation_id=uuid5(NAMESPACE_URL, f"{request.collection_run_id}:{digest}"),
                    collection_run_id=request.collection_run_id,
                    provider_name=self.spec.name,
                    exchange_name=self.spec.exchange_name,
                    source_endpoint=OKX_FUNDING_HISTORY_SOURCE_ENDPOINT,
                    request_parameters=dict(query),
                    request_timestamp_utc=requested,
                    ingested_at_utc=ingested,
                    data_type=MarketDataType.FUNDING_RATES,
                    payload=payload,
                    source_sha256=digest,
                    collection_status=CollectionStatus.SUCCEEDED,
                    raw_symbol=key.provider_instrument_id,
                    normalized_symbol=key.canonical_symbol,
                    observed_at_utc=funding_at,
                    provider_timestamp=str(timestamp_ms),
                    instrument_key=key,
                )
            oldest_time, next_cursor = min(page_times, key=lambda item: item[1])
            if oldest_time <= start or len(rows) < limit:
                break
            if next_cursor >= cursor or next_cursor in cursors:
                raise ValueError("OKX funding pagination cursor did not advance")
            if page_number == pages:
                raise ValueError("OKX funding pagination exceeded max_pages")
            cursors.add(next_cursor)
            cursor = next_cursor
        return tuple(observations[timestamp] for timestamp in sorted(observations))

    def _fetch_funding_interval(
        self,
        key: InstrumentKey,
    ) -> tuple[str | None, FundingIntervalSource, Mapping[str, object]]:
        query = {"instId": key.provider_instrument_id}
        response = self._transport.send(
            self._build_public_request(OKX_CURRENT_FUNDING_PATH, query)
        )
        rows = self._decode_envelope(response, OKX_CURRENT_FUNDING_PATH)
        matches = [
            row for row in rows
            if isinstance(row, Mapping) and row.get("instId") == key.provider_instrument_id
        ]
        if len(matches) > 1:
            raise ValueError("OKX current funding endpoint returned a duplicate instrument")
        if not matches:
            return None, FundingIntervalSource.UNAVAILABLE, {
                "source_endpoint": OKX_CURRENT_FUNDING_SOURCE_ENDPOINT,
                "reason": "instrument_not_returned",
            }
        funding_time = matches[0].get("fundingTime")
        next_funding_time = matches[0].get("nextFundingTime")
        if not (
            isinstance(funding_time, str)
            and funding_time.isdigit()
            and isinstance(next_funding_time, str)
            and next_funding_time.isdigit()
        ):
            return None, FundingIntervalSource.UNAVAILABLE, {
                "source_endpoint": OKX_CURRENT_FUNDING_SOURCE_ENDPOINT,
                "reason": "schedule_timestamps_unavailable",
            }
        elapsed_ms = int(next_funding_time) - int(funding_time)
        interval = _format_funding_interval(elapsed_ms)
        return interval, FundingIntervalSource.METADATA_REPORTED, {
            "source_endpoint": OKX_CURRENT_FUNDING_SOURCE_ENDPOINT,
            "funding_time": funding_time,
            "next_funding_time": next_funding_time,
        }

    def fetch_instruments(self, request: DataRequest) -> Sequence[RawObservation]:
        keys = self._instrument_request(request)
        observations: list[RawObservation] = []
        for key in keys:
            inst_type = "SPOT" if key.instrument_type is InstrumentType.SPOT else "SWAP"
            query = {"instType": inst_type, "instId": key.provider_instrument_id}
            requested = require_utc_datetime(self._clock(), field_name="OKX request clock")
            response = self._transport.send(self._build_public_request(OKX_INSTRUMENTS_PATH, query))
            ingested = require_utc_datetime(self._clock(), field_name="OKX ingestion clock")
            rows = self._decode_envelope(response, OKX_INSTRUMENTS_PATH)
            matches = [
                item for item in rows
                if isinstance(item, Mapping) and item.get("instId") == key.provider_instrument_id
            ]
            if len(matches) != 1:
                raise ValueError(f"OKX instruments expected one record for {key.provider_instrument_id}")
            payload = self._parse_instrument(matches[0], key)
            digest = sha256_observation_source(payload=payload, request_metadata=query)
            observations.append(
                RawObservation(
                    observation_id=uuid5(NAMESPACE_URL, f"{request.collection_run_id}:{digest}"),
                    collection_run_id=request.collection_run_id,
                    provider_name=self.spec.name,
                    exchange_name=self.spec.exchange_name,
                    source_endpoint=OKX_INSTRUMENTS_SOURCE_ENDPOINT,
                    request_parameters=dict(query),
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
        return tuple(observations)

    @staticmethod
    def _decode_envelope(response: HttpResponse, path: str) -> list[object]:
        if response.status != 200:
            raise TransportError(f"OKX public request returned HTTP {response.status} from {path}")
        try:
            envelope = json.loads(response.body_text)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError("OKX response must be valid UTF-8 JSON") from exc
        if not isinstance(envelope, Mapping):
            raise ValueError("OKX response must be a JSON object")
        if envelope.get("code") != "0":
            raise ValueError(f"OKX response returned result code {envelope.get('code')!r}: {envelope.get('msg')!r}")
        if not isinstance(envelope.get("msg"), str) or not isinstance(envelope.get("data"), list):
            raise ValueError("OKX response must contain string msg and list data")
        return envelope["data"]

    def _trade_request(
        self, request: DataRequest
    ) -> tuple[str, InstrumentKey, datetime, datetime, int, int]:
        if request.provider_name != self.spec.name or request.data_type is not MarketDataType.TRADES:
            raise ValueError("fetch_trades requires an OKX trades request")
        if len(request.symbols) != 1 or request.parameters or request.instruments:
            raise ValueError("OKX trades require one canonical spot symbol")
        symbol = normalize_symbol(request.symbols[0])
        key = okx_spot_instrument_key(symbol)
        start, end = self._window(request, "trade")
        limit = 100 if request.limit is None else request.limit
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= OKX_MAX_HISTORY_TRADE_LIMIT:
            raise ValueError("OKX history-trade limit must be between 1 and 100")
        return symbol, key, start, end, limit, self._pages(request)

    def _funding_request(
        self, request: DataRequest
    ) -> tuple[InstrumentKey, datetime, datetime, int, int]:
        if request.provider_name != self.spec.name or request.data_type is not MarketDataType.FUNDING_RATES:
            raise ValueError("fetch_funding_rates requires an OKX funding request")
        if len(request.instruments) != 1 or request.symbols or request.parameters:
            raise ValueError("OKX funding requires one explicit InstrumentKey")
        key = request.instruments[0]
        if key.provider_name != self.spec.name or key.instrument_type is not InstrumentType.PERPETUAL_SWAP:
            raise ValueError("OKX funding requires an OKX perpetual-swap InstrumentKey")
        start, end = self._window(request, "funding")
        limit = 400 if request.limit is None else request.limit
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= OKX_MAX_FUNDING_LIMIT:
            raise ValueError("OKX funding limit must be between 1 and 400")
        return key, start, end, limit, self._pages(request)

    def _instrument_request(self, request: DataRequest) -> tuple[InstrumentKey, ...]:
        if request.provider_name != self.spec.name or request.data_type is not MarketDataType.INSTRUMENTS:
            raise ValueError("fetch_instruments requires an OKX instruments request")
        if request.parameters or request.start_at_utc is not None or request.end_at_utc is not None:
            raise ValueError("OKX instrument requests do not accept windows or extra parameters")
        if request.instruments:
            keys = tuple(request.instruments)
        elif request.symbols:
            keys = tuple(okx_spot_instrument_key(symbol) for symbol in request.symbols)
        else:
            raise ValueError("OKX instrument requests require an explicit bounded subset")
        if any(
            key.provider_name != self.spec.name
            or key.instrument_type not in (InstrumentType.SPOT, InstrumentType.PERPETUAL_SWAP)
            for key in keys
        ):
            raise ValueError("OKX instruments support only explicit SPOT and SWAP identities")
        if request.limit is not None and len(keys) > request.limit:
            raise ValueError("requested OKX instrument subset exceeds the bounded limit")
        if len({key.provider_instrument_id for key in keys}) != len(keys):
            raise ValueError("requested OKX instruments must be unique")
        return tuple(sorted(keys, key=lambda item: (item.instrument_type.value, item.provider_instrument_id)))

    def _window(self, request: DataRequest, label: str) -> tuple[datetime, datetime]:
        if request.start_at_utc is None or request.end_at_utc is None:
            raise ValueError(f"OKX {label} requests require explicit UTC start and end")
        start = require_utc_datetime(request.start_at_utc, field_name="start_at_utc")
        end = require_utc_datetime(request.end_at_utc, field_name="end_at_utc")
        if end <= start:
            raise ValueError("end_at_utc must be later than start_at_utc")
        return start, end

    def _pages(self, request: DataRequest) -> int:
        pages = self._max_pages if request.max_pages is None else request.max_pages
        if isinstance(pages, bool) or not isinstance(pages, int) or not 1 <= pages <= OKX_MAX_PAGE_GUARD:
            raise ValueError("OKX max_pages must be between 1 and 1000")
        return min(pages, self._max_pages)

    @staticmethod
    def _parse_trade(
        row: object, key: InstrumentKey, symbol: str, position: int
    ) -> tuple[Mapping[str, object], str, datetime, int]:
        if not isinstance(row, Mapping):
            raise ValueError(f"OKX trade {position} must be an object")
        if row.get("instId") != key.provider_instrument_id:
            raise ValueError("OKX trade provider instrument mismatch")
        for name in ("tradeId", "px", "sz", "side", "ts"):
            if not isinstance(row.get(name), str) or not row[name].strip():
                raise ValueError(f"OKX trade {position} {name} must be a string")
        if row["side"] not in ("buy", "sell"):
            raise ValueError("OKX trade side must be buy or sell")
        traded_at, timestamp_ms = _from_ms(row["ts"], field_name="trade ts")
        trade_id = row["tradeId"]
        sequence = int(trade_id) if trade_id.isdigit() else None
        return {
            "provider_payload": dict(row),
            "symbol": symbol,
            "provider_instrument_id": key.provider_instrument_id,
            "provider_trade_id": trade_id,
            "provider_sequence": sequence,
            "first_provider_trade_id": None,
            "last_provider_trade_id": None,
            "traded_at_utc": _iso(traded_at),
            "price": row["px"],
            "quantity": row["sz"],
            "quote_quantity": None,
            "side": row["side"],
            "order_source": row.get("source"),
        }, trade_id, traded_at, timestamp_ms

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
            raise ValueError(f"OKX funding row {position} must be an object")
        if row.get("instId") != key.provider_instrument_id or row.get("instType") not in (None, "SWAP"):
            raise ValueError("OKX funding provider instrument mismatch")
        actual = row.get("realizedRate")
        predicted = row.get("fundingRate")
        if not isinstance(actual, str) or not actual.strip():
            raise ValueError("OKX realizedRate must be a string")
        if predicted is not None and (not isinstance(predicted, str) or not predicted.strip()):
            raise ValueError("OKX fundingRate must be a string when present")
        funding_at, timestamp_ms = _from_ms(row.get("fundingTime"), field_name="fundingTime")
        return {
            "provider_payload": dict(row),
            "funding_time_utc": _iso(funding_at),
            "rate": actual,
            "predicted_rate": predicted,
            "mark_price": None,
            "index_price": None,
            "funding_interval": funding_interval,
            "funding_interval_source": interval_source.value,
            "funding_interval_metadata": dict(interval_metadata),
            "formula_type": row.get("formulaType"),
            "method": row.get("method"),
        }, funding_at, timestamp_ms

    @staticmethod
    def _parse_instrument(item: Mapping[str, object], key: InstrumentKey) -> Mapping[str, object]:
        expected_type = "SPOT" if key.instrument_type is InstrumentType.SPOT else "SWAP"
        if item.get("instId") != key.provider_instrument_id or item.get("instType") != expected_type:
            raise ValueError("OKX instrument identity mismatch")
        if key.instrument_type is InstrumentType.SPOT:
            if item.get("baseCcy") != key.base_asset or item.get("quoteCcy") != key.quote_asset:
                raise ValueError("OKX spot base/quote mapping mismatch")
        settle = item.get("settleCcy")
        if key.instrument_type is InstrumentType.PERPETUAL_SWAP and settle not in ("", key.settlement_asset):
            raise ValueError("OKX swap settlement mapping mismatch")
        listing = item.get("listTime")
        expiry = item.get("expTime")
        listing_at = _iso(_from_ms(listing, field_name="listTime")[0]) if isinstance(listing, str) and listing.isdigit() else None
        expiry_at = _iso(_from_ms(expiry, field_name="expTime")[0]) if isinstance(expiry, str) and expiry.isdigit() else None
        return {
            "provider_payload": dict(item),
            "status": item.get("state"),
            "price_precision": None,
            "quantity_precision": None,
            "tick_size": item.get("tickSz"),
            "quantity_step": item.get("lotSz"),
            "minimum_quantity": item.get("minSz"),
            "minimum_notional": None,
            "contract_value": item.get("ctVal"),
            "contract_multiplier": item.get("ctMult"),
            "margin_asset": key.settlement_asset,
            "margin_type": key.margin_type,
            "listing_at_utc": listing_at,
            "expiry_at_utc": expiry_at,
            "funding_interval": None,
            "metadata": {
                "alias": item.get("alias"),
                "contract_type": item.get("ctType"),
                "contract_value_currency": item.get("ctValCcy"),
                "instrument_family": item.get("instFamily"),
                "underlying": item.get("uly"),
                "rule_type": item.get("ruleType"),
                "upcoming_changes": item.get("upcChg"),
            },
        }


__all__ = [
    "OKX_CURRENT_FUNDING_PATH",
    "OKX_FUNDING_HISTORY_PATH",
    "OKX_HISTORY_TRADES_PATH",
    "OKX_INSTRUMENTS_PATH",
    "OKX_PUBLIC_SPEC",
    "OkxPublicProvider",
    "okx_spot_instrument_key",
    "okx_swap_instrument_key",
]
