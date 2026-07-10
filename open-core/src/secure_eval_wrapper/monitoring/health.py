"""Non-numeric health aggregation with explicit precedence."""
from __future__ import annotations
from collections.abc import Iterable
from secure_eval_wrapper.monitoring.models import HealthCheckResult, HealthStatus, Severity

_PRECEDENCE = {
    HealthStatus.HEALTHY: 0,
    HealthStatus.UNKNOWN: 1,
    HealthStatus.DEGRADED: 2,
    HealthStatus.UNHEALTHY: 3,
}


def aggregate_health(results: Iterable[HealthCheckResult]) -> tuple[HealthStatus, tuple[HealthCheckResult, ...]]:
    values = tuple(results)
    if not values:
        return HealthStatus.UNKNOWN, ()
    critical = tuple(result for result in values if result.health_status is HealthStatus.UNHEALTHY and result.severity is Severity.CRITICAL)
    if critical:
        return HealthStatus.UNHEALTHY, critical
    status = max((result.health_status for result in values), key=_PRECEDENCE.__getitem__)
    causing = tuple(result for result in values if result.health_status is status)
    return status, causing