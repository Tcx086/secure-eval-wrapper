"""One deterministic, offline, point-in-time monitoring evaluation engine."""
from __future__ import annotations
from dataclasses import asdict, dataclass
from datetime import datetime
from types import SimpleNamespace

from secure_eval_wrapper.data_collection.hashing import sha256_payload
from secure_eval_wrapper.data_collection.time_utils import require_utc_datetime
from secure_eval_wrapper.monitoring.configuration import MonitoringConfiguration
from secure_eval_wrapper.monitoring.data_health import DataHealthInput, evaluate_data_health
from secure_eval_wrapper.monitoring.execution_health import ExecutionHealthInput, evaluate_execution_health
from secure_eval_wrapper.monitoring.health import aggregate_health
from secure_eval_wrapper.monitoring.models import (
    HealthSnapshot, HealthStatus, IncidentOccurrence, IncidentState, MonitoredComponent,
    MonitoredRunReference, MonitoringBundle, MonitoringEvent, MonitoringEventType,
    MonitoringIncident, MonitoringRun, PublicSafeProvenance, Severity,
    deterministic_monitoring_uuid,
)
from secure_eval_wrapper.monitoring.risk_health import RiskHealthInput, evaluate_risk_health
from secure_eval_wrapper.monitoring.signal_health import SignalHealthInput, evaluate_signal_health
from secure_eval_wrapper.monitoring.system_health import SystemHealthInput, evaluate_system_health

@dataclass(frozen=True)
class MonitoringInputs:
    data: DataHealthInput | None = None
    signals: SignalHealthInput | None = None
    execution: ExecutionHealthInput | None = None
    risk: RiskHealthInput | None = None
    system: SystemHealthInput | None = None
    simulated_fix_state: object | None = None

    @property
    def stable_input_sha256(self) -> str:
        return sha256_payload(asdict(self))


