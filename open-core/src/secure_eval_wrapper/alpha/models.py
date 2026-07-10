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

from secure_eval_wrapper.alpha.identity import SeriesIdentity
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


class AlphaEvaluationStatus(str, Enum):
    EMITTED = "emitted"
    WARMUP = "warmup"
    SKIPPED = "skipped"
    INVALID = "invalid"
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
    formula_sha256: str | None = None
    implementation_code_sha256: str | None = None
    repository_commit_sha: str = "source-tree"

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
        code_hash = self.implementation_sha256
        formula_hash = self.formula_sha256 or sha256_payload(
            {"name": self.name, "version": self.version, "output_semantics": self.output_semantics}
        )
        for name, digest in (("implementation_sha256", self.implementation_sha256), ("implementation_code_sha256", code_hash), ("formula_sha256", formula_hash)):
            if not _SHA256.fullmatch(digest):
                raise ValueError(f"{name} must be a lowercase SHA-256 digest")
        if not self.repository_commit_sha.strip():
            raise ValueError("repository_commit_sha must be non-empty")
        object.__setattr__(self, "implementation_code_sha256", code_hash)
        object.__setattr__(self, "formula_sha256", formula_hash)
        object.__setattr__(self, "parameter_schema", MappingProxyType(dict(self.parameter_schema)))
        object.__setattr__(self, "default_parameters", MappingProxyType(dict(self.default_parameters)))

    @property
    def content_sha256(self) -> str:
        return sha256_payload({
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
            "formula_sha256": self.formula_sha256,
            "implementation_code_sha256": self.implementation_code_sha256,
            "repository_commit_sha": self.repository_commit_sha,
        })


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
    as_of_utc: datetime | None = None
    series_identities: tuple[SeriesIdentity, ...] = ()
    repository_commit_sha: str | None = None

    def __post_init__(self) -> None:
        start = require_utc_datetime(self.window_start_utc, field_name="window_start_utc")
        end = require_utc_datetime(self.window_end_utc, field_name="window_end_utc")
        if start >= end:
            raise ValueError("alpha evaluation window must be non-empty and half-open")
        if self.as_of_utc is not None:
            require_utc_datetime(self.as_of_utc, field_name="as_of_utc")
        if not self.alpha_name.strip():
            raise ValueError("alpha_name must be non-empty")
        if not self.symbols or any(not value.strip() for value in self.symbols):
            raise ValueError("symbols must contain non-empty canonical symbols")
        if len(set(self.symbols)) != len(self.symbols):
            raise ValueError("symbols must be unique canonical selectors")
        if not self.dataset_refs or any(not value.strip() for value in self.dataset_refs):
            raise ValueError("dataset_refs must be non-empty")
        if not _SHA256.fullmatch(self.dataset_sha256):
            raise ValueError("dataset_sha256 must be a lowercase SHA-256 digest")
        if self.code_sha256 is not None and not _SHA256.fullmatch(self.code_sha256):
            raise ValueError("code_sha256 must be a lowercase SHA-256 digest")
        hashes = [item.series_identity_sha256 for item in self.series_identities]
        if len(set(hashes)) != len(hashes):
            raise ValueError("series_identities must be unique")

    @property
    def config_sha256(self) -> str:
        identities = tuple(sorted(item.series_identity_sha256 for item in self.series_identities))
        return sha256_payload({
            "alpha_name": self.alpha_name,
            "alpha_version": self.alpha_version,
            "symbols": tuple(sorted(self.symbols)) if not identities else (),
            "series_identity_sha256": identities,
            "parameters": dict(self.parameters),
            "fail_fast": self.fail_fast,
        })


