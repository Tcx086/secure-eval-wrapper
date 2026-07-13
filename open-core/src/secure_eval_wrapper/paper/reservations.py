"""Shared conservative price, risk-notional, and reservation authority for Phase 7."""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from secure_eval_wrapper.execution.models import AccountingMode, OrderSide, OrderType

CALCULATOR_VERSION = "phase7-reservation-v2"
PRICE_CALCULATOR_VERSION = "phase7-price-authority-v1"
DEFAULT_MAXIMUM_ADVERSE_SLIPPAGE_BPS = Decimal("200")


@dataclass(frozen=True)
class PriceSelection:
    market_evidence_price: Decimal
    risk_reference_price: Decimal
    worst_case_order_price: Decimal
    risk_notional: Decimal
    reservation_notional: Decimal
    price_deviation_bps: Decimal
    price_source_sha256: str
    price_calculator_version: str = PRICE_CALCULATOR_VERSION


@dataclass(frozen=True)
class ReservationRequirement:
    currency: str
    amount: Decimal
    quantity: Decimal
    reserve_price: Decimal | None
    maximum_fee_bps: Decimal
    maximum_adverse_slippage_bps: Decimal
    calculator_version: str = CALCULATOR_VERSION
    risk_notional: Decimal | None = None
    reservation_notional: Decimal | None = None


@dataclass(frozen=True)
class ReservationRemainder:
    amount: Decimal
    quantity: Decimal
    amount_consumed: Decimal
    quantity_consumed: Decimal


def _assets(identity) -> tuple[str, str]:
    parts = identity.canonical_symbol.replace("/", "-").split("-")
    if len(parts) < 2:
        raise ValueError("Spot symbol must identify base and quote assets")
    return parts[0].upper(), parts[1].upper()


def select_risk_price(intent, evidence, *, maximum_adverse_slippage_bps: Decimal = DEFAULT_MAXIMUM_ADVERSE_SLIPPAGE_BPS) -> PriceSelection:
    """Select one conservative absolute price/notional used by every risk boundary."""
    slippage_bps = Decimal(maximum_adverse_slippage_bps)
    if slippage_bps < 0:
        raise ValueError("maximum adverse slippage must be non-negative")
    raw_market_price = getattr(evidence, "price", None)
    # Legacy evidence is accepted only as explicitly classified internal fixture evidence.
    if raw_market_price is None:
        if getattr(evidence, "source_kind", None) != "fixture":
            raise ValueError("authoritative market evidence requires a price")
        raw_market_price = intent.reference_price
    market_price = Decimal(raw_market_price)
    if not market_price.is_finite() or market_price <= 0:
        raise ValueError("authoritative market evidence price must be positive")
    risk_reference = market_price * (Decimal(1) + slippage_bps / Decimal(10000))
    candidates = [risk_reference]
    kind = OrderType(intent.order_type)
    if kind in (OrderType.LIMIT, OrderType.STOP_LIMIT) and intent.limit_price is not None:
        candidates.append(Decimal(intent.limit_price))
    if kind in (OrderType.STOP, OrderType.STOP_LIMIT) and intent.stop_price is not None:
        candidates.append(Decimal(intent.stop_price))
    worst_case = max(candidates)
    quantity = abs(Decimal(intent.quantity))
    notional = quantity * worst_case
    deviation = abs(Decimal(intent.reference_price) - market_price) / market_price * Decimal(10000)
    return PriceSelection(
        market_price,
        risk_reference,
        worst_case,
        notional,
        notional,
        deviation,
        evidence.evidence_sha256,
    )


