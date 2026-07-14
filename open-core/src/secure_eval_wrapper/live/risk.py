"""PostgreSQL-state-driven Phase 8A runtime risk evaluation."""
from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal

from secure_eval_wrapper.paper.reservations import select_risk_price

from .authorities import LiveRuntimeRiskState
from .models import LiveKillState, LiveReconciliationStatus, LiveRiskDecision


def _age_seconds(now: datetime, value: datetime | None) -> Decimal:
    if value is None:
        return Decimal(0)
    return Decimal(str((now - value).total_seconds()))


def evaluate_live_risk(
    *,
    intent,
    market_evidence,
    configuration,
    state: LiveRuntimeRiskState,
    approval,
    approval_consumed_notional: Decimal,
    kill_switch_state: LiveKillState,
    evaluated_at_utc: datetime,
) -> LiveRiskDecision:
    """Pure calculation over state that the repository loaded and locked."""
    if not isinstance(state, LiveRuntimeRiskState):
        raise TypeError("operational live risk requires PostgreSQL-loaded LiveRuntimeRiskState")
    if state.live_run_id != intent.live_run_id or approval.live_run_id != intent.live_run_id:
        raise ValueError("runtime risk authorities belong to different runs")
    selection = select_risk_price(
        intent,
        market_evidence,
        maximum_adverse_slippage_bps=configuration.maximum_adverse_slippage_bps,
    )
    risk = selection.risk_notional
    signed = risk if intent.side.value == "buy" else -risk
    instrument = intent.series_identity.provider_instrument_id
    position = state.positions.get(instrument, {})
    current_position_notional = Decimal(str(position.get("notional", "0")))
    one_minute_ago = evaluated_at_utc - timedelta(minutes=1)
    order_rate = sum(timestamp > one_minute_ago for timestamp in state.order_timestamps_utc)
    cancellation_rate = sum(timestamp > one_minute_ago for timestamp in state.cancellation_timestamps_utc)
    drawdown = state.high_watermark_equity - state.current_equity

    reasons: list[str] = []
    if instrument not in configuration.allowed_instruments: reasons.append("instrument_not_allowed")
    if intent.order_type.value != "limit": reasons.append("only_limit_orders_allowed")
    if risk > configuration.maximum_order_notional: reasons.append("maximum_order_notional")
    projected_position = current_position_notional + signed
    if projected_position > configuration.maximum_position_notional or projected_position < 0: reasons.append("maximum_position_notional_or_spot_short")
    if state.gross_exposure + risk > configuration.maximum_gross_exposure: reasons.append("maximum_gross_exposure")
    if abs(state.net_exposure + signed) > configuration.maximum_net_exposure: reasons.append("maximum_net_exposure")
    if state.daily_submitted_notional + risk > configuration.maximum_daily_submitted_notional: reasons.append("maximum_daily_submitted_notional")
    if state.daily_realized_pnl < -configuration.maximum_daily_realized_loss: reasons.append("maximum_daily_realized_loss")
    if drawdown > configuration.maximum_drawdown: reasons.append("maximum_drawdown")
    if state.open_order_count + 1 > configuration.maximum_open_order_count: reasons.append("maximum_open_order_count")
    if order_rate + 1 > configuration.maximum_orders_per_minute: reasons.append("maximum_orders_per_minute")
    if cancellation_rate > configuration.maximum_cancellations_per_minute: reasons.append("maximum_cancellations_per_minute")
    if (evaluated_at_utc - state.latest_market_data_at_utc).total_seconds() > configuration.market_data_freshness_seconds: reasons.append("stale_market_data")
    if (evaluated_at_utc - state.latest_account_snapshot_at_utc).total_seconds() > configuration.account_snapshot_freshness_seconds: reasons.append("stale_account_snapshot")
    if (evaluated_at_utc - state.latest_reconciliation_at_utc).total_seconds() > configuration.reconciliation_freshness_seconds: reasons.append("stale_reconciliation")
    if state.latest_reconciliation_status is not LiveReconciliationStatus.RECONCILED: reasons.append("reconciliation_blocked")
    if _age_seconds(evaluated_at_utc, state.oldest_unknown_order_at_utc) > configuration.maximum_unknown_order_duration_seconds: reasons.append("unknown_order_age")
    if _age_seconds(evaluated_at_utc, state.oldest_unacknowledged_order_at_utc) > configuration.maximum_unacknowledged_order_duration_seconds: reasons.append("unacknowledged_order_age")
    if (evaluated_at_utc - state.run_started_at_utc).total_seconds() > configuration.maximum_run_duration_seconds: reasons.append("maximum_run_duration")
    if state.clock_skew_seconds > configuration.maximum_clock_skew_seconds: reasons.append("maximum_clock_skew")
    if state.transport_failure_count >= configuration.maximum_transport_failures: reasons.append("maximum_transport_failures")
    if approval_consumed_notional + risk > approval.maximum_total_approved_notional: reasons.append("approval_notional")
    if kill_switch_state is not LiveKillState.ARMED: reasons.append("kill_switch_not_armed")
    if selection.price_deviation_bps > configuration.maximum_reference_price_deviation_bps: reasons.append("maximum_reference_price_deviation")

    reasons.extend(market_evidence.rejection_reasons(
        series_identity=intent.series_identity,
        at_utc=evaluated_at_utc,
        maximum_age_seconds=configuration.market_data_freshness_seconds,
        expected_currency=intent.series_identity.settlement_asset,
        allow_fixture=False,
    ))
    reasons = list(dict.fromkeys(reasons))
    maximum_fee_amount = risk * configuration.maximum_fee_bps / Decimal(10000)
    reservation_notional = risk + maximum_fee_amount if intent.side.value == "buy" else risk
    return LiveRiskDecision(
        intent.order_intent_id,
        not reasons,
        tuple(reasons),
        selection.market_evidence_price,
        selection.risk_reference_price,
        selection.worst_case_order_price,
        selection.risk_notional,
        reservation_notional,
        selection.price_deviation_bps,
        selection.price_source_sha256,
        "phase8a-spot-risk-reservation-v2",
        evaluated_at_utc,
    )


__all__ = ["LiveRuntimeRiskState", "evaluate_live_risk"]
