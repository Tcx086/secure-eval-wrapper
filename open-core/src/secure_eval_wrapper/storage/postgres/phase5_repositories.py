"""Normalized PostgreSQL persistence for complete Phase 5 backtest runs."""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from typing import Iterator
from uuid import UUID

from secure_eval_wrapper.storage.postgres.alpha_signal_base import _PostgresRepositoryBase, _json_param
from secure_eval_wrapper.storage.postgres.phase5_rows import (
    account_snapshot_row,
    backtest_run_row,
    cash_ledger_row,
    equity_row,
    event_row,
    fill_row,
    funding_payment_row,
    metric_row,
    order_intent_row,
    order_row,
    position_row,
    position_snapshot_row,
    risk_decision_row,
)


class Phase5ConflictError(RuntimeError):
    """A deterministic logical identity or complete-run membership conflicts."""


@dataclass(frozen=True)
class _MembershipSpec:
    record_type: str
    output_name: str
    table: str
    id_column: str
    membership_column: str


_MEMBERSHIP_SPECS = {
    spec.record_type: spec
    for spec in (
        _MembershipSpec("order_intent", "order_intents", "execution.order_intents", "order_intent_id", "order_intent_id"),
        _MembershipSpec("risk_decision", "risk_decisions", "execution.risk_decisions", "risk_decision_id", "risk_decision_id"),
        _MembershipSpec("order", "orders", "execution.orders", "order_id", "order_id"),
        _MembershipSpec("fill", "fills", "execution.fills", "fill_id", "fill_id"),
        _MembershipSpec("position", "positions", "execution.positions", "position_id", "position_id"),
        _MembershipSpec("position_snapshot", "position_snapshots", "execution.position_snapshots", "position_snapshot_id", "position_snapshot_id"),
        _MembershipSpec("funding_payment", "funding_payments", "execution.funding_payments", "funding_payment_id", "funding_payment_id"),
        _MembershipSpec("cash_ledger_entry", "cash_ledger_entries", "execution.cash_ledger_entries", "cash_ledger_entry_id", "cash_ledger_entry_id"),
        _MembershipSpec("account_snapshot", "account_snapshots", "execution.account_snapshots", "account_snapshot_id", "account_snapshot_id"),
        _MembershipSpec("backtest_event", "events", "backtesting.backtest_events", "backtest_event_id", "backtest_event_id"),
        _MembershipSpec("equity_curve", "equity_points", "backtesting.equity_curves", "equity_curve_id", "equity_curve_id"),
    )
}

_GARBAGE_COLLECTION_ORDER = (
    "account_snapshot",
    "equity_curve",
    "backtest_event",
    "cash_ledger_entry",
    "position_snapshot",
    "risk_decision",
    "funding_payment",
    "fill",
    "order",
    "order_intent",
    "position",
)


