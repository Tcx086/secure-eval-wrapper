"""Operational and lineage health for public alpha/signal outputs."""
from __future__ import annotations
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from secure_eval_wrapper.monitoring.events import make_result
from secure_eval_wrapper.monitoring.models import HealthStatus, MonitoringCategory, MonitoredComponent

@dataclass(frozen=True)
class SignalHealthInput:
    latest_signal_at_utc: datetime | None = None
    alpha_run_status: str | None = None
    signal_run_status: str | None = None
    signal_count: int = 0
    component_count: int = 0
    invalid_or_skipped_count: int = 0
    evaluated_alpha_count: int = 0
    warmup_only: bool = False
    flat_signal_count: int = 0
    blocked_overlap_count: int = 0
    missing_component_lineage_count: int = 0
    duplicate_signal_count: int = 0
    absent_market_series_count: int = 0
    signal_before_data_count: int = 0
    hash_conflict_count: int = 0


def evaluate_signal_health(context, value: SignalHealthInput | None, configuration) -> tuple:
    c=MonitoringCategory.SIGNAL; component=MonitoredComponent.SIGNAL_PIPELINE.value; out=[]
    if value is None:
        return (make_result(context,category=c,component=component,check_name="signal_availability",health_status=HealthStatus.UNKNOWN,reason_code="signal_input_unavailable",explanation="No public alpha/signal run summary was supplied."),)
    if value.latest_signal_at_utc is None:
        status=HealthStatus.UNKNOWN; reason="signal_missing"; observed=None
    elif value.latest_signal_at_utc.tzinfo is None or value.latest_signal_at_utc.utcoffset() is None:
        status=HealthStatus.UNHEALTHY; reason="naive_timestamp"; observed=None
    else:
        age=Decimal(str((context.as_of_utc-value.latest_signal_at_utc).total_seconds())); observed=age
        status=HealthStatus.UNHEALTHY if age<0 else HealthStatus.DEGRADED if age>configuration.maximum_signal_age_seconds else HealthStatus.HEALTHY
        reason="signal_before_data_availability" if age<0 else "signal_stale" if status is HealthStatus.DEGRADED else "signal_fresh"
    out.append(make_result(context,category=c,component=component,check_name="signal_freshness",health_status=status,reason_code=reason,explanation="Latest signal age was evaluated against the declared as-of timestamp.",observed=observed,threshold=configuration.maximum_signal_age_seconds))
    statuses=(value.alpha_run_status,value.signal_run_status); failed=any(s=="failed" for s in statuses); partial=any(s=="partial" for s in statuses); missing=any(s is None for s in statuses)
    out.append(make_result(context,category=c,component=component,check_name="run_status",health_status=HealthStatus.UNKNOWN if missing else HealthStatus.UNHEALTHY if failed else HealthStatus.DEGRADED if partial else HealthStatus.HEALTHY,reason_code="run_status_unavailable" if missing else "run_failed" if failed else "run_partial" if partial else "run_completed",explanation="Alpha and signal run statuses were evaluated operationally, without using PnL.",observed={"alpha":value.alpha_run_status,"signal":value.signal_run_status}))
    ratio=Decimal(value.invalid_or_skipped_count)/Decimal(value.evaluated_alpha_count) if value.evaluated_alpha_count else None
    no_components=value.signal_count>0 and value.component_count==0
    out.append(make_result(context,category=c,component=component,check_name="component_lineage",health_status=HealthStatus.UNHEALTHY if no_components or value.missing_component_lineage_count or value.hash_conflict_count else HealthStatus.UNKNOWN if value.warmup_only else HealthStatus.DEGRADED if ratio is not None and ratio>Decimal("0.5") else HealthStatus.HEALTHY,reason_code="missing_signal_component_lineage" if no_components or value.missing_component_lineage_count else "input_hash_conflict" if value.hash_conflict_count else "warmup_only_output" if value.warmup_only else "excessive_skipped_alpha_ratio" if ratio is not None and ratio>Decimal("0.5") else "signal_lineage_complete",explanation="Component lineage, warmup output, and input hashes were checked.",observed={"component_count":value.component_count,"missing_lineage":value.missing_component_lineage_count,"skipped_ratio":ratio,"hash_conflicts":value.hash_conflict_count}))
    flat_ratio=Decimal(value.flat_signal_count)/Decimal(value.signal_count) if value.signal_count else None
    defects=value.duplicate_signal_count+value.absent_market_series_count+value.signal_before_data_count
    reason="duplicate_signal_identity" if value.duplicate_signal_count else "signal_series_absent" if value.absent_market_series_count else "signal_before_data_availability" if value.signal_before_data_count else "blocked_overlap_group" if value.blocked_overlap_count else "flat_signal_concentration" if flat_ratio is not None and flat_ratio>=configuration.flat_signal_warning_ratio else "signal_outputs_operational"
    status=HealthStatus.UNHEALTHY if defects else HealthStatus.DEGRADED if value.blocked_overlap_count or (flat_ratio is not None and flat_ratio>=configuration.flat_signal_warning_ratio) else HealthStatus.HEALTHY
    out.append(make_result(context,category=c,component=component,check_name="signal_output_quality",health_status=status,reason_code=reason,explanation="Signal identity, market-data lineage, overlap handling, and flat concentration were evaluated; profit was not considered.",observed={"duplicates":value.duplicate_signal_count,"absent_series":value.absent_market_series_count,"before_data":value.signal_before_data_count,"blocked_overlap":value.blocked_overlap_count,"flat_ratio":flat_ratio},threshold=configuration.flat_signal_warning_ratio))
    return tuple(out)