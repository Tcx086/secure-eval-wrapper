"""Convenience constructors for supported simulated FIX messages."""
from __future__ import annotations
from datetime import datetime
from decimal import Decimal
from secure_eval_wrapper.fix.models import FixExecType,FixMessage,FixMessageType,FixOrdStatus,FixOrderType,FixSide,FixTimeInForce

def _d(v): return format(v,"f") if isinstance(v,Decimal) else str(v)
def _time(v):
 from secure_eval_wrapper.fix.codec import format_utc
 return format_utc(v)
def message(msg_type,seq,sender,target,at,fields=None,**kwargs): return FixMessage(msg_type=msg_type,msg_seq_num=seq,sender_comp_id=sender,target_comp_id=target,sending_time_utc=at,fields={} if fields is None else fields,**kwargs)
def logon(seq,sender,target,at,heartbeat_seconds=30): return message(FixMessageType.LOGON,seq,sender,target,at,{98:"0",108:str(heartbeat_seconds)})
def heartbeat(seq,sender,target,at,test_request_id=None): return message(FixMessageType.HEARTBEAT,seq,sender,target,at,{} if test_request_id is None else {112:test_request_id})
def test_request(seq,sender,target,at,test_request_id): return message(FixMessageType.TEST_REQUEST,seq,sender,target,at,{112:test_request_id})
def resend_request(seq,sender,target,at,begin_seq_no,end_seq_no=0): return message(FixMessageType.RESEND_REQUEST,seq,sender,target,at,{7:str(begin_seq_no),16:str(end_seq_no)})
def sequence_reset(seq,sender,target,at,new_seq_no,gap_fill=True): return message(FixMessageType.SEQUENCE_RESET,seq,sender,target,at,{36:str(new_seq_no),123:"Y" if gap_fill else "N"})
def logout(seq,sender,target,at,text=None): return message(FixMessageType.LOGOUT,seq,sender,target,at,{} if text is None else {58:text})
def reject(seq,sender,target,at,ref_seq_num,text,ref_msg_type=None):
 fields={45:str(ref_seq_num),58:text};
 if ref_msg_type is not None: fields[372]=FixMessageType(ref_msg_type).value
 return message(FixMessageType.REJECT,seq,sender,target,at,fields)
def new_order_single(seq,sender,target,at,*,cl_ord_id,symbol,side,quantity,order_type,time_in_force=FixTimeInForce.GTC,price=None,stop_price=None):
 fields={11:cl_ord_id,55:symbol,54:FixSide(side).value,60:_time(at),38:_d(quantity),40:FixOrderType(order_type).value,59:FixTimeInForce(time_in_force).value}
 if price is not None: fields[44]=_d(price)
 if stop_price is not None: fields[99]=_d(stop_price)
 return message(FixMessageType.NEW_ORDER_SINGLE,seq,sender,target,at,fields)
def order_cancel_request(seq,sender,target,at,*,cl_ord_id,orig_cl_ord_id,symbol,side): return message(FixMessageType.ORDER_CANCEL_REQUEST,seq,sender,target,at,{11:cl_ord_id,41:orig_cl_ord_id,55:symbol,54:FixSide(side).value,60:_time(at)})
def execution_report(seq,sender,target,at,*,order_id,exec_id,cl_ord_id,symbol,side,ord_status,exec_type,leaves_qty,cum_qty,avg_px,text=None):
 fields={37:order_id,17:exec_id,11:cl_ord_id,55:symbol,54:FixSide(side).value,39:FixOrdStatus(ord_status).value,150:FixExecType(exec_type).value,151:_d(leaves_qty),14:_d(cum_qty),6:_d(avg_px)}
 if text is not None: fields[58]=text
 return message(FixMessageType.EXECUTION_REPORT,seq,sender,target,at,fields)
def order_cancel_reject(seq,sender,target,at,*,order_id,cl_ord_id,orig_cl_ord_id,ord_status,text): return message(FixMessageType.ORDER_CANCEL_REJECT,seq,sender,target,at,{37:order_id,11:cl_ord_id,41:orig_cl_ord_id,39:FixOrdStatus(ord_status).value,58:text})
def business_message_reject(seq,sender,target,at,*,ref_seq_num,text,reason="3"): return message(FixMessageType.BUSINESS_MESSAGE_REJECT,seq,sender,target,at,{45:str(ref_seq_num),58:text,380:reason})