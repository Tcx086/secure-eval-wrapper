"""Shared Phase 7 price authority applied to Phase 8 live risk decisions."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from secure_eval_wrapper.paper.reservations import select_risk_price

from .models import LiveKillState, LiveReconciliationStatus, LiveRiskDecision


@dataclass(frozen=True)
class LiveRiskState:
    position_notional: Decimal
    gross_exposure: Decimal
    net_exposure: Decimal
    daily_submitted_notional: Decimal
    approval_consumed_notional: Decimal
    open_order_count: int
    reconciliation_status: LiveReconciliationStatus
    kill_switch_state: LiveKillState


def evaluate_live_risk(*, intent, market_evidence, configuration, state: LiveRiskState, approval, evaluated_at_utc: datetime) -> LiveRiskDecision:
    selection = select_risk_price(intent, market_evidence, maximum_adverse_slippage_bps=configuration.maximum_adverse_slippage_bps)
    risk = selection.risk_notional
    signed = risk if intent.side.value == "buy" else -risk
    reasons = []
    if intent.series_identity.provider_instrument_id not in configuration.allowed_instruments:
        reasons.append("instrument_not_allowed")
    if intent.order_type.value != "limit": reasons.append("only_limit_orders_allowed")
    if risk > configuration.maximum_order_notional: reasons.append("maximum_order_notional")
    if state.position_notional + signed > configuration.maximum_position_notional or state.position_notional + signed < 0:
        reasons.append("maximum_position_notional_or_spot_short")
    if state.gross_exposure + risk > configuration.maximum_gross_exposure: reasons.append("maximum_gross_exposure")
    if abs(state.net_exposure + signed) > configuration.maximum_net_exposure: reasons.append("maximum_net_exposure")
    if state.daily_submitted_notional + risk > configuration.maximum_daily_submitted_notional: reasons.append("maximum_daily_submitted_notional")
    if state.approval_consumed_notional + risk > approval.maximum_total_approved_notional: reasons.append("approval_notional")
    if state.open_order_count + 1 > configuration.maximum_open_order_count: reasons.append("maximum_open_order_count")
    if selection.price_deviation_bps > configuration.maximum_reference_price_deviation_bps: reasons.append("maximum_reference_price_deviation")
    evidence_reasons = market_evidence.rejection_reasons(series_identity=intent.series_identity, at_utc=evaluated_at_utc, maximum_age_seconds=configuration.market_data_freshness_seconds, expected_currency=intent.series_identity.settlement_asset, allow_fixture=False)
    reasons.extend(evidence_reasons)
    if state.reconciliation_status is not LiveReconciliationStatus.RECONCILED: reasons.append("reconciliation_blocked")
    if state.kill_switch_state is not LiveKillState.ARMED: reasons.append("kill_switch_not_armed")
    reasons = tuple(dict.fromkeys(reasons))
    return LiveRiskDecision(intent.order_intent_id, not reasons, reasons, selection.market_evidence_price, selection.risk_reference_price, selection.worst_case_order_price, selection.risk_notional, selection.reservation_notional, selection.price_deviation_bps, selection.price_source_sha256, selection.price_calculator_version, evaluated_at_utc)


__all__ = ["LiveRiskState", "evaluate_live_risk"]
