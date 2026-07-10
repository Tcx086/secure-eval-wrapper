"""Domain contracts for public crypto market-data collection."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from enum import Enum
from uuid import NAMESPACE_URL, UUID, uuid5

from secure_eval_wrapper.data_collection.hashing import sha256_payload


class MarketDataType(str, Enum):
    OHLCV = "ohlcv"
    TRADES = "trades"
    FUNDING_RATES = "funding_rates"
    INSTRUMENTS = "instruments"


class ProviderCapabilityStatus(str, Enum):
    IMPLEMENTED = "implemented"
    PLANNED = "planned"
    UNKNOWN = "unknown"


class CollectionStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    PARTIAL = "partial"
    FAILED = "failed"


class TradeSide(str, Enum):
    BUY = "buy"
    SELL = "sell"
    UNKNOWN = "unknown"


class InstrumentType(str, Enum):
    SPOT = "spot"
    PERPETUAL_SWAP = "perpetual_swap"
    DATED_FUTURE = "dated_future"
    PERPETUAL = "perpetual_swap"
    FUTURE = "dated_future"
    OPTION = "option"
    INDEX = "index"


class InstrumentStatus(str, Enum):
    ACTIVE = "active"
    INACTIVE = "inactive"
    DELISTED = "delisted"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class ProviderSpec:
    name: str
    display_name: str
    exchange_name: str
    capabilities: Mapping[MarketDataType, ProviderCapabilityStatus]
    public_market_data_only: bool = True


@dataclass(frozen=True)
class InstrumentKey:
    """Unambiguous provider and canonical identity for a public instrument."""

    provider_name: str
    exchange_name: str
    provider_instrument_id: str
    base_asset: str
    quote_asset: str
    instrument_type: InstrumentType
    canonical_symbol: str
    settlement_asset: str | None = None
    contract_type: str | None = None
    margin_type: str | None = None

    def __post_init__(self) -> None:
        for field_name in (
            "provider_name",
            "exchange_name",
            "provider_instrument_id",
            "base_asset",
            "quote_asset",
            "canonical_symbol",
        ):
            value = getattr(self, field_name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"InstrumentKey {field_name} must be non-empty")
            object.__setattr__(self, field_name, value.strip())
        object.__setattr__(self, "instrument_type", InstrumentType(self.instrument_type))
        object.__setattr__(self, "base_asset", self.base_asset.upper())
        object.__setattr__(self, "quote_asset", self.quote_asset.upper())
        if self.settlement_asset is not None:
            if not isinstance(self.settlement_asset, str) or not self.settlement_asset.strip():
                raise ValueError("InstrumentKey settlement_asset must be non-empty when present")
            object.__setattr__(self, "settlement_asset", self.settlement_asset.strip().upper())
        if self.instrument_type in (
            InstrumentType.PERPETUAL_SWAP,
            InstrumentType.DATED_FUTURE,
        ) and self.settlement_asset is None:
            raise ValueError("derivative InstrumentKey requires a settlement_asset")

    @property
    def identity_sha256(self) -> str:
        return sha256_payload(
            {
                "provider_name": self.provider_name,
                "exchange_name": self.exchange_name,
                "provider_instrument_id": self.provider_instrument_id,
                "base_asset": self.base_asset,
                "quote_asset": self.quote_asset,
                "settlement_asset": self.settlement_asset,
                "instrument_type": self.instrument_type,
                "canonical_symbol": self.canonical_symbol,
                "contract_type": self.contract_type,
                "margin_type": self.margin_type,
            }
        )

    @property
    def identity_id(self) -> UUID:
        return uuid5(NAMESPACE_URL, f"instrument-key:{self.identity_sha256}")


@dataclass(frozen=True)
class DataRequest:
    collection_run_id: UUID
    provider_name: str
    data_type: MarketDataType
    symbols: tuple[str, ...]
    timeframe: str | None = None
    start_at_utc: datetime | None = None
    end_at_utc: datetime | None = None
    limit: int | None = None
    max_pages: int | None = None
    parameters: Mapping[str, object] = field(default_factory=dict)
    instruments: tuple[InstrumentKey, ...] = ()


@dataclass(frozen=True)
class RawObservation:
    observation_id: UUID
    collection_run_id: UUID
    provider_name: str
    exchange_name: str | None
    source_endpoint: str
    request_parameters: Mapping[str, object]
    request_timestamp_utc: datetime
    ingested_at_utc: datetime
    data_type: MarketDataType
    payload: object
    source_sha256: str
    collection_status: CollectionStatus
    raw_symbol: str | None = None
    normalized_symbol: str | None = None
    timeframe: str | None = None
    observed_at_utc: datetime | None = None
    provider_timestamp: str | None = None
    instrument_key: InstrumentKey | None = None


@dataclass(frozen=True)
class NormalizedBar:
    bar_id: UUID
    symbol: str
    exchange: str
    timeframe: str
    bar_open_time_utc: datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal
    source_observation_ids: tuple[UUID, ...]
    bar_close_time_utc: datetime | None = None
    is_final: bool | None = None
    provenance: Mapping[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class NormalizedTrade:
    trade_id: UUID
    symbol: str
    exchange: str
    traded_at_utc: datetime
    price: Decimal
    quantity: Decimal
    side: TradeSide
    source_observation_ids: tuple[UUID, ...]
    provider_trade_id: str | None = None
    ingested_at_utc: datetime | None = None
    provenance: Mapping[str, object] = field(default_factory=dict)
    instrument_key: InstrumentKey | None = None
    quote_quantity: Decimal | None = None
    provider_sequence: int | None = None
    first_provider_trade_id: str | None = None
    last_provider_trade_id: str | None = None


@dataclass(frozen=True)
class FundingRate:
    funding_rate_id: UUID
    symbol: str
    exchange: str
    funding_time_utc: datetime
    rate: Decimal
    source_observation_ids: tuple[UUID, ...]
    funding_interval: str | None = None
    predicted_rate: Decimal | None = None
    mark_price: Decimal | None = None
    index_price: Decimal | None = None
    provenance: Mapping[str, object] = field(default_factory=dict)
    instrument_key: InstrumentKey | None = None
    provider_instrument_id: str | None = None


@dataclass(frozen=True)
class InstrumentMetadata:
    instrument_id: UUID
    symbol: str
    exchange: str
    base_asset: str
    quote_asset: str
    instrument_type: InstrumentType
    status: InstrumentStatus
    source_observation_ids: tuple[UUID, ...]
    price_precision: int | None = None
    quantity_precision: int | None = None
    first_seen_at_utc: datetime | None = None
    last_seen_at_utc: datetime | None = None
    metadata: Mapping[str, object] = field(default_factory=dict)
    instrument_key: InstrumentKey | None = None
    settlement_asset: str | None = None
    tick_size: Decimal | None = None
    quantity_step: Decimal | None = None
    minimum_quantity: Decimal | None = None
    minimum_notional: Decimal | None = None
    contract_value: Decimal | None = None
    contract_multiplier: Decimal | None = None
    margin_asset: str | None = None
    margin_type: str | None = None
    listing_at_utc: datetime | None = None
    expiry_at_utc: datetime | None = None
    funding_interval: str | None = None
    metadata_sha256: str | None = None


@dataclass(frozen=True)
class CollectionRunSummary:
    collection_run_id: UUID
    provider_name: str
    started_at_utc: datetime
    completed_at_utc: datetime | None
    status: CollectionStatus
    request_count: int
    observation_count: int
    normalized_count: int
    source_hashes: tuple[str, ...] = ()
    messages: tuple[str, ...] = ()
