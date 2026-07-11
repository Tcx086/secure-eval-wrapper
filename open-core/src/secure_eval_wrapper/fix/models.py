"""Immutable contracts for the strictly simulated FIX 4.4-compatible subset."""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from enum import Enum
from types import MappingProxyType
from typing import Mapping
from uuid import NAMESPACE_URL, UUID, uuid5

from secure_eval_wrapper.data_collection.hashing import sha256_payload
from secure_eval_wrapper.data_collection.time_utils import require_utc_datetime


class FixDirection(str, Enum):
    INBOUND = "inbound"
    OUTBOUND = "outbound"


class FixValidationStatus(str, Enum):
    VALID = "valid"
    REJECTED = "rejected"


class FixSessionState(str, Enum):
    DISCONNECTED = "disconnected"
    LOGON_PENDING = "logon_pending"
    ESTABLISHED = "established"
    TEST_REQUEST_PENDING = "test_request_pending"
    LOGOUT_PENDING = "logout_pending"
    RECOVERING = "recovering"
    TERMINATED = "terminated"


class FixMessageType(str, Enum):
    HEARTBEAT = "0"
    TEST_REQUEST = "1"
    RESEND_REQUEST = "2"
    REJECT = "3"
    SEQUENCE_RESET = "4"
    LOGOUT = "5"
    LOGON = "A"
    EXECUTION_REPORT = "8"
    ORDER_CANCEL_REJECT = "9"
    NEW_ORDER_SINGLE = "D"
    ORDER_CANCEL_REQUEST = "F"
    BUSINESS_MESSAGE_REJECT = "j"


class FixSide(str, Enum):
    BUY = "1"
    SELL = "2"


class FixOrderType(str, Enum):
    MARKET = "1"
    LIMIT = "2"
    STOP = "3"
    STOP_LIMIT = "4"


class FixTimeInForce(str, Enum):
    GTC = "1"
    IOC = "3"


class FixOrdStatus(str, Enum):
    NEW = "0"
    FILLED = "2"
    CANCELLED = "4"
    REJECTED = "8"
    EXPIRED = "C"
    TRIGGERED = "E"


class FixExecType(str, Enum):
    NEW = "0"
    TRADE = "F"
    CANCELLED = "4"
    REJECTED = "8"
    EXPIRED = "C"
    TRIGGERED = "L"


class FixSessionEventType(str, Enum):
    STATE_TRANSITION = "state_transition"
    MESSAGE_ACCEPTED = "message_accepted"
    MESSAGE_REJECTED = "message_rejected"
    SEQUENCE_GAP = "sequence_gap"
    DUPLICATE_ACCEPTED = "duplicate_accepted"
    TEST_REQUEST_SENT = "test_request_sent"
    TEST_REQUEST_GRACE_EXPIRED = "test_request_grace_expired"
    HEARTBEAT_SENT = "heartbeat_sent"
    HEARTBEAT_RECEIVED = "heartbeat_received"
    CONNECTION_DROPPED = "connection_dropped"
    RECONNECTED = "reconnected"
    FAULT_ACTIVATED = "fault_activated"


class ReceiveDisposition(str, Enum):
    ACCEPTED_NEW = "accepted_new"
    ACCEPTED_REPLAY = "accepted_replay"
    REJECTED = "rejected"
    SEQUENCE_GAP = "sequence_gap"


class LatencyStage(str, Enum):
    INBOUND_DECODE = "inbound_decode"
    VALIDATION = "validation"
    RISK = "risk"
    ACKNOWLEDGEMENT = "acknowledgement"
    SIMULATED_BROKER = "simulated_broker"
    FILL_REPORT = "fill_report"
    OUTBOUND_ENCODE = "outbound_encode"


class ConnectionFaultType(str, Enum):
    DROP_BEFORE_LOGON = "drop_before_logon"
    DROP_AFTER_ACKNOWLEDGEMENT = "drop_after_acknowledgement"
    DROP_ACTIVE_ORDER = "drop_active_order"
    HEARTBEAT_RESPONSE_LOSS = "heartbeat_response_loss"
    DUPLICATE_INBOUND = "duplicate_inbound"
    INBOUND_SEQUENCE_GAP = "inbound_sequence_gap"
    DELAYED_OUTBOUND_REPORT = "delayed_outbound_report"
    RECONNECT_DELAY = "reconnect_delay"


