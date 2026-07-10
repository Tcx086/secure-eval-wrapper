"""Stable series identity and point-in-time availability helpers."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Sequence

from secure_eval_wrapper.data_collection.hashing import sha256_payload
from secure_eval_wrapper.data_collection.models import FundingRate, InstrumentType, NormalizedBar
from secure_eval_wrapper.data_collection.time_utils import require_utc_datetime

_FIXED_TIMEFRAME = re.compile(r"^([1-9][0-9]*)([smhd])$")


@dataclass(frozen=True)
class SeriesIdentity:
    """Immutable identity for one provider instrument and observation series."""

    provider_name: str
    exchange: str
    provider_instrument_id: str
    canonical_symbol: str
    instrument_type: InstrumentType
    timeframe: str
    settlement_asset: str | None = None

    def __post_init__(self) -> None:
        for name in (
            "provider_name",
            "exchange",
            "provider_instrument_id",
            "canonical_symbol",
            "timeframe",
        ):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"SeriesIdentity {name} must be non-empty")
            object.__setattr__(self, name, value.strip())
        object.__setattr__(self, "instrument_type", InstrumentType(self.instrument_type))
        if self.settlement_asset is not None:
            if not isinstance(self.settlement_asset, str) or not self.settlement_asset.strip():
                raise ValueError("SeriesIdentity settlement_asset must be non-empty when present")
            object.__setattr__(self, "settlement_asset", self.settlement_asset.strip().upper())
        if self.instrument_type in (InstrumentType.PERPETUAL_SWAP, InstrumentType.DATED_FUTURE) and self.settlement_asset is None:
            raise ValueError("derivative SeriesIdentity requires a settlement_asset")

    @property
    def series_identity_sha256(self) -> str:
        return sha256_payload(self.as_dict())

    def as_dict(self) -> dict[str, object]:
        return {
            "provider_name": self.provider_name,
            "exchange": self.exchange,
            "provider_instrument_id": self.provider_instrument_id,
            "canonical_symbol": self.canonical_symbol,
            "instrument_type": self.instrument_type.value,
            "timeframe": self.timeframe,
            "settlement_asset": self.settlement_asset,
        }

    @classmethod
    def legacy(cls, symbol: str, *, timeframe: str = "unspecified") -> "SeriesIdentity":
        return cls("legacy", "legacy", symbol, symbol, InstrumentType.SPOT, timeframe)


def fixed_timeframe_duration(timeframe: str) -> timedelta:
    """Return a duration only for explicitly supported, non-calendar timeframes."""

    if not isinstance(timeframe, str):
        raise ValueError("timeframe must be a string")
    match = _FIXED_TIMEFRAME.fullmatch(timeframe.strip())
    if match is None:
        raise ValueError(f"unsupported or calendar-dependent timeframe: {timeframe!r}")
    amount = int(match.group(1))
    unit = match.group(2)
    seconds = amount * {"s": 1, "m": 60, "h": 3600, "d": 86400}[unit]
    return timedelta(seconds=seconds)


def bar_available_at_utc(bar: NormalizedBar) -> datetime:
    """Return persisted close time, or a conservative fixed-duration legacy derivation."""

    opened = require_utc_datetime(bar.bar_open_time_utc, field_name="bar_open_time_utc")
    if bar.is_final is False:
        raise ValueError("non-final bars are not eligible for alpha evaluation")
    if bar.bar_close_time_utc is None:
        closed = opened + fixed_timeframe_duration(bar.timeframe)
    else:
        closed = require_utc_datetime(bar.bar_close_time_utc, field_name="bar_close_time_utc")
    if closed <= opened:
        raise ValueError("bar_close_time_utc must be after bar_open_time_utc")
    return closed


def record_available_at_utc(record: NormalizedBar | FundingRate) -> datetime:
    if isinstance(record, NormalizedBar):
        return bar_available_at_utc(record)
    return require_utc_datetime(record.funding_time_utc, field_name="funding_time_utc")


def series_identity_from_record(record: NormalizedBar | FundingRate) -> SeriesIdentity:
    key = record.instrument_key
    if key is not None:
        if key.canonical_symbol != record.symbol:
            raise ValueError("record symbol conflicts with provider instrument canonical_symbol")
        timeframe = record.timeframe if isinstance(record, NormalizedBar) else "funding_rate"
        return SeriesIdentity(
            provider_name=key.provider_name,
            exchange=key.exchange_name,
            provider_instrument_id=key.provider_instrument_id,
            canonical_symbol=key.canonical_symbol,
            instrument_type=key.instrument_type,
            timeframe=timeframe,
            settlement_asset=key.settlement_asset,
        )
    if isinstance(record, FundingRate):
        raise ValueError("funding series requires a complete provider instrument identity")
    provenance = dict(record.provenance)
    provider = str(provenance.get("provider_name") or record.exchange)
    provider_instrument_id = str(
        provenance.get("provider_instrument_id")
        or provenance.get("raw_symbol")
        or record.symbol
    )
    instrument_type = InstrumentType(provenance.get("instrument_type", InstrumentType.SPOT))
    settlement = provenance.get("settlement_asset")
    return SeriesIdentity(
        provider_name=provider,
        exchange=record.exchange,
        provider_instrument_id=provider_instrument_id,
        canonical_symbol=record.symbol,
        instrument_type=instrument_type,
        timeframe=record.timeframe,
        settlement_asset=None if settlement is None else str(settlement),
    )


def stable_economic_record(record: NormalizedBar | FundingRate) -> dict[str, object]:
    identity = series_identity_from_record(record)
    if isinstance(record, NormalizedBar):
        opened = require_utc_datetime(record.bar_open_time_utc, field_name="bar_open_time_utc")
        closed = (
            opened + fixed_timeframe_duration(record.timeframe)
            if record.bar_close_time_utc is None
            else require_utc_datetime(record.bar_close_time_utc, field_name="bar_close_time_utc")
        )
        if closed <= opened:
            raise ValueError("bar_close_time_utc must be after bar_open_time_utc")
        return {
            "data_type": "ohlcv",
            "series_identity": identity.as_dict(),
            "bar_open_time_utc": opened,
            "bar_available_at_utc": closed,
            "open": record.open,
            "high": record.high,
            "low": record.low,
            "close": record.close,
            "volume": record.volume,
            "is_final": record.is_final is not False,
        }
    return {
        "data_type": "funding_rates",
        "series_identity": identity.as_dict(),
        "funding_time_utc": require_utc_datetime(record.funding_time_utc, field_name="funding_time_utc"),
        "rate": record.rate,
        "funding_interval": record.funding_interval,
        "funding_interval_source": record.funding_interval_source.value,
    }


def eligible_input_sha256(
    records: Sequence[NormalizedBar | FundingRate],
    *,
    as_of_utc: datetime,
) -> str:
    as_of = require_utc_datetime(as_of_utc, field_name="as_of_utc")
    eligible = [record for record in records if record_available_at_utc(record) <= as_of]
    rows = sorted(
        (stable_economic_record(record) for record in eligible),
        key=lambda row: (
            str(row["series_identity"]),
            str(row.get("bar_available_at_utc") or row.get("funding_time_utc")),
            sha256_payload(row),
        ),
    )
    return sha256_payload({"records": rows})


__all__ = [
    "SeriesIdentity",
    "bar_available_at_utc",
    "eligible_input_sha256",
    "fixed_timeframe_duration",
    "record_available_at_utc",
    "series_identity_from_record",
    "stable_economic_record",
]
