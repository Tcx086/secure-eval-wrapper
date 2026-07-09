"""Domain-to-row mappings for the offline Phase 2D persistence path.

The mapping layer is deliberately free of database imports and I/O.  It keeps domain objects
usable by in-memory tests while the PostgreSQL repositories serialize JSONB values at the DB-API
boundary.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime
from enum import Enum
from uuid import NAMESPACE_URL, UUID, uuid5

from secure_eval_wrapper.data_collection.models import NormalizedBar, RawObservation
from secure_eval_wrapper.data_validation.models import (
    QuarantineReason,
    ValidationCheckStatus,
    ValidationReport,
    ValidationResult,
    ValidationStatus,
)
from secure_eval_wrapper.data_validation.quarantine import map_quarantine_reasons


def raw_observation_to_row(observation: RawObservation) -> dict[str, object]:
    """Map one raw observation to ``market_data.raw_source_observations`` columns."""

    if not isinstance(observation, RawObservation):
        raise TypeError("observation must be a RawObservation")
    return {
        "observation_id": observation.observation_id,
        "source_provider": observation.provider_name,
        "source_exchange": observation.exchange_name,
        "source_endpoint": observation.source_endpoint,
        "symbol_raw": observation.raw_symbol,
        "symbol_normalized": observation.normalized_symbol,
        "timeframe": observation.timeframe,
        "observed_at_utc": observation.observed_at_utc,
        "ingested_at_utc": observation.ingested_at_utc,
        "payload_jsonb": observation.payload,
        "source_sha256": observation.source_sha256,
        "collection_run_id": observation.collection_run_id,
    }


def normalized_bar_to_row(
    bar: NormalizedBar,
    *,
    validation_report_id: UUID,
    validation_status: ValidationStatus = ValidationStatus.ACCEPTED,
) -> dict[str, object]:
    """Map an accepted normalized bar to ``market_data.validated_bars`` columns."""

    if not isinstance(bar, NormalizedBar):
        raise TypeError("bar must be a NormalizedBar")
    if validation_status not in (
        ValidationStatus.ACCEPTED,
        ValidationStatus.ACCEPTED_WITH_WARNINGS,
    ):
        raise ValueError("validated bars require an accepted validation status")
    provenance = dict(bar.provenance)
    provenance.setdefault("bar_close_time_utc", bar.bar_close_time_utc)
    provenance.setdefault("is_final", bar.is_final)
    return {
        "bar_id": bar.bar_id,
        "symbol": bar.symbol,
        "exchange": bar.exchange,
        "timeframe": bar.timeframe,
        "bar_open_time_utc": bar.bar_open_time_utc,
        "open": bar.open,
        "high": bar.high,
        "low": bar.low,
        "close": bar.close,
        "volume": bar.volume,
        "validation_status": validation_status.value,
        "validation_report_id": validation_report_id,
        "source_observation_ids": list(bar.source_observation_ids),
        "provenance_jsonb": provenance,
    }


def _result_severity(result: ValidationResult) -> str:
    if result.status is ValidationCheckStatus.FAILED:
        return "error"
    if result.status is ValidationCheckStatus.WARNING:
        return "warning"
    return "info"


def validation_result_to_row(result: ValidationResult) -> dict[str, object]:
    """Map a validation result to one ``data_quality.data_quality_checks`` row.

    ``result_id`` is the storage primary key.  The declared ``check_id`` is retained in JSONB so
    multiple results for the same check definition can coexist across validation runs.
    """

    if not isinstance(result, ValidationResult):
        raise TypeError("result must be a ValidationResult")
    details = dict(result.details)
    details.update(
        {
            "declared_check_id": result.check_id,
            "message": result.message,
            "affected_observation_ids": list(result.affected_observation_ids),
        }
    )
    return {
        "check_id": result.result_id,
        "validation_run_id": result.validation_run_id,
        "check_type": str(result.details.get("check_type", result.check_id)),
        "severity": _result_severity(result),
        "symbol": result.symbol,
        "timeframe": result.timeframe,
        "window_start_utc": result.window_start_utc,
        "window_end_utc": result.window_end_utc,
        "status": result.status.value,
        "details_jsonb": details,
        "created_at_utc": result.created_at_utc,
    }


def validation_report_to_row(report: ValidationReport) -> dict[str, object]:
    """Map a validation report to ``data_quality.validation_reports`` columns."""

    if not isinstance(report, ValidationReport):
        raise TypeError("report must be a ValidationReport")
    report_status = report.status
    if report_status is ValidationStatus.QUARANTINED:
        report_status = ValidationStatus.REJECTED
    if report_status is ValidationStatus.FAILED:
        report_status = ValidationStatus.REJECTED
    report_jsonb = {
        "provider_names": list(report.provider_names),
        "data_types": [item.value for item in report.data_types],
        "symbols": list(report.symbols),
        "timeframes": list(report.timeframes),
        "window_start_utc": report.window_start_utc,
        "window_end_utc": report.window_end_utc,
        "tolerance_config_sha256": report.tolerance_config_sha256,
        "source_hashes": list(report.source_hashes),
        "results": [validation_result_to_row(item) for item in report.results],
    }
    return {
        "validation_report_id": report.validation_report_id,
        "validation_run_id": report.validation_run_id,
        "dataset_ref": report.dataset_ref,
        "accepted_count": report.accepted_count,
        "rejected_count": report.rejected_count,
        "warning_count": report.warning_count,
        "status": report_status.value,
        "report_sha256": report.report_sha256,
        "report_jsonb": report_jsonb,
        "created_at_utc": report.created_at_utc,
    }


def quarantine_decision_rows(
    report: ValidationReport,
    observations: Sequence[RawObservation] = (),
    bars: Sequence[NormalizedBar] = (),
) -> tuple[dict[str, object], ...]:
    """Build deterministic quarantine rows for failed source observations.

    Only provenance and quality metadata are copied into ``details_jsonb``; raw payloads are never
    copied into quarantine decisions.
    """

    if not isinstance(report, ValidationReport):
        raise TypeError("report must be a ValidationReport")
    observation_by_id = {item.observation_id: item for item in observations}
    bar_by_observation_id: dict[UUID, NormalizedBar] = {}
    for bar in bars:
        for observation_id in bar.source_observation_ids:
            bar_by_observation_id.setdefault(observation_id, bar)
    reasons = map_quarantine_reasons(report)
    result_details: dict[UUID, dict[str, object]] = {}
    for result in report.results:
        if result.status is not ValidationCheckStatus.FAILED:
            continue
        for observation_id in result.affected_observation_ids:
            result_details.setdefault(observation_id, dict(result.details))

    rows: list[dict[str, object]] = []
    for observation_id, reason in sorted(reasons.items(), key=lambda item: str(item[0])):
        observation = observation_by_id.get(observation_id)
        bar = bar_by_observation_id.get(observation_id)
        symbol = bar.symbol if bar is not None else (observation.normalized_symbol if observation else None)
        exchange = bar.exchange if bar is not None else (observation.exchange_name if observation else None)
        timeframe = bar.timeframe if bar is not None else (observation.timeframe if observation else None)
        source_sha256 = observation.source_sha256 if observation is not None else None
        details = dict(result_details.get(observation_id, {}))
        details["source_observation_id"] = observation_id
        rows.append(
            {
                "quarantine_id": uuid5(
                    NAMESPACE_URL,
                    f"quarantine-decision:{report.validation_report_id}:{observation_id}:{reason.value}",
                ),
                "validation_report_id": report.validation_report_id,
                "validation_run_id": report.validation_run_id,
                "observation_id": observation_id,
                "quarantine_reason": reason.value,
                "symbol": symbol,
                "exchange": exchange,
                "timeframe": timeframe,
                "source_sha256": source_sha256,
                "details_jsonb": details,
                "created_at_utc": report.created_at_utc,
            }
        )
    return tuple(rows)


__all__ = [
    "normalized_bar_to_row",
    "quarantine_decision_rows",
    "raw_observation_to_row",
    "validation_report_to_row",
    "validation_result_to_row",
]
