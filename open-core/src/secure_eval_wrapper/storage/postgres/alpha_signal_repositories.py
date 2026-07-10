"""Injected-connection PostgreSQL repositories for public alpha and signal research records."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from uuid import UUID

from secure_eval_wrapper.alpha.models import AlphaDefinition, AlphaRun, AlphaValue
from secure_eval_wrapper.signals.models import SignalRun, StandardizedSignal
from secure_eval_wrapper.storage.postgres.alpha_signal_mappers import (
    alpha_definition_to_row,
    alpha_run_to_row,
    alpha_value_to_row,
    signal_run_to_row,
    standardized_signal_to_row,
)
from secure_eval_wrapper.storage.postgres.alpha_signal_base import _PostgresRepositoryBase, _json_param


class AlphaSignalConflictError(RuntimeError):
    """A logical alpha/signal identity already exists with different content."""


class _ConflictProtectedRepository(_PostgresRepositoryBase):
    def _insert_or_validate(
        self,
        *,
        insert_sql: str,
        insert_params: Sequence[object],
        select_sql: str,
        select_params: Sequence[object],
        expected: Sequence[object],
        label: str,
    ) -> UUID:
        cursor = self.connection.cursor()
        try:
            cursor.execute(insert_sql, tuple(insert_params))
            row = cursor.fetchone()
            if row is None:
                cursor.execute(select_sql, tuple(select_params))
                row = cursor.fetchone()
                if row is None:
                    raise RuntimeError(f"stored {label} disappeared during conflict lookup")
                if tuple(row[1:]) != tuple(expected):
                    raise AlphaSignalConflictError(f"stored {label} content hash differs")
            value = row[0]
            if not isinstance(value, UUID):
                value = UUID(str(value))
        except Exception:
            if self.commit_on_write and hasattr(self.connection, "rollback"):
                self.connection.rollback()
            raise
        finally:
            close = getattr(cursor, "close", None)
            if close is not None:
                close()
        if self.commit_on_write and hasattr(self.connection, "commit"):
            self.connection.commit()
        return value


class PostgresAlphaRepository(_ConflictProtectedRepository):
    def register_alpha(self, definition: AlphaDefinition) -> UUID:
        row = alpha_definition_to_row(definition)
        columns = (
            "alpha_id", "alpha_name", "alpha_version", "description", "category",
            "required_data_types", "required_fields", "parameter_schema_jsonb",
            "default_parameters_jsonb", "minimum_warmup", "output_semantics", "horizon",
            "public_example", "status", "implementation_sha256", "content_sha256",
        )
        params = tuple(_json_param(row[column]) if column.endswith("_jsonb") else row[column] for column in columns)
        return self._insert_or_validate(
            insert_sql=f"INSERT INTO alpha.alpha_registry ({', '.join(columns)}) VALUES ({', '.join(['%s'] * len(columns))}) ON CONFLICT (alpha_name, alpha_version) DO NOTHING RETURNING alpha_id",
            insert_params=params,
            select_sql="SELECT alpha_id, implementation_sha256, content_sha256 FROM alpha.alpha_registry WHERE alpha_name = %s AND alpha_version = %s",
            select_params=(row["alpha_name"], row["alpha_version"]),
            expected=(row["implementation_sha256"], row["content_sha256"]),
            label="alpha definition",
        )

    def record_alpha_run(self, run: AlphaRun) -> UUID:
        row = alpha_run_to_row(run)
        columns = (
            "alpha_run_id", "alpha_id", "alpha_name", "alpha_version", "symbol_set",
            "window_start_utc", "window_end_utc", "dataset_refs", "input_data_sha256",
            "config_sha256", "implementation_sha256", "content_sha256", "started_at_utc",
            "completed_at_utc", "status", "output_count", "rejected_count", "skipped_count",
            "metadata_jsonb",
        )
        params = tuple(_json_param(row[column]) if column.endswith("_jsonb") else row[column] for column in columns)
        return self._insert_or_validate(
            insert_sql=f"INSERT INTO alpha.alpha_runs ({', '.join(columns)}) VALUES ({', '.join(['%s'] * len(columns))}) ON CONFLICT (alpha_run_id) DO NOTHING RETURNING alpha_run_id",
            insert_params=params,
            select_sql="SELECT alpha_run_id, content_sha256 FROM alpha.alpha_runs WHERE alpha_run_id = %s",
            select_params=(row["alpha_run_id"],),
            expected=(row["content_sha256"],),
            label="alpha run",
        )

    def record_alpha_value(self, value: AlphaValue) -> UUID:
        row = alpha_value_to_row(value)
        columns = (
            "alpha_value_id", "alpha_run_id", "alpha_id", "alpha_name", "alpha_version",
            "symbol", "timestamp_utc", "raw_score", "warmup_complete", "valid", "horizon",
            "source_observation_ids", "dataset_sha256", "config_sha256",
            "implementation_sha256", "content_sha256", "provenance_jsonb",
        )
        params = tuple(_json_param(row[column]) if column.endswith("_jsonb") else row[column] for column in columns)
        return self._insert_or_validate(
            insert_sql=f"INSERT INTO alpha.alpha_values ({', '.join(columns)}) VALUES ({', '.join(['%s'] * len(columns))}) ON CONFLICT (alpha_run_id, symbol, timestamp_utc) DO NOTHING RETURNING alpha_value_id",
            insert_params=params,
            select_sql="SELECT alpha_value_id, content_sha256 FROM alpha.alpha_values WHERE alpha_run_id = %s AND symbol = %s AND timestamp_utc = %s",
            select_params=(row["alpha_run_id"], row["symbol"], row["timestamp_utc"]),
            expected=(row["content_sha256"],),
            label="alpha value",
        )

    def get_alpha(self, alpha_id: UUID):
        return self._fetchone("SELECT * FROM alpha.alpha_registry WHERE alpha_id = %s", (alpha_id,))

    def list_alphas(self, *, status: str | None = None):
        if status is None:
            return self._fetchall("SELECT * FROM alpha.alpha_registry ORDER BY alpha_name, alpha_version")
        return self._fetchall("SELECT * FROM alpha.alpha_registry WHERE status = %s ORDER BY alpha_name, alpha_version", (status,))

    def list_alpha_values(self, *, alpha_run_id: UUID, start_utc: datetime, end_utc: datetime):
        return self._fetchall(
            "SELECT * FROM alpha.alpha_values WHERE alpha_run_id = %s AND timestamp_utc >= %s AND timestamp_utc < %s ORDER BY timestamp_utc, symbol, alpha_value_id",
            (alpha_run_id, start_utc, end_utc),
        )


class PostgresSignalRepository(_ConflictProtectedRepository):
    def record_signal_run(self, run: SignalRun) -> UUID:
        row = signal_run_to_row(run)
        columns = (
            "signal_run_id", "run_id", "dataset_ref", "config_sha256", "code_sha256", "seed",
            "started_at_utc", "completed_at_utc", "status", "metadata_jsonb", "alpha_run_ids",
            "symbol_universe", "window_start_utc", "window_end_utc", "ranking_config_jsonb",
            "threshold_config_jsonb", "combination_config_jsonb", "data_sha256", "output_count",
            "long_count", "short_count", "flat_count", "skipped_count", "failure_count", "content_sha256",
        )
        params = tuple(_json_param(row[column]) if column.endswith("_jsonb") else row[column] for column in columns)
        return self._insert_or_validate(
            insert_sql=f"INSERT INTO signals.signal_runs ({', '.join(columns)}) VALUES ({', '.join(['%s'] * len(columns))}) ON CONFLICT (signal_run_id) DO NOTHING RETURNING signal_run_id",
            insert_params=params,
            select_sql="SELECT signal_run_id, content_sha256 FROM signals.signal_runs WHERE signal_run_id = %s",
            select_params=(row["signal_run_id"],),
            expected=(row["content_sha256"],),
            label="signal run",
        )

    def record_signal(self, signal: StandardizedSignal) -> UUID:
        row = standardized_signal_to_row(signal)
        columns = (
            "signal_id", "signal_run_id", "alpha_id", "symbol", "timestamp_utc", "direction",
            "score", "confidence", "horizon", "provenance_jsonb", "alpha_ids_versions",
            "alpha_run_ids", "raw_score", "normalized_score", "rank", "percentile",
            "source_alpha_value_ids", "config_sha256", "data_sha256", "code_sha256", "content_sha256",
        )
        params = tuple(_json_param(row[column]) if column.endswith("_jsonb") else row[column] for column in columns)
        return self._insert_or_validate(
            insert_sql=f"INSERT INTO signals.signals ({', '.join(columns)}) VALUES ({', '.join(['%s'] * len(columns))}) ON CONFLICT (signal_run_id, symbol, timestamp_utc, horizon) DO NOTHING RETURNING signal_id",
            insert_params=params,
            select_sql="SELECT signal_id, content_sha256 FROM signals.signals WHERE signal_run_id = %s AND symbol = %s AND timestamp_utc = %s AND horizon = %s",
            select_params=(row["signal_run_id"], row["symbol"], row["timestamp_utc"], row["horizon"]),
            expected=(row["content_sha256"],),
            label="signal",
        )

    def list_signals(self, *, signal_run_id: UUID, start_utc: datetime | None = None, end_utc: datetime | None = None):
        clauses = ["signal_run_id = %s"]
        params: list[object] = [signal_run_id]
        if start_utc is not None:
            clauses.append("timestamp_utc >= %s")
            params.append(start_utc)
        if end_utc is not None:
            clauses.append("timestamp_utc < %s")
            params.append(end_utc)
        return self._fetchall(
            f"SELECT * FROM signals.signals WHERE {' AND '.join(clauses)} ORDER BY timestamp_utc, symbol, signal_id",
            params,
        )


class PostgresAlphaSignalRepository(PostgresAlphaRepository, PostgresSignalRepository):
    """One connection and one outer transaction for alpha-to-signal persistence."""


__all__ = [
    "AlphaSignalConflictError",
    "PostgresAlphaRepository",
    "PostgresAlphaSignalRepository",
    "PostgresSignalRepository",
]
