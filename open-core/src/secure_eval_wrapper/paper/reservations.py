"""Shared reservation authority for durable risk, accounting, and paper venues.

Every operational component calls this module instead of maintaining a private
reservation approximation.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from secure_eval_wrapper.execution.models import AccountingMode, OrderSide, OrderType

CALCULATOR_VERSION = "phase7-reservation-v1"
DEFAULT_MAXIMUM_ADVERSE_SLIPPAGE_BPS = Decimal("200")


@dataclass(frozen=True)
class ReservationRequirement:
    currency: str
    amount: Decimal
    quantity: Decimal
    reserve_price: Decimal | None
    maximum_fee_bps: Decimal
    maximum_adverse_slippage_bps: Decimal
    calculator_version: str = CALCULATOR_VERSION


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


def calculate_reservation(order, *, maximum_fee_bps: Decimal = Decimal("10"), maximum_adverse_slippage_bps: Decimal = DEFAULT_MAXIMUM_ADVERSE_SLIPPAGE_BPS, remaining_quantity: Decimal | None = None) -> ReservationRequirement:
    """Return the sole conservative reservation formula used by Phase 7."""
    fee_bps = Decimal(maximum_fee_bps)
    slippage_bps = Decimal(maximum_adverse_slippage_bps)
    quantity = Decimal(order.quantity if remaining_quantity is None else remaining_quantity)
    if quantity <= 0:
        raise ValueError("reservation quantity must be positive")
    if fee_bps < 0 or slippage_bps < 0:
        raise ValueError("reservation fee and slippage limits must be non-negative")
    reference = Decimal(order.reference_price) * (Decimal(1) + slippage_bps / Decimal(10000))
    prices = [reference]
    order_type = OrderType(order.order_type)
    if order_type in (OrderType.LIMIT, OrderType.STOP_LIMIT) and order.limit_price is not None:
        prices.append(Decimal(order.limit_price))
    if order_type in (OrderType.STOP, OrderType.STOP_LIMIT) and order.stop_price is not None:
        prices.append(Decimal(order.stop_price))
    reserve_price = max(prices)
    base, quote = _assets(order.series_identity)
    side = OrderSide(order.side)
    mode = AccountingMode(order.accounting_mode)
    if mode is AccountingMode.SPOT and side is OrderSide.SELL:
        return ReservationRequirement(base, quantity, quantity, None, fee_bps, slippage_bps)
    currency = quote if mode is AccountingMode.SPOT else order.series_identity.settlement_asset.upper()
    amount = quantity * reserve_price
    if side is OrderSide.BUY or mode is AccountingMode.LINEAR_PERPETUAL:
        amount *= Decimal(1) + fee_bps / Decimal(10000)
    return ReservationRequirement(currency, amount, quantity, reserve_price, fee_bps, slippage_bps)


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


__all__ = ["CALCULATOR_VERSION", "DEFAULT_MAXIMUM_ADVERSE_SLIPPAGE_BPS", "ReservationRequirement", "ReservationRemainder", "calculate_reservation", "reduce_reservation"]