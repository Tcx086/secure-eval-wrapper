"""Complete PostgreSQL mappings for immutable Phase 5 domain records."""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from decimal import Decimal

from secure_eval_wrapper.data_collection.hashing import sha256_payload
from secure_eval_wrapper.execution.models import PositionSnapshot, PositionState, PositionValuationStatus


def _identity(identity) -> dict[str, object]:
    return {
        "provider_name": identity.provider_name,
        "exchange_name": identity.exchange,
        "provider_instrument_id": identity.provider_instrument_id,
        "canonical_symbol": identity.canonical_symbol,
        "instrument_type": identity.instrument_type.value,
        "timeframe": identity.timeframe,
        "settlement_asset": identity.settlement_asset,
        "series_identity_sha256": identity.series_identity_sha256,
    }


def _lineage_sha256(label: str, record_id) -> str:
    return hashlib.sha256(f"{label}|{record_id}".encode("utf-8")).hexdigest()


def order_intent_row(value):
    return {
        "order_intent_id": value.order_intent_id, "signal_id": value.signal_id,
        "run_id": value.run_id, "backtest_run_id": value.run_id,
        "symbol": value.series_identity.canonical_symbol, **_identity(value.series_identity),
        "side": value.side.value, "order_type": value.order_type.value,
        "quantity": value.quantity, "limit_price": value.limit_price,
        "intent_status": value.status.value, "risk_summary_jsonb": {},
        "event_timestamp_utc": value.event_timestamp_utc, "execution_mode": "backtest",
        "accounting_mode": value.accounting_mode.value, "target_quantity": value.target_quantity,
        "current_quantity": value.current_quantity, "delta_quantity": value.delta_quantity,
        "reference_price": value.reference_price, "stop_price": value.stop_price,
        "time_in_force": value.time_in_force.value, "config_sha256": value.config_sha256,
        "data_sha256": value.data_sha256, "implementation_code_sha256": value.implementation_code_sha256,
        "repository_commit_sha": value.repository_commit_sha, "record_sha256": value.record_sha256,
        "parent_ids": list(value.parent_ids), "provenance_jsonb": dict(value.provenance),
        "created_at_utc": value.event_timestamp_utc,
    }


def risk_decision_row(value):
    return {
        "risk_decision_id": value.risk_decision_id, "run_id": value.run_id,
        "backtest_run_id": value.run_id, "order_intent_id": value.order_intent_id,
        "order_id": value.order_id, **_identity(value.series_identity),
        "decision_timestamp_utc": value.decision_timestamp_utc, "stage": value.stage.value,
        "decision_status": value.status.value, "relevant_limit": value.relevant_limit,
        "observed_value": value.observed_value, "configured_limit": value.configured_limit,
        "reason_code": value.reason_code, "explanation": value.explanation,
        "config_sha256": value.config_sha256, "record_sha256": value.record_sha256,
        "parent_ids": list(value.parent_ids), "provenance_jsonb": dict(value.provenance),
    }


def order_row(value):
    return {
        "order_id": value.order_id, "order_intent_id": value.order_intent_id,
        "broker_order_ref": f"sim-{value.order_id}", "run_id": value.run_id,
        "backtest_run_id": value.run_id, "symbol": value.series_identity.canonical_symbol,
        **_identity(value.series_identity), "side": value.side.value,
        "order_type": value.order_type.value, "order_status": value.status.value,
        "reject_reason": None if value.reject_reason is None else value.reject_reason.value,
        "submitted_at_utc": value.submitted_at_utc, "acknowledged_at_utc": value.submitted_at_utc,
        "broker_payload_jsonb": {"simulated": True}, "quantity": value.quantity,
        "limit_price": value.limit_price, "stop_price": value.stop_price,
        "accounting_mode": value.accounting_mode.value, "time_in_force": value.time_in_force.value,
        "triggered_at_utc": value.triggered_at_utc, "activation_reason": value.activation_reason,
        "config_sha256": value.config_sha256, "record_sha256": value.record_sha256,
        "parent_ids": list(value.parent_ids), "provenance_jsonb": dict(value.provenance),
    }


