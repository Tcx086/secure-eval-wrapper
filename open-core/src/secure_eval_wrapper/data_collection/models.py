"""Inert domain contracts for public crypto market-data collection.

The value objects in this module do not fetch data, connect to an exchange, or persist records.
They describe the boundary between future provider adapters, normalization, validation, and the
PostgreSQL repository layer.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from enum import Enum
from uuid import UUID


class MarketDataType(str, Enum):
    """Crypto market-data categories supported by the Phase 2 contracts."""

    OHLCV = "ohlcv"
    TRADES = "trades"
    FUNDING_RATES = "funding_rates"
    INSTRUMENTS = "instruments"


class ProviderCapabilityStatus(str, Enum):
    """Availability or planning state for a provider/data-type combination."""

    IMPLEMENTED = "implemented"
    PLANNED = "planned"
    UNKNOWN = "unknown"


class CollectionStatus(str, Enum):
    """Lifecycle states a future collector may report."""

    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    PARTIAL = "partial"
    FAILED = "failed"


class TradeSide(str, Enum):
    """Normalized aggressor-side values for public trades."""

    BUY = "buy"
    SELL = "sell"
    UNKNOWN = "unknown"


class InstrumentType(str, Enum):
    """Instrument types aligned with the PostgreSQL market-data schema."""

    SPOT = "spot"
    PERPETUAL = "perpetual"
    FUTURE = "future"
    OPTION = "option"
    INDEX = "index"


class InstrumentStatus(str, Enum):
    """Normalized exchange listing states."""

    ACTIVE = "active"
    INACTIVE = "inactive"
    DELISTED = "delisted"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class ProviderSpec:
    """Static provider metadata; it contains no credentials or client configuration."""

    name: str
    display_name: str
    exchange_name: str
    capabilities: Mapping[MarketDataType, ProviderCapabilityStatus]
    public_market_data_only: bool = True


@dataclass(frozen=True)
class DataRequest:
    """Provider-neutral request description for one future collection operation."""

    collection_run_id: UUID
    provider_name: str
    data_type: MarketDataType
    symbols: tuple[str, ...]
    timeframe: str | None = None
    start_at_utc: datetime | None = None
    end_at_utc: datetime | None = None
    limit: int | None = None
    parameters: Mapping[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class RawObservation:
    """One unvalidated provider payload with complete collection provenance."""

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


@dataclass(frozen=True)
class NormalizedBar:
    """Provider-neutral OHLCV bar awaiting validation and promotion."""

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
    """Provider-neutral public trade awaiting validation and promotion."""

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


@dataclass(frozen=True)
class FundingRate:
    """Normalized public funding-rate observation awaiting validation and promotion."""

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


@dataclass(frozen=True)
class InstrumentMetadata:
    """Normalized public instrument metadata awaiting validation and promotion."""

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


@dataclass(frozen=True)
class CollectionRunSummary:
    """Auditable counts and timing for a future collection run."""

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
