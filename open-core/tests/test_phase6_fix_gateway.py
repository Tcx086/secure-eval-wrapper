from __future__ import annotations
import unittest
from datetime import datetime,timezone,timedelta
from decimal import Decimal
from uuid import UUID
from secure_eval_wrapper.alpha.identity import SeriesIdentity
from secure_eval_wrapper.data_collection.models import InstrumentType
from secure_eval_wrapper.execution.brokers.simulated import SimulatedBroker
from secure_eval_wrapper.execution.fees import ZeroFeeModel
from secure_eval_wrapper.execution.models import AccountingMode,BrokerConfiguration
from secure_eval_wrapper.execution.slippage import ZeroSlippage
from secure_eval_wrapper.fix.gateway import GatewaySeries,SimulatedFixGateway
from secure_eval_wrapper.fix.messages import logon,new_order_single,order_cancel_request
from secure_eval_wrapper.fix.models import FixMessage,FixMessageType,FixOrderType,FixSessionConfiguration,FixSide,FixTimeInForce,ReceiveDisposition
from secure_eval_wrapper.fix.session import SimulatedFixSession
T=datetime(2026,1,1,tzinfo=timezone.utc)
def fixture():
 session=SimulatedFixSession(FixSessionConfiguration("CLIENT","SERVER")); session.connect(T); session.receive(logon(1,"SERVER","CLIENT",T),T); identity=SeriesIdentity("fixture","fixture","BTCUSDT","BTC/USDT",InstrumentType.SPOT,"1m"); broker=SimulatedBroker(BrokerConfiguration(),fee_model=ZeroFeeModel(),slippage_model=ZeroSlippage()); gateway=SimulatedFixGateway(session=session,broker=broker,run_id=UUID("00000000-0000-5000-8000-000000000001"),series_by_symbol={"BTC/USDT":GatewaySeries(identity,AccountingMode.SPOT,reference_price=Decimal("100"))},implementation_code_sha256="a"*64,repository_commit_sha="commit",data_sha256="b"*64); return session,broker,gateway
def order(seq=2,clid="C1",side=FixSide.BUY,kind=FixOrderType.MARKET,price=None): return new_order_single(seq,"SERVER","CLIENT",T+timedelta(seconds=seq),cl_ord_id=clid,symbol="BTC/USDT",side=side,quantity=Decimal("1"),order_type=kind,time_in_force=FixTimeInForce.GTC,price=price)
class GatewayTests(unittest.TestCase):
 def test_acknowledgement_is_not_fill(self):
  s,b,g=fixture(); reports=g.handle(order(),T+timedelta(seconds=2)); self.assertEqual(reports[-1].fields[150],"0"); self.assertEqual(len(b.active_orders()),1); self.assertEqual(len(g.links),1); self.assertIsNone(g.links[-1].fill_id)
 def test_fill_requires_explicit_market_event(self):
  s,b,g=fixture(); g.handle(order(),T+timedelta(seconds=2)); self.assertEqual(len(g.process_bar_open(symbol="BTC/USDT",timestamp_utc=T+timedelta(seconds=3),open_price=Decimal("101"))),1); self.assertEqual(g.links[-1].fill_id is not None,True)
 def test_spot_short_is_risk_rejected(self):
  s,b,g=fixture(); report=g.handle(order(side=FixSide.SELL),T+timedelta(seconds=2))[-1]; self.assertEqual(report.fields[150],"8"); self.assertEqual(len(b.active_orders()),0)
 def test_cancel_and_duplicate_cancel_reject(self):
  s,b,g=fixture(); g.handle(order(kind=FixOrderType.LIMIT,price=Decimal("90")),T+timedelta(seconds=2)); cancel=order_cancel_request(3,"SERVER","CLIENT",T+timedelta(seconds=3),cl_ord_id="X",orig_cl_ord_id="C1",symbol="BTC/USDT",side=FixSide.BUY); self.assertEqual(g.handle(cancel,T+timedelta(seconds=3))[-1].msg_type,FixMessageType.EXECUTION_REPORT); cancel2=order_cancel_request(4,"SERVER","CLIENT",T+timedelta(seconds=4),cl_ord_id="X2",orig_cl_ord_id="C1",symbol="BTC/USDT",side=FixSide.BUY); self.assertEqual(g.handle(cancel2,T+timedelta(seconds=4))[-1].msg_type,FixMessageType.ORDER_CANCEL_REJECT)
 def test_clord_content_conflict(self):
  s,b,g=fixture(); g.handle(order(),T+timedelta(seconds=2)); changed=order(seq=3,kind=FixOrderType.LIMIT,price=Decimal("90")); self.assertEqual(g.handle(changed,T+timedelta(seconds=3))[-1].msg_type,FixMessageType.BUSINESS_MESSAGE_REJECT); self.assertEqual(len(b.active_orders()),1)
 def test_wrong_session_is_rejected_before_order(self):
  s,b,g=fixture(); wrong=FixMessage(FixMessageType.NEW_ORDER_SINGLE,2,"OTHER","CLIENT",T,{11:"C",55:"BTC/USDT",54:"1",60:"20260101-00:00:00",38:"1",40:"1",59:"1"}); self.assertEqual(g.handle(wrong,T),()); self.assertEqual(s.rejected_observations[-1].rejection_code,"wrong_comp_ids"); self.assertEqual(s.next_inbound_seq_num,2); self.assertEqual(len(b.active_orders()),0)
if __name__=="__main__": unittest.main()