from __future__ import annotations

import dataclasses
import hashlib
import os
import re
import socket
import sys
import unittest
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from unittest.mock import patch
from uuid import UUID

from secure_eval_wrapper.alpha.identity import SeriesIdentity
from secure_eval_wrapper.data_collection.models import InstrumentType
from secure_eval_wrapper.execution.brokers.simulated import SimulatedBroker
from secure_eval_wrapper.execution.fees import ZeroFeeModel
from secure_eval_wrapper.execution.models import AccountingMode, BrokerConfiguration
from secure_eval_wrapper.execution.slippage import ZeroSlippage
from secure_eval_wrapper.fix.codec import FixCodec
from secure_eval_wrapper.fix.faults import FaultOrchestrator, FaultSchedule
from secure_eval_wrapper.fix.gateway import GatewaySeries, SimulatedFixGateway
from secure_eval_wrapper.fix.messages import *
from secure_eval_wrapper.fix.models import *
from secure_eval_wrapper.fix.session import SimulatedFixSession
from secure_eval_wrapper.monitoring.engine import MonitoringEngine, MonitoringInputs
from secure_eval_wrapper.monitoring.execution_health import ExecutionHealthInput
from secure_eval_wrapper.monitoring.configuration import MonitoringConfiguration
from secure_eval_wrapper.monitoring.models import IncidentState, MonitoredRunReference, PublicSafeProvenance

T = datetime(2026, 1, 1, tzinfo=timezone.utc)
RUN = UUID("00000000-0000-5000-8000-000000000001")


def fixture(mode=AccountingMode.SPOT):
    session = SimulatedFixSession(FixSessionConfiguration("CLIENT", "SERVER"))
    session.connect(T)
    session.receive(logon(1, "SERVER", "CLIENT", T), T)
    instrument = InstrumentType.SPOT if mode is AccountingMode.SPOT else InstrumentType.PERPETUAL_SWAP
    identity = SeriesIdentity("fixture", "fixture", "BTCUSDT", "BTC/USDT", instrument, "1m", None if mode is AccountingMode.SPOT else "USDT")
    broker = SimulatedBroker(BrokerConfiguration(), fee_model=ZeroFeeModel(), slippage_model=ZeroSlippage())
    gateway = SimulatedFixGateway(
        session=session, broker=broker, run_id=RUN,
        series_by_symbol={"BTC/USDT": GatewaySeries(identity, mode, reference_price=Decimal("100"))},
        implementation_code_sha256="a" * 64, repository_commit_sha="commit", data_sha256="b" * 64,
    )
    return session, broker, gateway


def order(seq, clid, side, quantity=Decimal("1"), price=None):
    return new_order_single(seq, "SERVER", "CLIENT", T + timedelta(seconds=seq), cl_ord_id=clid,
                            symbol="BTC/USDT", side=side, quantity=quantity,
                            order_type=FixOrderType.MARKET if price is None else FixOrderType.LIMIT,
                            price=price)