class MonitoringEngine:
    def __init__(self, repository=None) -> None:
        self.repository = repository

    def evaluate(
        self,
        *,
        configuration: MonitoringConfiguration,
        as_of_utc: datetime,
        inputs: MonitoringInputs,
        reference: MonitoredRunReference,
        provenance: PublicSafeProvenance,
        previous_incidents: tuple[MonitoringIncident, ...] = (),
        persist: bool = False,
    ) -> MonitoringBundle:
        require_utc_datetime(as_of_utc, field_name="MonitoringEngine as_of_utc")
        if not isinstance(configuration, MonitoringConfiguration) or not isinstance(inputs, MonitoringInputs):
            raise TypeError("configuration and inputs must use immutable monitoring contracts")
        stable_input_sha256=inputs.stable_input_sha256
        provisional=MonitoringRun(reference=reference,as_of_utc=as_of_utc,configuration_sha256=configuration.config_sha256,stable_input_sha256=stable_input_sha256,provenance=provenance,overall_status=HealthStatus.UNKNOWN,parent_ids=() if reference.monitored_run_id is None else (reference.monitored_run_id,))
        context=SimpleNamespace(monitoring_run_id=provisional.monitoring_run_id,as_of_utc=as_of_utc,configuration_sha256=configuration.config_sha256,stable_input_sha256=stable_input_sha256,provenance=provenance)
        checks=[]
        checks.extend(evaluate_data_health(context,inputs.data,configuration))
        checks.extend(evaluate_signal_health(context,inputs.signals,configuration))
        checks.extend(evaluate_execution_health(context,inputs.execution,configuration))
        checks.extend(evaluate_risk_health(context,inputs.risk,configuration))
        system_input=inputs.system
        if system_input is not None and inputs.simulated_fix_state is not None and system_input.fix_session_healthy is None:
            state=getattr(inputs.simulated_fix_state,"state",None); healthy=getattr(state,"value",state)=="established"
            system_input=SystemHealthInput(**{**system_input.__dict__,"fix_session_healthy":healthy})
        checks.extend(evaluate_system_health(context,system_input,configuration))
        checks=tuple(result for result in checks if configuration.enabled(result.check_name))
        grouped={}
        for result in checks: grouped.setdefault((result.category,result.component),[]).append(result)
        snapshots=[]
        for (category,component),children in sorted(grouped.items(),key=lambda item:(item[0][0].value,item[0][1])):
            status,causing=aggregate_health(children)
            snapshots.append(HealthSnapshot(monitoring_run_id=provisional.monitoring_run_id,evaluation_at_utc=as_of_utc,category=category,component=component,health_status=status,causing_check_ids=tuple(item.health_check_result_id for item in causing),reason_code=f"{component}_{status.value}",explanation=f"{component} health is {status.value} under explicit precedence.",configuration_sha256=configuration.config_sha256,stable_input_sha256=stable_input_sha256,provenance=provenance,parent_ids=tuple(item.health_check_result_id for item in children)))
        overall_status,overall_causing=aggregate_health(checks)
        snapshots.append(HealthSnapshot(monitoring_run_id=provisional.monitoring_run_id,evaluation_at_utc=as_of_utc,component=MonitoredComponent.OVERALL,health_status=overall_status,causing_check_ids=tuple(item.health_check_result_id for item in overall_causing),reason_code=f"overall_{overall_status.value}",explanation="Overall health follows critical-unhealthy, unhealthy, degraded, unknown, healthy precedence.",configuration_sha256=configuration.config_sha256,stable_input_sha256=stable_input_sha256,provenance=provenance,parent_ids=tuple(item.health_check_result_id for item in checks)))
        run=MonitoringRun(reference=reference,as_of_utc=as_of_utc,configuration_sha256=configuration.config_sha256,stable_input_sha256=stable_input_sha256,provenance=provenance,overall_status=overall_status,parent_ids=provisional.parent_ids,monitoring_run_id=provisional.monitoring_run_id)
        events=list(MonitoringEvent(monitoring_run_id=run.monitoring_run_id,evaluation_at_utc=as_of_utc,category=result.category,component=result.component,event_type=MonitoringEventType.CHECK_EVALUATED,severity=result.severity,reason_code=result.reason_code,explanation=result.explanation,configuration_sha256=configuration.config_sha256,stable_input_sha256=stable_input_sha256,provenance=provenance,details={"status":result.health_status.value},parent_ids=(result.health_check_result_id,)) for result in checks if result.health_status is not HealthStatus.HEALTHY)
        incidents,occurrences=self._evaluate_incidents(configuration,reference,checks,previous_incidents,as_of_utc,run.monitoring_run_id)
        for incident in incidents:
            event_type = MonitoringEventType.INCIDENT_RESOLVED if incident.state is IncidentState.RESOLVED else MonitoringEventType.INCIDENT_OPENED if incident.occurrence_count == 1 else MonitoringEventType.INCIDENT_UPDATED
            events.append(MonitoringEvent(monitoring_run_id=run.monitoring_run_id,evaluation_at_utc=as_of_utc,category=incident.category,component=incident.component,event_type=event_type,severity=incident.severity,reason_code=incident.reason_code,explanation=f"Incident episode {event_type.value.replace('_', ' ')}.",configuration_sha256=configuration.config_sha256,stable_input_sha256=stable_input_sha256,provenance=provenance,details={"incident_state":incident.state.value,"occurrence_count":incident.occurrence_count},parent_ids=(incident.incident_id,)))
        bundle=MonitoringBundle(run=run,check_results=checks,snapshots=tuple(snapshots),events=tuple(events),incidents=incidents,incident_occurrences=occurrences)
        if persist:
            if self.repository is None: raise ValueError("persist=True requires an injected PostgreSQL monitoring repository")
            from secure_eval_wrapper.monitoring.persistence import persist_monitoring_bundle
            persist_monitoring_bundle(self.repository,bundle)
        return bundle

    @staticmethod
    def _evaluate_incidents(configuration, reference, checks, previous, as_of_utc, monitoring_run_id):
        if not configuration.incident_management_enabled:
            return (), ()
        active = {
            (item.category, item.component, item.reason_code, item.monitored_identity): item
            for item in previous
            if item.state in (IncidentState.OPEN, IncidentState.ACKNOWLEDGED)
        }
        evidence_by_check = {(result.category, result.component, result.check_name): result for result in checks}
        failing = {}
        for result in checks:
            if result.health_status in (HealthStatus.DEGRADED, HealthStatus.UNHEALTHY):
                failing[(result.category, result.component, result.reason_code, reference.monitored_identity)] = result
        incidents = []
        occurrences = []
        for key, result in sorted(failing.items(), key=lambda item: tuple(str(v) for v in item[0])):
            prior = active.get(key)
            if prior is None:
                incident = MonitoringIncident(
                    category=result.category, component=result.component, check_name=result.check_name,
                    reason_code=result.reason_code, monitored_identity=reference.monitored_identity,
                    state=IncidentState.OPEN, severity=result.severity,
                    episode_started_at_utc=as_of_utc, latest_at_utc=as_of_utc, occurrence_count=1,
                    configuration_sha256=configuration.config_sha256, stable_input_sha256=result.stable_input_sha256,
                )
            else:
                incident = MonitoringIncident(
                    category=prior.category, component=prior.component, check_name=prior.check_name,
                    reason_code=prior.reason_code, monitored_identity=prior.monitored_identity,
                    state=prior.state,
                    severity=max((prior.severity, result.severity), key=lambda s: (Severity.INFO, Severity.WARNING, Severity.ERROR, Severity.CRITICAL).index(s)),
                    episode_started_at_utc=prior.episode_started_at_utc, latest_at_utc=as_of_utc,
                    occurrence_count=prior.occurrence_count + 1,
                    configuration_sha256=configuration.config_sha256, stable_input_sha256=result.stable_input_sha256,
                    incident_id=prior.incident_id,
                )
            incidents.append(incident)
            occurrences.append(IncidentOccurrence(
                incident_id=incident.incident_id, monitoring_run_id=monitoring_run_id,
                health_check_result_id=result.health_check_result_id, occurred_at_utc=as_of_utc,
            ))
        for key, prior in active.items():
            if key in failing:
                continue
            evidence = evidence_by_check.get((prior.category, prior.component, prior.check_name))
            if evidence is None or evidence.health_status is HealthStatus.UNKNOWN:
                incidents.append(prior)
                continue
            if evidence.health_status is HealthStatus.HEALTHY:
                incidents.append(MonitoringIncident(
                    category=prior.category, component=prior.component, check_name=prior.check_name,
                    reason_code=prior.reason_code, monitored_identity=prior.monitored_identity,
                    state=IncidentState.RESOLVED, severity=prior.severity,
                    episode_started_at_utc=prior.episode_started_at_utc, latest_at_utc=as_of_utc,
                    resolved_at_utc=as_of_utc, occurrence_count=prior.occurrence_count,
                    configuration_sha256=configuration.config_sha256, stable_input_sha256=evidence.stable_input_sha256,
                    incident_id=prior.incident_id,
                ))
            else:
                incidents.append(prior)
        return tuple(incidents), tuple(occurrences)
