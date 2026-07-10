"""PostgreSQL repositories for reconciliation summaries and child checks."""

from __future__ import annotations

from collections.abc import Sequence
from uuid import UUID

from secure_eval_wrapper.storage.postgres.repositories import (
    PostgresOfflineValidationRepository,
    _PostgresRepositoryBase,
    _json_param,
)
from secure_eval_wrapper.storage.repositories.interfaces import (
    ReconciliationRepository,
    StoragePayload,
    StorageRecord,
)


class PostgresReconciliationRepository(
    _PostgresRepositoryBase,
    ReconciliationRepository,
):
    """Parameterized, idempotent PostgreSQL reconciliation persistence."""

    def record_reconciliation_result(self, result: StoragePayload) -> UUID:
        return self._execute_returning_uuid(
            """
            INSERT INTO data_quality.reconciliation_results (
                reconciliation_id, validation_run_id, data_type, symbol, timeframe,
                provider_names, window_start_utc, window_end_utc, status,
                config_sha256, dataset_sha256, result_sha256, metrics_jsonb,
                created_at_utc
            ) VALUES (
                %s, %s, %s, %s, %s, %s::jsonb, %s, %s, %s,
                %s, %s, %s, %s::jsonb, %s
            )
            ON CONFLICT (
                validation_run_id, data_type, symbol, timeframe,
                config_sha256, dataset_sha256
            ) DO UPDATE SET symbol = EXCLUDED.symbol
            RETURNING reconciliation_id
            """,
            (
                result["reconciliation_id"],
                result["validation_run_id"],
                result["data_type"],
                result["symbol"],
                result.get("timeframe"),
                _json_param(result["provider_names"]),
                result.get("window_start_utc"),
                result.get("window_end_utc"),
                result["status"],
                result["config_sha256"],
                result["dataset_sha256"],
                result["result_sha256"],
                _json_param(result.get("metrics_jsonb", {})),
                result["created_at_utc"],
            ),
        )

    def record_reconciliation_check_result(self, result: StoragePayload) -> UUID:
        return self._execute_returning_uuid(
            """
            INSERT INTO data_quality.reconciliation_check_results (
                result_id, reconciliation_id, validation_run_id, check_id,
                check_type, status, severity, affected_observation_ids,
                details_jsonb, created_at_utc
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s)
            ON CONFLICT (reconciliation_id, check_id)
            DO UPDATE SET check_id = EXCLUDED.check_id
            RETURNING result_id
            """,
            (
                result["result_id"],
                result["reconciliation_id"],
                result["validation_run_id"],
                result["check_id"],
                result["check_type"],
                result["status"],
                result["severity"],
                list(result.get("affected_observation_ids", ())),
                _json_param(result.get("details_jsonb", {})),
                result["created_at_utc"],
            ),
        )

    def get_reconciliation_result(
        self,
        reconciliation_id: UUID,
    ) -> StorageRecord | None:
        return self._fetchone(
            """
            SELECT reconciliation_id, validation_run_id, data_type, symbol,
                   timeframe, provider_names, window_start_utc, window_end_utc,
                   status, config_sha256, dataset_sha256, result_sha256,
                   metrics_jsonb, created_at_utc
            FROM data_quality.reconciliation_results
            WHERE reconciliation_id = %s
            """,
            (reconciliation_id,),
        )

    def list_reconciliation_results(
        self,
        *,
        validation_run_id: UUID | None = None,
        symbol: str | None = None,
        status: str | None = None,
    ) -> Sequence[StorageRecord]:
        conditions: list[str] = []
        params: list[object] = []
        if validation_run_id is not None:
            conditions.append("validation_run_id = %s")
            params.append(validation_run_id)
        if symbol is not None:
            conditions.append("symbol = %s")
            params.append(symbol)
        if status is not None:
            conditions.append("status = %s")
            params.append(status)
        where_clause = " AND ".join(conditions) if conditions else "TRUE"
        sql = f"""
            SELECT reconciliation_id, validation_run_id, data_type, symbol,
                   timeframe, provider_names, window_start_utc, window_end_utc,
                   status, config_sha256, dataset_sha256, result_sha256,
                   metrics_jsonb, created_at_utc
            FROM data_quality.reconciliation_results
            WHERE {where_clause}
            ORDER BY created_at_utc, reconciliation_id
        """
        return self._fetchall(sql, params)

    def _fetchall(
        self,
        sql: str,
        params: Sequence[object],
    ) -> Sequence[StorageRecord]:
        cursor = self.connection.cursor()
        try:
            cursor.execute(sql, tuple(params))
            rows = cursor.fetchall()
            description = getattr(cursor, "description", None)
            if not description:
                return tuple(
                    {str(index): value for index, value in enumerate(row)}
                    for row in rows
                )
            names = [str(item[0]) for item in description]
            return tuple(
                {name: value for name, value in zip(names, row)} for row in rows
            )
        finally:
            close = getattr(cursor, "close", None)
            if close is not None:
                close()


class PostgresOhlcvPipelineRepository(
    PostgresOfflineValidationRepository,
    PostgresReconciliationRepository,
):
    """Unified repository for one atomic OHLCV pipeline persistence boundary."""


__all__ = [
    "PostgresOhlcvPipelineRepository",
    "PostgresReconciliationRepository",
]