def fix_uuid(kind, payload):
    return uuid5(NAMESPACE_URL, f"secure-eval-wrapper:simulated-fix:{kind}:{sha256_payload(payload)}")


def _text(value, name):
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be non-empty")
    return value.strip()


@dataclass(frozen=True)
class FixMessage:
    msg_type: FixMessageType
    msg_seq_num: int
    sender_comp_id: str
    target_comp_id: str
    sending_time_utc: datetime
    fields: Mapping[int, str] = field(default_factory=dict)
    poss_dup_flag: bool = False
    orig_sending_time_utc: datetime | None = None
    extensions: Mapping[int, str] = field(default_factory=dict)
    begin_string: str = "FIX.4.4"
    body_length: int | None = None
    checksum: int | None = None
    raw_message_sha256: str | None = None
    fix_message_id: UUID | None = None

    def __post_init__(self):
        object.__setattr__(self, "msg_type", FixMessageType(self.msg_type))
        if not isinstance(self.msg_seq_num, int) or isinstance(self.msg_seq_num, bool) or self.msg_seq_num <= 0:
            raise ValueError("msg_seq_num must be a positive integer")
        object.__setattr__(self, "sender_comp_id", _text(self.sender_comp_id, "sender_comp_id"))
        object.__setattr__(self, "target_comp_id", _text(self.target_comp_id, "target_comp_id"))
        require_utc_datetime(self.sending_time_utc, field_name="FIX SendingTime")
        if self.begin_string != "FIX.4.4":
            raise ValueError("unsupported BeginString")
        if self.poss_dup_flag and self.orig_sending_time_utc is None:
            raise ValueError("PossDupFlag requires OrigSendingTime")
        if self.orig_sending_time_utc is not None:
            require_utc_datetime(self.orig_sending_time_utc, field_name="FIX OrigSendingTime")
        normalized = {int(k): _text(str(v), f"tag {k}") for k, v in self.fields.items()}
        extensions = {int(k): _text(str(v), f"extension tag {k}") for k, v in self.extensions.items()}
        if set(normalized) & set(extensions):
            raise ValueError("a FIX tag cannot be both a field and an extension")
        object.__setattr__(self, "fields", MappingProxyType(normalized))
        object.__setattr__(self, "extensions", MappingProxyType(extensions))
        expected = fix_uuid("message", {
            "replay_hash": self.replay_identity_sha256,
            "sequence": self.msg_seq_num,
            "sending_time": self.sending_time_utc,
            "poss_dup": self.poss_dup_flag,
            "orig_sending_time": self.orig_sending_time_utc,
        })
        if self.fix_message_id is not None and self.fix_message_id != expected:
            raise ValueError("fix_message_id does not match deterministic identity")
        object.__setattr__(self, "fix_message_id", expected)

    @property
    def replay_identity_sha256(self) -> str:
        """Canonical economic/administrative identity, excluding replay transport differences."""
        return sha256_payload({
            "msg_type": self.msg_type.value,
            "sender_comp_id": self.sender_comp_id,
            "target_comp_id": self.target_comp_id,
            "fields": {str(tag): value for tag, value in sorted(self.fields.items())},
            "extensions": {str(tag): value for tag, value in sorted(self.extensions.items())},
        })

    @property
    def business_identity_sha256(self) -> str:
        return self.replay_identity_sha256

    @property
    def record_sha256(self) -> str:
        return sha256_payload({
            "fix_message_id": self.fix_message_id,
            "body_length": self.body_length,
            "checksum": self.checksum,
            "raw_message_sha256": self.raw_message_sha256,
            "replay_identity_sha256": self.replay_identity_sha256,
        })


