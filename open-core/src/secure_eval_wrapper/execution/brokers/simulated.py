"""Deterministic, bar-level simulated broker with no partial fills."""

from __future__ import annotations

from dataclasses import replace
from decimal import Decimal

from secure_eval_wrapper.execution.broker import Broker, BrokerResult
from secure_eval_wrapper.execution.fees import FeeModel
from secure_eval_wrapper.execution.models import (
    BrokerConfiguration,
    Fill,
    LiquidityFlag,
    OrderIntent,
    OrderStatus,
    OrderType,
    RejectReason,
    RiskDecisionStatus,
    SimulatedOrder,
    TimeInForce,
)
from secure_eval_wrapper.execution.slippage import SlippageModel


class SimulatedBroker(Broker):
    def __init__(self, configuration: BrokerConfiguration, *, fee_model: FeeModel, slippage_model: SlippageModel) -> None:
        self.configuration = configuration
        self.fee_model = fee_model
        self.slippage_model = slippage_model
        self._active: dict[object, SimulatedOrder] = {}
        self._history: list[SimulatedOrder] = []

    def submit_order_intent(self, intent: OrderIntent, risk_decision) -> BrokerResult:
        accepted = risk_decision.status is RiskDecisionStatus.ACCEPTED
        order = SimulatedOrder(intent.run_id, intent.order_intent_id, intent.series_identity, intent.event_timestamp_utc, intent.side, intent.order_type, intent.quantity, intent.accounting_mode, intent.time_in_force, OrderStatus.ACKNOWLEDGED if accepted else OrderStatus.REJECTED, self.configuration.config_sha256, intent.limit_price, intent.stop_price, reject_reason=None if accepted else RejectReason.RISK_BLOCKED, parent_ids=(intent.order_intent_id, risk_decision.risk_decision_id))
        self._history.append(order)
        if accepted:
            self._active[order.order_id] = order
        return BrokerResult((order,))

    def cancel_order(self, order_id, *, cancelled_at_utc, reason: str) -> BrokerResult:
        order = self._active.pop(order_id, None)
        if order is None:
            return BrokerResult()
        update = replace(order, status=OrderStatus.CANCELLED, activation_reason=reason, provenance={"cancelled_at_utc": cancelled_at_utc, "reason": reason})
        self._history.append(update)
        return BrokerResult((update,))

    def active_orders(self, *, series_identity=None) -> tuple[SimulatedOrder, ...]:
        values = self._active.values()
        if series_identity is not None:
            digest = series_identity.series_identity_sha256
            values = (item for item in values if item.series_identity.series_identity_sha256 == digest)
        return tuple(sorted(values, key=lambda row: (row.submitted_at_utc, str(row.order_id))))

    def _fill(self, order: SimulatedOrder, *, timestamp, base_price: Decimal, liquidity: LiquidityFlag, reason: str, risk_check, apply_slippage: bool) -> BrokerResult:
        if apply_slippage:
            price, slippage_amount = self.slippage_model.apply(base_price=base_price, side=order.side)
            slippage_bps = self.slippage_model.configuration.adverse_bps
        else:
            price, slippage_amount, slippage_bps = base_price, Decimal(0), Decimal(0)
        if order.limit_price is not None:
            if order.side.value == "buy" and price > order.limit_price:
                raise ValueError("buy limit fill cannot exceed its limit")
            if order.side.value == "sell" and price < order.limit_price:
                raise ValueError("sell limit fill cannot fall below its limit")
        fee = self.fee_model.calculate(price=price, quantity=order.quantity, liquidity=liquidity)
        risk = risk_check(order, price, liquidity, fee)
        if risk.status is RiskDecisionStatus.BLOCKED:
            self._active.pop(order.order_id, None)
            rejected = replace(order, status=OrderStatus.REJECTED, reject_reason=RejectReason.RISK_BLOCKED, activation_reason=risk.reason_code)
            self._history.append(rejected)
            return BrokerResult((rejected,), (), (risk,))
        fill = Fill(order.run_id, order.order_id, order.order_intent_id, order.series_identity, timestamp, order.side, order.quantity, base_price, price, order.accounting_mode, liquidity, fee, self.fee_model.configuration.fee_currency, slippage_amount, slippage_bps, reason, self.configuration.config_sha256, parent_ids=(order.order_id, risk.risk_decision_id))
        self._active.pop(order.order_id, None)
        filled = replace(order, status=OrderStatus.FILLED)
        self._history.append(filled)
        return BrokerResult((filled,), (fill,), (risk,))

    def _eligible(self, order: SimulatedOrder, timestamp) -> bool:
        return timestamp >= order.submitted_at_utc

    def process_bar_open(self, *, series_identity, timestamp_utc, open_price, risk_check) -> BrokerResult:
        updates, fills, risks = [], [], []
        for order in self.active_orders(series_identity=series_identity):
            if not self._eligible(order, timestamp_utc):
                continue
            result = None
            if order.order_type is OrderType.MARKET:
                result = self._fill(order, timestamp=timestamp_utc, base_price=open_price, liquidity=LiquidityFlag.TAKER, reason="next_bar_open", risk_check=risk_check, apply_slippage=True)
            elif order.order_type is OrderType.LIMIT or (order.order_type is OrderType.STOP_LIMIT and order.status is OrderStatus.TRIGGERED):
                reached = open_price <= order.limit_price if order.side.value == "buy" else open_price >= order.limit_price
                if reached:
                    result = self._fill(order, timestamp=timestamp_utc, base_price=open_price, liquidity=LiquidityFlag.MAKER, reason="limit_open_gap", risk_check=risk_check, apply_slippage=False)
            elif order.order_type is OrderType.STOP:
                reached = open_price >= order.stop_price if order.side.value == "buy" else open_price <= order.stop_price
                if reached:
                    result = self._fill(order, timestamp=timestamp_utc, base_price=open_price, liquidity=LiquidityFlag.TAKER, reason="stop_open_gap", risk_check=risk_check, apply_slippage=True)
            elif order.order_type is OrderType.STOP_LIMIT:
                triggered = open_price >= order.stop_price if order.side.value == "buy" else open_price <= order.stop_price
                if triggered:
                    activated = replace(order, status=OrderStatus.TRIGGERED, triggered_at_utc=timestamp_utc, activation_reason="stop_triggered_at_open")
                    self._active[order.order_id] = activated
                    updates.append(activated)
                    marketable = open_price <= order.limit_price if order.side.value == "buy" else open_price >= order.limit_price
                    if marketable:
                        result = self._fill(activated, timestamp=timestamp_utc, base_price=open_price, liquidity=LiquidityFlag.TAKER, reason="stop_limit_open", risk_check=risk_check, apply_slippage=False)
            if result is not None:
                updates.extend(result.order_updates); fills.extend(result.fills); risks.extend(result.risk_decisions)
            elif order.time_in_force is TimeInForce.IOC and order.order_id in self._active:
                self._active.pop(order.order_id)
                expired = replace(order, status=OrderStatus.EXPIRED, activation_reason="ioc_not_filled_at_first_eligible_open")
                updates.append(expired); self._history.append(expired)
        return BrokerResult(tuple(updates), tuple(fills), tuple(risks))

    def process_completed_bar(self, *, series_identity, timestamp_utc, open_price, high, low, close, risk_check) -> BrokerResult:
        updates, fills, risks = [], [], []
        for order in self.active_orders(series_identity=series_identity):
            if timestamp_utc <= order.submitted_at_utc or order.time_in_force is TimeInForce.IOC:
                continue
            result = None
            if order.order_type is OrderType.LIMIT or (order.order_type is OrderType.STOP_LIMIT and order.status is OrderStatus.TRIGGERED):
                reached = low <= order.limit_price if order.side.value == "buy" else high >= order.limit_price
                if reached:
                    result = self._fill(order, timestamp=timestamp_utc, base_price=order.limit_price, liquidity=LiquidityFlag.MAKER, reason="limit_intrabar_cross", risk_check=risk_check, apply_slippage=False)
            elif order.order_type is OrderType.STOP:
                reached = high >= order.stop_price if order.side.value == "buy" else low <= order.stop_price
                if reached:
                    result = self._fill(order, timestamp=timestamp_utc, base_price=order.stop_price, liquidity=LiquidityFlag.TAKER, reason="stop_intrabar_trigger", risk_check=risk_check, apply_slippage=True)
            elif order.order_type is OrderType.STOP_LIMIT:
                reached = high >= order.stop_price if order.side.value == "buy" else low <= order.stop_price
                if reached:
                    activated = replace(order, status=OrderStatus.TRIGGERED, triggered_at_utc=timestamp_utc, activation_reason="intrabar_trigger_deferred")
                    self._active[order.order_id] = activated
                    self._history.append(activated)
                    updates.append(activated)
            if result is not None:
                updates.extend(result.order_updates); fills.extend(result.fills); risks.extend(result.risk_decisions)
        return BrokerResult(tuple(updates), tuple(fills), tuple(risks))

    def expire_remaining_orders(self, *, expired_at_utc) -> BrokerResult:
        updates = []
        for order in self.active_orders():
            self._active.pop(order.order_id, None)
            expired = replace(order, status=OrderStatus.EXPIRED, activation_reason="backtest_end", provenance={"expired_at_utc": expired_at_utc})
            self._history.append(expired)
            updates.append(expired)
        return BrokerResult(tuple(updates))
