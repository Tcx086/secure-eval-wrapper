"""Domain-to-row mappings for reconciliation persistence."""

from __future__ import annotations

from uuid import UUID

from secure_eval_wrapper.data_validation.models import (
    ReconciliationResult,
    ValidationCheckStatus,
    ValidationResult,
)


def _severity(result: ValidationResult) -> str:
    if result.status is ValidationCheckStatus.FAILED:
        return "error"
    if result.status is ValidationCheckStatus.WARNING:
        return "warning"
    return "info"


def reconciliation_result_to_row(result: ReconciliationResult) -> dict[str, object]:
    """Map a reconciliation summary to its PostgreSQL columns."""

    if not isinstance(result, ReconciliationResult):
        raise TypeError("result must be a ReconciliationResult")
    return {
        "reconciliation_id": result.reconciliation_id,
        "validation_run_id": result.validation_run_id,
        "data_type": result.data_type.value,
        "symbol": result.symbol,
        "timeframe": result.timeframe,
        "provider_names": list(result.provider_names),
        "window_start_utc": result.window_start_utc,
        "window_end_utc": result.window_end_utc,
        "status": result.status.value,
        "config_sha256": result.config_sha256,
        "dataset_sha256": result.dataset_sha256,
        "result_sha256": result.result_sha256,
        "metrics_jsonb": dict(result.metrics),
        "created_at_utc": result.created_at_utc,
    }


def reconciliation_check_result_to_row(
    result: ValidationResult,
    *,
    reconciliation_id: UUID,
) -> dict[str, object]:
    """Map one reconciliation finding to its auditable child row."""

    if not isinstance(result, ValidationResult):
        raise TypeError("result must be a ValidationResult")
    if not isinstance(reconciliation_id, UUID):
        raise TypeError("reconciliation_id must be a UUID")
    details = dict(result.details)
    details["message"] = result.message
    return {
        "result_id": result.result_id,
        "reconciliation_id": reconciliation_id,
        "validation_run_id": result.validation_run_id,
        "check_id": result.check_id,
        "check_type": str(result.details.get("check_type", result.check_id)),
        "status": result.status.value,
        "severity": _severity(result),
        "affected_observation_ids": list(result.affected_observation_ids),
        "details_jsonb": details,
        "created_at_utc": result.created_at_utc,
    }


__all__ = [
    "reconciliation_check_result_to_row",
    "reconciliation_result_to_row",
]
