"""Deterministic monitoring of recorded Phase 5 risk state."""
from __future__ import annotations
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Mapping
from secure_eval_wrapper.monitoring.events import make_result
from secure_eval_wrapper.monitoring.models import HealthStatus, MonitoringCategory, MonitoredComponent, Severity

@dataclass(frozen=True)
class RiskHealthInput:
    decision_count: int = 0
    blocked_decision_count: int = 0
    reason_code_counts: Mapping[str,int] = field(default_factory=dict)
    maximum_limit_utilization: Decimal | None = None
    gross_exposure_utilization: Decimal | None = None
    net_exposure_utilization: Decimal | None = None
    maximum_series_position_utilization: Decimal | None = None
    gross_exposure_to_equity_utilization: Decimal | None = None
    current_drawdown: Decimal | None = None
    drawdown_limit_breached: bool = False
    equity: Decimal | None = None
    spot_short_attempt_count: int = 0
    insufficient_cash_attempt_count: int = 0
    invalid_price_decision_count: int = 0
    decision_without_intent_count: int = 0
    pre_fill_without_order_count: int = 0
    accepted_limit_violation_count: int = 0


def _util_status(value, config):
    if value is None: return HealthStatus.UNKNOWN
    if value>=config.critical_utilization: return HealthStatus.UNHEALTHY
    if value>=config.warning_utilization: return HealthStatus.DEGRADED
    return HealthStatus.HEALTHY


def evaluate_risk_health(context, value: RiskHealthInput | None, configuration) -> tuple:
    c=MonitoringCategory.RISK; component=MonitoredComponent.RISK_GUARD.value
    if value is None: return (make_result(context,category=c,component=component,check_name="risk_input",health_status=HealthStatus.UNKNOWN,reason_code="risk_input_unavailable",explanation="No recorded risk summary was supplied."),)
    out=[]; rate=Decimal(value.blocked_decision_count)/Decimal(value.decision_count) if value.decision_count else None
    status=HealthStatus.UNKNOWN if rate is None else HealthStatus.UNHEALTHY if rate>=configuration.critical_blocked_rate else HealthStatus.DEGRADED if rate>=configuration.warning_blocked_rate else HealthStatus.HEALTHY
    out.append(make_result(context,category=c,component=component,check_name="blocked_decision_rate",health_status=status,reason_code="risk_decisions_unavailable" if rate is None else "blocked_rate_critical" if status is HealthStatus.UNHEALTHY else "blocked_rate_warning" if status is HealthStatus.DEGRADED else "blocked_rate_healthy",explanation="Blocked decision rate uses inclusive warning and critical thresholds.",observed=rate,threshold={"warning_gte":configuration.warning_blocked_rate,"critical_gte":configuration.critical_blocked_rate}))
    values={"maximum":value.maximum_limit_utilization,"gross":value.gross_exposure_utilization,"net":value.net_exposure_utilization,"series":value.maximum_series_position_utilization,"gross_to_equity":value.gross_exposure_to_equity_utilization}; available=[v for v in values.values() if v is not None]; worst=max(available) if available else None; status=_util_status(worst,configuration)
    out.append(make_result(context,category=c,component=component,check_name="limit_utilization",health_status=status,reason_code="utilization_unavailable" if worst is None else "limit_breached" if status is HealthStatus.UNHEALTHY else "limit_utilization_warning" if status is HealthStatus.DEGRADED else "limit_utilization_healthy",explanation="Recorded utilization uses inclusive warning and breach boundaries; no leverage model is introduced.",observed=values,threshold={"warning_gte":configuration.warning_utilization,"critical_gte":configuration.critical_utilization}))
    draw=value.current_drawdown; status=HealthStatus.UNHEALTHY if value.drawdown_limit_breached or (draw is not None and draw>=configuration.critical_drawdown) or (value.equity is not None and value.equity<=0) else HealthStatus.UNKNOWN if draw is None or value.equity is None else HealthStatus.DEGRADED if draw>=configuration.warning_drawdown else HealthStatus.HEALTHY
    out.append(make_result(context,category=c,component=component,check_name="drawdown_and_equity",health_status=status,reason_code="non_positive_equity" if value.equity is not None and value.equity<=0 else "drawdown_limit_breach" if value.drawdown_limit_breached or (draw is not None and draw>=configuration.critical_drawdown) else "drawdown_unavailable" if draw is None or value.equity is None else "drawdown_warning" if status is HealthStatus.DEGRADED else "drawdown_healthy",explanation="Current drawdown and equity were checked against inclusive configured boundaries.",observed={"drawdown":draw,"equity":value.equity},threshold={"warning_gte":configuration.warning_drawdown,"critical_gte":configuration.critical_drawdown},severity=Severity.CRITICAL if value.equity is not None and value.equity<=0 else None))
    lineage=value.decision_without_intent_count+value.pre_fill_without_order_count+value.accepted_limit_violation_count; attempts=value.spot_short_attempt_count+value.insufficient_cash_attempt_count+value.invalid_price_decision_count; repeated=max(value.reason_code_counts.values(),default=0)
    out.append(make_result(context,category=c,component=component,check_name="risk_lineage_and_reasons",health_status=HealthStatus.UNHEALTHY if lineage else HealthStatus.DEGRADED if attempts or repeated>=3 else HealthStatus.HEALTHY,reason_code="accepted_limit_violation" if value.accepted_limit_violation_count else "risk_decision_without_intent" if value.decision_without_intent_count else "prefill_without_order" if value.pre_fill_without_order_count else "spot_short_attempt" if value.spot_short_attempt_count else "insufficient_cash_attempt" if value.insufficient_cash_attempt_count else "invalid_price_decision" if value.invalid_price_decision_count else "repeated_risk_reason" if repeated>=3 else "risk_lineage_healthy",explanation="Recorded risk lineage and repeated block reasons were checked.",observed={"lineage_failures":lineage,"blocked_attempts":attempts,"reason_counts":dict(value.reason_code_counts)}))
    return tuple(out)