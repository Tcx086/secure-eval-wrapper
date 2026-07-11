"""Atomic complete-bundle persistence for monitoring and simulated FIX."""
from __future__ import annotations
from dataclasses import dataclass


class MonitoringBundlePersistenceError(RuntimeError):
    pass


@dataclass(frozen=True)
class MonitoringBundleSummary:
    check_results: int
    snapshots: int
    events: int
    incidents: int
    incident_occurrences: int
    latency_samples: int
    fix_observations: int


def persist_monitoring_bundle(repository, bundle):
    if repository is None or not hasattr(repository, "transaction"):
        raise TypeError("monitoring persistence requires an injected transactional PostgreSQL repository")
    try:
        with repository.transaction():
            repository.record_monitoring_run(bundle.run)
            for value in bundle.check_results: repository.record_health_check_result(value)
            for value in bundle.snapshots: repository.record_health_snapshot(value)
            for value in bundle.events: repository.record_monitoring_event(value)
            for value in bundle.incidents: repository.record_incident(value)
            for value in bundle.incident_occurrences: repository.record_incident_occurrence(value)
            for value in bundle.latency_samples: repository.record_latency_sample(value, monitoring_run_id=bundle.run.monitoring_run_id)
            for value in bundle.fix_observations:
                if hasattr(value, "connection_fault_id"): repository.record_connection_fault(value)
                elif hasattr(value, "fix_order_link_id"): repository.record_fix_order_link(value)
                elif hasattr(value, "rejection_code"): repository.record_rejected_fix_observation(value)
    except Exception as exc:
        raise MonitoringBundlePersistenceError(f"complete monitoring persistence failed: {exc}") from exc
    return MonitoringBundleSummary(len(bundle.check_results), len(bundle.snapshots), len(bundle.events), len(bundle.incidents), len(bundle.incident_occurrences), len(bundle.latency_samples), len(bundle.fix_observations))


def persist_fix_transition(
    repository, *, session, at_utc, inbound_messages=(), outbound_messages=(),
    rejected_observations=(), session_events=(), order_links=(), latency_samples=(), faults=(),
    expected_state_version=None, expected_record_sha256=None,
):
    """Persist messages, immutable events, and the guarded current projection atomically."""
    prior_version = session.persisted_state_version
    prior_hash = session.persisted_record_sha256
    try:
        with repository.transaction():
            repository.record_fix_session(
                session, at_utc,
                expected_state_version=expected_state_version,
                expected_record_sha256=expected_record_sha256,
                last_transition_event_id=session_events[-1].event_id if session_events else None,
            )
            from secure_eval_wrapper.fix.models import FixDirection
            for message in inbound_messages:
                repository.record_fix_message(session.fix_session_id, FixDirection.INBOUND, message, at_utc, None)
            for message in outbound_messages:
                repository.record_fix_message(session.fix_session_id, FixDirection.OUTBOUND, message, at_utc, None)
            for observation in rejected_observations:
                repository.record_rejected_fix_observation(observation)
            for event in session_events:
                repository.record_fix_session_event(session, event)
            for link in order_links: repository.record_fix_order_link(link)
            for sample in latency_samples: repository.record_latency_sample(sample)
            for fault in faults: repository.record_connection_fault(fault)
    except Exception as exc:
        session.persisted_state_version = prior_version
        session.persisted_record_sha256 = prior_hash
        raise MonitoringBundlePersistenceError(f"simulated FIX transition persistence failed: {exc}") from exc
