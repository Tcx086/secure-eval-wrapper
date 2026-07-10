"""Fixture-default, socket-free simulated FIX lifecycle demo."""
from __future__ import annotations
import argparse,json,os
from datetime import datetime,timedelta,timezone
from decimal import Decimal
from uuid import UUID
from secure_eval_wrapper.alpha.identity import SeriesIdentity
from secure_eval_wrapper.data_collection.models import InstrumentType
from secure_eval_wrapper.execution.brokers.simulated import SimulatedBroker
from secure_eval_wrapper.execution.fees import ZeroFeeModel
from secure_eval_wrapper.execution.models import AccountingMode,BrokerConfiguration
from secure_eval_wrapper.execution.slippage import ZeroSlippage
from secure_eval_wrapper.fix.gateway import GatewaySeries,SimulatedFixGateway
from secure_eval_wrapper.fix.messages import heartbeat,logon,new_order_single,order_cancel_request,test_request
from secure_eval_wrapper.fix.models import FixOrderType,FixSessionConfiguration,FixSide,FixTimeInForce
from secure_eval_wrapper.fix.session import SimulatedFixSession

def run_demo():
 t=datetime(2026,1,1,tzinfo=timezone.utc); session=SimulatedFixSession(FixSessionConfiguration("PUBLIC_CLIENT","SIMULATED_VENUE",heartbeat_interval_seconds=Decimal("5"),test_request_grace_seconds=Decimal("2"),disconnect_timeout_seconds=Decimal("4"))); session.connect(t); session.receive(logon(1,"SIMULATED_VENUE","PUBLIC_CLIENT",t),t); session.receive(heartbeat(2,"SIMULATED_VENUE","PUBLIC_CLIENT",t+timedelta(seconds=1)),t+timedelta(seconds=1))
 identity=SeriesIdentity("synthetic","simulated","BTCUSDT","BTC/USDT",InstrumentType.SPOT,"1m"); broker=SimulatedBroker(BrokerConfiguration(),fee_model=ZeroFeeModel(),slippage_model=ZeroSlippage()); gateway=SimulatedFixGateway(session=session,broker=broker,run_id=UUID("00000000-0000-5000-8000-000000000001"),series_by_symbol={"BTC/USDT":GatewaySeries(identity,AccountingMode.SPOT,reference_price=Decimal("100"))},implementation_code_sha256="a"*64,repository_commit_sha="public-demo",data_sha256="b"*64)
 ack=gateway.handle(new_order_single(3,"SIMULATED_VENUE","PUBLIC_CLIENT",t+timedelta(seconds=2),cl_ord_id="DEMO-1",symbol="BTC/USDT",side=FixSide.BUY,quantity=Decimal("1"),order_type=FixOrderType.MARKET),t+timedelta(seconds=2)); fills=gateway.process_bar_open(symbol="BTC/USDT",timestamp_utc=t+timedelta(seconds=3),open_price=Decimal("101")); ack2=gateway.handle(new_order_single(4,"SIMULATED_VENUE","PUBLIC_CLIENT",t+timedelta(seconds=4),cl_ord_id="DEMO-2",symbol="BTC/USDT",side=FixSide.BUY,quantity=Decimal("1"),order_type=FixOrderType.LIMIT,time_in_force=FixTimeInForce.GTC,price=Decimal("90")),t+timedelta(seconds=4)); cancelled=gateway.handle(order_cancel_request(5,"SIMULATED_VENUE","PUBLIC_CLIENT",t+timedelta(seconds=5),cl_ord_id="CXL-2",orig_cl_ord_id="DEMO-2",symbol="BTC/USDT",side=FixSide.BUY),t+timedelta(seconds=5)); responses=session.receive(test_request(6,"SIMULATED_VENUE","PUBLIC_CLIENT",t+timedelta(seconds=6),"PEER-TEST"),t+timedelta(seconds=6)); session.drop(t+timedelta(seconds=7),"configured_demo_drop"); session.reconnect(t+timedelta(seconds=8)); session.receive(logon(7,"SIMULATED_VENUE","PUBLIC_CLIENT",t+timedelta(seconds=8)),t+timedelta(seconds=8)); session.request_logout(t+timedelta(seconds=9));
 return {"simulated":True,"state":session.state.value,"inbound_messages":len(session.inbound_messages),"outbound_messages":len(session.outbound_messages),"session_events":len(session.events),"acknowledgements":len(ack)+len(ack2),"fill_reports":len(fills),"cancel_reports":len(cancelled),"test_request_responses":len(responses),"external_network":False}
def main(argv=None):
 parser=argparse.ArgumentParser(); parser.add_argument("--persist",action="store_true"); args=parser.parse_args(argv)
 if args.persist and os.environ.get("ENABLE_POSTGRES_PERSISTENCE")!="true": parser.error("--persist requires ENABLE_POSTGRES_PERSISTENCE=true")
 print(json.dumps(run_demo(),sort_keys=True,separators=(",",":"))); return 0
if __name__=="__main__": raise SystemExit(main())