@dataclass(frozen=True)
class AlphaComputationPoint:
    timestamp_utc: datetime
    raw_score: Decimal | None
    warmup_complete: bool
    valid: bool
    source_observation_ids: tuple[UUID, ...]
    source_timestamps_utc: tuple[datetime, ...]
    provenance: Mapping[str, object] = field(default_factory=dict)
    status: AlphaEvaluationStatus | None = None
    reason_code: str | None = None
    reason_message: str | None = None

    def __post_init__(self) -> None:
        require_utc_datetime(self.timestamp_utc, field_name="alpha point timestamp")
        for timestamp in self.source_timestamps_utc:
            require_utc_datetime(timestamp, field_name="alpha source timestamp")
        status = self.status or (
            AlphaEvaluationStatus.EMITTED if self.valid else AlphaEvaluationStatus.WARMUP if not self.warmup_complete else AlphaEvaluationStatus.INVALID
        )
        object.__setattr__(self, "status", AlphaEvaluationStatus(status))
        if self.reason_code is None and not self.valid:
            object.__setattr__(self, "reason_code", str(self.provenance.get("reason", status.value)))
        if self.valid and (self.raw_score is None or not self.raw_score.is_finite() or not self.warmup_complete):
            raise ValueError("emitted alpha points require completed warmup and a finite Decimal score")
        if self.raw_score is not None and not self.raw_score.is_finite():
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
    series_identity: SeriesIdentity | None = None
    status: AlphaEvaluationStatus | None = None
    reason_code: str | None = None
    reason_message: str | None = None
    as_of_utc: datetime | None = None
    lookback_start_utc: datetime | None = None
    lookback_end_utc: datetime | None = None
    eligible_input_sha256: str | None = None
    formula_sha256: str | None = None
    implementation_code_sha256: str | None = None
    repository_commit_sha: str = "source-tree"

    def __post_init__(self) -> None:
        timestamp = require_utc_datetime(self.timestamp_utc, field_name="AlphaValue timestamp_utc")
        identity = self.series_identity or SeriesIdentity.legacy(self.symbol)
        if identity.canonical_symbol != self.symbol:
            raise ValueError("AlphaValue symbol must match its canonical series symbol")
        object.__setattr__(self, "series_identity", identity)
        as_of = timestamp if self.as_of_utc is None else require_utc_datetime(self.as_of_utc, field_name="AlphaValue as_of_utc")
        if timestamp != as_of:
            raise ValueError("AlphaValue timestamp_utc must represent as_of_utc")
        object.__setattr__(self, "as_of_utc", as_of)
        for name in ("lookback_start_utc", "lookback_end_utc"):
            value = getattr(self, name)
            if value is not None:
                require_utc_datetime(value, field_name=f"AlphaValue {name}")
        if self.lookback_start_utc and self.lookback_end_utc and self.lookback_start_utc > self.lookback_end_utc:
            raise ValueError("AlphaValue lookback bounds are reversed")
        if self.lookback_end_utc and self.lookback_end_utc > as_of:
            raise ValueError("AlphaValue lookback cannot extend after as_of_utc")
        code_hash = self.implementation_sha256
        formula_hash = self.formula_sha256 or sha256_payload({"alpha": self.alpha_name, "version": self.alpha_version})
        input_hash = self.eligible_input_sha256 or self.dataset_sha256
        for name, digest in (
            ("dataset_sha256", self.dataset_sha256),
            ("config_sha256", self.config_sha256),
            ("implementation_sha256", self.implementation_sha256),
            ("implementation_code_sha256", code_hash),
            ("formula_sha256", formula_hash),
            ("eligible_input_sha256", input_hash),
        ):
            if not _SHA256.fullmatch(digest):
                raise ValueError(f"{name} must be a lowercase SHA-256 digest")
        if code_hash != self.implementation_sha256:
            raise ValueError("legacy implementation_sha256 must equal implementation_code_sha256")
        object.__setattr__(self, "implementation_code_sha256", code_hash)
        object.__setattr__(self, "formula_sha256", formula_hash)
        object.__setattr__(self, "eligible_input_sha256", input_hash)
        status = self.status or (
            AlphaEvaluationStatus.EMITTED if self.valid else AlphaEvaluationStatus.WARMUP if not self.warmup_complete else AlphaEvaluationStatus.INVALID
        )
        object.__setattr__(self, "status", AlphaEvaluationStatus(status))
        if self.valid and (self.raw_score is None or not self.raw_score.is_finite() or not self.warmup_complete):
            raise ValueError("emitted AlphaValue requires completed warmup and a finite Decimal raw_score")
        if self.raw_score is not None and not self.raw_score.is_finite():
            raise ValueError("AlphaValue raw_score must be finite when present")
        if self.status is AlphaEvaluationStatus.EMITTED and not self.valid:
            raise ValueError("emitted AlphaValue must be valid")
        if not self.repository_commit_sha.strip():
            raise ValueError("repository_commit_sha must be non-empty")

    @property
    def record_sha256(self) -> str:
        """Stable economic record hash excluding run and collection provenance."""

        return sha256_payload({
            "alpha_id": self.alpha_id,
            "alpha_name": self.alpha_name,
            "alpha_version": self.alpha_version,
            "series_identity_sha256": self.series_identity.series_identity_sha256,
            "as_of_utc": self.as_of_utc,
            "raw_score": self.raw_score,
            "status": self.status.value,
            "reason_code": self.reason_code,
            "reason_message": self.reason_message,
            "lookback_start_utc": self.lookback_start_utc,
            "lookback_end_utc": self.lookback_end_utc,
            "eligible_input_sha256": self.eligible_input_sha256,
            "config_sha256": self.config_sha256,
            "formula_sha256": self.formula_sha256,
            "implementation_code_sha256": self.implementation_code_sha256,
            "repository_commit_sha": self.repository_commit_sha,
            "horizon": self.horizon,
        })

    @property
    def content_sha256(self) -> str:
        return self.record_sha256


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
    series_identity_sha256_set: tuple[str, ...] = ()
    formula_sha256: str | None = None
    implementation_code_sha256: str | None = None
    repository_commit_sha: str = "source-tree"

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
        code_hash = self.implementation_sha256
        formula_hash = self.formula_sha256 or sha256_payload({"alpha": self.alpha_name, "version": self.alpha_version})
        for digest in (self.input_data_sha256, self.config_sha256, self.implementation_sha256, code_hash, formula_hash, *self.series_identity_sha256_set):
            if not _SHA256.fullmatch(digest):
                raise ValueError("AlphaRun hashes must be lowercase SHA-256 digests")
        object.__setattr__(self, "implementation_code_sha256", code_hash)
        object.__setattr__(self, "formula_sha256", formula_hash)


@dataclass(frozen=True)
class AlphaFailure:
    symbol: str | None
    stage: str
    error_type: str
    message: str
    series_identity_sha256: str | None = None


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
    "AlphaEvaluationStatus",
    "AlphaFailure",
    "AlphaRun",
    "AlphaRunStatus",
    "AlphaStatus",
    "AlphaValue",
]
