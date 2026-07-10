"""Normalization for public trades, funding rates, and instrument metadata."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from decimal import Decimal, InvalidOperation
from uuid import NAMESPACE_URL, uuid5

from secure_eval_wrapper.data_collection.hashing import sha256_payload
from secure_eval_wrapper.data_collection.models import (
    FundingRate,
    InstrumentKey,
    InstrumentMetadata,
    InstrumentStatus,
    InstrumentType,
    MarketDataType,
    NormalizedTrade,
    RawObservation,
    TradeSide,
)
from secure_eval_wrapper.data_collection.symbols import normalize_symbol
from secure_eval_wrapper.data_collection.time_utils import coerce_utc_datetime, require_utc_datetime


_SHA256 = re.compile(r"^[0-9a-f]{64}$")


def _mapping(observation: RawObservation, data_type: MarketDataType) -> Mapping[str, object]:
    if not isinstance(observation, RawObservation):
        raise TypeError("observation must be a RawObservation")
    if observation.data_type is not data_type:
        raise ValueError(f"observation data_type must be {data_type.value}")
    if not isinstance(observation.payload, Mapping):
        raise ValueError("observation payload must be a mapping")
    if not _SHA256.fullmatch(observation.source_sha256):
        raise ValueError("observation source_sha256 must be lowercase SHA-256")
    require_utc_datetime(observation.request_timestamp_utc, field_name="request_timestamp_utc")
    require_utc_datetime(observation.ingested_at_utc, field_name="ingested_at_utc")
    return observation.payload


def _text(payload: Mapping[str, object], key: str, *, optional: bool = False) -> str | None:
    value = payload.get(key)
    if value is None and optional:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"payload ''{key}'' must be a non-empty string")
    return value.strip()


def _decimal(
    payload: Mapping[str, object],
    key: str,
    *,
    optional: bool = False,
    positive: bool = False,
    non_negative: bool = False,
) -> Decimal | None:
    value = payload.get(key)
    if value in (None, "") and optional:
        return None
    if isinstance(value, bool) or not isinstance(value, (str, int, Decimal)):
        raise ValueError(f"payload ''{key}'' must be an exact decimal value")
    try:
        parsed = Decimal(str(value).strip())
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(f"payload ''{key}'' is not a valid decimal") from exc
    if not parsed.is_finite():
        raise ValueError(f"payload ''{key}'' must be finite")
    if positive and parsed <= 0:
        raise ValueError(f"payload ''{key}'' must be positive")
    if non_negative and parsed < 0:
        raise ValueError(f"payload ''{key}'' must be non-negative")
    return parsed


def _instrument_key(observation: RawObservation) -> InstrumentKey:
    key = observation.instrument_key
    if not isinstance(key, InstrumentKey):
        raise ValueError("observation must preserve an InstrumentKey")
    return key


def _provenance(observation: RawObservation) -> dict[str, object]:
    key = observation.instrument_key
    return {
        "collection_run_id": str(observation.collection_run_id),
        "provider_name": observation.provider_name,
        "exchange_name": observation.exchange_name,
        "source_endpoint": observation.source_endpoint,
        "source_sha256": observation.source_sha256,
        "request_parameters": dict(observation.request_parameters),
        "request_timestamp_utc": observation.request_timestamp_utc,
        "ingested_at_utc": observation.ingested_at_utc,
        "collection_status": observation.collection_status.value,
        "provider_timestamp": observation.provider_timestamp,
        "provider_instrument_id": (
            key.provider_instrument_id if isinstance(key, InstrumentKey) else observation.raw_symbol
        ),
        "instrument_identity_sha256": (
            key.identity_sha256 if isinstance(key, InstrumentKey) else None
        ),
    }


def normalize_trade_observation(observation: RawObservation) -> NormalizedTrade:
    payload = _mapping(observation, MarketDataType.TRADES)
    key = _instrument_key(observation)
    if key.instrument_type is not InstrumentType.SPOT:
        raise ValueError("public trade normalization currently accepts spot instruments only")
    symbol = normalize_symbol(str(_text(payload, "symbol")))
    if symbol != key.canonical_symbol:
        raise ValueError("trade payload symbol conflicts with instrument identity")
    traded_at = coerce_utc_datetime(
        str(_text(payload, "traded_at_utc")),
        field_name="trade traded_at_utc",
    )
    if observation.observed_at_utc is not None and require_utc_datetime(
        observation.observed_at_utc, field_name="trade observed_at_utc"
    ) != traded_at:
        raise ValueError("trade timestamp conflicts with observation provenance")
    provider_trade_id = _text(payload, "provider_trade_id")
    side_value = _text(payload, "side", optional=True)
    side = TradeSide.UNKNOWN if side_value is None else TradeSide(side_value)
    sequence_value = payload.get("provider_sequence")
    if sequence_value is not None and (
        isinstance(sequence_value, bool) or not isinstance(sequence_value, int)
    ):
        raise ValueError("trade provider_sequence must be an integer")
    price = _decimal(payload, "price", positive=True)
    quantity = _decimal(payload, "quantity", positive=True)
    assert price is not None and quantity is not None
    return NormalizedTrade(
        trade_id=uuid5(
            NAMESPACE_URL,
            f"normalized-trade:{key.identity_sha256}:{provider_trade_id}",
        ),
        symbol=symbol,
        exchange=key.exchange_name,
        traded_at_utc=traded_at,
        price=price,
        quantity=quantity,
        side=side,
        source_observation_ids=(observation.observation_id,),
        provider_trade_id=provider_trade_id,
        ingested_at_utc=observation.ingested_at_utc,
        provenance=_provenance(observation),
        instrument_key=key,
        quote_quantity=_decimal(payload, "quote_quantity", optional=True, non_negative=True),
        provider_sequence=sequence_value,
        first_provider_trade_id=_text(payload, "first_provider_trade_id", optional=True),
        last_provider_trade_id=_text(payload, "last_provider_trade_id", optional=True),
    )


def normalize_trade_observations(
    observations: Sequence[RawObservation],
) -> tuple[NormalizedTrade, ...]:
    return tuple(normalize_trade_observation(item) for item in observations)


def normalize_funding_rate_observation(observation: RawObservation) -> FundingRate:
    payload = _mapping(observation, MarketDataType.FUNDING_RATES)
    key = _instrument_key(observation)
    if key.instrument_type is not InstrumentType.PERPETUAL_SWAP:
        raise ValueError("funding rates require a perpetual-swap instrument")
    funding_time = coerce_utc_datetime(
        str(_text(payload, "funding_time_utc")),
        field_name="funding_time_utc",
    )
    rate = _decimal(payload, "rate")
    assert rate is not None
    return FundingRate(
        funding_rate_id=uuid5(
            NAMESPACE_URL,
            f"funding-rate:{key.identity_sha256}:{funding_time.isoformat()}",
        ),
        symbol=key.canonical_symbol,
        exchange=key.exchange_name,
        funding_time_utc=funding_time,
        rate=rate,
        source_observation_ids=(observation.observation_id,),
        funding_interval=_text(payload, "funding_interval", optional=True),
        predicted_rate=_decimal(payload, "predicted_rate", optional=True),
        mark_price=_decimal(payload, "mark_price", optional=True, positive=True),
        index_price=_decimal(payload, "index_price", optional=True, positive=True),
        provenance=_provenance(observation),
        instrument_key=key,
        provider_instrument_id=key.provider_instrument_id,
    )


def normalize_funding_rate_observations(
    observations: Sequence[RawObservation],
) -> tuple[FundingRate, ...]:
    return tuple(normalize_funding_rate_observation(item) for item in observations)


_STATUS_MAP = {
    "active": InstrumentStatus.ACTIVE,
    "trading": InstrumentStatus.ACTIVE,
    "live": InstrumentStatus.ACTIVE,
    "preopen": InstrumentStatus.INACTIVE,
    "pending_trading": InstrumentStatus.INACTIVE,
    "inactive": InstrumentStatus.INACTIVE,
    "halt": InstrumentStatus.INACTIVE,
    "break": InstrumentStatus.INACTIVE,
    "delisted": InstrumentStatus.DELISTED,
    "settled": InstrumentStatus.DELISTED,
    "unknown": InstrumentStatus.UNKNOWN,
}


def normalize_instrument_observation(observation: RawObservation) -> InstrumentMetadata:
    payload = _mapping(observation, MarketDataType.INSTRUMENTS)
    key = _instrument_key(observation)
    status_text = str(_text(payload, "status")).lower()
    if status_text not in _STATUS_MAP:
        raise ValueError(f"unsupported instrument status ''{status_text}''")
    first_seen = observation.observed_at_utc or observation.ingested_at_utc
    first_seen = require_utc_datetime(first_seen, field_name="instrument observed_at_utc")
    listing_text = _text(payload, "listing_at_utc", optional=True)
    expiry_text = _text(payload, "expiry_at_utc", optional=True)
    metadata = payload.get("metadata", {})
    if not isinstance(metadata, Mapping):
        raise ValueError("instrument payload metadata must be a mapping")
    stable_metadata = {
        "instrument_key_sha256": key.identity_sha256,
        "status": _STATUS_MAP[status_text],
        "price_precision": payload.get("price_precision"),
        "quantity_precision": payload.get("quantity_precision"),
        "tick_size": payload.get("tick_size"),
        "quantity_step": payload.get("quantity_step"),
        "minimum_quantity": payload.get("minimum_quantity"),
        "minimum_notional": payload.get("minimum_notional"),
        "contract_value": payload.get("contract_value"),
        "contract_multiplier": payload.get("contract_multiplier"),
        "margin_asset": payload.get("margin_asset"),
        "margin_type": payload.get("margin_type"),
        "listing_at_utc": listing_text,
        "expiry_at_utc": expiry_text,
        "funding_interval": payload.get("funding_interval"),
        "metadata": dict(metadata),
    }
    metadata_sha256 = sha256_payload(stable_metadata)
    return InstrumentMetadata(
        instrument_id=uuid5(
            NAMESPACE_URL,
            f"instrument-metadata:{key.identity_sha256}:{metadata_sha256}",
        ),
        symbol=key.canonical_symbol,
        exchange=key.exchange_name,
        base_asset=key.base_asset,
        quote_asset=key.quote_asset,
        instrument_type=key.instrument_type,
        status=_STATUS_MAP[status_text],
        source_observation_ids=(observation.observation_id,),
        price_precision=payload.get("price_precision") if isinstance(payload.get("price_precision"), int) else None,
        quantity_precision=payload.get("quantity_precision") if isinstance(payload.get("quantity_precision"), int) else None,
        first_seen_at_utc=first_seen,
        last_seen_at_utc=observation.ingested_at_utc,
        metadata={**dict(metadata), "provenance": _provenance(observation)},
        instrument_key=key,
        settlement_asset=key.settlement_asset,
        tick_size=_decimal(payload, "tick_size", optional=True, positive=True),
        quantity_step=_decimal(payload, "quantity_step", optional=True, positive=True),
        minimum_quantity=_decimal(payload, "minimum_quantity", optional=True, non_negative=True),
        minimum_notional=_decimal(payload, "minimum_notional", optional=True, non_negative=True),
        contract_value=_decimal(payload, "contract_value", optional=True, positive=True),
        contract_multiplier=_decimal(payload, "contract_multiplier", optional=True, positive=True),
        margin_asset=_text(payload, "margin_asset", optional=True),
        margin_type=_text(payload, "margin_type", optional=True),
        listing_at_utc=(
            coerce_utc_datetime(listing_text, field_name="listing_at_utc")
            if listing_text is not None else None
        ),
        expiry_at_utc=(
            coerce_utc_datetime(expiry_text, field_name="expiry_at_utc")
            if expiry_text is not None else None
        ),
        funding_interval=_text(payload, "funding_interval", optional=True),
        metadata_sha256=metadata_sha256,
    )


def normalize_instrument_observations(
    observations: Sequence[RawObservation],
) -> tuple[InstrumentMetadata, ...]:
    return tuple(normalize_instrument_observation(item) for item in observations)


__all__ = [
    "normalize_funding_rate_observation",
    "normalize_funding_rate_observations",
    "normalize_instrument_observation",
    "normalize_instrument_observations",
    "normalize_trade_observation",
    "normalize_trade_observations",
]
