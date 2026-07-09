"""Deterministic, persistence-free validation report construction."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from datetime import datetime
from uuid import NAMESPACE_URL, UUID, uuid5

from secure_eval_wrapper.data_collection.hashing import sha256_payload
from secure_eval_wrapper.data_collection.models import MarketDataType
from secure_eval_wrapper.data_collection.time_utils import require_utc_datetime
from secure_eval_wrapper.data_validation.models import (
    ValidationCheckStatus,
    ValidationReport,
    ValidationResult,
    ValidationStatus,
)


_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")


def _stable_result_payload(result: ValidationResult) -> Mapping[str, object]:
    return {
        "check_id": result.check_id,
        "status": result.status,
        "message": result.message,
        "symbol": result.symbol,
        "timeframe": result.timeframe,
        "window_start_utc": result.window_start_utc,
        "window_end_utc": result.window_end_utc,
        "affected_observation_ids": tuple(
            sorted(result.affected_observation_ids, key=str)
        ),
        "details": dict(result.details),
    }


def build_validation_report(
    *,
    validation_run_id: UUID,
    dataset_ref: str,
    provider_names: Sequence[str],
    data_types: Sequence[MarketDataType],
    symbols: Sequence[str],
    timeframes: Sequence[str],
    window_start_utc: datetime | None,
    window_end_utc: datetime | None,
    results: Sequence[ValidationResult],
    record_observation_ids: Sequence[Sequence[UUID]],
    source_hashes: Sequence[str],
    tolerance_config: Mapping[str, object],
    created_at_utc: datetime,
) -> ValidationReport:
    """Build a report and hash only its stable validation content.

    Creation timestamps and generated report/result identifiers are intentionally excluded from
    ``report_sha256``. Rebuilding the same logical report therefore produces the same digest even
    when it is created at a different wall-clock time.
    """

    if not isinstance(dataset_ref, str) or not dataset_ref.strip():
        raise ValueError("dataset_ref must be a non-empty string")
    created_at_utc = require_utc_datetime(
        created_at_utc,
        field_name="validation report created_at_utc",
    )
    if window_start_utc is not None:
        window_start_utc = require_utc_datetime(
            window_start_utc,
            field_name="validation report window_start_utc",
        )
    if window_end_utc is not None:
        window_end_utc = require_utc_datetime(
            window_end_utc,
            field_name="validation report window_end_utc",
        )
    if (
        window_start_utc is not None
        and window_end_utc is not None
        and window_end_utc < window_start_utc
    ):
        raise ValueError("validation report window end precedes its start")

    normalized_results = tuple(results)
    for result in normalized_results:
        if result.validation_run_id != validation_run_id:
            raise ValueError("validation result belongs to a different validation run")

    normalized_source_hashes = tuple(sorted(set(source_hashes)))
    if any(not _SHA256_PATTERN.fullmatch(item) for item in normalized_source_hashes):
        raise ValueError("source_hashes must contain lowercase SHA-256 digests")

    failed_results = tuple(
        result
        for result in normalized_results
        if result.status is ValidationCheckStatus.FAILED
    )
    failed_observation_ids = {
        observation_id
        for result in failed_results
        for observation_id in result.affected_observation_ids
    }
    dataset_wide_failure = any(
        not result.affected_observation_ids for result in failed_results
    )
    record_id_groups = tuple(tuple(group) for group in record_observation_ids)
    if dataset_wide_failure:
        rejected_count = len(record_id_groups)
    else:
        rejected_count = sum(
            1 for group in record_id_groups if failed_observation_ids.intersection(group)
        )
    accepted_count = len(record_id_groups) - rejected_count
    warning_count = sum(
        result.status is ValidationCheckStatus.WARNING for result in normalized_results
    )
    if failed_results:
        status = ValidationStatus.REJECTED
    elif warning_count:
        status = ValidationStatus.ACCEPTED_WITH_WARNINGS
    else:
        status = ValidationStatus.ACCEPTED

    normalized_provider_names = tuple(sorted(set(provider_names)))
    normalized_data_types = tuple(sorted(set(data_types), key=lambda item: item.value))
    normalized_symbols = tuple(sorted(set(symbols)))
    normalized_timeframes = tuple(sorted(set(timeframes)))
    tolerance_config_sha256 = sha256_payload(dict(tolerance_config))
    stable_payload = {
        "validation_run_id": validation_run_id,
        "dataset_ref": dataset_ref.strip(),
        "provider_names": normalized_provider_names,
        "data_types": normalized_data_types,
        "symbols": normalized_symbols,
        "timeframes": normalized_timeframes,
        "window_start_utc": window_start_utc,
        "window_end_utc": window_end_utc,
        "results": tuple(_stable_result_payload(result) for result in normalized_results),
        "accepted_count": accepted_count,
        "rejected_count": rejected_count,
        "warning_count": warning_count,
        "status": status,
        "tolerance_config_sha256": tolerance_config_sha256,
        "source_hashes": normalized_source_hashes,
    }
    report_sha256 = sha256_payload(stable_payload)
    validation_report_id = uuid5(
        NAMESPACE_URL,
        f"validation-report:{validation_run_id}:{dataset_ref.strip()}",
    )
    return ValidationReport(
        validation_report_id=validation_report_id,
        validation_run_id=validation_run_id,
        dataset_ref=dataset_ref.strip(),
        provider_names=normalized_provider_names,
        data_types=normalized_data_types,
        symbols=normalized_symbols,
        timeframes=normalized_timeframes,
        window_start_utc=window_start_utc,
        window_end_utc=window_end_utc,
        results=normalized_results,
        accepted_count=accepted_count,
        rejected_count=rejected_count,
        warning_count=warning_count,
        status=status,
        tolerance_config_sha256=tolerance_config_sha256,
        source_hashes=normalized_source_hashes,
        report_sha256=report_sha256,
        created_at_utc=created_at_utc,
    )