class GatewayAccountingAuditTests(unittest.TestCase):
    def fill(self, gateway, seq, clid, side, quantity=Decimal("1")):
        before = gateway.current_quantity("BTC/USDT", T + timedelta(seconds=seq))
        gateway.handle(order(seq, clid, side, quantity), T + timedelta(seconds=seq))
        self.assertEqual(gateway.current_quantity("BTC/USDT", T + timedelta(seconds=seq)), before)
        reports = gateway.process_bar_open(symbol="BTC/USDT", timestamp_utc=T + timedelta(seconds=seq, milliseconds=1), open_price=Decimal("100"))
        self.assertEqual(reports[-1].fields[150], FixExecType.TRADE.value)

    def test_spot_buy_fill_then_sell_close(self):
        _, _, gateway = fixture()
        self.fill(gateway, 2, "BUY", FixSide.BUY)
        self.assertEqual(gateway.current_quantity("BTC/USDT", T), Decimal("1"))
        self.fill(gateway, 3, "SELL", FixSide.SELL)
        self.assertEqual(gateway.current_quantity("BTC/USDT", T), Decimal("0"))

    def test_spot_oversized_sell_blocked(self):
        _, broker, gateway = fixture()
        self.fill(gateway, 2, "BUY", FixSide.BUY)
        report = gateway.handle(order(3, "SELL", FixSide.SELL, Decimal("2")), T + timedelta(seconds=3))[-1]
        self.assertEqual(report.fields[150], FixExecType.REJECTED.value)
        self.assertEqual(gateway.current_quantity("BTC/USDT", T), Decimal("1"))
        self.assertEqual(len(broker.active_orders()), 0)

    def test_perpetual_reduce_flat_reverse(self):
        _, _, gateway = fixture(AccountingMode.LINEAR_PERPETUAL)
        self.fill(gateway, 2, "LONG", FixSide.BUY, Decimal("3"))
        self.fill(gateway, 3, "REDUCE", FixSide.SELL, Decimal("1"))
        self.assertEqual(gateway.current_quantity("BTC/USDT", T), Decimal("2"))
        self.fill(gateway, 4, "FLAT", FixSide.SELL, Decimal("2"))
        self.assertEqual(gateway.current_quantity("BTC/USDT", T), Decimal("0"))
        self.fill(gateway, 5, "REVERSE", FixSide.SELL, Decimal("2"))
        self.assertEqual(gateway.current_quantity("BTC/USDT", T), Decimal("-2"))

    def test_duplicate_fill_is_not_applied_twice(self):
        _, _, gateway = fixture()
        gateway.handle(order(2, "BUY", FixSide.BUY), T + timedelta(seconds=2))
        gateway.process_bar_open(symbol="BTC/USDT", timestamp_utc=T + timedelta(seconds=3), open_price=Decimal("100"))
        fill_id = next(iter(gateway._applied_fill_ids))
        duplicate = type("DuplicateFill", (), {"fill_id": fill_id})()
        self.assertFalse(gateway._apply_fill_once("BTC/USDT", duplicate))
        self.assertIn(fill_id, gateway._applied_fill_ids)
        self.assertEqual(gateway.current_quantity("BTC/USDT", T), Decimal("1"))


class SessionTimeoutAuditTests(unittest.TestCase):
    def session(self):
        session = SimulatedFixSession(FixSessionConfiguration("CLIENT", "SERVER", Decimal("5"), Decimal("2"), Decimal("7")))
        session.connect(T)
        session.receive(logon(1, "SERVER", "CLIENT", T), T)
        return session

    def test_grace_and_disconnect_boundaries_are_independent(self):
        session = self.session()
        session.tick(T + timedelta(seconds=5))
        self.assertFalse(session.test_request_grace_expired)
        session.tick(T + timedelta(seconds=6, milliseconds=999))
        self.assertFalse(session.test_request_grace_expired)
        session.tick(T + timedelta(seconds=7))
        self.assertTrue(session.test_request_grace_expired)
        self.assertEqual(session.state, FixSessionState.TEST_REQUEST_PENDING)
        session.tick(T + timedelta(seconds=11, milliseconds=999))
        self.assertEqual(session.state, FixSessionState.TEST_REQUEST_PENDING)
        session.tick(T + timedelta(seconds=12))
        self.assertEqual(session.state, FixSessionState.DISCONNECTED)

    def test_recent_inbound_prevents_test_request_and_outbound_schedules_heartbeat(self):
        session = self.session()
        session.receive(heartbeat(2, "SERVER", "CLIENT", T + timedelta(seconds=4)), T + timedelta(seconds=4))
        emitted = session.tick(T + timedelta(seconds=5))
        self.assertEqual(emitted[0].msg_type, FixMessageType.HEARTBEAT)
        self.assertEqual(session.state, FixSessionState.ESTABLISHED)


