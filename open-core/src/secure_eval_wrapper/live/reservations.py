"""Single typed Spot reservation authority for guarded-live dry-run."""
from __future__ import annotations

from decimal import Decimal

from .authorities import LiveReservationAuthority


CALCULATOR_VERSION = "phase8a-spot-reservation-v2"


def calculate_live_reservation(*, intent, risk_decision, maximum_fee_bps: Decimal) -> LiveReservationAuthority:
    risk_notional = risk_decision.risk_notional
    fee = risk_notional * maximum_fee_bps / Decimal(10000)
    base, _, quote = intent.series_identity.provider_instrument_id.partition("-")
    if not base or not quote:
        raise ValueError("Spot instrument must expose base and quote assets")
    if intent.side.value == "buy":
        currency = quote
        amount = intent.quantity * risk_decision.worst_case_order_price + fee
        policy = "reserve_quote_notional_plus_maximum_fee"
        reservation_notional = amount
    else:
        currency = base
        amount = intent.quantity
        policy = "reserve_base_quantity_fee_deducted_from_quote_proceeds"
        reservation_notional = risk_notional
    return LiveReservationAuthority(
        intent.live_run_id,
        intent.order_intent_id,
        currency,
        amount,
        amount,
        intent.quantity,
        intent.quantity,
        risk_decision.worst_case_order_price,
        maximum_fee_bps,
        fee,
        policy,
        risk_notional,
        reservation_notional,
        CALCULATOR_VERSION,
        {
            "intent": intent.record_hash,
            "market": intent.market_evidence_hash,
            "account": intent.account_snapshot_hash,
            "reconciliation": intent.reconciliation_hash,
            "risk": risk_decision.record_hash,
        },
    )


__all__ = ["CALCULATOR_VERSION", "calculate_live_reservation"]