def order_lineage_row(value):
    row = order_row(value)
    row.update(
        order_status="submitted",
        reject_reason=None,
        triggered_at_utc=None,
        activation_reason=None,
        provenance_jsonb={},
        record_sha256=_lineage_sha256("phase5-order-lineage-v1", value.order_id),
    )
    return row


def order_state_row(value, *, backtest_run_id, deterministic_ordinal: int):
    return {
        "backtest_run_id": backtest_run_id,
        "order_id": value.order_id,
        "deterministic_ordinal": deterministic_ordinal,
        "order_status": value.status.value,
        "triggered_at_utc": value.triggered_at_utc,
        "activation_reason": value.activation_reason,
        "reject_reason": None if value.reject_reason is None else value.reject_reason.value,
        "state_provenance_jsonb": dict(value.provenance),
        "final_record_sha256": value.record_sha256,
    }


def fill_row(value):
    return {
        "fill_id": value.fill_id, "order_id": value.order_id,
        "broker_fill_ref": f"sim-{value.fill_id}", "symbol": value.series_identity.canonical_symbol,
        "side": value.side.value, "filled_at_utc": value.filled_at_utc,
        "price": value.price, "quantity": value.quantity, "fee_amount": value.fee_amount,
        "fee_asset": value.fee_currency, "liquidity_flag": value.liquidity_flag.value,
        "fill_payload_jsonb": {"fill_reason": value.fill_reason}, "run_id": value.run_id,
        "backtest_run_id": value.run_id, "order_intent_id": value.order_intent_id,
        **_identity(value.series_identity), "accounting_mode": value.accounting_mode.value,
        "base_price": value.base_price, "notional": value.notional,
        "slippage_amount": value.slippage_amount, "slippage_bps": value.slippage_bps,
        "fill_reason": value.fill_reason, "config_sha256": value.config_sha256,
        "record_sha256": value.record_sha256, "parent_ids": list(value.parent_ids),
        "provenance_jsonb": dict(value.provenance),
    }


def position_row(value: PositionState):
    return {
        "position_id": value.position_id, "run_id": value.run_id, "backtest_run_id": value.run_id,
        "account_ref": value.account_ref, "symbol": value.series_identity.canonical_symbol,
        "quantity": value.quantity, "average_entry_price": value.average_entry_price,
        "realized_pnl": value.realized_pnl, "unrealized_pnl": 0,
        "source_fill_ids": list(value.source_fill_ids), "updated_at_utc": value.updated_at_utc,
        **_identity(value.series_identity), "accounting_mode": value.accounting_mode.value,
        "mark_price": None, "config_sha256": value.config_sha256,
        "record_sha256": value.record_sha256,
    }


def position_lineage_row(value: PositionState):
    row = position_row(value)
    row.update(
        quantity=0,
        average_entry_price=None,
        realized_pnl=0,
        unrealized_pnl=0,
        source_fill_ids=[],
        updated_at_utc=datetime(1970, 1, 1, tzinfo=timezone.utc),
        mark_price=None,
        record_sha256=_lineage_sha256("phase5-position-lineage-v1", value.position_id),
    )
    return row


