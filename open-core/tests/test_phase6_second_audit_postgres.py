from __future__ import annotations

import os
import unittest
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from uuid import UUID

from secure_eval_wrapper.alpha.identity import SeriesIdentity
from secure_eval_wrapper.data_collection.models import InstrumentType
from secure_eval_wrapper.execution.brokers.simulated import SimulatedBroker
from secure_eval_wrapper.execution.fees import ZeroFeeModel
from secure_eval_wrapper.execution.models import AccountingMode, BrokerConfiguration
from secure_eval_wrapper.execution.slippage import ZeroSlippage
from secure_eval_wrapper.fix.cli import _run_demo_context
from secure_eval_wrapper.fix.codec import FixCodec
from secure_eval_wrapper.fix.gateway import GatewaySeries, SimulatedFixGateway
from secure_eval_wrapper.fix.messages import heartbeat, logon, new_order_single
from secure_eval_wrapper.fix.models import (
    FixDirection,
    FixOrderType,
    FixSessionConfiguration,
    FixSessionEvent,
    FixSessionEventType,
    FixSessionState,
    FixSide,
)
from secure_eval_wrapper.fix.session import SimulatedFixSession
from secure_eval_wrapper.monitoring.persistence import (
    MonitoringBundlePersistenceError,
    persist_fix_transition,
)
from secure_eval_wrapper.storage.postgres.phase6_repositories import (
    PostgresPhase6Repository,
)


T = datetime(2026, 1, 1, tzinfo=timezone.utc)

TWO_SELL_RUN_ID = UUID("00000000-0000-5000-8000-000000000601")
INCREMENTAL_RUN_ID = UUID("00000000-0000-5000-8000-000000000602")


def build_spot_gateway(name: str, *, inventory: Decimal, run_id: UUID):
    sender = f"SECOND_AUDIT_{name}"
    target = "SECOND_AUDIT_SERVER"
    session = SimulatedFixSession(FixSessionConfiguration(sender, target))
    session.connect(T)
    session.receive(logon(1, target, sender, T), T)
    identity = SeriesIdentity(
        "synthetic",
        "simulated",
        "BTCUSDT",
        "BTC/USDT",
        InstrumentType.SPOT,
        "1m",
        settlement_asset="USDT",
    )
    gateway = SimulatedFixGateway(
        session=session,
        broker=SimulatedBroker(
            BrokerConfiguration(),
            fee_model=ZeroFeeModel(),
            slippage_model=ZeroSlippage(),
        ),
        run_id=run_id,
        series_by_symbol={
            "BTC/USDT": GatewaySeries(
                identity,
                AccountingMode.SPOT,
                current_quantity=inventory,
                reference_price=Decimal("100"),
            )
        },
        implementation_code_sha256="a" * 64,
        repository_commit_sha="phase6-second-audit-postgres",
        data_sha256="b" * 64,
    )
    return session, gateway


def sell_order(sender: str, sequence_number: int, cl_ord_id: str):
    return new_order_single(
        sequence_number,
        "SECOND_AUDIT_SERVER",
        sender,
        T + timedelta(seconds=sequence_number),
        cl_ord_id=cl_ord_id,
        symbol="BTC/USDT",
        side=FixSide.SELL,
        quantity=Decimal("1"),
        order_type=FixOrderType.MARKET,
    )


