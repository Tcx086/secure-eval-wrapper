"""Typed persistence orchestration for trades, funding, and instruments."""

from __future__ import annotations

from collections.abc import Sequence
from contextlib import nullcontext
from dataclasses import dataclass
from uuid import UUID

from secure_eval_wrapper.data_collection.models import (
    FundingRate,
    InstrumentMetadata,
    NormalizedTrade,
    RawObservation,
)
from secure_eval_wrapper.data_validation.gating import (
    accepted_funding_rates,
    accepted_instruments,
    accepted_trades,
)
from secure_eval_wrapper.data_validation.models import ValidationReport, ValidationStatus
from secure_eval_wrapper.storage.postgres.mappers import (
    funding_rate_to_row,
    instrument_metadata_to_row,
    normalized_trade_to_row,
    quarantine_decision_rows_for_records,
    raw_observation_to_row,
    validation_report_to_row,
    validation_result_to_row,
)


@dataclass(frozen=True)
class TradePersistenceSummary:
    validation_report_id: UUID
    raw_observation_ids: tuple[UUID, ...]
    check_ids: tuple[UUID, ...]
    accepted_trade_ids: tuple[UUID, ...]
    quarantine_ids: tuple[UUID, ...]


@dataclass(frozen=True)
class FundingPersistenceSummary:
    validation_report_id: UUID
    raw_observation_ids: tuple[UUID, ...]
    check_ids: tuple[UUID, ...]
    accepted_funding_rate_ids: tuple[UUID, ...]
    quarantine_ids: tuple[UUID, ...]


@dataclass(frozen=True)
class InstrumentPersistenceSummary:
    validation_report_id: UUID
    raw_observation_ids: tuple[UUID, ...]
    check_ids: tuple[UUID, ...]
    accepted_instrument_ids: tuple[UUID, ...]
    quarantine_ids: tuple[UUID, ...]


def _status(report: ValidationReport) -> ValidationStatus:
    return (
        ValidationStatus.ACCEPTED_WITH_WARNINGS
        if report.status is ValidationStatus.ACCEPTED_WITH_WARNINGS
        else ValidationStatus.ACCEPTED
    )


def _transaction(repository, manage_transaction: bool):
    return (
        repository.transaction()
        if manage_transaction and hasattr(repository, "transaction")
        else nullcontext()
    )


def _common(repository, observations, report):
    raw_ids = tuple(
        repository.record_raw_source_observation(raw_observation_to_row(item))
        for item in observations
    )
    report_id = repository.record_validation_report(validation_report_to_row(report))
    check_ids = tuple(
        repository.record_data_quality_check(validation_result_to_row(item))
        for item in report.results
    )
    return raw_ids, report_id, check_ids


def persist_trade_validation_flow(
    observations: Sequence[RawObservation],
    trades: Sequence[NormalizedTrade],
    report: ValidationReport,
    *,
    repository,
    manage_transaction: bool = True,
) -> TradePersistenceSummary:
    accepted = accepted_trades(trades, report)
    with _transaction(repository, manage_transaction):
        raw_ids, report_id, check_ids = _common(repository, observations, report)
        accepted_ids = tuple(
            repository.record_validated_trade(
                normalized_trade_to_row(
                    item,
                    validation_report_id=report_id,
                    validation_status=_status(report),
                )
            )
            for item in accepted
        )
        quarantine_ids = tuple(
            repository.record_quarantine_decision(row)
            for row in quarantine_decision_rows_for_records(
                report,
                observations,
                trades,
                accepted,
                validation_report_id=report_id,
            )
        )
    return TradePersistenceSummary(
        validation_report_id=report_id,
        raw_observation_ids=raw_ids,
        check_ids=check_ids,
        accepted_trade_ids=accepted_ids,
        quarantine_ids=quarantine_ids,
    )


def persist_funding_validation_flow(
    observations: Sequence[RawObservation],
    funding_rates: Sequence[FundingRate],
    report: ValidationReport,
    *,
    repository,
    manage_transaction: bool = True,
) -> FundingPersistenceSummary:
    accepted = accepted_funding_rates(funding_rates, report)
    with _transaction(repository, manage_transaction):
        raw_ids, report_id, check_ids = _common(repository, observations, report)
        accepted_ids = tuple(
            repository.record_funding_rate(
                funding_rate_to_row(
                    item,
                    validation_report_id=report_id,
                    validation_status=_status(report),
                )
            )
            for item in accepted
        )
        quarantine_ids = tuple(
            repository.record_quarantine_decision(row)
            for row in quarantine_decision_rows_for_records(
                report,
                observations,
                funding_rates,
                accepted,
                validation_report_id=report_id,
            )
        )
    return FundingPersistenceSummary(
        validation_report_id=report_id,
        raw_observation_ids=raw_ids,
        check_ids=check_ids,
        accepted_funding_rate_ids=accepted_ids,
        quarantine_ids=quarantine_ids,
    )


def persist_instrument_validation_flow(
    observations: Sequence[RawObservation],
    instruments: Sequence[InstrumentMetadata],
    report: ValidationReport,
    *,
    repository,
    manage_transaction: bool = True,
) -> InstrumentPersistenceSummary:
    accepted = accepted_instruments(instruments, report)
    with _transaction(repository, manage_transaction):
        raw_ids, report_id, check_ids = _common(repository, observations, report)
        accepted_ids = tuple(
            repository.upsert_instrument(
                instrument_metadata_to_row(
                    item,
                    validation_report_id=report_id,
                    validation_status=_status(report),
                )
            )
            for item in accepted
        )
        quarantine_ids = tuple(
            repository.record_quarantine_decision(row)
            for row in quarantine_decision_rows_for_records(
                report,
                observations,
                instruments,
                accepted,
                validation_report_id=report_id,
            )
        )
    return InstrumentPersistenceSummary(
        validation_report_id=report_id,
        raw_observation_ids=raw_ids,
        check_ids=check_ids,
        accepted_instrument_ids=accepted_ids,
        quarantine_ids=quarantine_ids,
    )


__all__ = [
    "FundingPersistenceSummary",
    "InstrumentPersistenceSummary",
    "TradePersistenceSummary",
    "persist_funding_validation_flow",
    "persist_instrument_validation_flow",
    "persist_trade_validation_flow",
]
