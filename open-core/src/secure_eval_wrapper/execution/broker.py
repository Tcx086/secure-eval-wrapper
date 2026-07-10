"""Broker interface used by backtesting and future adapters."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass

from secure_eval_wrapper.execution.models import Fill, OrderIntent, RiskDecision, SimulatedOrder


@dataclass(frozen=True)
class BrokerResult:
    order_updates: tuple[SimulatedOrder, ...] = ()
    fills: tuple[Fill, ...] = ()
    risk_decisions: tuple[RiskDecision, ...] = ()


class Broker(ABC):
    @abstractmethod
    def submit_order_intent(self, intent: OrderIntent, risk_decision: RiskDecision) -> BrokerResult:
        raise NotImplementedError

    @abstractmethod
    def cancel_order(self, order_id, *, cancelled_at_utc, reason: str) -> BrokerResult:
        raise NotImplementedError

    @abstractmethod
    def process_bar_open(self, *, series_identity, timestamp_utc, open_price, risk_check) -> BrokerResult:
        raise NotImplementedError

    @abstractmethod
    def process_completed_bar(self, *, series_identity, timestamp_utc, open_price, high, low, close, risk_check) -> BrokerResult:
        raise NotImplementedError

    @abstractmethod
    def expire_remaining_orders(self, *, expired_at_utc) -> BrokerResult:
        raise NotImplementedError

    @abstractmethod
    def active_orders(self, *, series_identity=None) -> tuple[SimulatedOrder, ...]:
        raise NotImplementedError
