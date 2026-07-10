"""Auditable domain contracts for public, point-in-time alpha evaluation."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from enum import Enum
from types import MappingProxyType
from typing import Mapping
from uuid import UUID

from secure_eval_wrapper.data_collection.hashing import sha256_payload
from secure_eval_wrapper.data_collection.time_utils import require_utc_datetime


_SHA256 = re.compile(r"^[0-9a-f]{64}$")


class AlphaStatus(str, Enum):
    DRAFT = "draft"
    ACTIVE = "active"
    DEPRECATED = "deprecated"


class AlphaRunStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    PARTIAL = "partial"
    FAILED = "failed"


@dataclass(frozen=True)
class AlphaDefinition:
    alpha_id: UUID
    name: str
    version: str
    description: str
    category: str
    required_data_types: tuple[str, ...]
    required_fields: tuple[str, ...]
    parameter_schema: Mapping[str, object]
    default_parameters: Mapping[str, object]
    minimum_warmup: int
    output_semantics: str
    horizon: str
    public_example: bool
    status: AlphaStatus
    implementation_sha256: str

    def __post_init__(self) -> None:
        for name in ("name", "version", "description", "category", "output_semantics", "horizon"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"AlphaDefinition {name} must be non-empty")
        if self.minimum_warmup < 0:
            raise ValueError("minimum_warmup must be non-negative")
        if not self.public_example:
            raise ValueError("the public registry accepts public example alphas only")
        object.__setattr__(self, "status", AlphaStatus(self.status))
        if not _SHA256.fullmatch(self.implementation_sha256):
            raise ValueError("implementation_sha256 must be a lowercase SHA-256 digest")
        object.__setattr__(self, "parameter_schema", MappingProxyType(dict(self.parameter_schema)))
        object.__setattr__(self, "default_parameters", MappingProxyType(dict(self.default_parameters)))

    @property
    def content_sha256(self) -> str:
        return sha256_payload(
            {
                "alpha_id": self.alpha_id,
                "name": self.name,
                "version": self.version,
                "description": self.description,
                "category": self.category,
                "required_data_types": self.required_data_types,
                "required_fields": self.required_fields,
                "parameter_schema": dict(self.parameter_schema),
                "default_parameters": dict(self.default_parameters),
                "minimum_warmup": self.minimum_warmup,
                "output_semantics": self.output_semantics,
                "horizon": self.horizon,
                "public_example": self.public_example,
                "status": self.status,
                "implementation_sha256": self.implementation_sha256,
            }
        )


@dataclass(frozen=True)
class AlphaEvaluationRequest:
    evaluation_run_id: UUID
    alpha_name: str
    symbols: tuple[str, ...]
    window_start_utc: datetime
    window_end_utc: datetime
    dataset_refs: tuple[str, ...]
    dataset_sha256: str
    parameters: Mapping[str, object] = field(default_factory=dict)
    alpha_version: str | None = None
    code_sha256: str | None = None
    fail_fast: bool = True
    persistence_enabled: bool = False

    def __post_init__(self) -> None:
        start = require_utc_datetime(self.window_start_utc, field_name="window_start_utc")
        end = require_utc_datetime(self.window_end_utc, field_name="window_end_utc")
        if start >= end:
            raise ValueError("alpha evaluation window must be non-empty and half-open")
        if not self.alpha_name.strip():
            raise ValueError("alpha_name must be non-empty")
        if not self.symbols or any(not value.strip() for value in self.symbols):
            raise ValueError("symbols must contain non-empty identities")
        if len(set(self.symbols)) != len(self.symbols):
            raise ValueError("symbols must be unique")
        if not self.dataset_refs or any(not value.strip() for value in self.dataset_refs):
            raise ValueError("dataset_refs must be non-empty")
        if not _SHA256.fullmatch(self.dataset_sha256):
            raise ValueError("dataset_sha256 must be a lowercase SHA-256 digest")
        if self.code_sha256 is not None and not _SHA256.fullmatch(self.code_sha256):
            raise ValueError("code_sha256 must be a lowercase SHA-256 digest")

    @property
    def config_sha256(self) -> str:
        return sha256_payload(
            {
                "alpha_name": self.alpha_name,
                "alpha_version": self.alpha_version,
                "symbols": tuple(sorted(self.symbols)),
                "window_start_utc": self.window_start_utc,
                "window_end_utc": self.window_end_utc,
                "dataset_refs": tuple(sorted(self.dataset_refs)),
                "parameters": dict(self.parameters),
                "fail_fast": self.fail_fast,
            }
        )


@dataclass(frozen=True)
class AlphaComputationPoint:
    timestamp_utc: datetime
    raw_score: Decimal | None
    warmup_complete: bool
    valid: bool
    source_observation_ids: tuple[UUID, ...]
    source_timestamps_utc: tuple[datetime, ...]
    provenance: Mapping[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        require_utc_datetime(self.timestamp_utc, field_name="alpha point timestamp")
        for timestamp in self.source_timestamps_utc:
            require_utc_datetime(timestamp, field_name="alpha source timestamp")
        if self.valid:
            if self.raw_score is None or not self.raw_score.is_finite():
                raise ValueError("valid alpha points require a finite Decimal score")
            if not self.warmup_complete:
                raise ValueError("valid alpha points require completed warmup")
        elif self.raw_score is not None and not self.raw_score.is_finite():
            raise ValueError("alpha point scores must be finite when present")


@dataclass(frozen=True)
class AlphaValue:
    alpha_value_id: UUID
    alpha_id: UUID
    alpha_name: str
    alpha_version: str
    alpha_run_id: UUID
    symbol: str
    timestamp_utc: datetime
    raw_score: Decimal | None
    warmup_complete: bool
    valid: bool
    horizon: str
    source_observation_ids: tuple[UUID, ...]
    dataset_sha256: str
    config_sha256: str
    implementation_sha256: str
    provenance: Mapping[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        require_utc_datetime(self.timestamp_utc, field_name="AlphaValue timestamp_utc")
        for digest_name in ("dataset_sha256", "config_sha256", "implementation_sha256"):
            if not _SHA256.fullmatch(getattr(self, digest_name)):
                raise ValueError(f"{digest_name} must be a lowercase SHA-256 digest")
        if self.valid and (self.raw_score is None or not self.raw_score.is_finite()):
            raise ValueError("valid AlphaValue requires a finite Decimal raw_score")
        if self.valid and not self.warmup_complete:
            raise ValueError("valid AlphaValue requires completed warmup")
        if self.raw_score is not None and not self.raw_score.is_finite():
            raise ValueError("AlphaValue raw_score must be finite when present")

    @property
    def content_sha256(self) -> str:
        return sha256_payload(
            {
                "alpha_value_id": self.alpha_value_id,
                "alpha_id": self.alpha_id,
                "alpha_name": self.alpha_name,
                "alpha_version": self.alpha_version,
                "alpha_run_id": self.alpha_run_id,
                "symbol": self.symbol,
                "timestamp_utc": self.timestamp_utc,
                "raw_score": self.raw_score,
                "warmup_complete": self.warmup_complete,
                "valid": self.valid,
                "horizon": self.horizon,
                "source_observation_ids": self.source_observation_ids,
                "dataset_sha256": self.dataset_sha256,
                "config_sha256": self.config_sha256,
                "implementation_sha256": self.implementation_sha256,
                "provenance": dict(self.provenance),
            }
        )


@dataclass(frozen=True)
class AlphaRun:
    alpha_run_id: UUID
    alpha_id: UUID
    alpha_name: str
    alpha_version: str
    symbols: tuple[str, ...]
    window_start_utc: datetime
    window_end_utc: datetime
    dataset_refs: tuple[str, ...]
    input_data_sha256: str
    config_sha256: str
    implementation_sha256: str
    started_at_utc: datetime
    completed_at_utc: datetime | None
    status: AlphaRunStatus
    output_count: int
    rejected_count: int
    skipped_count: int
    metadata: Mapping[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        start = require_utc_datetime(self.window_start_utc, field_name="AlphaRun window start")
        end = require_utc_datetime(self.window_end_utc, field_name="AlphaRun window end")
        require_utc_datetime(self.started_at_utc, field_name="AlphaRun started_at_utc")
        if start >= end:
            raise ValueError("AlphaRun window must be half-open")
        if self.completed_at_utc is not None:
            require_utc_datetime(self.completed_at_utc, field_name="AlphaRun completed_at_utc")
        object.__setattr__(self, "status", AlphaRunStatus(self.status))
        if min(self.output_count, self.rejected_count, self.skipped_count) < 0:
            raise ValueError("AlphaRun counts must be non-negative")


@dataclass(frozen=True)
class AlphaFailure:
    symbol: str | None
    stage: str
    error_type: str
    message: str


@dataclass(frozen=True)
class AlphaEvaluationResult:
    run: AlphaRun
    values: tuple[AlphaValue, ...]
    failures: tuple[AlphaFailure, ...] = ()


class AlphaEvaluationError(RuntimeError):
    def __init__(self, failure: AlphaFailure) -> None:
        super().__init__(f"alpha {failure.stage} failed: {failure.message}")
        self.failure = failure


__all__ = [
    "AlphaComputationPoint",
    "AlphaDefinition",
    "AlphaEvaluationError",
    "AlphaEvaluationRequest",
    "AlphaEvaluationResult",
    "AlphaFailure",
    "AlphaRun",
    "AlphaRunStatus",
    "AlphaStatus",
    "AlphaValue",
]
