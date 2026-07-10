"""Shared point-in-time preparation for validation-gated alpha inputs."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Sequence
from uuid import UUID

from secure_eval_wrapper.alpha.identity import (
    SeriesIdentity,
    eligible_input_sha256,
    record_available_at_utc,
    series_identity_from_record,
    stable_economic_record,
)
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
    """Economic availability timestamp used by point-in-time evaluation."""

    return record_available_at_utc(record)


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
        if any(not isinstance(record, (NormalizedBar, FundingRate)) for record in self.records):
            raise TypeError("alpha inputs must be normalized bars or funding rates")

    @property
    def dataset_sha256(self) -> str:
        """Collection-independent economic dataset digest retained as run provenance."""

        rows = sorted(
            (stable_economic_record(record) for record in self.records),
            key=lambda row: (
                str(row["series_identity"]),
                str(row.get("bar_available_at_utc") or row.get("funding_time_utc")),
                sha256_payload(row),
            ),
        )
        return sha256_payload({"validation_status": self.validation_status, "records": rows})


class PointInTimeSeries:
    """Immutable, deterministically sorted history for exactly one complete series identity."""

    def __init__(self, records: Sequence[AlphaInputRecord]) -> None:
        materialized = tuple(records)
        if not materialized:
            raise ValueError("point-in-time series cannot be empty")
        if any(not isinstance(item, type(materialized[0])) for item in materialized):
            raise ValueError("point-in-time series cannot mix data types")
        identities = {series_identity_from_record(item) for item in materialized}
        if len(identities) != 1:
            raise ValueError("point-in-time series requires exactly one complete series identity")
        if isinstance(materialized[0], NormalizedBar):
            if any(item.is_final is False for item in materialized if isinstance(item, NormalizedBar)):
                raise ValueError("non-final bars are not eligible for alpha evaluation")
        else:
            for item in materialized:
                assert isinstance(item, FundingRate)
                key = item.instrument_key
                if key is None or key.instrument_type is not InstrumentType.PERPETUAL_SWAP:
                    raise ValueError("funding alpha requires an unambiguous perpetual instrument")
                if item.funding_interval_source is FundingIntervalSource.UNAVAILABLE:
                    raise ValueError("funding alpha requires grounded funding interval evidence")
        ordered = tuple(sorted(materialized, key=record_timestamp))
        timestamps = tuple(record_timestamp(item) for item in ordered)
        if len(set(timestamps)) != len(timestamps):
            raise ValueError("duplicate logical availability timestamps are not allowed")
        if isinstance(materialized[0], NormalizedBar):
            open_times = tuple(item.bar_open_time_utc for item in ordered if isinstance(item, NormalizedBar))
            if len(set(open_times)) != len(open_times):
                raise ValueError("duplicate bar open timestamps are not allowed")
        self._records = ordered
        self._series_identity = next(iter(identities))

    @property
    def records(self) -> tuple[AlphaInputRecord, ...]:
        return self._records

    @property
    def series_identity(self) -> SeriesIdentity:
        return self._series_identity

    @property
    def symbol(self) -> str:
        return self._series_identity.canonical_symbol

    @property
    def data_type(self) -> str:
        return record_data_type(self._records[0])

    def eligible_as_of(self, as_of_utc: datetime) -> "PointInTimeSeries":
        as_of = require_utc_datetime(as_of_utc, field_name="as_of_utc")
        eligible = tuple(item for item in self._records if record_timestamp(item) <= as_of)
        if not eligible:
            raise ValueError(f"no records are available at {as_of.isoformat()}")
        return PointInTimeSeries(eligible)

    def eligible_input_sha256(self, as_of_utc: datetime) -> str:
        return eligible_input_sha256(self._records, as_of_utc=as_of_utc)

    def prior(self, index: int, offset: int = 1) -> AlphaInputRecord:
        if offset <= 0:
            raise ValueError("prior offset must be positive")
        target = index - offset
        if target < 0:
            raise IndexError("insufficient prior history")
        return self._records[target]

    def trailing(self, index: int, length: int, *, include_current: bool = True) -> tuple[AlphaInputRecord, ...]:
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


def series_identities_for_dataset(
    dataset: AlphaDataSet,
    *,
    symbols: Sequence[str],
    required_data_type: str,
) -> tuple[SeriesIdentity, ...]:
    requested = set(symbols)
    identities = {
        series_identity_from_record(item)
        for item in dataset.records
        if record_symbol(item) in requested and record_data_type(item) == required_data_type
    }
    return tuple(sorted(identities, key=lambda item: item.series_identity_sha256))


def prepare_point_in_time_series(
    dataset: AlphaDataSet,
    *,
    required_data_type: str,
    series_identity: SeriesIdentity | None = None,
    symbol: str | None = None,
) -> PointInTimeSeries:
    if not isinstance(dataset, AlphaDataSet):
        raise TypeError("dataset must be an AlphaDataSet validation-gate boundary")
    if series_identity is None and symbol is None:
        raise ValueError("series_identity or symbol is required")
    records = []
    for item in dataset.records:
        if record_data_type(item) != required_data_type:
            continue
        if isinstance(item, NormalizedBar) and item.is_final is False:
            continue
        identity = series_identity_from_record(item)
        if series_identity is not None and identity != series_identity:
            continue
        if series_identity is None and identity.canonical_symbol != symbol:
            continue
        records.append(item)
    if not records:
        label = series_identity.series_identity_sha256 if series_identity is not None else symbol
        raise ValueError(f"no eligible {required_data_type} records available for {label}")
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
    "series_identities_for_dataset",
]
