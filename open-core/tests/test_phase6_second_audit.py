from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from uuid import UUID

from secure_eval_wrapper.alpha.identity import SeriesIdentity
from secure_eval_wrapper.data_collection.models import InstrumentType
from secure_eval_wrapper.execution.brokers.simulated import SimulatedBroker
from secure_eval_wrapper.execution.fees import ZeroFeeModel
from secure_eval_wrapper.execution.models import AccountingMode, BrokerConfiguration, OrderStatus, RiskStage
from secure_eval_wrapper.execution.slippage import ZeroSlippage
from secure_eval_wrapper.fix.codec import FixCodec
from secure_eval_wrapper.fix.gateway import GatewaySeries, SimulatedFixGateway
from secure_eval_wrapper.fix.messages import heartbeat, logon, new_order_single, order_cancel_request, sequence_reset
from secure_eval_wrapper.fix.models import (
    FixMessage,
    FixMessageType,
    FixOrderType,
    FixSessionConfiguration,
    FixSide,
    FixTimeInForce,
    ReceiveDisposition,
    SessionReceiveResult,
)
from secure_eval_wrapper.fix.session import SimulatedFixSession


T = datetime(2026, 1, 1, tzinfo=timezone.utc)


def build_gateway(
    inventory: Decimal,
    *,
    accounting_mode: AccountingMode = AccountingMode.SPOT,
    current_position_callback=None,
    fill_application_callback=None,
    accounting_snapshot_callback=None,
    accounting_restore_callback=None,
):
    session = SimulatedFixSession(FixSessionConfiguration("AUDIT_CLIENT", "AUDIT_SERVER"))
    session.connect(T)
    session.receive(logon(1, "AUDIT_SERVER", "AUDIT_CLIENT", T), T)
    instrument_type = (
        InstrumentType.SPOT
        if accounting_mode is AccountingMode.SPOT
        else InstrumentType.PERPETUAL
    )
    identity = SeriesIdentity(
        "synthetic",
        "simulated",
        "BTCUSDT",
        "BTC/USDT",
        instrument_type,
        "1m",
        settlement_asset="USDT",
    )
    broker = SimulatedBroker(
        BrokerConfiguration(),
        fee_model=ZeroFeeModel(),
        slippage_model=ZeroSlippage(),
    )
    gateway = SimulatedFixGateway(
        session=session,
        broker=broker,
        run_id=UUID("00000000-0000-5000-8000-000000000006"),
        series_by_symbol={
            "BTC/USDT": GatewaySeries(
                identity,
                accounting_mode,
                current_quantity=inventory,
                reference_price=Decimal("100"),
            )
        },
        implementation_code_sha256="a" * 64,
        repository_commit_sha="second-audit",
        data_sha256="b" * 64,
        current_position_callback=current_position_callback,
        fill_application_callback=fill_application_callback,
        accounting_snapshot_callback=accounting_snapshot_callback,
        accounting_restore_callback=accounting_restore_callback,
    )
    return session, broker, gateway


def sell(seq, cl_ord_id, quantity, *, order_type=FixOrderType.MARKET, tif=FixTimeInForce.GTC, price=None):
    return new_order_single(
        seq,
        "AUDIT_SERVER",
        "AUDIT_CLIENT",
        T + timedelta(seconds=seq),
        cl_ord_id=cl_ord_id,
        symbol="BTC/USDT",
        side=FixSide.SELL,
        quantity=Decimal(quantity),
        order_type=order_type,
        time_in_force=tif,
        price=price,
    )


