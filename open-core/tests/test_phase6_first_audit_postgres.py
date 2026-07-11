from __future__ import annotations

import copy
import io
import os
import unittest
from contextlib import redirect_stdout
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from unittest.mock import patch

from secure_eval_wrapper.fix.cli import main as fix_cli_main
from secure_eval_wrapper.fix.codec import FixCodec
from secure_eval_wrapper.fix.faults import FaultOrchestrator, FaultSchedule
from secure_eval_wrapper.fix.messages import heartbeat, logon, logout, sequence_reset
from secure_eval_wrapper.fix.models import ConnectionFault, ConnectionFaultType, FixDirection, FixMessage, FixMessageType, FixSessionConfiguration
from secure_eval_wrapper.fix.session import SimulatedFixSession
from secure_eval_wrapper.monitoring.cli import build_demo_bundle, main as monitoring_cli_main
from secure_eval_wrapper.monitoring.configuration import MonitoringConfiguration
from secure_eval_wrapper.monitoring.engine import MonitoringEngine, MonitoringInputs
from secure_eval_wrapper.monitoring.execution_health import ExecutionHealthInput
from secure_eval_wrapper.monitoring.models import MonitoredRunReference, PublicSafeProvenance
from secure_eval_wrapper.monitoring.persistence import MonitoringBundlePersistenceError, persist_fix_transition, persist_monitoring_bundle
from secure_eval_wrapper.storage.postgres.phase6_repositories import Phase6ConflictError, PostgresPhase6Repository

T = datetime(2026, 1, 1, tzinfo=timezone.utc)


