"""Deterministic in-process simulated FIX session state machine."""
from __future__ import annotations
from datetime import datetime,timedelta
from decimal import Decimal
from secure_eval_wrapper.data_collection.time_utils import require_utc_datetime
from secure_eval_wrapper.fix.codec import FixCodec,FixValidationError
from secure_eval_wrapper.fix.messages import heartbeat,logon,logout,reject,resend_request,test_request
from secure_eval_wrapper.fix.models import FixMessage,FixMessageType,FixSessionConfiguration,FixSessionEvent,FixSessionEventType,FixSessionState

class FixSessionError(RuntimeError): pass

class SimulatedFixSession:
 def __init__(self,configuration:FixSessionConfiguration,*,codec:FixCodec|None=None):
  self.configuration=configuration; self.codec=codec or FixCodec(preserve_unknown_tags=configuration.preserve_unknown_tags); self.state=FixSessionState.DISCONNECTED; self.next_inbound_seq_num=1; self.next_outbound_seq_num=1; self.last_inbound_at_utc=None; self.last_outbound_at_utc=None; self.pending_test_request_id=None; self.pending_test_sent_at_utc=None; self.events=[]; self.inbound_messages=[]; self.outbound_messages=[]; self._accepted={}
 @property
 def fix_session_id(self): return self.configuration.fix_session_id
 def _event(self,kind,at,prior,new,reason,seq=None,parent=None):
  event=FixSessionEvent(self.fix_session_id,kind,at,prior,new,reason,seq,parent); self.events.append(event); return event
 def _transition(self,new,at,reason,kind=FixSessionEventType.STATE_TRANSITION,parent=None):
  prior=self.state; self.state=FixSessionState(new); return self._event(kind,at,prior,self.state,reason,parent=parent)
 def _emit(self,factory,at,*args,**kwargs):
  seq=self.next_outbound_seq_num; msg=factory(seq,self.configuration.sender_comp_id,self.configuration.target_comp_id,at,*args,**kwargs); self.next_outbound_seq_num+=1; self.last_outbound_at_utc=at; self.outbound_messages.append(msg); return msg
 def connect(self,at:datetime):
  require_utc_datetime(at,field_name="session connect")
  if self.state not in (FixSessionState.DISCONNECTED,FixSessionState.TERMINATED): raise FixSessionError("session is already connected or pending")
  self._transition(FixSessionState.LOGON_PENDING,at,"logon_started"); return self._emit(logon,at,heartbeat_seconds=int(self.configuration.heartbeat_interval_seconds))
 def reconnect(self,at:datetime):
  require_utc_datetime(at,field_name="session reconnect")
  if self.state is not FixSessionState.DISCONNECTED: raise FixSessionError("reconnect requires disconnected state")
  self._event(FixSessionEventType.RECONNECTED,at,self.state,self.state,"deterministic_reconnect"); return self.connect(at)
 def receive_raw(self,raw:bytes,processing_at_utc:datetime):
  require_utc_datetime(processing_at_utc,field_name="FIX processing time")
  try: msg=self.codec.decode(raw)
  except FixValidationError:
   # Invalid bytes do not advance inbound economic/session sequencing.
   raise
  return self.receive(msg,processing_at_utc)
 def receive(self,msg:FixMessage,at:datetime):
  require_utc_datetime(at,field_name="session receive")
  self.codec.encode(msg)  # Validate the complete supported subset before sequence/economic processing.
  if msg.sender_comp_id!=self.configuration.target_comp_id or msg.target_comp_id!=self.configuration.sender_comp_id: raise FixSessionError("FIX CompIDs do not match simulated session")
  expected=self.next_inbound_seq_num
  if msg.msg_seq_num<expected:
   prior_hash=self._accepted.get(msg.msg_seq_num)
   if msg.poss_dup_flag and prior_hash==msg.business_identity_sha256:
    self._event(FixSessionEventType.DUPLICATE_ACCEPTED,at,self.state,self.state,"valid_possdup_replay",msg.msg_seq_num,msg.fix_message_id); return ()
   self._event(FixSessionEventType.MESSAGE_REJECTED,at,self.state,self.state,"inbound_sequence_too_low",msg.msg_seq_num,msg.fix_message_id); return (self._emit(reject,at,ref_seq_num=msg.msg_seq_num,text="MsgSeqNum too low",ref_msg_type=msg.msg_type),)
  if msg.msg_seq_num>expected:
   self._transition(FixSessionState.RECOVERING,at,"inbound_sequence_gap",FixSessionEventType.SEQUENCE_GAP,msg.fix_message_id)
   return (self._emit(resend_request,at,begin_seq_no=expected,end_seq_no=msg.msg_seq_num-1),)
  self.inbound_messages.append(msg); self.last_inbound_at_utc=at; self._accepted[msg.msg_seq_num]=msg.business_identity_sha256
  responses=[]
  if msg.msg_type is FixMessageType.SEQUENCE_RESET:
   new_seq=int(msg.fields[36])
   if new_seq<=expected:
    self._event(FixSessionEventType.MESSAGE_REJECTED,at,self.state,self.state,"sequence_reset_not_forward",msg.msg_seq_num,msg.fix_message_id); return (self._emit(reject,at,ref_seq_num=msg.msg_seq_num,text="SequenceReset cannot decrease expected sequence",ref_msg_type=msg.msg_type),)
   self.next_inbound_seq_num=new_seq; self._transition(FixSessionState.ESTABLISHED,at,"sequence_recovered",parent=msg.fix_message_id)
  else:
   self.next_inbound_seq_num+=1
  if msg.msg_type is FixMessageType.LOGON:
   if self.state not in (FixSessionState.LOGON_PENDING,FixSessionState.DISCONNECTED): responses.append(self._emit(reject,at,ref_seq_num=msg.msg_seq_num,text="Duplicate Logon",ref_msg_type=msg.msg_type))
   else: self._transition(FixSessionState.ESTABLISHED,at,"logon_accepted",parent=msg.fix_message_id)
  elif msg.msg_type is FixMessageType.HEARTBEAT:
   test_id=msg.fields.get(112)
   if self.state is FixSessionState.TEST_REQUEST_PENDING and test_id==self.pending_test_request_id:
    self.pending_test_request_id=None; self.pending_test_sent_at_utc=None; self._transition(FixSessionState.ESTABLISHED,at,"matching_heartbeat_received",FixSessionEventType.HEARTBEAT_RECEIVED,msg.fix_message_id)
   else: self._event(FixSessionEventType.HEARTBEAT_RECEIVED,at,self.state,self.state,"heartbeat_received",msg.msg_seq_num,msg.fix_message_id)
  elif msg.msg_type is FixMessageType.TEST_REQUEST:
   responses.append(self._emit(heartbeat,at,test_request_id=msg.fields[112]))
  elif msg.msg_type is FixMessageType.RESEND_REQUEST:
   self._transition(FixSessionState.RECOVERING,at,"peer_resend_requested",parent=msg.fix_message_id)
  elif msg.msg_type is FixMessageType.LOGOUT:
   if self.state is not FixSessionState.LOGOUT_PENDING: responses.append(self._emit(logout,at,text="Simulated logout acknowledged"))
   self._transition(FixSessionState.TERMINATED,at,"logout_complete",parent=msg.fix_message_id)
  self._event(FixSessionEventType.MESSAGE_ACCEPTED,at,self.state,self.state,"message_accepted",msg.msg_seq_num,msg.fix_message_id)
  return tuple(responses)
 def request_logout(self,at):
  if self.state is not FixSessionState.ESTABLISHED: raise FixSessionError("logout requires established session")
  self._transition(FixSessionState.LOGOUT_PENDING,at,"logout_requested"); return self._emit(logout,at,text="Simulated session logout")
 def tick(self,at:datetime):
  require_utc_datetime(at,field_name="session tick")
  if self.state not in (FixSessionState.ESTABLISHED,FixSessionState.TEST_REQUEST_PENDING): return ()
  baseline=self.last_inbound_at_utc or self.last_outbound_at_utc
  if baseline is None: return ()
  silence=Decimal(str((at-baseline).total_seconds()))
  if self.state is FixSessionState.TEST_REQUEST_PENDING:
   since=Decimal(str((at-self.pending_test_sent_at_utc).total_seconds()))
   if since>=self.configuration.test_request_grace_seconds:
    self.pending_test_request_id=None; self.pending_test_sent_at_utc=None; self._transition(FixSessionState.DISCONNECTED,at,"heartbeat_timeout",FixSessionEventType.CONNECTION_DROPPED); return ()
   return ()
  if silence>=self.configuration.heartbeat_interval_seconds:
   request_id=f"TEST-{self.next_outbound_seq_num}"; msg=self._emit(test_request,at,test_request_id=request_id); self.pending_test_request_id=request_id; self.pending_test_sent_at_utc=at; self._transition(FixSessionState.TEST_REQUEST_PENDING,at,"peer_silence_threshold",FixSessionEventType.TEST_REQUEST_SENT,msg.fix_message_id); return (msg,)
  return ()
 def drop(self,at,reason="configured_simulated_drop"):
  require_utc_datetime(at,field_name="session drop"); self.pending_test_request_id=None; self.pending_test_sent_at_utc=None; self._transition(FixSessionState.DISCONNECTED,at,reason,FixSessionEventType.CONNECTION_DROPPED)