class SpotReservationTests(unittest.TestCase):
    def test_inventory_one_two_sells_of_one_at_most_one_fills(self):
        _, broker, gateway = build_gateway(Decimal("1"))
        gateway.handle(sell(2, "S1", "1"), T + timedelta(seconds=2))
        rejected = gateway.handle(sell(3, "S2", "1"), T + timedelta(seconds=3))[-1]
        self.assertEqual(rejected.fields[150], "8")
        gateway.process_bar_open(
            symbol="BTC/USDT",
            timestamp_utc=T + timedelta(seconds=4),
            open_price=Decimal("100"),
        )
        self.assertEqual(len(gateway.fills), 1)
        self.assertEqual(gateway.current_quantity("BTC/USDT", T), Decimal("0"))
        self.assertEqual(len(broker.active_orders()), 0)

    def test_inventory_two_two_sells_fill_on_same_bar(self):
        _, _, gateway = build_gateway(Decimal("2"))
        gateway.handle(sell(2, "S1", "1"), T + timedelta(seconds=2))
        gateway.handle(sell(3, "S2", "1"), T + timedelta(seconds=3))
        reports = gateway.process_bar_open(
            symbol="BTC/USDT",
            timestamp_utc=T + timedelta(seconds=4),
            open_price=Decimal("100"),
        )
        self.assertEqual(len(reports), 2)
        self.assertEqual(len(gateway.fills), 2)
        self.assertEqual(gateway.current_quantity("BTC/USDT", T), Decimal("0"))

    def test_fractional_reservations_are_exact(self):
        _, _, gateway = build_gateway(Decimal("1"))
        gateway.handle(sell(2, "S1", "0.4"), T + timedelta(seconds=2))
        gateway.handle(sell(3, "S2", "0.6"), T + timedelta(seconds=3))
        gateway.process_bar_open(
            symbol="BTC/USDT",
            timestamp_utc=T + timedelta(seconds=4),
            open_price=Decimal("100"),
        )
        self.assertEqual(len(gateway.fills), 2)
        self.assertEqual(gateway.current_quantity("BTC/USDT", T), Decimal("0"))

    def test_overlapping_fractional_reservation_blocks_second(self):
        _, broker, gateway = build_gateway(Decimal("1"))
        gateway.handle(sell(2, "S1", "0.6"), T + timedelta(seconds=2))
        report = gateway.handle(sell(3, "S2", "0.6"), T + timedelta(seconds=3))[-1]
        self.assertEqual(report.fields[150], "8")
        self.assertEqual(len(broker.active_orders()), 1)
        self.assertEqual(gateway.available_inventory("BTC/USDT", T), Decimal("0.4"))

    def test_cancellation_releases_reservation(self):
        _, broker, gateway = build_gateway(Decimal("1"))
        gateway.handle(
            sell(2, "S1", "0.6", order_type=FixOrderType.LIMIT, price=Decimal("200")),
            T + timedelta(seconds=2),
        )
        cancel = order_cancel_request(
            3,
            "AUDIT_SERVER",
            "AUDIT_CLIENT",
            T + timedelta(seconds=3),
            cl_ord_id="CXL-S1",
            orig_cl_ord_id="S1",
            symbol="BTC/USDT",
            side=FixSide.SELL,
            quantity=Decimal("0.6"),
        )
        gateway.handle(cancel, T + timedelta(seconds=3))
        gateway.handle(sell(4, "S2", "1"), T + timedelta(seconds=4))
        self.assertEqual(len(broker.active_orders()), 1)
        self.assertEqual(gateway.available_inventory("BTC/USDT", T), Decimal("0"))

    def test_ioc_expiry_releases_reservation(self):
        _, broker, gateway = build_gateway(Decimal("1"))
        gateway.handle(
            sell(
                2,
                "S1",
                "0.6",
                order_type=FixOrderType.LIMIT,
                tif=FixTimeInForce.IOC,
                price=Decimal("200"),
            ),
            T + timedelta(seconds=2),
        )
        gateway.process_bar_open(
            symbol="BTC/USDT",
            timestamp_utc=T + timedelta(seconds=3),
            open_price=Decimal("100"),
        )
        self.assertEqual(gateway.orders[0].status, OrderStatus.EXPIRED)
        gateway.handle(sell(3, "S2", "1"), T + timedelta(seconds=3))
        self.assertEqual(len(broker.active_orders()), 1)

    def test_prefill_rechecks_inventory_and_creates_no_fill(self):
        _, broker, gateway = build_gateway(Decimal("1"))
        gateway.handle(sell(2, "S1", "1"), T + timedelta(seconds=2))
        gateway._quantities["BTC/USDT"] = Decimal("0.5")
        market_event_time = T + timedelta(seconds=3)
        reports = gateway.process_bar_open(
            symbol="BTC/USDT",
            timestamp_utc=market_event_time,
            open_price=Decimal("70"),
        )
        prefill = next(
            decision
            for decision in gateway.risk_decisions
            if decision.stage is RiskStage.PRE_FILL
        )
        self.assertEqual(reports[-1].fields[150], "8")
        self.assertEqual(prefill.decision_timestamp_utc, market_event_time)
        self.assertEqual(len(gateway.fills), 0)
        self.assertEqual(gateway.current_quantity("BTC/USDT", T), Decimal("0.5"))
        self.assertEqual(len(broker.active_orders()), 0)

    def test_accounting_exception_restores_authoritative_position_and_order(self):
        accounting = {"quantity": Decimal("1")}
        snapshots = []
        restores = []

        def current_position(_series, _at):
            return accounting["quantity"]

        def snapshot():
            value = accounting["quantity"]
            snapshots.append(value)
            return value

        def restore(value):
            restores.append(value)
            accounting["quantity"] = value

        def mutate_then_fail(fill):
            accounting["quantity"] -= fill.quantity
            raise RuntimeError("injected accounting failure")

        _, broker, gateway = build_gateway(
            Decimal("1"),
            current_position_callback=current_position,
            fill_application_callback=mutate_then_fail,
            accounting_snapshot_callback=snapshot,
            accounting_restore_callback=restore,
        )
        gateway.handle(sell(2, "S1", "1"), T + timedelta(seconds=2))
        active_before = broker.active_orders()
        with self.assertRaises(RuntimeError):
            gateway.process_bar_open(
                symbol="BTC/USDT",
                timestamp_utc=T + timedelta(seconds=3),
                open_price=Decimal("100"),
            )
        self.assertEqual(snapshots, [Decimal("1")])
        self.assertEqual(restores, [Decimal("1")])
        self.assertEqual(accounting["quantity"], Decimal("1"))
        self.assertEqual(len(gateway.fills), 0)
        self.assertEqual(gateway.current_quantity("BTC/USDT", T), Decimal("1"))
        self.assertEqual(broker.active_orders(), active_before)

    def test_external_fill_callback_requires_snapshot_and_restore(self):
        with self.assertRaisesRegex(
            ValueError,
            "external fill accounting requires snapshot and restore callbacks",
        ):
            build_gateway(
                Decimal("1"),
                fill_application_callback=lambda _fill: None,
            )

    def test_duplicate_clord_replays_rejected_lifecycle(self):
        _, broker, gateway = build_gateway(Decimal("1"))
        original = sell(2, "S1", "2")
        first = gateway.handle(original, T + timedelta(seconds=2))[-1]
        duplicate = FixMessage(
            original.msg_type,
            3,
            original.sender_comp_id,
            original.target_comp_id,
            T + timedelta(seconds=3),
            original.fields,
        )
        replay = gateway.handle(duplicate, T + timedelta(seconds=3))[-1]
        self.assertEqual(first.fields[150], "8")
        self.assertEqual(replay.fields[150], "8")
        self.assertEqual(replay.fields[39], "8")
        self.assertEqual(len(broker.active_orders()), 0)

    def test_duplicate_clord_replays_filled_lifecycle(self):
        _, _, gateway = build_gateway(Decimal("1"))
        original = sell(2, "S1", "1")
        gateway.handle(original, T + timedelta(seconds=2))
        gateway.process_bar_open(
            symbol="BTC/USDT",
            timestamp_utc=T + timedelta(seconds=3),
            open_price=Decimal("100"),
        )
        duplicate = FixMessage(
            original.msg_type,
            3,
            original.sender_comp_id,
            original.target_comp_id,
            T + timedelta(seconds=4),
            original.fields,
        )
        replay = gateway.handle(duplicate, T + timedelta(seconds=4))[-1]
        self.assertEqual(replay.fields[150], "F")
        self.assertEqual(replay.fields[39], "2")
        self.assertEqual(replay.fields[14], "1")
        self.assertEqual(replay.fields[151], "0")
        self.assertEqual(gateway.current_quantity("BTC/USDT", T), Decimal("0"))
    def test_perpetual_signed_positions_do_not_use_spot_reservations(self):
        _, _, gateway = build_gateway(
            Decimal("0"),
            accounting_mode=AccountingMode.LINEAR_PERPETUAL,
        )
        gateway.handle(sell(2, "S1", "1"), T + timedelta(seconds=2))
        gateway.process_bar_open(
            symbol="BTC/USDT",
            timestamp_utc=T + timedelta(seconds=3),
            open_price=Decimal("100"),
        )
        self.assertEqual(gateway.current_quantity("BTC/USDT", T), Decimal("-1"))
        self.assertEqual(len(gateway.fills), 1)


