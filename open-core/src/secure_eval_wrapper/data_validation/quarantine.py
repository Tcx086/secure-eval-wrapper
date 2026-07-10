"""In-memory quarantine reason mapping for failed validation records."""

from __future__ import annotations

from collections.abc import Sequence
from uuid import UUID

from secure_eval_wrapper.data_validation.models import (
    QuarantineReason,
    ValidationCheckStatus,
    ValidationReport,
)


def map_quarantine_reasons(
    report: ValidationReport,
    *,
    dataset_observation_ids: Sequence[UUID] = (),
) -> dict[UUID, QuarantineReason]:
    """Map each failed source observation to one stable quarantine reason.

    Results are processed in report order, so when an observation fails multiple checks the first
    failed check deterministically takes precedence. The function creates no quarantine records
    and performs no persistence. A dataset-wide failed result is applied to the supplied
    dataset observation IDs.
    """

    reasons: dict[UUID, QuarantineReason] = {}
    for result in report.results:
        if result.status is not ValidationCheckStatus.FAILED:
            continue
        raw_reason = result.details.get("quarantine_reason")
        if not isinstance(raw_reason, str):
            continue
        try:
            reason = QuarantineReason(raw_reason)
        except ValueError as exc:
            raise ValueError(
                f"unknown quarantine reason in validation result: {raw_reason}"
            ) from exc
        affected_ids = result.affected_observation_ids or tuple(dataset_observation_ids)
        for observation_id in affected_ids:
            reasons.setdefault(observation_id, reason)
    return reasons
