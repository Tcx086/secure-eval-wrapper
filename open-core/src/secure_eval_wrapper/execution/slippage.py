"""Exact adverse slippage models."""

from __future__ import annotations

from abc import ABC, abstractmethod
from decimal import Decimal

from secure_eval_wrapper.execution.models import OrderSide, SlippageConfiguration


class SlippageModel(ABC):
    configuration: SlippageConfiguration

    @abstractmethod
    def apply(self, *, base_price: Decimal, side: OrderSide) -> tuple[Decimal, Decimal]:
        raise NotImplementedError


class FixedAdverseBasisPointSlippage(SlippageModel):
    def __init__(self, configuration: SlippageConfiguration) -> None:
        self.configuration = configuration

    def apply(self, *, base_price: Decimal, side: OrderSide) -> tuple[Decimal, Decimal]:
        if not base_price.is_finite() or base_price <= 0:
            raise ValueError("base_price must be finite and positive")
        side = OrderSide(side)
        amount = base_price * self.configuration.adverse_bps / Decimal(10_000)
        price = base_price + amount if side is OrderSide.BUY else base_price - amount
        if price <= 0:
            raise ValueError("adverse slippage produced a non-positive fill price")
        return price, amount


class ZeroSlippage(FixedAdverseBasisPointSlippage):
    def __init__(self) -> None:
        super().__init__(SlippageConfiguration())
