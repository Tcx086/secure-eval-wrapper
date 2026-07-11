"""Deterministic in-process fault schedule and orchestrator."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta

from secure_eval_wrapper.fix.models import (
    ConnectionFault,
    ConnectionFaultType,
    FixMessage,
    FixMessageType,
    FixSessionEventType,
)


@dataclass
class FaultSchedule:
    faults: tuple[ConnectionFault, ...] = ()

    def __post_init__(self):
        self.faults = tuple(sorted(self.faults, key=lambda f: (f.scheduled_at_utc, f.fault_type.value, str(f.connection_fault_id))))
        self._activated = set()

    def due(self, at_utc, *, fault_type=None):
        kind = None if fault_type is None else ConnectionFaultType(fault_type)
        return tuple(f for f in self.faults if f.connection_fault_id not in self._activated and f.scheduled_at_utc <= at_utc and (kind is None or f.fault_type is kind))

    def activate(self, fault, at_utc):
        if fault.connection_fault_id in self._activated:
            return fault
        self._activated.add(fault.connection_fault_id)
        return ConnectionFault(
            fix_session_id=fault.fix_session_id,
            fault_type=fault.fault_type,
            scheduled_at_utc=fault.scheduled_at_utc,
            reason_code=fault.reason_code,
            configuration=fault.configuration,
            activated_at_utc=at_utc,
        )


@dataclass(frozen=True)
class FaultActivationEvidence:
    connection_fault_id: object
    fault_type: ConnectionFaultType
    activated_at_utc: object
    session_event_id: object
    health_status: str
    reason_code: str
    outcome: str


class FaultOrchestrator:
    """Applies only recorded, scheduled, deterministic in-process faults."""

    def __init__(self, schedule: FaultSchedule, session):
        self.schedule = schedule
        self.session = session
        self.activated_faults = []
        self.monitoring_evidence = []
        self.delayed_outbound = []
        self._duplicate_pending = False

    def _activate_one(self, kind, at, outcome):
        due = self.schedule.due(at, fault_type=kind)
        if not due:
            return None
        activated = self.schedule.activate(due[0], at)
        event = self.session._event(
            FixSessionEventType.FAULT_ACTIVATED,
            at,
            self.session.state,
            self.session.state,
            f"fault_activated:{activated.fault_type.value}",
        )
        self.activated_faults.append(activated)
        self.monitoring_evidence.append(FaultActivationEvidence(
            activated.connection_fault_id,
            activated.fault_type,
            at,
            event.event_id,
            "unhealthy",
            activated.reason_code,
            outcome,
        ))
        return activated

    def before_inbound(self, message, at):
        if message.msg_type is FixMessageType.LOGON:
            fault = self._activate_one(ConnectionFaultType.DROP_BEFORE_LOGON, at, "connection dropped before Logon processing")
            if fault is not None:
                self.session.drop(at, fault.reason_code)
                return None
        gap = self._activate_one(ConnectionFaultType.INBOUND_SEQUENCE_GAP, at, "inbound MsgSeqNum deterministically increased")
        if gap is not None:
            size = int(gap.configuration.get("gap_size", 1))
            return FixMessage(
                msg_type=message.msg_type,
                msg_seq_num=message.msg_seq_num + size,
                sender_comp_id=message.sender_comp_id,
                target_comp_id=message.target_comp_id,
                sending_time_utc=message.sending_time_utc,
                fields=message.fields,
                extensions=message.extensions,
            )
        duplicate = self._activate_one(ConnectionFaultType.DUPLICATE_INBOUND, at, "same inbound message delivered twice; economic handling remains once")
        self._duplicate_pending = duplicate is not None
        return message

    def after_session_receive(self, message, at):
        if self._duplicate_pending:
            self._duplicate_pending = False
            self.session.receive(message, at)

    def after_gateway_response(self, message, responses, at, *, gateway):
        responses = tuple(responses)
        if message.msg_type is FixMessageType.TEST_REQUEST:
            if self._activate_one(ConnectionFaultType.HEARTBEAT_RESPONSE_LOSS, at, "Heartbeat response suppressed") is not None:
                responses = tuple(item for item in responses if item.msg_type is not FixMessageType.HEARTBEAT)
        if message.msg_type is FixMessageType.NEW_ORDER_SINGLE and responses:
            fault = self._activate_one(ConnectionFaultType.DROP_AFTER_ACKNOWLEDGEMENT, at, "acknowledgement emitted, then connection dropped")
            if fault is not None:
                self.session.drop(at, fault.reason_code)
            if gateway.broker.active_orders():
                fault = self._activate_one(ConnectionFaultType.DROP_ACTIVE_ORDER, at, "connection dropped while simulated order remained active")
                if fault is not None:
                    self.session.drop(at, fault.reason_code)
        return self._delay_reports(responses, at)

    def after_market_event(self, reports, at, *, gateway):
        return self._delay_reports(tuple(reports), at)

    def _delay_reports(self, reports, at):
        if not reports:
            return reports
        fault = self._activate_one(ConnectionFaultType.DELAYED_OUTBOUND_REPORT, at, "outbound reports queued until deterministic release")
        if fault is None:
            return reports
        delay = float(fault.configuration.get("delay_seconds", 1))
        self.delayed_outbound.append((at + timedelta(seconds=delay), reports))
        return ()

    def release_delayed(self, at):
        ready = []
        remaining = []
        for release_at, reports in self.delayed_outbound:
            if release_at <= at:
                ready.extend(reports)
            else:
                remaining.append((release_at, reports))
        self.delayed_outbound = remaining
        return tuple(ready)

    def reconnect(self, at):
        fault = self._activate_one(ConnectionFaultType.RECONNECT_DELAY, at, "reconnect deferred until deterministic deadline")
        if fault is not None:
            delay = float(fault.configuration.get("delay_seconds", 1))
            deadline = fault.scheduled_at_utc + timedelta(seconds=delay)
            if at < deadline:
                return None
        return self.session.reconnect(at)
