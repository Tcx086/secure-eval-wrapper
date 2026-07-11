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
    rejected_observations=(), rejected_occurrences=(), session_events=(), order_links=(),
    order_intents=(), risk_decisions=(), orders=(), fills=(), latency_samples=(), faults=(),
    expected_state_version=None, expected_record_sha256=None,
):
    """Persist messages, immutable events, and the guarded current projection atomically."""
    prior_version = session.persisted_state_version
    prior_hash = session.persisted_record_sha256
    prior_transition_sequence = getattr(session, "persisted_transition_sequence", None)
    prior_event_hash = getattr(session, "persisted_event_sha256", None)
    prior_event_id = getattr(session, "persisted_transition_event_id", None)
    if session_events:
        tail_projection = session_events[-1]
        expected_projection = (
            session.state,
            session.next_inbound_seq_num,
            session.next_outbound_seq_num,
            session.last_inbound_at_utc,
            session.last_outbound_at_utc,
            session.pending_test_request_id,
            session.pending_test_deadline_at_utc,
            session.test_request_grace_expired,
        )
        event_projection = (
            tail_projection.new_state,
            tail_projection.projected_next_inbound_seq_num,
            tail_projection.projected_next_outbound_seq_num,
            tail_projection.projected_last_inbound_at_utc,
            tail_projection.projected_last_outbound_at_utc,
            tail_projection.projected_pending_test_request_id,
            tail_projection.projected_pending_test_deadline_at_utc,
            tail_projection.projected_test_request_grace_expired,
        )
        if event_projection != expected_projection:
            raise MonitoringBundlePersistenceError(
                "simulated FIX transition persistence failed: final event snapshot does not match session projection"
            )
    if not rejected_occurrences and rejected_observations:
        observation_ids = {item.observation_id for item in rejected_observations}
        rejected_occurrences = tuple(
            item for item in getattr(session, "rejected_occurrences", ())
            if item.observation_id in observation_ids
        )
    tail = session_events[-1] if session_events else None
    try:
        with repository.transaction():
            repository.record_fix_session(
                session, at_utc,
                expected_state_version=expected_state_version,
                expected_record_sha256=expected_record_sha256,
                last_transition_event_id=None if tail is None else tail.event_id,
                last_transition_sequence=None if tail is None else tail.transition_sequence,
                authoritative_event_sha256=None if tail is None else tail.record_sha256,
            )
            from secure_eval_wrapper.fix.models import FixDirection
            for message in inbound_messages:
                repository.record_fix_message(session.fix_session_id, FixDirection.INBOUND, message, at_utc, None)
            for message in outbound_messages:
                repository.record_fix_message(session.fix_session_id, FixDirection.OUTBOUND, message, at_utc, None)
            for observation in rejected_observations:
                repository.record_rejected_fix_observation(observation)
            for occurrence in rejected_occurrences:
                repository.record_rejected_fix_occurrence(occurrence)
            for event in session_events:
                repository.record_fix_session_event(session, event)
            for value in order_intents:
                repository.record_simulated_fix_order_intent(value)
            for value in orders:
                repository.record_simulated_fix_order(value)
            for value in risk_decisions:
                repository.record_simulated_fix_risk_decision(value)
            for value in fills:
                repository.record_simulated_fix_fill(value)
            for link in order_links:
                repository.record_fix_order_link(link)
            for sample in latency_samples: repository.record_latency_sample(sample)
            for fault in faults: repository.record_connection_fault(fault)
    except Exception as exc:
        session.persisted_state_version = prior_version
        session.persisted_record_sha256 = prior_hash
        session.persisted_transition_sequence = prior_transition_sequence
        session.persisted_event_sha256 = prior_event_hash
        session.persisted_transition_event_id = prior_event_id
        raise MonitoringBundlePersistenceError(f"simulated FIX transition persistence failed: {exc}") from exc