class ReplayAuditTests(unittest.TestCase):
    def replay(self, original, **changes):
        fields = dict(original.fields)
        fields.update(changes.pop("fields", {}))
        return FixMessage(original.msg_type, original.msg_seq_num, original.sender_comp_id, original.target_comp_id,
                          original.sending_time_utc + timedelta(seconds=1), fields,
                          poss_dup_flag=True, orig_sending_time_utc=original.sending_time_utc, **changes)

    def test_identical_possdup_creates_no_duplicate_order_or_fill(self):
        session, broker, gateway = fixture()
        original = order(2, "C1", FixSide.BUY)
        gateway.handle(original, T + timedelta(seconds=2))
        self.assertEqual(gateway.handle(self.replay(original), T + timedelta(seconds=3)), ())
        self.assertEqual(len(broker.active_orders()), 1)
        self.assertEqual(len(gateway.links), 1)
        self.assertEqual(session.events[-1].event_type, FixSessionEventType.DUPLICATE_ACCEPTED)

    def test_changed_quantity_price_symbol_side_each_conflict(self):
        changes = ({38: "2"}, {40: "2", 44: "99"}, {55: "ETH/USDT"}, {54: "2"})
        for changed in changes:
            with self.subTest(changed=changed):
                session, _, gateway = fixture()
                original = order(2, "C1", FixSide.BUY)
                gateway.handle(original, T + timedelta(seconds=2))
                response = gateway.handle(self.replay(original, fields=changed), T + timedelta(seconds=3))
                self.assertEqual(len(response), 1)
                self.assertEqual(response[0].msg_type, FixMessageType.REJECT)
                self.assertEqual(session.events[-1].event_type, FixSessionEventType.MESSAGE_REJECTED)


class RejectedObservationAuditTests(unittest.TestCase):
    def setUp(self):
        self.codec = FixCodec()
        self.session = SimulatedFixSession(FixSessionConfiguration("CLIENT", "SERVER"))
        self.session.connect(T)

    def rebuild(self, fields):
        body = b"\x01".join(f"{tag}={value}".encode() for tag, value in fields) + b"\x01"
        prefix = f"8=FIX.4.4\x019={len(body)}\x01".encode()
        before = prefix + body
        return before + f"10={sum(before) % 256:03d}\x01".encode()

    def assert_rejected(self, raw, code="validation_rejected"):
        before = self.session.next_inbound_seq_num
        result = self.session.receive_raw(raw, T)
        self.assertEqual(result.disposition, ReceiveDisposition.REJECTED)
        self.assertEqual(result.rejected_observation.validation_status, FixValidationStatus.REJECTED)
        self.assertEqual(result.rejected_observation.rejection_code, code)
        self.assertEqual(self.session.next_inbound_seq_num, before)

    def test_malformed_length_checksum_invalid_type_duplicate_and_field(self):
        valid = self.codec.encode(logon(1, "SERVER", "CLIENT", T))
        match = re.search(br"9=(\d+)", valid); declared = int(match.group(1)); self.assert_rejected(valid[:match.start(1)] + str(declared - 1).encode() + valid[match.end(1):])
        self.assert_rejected(valid[:-5] + b"000\x01")
        common = [(35, "Z"), (34, "1"), (49, "SERVER"), (56, "CLIENT"), (52, "20260101-00:00:00")]
        self.assert_rejected(self.rebuild(common))
        self.assert_rejected(self.rebuild([(35, "A"), (35, "A"), (34, "1"), (49, "SERVER"), (56, "CLIENT"), (52, "20260101-00:00:00"), (98, "0"), (108, "30")]))
        self.assert_rejected(self.rebuild([(35, "D"), (34, "1"), (49, "SERVER"), (56, "CLIENT"), (52, "20260101-00:00:00"), (11, "C"), (55, "BTC"), (54, "9"), (60, "20260101-00:00:00"), (38, "1"), (40, "1"), (59, "1")]))

    def test_wrong_compids_are_observed(self):
        raw = self.codec.encode(logon(1, "OTHER", "CLIENT", T))
        self.assert_rejected(raw, "wrong_comp_ids")
        observation = self.session.rejected_observations[-1]
        self.assertEqual(observation.sender_comp_id, "OTHER")
        self.assertEqual(observation.msg_seq_num, 1)
        self.assertEqual(observation.raw_message_sha256, hashlib.sha256(raw).hexdigest())