@unittest.skipUnless(os.environ.get("RUN_POSTGRES_INTEGRATION", "").lower() == "true", "real PostgreSQL integration is explicitly gated")
class Phase6FirstAuditPostgresTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import psycopg
        cls.connection = psycopg.connect(
            host=os.environ["POSTGRES_HOST"], port=int(os.environ["POSTGRES_PORT"]),
            dbname=os.environ["POSTGRES_DB"], user=os.environ["POSTGRES_USER"],
            password=os.environ["POSTGRES_PASSWORD"], sslmode=os.environ.get("POSTGRES_SSLMODE", "disable"),
        )
        cls.repo = PostgresPhase6Repository(cls.connection)

    @classmethod
    def tearDownClass(cls):
        cls.cleanup()
        cls.connection.close()

    @classmethod
    def cleanup(cls):
        try:
            with cls.connection.cursor() as cursor:
                cursor.execute("DELETE FROM monitoring.fix_sessions WHERE session_key LIKE 'AUDIT_%' OR session_key='PUBLIC_CLIENT->SIMULATED_VENUE'")
                cursor.execute("DELETE FROM monitoring.monitoring_runs WHERE monitored_identity IN ('public-monitoring-demo','audit-postgres')")
                cursor.execute("DELETE FROM monitoring.incidents WHERE monitored_identity IN ('public-monitoring-demo','audit-postgres')")
            cls.connection.commit()
        except Exception:
            cls.connection.rollback()
            raise

    def setUp(self):
        self.cleanup()

    def session(self, sender="AUDIT_CLIENT", target="AUDIT_SERVER"):
        session = SimulatedFixSession(FixSessionConfiguration(sender, target, Decimal("5"), Decimal("2"), Decimal("7")))
        outbound = session.connect(T)
        inbound = logon(1, target, sender, T)
        session.receive(inbound, T)
        return session, outbound, inbound

    def persist_initial(self, session, outbound, inbound):
        persist_fix_transition(
            self.repo, session=session, at_utc=T, inbound_messages=(inbound,), outbound_messages=(outbound,),
            session_events=tuple(session.events),
        )

    def test_real_monitoring_and_fix_cli_persistence(self):
        with patch.dict(os.environ, {"ENABLE_POSTGRES_PERSISTENCE": "true"}):
            monitoring_output = io.StringIO()
            with redirect_stdout(monitoring_output):
                self.assertEqual(monitoring_cli_main(["--persist"]), 0)
            self.assertIn('"persistence_status":"postgresql"', monitoring_output.getvalue())
            fix_output = io.StringIO()
            with redirect_stdout(fix_output):
                self.assertEqual(fix_cli_main(["--persist"]), 0)
            self.assertIn('"persistence_status":"postgresql"', fix_output.getvalue())
            self.assertIn('"resulting_quantity":"0"', fix_output.getvalue())
        with self.connection.cursor() as cursor:
            cursor.execute("SELECT count(*) FROM monitoring.monitoring_runs WHERE monitored_identity='public-monitoring-demo'")
            self.assertEqual(cursor.fetchone()[0], 1)
            cursor.execute("SELECT count(*) FROM monitoring.fix_sessions WHERE session_key='PUBLIC_CLIENT->SIMULATED_VENUE'")
            self.assertEqual(cursor.fetchone()[0], 1)
            cursor.execute("SELECT count(*) FROM monitoring.fix_messages m JOIN monitoring.fix_sessions s USING(fix_session_id) WHERE s.session_key='PUBLIC_CLIENT->SIMULATED_VENUE'")
            self.assertGreater(cursor.fetchone()[0], 10)
            cursor.execute("SELECT count(*) FROM monitoring.fix_session_events e JOIN monitoring.fix_sessions s USING(fix_session_id) WHERE s.session_key='PUBLIC_CLIENT->SIMULATED_VENUE'")
            self.assertGreater(cursor.fetchone()[0], 10)
            demo_run = "00000000-0000-5000-8000-000000000001"
            cursor.execute("SELECT count(*) FROM execution.order_intents WHERE run_id=%s AND execution_mode='simulated_fix'", (demo_run,))
            self.assertGreater(cursor.fetchone()[0], 0)
            cursor.execute("SELECT count(*) FROM execution.risk_decisions WHERE run_id=%s", (demo_run,))
            self.assertGreater(cursor.fetchone()[0], 0)
            cursor.execute("SELECT count(*) FROM execution.orders WHERE run_id=%s", (demo_run,))
            self.assertGreater(cursor.fetchone()[0], 0)
            cursor.execute("SELECT count(*) FROM execution.fills WHERE run_id=%s", (demo_run,))
            self.assertGreater(cursor.fetchone()[0], 0)
            cursor.execute("SELECT count(*) FROM monitoring.fix_order_links l JOIN monitoring.fix_sessions s USING(fix_session_id) WHERE s.session_key='PUBLIC_CLIENT->SIMULATED_VENUE'")
            self.assertGreater(cursor.fetchone()[0], 0)
            cursor.execute("SELECT count(*) FROM monitoring.fix_messages m JOIN monitoring.fix_sessions s USING(fix_session_id) WHERE s.session_key='PUBLIC_CLIENT->SIMULATED_VENUE' AND m.direction='outbound' AND m.msg_type='8'")
            self.assertGreater(cursor.fetchone()[0], 0)

    def test_unknown_incident_evidence_persists_open_then_healthy_resolves(self):
        engine = MonitoringEngine()
        config = MonitoringConfiguration()
        reference = MonitoredRunReference("audit-postgres")
        provenance = PublicSafeProvenance("a" * 64, "commit", "tree")
        failed = engine.evaluate(configuration=config, as_of_utc=T,
                                 inputs=MonitoringInputs(execution=ExecutionHealthInput(blocked_order_fill_count=1)),
                                 reference=reference, provenance=provenance)
        incident = next(item for item in failed.incidents if item.reason_code == "blocked_order_filled")
        persist_monitoring_bundle(self.repo, failed)
        unknown = engine.evaluate(configuration=config, as_of_utc=T + timedelta(seconds=1),
                                  inputs=MonitoringInputs(execution=None), reference=reference,
                                  provenance=provenance, previous_incidents=(incident,))
        persist_monitoring_bundle(self.repo, unknown)
        with self.connection.cursor() as cursor:
            cursor.execute("SELECT state,occurrence_count FROM monitoring.incidents WHERE incident_id=%s", (incident.incident_id,))
            self.assertEqual(cursor.fetchone(), ("open", 1))
        healthy_input = ExecutionHealthInput(position_reconciliation_ok=True, cash_reconciliation_ok=True,
                                              account_equity_reconciliation_ok=True, complete_reconstruction_ok=True)
        healthy = engine.evaluate(configuration=config, as_of_utc=T + timedelta(seconds=2),
                                  inputs=MonitoringInputs(execution=healthy_input), reference=reference,
                                  provenance=provenance, previous_incidents=(incident,))
        persist_monitoring_bundle(self.repo, healthy)
        with self.connection.cursor() as cursor:
            cursor.execute("SELECT state,resolved_at_utc FROM monitoring.incidents WHERE incident_id=%s", (incident.incident_id,))
            state, resolved_at = cursor.fetchone()
        self.assertEqual(state, "resolved")
        self.assertIsNotNone(resolved_at)

    def test_rejected_raw_observation_is_persisted_without_sequence_advance(self):
        session = SimulatedFixSession(FixSessionConfiguration("AUDIT_REJECT", "AUDIT_SERVER"))
        outbound = session.connect(T)
        raw = FixCodec().encode(logon(1, "AUDIT_SERVER", "AUDIT_REJECT", T))
        bad = raw[:-5] + b"000\x01"
        result = session.receive_raw(bad, T)
        self.assertEqual(session.next_inbound_seq_num, 1)
        persist_fix_transition(
            self.repo, session=session, at_utc=T, outbound_messages=(outbound,),
            rejected_observations=(result.rejected_observation,), session_events=tuple(session.events),
        )
        with self.connection.cursor() as cursor:
            cursor.execute("SELECT validation_status,rejection_code,rejection_reason,msg_seq_num FROM monitoring.fix_messages WHERE fix_message_id=%s", (result.rejected_observation.observation_id,))
            row = cursor.fetchone()
        self.assertEqual(row[0], "rejected")
        self.assertEqual(row[1], "validation_rejected")
        self.assertTrue(row[2])
        self.assertEqual(row[3], 1)

    def test_replay_persistence_is_idempotent_and_changed_content_conflicts(self):
        session, outbound, inbound = self.session("AUDIT_REPLAY", "AUDIT_SERVER")
        self.persist_initial(session, outbound, inbound)
        original = heartbeat(2, "AUDIT_SERVER", "AUDIT_REPLAY", T + timedelta(seconds=1))
        before_events = len(session.events)
        session.receive(original, T + timedelta(seconds=1))
        persist_fix_transition(self.repo, session=session, at_utc=T + timedelta(seconds=1), inbound_messages=(original,), session_events=tuple(session.events[before_events:]))
        replay = FixMessage(FixMessageType.HEARTBEAT, 2, "AUDIT_SERVER", "AUDIT_REPLAY", T + timedelta(seconds=2),
                            poss_dup_flag=True, orig_sending_time_utc=original.sending_time_utc)
        self.assertEqual(session.receive(replay, T + timedelta(seconds=2)).disposition.value, "accepted_replay")
        with self.repo.transaction():
            first_id = self.repo.record_fix_message(session.fix_session_id, FixDirection.INBOUND, replay, T + timedelta(seconds=2), None)
        self.assertEqual(first_id, original.fix_message_id)
        changed = FixMessage(FixMessageType.HEARTBEAT, 2, "AUDIT_SERVER", "AUDIT_REPLAY", T + timedelta(seconds=3),
                             fields={112: "CHANGED"}, poss_dup_flag=True, orig_sending_time_utc=original.sending_time_utc)
        with self.assertRaises(Phase6ConflictError):
            with self.repo.transaction():
                self.repo.record_fix_message(session.fix_session_id, FixDirection.INBOUND, changed, T + timedelta(seconds=3), None)

    def test_projection_rejects_stale_regression_and_illegal_transition_and_accepts_recovery_logout(self):
        session, outbound, inbound = self.session("AUDIT_STATE", "AUDIT_SERVER")
        self.persist_initial(session, outbound, inbound)
        stale, _, _ = self.session("AUDIT_STATE", "AUDIT_SERVER")
        stale.persisted_state_version = session.persisted_state_version
        stale.persisted_record_sha256 = session.persisted_record_sha256
        event_start = len(session.events)
        beat = heartbeat(2, "AUDIT_SERVER", "AUDIT_STATE", T + timedelta(seconds=1))
        session.receive(beat, T + timedelta(seconds=1))
        persist_fix_transition(self.repo, session=session, at_utc=T + timedelta(seconds=1), inbound_messages=(beat,), session_events=tuple(session.events[event_start:]))
        stale_beat = heartbeat(2, "AUDIT_SERVER", "AUDIT_STATE", T + timedelta(seconds=2))
        stale.receive(stale_beat, T + timedelta(seconds=2))
        with self.assertRaises(MonitoringBundlePersistenceError):
            persist_fix_transition(self.repo, session=stale, at_utc=T + timedelta(seconds=2), inbound_messages=(stale_beat,), session_events=tuple(stale.events[event_start:]))
        current_seq = session.next_inbound_seq_num
        session.next_inbound_seq_num -= 1
        with self.assertRaises(MonitoringBundlePersistenceError):
            persist_fix_transition(self.repo, session=session, at_utc=T + timedelta(seconds=2))
        session.next_inbound_seq_num = current_seq
        prior_state = session.state
        session.state = session.state.LOGON_PENDING
        with self.assertRaises(MonitoringBundlePersistenceError):
            persist_fix_transition(self.repo, session=session, at_utc=T + timedelta(seconds=2))
        session.state = prior_state
        start = len(session.events)
        gap = heartbeat(5, "AUDIT_SERVER", "AUDIT_STATE", T + timedelta(seconds=3))
        session.receive(gap, T + timedelta(seconds=3))
        persist_fix_transition(self.repo, session=session, at_utc=T + timedelta(seconds=3), outbound_messages=(session.outbound_messages[-1],), session_events=tuple(session.events[start:]))
        start = len(session.events)
        reset = sequence_reset(3, "AUDIT_SERVER", "AUDIT_STATE", T + timedelta(seconds=4), 5)
        session.receive(reset, T + timedelta(seconds=4))
        persist_fix_transition(self.repo, session=session, at_utc=T + timedelta(seconds=4), inbound_messages=(reset,), session_events=tuple(session.events[start:]))
        start = len(session.events)
        session.request_logout(T + timedelta(seconds=5))
        persist_fix_transition(self.repo, session=session, at_utc=T + timedelta(seconds=5), outbound_messages=(session.outbound_messages[-1],), session_events=tuple(session.events[start:]))
        start = len(session.events)
        peer_logout = logout(5, "AUDIT_SERVER", "AUDIT_STATE", T + timedelta(seconds=6))
        session.receive(peer_logout, T + timedelta(seconds=6))
        persist_fix_transition(self.repo, session=session, at_utc=T + timedelta(seconds=6), inbound_messages=(peer_logout,), session_events=tuple(session.events[start:]))
        self.assertEqual(self.repo.get_fix_session(session.fix_session_id)["state"], "terminated")

    def test_complete_fix_transition_rolls_back_without_orphans(self):
        session, outbound, inbound = self.session("AUDIT_ROLLBACK", "AUDIT_SERVER")
        self.persist_initial(session, outbound, inbound)
        stored_version = session.persisted_state_version
        stored_hash = session.persisted_record_sha256
        start = len(session.events)
        beat = heartbeat(2, "AUDIT_SERVER", "AUDIT_ROLLBACK", T + timedelta(seconds=1))
        session.receive(beat, T + timedelta(seconds=1))

        class Failing:
            def __init__(self, repository): self.repository = repository
            def __getattr__(self, name):
                if name == "record_fix_session_event":
                    return lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("injected event failure"))
                return getattr(self.repository, name)

        with self.assertRaises(MonitoringBundlePersistenceError):
            persist_fix_transition(Failing(self.repo), session=session, at_utc=T + timedelta(seconds=1), inbound_messages=(beat,), session_events=tuple(session.events[start:]))
        self.assertEqual(session.persisted_state_version, stored_version)
        self.assertEqual(session.persisted_record_sha256, stored_hash)
        row = self.repo.get_fix_session(session.fix_session_id)
        self.assertEqual(row["state_version"], stored_version)
        with self.connection.cursor() as cursor:
            cursor.execute("SELECT count(*) FROM monitoring.fix_messages WHERE fix_session_id=%s AND msg_seq_num=2 AND direction='inbound'", (session.fix_session_id,))
            self.assertEqual(cursor.fetchone()[0], 0)
            cursor.execute("SELECT count(*) FROM monitoring.fix_messages m LEFT JOIN monitoring.fix_sessions s USING(fix_session_id) WHERE s.fix_session_id IS NULL")
            self.assertEqual(cursor.fetchone()[0], 0)
            cursor.execute("SELECT count(*) FROM monitoring.fix_session_events e LEFT JOIN monitoring.fix_sessions s USING(fix_session_id) WHERE e.fix_session_id IS NOT NULL AND s.fix_session_id IS NULL")
            self.assertEqual(cursor.fetchone()[0], 0)
            cursor.execute("SELECT count(*) FROM monitoring.fix_order_links l LEFT JOIN monitoring.fix_sessions s USING(fix_session_id) WHERE s.fix_session_id IS NULL")
            self.assertEqual(cursor.fetchone()[0], 0)
            cursor.execute("SELECT count(*) FROM monitoring.connection_faults f LEFT JOIN monitoring.fix_sessions s USING(fix_session_id) WHERE s.fix_session_id IS NULL")
            self.assertEqual(cursor.fetchone()[0], 0)

    def test_fault_activation_and_event_persist_atomically(self):
        session, outbound, inbound = self.session("AUDIT_FAULT", "AUDIT_SERVER")
        self.persist_initial(session, outbound, inbound)
        fault = ConnectionFault(session.fix_session_id, ConnectionFaultType.HEARTBEAT_RESPONSE_LOSS, T + timedelta(seconds=1), "audit_fault")
        orchestrator = FaultOrchestrator(FaultSchedule((fault,)), session)
        start = len(session.events)
        activated = orchestrator._activate_one(ConnectionFaultType.HEARTBEAT_RESPONSE_LOSS, T + timedelta(seconds=1), "response suppressed")
        persist_fix_transition(self.repo, session=session, at_utc=T + timedelta(seconds=1), session_events=tuple(session.events[start:]), faults=(activated,))
        with self.connection.cursor() as cursor:
            cursor.execute("SELECT activated_at_utc FROM monitoring.connection_faults WHERE connection_fault_id=%s", (activated.connection_fault_id,))
            self.assertIsNotNone(cursor.fetchone()[0])
            cursor.execute("SELECT count(*) FROM monitoring.fix_session_events WHERE fix_session_id=%s AND event_type='fault_activated'", (session.fix_session_id,))
            self.assertEqual(cursor.fetchone()[0], 1)


if __name__ == "__main__":
    unittest.main()
