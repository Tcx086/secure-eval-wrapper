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

from secure_eval_wrapper.storage.postgres.mappers import instrument_metadata_from_row
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


class ValidationReportConflictError(RuntimeError):
    """A stored validation-report identity has different logical content."""


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

    def _execute_returning_uuid(self, sql: str, params: Sequence[object] = ()) -> UUID:
        """Execute a write with ``RETURNING`` and return the database-selected UUID."""

        cursor = self.connection.cursor()
        try:
            cursor.execute(sql, tuple(params))
            row = cursor.fetchone()
            if row is None:
                raise RuntimeError("PostgreSQL write returned no UUID")
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

    def _fetchall(
        self,
        sql: str,
        params: Sequence[object] = (),
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
                {name: value for name, value in zip(names, row)}
                for row in rows
            )
        finally:
            close = getattr(cursor, "close", None)
            if close is not None:
                close()

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
                ingested_at_utc, payload_jsonb, source_sha256, collection_run_id,
                data_type, provider_instrument_id, instrument_type
            ) VALUES (
                %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s, %s,
                %s, %s, %s
            )
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
                observation.get("data_type"),
                observation.get("provider_instrument_id"),
                observation.get("instrument_type"),
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
        """List bars in the half-open ``[start_utc, end_utc)`` interval."""

        cursor = self.connection.cursor()
        try:
            cursor.execute(
                """
                SELECT bar_id, symbol, exchange, timeframe, bar_open_time_utc,
                       open, high, low, close, volume, validation_status,
                       validation_report_id, source_observation_ids, provenance_jsonb
                FROM market_data.validated_bars
                WHERE symbol = %s AND exchange = %s AND timeframe = %s
                  AND bar_open_time_utc >= %s AND bar_open_time_utc < %s
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

    def _record_hashed_identity(
        self,
        *,
        insert_sql: str,
        insert_params: Sequence[object],
        select_sql: str,
        select_params: Sequence[object],
        incoming_hash: str,
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
                    raise RuntimeError(f"{label} conflict was not readable")
                if str(row[1]).strip() != incoming_hash:
                    raise RuntimeError(f"{label} identity conflict: record_sha256 differs")
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

    def record_validated_trade(self, trade: StoragePayload) -> UUID:
        identity = (
            trade["provider_name"],
            trade["provider_instrument_id"],
            trade["provider_trade_id"],
        )
        return self._record_hashed_identity(
            insert_sql="""
                INSERT INTO market_data.validated_trades (
                    trade_id, provider_trade_id, provider_name,
                    provider_instrument_id, instrument_type, symbol, exchange,
                    traded_at_utc, price, quantity, quote_quantity, side,
                    provider_sequence, record_sha256, validation_status,
                    validation_report_id, source_observation_ids, provenance_jsonb
                ) VALUES (
                    %s, %s, %s, %s, %s, %s, %s, %s, %s,
                    %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb
                )
                ON CONFLICT (
                    provider_name, provider_instrument_id, provider_trade_id
                ) DO NOTHING
                RETURNING trade_id, record_sha256
            """,
            insert_params=(
                trade["trade_id"],
                trade["provider_trade_id"],
                trade["provider_name"],
                trade["provider_instrument_id"],
                trade["instrument_type"],
                trade["symbol"],
                trade["exchange"],
                trade["traded_at_utc"],
                trade["price"],
                trade["quantity"],
                trade.get("quote_quantity"),
                trade["side"],
                trade.get("provider_sequence"),
                trade["record_sha256"],
                trade["validation_status"],
                trade["validation_report_id"],
                list(trade.get("source_observation_ids", ())),
                _json_param(trade.get("provenance_jsonb", {})),
            ),
            select_sql="""
                SELECT trade_id, record_sha256
                FROM market_data.validated_trades
                WHERE provider_name = %s
                  AND provider_instrument_id = %s
                  AND provider_trade_id = %s
            """,
            select_params=identity,
            incoming_hash=str(trade["record_sha256"]),
            label="validated trade",
        )

    def record_funding_rate(self, funding_rate: StoragePayload) -> UUID:
        identity = (
            funding_rate["provider_name"],
            funding_rate["provider_instrument_id"],
            funding_rate["instrument_type"],
            funding_rate["funding_time_utc"],
        )
        return self._record_hashed_identity(
            insert_sql="""
                INSERT INTO market_data.funding_rates (
                    funding_rate_id, provider_name, provider_instrument_id,
                    instrument_type, settlement_asset, symbol, exchange,
                    funding_interval, funding_time_utc, rate, predicted_rate,
                    mark_price, index_price, record_sha256, validation_status,
                    validation_report_id, source_observation_ids, provenance_jsonb
                ) VALUES (
                    %s, %s, %s, %s, %s, %s, %s, %s, %s,
                    %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb
                )
                ON CONFLICT (
                    provider_name, provider_instrument_id,
                    instrument_type, funding_time_utc
                ) DO NOTHING
                RETURNING funding_rate_id, record_sha256
            """,
            insert_params=(
                funding_rate["funding_rate_id"],
                funding_rate["provider_name"],
                funding_rate["provider_instrument_id"],
                funding_rate["instrument_type"],
                funding_rate.get("settlement_asset"),
                funding_rate["symbol"],
                funding_rate["exchange"],
                funding_rate.get("funding_interval"),
                funding_rate["funding_time_utc"],
                funding_rate["rate"],
                funding_rate.get("predicted_rate"),
                funding_rate.get("mark_price"),
                funding_rate.get("index_price"),
                funding_rate["record_sha256"],
                funding_rate["validation_status"],
                funding_rate["validation_report_id"],
                list(funding_rate.get("source_observation_ids", ())),
                _json_param(funding_rate.get("provenance_jsonb", {})),
            ),
            select_sql="""
                SELECT funding_rate_id, record_sha256
                FROM market_data.funding_rates
                WHERE provider_name = %s
                  AND provider_instrument_id = %s
                  AND instrument_type = %s
                  AND funding_time_utc = %s
            """,
            select_params=identity,
            incoming_hash=str(funding_rate["record_sha256"]),
            label="funding rate",
        )

    def upsert_instrument(self, instrument: StoragePayload) -> UUID:
        return self._execute_returning_uuid(
            """
            INSERT INTO market_data.instruments (
                instrument_id, provider_name, provider_instrument_id, symbol,
                canonical_display_symbol, exchange, base_asset, quote_asset,
                settlement_asset, instrument_type, contract_type, margin_type,
                status, price_precision, quantity_precision, tick_size,
                quantity_step, minimum_quantity, minimum_notional,
                contract_value, contract_multiplier, margin_asset,
                listing_at_utc, expiry_at_utc, funding_interval,
                metadata_sha256, metadata_jsonb, validation_status,
                validation_report_id, source_observation_ids, provenance_jsonb,
                first_seen_at_utc, last_seen_at_utc
            ) VALUES (
                %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                %s, %s, %s, %s, %s::jsonb, %s, %s, %s, %s::jsonb,
                %s, %s
            )
            ON CONFLICT (
                provider_name, provider_instrument_id,
                instrument_type, metadata_sha256
            ) DO UPDATE SET metadata_sha256 = EXCLUDED.metadata_sha256
            RETURNING instrument_id
            """,
            (
                instrument["instrument_id"],
                instrument["provider_name"],
                instrument["provider_instrument_id"],
                instrument["symbol"],
                instrument["canonical_display_symbol"],
                instrument["exchange"],
                instrument["base_asset"],
                instrument["quote_asset"],
                instrument.get("settlement_asset"),
                instrument["instrument_type"],
                instrument.get("contract_type"),
                instrument.get("margin_type"),
                instrument["status"],
                instrument.get("price_precision"),
                instrument.get("quantity_precision"),
                instrument.get("tick_size"),
                instrument.get("quantity_step"),
                instrument.get("minimum_quantity"),
                instrument.get("minimum_notional"),
                instrument.get("contract_value"),
                instrument.get("contract_multiplier"),
                instrument.get("margin_asset"),
                instrument.get("listing_at_utc"),
                instrument.get("expiry_at_utc"),
                instrument.get("funding_interval"),
                instrument["metadata_sha256"],
                _json_param(instrument.get("metadata_jsonb", {})),
                instrument["validation_status"],
                instrument["validation_report_id"],
                list(instrument.get("source_observation_ids", ())),
                _json_param(instrument.get("provenance_jsonb", {})),
                instrument.get("first_seen_at_utc"),
                instrument.get("last_seen_at_utc"),
            ),
        )

    def list_validated_trades(
        self,
        *,
        provider_name: str,
        provider_instrument_id: str,
        instrument_type: str,
        start_utc: datetime,
        end_utc: datetime,
    ) -> Sequence[StorageRecord]:
        return self._fetchall(
            """
            SELECT *
            FROM market_data.validated_trades
            WHERE provider_name = %s AND provider_instrument_id = %s
              AND instrument_type = %s
              AND traded_at_utc >= %s AND traded_at_utc < %s
            ORDER BY traded_at_utc, provider_trade_id, trade_id
            """,
            (provider_name, provider_instrument_id, instrument_type, start_utc, end_utc),
        )

    def list_funding_rates(
        self,
        *,
        provider_name: str,
        provider_instrument_id: str,
        instrument_type: str,
        start_utc: datetime,
        end_utc: datetime,
    ) -> Sequence[StorageRecord]:
        return self._fetchall(
            """
            SELECT *
            FROM market_data.funding_rates
            WHERE provider_name = %s AND provider_instrument_id = %s
              AND instrument_type = %s
              AND funding_time_utc >= %s AND funding_time_utc < %s
            ORDER BY funding_time_utc, funding_rate_id
            """,
            (provider_name, provider_instrument_id, instrument_type, start_utc, end_utc),
        )

    def get_instrument(
        self,
        *,
        provider_name: str,
        provider_instrument_id: str,
        instrument_type: str,
    ) -> StorageRecord | None:
        return self._fetchone(
            """
            SELECT *
            FROM market_data.instruments
            WHERE provider_name = %s AND provider_instrument_id = %s
              AND instrument_type = %s
            ORDER BY last_seen_at_utc DESC NULLS LAST, created_at_utc DESC, instrument_id
            LIMIT 1
            """,
            (provider_name, provider_instrument_id, instrument_type),
        )

    def get_instrument_snapshot(
        self,
        *,
        provider_name: str,
        provider_instrument_id: str,
        instrument_type: str,
    ):
        row = self.get_instrument(
            provider_name=provider_name,
            provider_instrument_id=provider_instrument_id,
            instrument_type=instrument_type,
        )
        return None if row is None else instrument_metadata_from_row(row)
    def list_instruments(
        self,
        *,
        provider_name: str | None = None,
        instrument_type: str | None = None,
    ) -> Sequence[StorageRecord]:
        conditions = []
        params: list[object] = []
        if provider_name is not None:
            conditions.append("provider_name = %s")
            params.append(provider_name)
        if instrument_type is not None:
            conditions.append("instrument_type = %s")
            params.append(instrument_type)
        where = " AND ".join(conditions) if conditions else "TRUE"
        return self._fetchall(
            f"""
            SELECT *
            FROM market_data.instruments
            WHERE {where}
            ORDER BY provider_name, instrument_type, provider_instrument_id,
                     last_seen_at_utc, instrument_id
            """,
            params,
        )


class PostgresDataQualityRepository(_PostgresRepositoryBase, DataQualityRepository):
    """PostgreSQL writes for validation reports and individual check results."""

    def record_validation_report(self, report: StoragePayload) -> UUID:
        report_id = report["validation_report_id"]
        identity_params = (report["validation_run_id"], report["dataset_ref"])
        cursor = self.connection.cursor()
        try:
            cursor.execute(
                """
                INSERT INTO data_quality.validation_reports (
                    validation_report_id, validation_run_id, dataset_ref,
                    accepted_count, rejected_count, warning_count, status,
                    report_sha256, report_jsonb, created_at_utc
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s)
                ON CONFLICT (validation_run_id, dataset_ref) DO NOTHING
                RETURNING validation_report_id
                """,
                (
                    report_id,
                    *identity_params,
                    report["accepted_count"],
                    report["rejected_count"],
                    report["warning_count"],
                    report["status"],
                    report.get("report_sha256"),
                    _json_param(report.get("report_jsonb", {})),
                    report["created_at_utc"],
                ),
            )
            row = cursor.fetchone()
            if row is None:
                cursor.execute(
                    """
                    SELECT validation_report_id, report_sha256
                    FROM data_quality.validation_reports
                    WHERE validation_run_id = %s AND dataset_ref = %s
                    """,
                    identity_params,
                )
                row = cursor.fetchone()
                if row is None:
                    raise RuntimeError(
                        "validation report conflict was not readable after insert"
                    )
                stored_hash = None if row[1] is None else str(row[1]).strip()
                incoming_value = report.get("report_sha256")
                incoming_hash = (
                    None if incoming_value is None else str(incoming_value).strip()
                )
                if stored_hash != incoming_hash:
                    raise ValidationReportConflictError(
                        "validation report identity conflict: stored and incoming "
                        "report_sha256 values differ"
                    )
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
    "ValidationReportConflictError",
]