@dataclass(frozen=True)
class RejectedFixObservation:
    fix_session_id: UUID
    direction: FixDirection
    processing_time_utc: datetime
    raw_message_sha256: str
    rejection_code: str
    rejection_reason: str
    parsed_header_fields: Mapping[int, str] = field(default_factory=dict)
    msg_seq_num: int | None = None
    msg_type: str | None = None
    sender_comp_id: str | None = None
    target_comp_id: str | None = None

    def __post_init__(self):
        object.__setattr__(self, "direction", FixDirection(self.direction))
        require_utc_datetime(self.processing_time_utc, field_name="rejected FIX processing time")
        if len(self.raw_message_sha256) != 64 or any(c not in "0123456789abcdef" for c in self.raw_message_sha256):
            raise ValueError("raw_message_sha256 must be canonical lowercase SHA-256")
        object.__setattr__(self, "rejection_code", _text(self.rejection_code, "rejection_code"))
        object.__setattr__(self, "rejection_reason", _text(self.rejection_reason, "rejection_reason"))
        if self.msg_seq_num is not None and self.msg_seq_num <= 0:
            raise ValueError("rejected observation sequence must be positive when present")
        object.__setattr__(self, "parsed_header_fields", MappingProxyType(dict(self.parsed_header_fields)))

    @property
    def observation_id(self):
        return fix_uuid("rejected-observation", {
            "session": self.fix_session_id,
            "direction": self.direction,
            "raw": self.raw_message_sha256,
            "code": self.rejection_code,
        })

    @property
    def fix_message_id(self):
        return self.observation_id

    @property
    def validation_status(self):
        return FixValidationStatus.REJECTED

    @property
    def record_sha256(self):
        return sha256_payload({
            "observation_id": self.observation_id,
            "processing_time_utc": self.processing_time_utc,
            "parsed_header_fields": dict(self.parsed_header_fields),
            "msg_seq_num": self.msg_seq_num,
            "msg_type": self.msg_type,
            "sender_comp_id": self.sender_comp_id,
            "target_comp_id": self.target_comp_id,
            "rejection_reason": self.rejection_reason,
        })


@dataclass(frozen=True)
class SessionReceiveResult:
    disposition: ReceiveDisposition
    responses: tuple[FixMessage, ...] = ()
    message: FixMessage | None = None
    rejected_observation: RejectedFixObservation | None = None

    def __post_init__(self):
        object.__setattr__(self, "disposition", ReceiveDisposition(self.disposition))
        object.__setattr__(self, "responses", tuple(self.responses))

    def __iter__(self):
        return iter(self.responses)

    def __len__(self):
        return len(self.responses)

    def __getitem__(self, index):
        return self.responses[index]

    def __add__(self, other):
        return self.responses + tuple(other)

    def __eq__(self, other):
        if isinstance(other, tuple):
            return self.responses == other
        if isinstance(other, SessionReceiveResult):
            return (self.disposition, self.responses, self.message, self.rejected_observation) == (other.disposition, other.responses, other.message, other.rejected_observation)
        return NotImplemented


@dataclass(frozen=True)
class FixSessionConfiguration:
    sender_comp_id: str
    target_comp_id: str
    heartbeat_interval_seconds: Decimal = Decimal("30")
    test_request_grace_seconds: Decimal = Decimal("10")
    disconnect_timeout_seconds: Decimal = Decimal("60")
    preserve_unknown_tags: bool = False

    def __post_init__(self):
        _text(self.sender_comp_id, "sender_comp_id")
        _text(self.target_comp_id, "target_comp_id")
        for name in ("heartbeat_interval_seconds", "test_request_grace_seconds", "disconnect_timeout_seconds"):
            value = getattr(self, name)
            if not isinstance(value, Decimal) or not value.is_finite() or value <= 0:
                raise ValueError(f"{name} must be a positive finite Decimal")
        if self.disconnect_timeout_seconds < self.test_request_grace_seconds:
            raise ValueError("disconnect timeout cannot be shorter than test-request grace")

    @property
    def session_key(self):
        return f"{self.sender_comp_id}->{self.target_comp_id}"

    @property
    def config_sha256(self):
        return sha256_payload({
            "sender": self.sender_comp_id,
            "target": self.target_comp_id,
            "heartbeat": self.heartbeat_interval_seconds,
            "grace": self.test_request_grace_seconds,
            "disconnect": self.disconnect_timeout_seconds,
            "preserve_unknown": self.preserve_unknown_tags,
        })

    @property
    def fix_session_id(self):
        return fix_uuid("session", {"session_key": self.session_key, "config": self.config_sha256})


