"""Canonical record-level validation gates for normalized OHLCV data."""

from __future__ import annotations

from collections.abc import Sequence

from secure_eval_wrapper.data_collection.models import NormalizedBar
from secure_eval_wrapper.data_validation.models import (
    ValidationCheckStatus,
    ValidationReport,
)


def accepted_ohlcv_bars(
    bars: Sequence[NormalizedBar],
    report: ValidationReport,
) -> tuple[NormalizedBar, ...]:
    """Return bars whose source observations are not covered by a failed result.

    A failed result without affected observation identifiers is a dataset-wide failure and rejects
    every bar. Warning results never remove a bar from downstream eligibility.
    """

    if not isinstance(report, ValidationReport):
        raise TypeError("report must be a ValidationReport")
    normalized_bars = tuple(bars)
    if any(not isinstance(bar, NormalizedBar) for bar in normalized_bars):
        raise TypeError("bars must contain only NormalizedBar records")

    failed_results = tuple(
        result
        for result in report.results
        if result.status is ValidationCheckStatus.FAILED
    )
    if any(not result.affected_observation_ids for result in failed_results):
        return ()
    failed_observation_ids = {
        observation_id
        for result in failed_results
        for observation_id in result.affected_observation_ids
    }
    return tuple(
        bar
        for bar in normalized_bars
        if not failed_observation_ids.intersection(bar.source_observation_ids)
    )


__all__ = ["accepted_ohlcv_bars"]
