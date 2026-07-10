"""In-process FIX gateway that can call only the existing SimulatedBroker."""
from __future__ import annotations
from dataclasses import dataclass
from decimal import Decimal
from typing import Callable,Mapping
from uuid import UUID
from secure_eval_wrapper.data_collection.hashing import sha256_payload
from secure_eval_wrapper.execution.broker import BrokerResult
from secure_eval_wrapper.execution.brokers.simulated import SimulatedBroker
from secure_eval_wrapper.execution.models import AccountingMode,OrderIntent,OrderIntentStatus,OrderSide,OrderStatus,OrderType,RiskDecision,RiskDecisionStatus,RiskStage,TimeInForce
from secure_eval_wrapper.fix.messages import business_message_reject,execution_report,order_cancel_reject
from secure_eval_wrapper.fix.models import FixExecType,FixMessage,FixMessageType,FixOrderLink,FixOrderType,FixOrdStatus,FixSessionState,FixSide,FixTimeInForce,fix_uuid

@dataclass(frozen=True)
class GatewaySeries:
 series_identity:object
 accounting_mode:AccountingMode
 current_quantity:Decimal=Decimal("0")
 reference_price:Decimal|None=None

class SimulatedFixGateway:
 def __init__(self,*,session,broker,run_id:UUID,series_by_symbol:Mapping[str,GatewaySeries],implementation_code_sha256:str,repository_commit_sha:str,data_sha256:str,risk_check:Callable|None=None,pre_fill_risk_check:Callable|None=None,fill_callback:Callable|None=None):
  if not isinstance(broker,SimulatedBroker): raise TypeError("Phase 6 FIX gateway requires the existing SimulatedBroker")
  self.session=session; self.broker=broker; self.run_id=run_id; self.series_by_symbol=dict(series_by_symbol); self.implementation_code_sha256=implementation_code_sha256; self.repository_commit_sha=repository_commit_sha; self.data_sha256=data_sha256; self.risk_check=risk_check; self.pre_fill_risk_check=pre_fill_risk_check; self.fill_callback=fill_callback; self._by_clord={}; self._economic={}; self.links=[]; self.reports=[]
 def _risk(self,intent):
  if self.risk_check is not None: return self.risk_check(intent)
  blocked = intent.accounting_mode is AccountingMode.SPOT and intent.target_quantity < 0
  return RiskDecision(run_id=self.run_id,order_intent_id=intent.order_intent_id,series_identity=intent.series_identity,decision_timestamp_utc=intent.event_timestamp_utc,stage=RiskStage.PRE_SUBMIT,status=RiskDecisionStatus.BLOCKED if blocked else RiskDecisionStatus.ACCEPTED,reason_code="spot_short_prohibited" if blocked else "simulated_fix_accepted",explanation="Spot short target was blocked by the simulated gateway." if blocked else "Accepted by the explicitly simulated FIX gateway default policy.",config_sha256=self.broker.configuration.config_sha256)
 def _prefill(self,order,price,liquidity,fee):
  if self.pre_fill_risk_check is not None: return self.pre_fill_risk_check(order,price,liquidity,fee)
  return RiskDecision(run_id=self.run_id,order_intent_id=order.order_intent_id,order_id=order.order_id,series_identity=order.series_identity,decision_timestamp_utc=order.submitted_at_utc,stage=RiskStage.PRE_FILL,status=RiskDecisionStatus.ACCEPTED,reason_code="simulated_fix_prefill_accepted",explanation="Accepted by the explicitly simulated FIX gateway default pre-fill policy.",config_sha256=self.broker.configuration.config_sha256)
 def _report(self,at,*,cl_ord_id,order,exec_type,ord_status,fill=None,text=None):
  cum=Decimal("0") if fill is None else fill.quantity; leaves=order.quantity-cum if ord_status not in (FixOrdStatus.CANCELLED,FixOrdStatus.REJECTED,FixOrdStatus.EXPIRED) else Decimal("0"); avg=Decimal("0") if fill is None else fill.price
  msg=self.session._emit(execution_report,at,order_id=str(order.order_id),exec_id=str(fix_uuid("exec-report",{"order":order.order_id,"status":ord_status,"fill":None if fill is None else fill.fill_id})),cl_ord_id=cl_ord_id,symbol=order.series_identity.canonical_symbol,side=FixSide.BUY if order.side is OrderSide.BUY else FixSide.SELL,ord_status=ord_status,exec_type=exec_type,leaves_qty=leaves,cum_qty=cum,avg_px=avg,text=text); self.reports.append(msg)
  link=FixOrderLink(self.session.fix_session_id,cl_ord_id,order_intent_id=order.order_intent_id,order_id=order.order_id,fill_id=None if fill is None else fill.fill_id,execution_report_message_id=msg.fix_message_id); self.links.append(link); return msg
 def handle(self,message:FixMessage,processing_at_utc):
  admin=self.session.receive(message,processing_at_utc)
  if message.msg_type not in (FixMessageType.NEW_ORDER_SINGLE,FixMessageType.ORDER_CANCEL_REQUEST): return admin
  if self.session.state is not FixSessionState.ESTABLISHED: return admin+(self.session._emit(business_message_reject,processing_at_utc,ref_seq_num=message.msg_seq_num,text="Simulated FIX session is not established"),)
  return admin+(self._new(message,processing_at_utc) if message.msg_type is FixMessageType.NEW_ORDER_SINGLE else self._cancel(message,processing_at_utc),)
 def _new(self,message,at):
  clid=message.fields[11]; economic=sha256_payload({k:message.fields.get(k) for k in (11,55,54,38,40,44,99,59)})
  if clid in self._economic:
   if self._economic[clid]!=economic: return self.session._emit(business_message_reject,at,ref_seq_num=message.msg_seq_num,text="ClOrdID economic content conflict")
   order=self._by_clord[clid]; return self._report(at,cl_ord_id=clid,order=order,exec_type=FixExecType.NEW,ord_status=FixOrdStatus.NEW,text="Idempotent simulated acknowledgement")
  symbol=message.fields[55]; series=self.series_by_symbol.get(symbol)
  if series is None: return self.session._emit(business_message_reject,at,ref_seq_num=message.msg_seq_num,text="Unsupported simulated symbol")
  quantity=Decimal(message.fields[38]);
  if quantity<=0: return self.session._emit(business_message_reject,at,ref_seq_num=message.msg_seq_num,text="OrderQty must be positive")
  side=OrderSide.BUY if FixSide(message.fields[54]) is FixSide.BUY else OrderSide.SELL; order_type={FixOrderType.MARKET:OrderType.MARKET,FixOrderType.LIMIT:OrderType.LIMIT,FixOrderType.STOP:OrderType.STOP,FixOrderType.STOP_LIMIT:OrderType.STOP_LIMIT}[FixOrderType(message.fields[40])]; tif=TimeInForce.GTC if FixTimeInForce(message.fields[59]) is FixTimeInForce.GTC else TimeInForce.IOC; delta=quantity if side is OrderSide.BUY else -quantity; reference=series.reference_price or Decimal(message.fields.get(44) or message.fields.get(99) or "0")
  if reference<=0: return self.session._emit(business_message_reject,at,ref_seq_num=message.msg_seq_num,text="A positive simulated reference price is required")
  intent=OrderIntent(run_id=self.run_id,signal_id=fix_uuid("synthetic-signal",{"message":message.fix_message_id}),series_identity=series.series_identity,event_timestamp_utc=at,side=side,order_type=order_type,quantity=quantity,target_quantity=series.current_quantity+delta,current_quantity=series.current_quantity,delta_quantity=delta,reference_price=reference,accounting_mode=series.accounting_mode,time_in_force=tif,config_sha256=self.broker.configuration.config_sha256,data_sha256=self.data_sha256,implementation_code_sha256=self.implementation_code_sha256,repository_commit_sha=self.repository_commit_sha,limit_price=None if 44 not in message.fields else Decimal(message.fields[44]),stop_price=None if 99 not in message.fields else Decimal(message.fields[99]),status=OrderIntentStatus.SUBMITTED,provenance={"simulated_fix":True,"cl_ord_id":clid})
  risk=self._risk(intent); result=self.broker.submit_order_intent(intent,risk); order=result.order_updates[-1]; self._economic[clid]=economic; self._by_clord[clid]=order
  if order.status is OrderStatus.REJECTED: return self._report(at,cl_ord_id=clid,order=order,exec_type=FixExecType.REJECTED,ord_status=FixOrdStatus.REJECTED,text=risk.reason_code)
  return self._report(at,cl_ord_id=clid,order=order,exec_type=FixExecType.NEW,ord_status=FixOrdStatus.NEW,text="Simulated acknowledgement; not a fill")
 def _cancel(self,message,at):
  cancel_id=message.fields[11]; orig=message.fields[41]; order=self._by_clord.get(orig)
  if order is None:
   return self.session._emit(order_cancel_reject,at,order_id="UNKNOWN",cl_ord_id=cancel_id,orig_cl_ord_id=orig,ord_status=FixOrdStatus.REJECTED,text="Unknown OrigClOrdID")
  result=self.broker.cancel_order(order.order_id,cancelled_at_utc=at,reason="simulated_fix_cancel")
  if not result.order_updates:
   status={OrderStatus.FILLED:FixOrdStatus.FILLED,OrderStatus.CANCELLED:FixOrdStatus.CANCELLED,OrderStatus.EXPIRED:FixOrdStatus.EXPIRED}.get(order.status,FixOrdStatus.REJECTED)
   return self.session._emit(order_cancel_reject,at,order_id=str(order.order_id),cl_ord_id=cancel_id,orig_cl_ord_id=orig,ord_status=status,text="Order is not active")
  updated=result.order_updates[-1]; self._by_clord[orig]=updated; return self._report(at,cl_ord_id=orig,order=updated,exec_type=FixExecType.CANCELLED,ord_status=FixOrdStatus.CANCELLED,text="Simulated cancellation")
 def process_bar_open(self,*,symbol,timestamp_utc,open_price):
  series=self.series_by_symbol[symbol]; result=self.broker.process_bar_open(series_identity=series.series_identity,timestamp_utc=timestamp_utc,open_price=open_price,risk_check=self._prefill); return self._reports_from_broker(result,timestamp_utc)
 def process_completed_bar(self,*,symbol,timestamp_utc,open_price,high,low,close):
  series=self.series_by_symbol[symbol]; result=self.broker.process_completed_bar(series_identity=series.series_identity,timestamp_utc=timestamp_utc,open_price=open_price,high=high,low=low,close=close,risk_check=self._prefill); return self._reports_from_broker(result,timestamp_utc)
 def _reports_from_broker(self,result,at):
  reports=[]; fills={fill.order_id:fill for fill in result.fills}
  for order in result.order_updates:
   clid=next((key for key,value in self._by_clord.items() if value.order_id==order.order_id),None)
   if clid is None: continue
   self._by_clord[clid]=order; fill=fills.get(order.order_id)
   mapping={OrderStatus.TRIGGERED:(FixExecType.TRIGGERED,FixOrdStatus.TRIGGERED),OrderStatus.FILLED:(FixExecType.TRADE,FixOrdStatus.FILLED),OrderStatus.REJECTED:(FixExecType.REJECTED,FixOrdStatus.REJECTED),OrderStatus.EXPIRED:(FixExecType.EXPIRED,FixOrdStatus.EXPIRED),OrderStatus.CANCELLED:(FixExecType.CANCELLED,FixOrdStatus.CANCELLED)}
   if order.status in mapping: reports.append(self._report(at,cl_ord_id=clid,order=order,exec_type=mapping[order.status][0],ord_status=mapping[order.status][1],fill=fill,text="Simulated broker lifecycle update"))
   if fill is not None and self.fill_callback is not None: self.fill_callback(fill)
  return tuple(reports)