@dataclass(frozen=True)
class FixSessionEvent:
    fix_session_id: UUID
    event_type: FixSessionEventType
    event_at_utc: datetime
    prior_state: FixSessionState
    new_state: FixSessionState
    reason_code: str
    sequence_number: int | None = None
    parent_message_id: UUID | None = None
    transition_sequence: int = 0
    previous_event_sha256: str | None = None

    def __post_init__(self):
        object.__setattr__(self, "event_type", FixSessionEventType(self.event_type))
        object.__setattr__(self, "prior_state", FixSessionState(self.prior_state))
        object.__setattr__(self, "new_state", FixSessionState(self.new_state))
        require_utc_datetime(self.event_at_utc, field_name="session event")
        _text(self.reason_code, "reason_code")
        if self.transition_sequence < 0:
            raise ValueError("transition_sequence must be non-negative")

    @property
    def event_id(self):
        return fix_uuid("session-event", {
            "session": self.fix_session_id,
            "transition_sequence": self.transition_sequence,
            "type": self.event_type,
            "at": self.event_at_utc,
            "reason": self.reason_code,
            "sequence": self.sequence_number,
            "parent": self.parent_message_id,
        })

    @property
    def record_sha256(self):
        return sha256_payload({
            "event_id": self.event_id,
            "prior": self.prior_state,
            "new": self.new_state,
            "previous_event_sha256": self.previous_event_sha256,
        })


@dataclass(frozen=True)
class LatencySample:
    fix_session_id: UUID
    fix_message_id: UUID | None
    stage: LatencyStage
    simulated_start_utc: datetime
    simulated_end_utc: datetime
    duration_microseconds: int
    threshold_microseconds: int | None = None

    def __post_init__(self):
        object.__setattr__(self, "stage", LatencyStage(self.stage))
        require_utc_datetime(self.simulated_start_utc, field_name="latency start")
        require_utc_datetime(self.simulated_end_utc, field_name="latency end")
        if self.simulated_end_utc < self.simulated_start_utc or self.duration_microseconds < 0 or (self.threshold_microseconds is not None and self.threshold_microseconds < 0):
            raise ValueError("invalid simulated latency")

    @property
    def breached(self):
        return self.threshold_microseconds is not None and self.duration_microseconds > self.threshold_microseconds

    @property
    def latency_sample_id(self):
        return fix_uuid("latency", {"session": self.fix_session_id, "message": self.fix_message_id, "stage": self.stage, "start": self.simulated_start_utc})

    @property
    def record_sha256(self):
        return sha256_payload({"latency_sample_id": self.latency_sample_id, "end": self.simulated_end_utc, "duration": self.duration_microseconds, "threshold": self.threshold_microseconds, "breached": self.breached})


@dataclass(frozen=True)
class ConnectionFault:
    fix_session_id: UUID
    fault_type: ConnectionFaultType
    scheduled_at_utc: datetime
    reason_code: str
    configuration: Mapping[str, object] = field(default_factory=dict)
    activated_at_utc: datetime | None = None

    def __post_init__(self):
        object.__setattr__(self, "fault_type", ConnectionFaultType(self.fault_type))
        require_utc_datetime(self.scheduled_at_utc, field_name="fault schedule")
        _text(self.reason_code, "reason_code")
        object.__setattr__(self, "configuration", MappingProxyType(dict(self.configuration)))
        if self.activated_at_utc is not None:
            require_utc_datetime(self.activated_at_utc, field_name="fault activation")

    @property
    def connection_fault_id(self):
        return fix_uuid("fault", {"session": self.fix_session_id, "type": self.fault_type, "scheduled": self.scheduled_at_utc})

    @property
    def record_sha256(self):
        return sha256_payload({"fault_id": self.connection_fault_id, "reason": self.reason_code, "configuration": dict(self.configuration), "activated": self.activated_at_utc})


@dataclass(frozen=True)
class FixOrderLink:
    fix_session_id: UUID
    cl_ord_id: str
    orig_cl_ord_id: str | None = None
    order_intent_id: UUID | None = None
    order_id: UUID | None = None
    fill_id: UUID | None = None
    execution_report_message_id: UUID | None = None
    business_identity_sha256: str = ""

    def __post_init__(self):
        _text(self.cl_ord_id, "cl_ord_id")
        if self.orig_cl_ord_id is not None:
            _text(self.orig_cl_ord_id, "orig_cl_ord_id")
        if not self.business_identity_sha256:
            object.__setattr__(self, "business_identity_sha256", sha256_payload({"session": self.fix_session_id, "cl_ord_id": self.cl_ord_id, "orig_cl_ord_id": self.orig_cl_ord_id}))

    @property
    def fix_order_link_id(self):
        return fix_uuid("order-link", {"session": self.fix_session_id, "cl_ord_id": self.cl_ord_id, "fill_id": self.fill_id, "report": self.execution_report_message_id})

    @property
    def record_sha256(self):
        return sha256_payload({"fix_order_link_id": self.fix_order_link_id, "intent": self.order_intent_id, "order": self.order_id, "fill": self.fill_id, "business": self.business_identity_sha256})
