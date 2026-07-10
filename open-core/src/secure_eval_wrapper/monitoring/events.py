"""Shared result construction for category evaluators."""
from __future__ import annotations
from secure_eval_wrapper.monitoring.models import CheckStatus, HealthCheckResult, HealthStatus, Severity


def make_result(context, *, category, component, check_name, health_status, reason_code, explanation, observed=None, threshold=None, severity=None):
    health_status = HealthStatus(health_status)
    if severity is None:
        severity = {HealthStatus.HEALTHY: Severity.INFO, HealthStatus.UNKNOWN: Severity.WARNING, HealthStatus.DEGRADED: Severity.WARNING, HealthStatus.UNHEALTHY: Severity.ERROR}[health_status]
    status = {HealthStatus.HEALTHY: CheckStatus.PASSED, HealthStatus.UNKNOWN: CheckStatus.UNKNOWN, HealthStatus.DEGRADED: CheckStatus.WARNING, HealthStatus.UNHEALTHY: CheckStatus.FAILED}[health_status]
    return HealthCheckResult(
        monitoring_run_id=context.monitoring_run_id,
        evaluation_at_utc=context.as_of_utc,
        category=category,
        component=component,
        check_name=check_name,
        status=status,
        health_status=health_status,
        severity=severity,
        reason_code=reason_code,
        explanation=explanation,
        observed_value=observed,
        configured_threshold=threshold,
        configuration_sha256=context.configuration_sha256,
        stable_input_sha256=context.stable_input_sha256,
        provenance=context.provenance,
    )