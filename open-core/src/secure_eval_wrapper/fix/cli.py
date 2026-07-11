"""Fixture-default, socket-free simulated FIX lifecycle demo."""
from __future__ import annotations

import argparse
import json
import os
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from uuid import UUID

from secure_eval_wrapper.alpha.identity import SeriesIdentity
from secure_eval_wrapper.data_collection.models import InstrumentType
from secure_eval_wrapper.execution.brokers.simulated import SimulatedBroker
from secure_eval_wrapper.execution.fees import ZeroFeeModel
from secure_eval_wrapper.execution.models import AccountingMode, BrokerConfiguration
from secure_eval_wrapper.execution.slippage import ZeroSlippage
from secure_eval_wrapper.fix.gateway import GatewaySeries, SimulatedFixGateway
from secure_eval_wrapper.fix.messages import heartbeat, logon, new_order_single, order_cancel_request, test_request
from secure_eval_wrapper.fix.models import FixOrderType, FixSessionConfiguration, FixSide, FixTimeInForce
from secure_eval_wrapper.fix.session import SimulatedFixSession


def _run_demo_context():
    at = datetime(2026, 1, 1, tzinfo=timezone.utc)
    session = SimulatedFixSession(FixSessionConfiguration(
        "PUBLIC_CLIENT", "SIMULATED_VENUE", heartbeat_interval_seconds=Decimal("5"),
        test_request_grace_seconds=Decimal("2"), disconnect_timeout_seconds=Decimal("4"),
    ))
    session.connect(at)
    session.receive(logon(1, "SIMULATED_VENUE", "PUBLIC_CLIENT", at), at)
    session.receive(heartbeat(2, "SIMULATED_VENUE", "PUBLIC_CLIENT", at + timedelta(seconds=1)), at + timedelta(seconds=1))
    identity = SeriesIdentity("synthetic", "simulated", "BTCUSDT", "BTC/USDT", InstrumentType.SPOT, "1m")
    broker = SimulatedBroker(BrokerConfiguration(), fee_model=ZeroFeeModel(), slippage_model=ZeroSlippage())
    gateway = SimulatedFixGateway(
        session=session, broker=broker, run_id=UUID("00000000-0000-5000-8000-000000000001"),
        series_by_symbol={"BTC/USDT": GatewaySeries(identity, AccountingMode.SPOT, reference_price=Decimal("100"))},
        implementation_code_sha256="a" * 64, repository_commit_sha="public-demo", data_sha256="b" * 64,
    )
    buy_ack = gateway.handle(new_order_single(3, "SIMULATED_VENUE", "PUBLIC_CLIENT", at + timedelta(seconds=2),
                                               cl_ord_id="DEMO-BUY", symbol="BTC/USDT", side=FixSide.BUY,
                                               quantity=Decimal("1"), order_type=FixOrderType.MARKET), at + timedelta(seconds=2))
    buy_fill = gateway.process_bar_open(symbol="BTC/USDT", timestamp_utc=at + timedelta(seconds=3), open_price=Decimal("101"))
    sell_ack = gateway.handle(new_order_single(4, "SIMULATED_VENUE", "PUBLIC_CLIENT", at + timedelta(seconds=4),
                                                cl_ord_id="DEMO-SELL", symbol="BTC/USDT", side=FixSide.SELL,
                                                quantity=Decimal("1"), order_type=FixOrderType.MARKET), at + timedelta(seconds=4))
    sell_fill = gateway.process_bar_open(symbol="BTC/USDT", timestamp_utc=at + timedelta(seconds=5), open_price=Decimal("102"))
    limit_ack = gateway.handle(new_order_single(5, "SIMULATED_VENUE", "PUBLIC_CLIENT", at + timedelta(seconds=6),
                                                 cl_ord_id="DEMO-LIMIT", symbol="BTC/USDT", side=FixSide.BUY,
                                                 quantity=Decimal("1"), order_type=FixOrderType.LIMIT,
                                                 time_in_force=FixTimeInForce.GTC, price=Decimal("90")), at + timedelta(seconds=6))
    cancelled = gateway.handle(order_cancel_request(6, "SIMULATED_VENUE", "PUBLIC_CLIENT", at + timedelta(seconds=7),
                                                    cl_ord_id="CXL-LIMIT", orig_cl_ord_id="DEMO-LIMIT",
                                                    symbol="BTC/USDT", side=FixSide.BUY), at + timedelta(seconds=7))
    responses = session.receive(test_request(7, "SIMULATED_VENUE", "PUBLIC_CLIENT", at + timedelta(seconds=8), "PEER-TEST"), at + timedelta(seconds=8))
    session.drop(at + timedelta(seconds=9), "configured_demo_drop")
    session.reconnect(at + timedelta(seconds=10))
    session.receive(logon(8, "SIMULATED_VENUE", "PUBLIC_CLIENT", at + timedelta(seconds=10)), at + timedelta(seconds=10))
    session.request_logout(at + timedelta(seconds=11))
    summary = {
        "simulated": True, "state": session.state.value,
        "inbound_messages": len(session.inbound_messages), "outbound_messages": len(session.outbound_messages),
        "session_events": len(session.events), "acknowledgements": len(buy_ack) + len(sell_ack) + len(limit_ack),
        "fill_reports": len(buy_fill) + len(sell_fill), "cancel_reports": len(cancelled),
        "test_request_responses": len(responses), "resulting_quantity": str(gateway.current_quantity("BTC/USDT", at + timedelta(seconds=11))),
        "external_network": False,
    }
    return summary, session, gateway


def run_demo():
    return _run_demo_context()[0]


def _connect_postgres():
    """Import the optional driver only after both persistence gates pass."""
    import psycopg
    from secure_eval_wrapper.storage.postgres.connection import build_connection_kwargs
    return psycopg.connect(**build_connection_kwargs())


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--persist", action="store_true")
    args = parser.parse_args(argv)
    if args.persist and os.environ.get("ENABLE_POSTGRES_PERSISTENCE") != "true":
        parser.error("--persist requires ENABLE_POSTGRES_PERSISTENCE=true")
    summary, session, gateway = _run_demo_context()
    summary["persistence_status"] = "disabled"
    if args.persist:
        connection = _connect_postgres()
        try:
            from secure_eval_wrapper.monitoring.persistence import persist_fix_transition
            from secure_eval_wrapper.storage.postgres.phase6_repositories import PostgresPhase6Repository
            persist_fix_transition(
                PostgresPhase6Repository(connection), session=session,
                at_utc=datetime(2026, 1, 1, 0, 0, 11, tzinfo=timezone.utc),
                inbound_messages=tuple(session.inbound_messages), outbound_messages=tuple(session.outbound_messages),
                rejected_observations=tuple(session.rejected_observations),
                rejected_occurrences=tuple(session.rejected_occurrences),
                session_events=tuple(session.events),
                order_links=gateway.links,
                order_intents=gateway.intents,
                risk_decisions=gateway.risk_decisions,
                orders=gateway.orders,
                fills=gateway.fills,
            )
            summary["persisted_order_intents"] = len(gateway.intents)
            summary["persisted_risk_decisions"] = len(gateway.risk_decisions)
            summary["persisted_orders"] = len(gateway.orders)
            summary["persisted_fills"] = len(gateway.fills)
            summary["persisted_fix_order_links"] = len(gateway.links)
            summary["persisted_execution_reports"] = len(gateway.reports)
            summary["persistence_status"] = "postgresql"
        finally:
            connection.close()
    print(json.dumps(summary, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