class PostgresPhase5Repository(_PostgresRepositoryBase):
    @contextmanager
    def _write_scope(self) -> Iterator[None]:
        if self.commit_on_write:
            with self.transaction():
                yield
        else:
            yield

    def _record(
        self,
        *,
        table: str,
        id_column: str,
        row: dict[str, object],
        logical_where: str,
        logical_params: tuple[object, ...],
        label: str,
    ) -> UUID:
        columns = tuple(row)
        params = tuple(_json_param(row[name]) if name.endswith("_jsonb") else row[name] for name in columns)
        sql = (
            f"INSERT INTO {table} ({', '.join(columns)}) "
            f"VALUES ({', '.join(['%s'] * len(columns))}) "
            f"ON CONFLICT DO NOTHING RETURNING {id_column}, record_sha256"
        )
        cursor = self.connection.cursor()
        try:
            cursor.execute(sql, params)
            stored = cursor.fetchone()
            if stored is None:
                cursor.execute(
                    f"SELECT {id_column}, record_sha256 FROM {table} WHERE {id_column} = %s",
                    (row[id_column],),
                )
                stored = cursor.fetchone()
            if stored is None:
                cursor.execute(
                    f"SELECT {id_column}, record_sha256 FROM {table} WHERE {logical_where}",
                    logical_params,
                )
                stored = cursor.fetchone()
            if stored is None:
                raise Phase5ConflictError(f"{label} logical identity conflicted with an inaccessible row")
            if str(stored[1]) != str(row["record_sha256"]):
                raise Phase5ConflictError(f"stored {label} record hash differs")
            return stored[0] if isinstance(stored[0], UUID) else UUID(str(stored[0]))
        finally:
            close = getattr(cursor, "close", None)
            if close is not None:
                close()

    def _record_membership(
        self,
        *,
        backtest_run_id: UUID,
        record_type: str,
        record_id: UUID,
        deterministic_ordinal: int,
    ) -> None:
        if not isinstance(backtest_run_id, UUID):
            raise TypeError("backtest_run_id must be a UUID")
        if not isinstance(deterministic_ordinal, int) or deterministic_ordinal < 0:
            raise ValueError("membership ordinal must be a non-negative integer")
        try:
            spec = _MEMBERSHIP_SPECS[record_type]
        except KeyError as exc:
            raise ValueError(f"unsupported Phase 5 membership type: {record_type}") from exc
        cursor = self.connection.cursor()
        try:
            cursor.execute(
                "INSERT INTO backtesting.backtest_run_memberships "
                f"(backtest_run_id, record_type, record_id, deterministic_ordinal, {spec.membership_column}) "
                "VALUES (%s, %s, %s, %s, %s) ON CONFLICT DO NOTHING "
                "RETURNING record_id, deterministic_ordinal",
                (backtest_run_id, record_type, record_id, deterministic_ordinal, record_id),
            )
            stored = cursor.fetchone()
            if stored is None:
                cursor.execute(
                    "SELECT record_id, deterministic_ordinal "
                    "FROM backtesting.backtest_run_memberships "
                    "WHERE backtest_run_id = %s AND record_type = %s "
                    "AND (record_id = %s OR deterministic_ordinal = %s) "
                    "ORDER BY record_id LIMIT 1",
                    (backtest_run_id, record_type, record_id, deterministic_ordinal),
                )
                stored = cursor.fetchone()
            if stored is None:
                raise Phase5ConflictError("run membership conflicted with an inaccessible row")
            stored_id = stored[0] if isinstance(stored[0], UUID) else UUID(str(stored[0]))
            if stored_id != record_id or int(stored[1]) != deterministic_ordinal:
                raise Phase5ConflictError(
                    f"stored {record_type} run membership differs in record identity or deterministic order"
                )
        finally:
            close = getattr(cursor, "close", None)
            if close is not None:
                close()

    def _record_with_membership(
        self,
        *,
        backtest_run_id: UUID,
        membership_ordinal: int,
        record_type: str,
        table: str,
        id_column: str,
        row: dict[str, object],
        logical_where: str,
        logical_params: tuple[object, ...],
        label: str,
    ) -> UUID:
        owner_row = row | {"backtest_run_id": backtest_run_id}
        with self._write_scope():
            record_id = self._record(
                table=table,
                id_column=id_column,
                row=owner_row,
                logical_where=logical_where,
                logical_params=logical_params,
                label=label,
            )
            self._record_membership(
                backtest_run_id=backtest_run_id,
                record_type=record_type,
                record_id=record_id,
                deterministic_ordinal=membership_ordinal,
            )
        return record_id

    def record_backtest_run(self, value):
        row = backtest_run_row(value)
        with self._write_scope():
            return self._record(
                table="backtesting.backtest_runs",
                id_column="backtest_run_id",
                row=row,
                logical_where="backtest_run_id = %s",
                logical_params=(row["backtest_run_id"],),
                label="backtest run",
            )

    def record_order_intent(self, value, *, backtest_run_id: UUID, membership_ordinal: int):
        row = order_intent_row(value)
        return self._record_with_membership(
            backtest_run_id=backtest_run_id, membership_ordinal=membership_ordinal,
            record_type="order_intent", table="execution.order_intents", id_column="order_intent_id", row=row,
            logical_where="run_id = %s AND signal_id = %s AND series_identity_sha256 = %s AND event_timestamp_utc = %s",
            logical_params=(row["run_id"], row["signal_id"], row["series_identity_sha256"], row["event_timestamp_utc"]),
            label="order intent",
        )

    def record_risk_decision(self, value, *, backtest_run_id: UUID, membership_ordinal: int):
        row = risk_decision_row(value)
        return self._record_with_membership(
            backtest_run_id=backtest_run_id, membership_ordinal=membership_ordinal,
            record_type="risk_decision", table="execution.risk_decisions", id_column="risk_decision_id", row=row,
            logical_where="order_intent_id = %s AND stage = %s AND decision_timestamp_utc = %s",
            logical_params=(row["order_intent_id"], row["stage"], row["decision_timestamp_utc"]),
            label="risk decision",
        )

    def record_order(self, value, *, backtest_run_id: UUID, membership_ordinal: int):
        row = order_row(value)
        return self._record_with_membership(
            backtest_run_id=backtest_run_id, membership_ordinal=membership_ordinal,
            record_type="order", table="execution.orders", id_column="order_id", row=row,
            logical_where="order_intent_id = %s", logical_params=(row["order_intent_id"],), label="order",
        )

    def record_fill(self, value, *, backtest_run_id: UUID, membership_ordinal: int):
        row = fill_row(value)
        return self._record_with_membership(
            backtest_run_id=backtest_run_id, membership_ordinal=membership_ordinal,
            record_type="fill", table="execution.fills", id_column="fill_id", row=row,
            logical_where="order_id = %s", logical_params=(row["order_id"],), label="fill",
        )

    def upsert_position(self, value, *, backtest_run_id: UUID, membership_ordinal: int):
        row = position_row(value)
        return self._record_with_membership(
            backtest_run_id=backtest_run_id, membership_ordinal=membership_ordinal,
            record_type="position", table="execution.positions", id_column="position_id", row=row,
            logical_where="run_id = %s AND account_ref = %s AND series_identity_sha256 = %s",
            logical_params=(row["run_id"], row["account_ref"], row["series_identity_sha256"]), label="position",
        )

    def record_position_snapshot(self, value, *, backtest_run_id: UUID, membership_ordinal: int):
        row = position_snapshot_row(value)
        return self._record_with_membership(
            backtest_run_id=backtest_run_id, membership_ordinal=membership_ordinal,
            record_type="position_snapshot", table="execution.position_snapshots", id_column="position_snapshot_id", row=row,
            logical_where="run_id = %s AND account_ref = %s AND position_id = %s AND snapshot_at_utc = %s AND snapshot_kind = %s AND source_event_id = %s AND logical_sequence = %s",
            logical_params=(row["run_id"], row["account_ref"], row["position_id"], row["snapshot_at_utc"], row["snapshot_kind"], row["source_event_id"], row["logical_sequence"]),
            label="position snapshot",
        )

    def record_funding_payment(self, value, *, backtest_run_id: UUID, membership_ordinal: int):
        row = funding_payment_row(value)
        return self._record_with_membership(
            backtest_run_id=backtest_run_id, membership_ordinal=membership_ordinal,
            record_type="funding_payment", table="execution.funding_payments", id_column="funding_payment_id", row=row,
            logical_where="run_id = %s AND funding_rate_id = %s AND series_identity_sha256 = %s",
            logical_params=(row["run_id"], row["funding_rate_id"], row["series_identity_sha256"]), label="funding payment",
        )

    def record_cash_ledger_entry(self, value, *, backtest_run_id: UUID, membership_ordinal: int):
        row = cash_ledger_row(value)
        return self._record_with_membership(
            backtest_run_id=backtest_run_id, membership_ordinal=membership_ordinal,
            record_type="cash_ledger_entry", table="execution.cash_ledger_entries", id_column="cash_ledger_entry_id", row=row,
            logical_where="run_id = %s AND ledger_sequence = %s",
            logical_params=(row["run_id"], row["ledger_sequence"]), label="cash ledger entry",
        )

    def record_account_snapshot(self, value, *, backtest_run_id: UUID, membership_ordinal: int):
        row = account_snapshot_row(value)
        return self._record_with_membership(
            backtest_run_id=backtest_run_id, membership_ordinal=membership_ordinal,
            record_type="account_snapshot", table="execution.account_snapshots", id_column="account_snapshot_id", row=row,
            logical_where="run_id = %s AND account_ref = %s AND snapshot_at_utc = %s",
            logical_params=(row["run_id"], row["account_ref"], row["snapshot_at_utc"]), label="account snapshot",
        )

    def record_backtest_event(self, value, *, backtest_run_id: UUID, membership_ordinal: int):
        row = event_row(value)
        return self._record_with_membership(
            backtest_run_id=backtest_run_id, membership_ordinal=membership_ordinal,
            record_type="backtest_event", table="backtesting.backtest_events", id_column="backtest_event_id", row=row,
            logical_where="backtest_event_id = %s", logical_params=(row["backtest_event_id"],), label="backtest event",
        )

    def record_equity_curve_point(self, value, *, backtest_run_id: UUID, membership_ordinal: int):
        row = equity_row(value)
        return self._record_with_membership(
            backtest_run_id=backtest_run_id, membership_ordinal=membership_ordinal,
            record_type="equity_curve", table="backtesting.equity_curves", id_column="equity_curve_id", row=row,
            logical_where="equity_curve_id = %s", logical_params=(row["equity_curve_id"],), label="equity point",
        )

    def record_backtest_metric(self, value, *, backtest_run_id: UUID):
        if value.backtest_run_id != backtest_run_id:
            raise ValueError("metric backtest_run_id does not match the complete run being persisted")
        row = metric_row(value)
        with self._write_scope():
            return self._record(
                table="backtesting.backtest_metrics", id_column="backtest_metric_id", row=row,
                logical_where="backtest_run_id = %s AND metric_name = %s",
                logical_params=(backtest_run_id, row["metric_name"]), label="backtest metric",
            )

    def _list_run_records(self, *, backtest_run_id: UUID, record_type: str):
        try:
            spec = _MEMBERSHIP_SPECS[record_type]
        except KeyError as exc:
            raise ValueError(f"unsupported Phase 5 membership type: {record_type}") from exc
        return self._fetchall(
            f"SELECT child.*, membership.backtest_run_id AS membership_backtest_run_id, membership.deterministic_ordinal AS membership_ordinal FROM {spec.table} AS child "
            "JOIN backtesting.backtest_run_memberships AS membership "
            f"ON membership.{spec.membership_column} = child.{spec.id_column} "
            "WHERE membership.backtest_run_id = %s AND membership.record_type = %s "
            f"ORDER BY membership.deterministic_ordinal, child.{spec.id_column}",
            (backtest_run_id, record_type),
        )

    def get_backtest_run(self, *, backtest_run_id: UUID):
        return self._fetchone(
            "SELECT * FROM backtesting.backtest_runs WHERE backtest_run_id = %s",
            (backtest_run_id,),
        )

    def get_backtest_bundle(self, *, backtest_run_id: UUID):
        run = self.get_backtest_run(backtest_run_id=backtest_run_id)
        if run is None:
            return None
        bundle = {"run": run}
        for spec in _MEMBERSHIP_SPECS.values():
            bundle[spec.output_name] = self._list_run_records(
                backtest_run_id=backtest_run_id,
                record_type=spec.record_type,
            )
        bundle["metrics"] = self.list_backtest_metrics(backtest_run_id=backtest_run_id)
        return bundle

    def list_fills(self, *, backtest_run_id: UUID, order_id: UUID | None = None):
        clauses = ["membership.backtest_run_id = %s", "membership.record_type = 'fill'"]
        params: list[object] = [backtest_run_id]
        if order_id is not None:
            clauses.append("child.order_id = %s")
            params.append(order_id)
        return self._fetchall(
            "SELECT child.*, membership.backtest_run_id AS membership_backtest_run_id, membership.deterministic_ordinal AS membership_ordinal FROM execution.fills AS child "
            "JOIN backtesting.backtest_run_memberships AS membership "
            "ON membership.fill_id = child.fill_id WHERE "
            + " AND ".join(clauses)
            + " ORDER BY membership.deterministic_ordinal, child.fill_id",
            tuple(params),
        )

    def list_backtest_events(
        self,
        *,
        backtest_run_id: UUID,
        start_utc: datetime,
        end_utc: datetime,
    ):
        return self._fetchall(
            "SELECT child.*, membership.backtest_run_id AS membership_backtest_run_id, membership.deterministic_ordinal AS membership_ordinal FROM backtesting.backtest_events AS child "
            "JOIN backtesting.backtest_run_memberships AS membership "
            "ON membership.backtest_event_id = child.backtest_event_id "
            "WHERE membership.backtest_run_id = %s "
            "AND membership.record_type = 'backtest_event' "
            "AND child.event_timestamp_utc >= %s AND child.event_timestamp_utc < %s "
            "ORDER BY child.event_timestamp_utc, child.event_priority, child.deterministic_sequence, membership.deterministic_ordinal",
            (backtest_run_id, start_utc, end_utc),
        )

    def list_backtest_metrics(self, *, backtest_run_id: UUID):
        return self._fetchall(
            "SELECT * FROM backtesting.backtest_metrics "
            "WHERE backtest_run_id = %s ORDER BY metric_name, backtest_metric_id",
            (backtest_run_id,),
        )

    def _execute(self, sql: str, params: tuple[object, ...]) -> int:
        cursor = self.connection.cursor()
        try:
            cursor.execute(sql, params)
            return int(getattr(cursor, "rowcount", 0) or 0)
        finally:
            close = getattr(cursor, "close", None)
            if close is not None:
                close()

    def delete_backtest_run(self, *, backtest_run_id: UUID) -> dict[str, int]:
        deleted_records: dict[str, int] = {}
        with self._write_scope():
            for spec in _MEMBERSHIP_SPECS.values():
                self._execute(
                    f"UPDATE {spec.table} AS child SET backtest_run_id = ("
                    "SELECT membership.backtest_run_id "
                    "FROM backtesting.backtest_run_memberships AS membership "
                    f"WHERE membership.{spec.membership_column} = child.{spec.id_column} "
                    "AND membership.backtest_run_id <> %s "
                    "ORDER BY membership.backtest_run_id LIMIT 1) "
                    "WHERE child.backtest_run_id = %s",
                    (backtest_run_id, backtest_run_id),
                )
            deleted_runs = self._execute(
                "DELETE FROM backtesting.backtest_runs WHERE backtest_run_id = %s",
                (backtest_run_id,),
            )
            for record_type in _GARBAGE_COLLECTION_ORDER:
                spec = _MEMBERSHIP_SPECS[record_type]
                deleted_records[record_type] = self._execute(
                    f"DELETE FROM {spec.table} AS child WHERE NOT EXISTS ("
                    "SELECT 1 FROM backtesting.backtest_run_memberships AS membership "
                    f"WHERE membership.{spec.membership_column} = child.{spec.id_column})",
                    (),
                )
        return {"backtest_runs": deleted_runs, **deleted_records}


__all__ = ["Phase5ConflictError", "PostgresPhase5Repository"]
