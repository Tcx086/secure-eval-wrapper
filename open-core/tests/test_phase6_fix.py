from __future__ import annotations
import unittest
from datetime import datetime,timedelta,timezone
from decimal import Decimal
from secure_eval_wrapper.fix.codec import FixCodec,FixValidationError
from secure_eval_wrapper.fix.messages import *
from secure_eval_wrapper.fix.models import *
from secure_eval_wrapper.fix.session import SimulatedFixSession
T=datetime(2026,1,1,tzinfo=timezone.utc)
class CodecTests(unittest.TestCase):
 def setUp(self): self.codec=FixCodec()
 def roundtrip(self,msg): return self.codec.decode(self.codec.encode(msg))
 def test_exact_logon(self): self.assertEqual(self.codec.encode(logon(1,"A","B",T)),b"8=FIX.4.4\x019=53\x0135=A\x0134=1\x0149=A\x0156=B\x0152=20260101-00:00:00\x0198=0\x01108=30\x0110=159\x01")
 def test_supported_messages_roundtrip(self):
  values=(heartbeat(1,"A","B",T),test_request(1,"A","B",T,"X"),logout(1,"A","B",T),new_order_single(1,"A","B",T,cl_ord_id="C",symbol="BTC",side=FixSide.BUY,quantity=Decimal("1"),order_type=FixOrderType.MARKET),order_cancel_request(1,"A","B",T,cl_ord_id="X",orig_cl_ord_id="C",symbol="BTC",side=FixSide.BUY),execution_report(1,"A","B",T,order_id="O",exec_id="E",cl_ord_id="C",symbol="BTC",side=FixSide.BUY,ord_status=FixOrdStatus.NEW,exec_type=FixExecType.NEW,leaves_qty=Decimal("1"),cum_qty=Decimal("0"),avg_px=Decimal("0")),order_cancel_reject(1,"A","B",T,order_id="O",cl_ord_id="X",orig_cl_ord_id="C",ord_status=FixOrdStatus.FILLED,text="filled"))
  self.assertEqual([self.roundtrip(v).msg_type for v in values],[v.msg_type for v in values])
 def corrupt(self,raw,old,new): return raw.replace(old,new,1)
 def test_bad_begin(self):
  raw=self.codec.encode(logon(1,"A","B",T)); self.assertRaises(FixValidationError,self.codec.decode,self.corrupt(raw,b"FIX.4.4",b"FIX.4.2"))
 def test_bad_length(self):
  raw=self.codec.encode(logon(1,"A","B",T)); self.assertRaises(FixValidationError,self.codec.decode,self.corrupt(raw,b"9=53",b"9=52"))
 def test_bad_checksum(self):
  raw=self.codec.encode(logon(1,"A","B",T)); self.assertRaises(FixValidationError,self.codec.decode,raw[:-5]+b"000\x01")
 def test_missing_msgtype(self):
  raw=self.codec.encode(logon(1,"A","B",T)); item=raw.replace(b"35=A\x01",b""); self.assertRaises(FixValidationError,self.codec.decode,item)
 def test_duplicate_tag(self):
  raw=self.codec.encode(logon(1,"A","B",T)); item=raw.replace(b"35=A\x01",b"35=A\x0135=A\x01"); self.assertRaises(FixValidationError,self.codec.decode,item)
 def test_invalid_sequence(self): self.assertRaises(ValueError,FixMessage,FixMessageType.HEARTBEAT,0,"A","B",T)
 def test_invalid_decimal(self):
  msg=FixMessage(FixMessageType.NEW_ORDER_SINGLE,1,"A","B",T,{11:"C",55:"BTC",54:"1",60:"20260101-00:00:00",38:"bad",40:"1",59:"1"}); self.assertRaises(FixValidationError,self.codec.encode,msg)
 def test_unsupported_order_type(self):
  msg=FixMessage(FixMessageType.NEW_ORDER_SINGLE,1,"A","B",T,{11:"C",55:"BTC",54:"1",60:"20260101-00:00:00",38:"1",40:"Z",59:"1"}); self.assertRaises(FixValidationError,self.codec.encode,msg)
 def test_unknown_tags_policy(self):
  msg=FixMessage(FixMessageType.HEARTBEAT,1,"A","B",T,extensions={9000:"public"}); raw=FixCodec(preserve_unknown_tags=True).encode(msg); self.assertEqual(FixCodec(preserve_unknown_tags=True).decode(raw).extensions[9000],"public"); self.assertRaises(FixValidationError,FixCodec().decode,raw)
 def test_possdup_requires_orig(self): self.assertRaises(ValueError,FixMessage,FixMessageType.HEARTBEAT,1,"A","B",T,{},True)