def position_state_row(
    value: PositionState,
    *,
    backtest_run_id,
    deterministic_ordinal: int,
    final_snapshot: PositionSnapshot | None = None,
):
    if final_snapshot is not None and final_snapshot.position_id != value.position_id:
        raise ValueError("final position snapshot does not match position lineage")
    quantity = value.quantity if final_snapshot is None else final_snapshot.quantity
    average_entry_price = value.average_entry_price if final_snapshot is None else final_snapshot.average_entry_price
    realized_pnl = value.realized_pnl if final_snapshot is None else final_snapshot.realized_pnl
    mark_price = None if final_snapshot is None else final_snapshot.mark_price
    unrealized_pnl = Decimal("0") if final_snapshot is None else final_snapshot.unrealized_pnl
    valuation_at_utc = value.updated_at_utc if final_snapshot is None else final_snapshot.snapshot_at_utc
    mark_source = None if final_snapshot is None or final_snapshot.mark_source is None else final_snapshot.mark_source.value
    stale_mark_age_seconds = None if final_snapshot is None else final_snapshot.stale_mark_age_seconds
    source_position_snapshot_id = None if final_snapshot is None else final_snapshot.position_snapshot_id
    valuation_status = (
        PositionValuationStatus.FLAT
        if quantity == 0
        else PositionValuationStatus.MARKED
        if mark_price is not None
        else PositionValuationStatus.UNMARKED
    )
    if valuation_status is PositionValuationStatus.UNMARKED:
        unrealized_pnl = Decimal("0")
        mark_source = None
        stale_mark_age_seconds = None
    state_payload = {
        "backtest_run_id": backtest_run_id,
        "position_id": value.position_id,
        "account_ref": value.account_ref,
        "series_identity_sha256": value.series_identity.series_identity_sha256,
        "deterministic_ordinal": deterministic_ordinal,
        "accounting_mode": value.accounting_mode,
        "quantity": quantity,
        "average_entry_price": average_entry_price,
        "realized_pnl": realized_pnl,
        "unrealized_pnl": unrealized_pnl,
        "source_fill_ids": value.source_fill_ids,
        "updated_at_utc": value.updated_at_utc,
        "mark_price": mark_price,
        "valuation_at_utc": valuation_at_utc,
        "mark_source": mark_source,
        "stale_mark_age_seconds": stale_mark_age_seconds,
        "valuation_status": valuation_status,
        "source_position_snapshot_id": source_position_snapshot_id,
        "config_sha256": value.config_sha256,
    }
    return {
        **state_payload,
        "accounting_mode": value.accounting_mode.value,
        "source_fill_ids": list(value.source_fill_ids),
        "valuation_status": valuation_status.value,
        "final_record_sha256": sha256_payload(state_payload),
    }

def position_snapshot_row(value):
    return {
        "position_snapshot_id": value.position_snapshot_id, "run_id": value.run_id,
        "backtest_run_id": value.run_id, "position_id": value.position_id,
        "account_ref": value.account_ref, "source_fill_id": value.source_fill_id,
        "source_event_id": value.source_event_id, "logical_sequence": value.logical_sequence,
        "snapshot_kind": value.snapshot_kind.value,
        "mark_source": None if value.mark_source is None else value.mark_source.value,
        **_identity(value.series_identity),
        "accounting_mode": value.accounting_mode.value, "snapshot_at_utc": value.snapshot_at_utc,
        "quantity": value.quantity, "average_entry_price": value.average_entry_price,
        "mark_price": value.mark_price, "realized_pnl": value.realized_pnl,
        "unrealized_pnl": value.unrealized_pnl, "stale_mark_age_seconds": value.stale_mark_age_seconds,
        "config_sha256": value.config_sha256, "record_sha256": value.record_sha256,
        "parent_ids": list(value.parent_ids),
    }


def funding_payment_row(value):
    return {
        "funding_payment_id": value.funding_payment_id, "run_id": value.run_id,
        "backtest_run_id": value.run_id, "funding_rate_id": value.funding_rate_id,
        **_identity(value.series_identity), "funding_timestamp_utc": value.funding_timestamp_utc,
        "signed_quantity": value.signed_quantity, "mark_price": value.mark_price,
        "funding_rate": value.funding_rate, "cash_flow": value.cash_flow,
        "funding_interval": value.funding_interval, "funding_interval_source": value.funding_interval_source,
        "source_observation_ids": list(value.source_observation_ids), "config_sha256": value.config_sha256,
        "record_sha256": value.record_sha256, "parent_ids": list(value.parent_ids),
        "provenance_jsonb": dict(value.provenance),
    }


