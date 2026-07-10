"""Inert data-quality, reconciliation, and promotion contracts."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from uuid import UUID

from secure_eval_wrapper.data_collection.models import MarketDataType


class ValidationSeverity(str, Enum):
    """Impact assigned to a validation check."""

    INFO = "info"
    WARNING = "warning"
    ERROR = "error"
    CRITICAL = "critical"


class ValidationCheckStatus(str, Enum):
    """Outcome of an individual validation or reconciliation check."""

    PASSED = "passed"
    WARNING = "warning"
    FAILED = "failed"
    SKIPPED = "skipped"


class ValidationStatus(str, Enum):
    """Final gate decision for a dataset."""

    ACCEPTED = "accepted"
    ACCEPTED_WITH_WARNINGS = "accepted_with_warnings"
    REJECTED = "rejected"
    QUARANTINED = "quarantined"
    FAILED = "failed"


class QuarantineReason(str, Enum):
    """Stable reason codes for observations that cannot be promoted."""

    AMBIGUOUS_TIMESTAMP = "ambiguous_timestamp"
    MISSING_REQUIRED_DATA = "missing_required_data"
    DUPLICATE_RECORD = "duplicate_record"
    NON_MONOTONIC_TIMESTAMP = "non_monotonic_timestamp"
    INVALID_OHLC_RELATIONSHIP = "invalid_ohlc_relationship"
    INVALID_VOLUME = "invalid_volume"
    PRICE_OUTLIER = "price_outlier"
    VOLUME_ANOMALY = "volume_anomaly"
    STALE_DATA = "stale_data"
    PARTIAL_CANDLE = "partial_candle"
    SYMBOL_MAPPING_INCONSISTENCY = "symbol_mapping_inconsistency"
    FUNDING_TIMESTAMP_GAP = "funding_timestamp_gap"
    INSTRUMENT_METADATA_DRIFT = "instrument_metadata_drift"
    CROSS_SOURCE_MISMATCH = "cross_source_mismatch"
    PARSE_ERROR = "parse_error"
    UNSUPPORTED_PAYLOAD = "unsupported_payload"


@dataclass(frozen=True)
class ValidationCheck:
    """Declarative check definition with reproducible parameters."""

    check_id: UUID
    check_type: str
    description: str
    severity: ValidationSeverity
    data_types: tuple[MarketDataType, ...]
    parameters: Mapping[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class ValidationResult:
    """Outcome emitted by a future validator for one declared check."""

    result_id: UUID
    validation_run_id: UUID
    check_id: UUID
    status: ValidationCheckStatus
    created_at_utc: datetime
    message: str
    symbol: str | None = None
    timeframe: str | None = None
    window_start_utc: datetime | None = None
    window_end_utc: datetime | None = None
    affected_observation_ids: tuple[UUID, ...] = ()
    details: Mapping[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class ValidationReport:
    """Dataset-level validation gate record aligned with the PostgreSQL design."""

    validation_report_id: UUID
    validation_run_id: UUID
    dataset_ref: str
    provider_names: tuple[str, ...]
    data_types: tuple[MarketDataType, ...]
    symbols: tuple[str, ...]
    timeframes: tuple[str, ...]
    window_start_utc: datetime | None
    window_end_utc: datetime | None
    results: tuple[ValidationResult, ...]
    accepted_count: int
    rejected_count: int
    warning_count: int
    status: ValidationStatus
    tolerance_config_sha256: str
    source_hashes: tuple[str, ...]
    report_sha256: str | None
    created_at_utc: datetime


@dataclass(frozen=True)
class ReconciliationResult:
    """Cross-provider comparison outcome for one logical data window."""

    reconciliation_id: UUID
    validation_run_id: UUID
    data_type: MarketDataType
    symbol: str
    timeframe: str | None
    provider_names: tuple[str, ...]
    window_start_utc: datetime | None
    window_end_utc: datetime | None
    status: ValidationCheckStatus
    results: tuple[ValidationResult, ...]
    metrics: Mapping[str, object]
    config_sha256: str
    dataset_sha256: str
    result_sha256: str
    created_at_utc: datetime
