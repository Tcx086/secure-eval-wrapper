"""Exact maker/taker fee models."""

from __future__ import annotations

from abc import ABC, abstractmethod
from decimal import Decimal

from secure_eval_wrapper.execution.models import FeeConfiguration, LiquidityFlag


class FeeModel(ABC):
    configuration: FeeConfiguration

    @abstractmethod
    def calculate(self, *, price: Decimal, quantity: Decimal, liquidity: LiquidityFlag) -> Decimal:
        raise NotImplementedError


class FixedBasisPointFeeModel(FeeModel):
    def __init__(self, configuration: FeeConfiguration) -> None:
        self.configuration = configuration

    def calculate(self, *, price: Decimal, quantity: Decimal, liquidity: LiquidityFlag) -> Decimal:
        liquidity = LiquidityFlag(liquidity)
        bps = self.configuration.maker_bps if liquidity is LiquidityFlag.MAKER else self.configuration.taker_bps
        fee = price * quantity * bps / Decimal(10_000)
        if not fee.is_finite() or fee < 0:
            raise ValueError("calculated fee must be finite and non-negative")
        return fee


class ZeroFeeModel(FixedBasisPointFeeModel):
    def __init__(self, fee_currency: str = "USDT") -> None:
        super().__init__(FeeConfiguration(fee_currency=fee_currency))
