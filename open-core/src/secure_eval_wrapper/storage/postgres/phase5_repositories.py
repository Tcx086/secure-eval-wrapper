"""Injected-connection, conflict-protected PostgreSQL Phase 5 repositories."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from uuid import UUID

from secure_eval_wrapper.storage.postgres.alpha_signal_base import _PostgresRepositoryBase, _json_param
from secure_eval_wrapper.storage.postgres.phase5_rows import (
    account_snapshot_row, backtest_run_row, cash_ledger_row, equity_row, event_row,
    fill_row, funding_payment_row, metric_row, order_intent_row, order_row,
    position_row, position_snapshot_row, risk_decision_row,
)


class Phase5ConflictError(RuntimeError):
    """A deterministic logical identity already exists with different content."""


class PostgresPhase5Repository(_PostgresRepositoryBase):
    def __init__(self, connection, *, commit_on_write: bool = True) -> None:
        super().__init__(connection, commit_on_write=commit_on_write)
        self._backtest_run_id: UUID | None = None

    def _record(self, *, table: str, id_column: str, row: dict[str, object], logical_where: str, logical_params: Sequence[object], label: str) -> UUID:
        if table != "backtesting.backtest_runs" and "backtest_run_id" in row and self._backtest_run_id is not None:
            row = row | {"backtest_run_id": self._backtest_run_id}
        columns = tuple(row)
        params = tuple(_json_param(row[name]) if name.endswith("_jsonb") else row[name] for name in columns)
        sql = f"INSERT INTO {table} ({', '.join(columns)}) VALUES ({', '.join(['%s'] * len(columns))}) ON CONFLICT DO NOTHING RETURNING {id_column}, record_sha256"
        cursor = self.connection.cursor()
        try:
            cursor.execute(sql, params)
            stored = cursor.fetchone()
            if stored is None:
                cursor.execute(f"SELECT {id_column}, record_sha256 FROM {table} WHERE {logical_where}", tuple(logical_params))
                stored = cursor.fetchone()
                if stored is None:
                    raise Phase5ConflictError(f"{label} logical identity conflicted with an inaccessible row")
                if str(stored[1]) != str(row["record_sha256"]):
                    raise Phase5ConflictError(f"stored {label} record hash differs")
            result = stored[0] if isinstance(stored[0], UUID) else UUID(str(stored[0]))
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
        return result

    def record_backtest_run(self, value):
        row = backtest_run_row(value)
        result = self._record(table="backtesting.backtest_runs", id_column="backtest_run_id", row=row, logical_where="backtest_run_id = %s", logical_params=(row["backtest_run_id"],), label="backtest run")
        self._backtest_run_id = value.backtest_run_id
        return result

    def record_order_intent(self, value):
        row = order_intent_row(value)
        return self._record(table="execution.order_intents", id_column="order_intent_id", row=row, logical_where="run_id = %s AND signal_id = %s AND series_identity_sha256 = %s AND event_timestamp_utc = %s", logical_params=(row["run_id"], row["signal_id"], row["series_identity_sha256"], row["event_timestamp_utc"]), label="order intent")

    def record_risk_decision(self, value):
        row = risk_decision_row(value)
        return self._record(table="execution.risk_decisions", id_column="risk_decision_id", row=row, logical_where="order_intent_id = %s AND stage = %s AND decision_timestamp_utc = %s", logical_params=(row["order_intent_id"], row["stage"], row["decision_timestamp_utc"]), label="risk decision")

    def record_order(self, value):
        row = order_row(value)
        return self._record(table="execution.orders", id_column="order_id", row=row, logical_where="order_intent_id = %s", logical_params=(row["order_intent_id"],), label="order")

    def record_fill(self, value):
        row = fill_row(value)
        return self._record(table="execution.fills", id_column="fill_id", row=row, logical_where="order_id = %s", logical_params=(row["order_id"],), label="fill")

    def upsert_position(self, value):
        row = position_row(value)
        return self._record(table="execution.positions", id_column="position_id", row=row, logical_where="run_id = %s AND account_ref = %s AND series_identity_sha256 = %s", logical_params=(row["run_id"], row["account_ref"], row["series_identity_sha256"]), label="position")

    def record_position_snapshot(self, value):
        row = position_snapshot_row(value)
        return self._record(table="execution.position_snapshots", id_column="position_snapshot_id", row=row, logical_where="run_id = %s AND account_ref = %s AND position_id = %s AND snapshot_at_utc = %s AND snapshot_kind = %s AND source_event_id = %s AND logical_sequence = %s", logical_params=(row["run_id"], row["account_ref"], row["position_id"], row["snapshot_at_utc"], row["snapshot_kind"], row["source_event_id"], row["logical_sequence"]), label="position snapshot")

    def record_funding_payment(self, value):
        row = funding_payment_row(value)
        return self._record(table="execution.funding_payments", id_column="funding_payment_id", row=row, logical_where="run_id = %s AND funding_rate_id = %s AND series_identity_sha256 = %s", logical_params=(row["run_id"], row["funding_rate_id"], row["series_identity_sha256"]), label="funding payment")

    def record_cash_ledger_entry(self, value):
        row = cash_ledger_row(value)
        return self._record(table="execution.cash_ledger_entries", id_column="cash_ledger_entry_id", row=row, logical_where="run_id = %s AND ledger_sequence = %s", logical_params=(row["run_id"], row["ledger_sequence"]), label="cash ledger entry")

    def record_account_snapshot(self, value):
        row = account_snapshot_row(value)
        return self._record(table="execution.account_snapshots", id_column="account_snapshot_id", row=row, logical_where="run_id = %s AND account_ref = %s AND snapshot_at_utc = %s", logical_params=(row["run_id"], row["account_ref"], row["snapshot_at_utc"]), label="account snapshot")

    def record_backtest_event(self, value):
        row = event_row(value)
        backtest_run_id = self._backtest_run_id or row["backtest_run_id"]
        return self._record(table="backtesting.backtest_events", id_column="backtest_event_id", row=row, logical_where="backtest_run_id = %s AND deterministic_sequence = %s", logical_params=(backtest_run_id, row["deterministic_sequence"]), label="backtest event")

    def record_equity_curve_point(self, value):
        row = equity_row(value)
        backtest_run_id = self._backtest_run_id or row["backtest_run_id"]
        return self._record(table="backtesting.equity_curves", id_column="equity_curve_id", row=row, logical_where="backtest_run_id = %s AND timestamp_utc = %s", logical_params=(backtest_run_id, row["timestamp_utc"]), label="equity point")

    def record_backtest_metric(self, value):
        row = metric_row(value)
        backtest_run_id = self._backtest_run_id or row["backtest_run_id"]
        return self._record(table="backtesting.backtest_metrics", id_column="backtest_metric_id", row=row, logical_where="backtest_run_id = %s AND metric_name = %s", logical_params=(backtest_run_id, row["metric_name"]), label="backtest metric")

    def list_fills(self, *, order_id: UUID):
        return self._fetchall("SELECT * FROM execution.fills WHERE order_id = %s ORDER BY filled_at_utc, fill_id", (order_id,))

    def list_backtest_events(self, *, backtest_run_id: UUID, start_utc: datetime, end_utc: datetime):
        return self._fetchall("SELECT * FROM backtesting.backtest_events WHERE backtest_run_id = %s AND event_timestamp_utc >= %s AND event_timestamp_utc < %s ORDER BY event_timestamp_utc, event_priority, deterministic_sequence", (backtest_run_id, start_utc, end_utc))

    def list_backtest_metrics(self, *, backtest_run_id: UUID):
        return self._fetchall("SELECT * FROM backtesting.backtest_metrics WHERE backtest_run_id = %s ORDER BY metric_name", (backtest_run_id,))


__all__ = ["Phase5ConflictError", "PostgresPhase5Repository"]