class SessionTests(unittest.TestCase):
 def setUp(self): self.s=SimulatedFixSession(FixSessionConfiguration("CLIENT","SERVER",Decimal("5"),Decimal("2"),Decimal("4"))); self.s.connect(T); self.s.receive(logon(1,"SERVER","CLIENT",T),T)
 def test_logon(self): self.assertEqual(self.s.state,FixSessionState.ESTABLISHED)
 def test_duplicate_logon_rejected(self): self.assertEqual(self.s.receive(logon(2,"SERVER","CLIENT",T+timedelta(seconds=1)),T+timedelta(seconds=1))[0].msg_type,FixMessageType.REJECT)
 def test_heartbeat_test_recovery(self):
  request=self.s.tick(T+timedelta(seconds=5))[0]; self.assertEqual(request.msg_type,FixMessageType.TEST_REQUEST); self.s.receive(heartbeat(2,"SERVER","CLIENT",T+timedelta(seconds=6),test_request_id=self.s.pending_test_request_id),T+timedelta(seconds=6)); self.assertEqual(self.s.state,FixSessionState.ESTABLISHED)
 def test_timeout_disconnect(self): self.s.tick(T+timedelta(seconds=5)); self.s.tick(T+timedelta(seconds=9)); self.assertEqual(self.s.state,FixSessionState.DISCONNECTED)
 def test_high_sequence_resend(self): self.assertEqual(self.s.receive(heartbeat(4,"SERVER","CLIENT",T+timedelta(seconds=1)),T+timedelta(seconds=1))[0].msg_type,FixMessageType.RESEND_REQUEST)
 def test_low_sequence_reject(self): self.s.receive(heartbeat(2,"SERVER","CLIENT",T+timedelta(seconds=1)),T+timedelta(seconds=1)); self.assertEqual(self.s.receive(heartbeat(1,"SERVER","CLIENT",T),T+timedelta(seconds=2))[0].msg_type,FixMessageType.REJECT)
 def test_possdup(self):
  original=heartbeat(2,"SERVER","CLIENT",T+timedelta(seconds=1)); self.s.receive(original,T+timedelta(seconds=1)); duplicate=FixMessage(FixMessageType.HEARTBEAT,2,"SERVER","CLIENT",T+timedelta(seconds=2),poss_dup_flag=True,orig_sending_time_utc=original.sending_time_utc); self.assertEqual(self.s.receive(duplicate,T+timedelta(seconds=2)),())
 def test_sequence_reset(self): self.s.receive(sequence_reset(2,"SERVER","CLIENT",T+timedelta(seconds=1),5),T+timedelta(seconds=1)); self.assertEqual(self.s.next_inbound_seq_num,5)
 def test_backwards_sequence_reset(self): self.assertEqual(self.s.receive(sequence_reset(2,"SERVER","CLIENT",T+timedelta(seconds=1),1),T+timedelta(seconds=1))[0].msg_type,FixMessageType.REJECT)
 def test_logout_and_reconnect(self): self.s.receive(logout(2,"SERVER","CLIENT",T+timedelta(seconds=1)),T+timedelta(seconds=1)); self.assertEqual(self.s.state,FixSessionState.TERMINATED)
 def test_invalid_message_does_not_advance(self):
  before=self.s.next_inbound_seq_num; bad=FixMessage(FixMessageType.NEW_ORDER_SINGLE,before,"SERVER","CLIENT",T,{11:"C"}); self.assertRaises(FixValidationError,self.s.receive,bad,T); self.assertEqual(self.s.next_inbound_seq_num,before)
class LatencyFaultTests(unittest.TestCase):
 def test_fixed_latency_breach(self):
  from secure_eval_wrapper.fix.latency import FixedLatencyModel
  sample=FixedLatencyModel({LatencyStage.RISK:10},{LatencyStage.RISK:9}).apply(fix_session_id=FixSessionConfiguration("A","B").fix_session_id,fix_message_id=None,stage=LatencyStage.RISK,start_utc=T); self.assertEqual(sample.duration_microseconds,10); self.assertTrue(sample.breached)
 def test_fault_schedule_replay(self):
  from secure_eval_wrapper.fix.faults import FaultSchedule
  f=ConnectionFault(FixSessionConfiguration("A","B").fix_session_id,ConnectionFaultType.HEARTBEAT_RESPONSE_LOSS,T,"fixture"); s=FaultSchedule((f,)); self.assertEqual(len(s.due(T)),1); s.activate(f,T); self.assertEqual(s.due(T),())
if __name__=="__main__": unittest.main()