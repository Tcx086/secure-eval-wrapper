"""Concrete PostgreSQL repositories for Phase 2D offline validation persistence.

Repositories accept an already-created DB-API connection.  Importing this module never selects a
PostgreSQL driver or opens a connection.  Callers can use ``transaction()`` on the unified
repository to make the raw observation, report, checks, bars, and quarantine writes atomic.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from contextlib import contextmanager
from datetime import datetime
from decimal import Decimal
from typing import Any, Iterator
from uuid import UUID

from secure_eval_wrapper.storage.repositories.interfaces import (
    DataQualityRepository,
    MarketDataRepository,
    QuarantineRepository,
    StoragePayload,
    StorageRecord,
)


def _jsonable(value: object) -> object:
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if isinstance(value, (UUID, Decimal, datetime)):
        return str(value)
    if hasattr(value, "value"):
        return _jsonable(value.value)  # enum
    if isinstance(value, Mapping):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [_jsonable(item) for item in value]
    return str(value)


def _json_param(value: object) -> str:
    return json.dumps(_jsonable(value), sort_keys=True, separators=(",", ":"), allow_nan=False)


class _PostgresRepositoryBase:
    def __init__(self, connection: Any, *, commit_on_write: bool = True) -> None:
        if connection is None:
            raise TypeError("a DB-API PostgreSQL connection is required")
        self.connection = connection
        self.commit_on_write = commit_on_write

    def _execute(self, sql: str, params: Sequence[object] = ()) -> None:
        cursor = self.connection.cursor()
        try:
            cursor.execute(sql, tuple(params))
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

    def _fetchone(self, sql: str, params: Sequence[object] = ()) -> StorageRecord | None:
        cursor = self.connection.cursor()
        try:
            cursor.execute(sql, tuple(params))
            row = cursor.fetchone()
            if row is None:
                return None
            description = getattr(cursor, "description", None)
            if description:
                return {str(item[0]): value for item, value in zip(description, row)}
            return {str(index): value for index, value in enumerate(row)}
        finally:
            close = getattr(cursor, "close", None)
            if close is not None:
                close()

    @contextmanager
    def transaction(self) -> Iterator["_PostgresRepositoryBase"]:
        """Run writes atomically, temporarily disabling per-method commits."""

        previous = self.commit_on_write
        self.commit_on_write = False
        try:
            yield self
        except Exception:
            if hasattr(self.connection, "rollback"):
                self.connection.rollback()
            raise
        else:
            if hasattr(self.connection, "commit"):
                self.connection.commit()
        finally:
            self.commit_on_write = previous


class PostgresMarketDataRepository(_PostgresRepositoryBase, MarketDataRepository):
    """PostgreSQL writes for raw observations and validated OHLCV bars."""

    def record_raw_source_observation(self, observation: StoragePayload) -> UUID:
        observation_id = observation["observation_id"]
        self._execute(
            """
            INSERT INTO market_data.raw_source_observations (
                observation_id, source_provider, source_exchange, source_endpoint,
                symbol_raw, symbol_normalized, timeframe, observed_at_utc,
                ingested_at_utc, payload_jsonb, source_sha256, collection_run_id
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s, %s)
            ON CONFLICT (observation_id) DO NOTHING
            """,
            (
                observation_id,
                observation["source_provider"],
                observation.get("source_exchange"),
                observation["source_endpoint"],
                observation.get("symbol_raw"),
                observation.get("symbol_normalized"),
                observation.get("timeframe"),
                observation.get("observed_at_utc"),
                observation["ingested_at_utc"],
                _json_param(observation["payload_jsonb"]),
                observation["source_sha256"],
                observation.get("collection_run_id"),
            ),
        )
        return observation_id  # type: ignore[return-value]

    def record_validated_bar(self, bar: StoragePayload) -> UUID:
        bar_id = bar["bar_id"]
        self._execute(
            """
            INSERT INTO market_data.validated_bars (
                bar_id, symbol, exchange, timeframe, bar_open_time_utc,
                open, high, low, close, volume, validation_status,
                validation_report_id, source_observation_ids, provenance_jsonb
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb)
            ON CONFLICT (symbol, exchange, timeframe, bar_open_time_utc) DO NOTHING
            """,
            (
                bar_id,
                bar["symbol"],
                bar["exchange"],
                bar["timeframe"],
                bar["bar_open_time_utc"],
                bar["open"],
                bar["high"],
                bar["low"],
                bar["close"],
                bar["volume"],
                bar["validation_status"],
                bar.get("validation_report_id"),
                list(bar.get("source_observation_ids", ())),
                _json_param(bar.get("provenance_jsonb", {})),
            ),
        )
        return bar_id  # type: ignore[return-value]

    def list_validated_bars(
        self,
        *,
        symbol: str,
        exchange: str,
        timeframe: str,
        start_utc: datetime,
        end_utc: datetime,
    ) -> Sequence[StorageRecord]:
        cursor = self.connection.cursor()
        try:
            cursor.execute(
                """
                SELECT bar_id, symbol, exchange, timeframe, bar_open_time_utc,
                       open, high, low, close, volume, validation_status,
                       validation_report_id, source_observation_ids, provenance_jsonb
                FROM market_data.validated_bars
                WHERE symbol = %s AND exchange = %s AND timeframe = %s
                  AND bar_open_time_utc >= %s AND bar_open_time_utc <= %s
                ORDER BY bar_open_time_utc
                """,
                (symbol, exchange, timeframe, start_utc, end_utc),
            )
            rows = cursor.fetchall()
            description = getattr(cursor, "description", None)
            if not description:
                return tuple({str(i): value for i, value in enumerate(row)} for row in rows)
            names = [str(item[0]) for item in description]
            return tuple({name: value for name, value in zip(names, row)} for row in rows)
        finally:
            close = getattr(cursor, "close", None)
            if close is not None:
                close()

    def record_validated_trade(self, trade: StoragePayload) -> UUID:
        raise NotImplementedError("Phase 2D only persists OHLCV bars")

    def record_funding_rate(self, funding_rate: StoragePayload) -> UUID:
        raise NotImplementedError("Phase 2D does not persist funding rates")

    def upsert_instrument(self, instrument: StoragePayload) -> UUID:
        raise NotImplementedError("Phase 2D does not persist instruments")


class PostgresDataQualityRepository(_PostgresRepositoryBase, DataQualityRepository):
    """PostgreSQL writes for validation reports and individual check results."""

    def record_validation_report(self, report: StoragePayload) -> UUID:
        report_id = report["validation_report_id"]
        self._execute(
            """
            INSERT INTO data_quality.validation_reports (
                validation_report_id, validation_run_id, dataset_ref,
                accepted_count, rejected_count, warning_count, status,
                report_sha256, report_jsonb, created_at_utc
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s)
            ON CONFLICT (validation_run_id, dataset_ref) DO NOTHING
            """,
            (
                report_id,
                report["validation_run_id"],
                report["dataset_ref"],
                report["accepted_count"],
                report["rejected_count"],
                report["warning_count"],
                report["status"],
                report.get("report_sha256"),
                _json_param(report.get("report_jsonb", {})),
                report["created_at_utc"],
            ),
        )
        return report_id  # type: ignore[return-value]

    def record_data_quality_check(self, check: StoragePayload) -> UUID:
        check_id = check["check_id"]
        self._execute(
            """
            INSERT INTO data_quality.data_quality_checks (
                check_id, validation_run_id, check_type, severity, symbol,
                timeframe, window_start_utc, window_end_utc, status,
                details_jsonb, created_at_utc
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s)
            ON CONFLICT (check_id) DO NOTHING
            """,
            (
                check_id,
                check["validation_run_id"],
                check["check_type"],
                check["severity"],
                check.get("symbol"),
                check.get("timeframe"),
                check.get("window_start_utc"),
                check.get("window_end_utc"),
                check["status"],
                _json_param(check.get("details_jsonb", {})),
                check["created_at_utc"],
            ),
        )
        return check_id  # type: ignore[return-value]

    def get_validation_report(self, validation_report_id: UUID) -> StorageRecord | None:
        return self._fetchone(
            """
            SELECT validation_report_id, validation_run_id, dataset_ref,
                   accepted_count, rejected_count, warning_count, status,
                   report_sha256, report_jsonb, created_at_utc
            FROM data_quality.validation_reports
            WHERE validation_report_id = %s
            """,
            (validation_report_id,),
        )


class PostgresQuarantineRepository(_PostgresRepositoryBase, QuarantineRepository):
    """PostgreSQL writes for failed offline source observations."""

    def record_quarantine_decision(self, decision: StoragePayload) -> UUID:
        quarantine_id = decision["quarantine_id"]
        self._execute(
            """
            INSERT INTO data_quality.quarantine_decisions (
                quarantine_id, validation_report_id, validation_run_id, observation_id,
                quarantine_reason, symbol, exchange, timeframe, source_sha256,
                details_jsonb, created_at_utc
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s)
            ON CONFLICT (quarantine_id) DO NOTHING
            """,
            (
                quarantine_id,
                decision["validation_report_id"],
                decision["validation_run_id"],
                decision["observation_id"],
                decision["quarantine_reason"],
                decision.get("symbol"),
                decision.get("exchange"),
                decision.get("timeframe"),
                decision.get("source_sha256"),
                _json_param(decision.get("details_jsonb", {})),
                decision["created_at_utc"],
            ),
        )
        return quarantine_id  # type: ignore[return-value]


class PostgresOfflineValidationRepository(
    PostgresMarketDataRepository,
    PostgresDataQualityRepository,
    PostgresQuarantineRepository,
):
    """Unified Phase 2D repository sharing one PostgreSQL connection and transaction."""


__all__ = [
    "PostgresDataQualityRepository",
    "PostgresMarketDataRepository",
    "PostgresOfflineValidationRepository",
    "PostgresQuarantineRepository",
]
