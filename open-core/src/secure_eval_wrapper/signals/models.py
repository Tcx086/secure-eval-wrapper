"""Standardized research-signal contracts without execution semantics."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from enum import Enum
from typing import Mapping
from uuid import UUID

from secure_eval_wrapper.alpha.models import AlphaValue
from secure_eval_wrapper.data_collection.hashing import sha256_payload
from secure_eval_wrapper.data_collection.time_utils import require_utc_datetime


_SHA256 = re.compile(r"^[0-9a-f]{64}$")


class SignalDirection(str, Enum):
    LONG = "long"
    SHORT = "short"
    FLAT = "flat"


class SignalRunStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    PARTIAL = "partial"
    FAILED = "failed"


class RankOrder(str, Enum):
    ASCENDING = "ascending"
    DESCENDING = "descending"


class RankMethod(str, Enum):
    DENSE = "dense"
    ORDINAL = "ordinal"


@dataclass(frozen=True)
class RankingConfig:
    order: RankOrder = RankOrder.DESCENDING
    method: RankMethod = RankMethod.DENSE

    def __post_init__(self) -> None:
        object.__setattr__(self, "order", RankOrder(self.order))
        object.__setattr__(self, "method", RankMethod(self.method))

    def as_dict(self) -> dict[str, object]:
        return {"order": self.order.value, "method": self.method.value}


@dataclass(frozen=True)
class RankedAlphaValue:
    alpha_value: AlphaValue
    rank: int
    percentile: Decimal
    normalized_score: Decimal

    def __post_init__(self) -> None:
        if self.rank < 1:
            raise ValueError("rank must be positive")
        if not Decimal(0) <= self.percentile <= Decimal(1):
            raise ValueError("percentile must be in [0, 1]")
        if not Decimal(-1) <= self.normalized_score <= Decimal(1):
            raise ValueError("normalized_score must be in [-1, 1]")


@dataclass(frozen=True)
class ThresholdedAlphaValue:
    ranked: RankedAlphaValue
    direction: SignalDirection
    threshold_config_sha256: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "direction", SignalDirection(self.direction))
        if not _SHA256.fullmatch(self.threshold_config_sha256):
            raise ValueError("threshold_config_sha256 must be a SHA-256 digest")


@dataclass(frozen=True)
class SignalContribution:
    alpha_value_id: UUID
    alpha_id: UUID
    alpha_name: str
    alpha_version: str
    direction: SignalDirection
    raw_score: Decimal
    normalized_score: Decimal
    configured_weight: Decimal
    effective_weight: Decimal
    signed_contribution: Decimal


@dataclass(frozen=True)
class CombinationOutcome:
    direction: SignalDirection
    raw_score: Decimal
    normalized_score: Decimal
    contributions: tuple[SignalContribution, ...]
    contributor_count: int
    expected_contributor_count: int
    coverage_ratio: Decimal
    agreement_ratio: Decimal
    conflict: bool
    insufficient_coverage: bool
    skipped: bool = False


@dataclass(frozen=True)
class StandardizedSignal:
    signal_id: UUID
    signal_run_id: UUID
    alpha_ids_versions: tuple[str, ...]
    alpha_run_ids: tuple[UUID, ...]
    symbol: str
    timestamp_utc: datetime
    direction: SignalDirection
    raw_score: Decimal
    normalized_score: Decimal
    rank: int | None
    percentile: Decimal | None
    confidence: Decimal
    horizon: str
    source_alpha_value_ids: tuple[UUID, ...]
    config_sha256: str
    data_sha256: str
    code_sha256: str
    provenance: Mapping[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        require_utc_datetime(self.timestamp_utc, field_name="signal timestamp_utc")
        object.__setattr__(self, "direction", SignalDirection(self.direction))
        if not self.raw_score.is_finite() or not self.normalized_score.is_finite():
            raise ValueError("signal scores must be finite Decimal values")
        if not Decimal(-1) <= self.normalized_score <= Decimal(1):
            raise ValueError("signal normalized_score must be in [-1, 1]")
        if self.percentile is not None and not Decimal(0) <= self.percentile <= Decimal(1):
            raise ValueError("signal percentile must be in [0, 1]")
        if not Decimal(0) <= self.confidence <= Decimal(1):
            raise ValueError("signal confidence must be in [0, 1]")
        for digest_name in ("config_sha256", "data_sha256", "code_sha256"):
            if not _SHA256.fullmatch(getattr(self, digest_name)):
                raise ValueError(f"{digest_name} must be a SHA-256 digest")

    @property
    def content_sha256(self) -> str:
        return sha256_payload(
            {
                "signal_id": self.signal_id,
                "signal_run_id": self.signal_run_id,
                "alpha_ids_versions": self.alpha_ids_versions,
                "alpha_run_ids": self.alpha_run_ids,
                "symbol": self.symbol,
                "timestamp_utc": self.timestamp_utc,
                "direction": self.direction,
                "raw_score": self.raw_score,
                "normalized_score": self.normalized_score,
                "rank": self.rank,
                "percentile": self.percentile,
                "confidence": self.confidence,
                "horizon": self.horizon,
                "source_alpha_value_ids": self.source_alpha_value_ids,
                "config_sha256": self.config_sha256,
                "data_sha256": self.data_sha256,
                "code_sha256": self.code_sha256,
                "provenance": dict(self.provenance),
            }
        )


@dataclass(frozen=True)
class SignalRun:
    signal_run_id: UUID
    alpha_run_ids: tuple[UUID, ...]
    symbol_universe: tuple[str, ...]
    window_start_utc: datetime
    window_end_utc: datetime
    ranking_config: Mapping[str, object]
    threshold_config: Mapping[str, object]
    combination_config: Mapping[str, object]
    config_sha256: str
    code_sha256: str
    data_sha256: str
    status: SignalRunStatus
    output_count: int
    long_count: int
    short_count: int
    flat_count: int
    skipped_count: int
    failure_count: int
    started_at_utc: datetime
    completed_at_utc: datetime | None
    metadata: Mapping[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        start = require_utc_datetime(self.window_start_utc, field_name="SignalRun window start")
        end = require_utc_datetime(self.window_end_utc, field_name="SignalRun window end")
        if start >= end:
            raise ValueError("SignalRun window must be half-open")
        require_utc_datetime(self.started_at_utc, field_name="SignalRun started_at_utc")
        if self.completed_at_utc is not None:
            require_utc_datetime(self.completed_at_utc, field_name="SignalRun completed_at_utc")
        object.__setattr__(self, "status", SignalRunStatus(self.status))
        for digest_name in ("config_sha256", "code_sha256", "data_sha256"):
            if not _SHA256.fullmatch(getattr(self, digest_name)):
                raise ValueError(f"{digest_name} must be a SHA-256 digest")


@dataclass(frozen=True)
class SignalFailure:
    stage: str
    error_type: str
    message: str
    alpha_value_id: UUID | None = None


@dataclass(frozen=True)
class SignalPipelineResult:
    run: SignalRun
    signals: tuple[StandardizedSignal, ...]
    failures: tuple[SignalFailure, ...] = ()


class SignalPipelineError(RuntimeError):
    def __init__(self, failure: SignalFailure) -> None:
        super().__init__(f"signal {failure.stage} failed: {failure.message}")
        self.failure = failure


__all__ = [
    "CombinationOutcome",
    "RankMethod",
    "RankOrder",
    "RankedAlphaValue",
    "RankingConfig",
    "SignalContribution",
    "SignalDirection",
    "SignalFailure",
    "SignalPipelineError",
    "SignalPipelineResult",
    "SignalRun",
    "SignalRunStatus",
    "StandardizedSignal",
    "ThresholdedAlphaValue",
]
