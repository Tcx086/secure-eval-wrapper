"""Pre-submit and pre-fill risk checks shared by simulated execution."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Mapping

from secure_eval_wrapper.execution.models import (
    AccountingMode,
    OrderIntent,
    OrderSide,
    PositionState,
    RiskDecision,
    RiskDecisionStatus,
    RiskLimitConfiguration,
    RiskStage,
)


@dataclass(frozen=True)
class PortfolioRiskView:
    cash: Decimal
    equity: Decimal
    peak_equity: Decimal
    positions: Mapping[str, PositionState] = field(default_factory=dict)
    marks: Mapping[str, Decimal] = field(default_factory=dict)
    open_orders_per_series: Mapping[str, int] = field(default_factory=dict)


class RiskGuard:
    def __init__(self, configuration: RiskLimitConfiguration) -> None:
        self.configuration = configuration

    def assess(
        self,
        intent: OrderIntent,
        *,
        price: Decimal,
        stage: RiskStage,
        decision_timestamp_utc: datetime,
        portfolio: PortfolioRiskView,
        fee_amount: Decimal = Decimal(0),
    ) -> RiskDecision:
        stage = RiskStage(stage)
        series_hash = intent.series_identity.series_identity_sha256

        def decision(
            status: RiskDecisionStatus,
            reason: str,
            explanation: str,
            *,
            limit_name: str | None = None,
            observed: Decimal | None = None,
            limit: Decimal | None = None,
        ) -> RiskDecision:
            return RiskDecision(
                run_id=intent.run_id,
                order_intent_id=intent.order_intent_id,
                series_identity=intent.series_identity,
                decision_timestamp_utc=decision_timestamp_utc,
                stage=stage,
                status=status,
                reason_code=reason,
                explanation=explanation,
                relevant_limit=limit_name,
                observed_value=observed,
                configured_limit=limit,
                config_sha256=self.configuration.config_sha256,
                parent_ids=(intent.order_intent_id,),
            )

        if not isinstance(price, Decimal) or not price.is_finite() or price <= 0:
            return decision(RiskDecisionStatus.BLOCKED, "missing_or_invalid_price", "A finite positive execution price is required.", limit_name="price", observed=price if isinstance(price, Decimal) and price.is_finite() else None)
        if not intent.quantity.is_finite() or intent.quantity <= 0:
            return decision(RiskDecisionStatus.BLOCKED, "invalid_quantity", "Order quantity must be finite and positive.", limit_name="quantity", observed=intent.quantity if intent.quantity.is_finite() else None)
        if intent.accounting_mode not in (AccountingMode.SPOT, AccountingMode.LINEAR_PERPETUAL):
            return decision(RiskDecisionStatus.BLOCKED, "unsupported_accounting_mode", "Only Spot and linear perpetual accounting are supported.")
        if not fee_amount.is_finite() or fee_amount < 0:
            return decision(RiskDecisionStatus.BLOCKED, "invalid_fee", "The estimated fee must be finite and non-negative.")

        existing = portfolio.positions.get(series_hash)
        current = Decimal(0) if existing is None else existing.quantity
        signed_delta = intent.quantity * intent.side.sign
        prospective = current + signed_delta
        increasing_risk = abs(prospective) > abs(current)
        notional = intent.quantity * price
        prospective_notional = abs(prospective * price)

        if intent.accounting_mode is AccountingMode.SPOT and self.configuration.prohibit_spot_shorts and prospective < 0:
            return decision(RiskDecisionStatus.BLOCKED, "spot_short_prohibited", "Spot inventory may not become negative.", limit_name="spot_quantity", observed=prospective, limit=Decimal(0))
        if intent.accounting_mode is AccountingMode.SPOT and intent.side is OrderSide.BUY:
            cash_required = notional + fee_amount
            if cash_required > portfolio.cash:
                return decision(RiskDecisionStatus.BLOCKED, "insufficient_cash", "Spot purchase notional plus fee exceeds available cash.", limit_name="available_cash", observed=cash_required, limit=portfolio.cash)

        limits = self.configuration
        if limits.max_order_notional is not None and notional > limits.max_order_notional:
            return decision(RiskDecisionStatus.BLOCKED, "max_order_notional", "Order notional exceeds the configured maximum.", limit_name="max_order_notional", observed=notional, limit=limits.max_order_notional)
        if limits.max_position_notional_per_series is not None and prospective_notional > limits.max_position_notional_per_series:
            return decision(RiskDecisionStatus.BLOCKED, "max_position_notional", "Prospective series exposure exceeds the configured maximum.", limit_name="max_position_notional_per_series", observed=prospective_notional, limit=limits.max_position_notional_per_series)
        if stage is RiskStage.PRE_SUBMIT:
            count = Decimal(portfolio.open_orders_per_series.get(series_hash, 0) + 1)
            if count > limits.max_open_orders_per_series:
                return decision(RiskDecisionStatus.BLOCKED, "max_open_orders", "Open-order count exceeds the per-series limit.", limit_name="max_open_orders_per_series", observed=count, limit=Decimal(limits.max_open_orders_per_series))

        notionals: dict[str, Decimal] = {}
        for key, position in portfolio.positions.items():
            mark = price if key == series_hash else portfolio.marks.get(key)
            if mark is not None and mark.is_finite() and mark > 0:
                notionals[key] = position.quantity * mark
        notionals[series_hash] = prospective * price
        gross = sum((abs(value) for value in notionals.values()), Decimal(0))
        net = abs(sum(notionals.values(), Decimal(0)))
        if limits.max_gross_exposure is not None and gross > limits.max_gross_exposure:
            return decision(RiskDecisionStatus.BLOCKED, "max_gross_exposure", "Prospective gross exposure exceeds the configured maximum.", limit_name="max_gross_exposure", observed=gross, limit=limits.max_gross_exposure)
        if limits.max_net_exposure is not None and net > limits.max_net_exposure:
            return decision(RiskDecisionStatus.BLOCKED, "max_net_exposure", "Prospective absolute net exposure exceeds the configured maximum.", limit_name="max_net_exposure", observed=net, limit=limits.max_net_exposure)
        if limits.max_gross_exposure_to_equity is not None:
            ratio = Decimal("Infinity") if portfolio.equity <= 0 else gross / portfolio.equity
            if not ratio.is_finite() or ratio > limits.max_gross_exposure_to_equity:
                return decision(RiskDecisionStatus.BLOCKED, "max_gross_to_equity", "Prospective gross-exposure-to-equity ratio exceeds the configured maximum.", limit_name="max_gross_exposure_to_equity", observed=None if not ratio.is_finite() else ratio, limit=limits.max_gross_exposure_to_equity)
        if limits.max_drawdown_fraction is not None and increasing_risk and portfolio.peak_equity > 0:
            drawdown = max(Decimal(0), (portfolio.peak_equity - portfolio.equity) / portfolio.peak_equity)
            if drawdown > limits.max_drawdown_fraction:
                return decision(RiskDecisionStatus.BLOCKED, "max_drawdown", "Current drawdown blocks new risk-increasing orders.", limit_name="max_drawdown_fraction", observed=drawdown, limit=limits.max_drawdown_fraction)
        return decision(RiskDecisionStatus.ACCEPTED, "accepted", "All configured risk limits passed.")