class TypedRejectionAndOccurrenceTests(unittest.TestCase):
    def test_direct_and_raw_wrong_compids_are_typed_and_audited(self):
        session, broker, gateway = build_gateway(Decimal("1"))
        wrong = sell(2, "S1", "1")
        wrong = FixMessage(
            wrong.msg_type,
            wrong.msg_seq_num,
            "WRONG",
            wrong.target_comp_id,
            wrong.sending_time_utc,
            wrong.fields,
        )
        before = session.next_inbound_seq_num
        direct_result = gateway.handle(wrong, T + timedelta(seconds=2))
        self.assertIsInstance(direct_result, SessionReceiveResult)
        self.assertEqual(direct_result.disposition, ReceiveDisposition.REJECTED)
        self.assertEqual(direct_result.responses, ())
        direct = direct_result.rejected_observation
        self.assertIsNotNone(direct)
        self.assertIs(direct, session.rejected_observations[-1])
        self.assertEqual(direct.rejection_code, "wrong_comp_ids")
        self.assertEqual(session.next_inbound_seq_num, before)
        raw_result = gateway.handle_raw(FixCodec().encode(wrong), T + timedelta(seconds=3))
        self.assertEqual(raw_result.disposition, ReceiveDisposition.REJECTED)
        self.assertEqual(raw_result.rejected_observation.observation_id, direct.observation_id)
        self.assertEqual(session.next_inbound_seq_num, before)
        self.assertEqual(len(broker.active_orders()), 0)
        self.assertEqual(len(session.rejected_occurrences), 2)
        self.assertNotEqual(
            session.rejected_occurrences[0].occurrence_id,
            session.rejected_occurrences[1].occurrence_id,
        )

    def test_invalid_typed_content_returns_rejected_disposition(self):
        session, _, _ = build_gateway(Decimal("1"))
        bad = FixMessage(
            FixMessageType.NEW_ORDER_SINGLE,
            2,
            "AUDIT_SERVER",
            "AUDIT_CLIENT",
            T + timedelta(seconds=2),
            {11: "MISSING-REQUIRED-FIELDS"},
        )
        result = session.receive(bad, T + timedelta(seconds=2))
        self.assertEqual(result.disposition, ReceiveDisposition.REJECTED)
        self.assertEqual(result.rejected_observation.rejection_code, "validation_rejected")
        self.assertEqual(result.rejected_observation.msg_seq_num, 2)
        self.assertEqual(result.rejected_observation.msg_type, FixMessageType.NEW_ORDER_SINGLE.value)
        self.assertEqual(result.rejected_observation.sender_comp_id, "AUDIT_SERVER")
        self.assertEqual(result.rejected_observation.target_comp_id, "AUDIT_CLIENT")
        self.assertEqual(result.rejected_observation.parsed_header_fields[34], "2")
        self.assertEqual(result.rejected_observation.parsed_header_fields[35], "D")
        self.assertEqual(result.rejected_observation.parsed_header_fields[49], "AUDIT_SERVER")
        self.assertEqual(result.rejected_observation.parsed_header_fields[56], "AUDIT_CLIENT")
        self.assertEqual(session.next_inbound_seq_num, 2)

    def test_administrative_message_during_logon_pending_is_rejected(self):
        session = SimulatedFixSession(
            FixSessionConfiguration("AUDIT_CLIENT", "AUDIT_SERVER")
        )
        session.connect(T)
        inbound_before = session.next_inbound_seq_num
        outbound_before = session.next_outbound_seq_num
        result = session.receive(
            heartbeat(1, "AUDIT_SERVER", "AUDIT_CLIENT", T + timedelta(seconds=1)),
            T + timedelta(seconds=1),
        )
        self.assertEqual(result.disposition, ReceiveDisposition.REJECTED)
        self.assertEqual(
            result.rejected_observation.rejection_code,
            "unsupported_session_state",
        )
        self.assertEqual(session.next_inbound_seq_num, inbound_before)
        self.assertEqual(session.next_outbound_seq_num, outbound_before)

    def assert_rejected_replay_is_idempotent(self, message, at, rejection_code):
        session, broker, gateway = build_gateway(Decimal("1"))
        before = (
            len(session.rejected_observations),
            len(session.rejected_occurrences),
            len(session.events),
            len(session.outbound_messages),
            session.next_outbound_seq_num,
        )
        first = gateway.handle(message, at)
        after_first = (
            len(session.rejected_observations),
            len(session.rejected_occurrences),
            len(session.events),
            len(session.outbound_messages),
            session.next_outbound_seq_num,
        )
        event_tail_id = session.events[-1].event_id
        second = gateway.handle(message, at)
        after_second = (
            len(session.rejected_observations),
            len(session.rejected_occurrences),
            len(session.events),
            len(session.outbound_messages),
            session.next_outbound_seq_num,
        )

        for result in (first, second):
            self.assertIsInstance(result, SessionReceiveResult)
            self.assertEqual(result.disposition, ReceiveDisposition.REJECTED)
            self.assertEqual(result.rejected_observation.rejection_code, rejection_code)
        self.assertEqual(
            first.rejected_observation.observation_id,
            second.rejected_observation.observation_id,
        )
        self.assertEqual(len(first.responses), 1)
        self.assertEqual(first.responses[0].msg_type, FixMessageType.REJECT)
        self.assertEqual(after_first[0], before[0] + 1)
        self.assertEqual(after_first[1], before[1] + 1)
        self.assertGreater(after_first[2], before[2])
        self.assertEqual(after_first[3], before[3] + 1)
        self.assertEqual(after_first[4], before[4] + 1)
        self.assertEqual(after_second, after_first)
        self.assertEqual(session.events[-1].event_id, event_tail_id)
        self.assertEqual(session.next_inbound_seq_num, 2)
        self.assertEqual(broker.active_orders(), ())
        self.assertEqual(gateway.intents, ())
        self.assertEqual(gateway.risk_decisions, ())
        self.assertEqual(gateway.orders, ())
        self.assertEqual(gateway.fills, ())
        self.assertEqual(gateway.links, ())
        self.assertEqual(gateway.reports, ())

    def test_same_low_sequence_rejection_delivery_is_idempotent(self):
        message = heartbeat(1, "AUDIT_SERVER", "AUDIT_CLIENT", T)
        self.assert_rejected_replay_is_idempotent(
            message,
            T + timedelta(seconds=2),
            "inbound_sequence_too_low",
        )

    def test_same_nonforward_sequence_reset_delivery_is_idempotent(self):
        message = sequence_reset(
            2,
            "AUDIT_SERVER",
            "AUDIT_CLIENT",
            T + timedelta(seconds=2),
            new_seq_no=2,
        )
        self.assert_rejected_replay_is_idempotent(
            message,
            T + timedelta(seconds=2),
            "sequence_reset_not_forward",
        )

    def test_same_logical_rejection_occurrence_is_idempotent(self):
        session, _, gateway = build_gateway(Decimal("1"))
        raw = b"not-fix"
        observations_before = len(session.rejected_observations)
        occurrences_before = len(session.rejected_occurrences)
        events_before = len(session.events)
        first = gateway.handle_raw(raw, T + timedelta(seconds=2))
        event_tail = session.events[-1]
        second = gateway.handle_raw(raw, T + timedelta(seconds=2))
        self.assertEqual(
            first.rejected_observation.observation_id,
            second.rejected_observation.observation_id,
        )
        self.assertEqual(len(session.rejected_observations), observations_before + 1)
        self.assertEqual(len(session.rejected_occurrences), occurrences_before + 1)
        self.assertEqual(len(session.events), events_before + 1)
        self.assertEqual(session.events[-1].event_id, event_tail.event_id)


if __name__ == "__main__":
    unittest.main()