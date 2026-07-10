"""Atomic persistence orchestration for cross-source reconciliation."""

from __future__ import annotations

from contextlib import nullcontext
from dataclasses import dataclass
from uuid import UUID

from secure_eval_wrapper.data_validation.models import ReconciliationResult
from secure_eval_wrapper.storage.postgres.reconciliation_mappers import (
    reconciliation_check_result_to_row,
    reconciliation_result_to_row,
)
from secure_eval_wrapper.storage.repositories.interfaces import ReconciliationRepository


@dataclass(frozen=True)
class ReconciliationPersistenceSummary:
    """Database-selected identifiers written in one transaction."""

    reconciliation_id: UUID
    check_result_ids: tuple[UUID, ...]


def persist_reconciliation_result(
    result: ReconciliationResult,
    *,
    repository: ReconciliationRepository,
    manage_transaction: bool = True,
) -> ReconciliationPersistenceSummary:
    """Persist a summary and all child checks atomically when transaction-managed."""

    if not isinstance(result, ReconciliationResult):
        raise TypeError("result must be a ReconciliationResult")
    if repository is None:
        raise TypeError("repository is required")
    transaction = (
        repository.transaction()  # type: ignore[attr-defined]
        if manage_transaction and hasattr(repository, "transaction")
        else nullcontext()
    )
    with transaction:
        reconciliation_id = repository.record_reconciliation_result(
            reconciliation_result_to_row(result)
        )
        check_ids = tuple(
            repository.record_reconciliation_check_result(
                reconciliation_check_result_to_row(
                    check,
                    reconciliation_id=reconciliation_id,
                )
            )
            for check in result.results
        )
    return ReconciliationPersistenceSummary(
        reconciliation_id=reconciliation_id,
        check_result_ids=check_ids,
    )


__all__ = [
    "ReconciliationPersistenceSummary",
    "persist_reconciliation_result",
]