@unittest.skipUnless(
    os.environ.get("RUN_POSTGRES_INTEGRATION", "").lower() == "true",
    "real PostgreSQL integration is explicitly gated",
)
class Phase6SecondAuditPostgresTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import psycopg

        cls.connection = psycopg.connect(
            host=os.environ["POSTGRES_HOST"],
            port=int(os.environ["POSTGRES_PORT"]),
            dbname=os.environ["POSTGRES_DB"],
            user=os.environ["POSTGRES_USER"],
            password=os.environ["POSTGRES_PASSWORD"],
            sslmode=os.environ.get("POSTGRES_SSLMODE", "disable"),
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
                cursor.execute(
                    "DELETE FROM monitoring.fix_sessions "
                    "WHERE session_key LIKE 'SECOND_AUDIT_%' "
                    "OR session_key='PUBLIC_CLIENT->SIMULATED_VENUE'"
                )
                for run_id in (
                    UUID("00000000-0000-5000-8000-000000000001"),
                    TWO_SELL_RUN_ID,
                    INCREMENTAL_RUN_ID,
                ):
                    cursor.execute(
                        "DELETE FROM execution.risk_decisions WHERE run_id=%s",
                        (run_id,),
                    )
                    cursor.execute(
                        "DELETE FROM execution.fills WHERE run_id=%s",
                        (run_id,),
                    )
                    cursor.execute(
                        "DELETE FROM execution.orders WHERE run_id=%s",
                        (run_id,),
                    )
                    cursor.execute(
                        "DELETE FROM execution.order_intents WHERE run_id=%s",
                        (run_id,),
                    )
            cls.connection.commit()
        except Exception:
            cls.connection.rollback()
            raise

    def setUp(self):
        self.cleanup()

    def session(self, name):
        session = SimulatedFixSession(
            FixSessionConfiguration(f"SECOND_AUDIT_{name}", "SECOND_AUDIT_SERVER")
        )
        outbound = session.connect(T)
        inbound = logon(1, "SECOND_AUDIT_SERVER", f"SECOND_AUDIT_{name}", T)
        session.receive(inbound, T)
        return session, outbound, inbound

    def persist_initial(self, session, outbound, inbound):
        persist_fix_transition(
            self.repo,
            session=session,
            at_utc=T,
            inbound_messages=(inbound,),
            outbound_messages=(outbound,),
            session_events=tuple(session.events),
        )

    def test_changed_state_without_event_fails(self):
        session, outbound, inbound = self.session("STATE")
        self.persist_initial(session, outbound, inbound)
        session.state = FixSessionState.RECOVERING
        with self.assertRaises(MonitoringBundlePersistenceError):
            persist_fix_transition(
                self.repo,
                session=session,
                at_utc=T + timedelta(seconds=1),
            )

    def test_changed_sequence_without_event_fails(self):
        session, outbound, inbound = self.session("SEQUENCE")
        self.persist_initial(session, outbound, inbound)
        session.next_inbound_seq_num += 1
        with self.assertRaises(MonitoringBundlePersistenceError):
            persist_fix_transition(
                self.repo,
                session=session,
                at_utc=T + timedelta(seconds=1),
            )

    def test_event_tail_state_mismatch_fails_at_commit(self):
        session, outbound, inbound = self.session("TAIL")
        self.persist_initial(session, outbound, inbound)
        event = FixSessionEvent(
            session.fix_session_id,
            FixSessionEventType.STATE_TRANSITION,
            T + timedelta(seconds=1),
            FixSessionState.ESTABLISHED,
            FixSessionState.DISCONNECTED,
            "injected_tail_mismatch",
            transition_sequence=session.persisted_transition_sequence + 1,
            previous_event_sha256=session.persisted_event_sha256,
            projected_next_inbound_seq_num=session.next_inbound_seq_num,
            projected_next_outbound_seq_num=session.next_outbound_seq_num,
            projected_last_inbound_at_utc=session.last_inbound_at_utc,
            projected_last_outbound_at_utc=session.last_outbound_at_utc,
            projected_pending_test_request_id=session.pending_test_request_id,
            projected_pending_test_deadline_at_utc=session.pending_test_deadline_at_utc,
            projected_test_request_grace_expired=session.test_request_grace_expired,
        )
        with self.assertRaises(Exception):
            with self.connection.transaction():
                self.repo.record_fix_session_event(session, event)
                with self.connection.cursor() as cursor:
                    cursor.execute(
                        "UPDATE monitoring.fix_sessions SET "
                        "state_version=state_version+1,"
                        "previous_state_hash=record_sha256,"
                        "record_sha256=%s,"
                        "last_transition_event_id=%s,"
                        "last_transition_sequence=%s,"
                        "authoritative_event_sha256=%s "
                        "WHERE fix_session_id=%s",
                        (
                            "c" * 64,
                            event.event_id,
                            event.transition_sequence,
                            event.record_sha256,
                            session.fix_session_id,
                        ),
                    )

    def test_missing_event_ordinal_fails(self):
        session, outbound, inbound = self.session("ORDINAL")
        self.persist_initial(session, outbound, inbound)
        event = FixSessionEvent(
            session.fix_session_id,
            FixSessionEventType.MESSAGE_ACCEPTED,
            T + timedelta(seconds=1),
            FixSessionState.ESTABLISHED,
            FixSessionState.ESTABLISHED,
            "injected_ordinal_gap",
            transition_sequence=session.persisted_transition_sequence + 2,
            previous_event_sha256=session.persisted_event_sha256,
            projected_next_inbound_seq_num=session.next_inbound_seq_num,
            projected_next_outbound_seq_num=session.next_outbound_seq_num,
            projected_last_inbound_at_utc=session.last_inbound_at_utc,
            projected_last_outbound_at_utc=session.last_outbound_at_utc,
            projected_pending_test_request_id=session.pending_test_request_id,
            projected_pending_test_deadline_at_utc=session.pending_test_deadline_at_utc,
            projected_test_request_grace_expired=session.test_request_grace_expired,
        )
        with self.assertRaises(Exception):
            with self.connection.transaction():
                self.repo.record_fix_session_event(session, event)

    def test_exact_unchanged_replay_is_idempotent(self):
        session, outbound, inbound = self.session("IDEMPOTENT")
        self.persist_initial(session, outbound, inbound)
        version = session.persisted_state_version
        record_hash = session.persisted_record_sha256
        persist_fix_transition(
            self.repo,
            session=session,
            at_utc=T + timedelta(seconds=1),
        )
        row = self.repo.get_fix_session(session.fix_session_id)
        self.assertEqual(row["state_version"], version)
        self.assertEqual(row["record_sha256"], record_hash)

    def test_legal_transition_with_matching_event_succeeds(self):
        session, outbound, inbound = self.session("LEGAL")
        self.persist_initial(session, outbound, inbound)
        start = len(session.events)
        message = heartbeat(
            2,
            "SECOND_AUDIT_SERVER",
            "SECOND_AUDIT_LEGAL",
            T + timedelta(seconds=1),
        )
        session.receive(message, T + timedelta(seconds=1))
        persist_fix_transition(
            self.repo,
            session=session,
            at_utc=T + timedelta(seconds=1),
            inbound_messages=(message,),
            session_events=tuple(session.events[start:]),
        )
        row = self.repo.get_fix_session(session.fix_session_id)
        self.assertEqual(row["next_inbound_seq_num"], 3)
        self.assertEqual(
            row["last_transition_sequence"],
            session.events[-1].transition_sequence,
        )
        self.assertEqual(
            row["authoritative_event_sha256"],
            session.events[-1].record_sha256,
        )

    def test_repeated_rejected_occurrences_are_separate_and_half_open(self):
        session = SimulatedFixSession(
            FixSessionConfiguration(
                "SECOND_AUDIT_REJECT",
                "SECOND_AUDIT_SERVER",
                Decimal("30"),
            )
        )
        outbound = session.connect(T)
        bad = b"not-a-fix-message"
        first = session.receive_raw(bad, T + timedelta(seconds=1))
        second = session.receive_raw(bad, T + timedelta(seconds=2))
        persist_fix_transition(
            self.repo,
            session=session,
            at_utc=T + timedelta(seconds=2),
            outbound_messages=(outbound,),
            rejected_observations=(first.rejected_observation, second.rejected_observation),
            rejected_occurrences=tuple(session.rejected_occurrences),
            session_events=tuple(session.events),
        )
        with self.connection.cursor() as cursor:
            cursor.execute(
                "SELECT count(*) FROM monitoring.fix_messages "
                "WHERE fix_session_id=%s AND validation_status='rejected'",
                (session.fix_session_id,),
            )
            self.assertEqual(cursor.fetchone()[0], 1)
            cursor.execute(
                "SELECT count(*) FROM monitoring.fix_rejection_occurrences "
                "WHERE fix_session_id=%s",
                (session.fix_session_id,),
            )
            self.assertEqual(cursor.fetchone()[0], 2)
        rows = self.repo.list_rejected_fix_occurrences(
            session.fix_session_id,
            T + timedelta(seconds=1),
            T + timedelta(seconds=2),
        )
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["processing_time_utc"], T + timedelta(seconds=1))

    def test_combined_fix_execution_child_failure_rolls_back_everything(self):
        _, session, gateway = _run_demo_context()

        class FailingRepository:
            def __init__(self, repository):
                self.repository = repository

            def __getattr__(self, name):
                if name == "record_fix_order_link":
                    return lambda *_args, **_kwargs: (_ for _ in ()).throw(
                        RuntimeError("injected FIX link failure")
                    )
                return getattr(self.repository, name)

        with self.assertRaises(MonitoringBundlePersistenceError):
            persist_fix_transition(
                FailingRepository(self.repo),
                session=session,
                at_utc=T + timedelta(seconds=11),
                inbound_messages=tuple(session.inbound_messages),
                outbound_messages=tuple(session.outbound_messages),
                rejected_observations=tuple(session.rejected_observations),
                rejected_occurrences=tuple(session.rejected_occurrences),
                session_events=tuple(session.events),
                order_links=gateway.links,
                order_intents=gateway.intents,
                risk_decisions=gateway.risk_decisions,
                orders=gateway.orders,
                fills=gateway.fills,
            )
        with self.connection.cursor() as cursor:
            for table in (
                "execution.order_intents",
                "execution.risk_decisions",
                "execution.orders",
                "execution.fills",
            ):
                cursor.execute(
                    f"SELECT count(*) FROM {table} WHERE run_id=%s",
                    (gateway.run_id,),
                )
                self.assertEqual(cursor.fetchone()[0], 0)
            cursor.execute(
                "SELECT count(*) FROM monitoring.fix_sessions WHERE fix_session_id=%s",
                (session.fix_session_id,),
            )
            self.assertEqual(cursor.fetchone()[0], 0)


    def test_real_two_sell_inventory_persists_without_negative_position(self):
        session, gateway = build_spot_gateway(
            "TWO_SELLS",
            inventory=Decimal("2"),
            run_id=TWO_SELL_RUN_ID,
        )
        sender = session.configuration.sender_comp_id
        gateway.handle(sell_order(sender, 2, "PERSIST-SELL-1"), T + timedelta(seconds=2))
        gateway.handle(sell_order(sender, 3, "PERSIST-SELL-2"), T + timedelta(seconds=3))
        gateway.process_bar_open(
            symbol="BTC/USDT",
            timestamp_utc=T + timedelta(seconds=4),
            open_price=Decimal("100"),
        )

        self.assertEqual(len(gateway.fills), 2)
        self.assertEqual(gateway.current_quantity("BTC/USDT", T), Decimal("0"))
        persist_fix_transition(
            self.repo,
            session=session,
            at_utc=T + timedelta(seconds=4),
            inbound_messages=tuple(session.inbound_messages),
            outbound_messages=tuple(session.outbound_messages),
            session_events=tuple(session.events),
            order_links=gateway.links,
            order_intents=gateway.intents,
            risk_decisions=gateway.risk_decisions,
            orders=gateway.orders,
            fills=gateway.fills,
        )

        with self.connection.cursor() as cursor:
            cursor.execute(
                "SELECT count(*), COALESCE(sum(quantity), 0) "
                "FROM execution.fills WHERE run_id=%s AND side='sell'",
                (TWO_SELL_RUN_ID,),
            )
            count, sold_quantity = cursor.fetchone()
            cursor.execute(
                "SELECT count(*) FROM execution.orders WHERE run_id=%s",
                (TWO_SELL_RUN_ID,),
            )
            order_count = cursor.fetchone()[0]
        self.assertEqual(count, 2)
        self.assertEqual(order_count, 2)
        self.assertEqual(Decimal("2") - sold_quantity, Decimal("0"))
        self.assertGreaterEqual(Decimal("2") - sold_quantity, Decimal("0"))

    def test_fresh_reconstructed_exact_session_replay_is_idempotent(self):
        original, outbound, inbound = self.session("FRESH_REPLAY")
        self.persist_initial(original, outbound, inbound)
        stored = self.repo.get_fix_session(original.fix_session_id)

        reconstructed, replay_outbound, replay_inbound = self.session("FRESH_REPLAY")
        self.assertIsNone(reconstructed.persisted_state_version)
        persist_fix_transition(
            self.repo,
            session=reconstructed,
            at_utc=T + timedelta(seconds=1),
            inbound_messages=(replay_inbound,),
            outbound_messages=(replay_outbound,),
            session_events=tuple(reconstructed.events),
        )

        replayed = self.repo.get_fix_session(original.fix_session_id)
        self.assertEqual(replayed["state_version"], stored["state_version"])
        self.assertEqual(replayed["record_sha256"], stored["record_sha256"])
        self.assertEqual(
            replayed["last_transition_sequence"],
            stored["last_transition_sequence"],
        )
        self.assertEqual(
            reconstructed.persisted_state_version,
            stored["state_version"],
        )
        self.assertEqual(
            reconstructed.persisted_record_sha256,
            stored["record_sha256"],
        )

    def test_unrelated_same_state_event_cannot_authorize_sequence_or_pending_mutation(self):
        sequence_session, outbound, inbound = self.session("UNRELATED_SEQUENCE")
        self.persist_initial(sequence_session, outbound, inbound)
        event_start = len(sequence_session.events)
        sequence_session.next_inbound_seq_num += 1
        sequence_session._event(
            FixSessionEventType.MESSAGE_REJECTED,
            T + timedelta(seconds=1),
            sequence_session.state,
            sequence_session.state,
            "injected_unrelated_sequence_authority",
        )
        with self.assertRaises(MonitoringBundlePersistenceError):
            persist_fix_transition(
                self.repo,
                session=sequence_session,
                at_utc=T + timedelta(seconds=1),
                session_events=tuple(sequence_session.events[event_start:]),
            )

        pending_session, outbound, inbound = self.session("UNRELATED_PENDING")
        self.persist_initial(pending_session, outbound, inbound)
        event_start = len(pending_session.events)
        outbound_start = len(pending_session.outbound_messages)
        pending_session.tick(T + timedelta(seconds=31))
        persist_fix_transition(
            self.repo,
            session=pending_session,
            at_utc=T + timedelta(seconds=31),
            outbound_messages=tuple(pending_session.outbound_messages[outbound_start:]),
            session_events=tuple(pending_session.events[event_start:]),
        )
        event_start = len(pending_session.events)
        pending_session.pending_test_request_id = "FORGED-PENDING-ID"
        pending_session.pending_test_deadline_at_utc += timedelta(seconds=1)
        pending_session._event(
            FixSessionEventType.MESSAGE_REJECTED,
            T + timedelta(seconds=32),
            pending_session.state,
            pending_session.state,
            "injected_unrelated_pending_authority",
        )
        with self.assertRaises(MonitoringBundlePersistenceError):
            persist_fix_transition(
                self.repo,
                session=pending_session,
                at_utc=T + timedelta(seconds=32),
                session_events=tuple(pending_session.events[event_start:]),
            )

    def test_rejection_occurrence_cannot_reference_valid_message(self):
        from psycopg.errors import ForeignKeyViolation

        session, outbound, inbound = self.session("VALID_PARENT")
        self.persist_initial(session, outbound, inbound)
        with self.assertRaises(ForeignKeyViolation):
            with self.connection.transaction():
                with self.connection.cursor() as cursor:
                    cursor.execute(
                        "INSERT INTO monitoring.fix_rejection_occurrences "
                        "(fix_rejection_occurrence_id,fix_message_id,fix_session_id,"
                        "direction,validation_status,processing_time_utc,record_sha256) "
                        "VALUES (%s,%s,%s,%s,'rejected',%s,%s)",
                        (
                            UUID("00000000-0000-5000-8000-000000000603"),
                            inbound.fix_message_id,
                            session.fix_session_id,
                            FixDirection.INBOUND.value,
                            T + timedelta(seconds=1),
                            "c" * 64,
                        ),
                    )

    def test_rejection_history_includes_stable_observation_evidence(self):
        session, outbound, inbound = self.session("REJECTION_EVIDENCE")
        raw = (
            b"8=FIX.4.4\x019=5\x0135=D\x0134=2\x01"
            b"49=SECOND_AUDIT_SERVER\x0156=SECOND_AUDIT_REJECTION_EVIDENCE\x01"
        )
        result = session.receive_raw(raw, T + timedelta(seconds=1))
        observation = result.rejected_observation
        persist_fix_transition(
            self.repo,
            session=session,
            at_utc=T + timedelta(seconds=1),
            inbound_messages=(inbound,),
            outbound_messages=(outbound,),
            rejected_observations=(observation,),
            rejected_occurrences=tuple(session.rejected_occurrences),
            session_events=tuple(session.events),
        )

        rows = self.repo.list_rejected_fix_occurrences(
            session.fix_session_id,
            T,
            T + timedelta(seconds=2),
        )
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["rejection_code"], observation.rejection_code)
        self.assertEqual(rows[0]["rejection_reason"], observation.rejection_reason)
        self.assertEqual(
            rows[0]["raw_message_sha256"],
            observation.raw_message_sha256,
        )
        self.assertEqual(rows[0]["validation_status"], "rejected")
        self.assertEqual(rows[0]["parsed_fields_jsonb"]["35"], "D")
        self.assertEqual(rows[0]["parsed_fields_jsonb"]["34"], "2")

    def test_update_path_failure_rolls_back_projection_and_event_tail(self):
        session, outbound, inbound = self.session("UPDATE_ROLLBACK")
        self.persist_initial(session, outbound, inbound)
        before = dict(self.repo.get_fix_session(session.fix_session_id))
        with self.connection.cursor() as cursor:
            cursor.execute(
                "SELECT count(*) FROM monitoring.fix_session_events "
                "WHERE fix_session_id=%s",
                (session.fix_session_id,),
            )
            event_count_before = cursor.fetchone()[0]
        persisted_metadata_before = (
            session.persisted_state_version,
            session.persisted_record_sha256,
            session.persisted_transition_sequence,
            session.persisted_event_sha256,
            session.persisted_transition_event_id,
        )
        event_start = len(session.events)
        message = heartbeat(
            2,
            "SECOND_AUDIT_SERVER",
            "SECOND_AUDIT_UPDATE_ROLLBACK",
            T + timedelta(seconds=1),
        )
        session.receive(message, T + timedelta(seconds=1))
        new_events = tuple(session.events[event_start:])

        class FailingAfterEventTail:
            def __init__(self, repository, event_count):
                self.repository = repository
                self.events_remaining = event_count

            def __getattr__(self, name):
                if name != "record_fix_session_event":
                    return getattr(self.repository, name)

                def record_then_fail(*args, **kwargs):
                    value = self.repository.record_fix_session_event(*args, **kwargs)
                    self.events_remaining -= 1
                    if self.events_remaining == 0:
                        raise RuntimeError("injected failure after event-tail insert")
                    return value

                return record_then_fail

        with self.assertRaises(MonitoringBundlePersistenceError):
            persist_fix_transition(
                FailingAfterEventTail(self.repo, len(new_events)),
                session=session,
                at_utc=T + timedelta(seconds=1),
                inbound_messages=(message,),
                session_events=new_events,
            )

        after = self.repo.get_fix_session(session.fix_session_id)
        for column in (
            "state_version",
            "record_sha256",
            "next_inbound_seq_num",
            "last_transition_event_id",
            "last_transition_sequence",
            "authoritative_event_sha256",
        ):
            self.assertEqual(after[column], before[column])
        with self.connection.cursor() as cursor:
            cursor.execute(
                "SELECT count(*) FROM monitoring.fix_session_events "
                "WHERE fix_session_id=%s",
                (session.fix_session_id,),
            )
            self.assertEqual(cursor.fetchone()[0], event_count_before)
            cursor.execute(
                "SELECT count(*) FROM monitoring.fix_messages "
                "WHERE fix_session_id=%s AND direction='inbound' AND msg_seq_num=2",
                (session.fix_session_id,),
            )
            self.assertEqual(cursor.fetchone()[0], 0)
        self.assertEqual(
            (
                session.persisted_state_version,
                session.persisted_record_sha256,
                session.persisted_transition_sequence,
                session.persisted_event_sha256,
                session.persisted_transition_event_id,
            ),
            persisted_metadata_before,
        )

    def test_incremental_acknowledged_then_filled_order_lifecycle_persists(self):
        session, gateway = build_spot_gateway(
            "INCREMENTAL",
            inventory=Decimal("1"),
            run_id=INCREMENTAL_RUN_ID,
        )
        sender = session.configuration.sender_comp_id
        gateway.handle(
            sell_order(sender, 2, "INCREMENTAL-SELL"),
            T + timedelta(seconds=2),
        )
        persist_fix_transition(
            self.repo,
            session=session,
            at_utc=T + timedelta(seconds=2),
            inbound_messages=tuple(session.inbound_messages),
            outbound_messages=tuple(session.outbound_messages),
            session_events=tuple(session.events),
            order_links=gateway.links,
            order_intents=gateway.intents,
            risk_decisions=gateway.risk_decisions,
            orders=gateway.orders,
        )
        acknowledged_version = session.persisted_state_version
        event_start = len(session.events)
        outbound_start = len(session.outbound_messages)

        gateway.process_bar_open(
            symbol="BTC/USDT",
            timestamp_utc=T + timedelta(seconds=3),
            open_price=Decimal("100"),
        )
        persist_fix_transition(
            self.repo,
            session=session,
            at_utc=T + timedelta(seconds=3),
            outbound_messages=tuple(session.outbound_messages[outbound_start:]),
            session_events=tuple(session.events[event_start:]),
            order_links=gateway.links,
            order_intents=gateway.intents,
            risk_decisions=gateway.risk_decisions,
            orders=gateway.orders,
            fills=gateway.fills,
        )

        self.assertGreater(session.persisted_state_version, acknowledged_version)
        self.assertEqual(gateway.current_quantity("BTC/USDT", T), Decimal("0"))
        with self.connection.cursor() as cursor:
            cursor.execute(
                "SELECT count(*) FROM execution.order_intents WHERE run_id=%s",
                (INCREMENTAL_RUN_ID,),
            )
            self.assertEqual(cursor.fetchone()[0], 1)
            cursor.execute(
                "SELECT count(*) FROM execution.orders WHERE run_id=%s",
                (INCREMENTAL_RUN_ID,),
            )
            self.assertEqual(cursor.fetchone()[0], 1)
            cursor.execute(
                "SELECT count(*), COALESCE(sum(quantity), 0) "
                "FROM execution.fills WHERE run_id=%s",
                (INCREMENTAL_RUN_ID,),
            )
            self.assertEqual(cursor.fetchone(), (1, Decimal("1")))
        lifecycle = self.repo.list_order_lifecycle(
            session.fix_session_id,
            "INCREMENTAL-SELL",
        )
        self.assertEqual(len(lifecycle), 2)
        self.assertEqual(sum(row["fill_id"] is None for row in lifecycle), 1)
        self.assertEqual(sum(row["fill_id"] is not None for row in lifecycle), 1)
if __name__ == "__main__":
    unittest.main()