def cash_ledger_row(value):
    return {
        "cash_ledger_entry_id": value.cash_ledger_entry_id, "run_id": value.run_id,
        "backtest_run_id": value.run_id, "event_timestamp_utc": value.event_timestamp_utc,
        "ledger_sequence": value.ledger_sequence,
        "entry_type": value.entry_type.value, "amount": value.amount,
        "balance_after": value.balance_after, "currency": value.currency,
        "series_identity_sha256": None if value.series_identity is None else value.series_identity.series_identity_sha256,
        "fill_id": value.fill_id, "funding_payment_id": value.funding_payment_id,
        "config_sha256": value.config_sha256, "record_sha256": value.record_sha256,
        "parent_ids": list(value.parent_ids),
    }


def account_snapshot_row(value):
    return {
        "account_snapshot_id": value.account_snapshot_id, "run_id": value.run_id,
        "backtest_run_id": value.run_id, "account_ref": value.account_ref,
        "snapshot_at_utc": value.snapshot_at_utc, "equity": value.equity, "cash": value.cash,
        "margin_used": 0, "balances_jsonb": {"base_currency_only": True},
        "classification": "public_synthetic", "gross_exposure": value.gross_exposure,
        "net_exposure": value.net_exposure, "realized_pnl": value.realized_pnl,
        "unrealized_pnl": value.unrealized_pnl, "total_fees": value.total_fees,
        "total_funding": value.total_funding, "stale_mark_count": value.stale_mark_count,
        "config_sha256": value.config_sha256, "record_sha256": value.record_sha256,
        "parent_ids": list(value.parent_ids),
    }


def backtest_run_row(value):
    return {
        "backtest_run_id": value.backtest_run_id, "run_id": value.run_id,
        "signal_run_id": value.signal_run_id, "execution_model_sha256": value.config_sha256,
        "config_sha256": value.config_sha256, "started_at_utc": value.started_at_utc,
        "completed_at_utc": value.completed_at_utc, "status": value.status.value,
        "metadata_jsonb": dict(value.metadata), "initial_cash": value.initial_cash,
        "base_currency": value.base_currency, "fee_currency": value.fee_currency, "account_ref": value.account_ref,
        "data_sha256": value.data_sha256,
        "implementation_code_sha256": value.implementation_code_sha256,
        "repository_commit_sha": value.repository_commit_sha, "record_sha256": value.record_sha256,
    }


def event_row(value):
    identity = {} if value.series_identity is None else _identity(value.series_identity)
    if value.series_identity is None:
        identity = {key: None for key in ("provider_name", "exchange_name", "provider_instrument_id", "canonical_symbol", "instrument_type", "timeframe", "settlement_asset", "series_identity_sha256")}
    return {
        "backtest_event_id": value.execution_event_id, "backtest_run_id": value.run_id,
        "deterministic_sequence": value.sequence, "event_timestamp_utc": value.event_timestamp_utc,
        "event_priority": value.priority, "event_type": value.event_type.value, **identity,
        "parent_record_id": value.parent_record_id, "event_sha256": value.event_sha256,
        "config_sha256": value.config_sha256, "record_sha256": value.record_sha256,
        "metadata_jsonb": dict(value.metadata),
    }


def equity_row(value):
    return {
        "equity_curve_id": value.equity_curve_id, "backtest_run_id": value.run_id,
        "timestamp_utc": value.timestamp_utc, "equity": value.equity, "cash": value.cash,
        "drawdown": value.drawdown_amount, "exposure": value.gross_exposure,
        "details_jsonb": {}, "drawdown_fraction": value.drawdown_fraction,
        "gross_exposure": value.gross_exposure, "net_exposure": value.net_exposure,
        "stale_mark_count": value.stale_mark_count, "config_sha256": value.config_sha256,
        "record_sha256": value.record_sha256,
    }


def metric_row(value):
    return {
        "backtest_metric_id": value.backtest_metric_id, "backtest_run_id": value.backtest_run_id,
        "metric_name": value.name, "metric_value": value.value, "metric_unit": value.unit,
        "details_jsonb": dict(value.details), "metric_status": value.status.value,
        "config_sha256": value.config_sha256, "record_sha256": value.record_sha256,
    }


__all__ = [name for name in tuple(globals()) if name.endswith("_row")]