class IncidentEvidenceAuditTests(unittest.TestCase):
    def evaluate(self, inputs, previous=(), at=T):
        return MonitoringEngine().evaluate(
            configuration=MonitoringConfiguration(), as_of_utc=at, inputs=inputs,
            reference=MonitoredRunReference("audit"), provenance=PublicSafeProvenance("a" * 64, "commit", "tree"),
            previous_incidents=previous,
        )

    def test_unknown_preserves_open_and_acknowledged_then_healthy_resolves(self):
        failed = self.evaluate(MonitoringInputs(execution=ExecutionHealthInput(blocked_order_fill_count=1)))
        incident = next(item for item in failed.incidents if item.reason_code == "blocked_order_filled")
        unknown = self.evaluate(MonitoringInputs(execution=None), (incident,), T + timedelta(seconds=1))
        preserved = next(item for item in unknown.incidents if item.incident_id == incident.incident_id)
        self.assertEqual(preserved.state, IncidentState.OPEN)
        acknowledged = dataclasses.replace(incident, state=IncidentState.ACKNOWLEDGED)
        unknown_ack = self.evaluate(MonitoringInputs(execution=None), (acknowledged,), T + timedelta(seconds=2))
        self.assertEqual(next(item for item in unknown_ack.incidents if item.incident_id == incident.incident_id).state, IncidentState.ACKNOWLEDGED)
        healthy_input = ExecutionHealthInput(position_reconciliation_ok=True, cash_reconciliation_ok=True,
                                              account_equity_reconciliation_ok=True, complete_reconstruction_ok=True)
        healthy = self.evaluate(MonitoringInputs(execution=healthy_input), (incident,), T + timedelta(seconds=3))
        resolved = next(item for item in healthy.incidents if item.incident_id == incident.incident_id)
        self.assertEqual(resolved.state, IncidentState.RESOLVED)
        later = self.evaluate(MonitoringInputs(execution=ExecutionHealthInput(blocked_order_fill_count=1)), (resolved,), T + timedelta(seconds=4))
        self.assertNotEqual(next(item for item in later.incidents if item.reason_code == "blocked_order_filled").incident_id, incident.incident_id)


class FaultOrchestratorAuditTests(unittest.TestCase):
    def configured(self, kind, configuration=None):
        session, broker, gateway = fixture()
        fault = ConnectionFault(session.fix_session_id, kind, T + timedelta(seconds=2), "audit_fault", configuration or {})
        orchestrator = FaultOrchestrator(FaultSchedule((fault,)), session)
        gateway.fault_orchestrator = orchestrator
        return session, broker, gateway, orchestrator

    def test_all_fault_types_activate_with_evidence_and_outcome(self):
        # Drop before Logon uses a fresh pending session.
        pending = SimulatedFixSession(FixSessionConfiguration("CLIENT", "SERVER")); pending.connect(T)
        fault = ConnectionFault(pending.fix_session_id, ConnectionFaultType.DROP_BEFORE_LOGON, T, "audit")
        orchestrator = FaultOrchestrator(FaultSchedule((fault,)), pending)
        self.assertIsNone(orchestrator.before_inbound(logon(1, "SERVER", "CLIENT", T), T))
        self.assertEqual(pending.state, FixSessionState.DISCONNECTED)
        for kind in (ConnectionFaultType.DROP_AFTER_ACKNOWLEDGEMENT, ConnectionFaultType.DROP_ACTIVE_ORDER,
                     ConnectionFaultType.DUPLICATE_INBOUND, ConnectionFaultType.INBOUND_SEQUENCE_GAP,
                     ConnectionFaultType.DELAYED_OUTBOUND_REPORT):
            session, _, gateway, orchestrator = self.configured(kind, {"delay_seconds": 1, "gap_size": 1})
            gateway.handle(order(2, f"{kind.value}", FixSide.BUY), T + timedelta(seconds=2))
            self.assertEqual(len(orchestrator.activated_faults), 1)
            self.assertEqual(len(orchestrator.monitoring_evidence), 1)
        session, _, gateway, orchestrator = self.configured(ConnectionFaultType.HEARTBEAT_RESPONSE_LOSS)
        response = gateway.handle(test_request(2, "SERVER", "CLIENT", T + timedelta(seconds=2), "X"), T + timedelta(seconds=2))
        self.assertEqual(response, ())
        session, _, _, orchestrator = self.configured(ConnectionFaultType.RECONNECT_DELAY, {"delay_seconds": 2})
        session.drop(T + timedelta(seconds=1))
        self.assertIsNone(orchestrator.reconnect(T + timedelta(seconds=2)))
        self.assertIsNotNone(orchestrator.reconnect(T + timedelta(seconds=4)))