def calculate_reservation(order, *, maximum_fee_bps: Decimal = Decimal("10"), maximum_adverse_slippage_bps: Decimal = DEFAULT_MAXIMUM_ADVERSE_SLIPPAGE_BPS, remaining_quantity: Decimal | None = None) -> ReservationRequirement:
    """Return the sole conservative reservation formula used by Phase 7."""
    fee_bps = Decimal(maximum_fee_bps)
    slippage_bps = Decimal(maximum_adverse_slippage_bps)
    quantity = Decimal(order.quantity if remaining_quantity is None else remaining_quantity)
    if quantity <= 0:
        raise ValueError("reservation quantity must be positive")
    if fee_bps < 0 or slippage_bps < 0:
        raise ValueError("reservation fee and slippage limits must be non-negative")
    selected = getattr(order, "worst_case_order_price", None) if getattr(order, "price_calculator_version", None) is not None else None
    if selected is None:
        reference = Decimal(order.reference_price) * (Decimal(1) + slippage_bps / Decimal(10000))
        prices = [reference]
        order_type = OrderType(order.order_type)
        if order_type in (OrderType.LIMIT, OrderType.STOP_LIMIT) and order.limit_price is not None:
            prices.append(Decimal(order.limit_price))
        if order_type in (OrderType.STOP, OrderType.STOP_LIMIT) and order.stop_price is not None:
            prices.append(Decimal(order.stop_price))
        reserve_price = max(prices)
    else:
        reserve_price = Decimal(selected)
    risk_notional = Decimal(getattr(order, "risk_notional", quantity * reserve_price) or quantity * reserve_price)
    reservation_notional = Decimal(getattr(order, "reservation_notional", risk_notional) or risk_notional)
    base, quote = _assets(order.series_identity)
    side = OrderSide(order.side)
    mode = AccountingMode(order.accounting_mode)
    if mode is AccountingMode.SPOT and side is OrderSide.SELL:
        return ReservationRequirement(base, quantity, quantity, None, fee_bps, slippage_bps, risk_notional=risk_notional, reservation_notional=reservation_notional)
    currency = quote if mode is AccountingMode.SPOT else order.series_identity.settlement_asset.upper()
    amount = quantity * reserve_price
    if side is OrderSide.BUY or mode is AccountingMode.LINEAR_PERPETUAL:
        amount *= Decimal(1) + fee_bps / Decimal(10000)
    return ReservationRequirement(currency, amount, quantity, reserve_price, fee_bps, slippage_bps, risk_notional=risk_notional, reservation_notional=reservation_notional)


def reduce_reservation(*, current_amount: Decimal, current_quantity: Decimal, fill_quantity: Decimal, fill_price: Decimal, fill_fee: Decimal, fee_currency: str, reservation_currency: str, side: OrderSide, accounting_mode: AccountingMode) -> ReservationRemainder:
    """Reduce using actual fill economics, never proportional scaling."""
    amount = Decimal(current_amount)
    quantity = Decimal(current_quantity)
    filled = Decimal(fill_quantity)
    price = Decimal(fill_price)
    fee = Decimal(fill_fee)
    if filled <= 0 or filled > quantity or price <= 0 or fee < 0:
        raise ValueError("invalid fill reservation reduction")
    side = OrderSide(side)
    mode = AccountingMode(accounting_mode)
    reservation_currency = reservation_currency.upper()
    fee_currency = fee_currency.upper()
    if mode is AccountingMode.SPOT and side is OrderSide.BUY:
        if fee_currency != reservation_currency:
            raise ValueError("Spot buy reservation requires quote-currency fee evidence")
        consumed = filled * price + fee
    elif mode is AccountingMode.SPOT:
        consumed = filled + (fee if fee_currency == reservation_currency else Decimal(0))
    else:
        consumed = filled * price + (fee if fee_currency == reservation_currency else Decimal(0))
    if consumed > amount:
        raise ValueError("fill economics exceed durable reservation")
    remaining_quantity = quantity - filled
    remaining_amount = amount - consumed
    if remaining_quantity == 0:
        remaining_amount = Decimal(0)
    return ReservationRemainder(remaining_amount, remaining_quantity, amount - remaining_amount, filled)


__all__ = ["CALCULATOR_VERSION", "PRICE_CALCULATOR_VERSION", "DEFAULT_MAXIMUM_ADVERSE_SLIPPAGE_BPS", "PriceSelection", "ReservationRequirement", "ReservationRemainder", "select_risk_price", "calculate_reservation", "reduce_reservation"]
