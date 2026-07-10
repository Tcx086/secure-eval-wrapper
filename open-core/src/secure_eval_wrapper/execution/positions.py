"""Exact Spot and linear-perpetual position transitions."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from secure_eval_wrapper.alpha.identity import SeriesIdentity
from secure_eval_wrapper.execution.models import AccountingMode, Fill, PositionState


@dataclass(frozen=True)
class PositionTransition:
    state: PositionState
    realized_pnl_delta: Decimal
    closed_quantity: Decimal
    reversed: bool


def empty_position(
    *, run_id,
    series_identity: SeriesIdentity,
    accounting_mode: AccountingMode,
    timestamp_utc: datetime,
    config_sha256: str,
    account_ref: str = "public-simulation",
) -> PositionState:
    return PositionState(run_id, account_ref, series_identity, accounting_mode, Decimal(0), None, Decimal(0), timestamp_utc, config_sha256)


def apply_fill_to_position(current: PositionState, fill: Fill) -> PositionTransition:
    if fill.fill_id in current.source_fill_ids:
        raise ValueError("the same fill cannot be applied twice")
    if current.series_identity.series_identity_sha256 != fill.series_identity.series_identity_sha256:
        raise ValueError("fill and position series identities differ")
    if current.accounting_mode is not fill.accounting_mode:
        raise ValueError("fill and position accounting modes differ")
    signed = fill.quantity * fill.side.sign
    old_q = current.quantity
    new_q = old_q + signed
    old_avg = current.average_entry_price
    realized_delta = Decimal(0)
    closed = Decimal(0)
    reversed_position = False

    if fill.accounting_mode is AccountingMode.SPOT:
        if new_q < 0:
            raise ValueError("Spot fill would create negative inventory")
        if signed > 0:
            total_cost = (old_q * (old_avg or Decimal(0))) + (signed * fill.price)
            new_avg = total_cost / new_q
        else:
            closed = -signed
            if old_avg is None or closed > old_q:
                raise ValueError("Spot sell exceeds available inventory")
            realized_delta = closed * (fill.price - old_avg)
            new_avg = None if new_q == 0 else old_avg
    elif old_q == 0 or old_q * signed > 0:
        new_avg = fill.price if old_q == 0 else ((abs(old_q) * (old_avg or Decimal(0))) + (abs(signed) * fill.price)) / abs(new_q)
    else:
        closed = min(abs(old_q), abs(signed))
        if old_avg is None:
            raise ValueError("non-zero position requires average entry price")
        realized_delta = closed * (fill.price - old_avg) * (Decimal(1) if old_q > 0 else Decimal(-1))
        if new_q == 0:
            new_avg = None
        elif old_q * new_q > 0:
            new_avg = old_avg
        else:
            new_avg = fill.price
            reversed_position = True

    state = PositionState(
        run_id=current.run_id,
        account_ref=current.account_ref,
        series_identity=current.series_identity,
        accounting_mode=current.accounting_mode,
        quantity=new_q,
        average_entry_price=new_avg,
        realized_pnl=current.realized_pnl + realized_delta,
        updated_at_utc=fill.filled_at_utc,
        config_sha256=current.config_sha256,
        source_fill_ids=current.source_fill_ids + (fill.fill_id,),
        position_id=current.position_id,
    )
    return PositionTransition(state, realized_delta, closed, reversed_position)


def unrealized_pnl(state: PositionState, mark_price: Decimal | None) -> Decimal:
    if state.quantity == 0 or mark_price is None:
        return Decimal(0)
    if not mark_price.is_finite() or mark_price <= 0:
        raise ValueError("mark_price must be finite and positive")
    return state.quantity * (mark_price - (state.average_entry_price or mark_price))
