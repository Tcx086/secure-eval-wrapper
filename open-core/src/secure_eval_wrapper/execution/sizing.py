"""Deterministic signal-to-target sizing without execution side effects."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, ROUND_DOWN
from enum import Enum

from secure_eval_wrapper.data_collection.hashing import sha256_payload
from secure_eval_wrapper.execution.models import AccountingMode, OrderSide
from secure_eval_wrapper.signals.models import SignalDirection, StandardizedSignal


class SizingMode(str, Enum):
    FIXED_QUANTITY = "fixed_quantity"
    FIXED_NOTIONAL = "fixed_notional"


@dataclass(frozen=True)
class SizingConfiguration:
    mode: SizingMode
    target_value: Decimal
    quantity_step: Decimal | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "mode", SizingMode(self.mode))
        if not self.target_value.is_finite() or self.target_value <= 0:
            raise ValueError("target_value must be a finite positive Decimal")
        if self.quantity_step is not None and (not self.quantity_step.is_finite() or self.quantity_step <= 0):
            raise ValueError("quantity_step must be finite and positive")

    @property
    def config_sha256(self) -> str:
        return sha256_payload({"mode": self.mode, "target_value": self.target_value, "quantity_step": self.quantity_step})


@dataclass(frozen=True)
class SizingResult:
    target_quantity: Decimal
    current_quantity: Decimal
    delta_quantity: Decimal
    side: OrderSide | None
    reference_price: Decimal
    no_action_reason: str | None
    config_sha256: str

    @property
    def is_no_action(self) -> bool:
        return self.delta_quantity == 0


def _round_down_absolute(quantity: Decimal, step: Decimal | None) -> Decimal:
    if step is None:
        return quantity
    units = (abs(quantity) / step).to_integral_value(rounding=ROUND_DOWN)
    rounded = units * step
    return rounded.copy_sign(quantity)


def size_signal(
    signal: StandardizedSignal,
    *,
    current_quantity: Decimal,
    reference_price: Decimal,
    accounting_mode: AccountingMode,
    configuration: SizingConfiguration,
) -> SizingResult:
    if not current_quantity.is_finite():
        raise ValueError("current_quantity must be finite")
    if not reference_price.is_finite() or reference_price <= 0:
        raise ValueError("reference_price must be finite and positive")
    accounting_mode = AccountingMode(accounting_mode)
    direction_sign = {SignalDirection.LONG: Decimal(1), SignalDirection.SHORT: Decimal(-1), SignalDirection.FLAT: Decimal(0)}[signal.direction]
    if accounting_mode is AccountingMode.SPOT and direction_sign < 0:
        raise ValueError("Spot short target sizing is prohibited")
    absolute = configuration.target_value if configuration.mode is SizingMode.FIXED_QUANTITY else configuration.target_value / reference_price
    target = _round_down_absolute(direction_sign * absolute, configuration.quantity_step)
    delta = target - current_quantity
    reason = None
    if direction_sign != 0 and target == 0:
        reason = "rounded_target_zero"
    elif delta == 0:
        reason = "target_already_reached"
    side = None if delta == 0 else OrderSide.BUY if delta > 0 else OrderSide.SELL
    return SizingResult(target, current_quantity, delta, side, reference_price, reason, configuration.config_sha256)
