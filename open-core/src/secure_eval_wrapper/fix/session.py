"""Deterministic in-process simulated FIX session state machine."""
from __future__ import annotations

import hashlib
from datetime import datetime, timedelta
from decimal import Decimal

from secure_eval_wrapper.data_collection.time_utils import require_utc_datetime
from secure_eval_wrapper.fix.codec import FixCodec, FixValidationError
from secure_eval_wrapper.fix.messages import heartbeat, logon, logout, reject, resend_request, test_request
from secure_eval_wrapper.fix.models import (
    FixDirection,
    FixMessage,
    FixMessageType,
    FixSessionConfiguration,
    FixSessionEvent,
    FixSessionEventType,
    FixSessionState,
    ReceiveDisposition,
    RejectedFixObservation,
    RejectedFixOccurrence,
    SessionReceiveResult,
)


class FixSessionError(RuntimeError):
    pass


class SimulatedFixSession:
    def __init__(self, configuration: FixSessionConfiguration, *, codec: FixCodec | None = None):
        self.configuration = configuration
        self.codec = codec or FixCodec(preserve_unknown_tags=configuration.preserve_unknown_tags)
        self.state = FixSessionState.DISCONNECTED
        self.next_inbound_seq_num = 1
        self.next_outbound_seq_num = 1
        self.last_inbound_at_utc = None
        self.last_outbound_at_utc = None
        self.pending_test_request_id = None
        self.pending_test_sent_at_utc = None
        self.pending_test_deadline_at_utc = None
        self.test_request_grace_expired = False
        self.events = []
        self.inbound_messages = []
        self.outbound_messages = []
        self.rejected_observations = []
        self.rejected_occurrences = []
        self._rejected_observations_by_id = {}
        self._rejected_occurrence_ids = set()
        self._accepted = {}
        self.persisted_state_version = None
        self.persisted_record_sha256 = None
        self.persisted_transition_sequence = None
        self.persisted_event_sha256 = None
        self.persisted_transition_event_id = None

    @property
    def fix_session_id(self):
        return self.configuration.fix_session_id

    def _event(self, kind, at, prior, new, reason, seq=None, parent=None):
        previous = self.events[-1].record_sha256 if self.events else None
        event = FixSessionEvent(
            self.fix_session_id,
            kind,
            at,
            prior,
            new,
            reason,
            seq,
            parent,
            transition_sequence=len(self.events),
            previous_event_sha256=previous,
            projected_next_inbound_seq_num=self.next_inbound_seq_num,
            projected_next_outbound_seq_num=self.next_outbound_seq_num,
            projected_last_inbound_at_utc=self.last_inbound_at_utc,
            projected_last_outbound_at_utc=self.last_outbound_at_utc,
            projected_pending_test_request_id=self.pending_test_request_id,
            projected_pending_test_deadline_at_utc=self.pending_test_deadline_at_utc,
            projected_test_request_grace_expired=self.test_request_grace_expired,
        )
        self.events.append(event)
        return event

    def _transition(self, new, at, reason, kind=FixSessionEventType.STATE_TRANSITION, parent=None):
        prior = self.state
        self.state = FixSessionState(new)
        return self._event(kind, at, prior, self.state, reason, parent=parent)

    def _emit(self, factory, at, *args, **kwargs):
        seq = self.next_outbound_seq_num
        msg = factory(seq, self.configuration.sender_comp_id, self.configuration.target_comp_id, at, *args, **kwargs)
        self.next_outbound_seq_num += 1
        self.last_outbound_at_utc = at
        self.outbound_messages.append(msg)
        self._event(
            FixSessionEventType.MESSAGE_SENT,
            at,
            self.state,
            self.state,
            f"message_sent_{msg.msg_type.value}",
            msg.msg_seq_num,
            msg.fix_message_id,
        )
        return msg

    def connect(self, at: datetime):
        require_utc_datetime(at, field_name="session connect")
        if self.state not in (FixSessionState.DISCONNECTED, FixSessionState.TERMINATED):
            raise FixSessionError("session is already connected or pending")
        self._transition(FixSessionState.LOGON_PENDING, at, "logon_started")
        return self._emit(logon, at, heartbeat_seconds=int(self.configuration.heartbeat_interval_seconds))

    def reconnect(self, at: datetime):
        require_utc_datetime(at, field_name="session reconnect")
        if self.state is not FixSessionState.DISCONNECTED:
            raise FixSessionError("reconnect requires disconnected state")
        self._event(FixSessionEventType.RECONNECTED, at, self.state, self.state, "deterministic_reconnect")
        return self.connect(at)

    @staticmethod
    def _safe_header(raw: bytes):
        parsed = {}
        try:
            text = bytes(raw).decode("ascii", errors="strict")
        except Exception:
            return parsed
        for item in text.split("\x01"):
            if "=" not in item:
                continue
            key, value = item.split("=", 1)
            try:
                tag = int(key)
            except ValueError:
                continue
            if tag in {8, 9, 35, 34, 49, 56, 52, 43, 122, 10} and tag not in parsed and value:
                parsed[tag] = value
        return parsed

    def _record_rejected_observation(
        self, raw, at, reason, code="validation_rejected", *, record_event=True
    ):
        parsed = self._safe_header(raw)
        seq = None
        try:
            candidate = int(parsed.get(34, ""))
            if candidate > 0:
                seq = candidate
        except ValueError:
            pass
        observation = RejectedFixObservation(
            fix_session_id=self.fix_session_id,
            direction=FixDirection.INBOUND,
            processing_time_utc=at,
            raw_message_sha256=hashlib.sha256(bytes(raw)).hexdigest(),
            rejection_code=code,
            rejection_reason=str(reason),
            parsed_header_fields=parsed,
            msg_seq_num=seq,
            msg_type=parsed.get(35),
            sender_comp_id=parsed.get(49),
            target_comp_id=parsed.get(56),
        )
        stored = self._rejected_observations_by_id.get(observation.observation_id)
        if stored is not None and stored.record_sha256 != observation.record_sha256:
            raise ValueError("same rejected FIX observation identity has conflicting content")
        if stored is None:
            self._rejected_observations_by_id[observation.observation_id] = observation
            self.rejected_observations.append(observation)
        else:
            observation = stored
        occurrence = RejectedFixOccurrence(
            observation.observation_id,
            self.fix_session_id,
            FixDirection.INBOUND,
            at,
        )
        if occurrence.occurrence_id in self._rejected_occurrence_ids:
            return observation, False
        self._rejected_occurrence_ids.add(occurrence.occurrence_id)
        self.rejected_occurrences.append(occurrence)
        if record_event:
            self._event(
                FixSessionEventType.MESSAGE_REJECTED,
                at,
                self.state,
                self.state,
                code,
                seq,
                observation.observation_id,
            )
        return observation, True

    def _rejected_observation(
        self, raw, at, reason, code="validation_rejected", *, record_event=True
    ):
        observation, _ = self._record_rejected_observation(
            raw, at, reason, code, record_event=record_event
        )
        return observation

    def receive_raw(self, raw: bytes, processing_at_utc: datetime):
        require_utc_datetime(processing_at_utc, field_name="FIX processing time")
        try:
            msg = self.codec.decode(raw)
        except (FixValidationError, ValueError) as exc:
            observation = self._rejected_observation(raw, processing_at_utc, exc)
            return SessionReceiveResult(ReceiveDisposition.REJECTED, rejected_observation=observation)
        return self.receive(msg, processing_at_utc, raw_bytes=raw)

    @staticmethod
    def _typed_fallback_raw(msg):
        fields = [
            f"35={msg.msg_type.value}",
            f"34={msg.msg_seq_num}",
            f"49={msg.sender_comp_id}",
            f"56={msg.target_comp_id}",
            f"52={msg.sending_time_utc.isoformat()}",
        ]
        fields.extend(
            f"{tag}={value}"
            for tag, value in sorted({**msg.fields, **msg.extensions}.items())
        )
        return ("\x01".join(fields) + "\x01").encode(
            "ascii", errors="backslashreplace"
        )

    def receive(self, msg: FixMessage, at: datetime, *, raw_bytes=None):
        require_utc_datetime(at, field_name="session receive")
        try:
            encoded = self.codec.encode(msg) if raw_bytes is None else bytes(raw_bytes)
            if raw_bytes is not None:
                self.codec.encode(msg)
        except (FixValidationError, ValueError, TypeError) as exc:
            raw = self._typed_fallback_raw(msg) if raw_bytes is None else bytes(raw_bytes)
            observation = self._rejected_observation(raw, at, exc, "validation_rejected")
            return SessionReceiveResult(ReceiveDisposition.REJECTED, message=msg, rejected_observation=observation)
        if msg.sender_comp_id != self.configuration.target_comp_id or msg.target_comp_id != self.configuration.sender_comp_id:
            observation = self._rejected_observation(encoded, at, "FIX CompIDs do not match simulated session", "wrong_comp_ids")
            return SessionReceiveResult(ReceiveDisposition.REJECTED, message=msg, rejected_observation=observation)
        unsupported = (
            (msg.msg_type is FixMessageType.LOGON and self.state not in (FixSessionState.LOGON_PENDING, FixSessionState.DISCONNECTED))
            or (self.state is FixSessionState.LOGON_PENDING and msg.msg_type is not FixMessageType.LOGON)
            or (msg.msg_type in (FixMessageType.NEW_ORDER_SINGLE, FixMessageType.ORDER_CANCEL_REQUEST) and self.state is not FixSessionState.ESTABLISHED)
            or (self.state in (FixSessionState.DISCONNECTED, FixSessionState.TERMINATED) and msg.msg_type is not FixMessageType.LOGON)
        )
        if unsupported:
            observation = self._rejected_observation(encoded, at, f"{msg.msg_type.value} is unsupported while session is {self.state.value}", "unsupported_session_state")
            return SessionReceiveResult(ReceiveDisposition.REJECTED, message=msg, rejected_observation=observation)
        expected = self.next_inbound_seq_num
        if msg.msg_seq_num < expected:
            prior_hash = self._accepted.get(msg.msg_seq_num)
            if msg.poss_dup_flag and prior_hash == msg.replay_identity_sha256:
                self._event(FixSessionEventType.DUPLICATE_ACCEPTED, at, self.state, self.state, "valid_possdup_replay", msg.msg_seq_num, msg.fix_message_id)
                return SessionReceiveResult(ReceiveDisposition.ACCEPTED_REPLAY, message=msg)
            observation, occurrence_created = self._record_rejected_observation(
                encoded,
                at,
                "MsgSeqNum too low or replay content conflict",
                "inbound_sequence_too_low",
                record_event=False,
            )
            if not occurrence_created:
                return SessionReceiveResult(
                    ReceiveDisposition.REJECTED,
                    message=msg,
                    rejected_observation=observation,
                )
            response = self._emit(reject, at, ref_seq_num=msg.msg_seq_num, text="MsgSeqNum too low or replay content conflict", ref_msg_type=msg.msg_type)
            self._event(FixSessionEventType.MESSAGE_REJECTED, at, self.state, self.state, "inbound_sequence_too_low", msg.msg_seq_num, observation.observation_id)
            return SessionReceiveResult(ReceiveDisposition.REJECTED, (response,), message=msg, rejected_observation=observation)
        if msg.msg_seq_num > expected:
            self._transition(FixSessionState.RECOVERING, at, "inbound_sequence_gap", FixSessionEventType.SEQUENCE_GAP, msg.fix_message_id)
            response = self._emit(resend_request, at, begin_seq_no=expected, end_seq_no=msg.msg_seq_num - 1)
            return SessionReceiveResult(ReceiveDisposition.SEQUENCE_GAP, (response,), message=msg)

        if msg.msg_type is FixMessageType.SEQUENCE_RESET and int(msg.fields[36]) <= expected:
            observation, occurrence_created = self._record_rejected_observation(
                encoded,
                at,
                "SequenceReset cannot decrease expected sequence",
                "sequence_reset_not_forward",
                record_event=False,
            )
            if not occurrence_created:
                return SessionReceiveResult(
                    ReceiveDisposition.REJECTED,
                    message=msg,
                    rejected_observation=observation,
                )
            response = self._emit(reject, at, ref_seq_num=msg.msg_seq_num, text="SequenceReset cannot decrease expected sequence", ref_msg_type=msg.msg_type)
            self._event(FixSessionEventType.MESSAGE_REJECTED, at, self.state, self.state, "sequence_reset_not_forward", msg.msg_seq_num, observation.observation_id)
            return SessionReceiveResult(ReceiveDisposition.REJECTED, (response,), message=msg, rejected_observation=observation)

        self.inbound_messages.append(msg)
        self.last_inbound_at_utc = at
        self._accepted[msg.msg_seq_num] = msg.replay_identity_sha256
        responses = []
        if msg.msg_type is FixMessageType.SEQUENCE_RESET:
            new_seq = int(msg.fields[36])
            self.next_inbound_seq_num = new_seq
            self._transition(FixSessionState.ESTABLISHED, at, "sequence_recovered", parent=msg.fix_message_id)
        else:
            self.next_inbound_seq_num += 1

        if msg.msg_type is FixMessageType.LOGON:
            if self.state not in (FixSessionState.LOGON_PENDING, FixSessionState.DISCONNECTED):
                responses.append(self._emit(reject, at, ref_seq_num=msg.msg_seq_num, text="Duplicate Logon", ref_msg_type=msg.msg_type))
            else:
                self._transition(FixSessionState.ESTABLISHED, at, "logon_accepted", parent=msg.fix_message_id)
        elif msg.msg_type is FixMessageType.HEARTBEAT:
            test_id = msg.fields.get(112)
            if self.state is FixSessionState.TEST_REQUEST_PENDING and test_id == self.pending_test_request_id:
                if at <= self.pending_test_deadline_at_utc:
                    self._clear_pending_test()
                    self._transition(FixSessionState.ESTABLISHED, at, "matching_heartbeat_received", FixSessionEventType.HEARTBEAT_RECEIVED, msg.fix_message_id)
                else:
                    self._event(FixSessionEventType.HEARTBEAT_RECEIVED, at, self.state, self.state, "late_test_request_heartbeat", msg.msg_seq_num, msg.fix_message_id)
            else:
                self._event(FixSessionEventType.HEARTBEAT_RECEIVED, at, self.state, self.state, "heartbeat_received", msg.msg_seq_num, msg.fix_message_id)
        elif msg.msg_type is FixMessageType.TEST_REQUEST:
            responses.append(self._emit(heartbeat, at, test_request_id=msg.fields[112]))
        elif msg.msg_type is FixMessageType.RESEND_REQUEST:
            self._transition(FixSessionState.RECOVERING, at, "peer_resend_requested", parent=msg.fix_message_id)
        elif msg.msg_type is FixMessageType.LOGOUT:
            if self.state is not FixSessionState.LOGOUT_PENDING:
                responses.append(self._emit(logout, at, text="Simulated logout acknowledged"))
            self._transition(FixSessionState.TERMINATED, at, "logout_complete", parent=msg.fix_message_id)
        self._event(FixSessionEventType.MESSAGE_ACCEPTED, at, self.state, self.state, "message_accepted", msg.msg_seq_num, msg.fix_message_id)
        return SessionReceiveResult(ReceiveDisposition.ACCEPTED_NEW, tuple(responses), message=msg)

    def _clear_pending_test(self):
        self.pending_test_request_id = None
        self.pending_test_sent_at_utc = None
        self.pending_test_deadline_at_utc = None
        self.test_request_grace_expired = False

    def request_logout(self, at):
        if self.state is not FixSessionState.ESTABLISHED:
            raise FixSessionError("logout requires established session")
        self._transition(FixSessionState.LOGOUT_PENDING, at, "logout_requested")
        return self._emit(logout, at, text="Simulated session logout")

    def tick(self, at: datetime):
        require_utc_datetime(at, field_name="session tick")
        if self.state not in (FixSessionState.ESTABLISHED, FixSessionState.TEST_REQUEST_PENDING):
            return ()
        if self.state is FixSessionState.TEST_REQUEST_PENDING:
            grace_deadline = self.pending_test_deadline_at_utc
            disconnect_deadline = self.pending_test_sent_at_utc + timedelta(seconds=float(self.configuration.disconnect_timeout_seconds))
            if at >= grace_deadline and not self.test_request_grace_expired:
                self.test_request_grace_expired = True
                self._event(FixSessionEventType.TEST_REQUEST_GRACE_EXPIRED, at, self.state, self.state, "test_request_grace_expired")
            if at >= disconnect_deadline:
                self._clear_pending_test()
                self._transition(FixSessionState.DISCONNECTED, at, "disconnect_timeout", FixSessionEventType.CONNECTION_DROPPED)
            return ()

        if self.last_inbound_at_utc is not None:
            peer_silence = Decimal(str((at - self.last_inbound_at_utc).total_seconds()))
            if peer_silence >= self.configuration.heartbeat_interval_seconds:
                request_id = f"TEST-{self.next_outbound_seq_num}"
                msg = self._emit(test_request, at, test_request_id=request_id)
                self.pending_test_request_id = request_id
                self.pending_test_sent_at_utc = at
                self.pending_test_deadline_at_utc = at + timedelta(seconds=float(self.configuration.test_request_grace_seconds))
                self.test_request_grace_expired = False
                self._transition(FixSessionState.TEST_REQUEST_PENDING, at, "peer_silence_threshold", FixSessionEventType.TEST_REQUEST_SENT, msg.fix_message_id)
                return (msg,)

        if self.last_outbound_at_utc is not None:
            outbound_silence = Decimal(str((at - self.last_outbound_at_utc).total_seconds()))
            if outbound_silence >= self.configuration.heartbeat_interval_seconds:
                msg = self._emit(heartbeat, at)
                self._event(FixSessionEventType.HEARTBEAT_SENT, at, self.state, self.state, "outbound_heartbeat_interval", msg.msg_seq_num, msg.fix_message_id)
                return (msg,)
        return ()

    def drop(self, at, reason="configured_simulated_drop"):
        require_utc_datetime(at, field_name="session drop")
        self._clear_pending_test()
        self._transition(FixSessionState.DISCONNECTED, at, reason, FixSessionEventType.CONNECTION_DROPPED)
