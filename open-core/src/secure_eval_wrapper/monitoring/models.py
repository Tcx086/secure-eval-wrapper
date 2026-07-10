"""Immutable deterministic monitoring contracts for public-safe offline evaluation."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from enum import Enum
from types import MappingProxyType
from typing import Mapping
from uuid import NAMESPACE_URL, UUID, uuid5

from secure_eval_wrapper.data_collection.hashing import sha256_payload
from secure_eval_wrapper.data_collection.time_utils import require_utc_datetime

_SHA256 = re.compile(r"^[0-9a-f]{64}$")


def _text(value: str, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be non-empty")
    return value.strip()


def _hash(value: str, name: str) -> str:
    if not isinstance(value, str) or not _SHA256.fullmatch(value):
        raise ValueError(f"{name} must be a lowercase SHA-256 digest")
    return value


def _mapping(value: Mapping[str, object]) -> Mapping[str, object]:
    return MappingProxyType(dict(value))


def deterministic_monitoring_uuid(kind: str, payload: object) -> UUID:
    return uuid5(NAMESPACE_URL, f"secure-eval-wrapper:monitoring:{kind}:{sha256_payload(payload)}")


class MonitoringCategory(str, Enum):
    DATA = "data"
    SIGNAL = "signal"
    EXECUTION = "execution"
    RISK = "risk"
    SYSTEM = "system"
    FIX_SESSION = "fix_session"


class HealthStatus(str, Enum):
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    UNHEALTHY = "unhealthy"
    UNKNOWN = "unknown"


class Severity(str, Enum):
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"
    CRITICAL = "critical"


class CheckStatus(str, Enum):
    PASSED = "passed"
    WARNING = "warning"
    FAILED = "failed"
    UNKNOWN = "unknown"


class IncidentState(str, Enum):
    OPEN = "open"
    ACKNOWLEDGED = "acknowledged"
    RESOLVED = "resolved"


class MonitoringEventType(str, Enum):
    CHECK_EVALUATED = "check_evaluated"
    HEALTH_CHANGED = "health_changed"
    INCIDENT_OPENED = "incident_opened"
    INCIDENT_UPDATED = "incident_updated"
    INCIDENT_RESOLVED = "incident_resolved"
    SESSION_TRANSITION = "session_transition"
    FAULT_INJECTED = "fault_injected"
    LATENCY_BREACH = "latency_breach"


class MonitoredComponent(str, Enum):
    MARKET_DATA = "market_data"
    FUNDING_DATA = "funding_data"
    ALPHA_PIPELINE = "alpha_pipeline"
    SIGNAL_PIPELINE = "signal_pipeline"
    SIMULATED_EXECUTION = "simulated_execution"
    RISK_GUARD = "risk_guard"
    POSTGRESQL = "postgresql"
    APPLICATION = "application"
    FIX_SESSION = "fix_session"
    OVERALL = "overall"


class ThresholdComparison(str, Enum):
    GREATER_THAN = "greater_than"
    GREATER_THAN_OR_EQUAL = "greater_than_or_equal"
    LESS_THAN = "less_than"
    LESS_THAN_OR_EQUAL = "less_than_or_equal"
    EQUAL = "equal"
    NOT_EQUAL = "not_equal"

    def evaluate(self, observed: Decimal, threshold: Decimal) -> bool:
        if not isinstance(observed, Decimal) or not observed.is_finite():
            raise ValueError("observed must be a finite Decimal")
        if not isinstance(threshold, Decimal) or not threshold.is_finite():
            raise ValueError("threshold must be a finite Decimal")
        return {
            self.GREATER_THAN: observed > threshold,
            self.GREATER_THAN_OR_EQUAL: observed >= threshold,
            self.LESS_THAN: observed < threshold,
            self.LESS_THAN_OR_EQUAL: observed <= threshold,
            self.EQUAL: observed == threshold,
            self.NOT_EQUAL: observed != threshold,
        }[self]


@dataclass(frozen=True)
class PublicSafeProvenance:
    implementation_code_sha256: str
    repository_commit_sha: str
    stable_source_identity: str
    operational_metadata: Mapping[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        _hash(self.implementation_code_sha256, "implementation_code_sha256")
        _text(self.repository_commit_sha, "repository_commit_sha")
        _text(self.stable_source_identity, "stable_source_identity")
        object.__setattr__(self, "operational_metadata", _mapping(self.operational_metadata))

    @property
    def stable_payload(self) -> dict[str, object]:
        return {
            "implementation_code_sha256": self.implementation_code_sha256,
            "repository_commit_sha": self.repository_commit_sha,
            "stable_source_identity": self.stable_source_identity,
        }


@dataclass(frozen=True)
class MonitoredRunReference:
    monitored_identity: str
    monitored_run_id: UUID | None = None
    mode: str = "public_simulation"

    def __post_init__(self) -> None:
        _text(self.monitored_identity, "monitored_identity")
        _text(self.mode, "mode")
        if self.mode != "public_simulation":
            raise ValueError("Phase 6 can monitor public_simulation mode only")


@dataclass(frozen=True)
class HealthCheckDefinition:
    check_name: str
    category: MonitoringCategory
    component: MonitoredComponent | str
    enabled: bool = True
    comparison: ThresholdComparison | None = None
    warning_threshold: Decimal | None = None
    critical_threshold: Decimal | None = None
    unknown_when_unavailable: bool = True

    def __post_init__(self) -> None:
        object.__setattr__(self, "check_name", _text(self.check_name, "check_name"))
        object.__setattr__(self, "category", MonitoringCategory(self.category))
        component = self.component.value if isinstance(self.component, MonitoredComponent) else _text(self.component, "component")
        object.__setattr__(self, "component", component)
        if self.comparison is not None:
            object.__setattr__(self, "comparison", ThresholdComparison(self.comparison))
        for name in ("warning_threshold", "critical_threshold"):
            value = getattr(self, name)
            if value is not None and (not isinstance(value, Decimal) or not value.is_finite()):
                raise ValueError(f"{name} must be a finite Decimal")
        if (self.warning_threshold is not None or self.critical_threshold is not None) and self.comparison is None:
            raise ValueError("threshold checks require a comparison")


@dataclass(frozen=True)
class HealthCheckResult:
    monitoring_run_id: UUID
    evaluation_at_utc: datetime
    category: MonitoringCategory
    component: MonitoredComponent | str
    check_name: str
    status: CheckStatus
    health_status: HealthStatus
    severity: Severity
    reason_code: str
    explanation: str
    configuration_sha256: str
    stable_input_sha256: str
    provenance: PublicSafeProvenance
    observed_value: object | None = None
    configured_threshold: object | None = None
    parent_ids: tuple[UUID, ...] = ()
    health_check_result_id: UUID | None = None

    def __post_init__(self) -> None:
        require_utc_datetime(self.evaluation_at_utc, field_name="HealthCheckResult evaluation_at_utc")
        object.__setattr__(self, "category", MonitoringCategory(self.category))
        component = self.component.value if isinstance(self.component, MonitoredComponent) else _text(self.component, "component")
        object.__setattr__(self, "component", component)
        object.__setattr__(self, "check_name", _text(self.check_name, "check_name"))
        object.__setattr__(self, "status", CheckStatus(self.status))
        object.__setattr__(self, "health_status", HealthStatus(self.health_status))
        object.__setattr__(self, "severity", Severity(self.severity))
        object.__setattr__(self, "reason_code", _text(self.reason_code, "reason_code"))
        object.__setattr__(self, "explanation", _text(self.explanation, "explanation"))
        _hash(self.configuration_sha256, "configuration_sha256")
        _hash(self.stable_input_sha256, "stable_input_sha256")
        if self.status is CheckStatus.PASSED and self.health_status is not HealthStatus.HEALTHY:
            raise ValueError("a passed check must be healthy")
        if self.status is CheckStatus.UNKNOWN and self.health_status is not HealthStatus.UNKNOWN:
            raise ValueError("an unknown check must have unknown health")
        expected = deterministic_monitoring_uuid("check-result", self.logical_payload)
        if self.health_check_result_id is not None and self.health_check_result_id != expected:
            raise ValueError("health_check_result_id does not match deterministic identity")
        object.__setattr__(self, "health_check_result_id", expected)

    @property
    def logical_payload(self) -> dict[str, object]:
        return {"monitoring_run_id": self.monitoring_run_id, "category": self.category, "component": self.component, "check_name": self.check_name}

    @property
    def record_sha256(self) -> str:
        return sha256_payload({**self.logical_payload, "evaluation_at_utc": self.evaluation_at_utc, "status": self.status, "health_status": self.health_status, "severity": self.severity, "reason_code": self.reason_code, "explanation": self.explanation, "observed_value": self.observed_value, "configured_threshold": self.configured_threshold, "configuration_sha256": self.configuration_sha256, "stable_input_sha256": self.stable_input_sha256, "provenance": self.provenance.stable_payload, "parent_ids": self.parent_ids})


@dataclass(frozen=True)
class HealthSnapshot:
    monitoring_run_id: UUID
    evaluation_at_utc: datetime
    component: MonitoredComponent | str
    health_status: HealthStatus
    causing_check_ids: tuple[UUID, ...]
    reason_code: str
    explanation: str
    configuration_sha256: str
    stable_input_sha256: str
    provenance: PublicSafeProvenance
    category: MonitoringCategory | None = None
    parent_ids: tuple[UUID, ...] = ()
    health_snapshot_id: UUID | None = None

    def __post_init__(self) -> None:
        require_utc_datetime(self.evaluation_at_utc, field_name="HealthSnapshot evaluation_at_utc")
        component = self.component.value if isinstance(self.component, MonitoredComponent) else _text(self.component, "component")
        object.__setattr__(self, "component", component)
        object.__setattr__(self, "health_status", HealthStatus(self.health_status))
        if self.category is not None:
            object.__setattr__(self, "category", MonitoringCategory(self.category))
        _text(self.reason_code, "reason_code"); _text(self.explanation, "explanation")
        _hash(self.configuration_sha256, "configuration_sha256"); _hash(self.stable_input_sha256, "stable_input_sha256")
        expected = deterministic_monitoring_uuid("health-snapshot", {"monitoring_run_id": self.monitoring_run_id, "component": component})
        if self.health_snapshot_id is not None and self.health_snapshot_id != expected:
            raise ValueError("health_snapshot_id does not match deterministic identity")
        object.__setattr__(self, "health_snapshot_id", expected)

    @property
    def record_sha256(self) -> str:
        return sha256_payload({"health_snapshot_id": self.health_snapshot_id, "monitoring_run_id": self.monitoring_run_id, "evaluation_at_utc": self.evaluation_at_utc, "category": self.category, "component": self.component, "health_status": self.health_status, "causing_check_ids": self.causing_check_ids, "reason_code": self.reason_code, "explanation": self.explanation, "configuration_sha256": self.configuration_sha256, "stable_input_sha256": self.stable_input_sha256, "provenance": self.provenance.stable_payload, "parent_ids": self.parent_ids})


@dataclass(frozen=True)
class MonitoringEvent:
    monitoring_run_id: UUID
    evaluation_at_utc: datetime
    category: MonitoringCategory
    component: str
    event_type: MonitoringEventType
    severity: Severity
    reason_code: str
    explanation: str
    configuration_sha256: str
    stable_input_sha256: str
    provenance: PublicSafeProvenance
    details: Mapping[str, object] = field(default_factory=dict)
    parent_ids: tuple[UUID, ...] = ()
    monitoring_event_id: UUID | None = None

    def __post_init__(self) -> None:
        require_utc_datetime(self.evaluation_at_utc, field_name="MonitoringEvent evaluation_at_utc")
        object.__setattr__(self, "category", MonitoringCategory(self.category)); object.__setattr__(self, "component", _text(self.component, "component"))
        object.__setattr__(self, "event_type", MonitoringEventType(self.event_type)); object.__setattr__(self, "severity", Severity(self.severity))
        _text(self.reason_code, "reason_code"); _text(self.explanation, "explanation")
        _hash(self.configuration_sha256, "configuration_sha256"); _hash(self.stable_input_sha256, "stable_input_sha256")
        object.__setattr__(self, "details", _mapping(self.details))
        expected = deterministic_monitoring_uuid("event", {"monitoring_run_id": self.monitoring_run_id, "category": self.category, "component": self.component, "event_type": self.event_type, "reason_code": self.reason_code, "parent_ids": self.parent_ids})
        if self.monitoring_event_id is not None and self.monitoring_event_id != expected:
            raise ValueError("monitoring_event_id does not match deterministic identity")
        object.__setattr__(self, "monitoring_event_id", expected)

    @property
    def record_sha256(self) -> str:
        return sha256_payload({"monitoring_event_id": self.monitoring_event_id, "evaluation_at_utc": self.evaluation_at_utc, "severity": self.severity, "explanation": self.explanation, "details": dict(self.details), "configuration_sha256": self.configuration_sha256, "stable_input_sha256": self.stable_input_sha256, "provenance": self.provenance.stable_payload})


@dataclass(frozen=True)
class MonitoringIncident:
    category: MonitoringCategory
    component: str
    reason_code: str
    monitored_identity: str
    state: IncidentState
    severity: Severity
    episode_started_at_utc: datetime
    latest_at_utc: datetime
    occurrence_count: int
    configuration_sha256: str
    stable_input_sha256: str
    resolved_at_utc: datetime | None = None
    incident_id: UUID | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "category", MonitoringCategory(self.category)); object.__setattr__(self, "component", _text(self.component, "component"))
        object.__setattr__(self, "state", IncidentState(self.state)); object.__setattr__(self, "severity", Severity(self.severity))
        _text(self.reason_code, "reason_code"); _text(self.monitored_identity, "monitored_identity")
        require_utc_datetime(self.episode_started_at_utc, field_name="incident episode_started_at_utc"); require_utc_datetime(self.latest_at_utc, field_name="incident latest_at_utc")
        if self.latest_at_utc < self.episode_started_at_utc or self.occurrence_count <= 0:
            raise ValueError("incident episode timestamps/count are invalid")
        if self.resolved_at_utc is not None:
            require_utc_datetime(self.resolved_at_utc, field_name="incident resolved_at_utc")
        if self.state is IncidentState.RESOLVED and self.resolved_at_utc is None:
            raise ValueError("resolved incident requires resolved_at_utc")
        _hash(self.configuration_sha256, "configuration_sha256"); _hash(self.stable_input_sha256, "stable_input_sha256")
        expected = deterministic_monitoring_uuid("incident-episode", {"category": self.category, "component": self.component, "reason_code": self.reason_code, "monitored_identity": self.monitored_identity, "episode_started_at_utc": self.episode_started_at_utc})
        if self.incident_id is not None and self.incident_id != expected:
            raise ValueError("incident_id does not match deterministic episode identity")
        object.__setattr__(self, "incident_id", expected)

    @property
    def record_sha256(self) -> str:
        return sha256_payload({name: getattr(self, name) for name in ("incident_id","category","component","reason_code","monitored_identity","state","severity","episode_started_at_utc","latest_at_utc","resolved_at_utc","occurrence_count","configuration_sha256","stable_input_sha256")})


@dataclass(frozen=True)
class IncidentOccurrence:
    incident_id: UUID
    monitoring_run_id: UUID
    health_check_result_id: UUID
    occurred_at_utc: datetime
    incident_occurrence_id: UUID | None = None

    def __post_init__(self) -> None:
        require_utc_datetime(self.occurred_at_utc, field_name="IncidentOccurrence occurred_at_utc")
        expected = deterministic_monitoring_uuid("incident-occurrence", {"incident_id": self.incident_id, "health_check_result_id": self.health_check_result_id})
        if self.incident_occurrence_id is not None and self.incident_occurrence_id != expected:
            raise ValueError("incident_occurrence_id does not match deterministic identity")
        object.__setattr__(self, "incident_occurrence_id", expected)

    @property
    def record_sha256(self) -> str:
        return sha256_payload({"incident_occurrence_id": self.incident_occurrence_id, "incident_id": self.incident_id, "monitoring_run_id": self.monitoring_run_id, "health_check_result_id": self.health_check_result_id, "occurred_at_utc": self.occurred_at_utc})


@dataclass(frozen=True)
class MonitoringRun:
    reference: MonitoredRunReference
    as_of_utc: datetime
    configuration_sha256: str
    stable_input_sha256: str
    provenance: PublicSafeProvenance
    overall_status: HealthStatus
    parent_ids: tuple[UUID, ...] = ()
    monitoring_run_id: UUID | None = None

    def __post_init__(self) -> None:
        require_utc_datetime(self.as_of_utc, field_name="MonitoringRun as_of_utc"); _hash(self.configuration_sha256, "configuration_sha256"); _hash(self.stable_input_sha256, "stable_input_sha256")
        object.__setattr__(self, "overall_status", HealthStatus(self.overall_status))
        expected = deterministic_monitoring_uuid("run", {"reference": {"monitored_identity": self.reference.monitored_identity, "monitored_run_id": self.reference.monitored_run_id, "mode": self.reference.mode}, "as_of_utc": self.as_of_utc, "configuration_sha256": self.configuration_sha256, "stable_input_sha256": self.stable_input_sha256, "provenance": self.provenance.stable_payload})
        if self.monitoring_run_id is not None and self.monitoring_run_id != expected:
            raise ValueError("monitoring_run_id does not match deterministic identity")
        object.__setattr__(self, "monitoring_run_id", expected)

    @property
    def record_sha256(self) -> str:
        return sha256_payload({"monitoring_run_id": self.monitoring_run_id, "overall_status": self.overall_status, "parent_ids": self.parent_ids})


@dataclass(frozen=True)
class MonitoringBundle:
    run: MonitoringRun
    check_results: tuple[HealthCheckResult, ...]
    snapshots: tuple[HealthSnapshot, ...]
    events: tuple[MonitoringEvent, ...]
    incidents: tuple[MonitoringIncident, ...] = ()
    incident_occurrences: tuple[IncidentOccurrence, ...] = ()
    latency_samples: tuple[object, ...] = ()
    fix_observations: tuple[object, ...] = ()