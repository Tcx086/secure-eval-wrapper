"""Read-only health checks over Phase 5 simulated execution bundles."""
from __future__ import annotations
from dataclasses import dataclass
from decimal import Decimal
from secure_eval_wrapper.monitoring.events import make_result
from secure_eval_wrapper.monitoring.models import HealthStatus, MonitoringCategory, MonitoredComponent, Severity

@dataclass(frozen=True)
class ExecutionHealthInput:
    order_count: int = 0
    active_order_count: int = 0
    oldest_active_order_age_seconds: Decimal | None = None
    stuck_order_count: int = 0
    rejected_order_count: int = 0
    expired_order_count: int = 0
    fill_count: int = 0
    fill_latency_samples_microseconds: tuple[int, ...] = ()
    fill_latency_threshold_microseconds: int | None = None
    missing_pre_submit_risk_count: int = 0
    missing_pre_fill_risk_count: int = 0
    blocked_order_fill_count: int = 0
    fill_without_order_count: int = 0
    fill_without_intent_count: int = 0
    duplicate_or_replayed_fill_count: int = 0
    stale_mark_count: int = 0
    unmarked_nonzero_position_count: int = 0
    position_reconciliation_ok: bool | None = None
    cash_reconciliation_ok: bool | None = None
    account_equity_reconciliation_ok: bool | None = None
    orphan_membership_count: int = 0
    orphan_projection_count: int = 0
    complete_reconstruction_ok: bool | None = None
    metric_count: int = 0
    equity_point_count: int = 0
    signal_derived_pnl: Decimal = Decimal("0")


def evaluate_execution_health(context, value: ExecutionHealthInput | None, configuration) -> tuple:
    c=MonitoringCategory.EXECUTION; component=MonitoredComponent.SIMULATED_EXECUTION.value
    if value is None: return (make_result(context,category=c,component=component,check_name="execution_input",health_status=HealthStatus.UNKNOWN,reason_code="execution_input_unavailable",explanation="No Phase 5 simulated-execution summary was supplied."),)
    out=[]; critical=value.blocked_order_fill_count+value.fill_without_order_count+value.fill_without_intent_count+value.duplicate_or_replayed_fill_count
    missing=value.missing_pre_submit_risk_count+value.missing_pre_fill_risk_count
    out.append(make_result(context,category=c,component=component,check_name="fill_lineage_and_risk",health_status=HealthStatus.UNHEALTHY if critical or missing else HealthStatus.HEALTHY,reason_code="blocked_order_filled" if value.blocked_order_fill_count else "fill_without_order" if value.fill_without_order_count else "fill_without_intent" if value.fill_without_intent_count else "fill_replay" if value.duplicate_or_replayed_fill_count else "missing_risk_decision" if missing else "fill_lineage_reconciled",explanation="Every fill was checked for order, intent, risk, and replay lineage.",observed={"critical":critical,"missing_risk_decisions":missing},severity=Severity.CRITICAL if critical else None))
    age=value.oldest_active_order_age_seconds; stuck=value.stuck_order_count or (age is not None and age>configuration.maximum_active_order_age_seconds)
    out.append(make_result(context,category=c,component=component,check_name="order_lifecycle",health_status=HealthStatus.DEGRADED if stuck or value.rejected_order_count or value.expired_order_count else HealthStatus.HEALTHY,reason_code="order_stuck" if stuck else "orders_rejected" if value.rejected_order_count else "orders_expired" if value.expired_order_count else "order_lifecycle_healthy",explanation="Active-order age and rejected/expired lifecycle counts were evaluated.",observed={"oldest_active_age":age,"stuck":value.stuck_order_count,"rejected":value.rejected_order_count,"expired":value.expired_order_count},threshold=configuration.maximum_active_order_age_seconds))
    latency_max=max(value.fill_latency_samples_microseconds,default=None); latency_threshold=value.fill_latency_threshold_microseconds
    latency_status=HealthStatus.UNKNOWN if latency_max is None or latency_threshold is None else HealthStatus.DEGRADED if latency_max>latency_threshold else HealthStatus.HEALTHY
    out.append(make_result(context,category=c,component=component,check_name="fill_latency",health_status=latency_status,reason_code="fill_latency_unavailable" if latency_status is HealthStatus.UNKNOWN else "fill_latency_breach" if latency_status is HealthStatus.DEGRADED else "fill_latency_healthy",explanation="Explicit deterministic simulated fill-latency samples were compared to the configured threshold; these are not network measurements.",observed=latency_max,threshold=latency_threshold))
    valuation=value.stale_mark_count+value.unmarked_nonzero_position_count
    out.append(make_result(context,category=c,component=component,check_name="position_valuation",health_status=HealthStatus.UNHEALTHY if value.unmarked_nonzero_position_count else HealthStatus.DEGRADED if value.stale_mark_count else HealthStatus.HEALTHY,reason_code="unmarked_open_position" if value.unmarked_nonzero_position_count else "stale_mark" if value.stale_mark_count else "positions_marked",explanation="Stale marks and unmarked non-zero final positions were checked.",observed={"stale_marks":value.stale_mark_count,"unmarked_open_positions":value.unmarked_nonzero_position_count}))
    reconciliation=(value.position_reconciliation_ok,value.cash_reconciliation_ok,value.account_equity_reconciliation_ok,value.complete_reconstruction_ok)
    unavailable=any(v is None for v in reconciliation); failed=any(v is False for v in reconciliation)
    orphan=value.orphan_membership_count+value.orphan_projection_count; structural=orphan or (value.metric_count>0 and value.equity_point_count==0) or (value.fill_count==0 and value.signal_derived_pnl!=0)
    out.append(make_result(context,category=c,component=component,check_name="bundle_reconciliation",health_status=HealthStatus.UNHEALTHY if failed or structural else HealthStatus.UNKNOWN if unavailable else HealthStatus.HEALTHY,reason_code="reconciliation_mismatch" if failed else "orphan_run_record" if orphan else "metrics_without_equity" if value.metric_count>0 and value.equity_point_count==0 else "pnl_without_fills" if value.fill_count==0 and value.signal_derived_pnl!=0 else "reconciliation_unavailable" if unavailable else "bundle_reconciled",explanation="Positions, cash, equity, memberships, projections, metrics, and reconstruction were reconciled read-only.",observed={"reconciliation":reconciliation,"orphans":orphan,"metrics":value.metric_count,"equity_points":value.equity_point_count,"fills":value.fill_count}))
    return tuple(out)