"""Offline-only accepted/rejected persistence orchestration for Phase 2D."""

from __future__ import annotations

from collections.abc import Sequence
from contextlib import nullcontext
from dataclasses import dataclass
from uuid import UUID

from secure_eval_wrapper.data_collection.models import NormalizedBar, RawObservation
from secure_eval_wrapper.data_validation.gating import accepted_ohlcv_bars
from secure_eval_wrapper.data_validation.models import ValidationReport, ValidationStatus
from secure_eval_wrapper.storage.postgres.mappers import (
    normalized_bar_to_row,
    quarantine_decision_rows,
    raw_observation_to_row,
    validation_report_to_row,
    validation_result_to_row,
)
from secure_eval_wrapper.storage.repositories.interfaces import (
    DataQualityRepository,
    MarketDataRepository,
    QuarantineRepository,
)


@dataclass(frozen=True)
class OfflinePersistenceSummary:
    """Identifiers written by one offline validation persistence run."""

    validation_report_id: UUID
    raw_observation_ids: tuple[UUID, ...]
    data_quality_check_ids: tuple[UUID, ...]
    accepted_bar_ids: tuple[UUID, ...]
    quarantine_decision_ids: tuple[UUID, ...]


def persist_offline_ohlcv_validation_flow(
    observations: Sequence[RawObservation],
    bars: Sequence[NormalizedBar],
    report: ValidationReport,
    *,
    repository: object | None = None,
    market_data_repository: MarketDataRepository | None = None,
    data_quality_repository: DataQualityRepository | None = None,
    quarantine_repository: QuarantineRepository | None = None,
    manage_transaction: bool = True,
) -> OfflinePersistenceSummary:
    """Persist one already-validated offline OHLCV flow.

    The function performs no validation, network access, exchange-client work, or trading logic.
    It records raw observations first, then the report and check results, promotes bars whose source
    observations were not rejected, and records deterministic quarantine decisions for failed
    observations.  A PostgreSQL repository's ``transaction()`` context makes the sequence atomic.
    """

    if not isinstance(report, ValidationReport):
        raise TypeError("report must be a ValidationReport")
    if repository is not None:
        market_data_repository = repository  # type: ignore[assignment]
        data_quality_repository = repository  # type: ignore[assignment]
        quarantine_repository = repository  # type: ignore[assignment]
    if market_data_repository is None or data_quality_repository is None:
        raise TypeError(
            "provide repository or both market_data_repository and data_quality_repository"
        )
    if quarantine_repository is None:
        if hasattr(data_quality_repository, "record_quarantine_decision"):
            quarantine_repository = data_quality_repository  # type: ignore[assignment]
        elif hasattr(market_data_repository, "record_quarantine_decision"):
            quarantine_repository = market_data_repository  # type: ignore[assignment]
        else:
            raise TypeError("a quarantine_repository is required for failed observations")

    accepted_status = (
        ValidationStatus.ACCEPTED_WITH_WARNINGS
        if report.status is ValidationStatus.ACCEPTED_WITH_WARNINGS
        else ValidationStatus.ACCEPTED
    )
    accepted_bars = accepted_ohlcv_bars(bars, report)

    transaction_owner = repository or market_data_repository
    transaction = (
        transaction_owner.transaction()
        if manage_transaction and hasattr(transaction_owner, "transaction")
        else nullcontext()
    )
    with transaction:
        raw_ids = tuple(
            market_data_repository.record_raw_source_observation(raw_observation_to_row(item))
            for item in observations
        )
        stored_validation_report_id = data_quality_repository.record_validation_report(
            validation_report_to_row(report)
        )
        quarantine_rows = quarantine_decision_rows(
            report,
            observations,
            bars,
            validation_report_id=stored_validation_report_id,
        )
        check_ids = tuple(
            data_quality_repository.record_data_quality_check(validation_result_to_row(result))
            for result in report.results
        )
        bar_ids = tuple(
            market_data_repository.record_validated_bar(
                normalized_bar_to_row(
                    bar,
                    validation_report_id=stored_validation_report_id,
                    validation_status=accepted_status,
                )
            )
            for bar in accepted_bars
        )
        quarantine_ids = tuple(
            quarantine_repository.record_quarantine_decision(row)
            for row in quarantine_rows
        )

    return OfflinePersistenceSummary(
        validation_report_id=stored_validation_report_id,
        raw_observation_ids=raw_ids,
        data_quality_check_ids=check_ids,
        accepted_bar_ids=bar_ids,
        quarantine_decision_ids=quarantine_ids,
    )


__all__ = ["OfflinePersistenceSummary", "persist_offline_ohlcv_validation_flow"]
