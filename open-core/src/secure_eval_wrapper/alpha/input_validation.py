"""Shared point-in-time preparation for validation-gated alpha inputs."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Sequence
from uuid import UUID

from secure_eval_wrapper.data_collection.hashing import sha256_payload
from secure_eval_wrapper.data_collection.models import (
    FundingIntervalSource,
    FundingRate,
    InstrumentType,
    NormalizedBar,
)
from secure_eval_wrapper.data_collection.time_utils import require_utc_datetime


AlphaInputRecord = NormalizedBar | FundingRate
_ACCEPTED_STATUSES = {"accepted", "accepted_with_warnings"}


def record_timestamp(record: AlphaInputRecord) -> datetime:
    return record.bar_open_time_utc if isinstance(record, NormalizedBar) else record.funding_time_utc


def record_symbol(record: AlphaInputRecord) -> str:
    return record.symbol


def record_source_ids(record: AlphaInputRecord) -> tuple[UUID, ...]:
    return tuple(record.source_observation_ids)


def record_data_type(record: AlphaInputRecord) -> str:
    return "ohlcv" if isinstance(record, NormalizedBar) else "funding_rates"


@dataclass(frozen=True)
class AlphaDataSet:
    """An explicit boundary proving records passed the Phase 2 validation gate."""

    records: tuple[AlphaInputRecord, ...]
    validation_status: str
    validation_report_ids: tuple[UUID, ...]
    dataset_ref: str

    def __post_init__(self) -> None:
        if self.validation_status not in _ACCEPTED_STATUSES:
            raise ValueError("alpha input requires accepted validation-gated data")
        if not self.records:
            raise ValueError("alpha input dataset must contain records")
        if not self.validation_report_ids:
            raise ValueError("alpha input requires validation report lineage")
        if not self.dataset_ref.strip():
            raise ValueError("dataset_ref must be non-empty")
        for record in self.records:
            if not isinstance(record, (NormalizedBar, FundingRate)):
                raise TypeError("alpha inputs must be normalized bars or funding rates")

    @property
    def dataset_sha256(self) -> str:
        rows = []
        for record in sorted(
            self.records,
            key=lambda item: (record_symbol(item), record_timestamp(item), str(record_source_ids(item))),
        ):
            if isinstance(record, NormalizedBar):
                content = {
                    "type": "ohlcv",
                    "symbol": record.symbol,
                    "exchange": record.exchange,
                    "timeframe": record.timeframe,
                    "timestamp_utc": record.bar_open_time_utc,
                    "open": record.open,
                    "high": record.high,
                    "low": record.low,
                    "close": record.close,
                    "volume": record.volume,
                    "is_final": record.is_final,
                    "source_observation_ids": record.source_observation_ids,
                }
            else:
                key = record.instrument_key
                content = {
                    "type": "funding_rates",
                    "symbol": record.symbol,
                    "exchange": record.exchange,
                    "timestamp_utc": record.funding_time_utc,
                    "rate": record.rate,
                    "funding_interval": record.funding_interval,
                    "funding_interval_source": record.funding_interval_source,
                    "instrument_identity": key.identity_sha256 if key is not None else None,
                    "source_observation_ids": record.source_observation_ids,
                }
            rows.append(content)
        return sha256_payload(
            {
                "dataset_ref": self.dataset_ref,
                "validation_status": self.validation_status,
                "validation_report_ids": tuple(sorted(self.validation_report_ids, key=str)),
                "records": rows,
            }
        )


class PointInTimeSeries:
    """Immutable, deterministically sorted single-symbol history with trailing-only access."""

    def __init__(self, records: Sequence[AlphaInputRecord]) -> None:
        materialized = tuple(records)
        if not materialized:
            raise ValueError("point-in-time series cannot be empty")
        if any(not isinstance(item, type(materialized[0])) for item in materialized):
            raise ValueError("point-in-time series cannot mix data types")
        symbols = {record_symbol(item) for item in materialized}
        if len(symbols) != 1:
            raise ValueError("point-in-time series requires exactly one symbol")
        if isinstance(materialized[0], NormalizedBar):
            timeframes = {item.timeframe for item in materialized if isinstance(item, NormalizedBar)}
            if len(timeframes) != 1:
                raise ValueError("point-in-time series cannot mix timeframes")
            exchanges = {item.exchange for item in materialized if isinstance(item, NormalizedBar)}
            if len(exchanges) != 1:
                raise ValueError("point-in-time series cannot mix exchanges")
            if any(item.is_final is False for item in materialized if isinstance(item, NormalizedBar)):
                raise ValueError("non-final bars are not eligible for alpha evaluation")
        else:
            instrument_identities = set()
            for item in materialized:
                assert isinstance(item, FundingRate)
                key = item.instrument_key
                if key is not None:
                    instrument_identities.add(key.identity_sha256)
                if key is None or key.instrument_type is not InstrumentType.PERPETUAL_SWAP:
                    raise ValueError("funding alpha requires an unambiguous perpetual instrument")
                if item.funding_interval_source is FundingIntervalSource.UNAVAILABLE:
                    raise ValueError("funding alpha requires grounded funding interval evidence")
            if len(instrument_identities) != 1:
                raise ValueError("funding alpha cannot mix instrument identities")
        ordered = tuple(sorted(materialized, key=lambda item: record_timestamp(item)))
        timestamps = tuple(record_timestamp(item) for item in ordered)
        for timestamp in timestamps:
            require_utc_datetime(timestamp, field_name="alpha input timestamp")
        if len(set(timestamps)) != len(timestamps):
            raise ValueError("duplicate logical timestamps are not allowed")
        self._records = ordered

    @property
    def records(self) -> tuple[AlphaInputRecord, ...]:
        return self._records

    @property
    def symbol(self) -> str:
        return record_symbol(self._records[0])

    @property
    def data_type(self) -> str:
        return record_data_type(self._records[0])

    def prior(self, index: int, offset: int = 1) -> AlphaInputRecord:
        if offset <= 0:
            raise ValueError("prior offset must be positive")
        target = index - offset
        if target < 0:
            raise IndexError("insufficient prior history")
        return self._records[target]

    def trailing(
        self,
        index: int,
        length: int,
        *,
        include_current: bool = True,
    ) -> tuple[AlphaInputRecord, ...]:
        if length <= 0:
            raise ValueError("trailing window length must be positive")
        end = index + 1 if include_current else index
        start = end - length
        if start < 0 or end > len(self._records):
            raise IndexError("insufficient trailing history")
        window = self._records[start:end]
        output_time = record_timestamp(self._records[index])
        if any(record_timestamp(item) > output_time for item in window):
            raise AssertionError("point-in-time window included a future record")
        return window

    @staticmethod
    def decimals(records: Sequence[AlphaInputRecord], field_name: str) -> tuple[Decimal, ...]:
        values = []
        for record in records:
            value = getattr(record, field_name)
            if not isinstance(value, Decimal) or not value.is_finite():
                raise ValueError(f"{field_name} must contain finite Decimal values")
            values.append(value)
        return tuple(values)


def prepare_point_in_time_series(
    dataset: AlphaDataSet,
    *,
    symbol: str,
    required_data_type: str,
) -> PointInTimeSeries:
    if not isinstance(dataset, AlphaDataSet):
        raise TypeError("dataset must be an AlphaDataSet validation-gate boundary")
    records = tuple(
        item
        for item in dataset.records
        if record_symbol(item) == symbol and record_data_type(item) == required_data_type
    )
    if not records:
        raise ValueError(f"no {required_data_type} records available for {symbol}")
    return PointInTimeSeries(records)


__all__ = [
    "AlphaDataSet",
    "AlphaInputRecord",
    "PointInTimeSeries",
    "prepare_point_in_time_series",
    "record_data_type",
    "record_source_ids",
    "record_symbol",
    "record_timestamp",
]
