"""Explicit system-boundary health inputs; unchecked state remains unknown."""
from __future__ import annotations
from dataclasses import dataclass
from secure_eval_wrapper.monitoring.events import make_result
from secure_eval_wrapper.monitoring.models import HealthStatus, MonitoringCategory, MonitoredComponent, Severity

@dataclass(frozen=True)
class SystemHealthInput:
    expected_migration_version: str = "0014"
    observed_migration_version: str | None = None
    migration_hashes_match: bool | None = None
    expected_schema_objects_present: bool | None = None
    postgresql_available: bool | None = None
    persistence_transaction_ok: bool | None = None
    package_version_matches: bool | None = None
    source_tree_identity_matches: bool | None = None
    status_files_synchronized: bool | None = None
    live_trading_disabled: bool | None = None
    postgresql_only_authority: bool | None = None
    private_path_boundary_clean: bool | None = None
    configuration_valid: bool | None = None
    engine_exception: str | None = None
    fix_session_healthy: bool | None = None


def evaluate_system_health(context, value: SystemHealthInput | None, configuration) -> tuple:
    c=MonitoringCategory.SYSTEM; component=MonitoredComponent.APPLICATION.value
    if value is None: return (make_result(context,category=c,component=component,check_name="system_input",health_status=HealthStatus.UNKNOWN,reason_code="system_input_unavailable",explanation="No explicit system verification results were supplied."),)
    out=[]
    checks=(
        ("migration_catalog", value.observed_migration_version==value.expected_migration_version if value.observed_migration_version is not None else None, "migration_version_mismatch", {"expected":value.expected_migration_version,"observed":value.observed_migration_version}),
        ("migration_hashes",value.migration_hashes_match,"migration_hash_conflict",None),
        ("schema_objects",value.expected_schema_objects_present,"expected_schema_object_missing",None),
        ("postgresql_availability",value.postgresql_available,"postgresql_unavailable",None),
        ("persistence_transaction",value.persistence_transaction_ok,"persistence_transaction_failure",None),
        ("package_version",value.package_version_matches,"package_version_mismatch",None),
        ("source_tree_identity",value.source_tree_identity_matches,"source_tree_identity_mismatch",None),
        ("status_file_synchronization",value.status_files_synchronized,"status_files_inconsistent",None),
        ("live_trading_disabled",value.live_trading_disabled,"live_trading_not_disabled",None),
        ("postgresql_only_authority",value.postgresql_only_authority,"non_postgresql_authority",None),
        ("private_path_boundary",value.private_path_boundary_clean,"private_generated_path_detected",None),
        ("configuration",value.configuration_valid,"invalid_configuration",None),
    )
    for name, ok, failure, observed in checks:
        status=HealthStatus.UNKNOWN if ok is None else HealthStatus.HEALTHY if ok else HealthStatus.UNHEALTHY
        out.append(make_result(context,category=c,component=MonitoredComponent.POSTGRESQL.value if name in {"migration_catalog","migration_hashes","schema_objects","postgresql_availability","persistence_transaction"} else component,check_name=name,health_status=status,reason_code=f"{name}_unchecked" if ok is None else f"{name}_healthy" if ok else failure,explanation=f"Explicit {name.replace('_',' ')} evidence was evaluated; absent evidence is unknown.",observed=observed,severity=Severity.CRITICAL if ok is False and name in {"live_trading_disabled","postgresql_only_authority","private_path_boundary"} else None))
    exception_ok=value.engine_exception is None
    out.append(make_result(context,category=c,component=component,check_name="monitoring_engine_exception",health_status=HealthStatus.HEALTHY if exception_ok else HealthStatus.UNHEALTHY,reason_code="monitoring_engine_healthy" if exception_ok else "monitoring_engine_exception",explanation="The monitoring evaluation exception channel was checked.",observed=value.engine_exception))
    fix=value.fix_session_healthy; out.append(make_result(context,category=MonitoringCategory.FIX_SESSION,component=MonitoredComponent.FIX_SESSION.value,check_name="fix_session_health",health_status=HealthStatus.UNKNOWN if fix is None else HealthStatus.HEALTHY if fix else HealthStatus.UNHEALTHY,reason_code="fix_session_unchecked" if fix is None else "fix_session_healthy" if fix else "fix_session_unhealthy",explanation="Simulated FIX heartbeat/session health was supplied explicitly; it was not inferred."))
    return tuple(out)
