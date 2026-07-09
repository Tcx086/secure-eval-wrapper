"""Abstract boundaries for validation, reconciliation, and dataset promotion.

Phase 2A deliberately provides no validation algorithms and no persistence implementation.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Mapping, Sequence
from uuid import UUID

from secure_eval_wrapper.data_collection.models import (
    FundingRate,
    InstrumentMetadata,
    NormalizedBar,
    NormalizedTrade,
    RawObservation,
)
from secure_eval_wrapper.data_validation.models import (
    QuarantineReason,
    ReconciliationResult,
    ValidationCheck,
    ValidationReport,
)


NormalizedRecord = NormalizedBar | NormalizedTrade | FundingRate | InstrumentMetadata
ValidationInput = RawObservation | NormalizedRecord


class DataValidator(ABC):
    """Contract for future single-source validation implementations."""

    @abstractmethod
    def validate(
        self,
        *,
        validation_run_id: UUID,
        dataset_ref: str,
        records: Sequence[ValidationInput],
        checks: Sequence[ValidationCheck],
    ) -> ValidationReport:
        """Evaluate declared checks and return a dataset-level validation report."""


class CrossSourceReconciler(ABC):
    """Contract for future cross-provider comparison implementations."""

    @abstractmethod
    def reconcile(
        self,
        *,
        validation_run_id: UUID,
        datasets_by_provider: Mapping[str, Sequence[NormalizedRecord]],
        checks: Sequence[ValidationCheck],
    ) -> ReconciliationResult:
        """Compare normalized records representing the same logical data window."""


class DatasetPromoter(ABC):
    """Gate accepted records into PostgreSQL repositories or quarantine rejected records."""

    @abstractmethod
    def promote(
        self,
        *,
        records: Sequence[NormalizedRecord],
        report: ValidationReport,
    ) -> Sequence[UUID]:
        """Persist accepted records through PostgreSQL repository abstractions."""

    @abstractmethod
    def quarantine(
        self,
        *,
        records: Sequence[ValidationInput],
        report: ValidationReport,
        reasons_by_observation_id: Mapping[UUID, QuarantineReason],
    ) -> Sequence[UUID]:
        """Persist rejection metadata without promoting records to validated datasets."""