class ExactCodecCompatibilityTests(unittest.TestCase):
    def test_every_supported_message_has_stable_exact_encoding(self):
        messages = (
            heartbeat(1, "A", "B", T), test_request(1, "A", "B", T, "X"),
            resend_request(1, "A", "B", T, 1, 2),
            reject(1, "A", "B", T, 1, "bad", FixMessageType.NEW_ORDER_SINGLE),
            sequence_reset(1, "A", "B", T, 2), logout(1, "A", "B", T, "bye"), logon(1, "A", "B", T),
            execution_report(1, "A", "B", T, order_id="O", exec_id="E", cl_ord_id="C", symbol="BTC",
                             side=FixSide.BUY, ord_status=FixOrdStatus.NEW, exec_type=FixExecType.NEW,
                             leaves_qty=Decimal("1"), cum_qty=Decimal("0"), avg_px=Decimal("0")),
            order_cancel_reject(1, "A", "B", T, order_id="NONE", cl_ord_id="X", orig_cl_ord_id="C",
                                ord_status=FixOrdStatus.REJECTED, text="unknown"),
            new_order_single(1, "A", "B", T, cl_ord_id="C", symbol="BTC", side=FixSide.BUY,
                             quantity=Decimal("1"), order_type=FixOrderType.MARKET),
            order_cancel_request(1, "A", "B", T, cl_ord_id="X", orig_cl_ord_id="C", symbol="BTC", side=FixSide.BUY),
            business_message_reject(1, "A", "B", T, ref_seq_num=1, ref_msg_type=FixMessageType.NEW_ORDER_SINGLE, text="bad"),
        )
        expected = {
            "0": "58e89ec0cfb90620deeff006751533699b471768ebf608a50e00244eb0efa138",
            "1": "8e96e1906d4a6dd8a1e493a0725ed0daf532f1923c6e4427f7acccafd3576e8e",
            "2": "1e6157a007ddb60d4dd0c3bab23906ef3684b06b960859877709d2d89fad838b",
            "3": "6245b1a26d5d895e56438f5820de3adc20698042b14314f2f0f24fcd588a6749",
            "4": "61aab26d39e445cea059d94f3a8bf95940c753c8f40326711816b82775419718",
            "5": "c4746e862b04c4aafa1fc2a96ab4b1d811ab69373160d94575752cc8a4543d12",
            "A": "3bf8146d11e1b4f58c79bce6ce9ab06234c4da365bd3951d81b14a486374f499",
            "8": "01b03aa75b5332fb6722137ed7ff043d2c8db1d1ae609f00a22d244cb268be50",
            "9": "d5c26a494c011b82df42f4a9332f100bf90527c76098aa07de4147207d1a61eb",
            "D": "81726eea040acee65c143a6c80168faaab78ac8af474d86bcbbd23e6c063b400",
            "F": "8913f5f8a1facbb772fcbc460c549ba2f70a3469cc3d01493863bfdf194d6ef0",
            "j": "6b367edc8eeb11110989b9dc95034141ce499f286e758297e89173271add9b62",
        }
        codec = FixCodec()
        self.assertEqual({message.msg_type for message in messages}, set(FixMessageType))
        for message in messages:
            raw = codec.encode(message)
            self.assertEqual(hashlib.sha256(raw).hexdigest(), expected[message.msg_type.value])
            self.assertEqual(codec.encode(codec.decode(raw)), raw)

class CliGateAuditTests(unittest.TestCase):
    def test_default_clis_do_not_connect_or_import_driver_or_open_socket(self):
        from secure_eval_wrapper.monitoring import cli as monitor_cli
        from secure_eval_wrapper.fix import cli as fix_cli
        sys.modules.pop("psycopg", None)
        with patch.object(socket, "socket", side_effect=AssertionError("socket attempted")):
            with patch.object(monitor_cli, "_connect_postgres", side_effect=AssertionError("database attempted")):
                self.assertEqual(monitor_cli.main([]), 0)
            with patch.object(fix_cli, "_connect_postgres", side_effect=AssertionError("database attempted")):
                self.assertEqual(fix_cli.main([]), 0)
        self.assertNotIn("psycopg", sys.modules)

    def test_persist_flag_requires_environment_gate(self):
        from secure_eval_wrapper.monitoring import cli as monitor_cli
        from secure_eval_wrapper.fix import cli as fix_cli
        with patch.dict(os.environ, {"ENABLE_POSTGRES_PERSISTENCE": ""}):
            with self.assertRaises(SystemExit): monitor_cli.main(["--persist"])
            with self.assertRaises(SystemExit): fix_cli.main(["--persist"])


if __name__ == "__main__":
    unittest.main()
