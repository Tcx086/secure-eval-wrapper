"""Deterministic in-process paper venue; it is not an exchange emulator."""
from __future__ import annotations
from dataclasses import dataclass,replace
from datetime import timedelta
from decimal import Decimal
from secure_eval_wrapper.data_collection.hashing import sha256_payload
from secure_eval_wrapper.execution.models import AccountingMode,OrderSide,OrderType,TimeInForce
from ..enums import AccountSnapshotStatus,InternalPaperFaultType,PaperEnvironment,VenueOrderState
from ..models import PaperAccountSnapshot,VenueBalance,VenueFill,VenueOrder,VenuePosition,deterministic_paper_uuid
from ..venue import EconomicConflictError,PaperVenue,UnknownSubmissionResult,VenueTimeout

@dataclass(frozen=True)
class InternalFault:
    fault_type:InternalPaperFaultType; client_order_id:str|None=None; activation_ordinal:int=0
    @property
    def fault_id(self):return deterministic_paper_uuid("internal-fault",{"type":self.fault_type,"client_order_id":self.client_order_id,"ordinal":self.activation_ordinal})

class InternalPaperVenue(PaperVenue):
    def __init__(self,*,account_reference="public-internal-paper",initial_balances=None,fee_bps=Decimal("10"),faults=()):
        self.account_reference=account_reference; self.fee_bps=fee_bps; self._balances={k.upper():Decimal(v) for k,v in (initial_balances or {"USDT":Decimal("10000")}).items()}; self._positions={}; self._orders={}; self._venue_ids={}; self._fills={}; self._events=[]; self._sequence=0; self._faults=tuple(faults); self._activated=set(); self._last_snapshot=None
    def _fault(self,kind,client=None):
        for f in self._faults:
            if f.fault_id not in self._activated and f.fault_type is kind and (f.client_order_id is None or f.client_order_id==client):
                self._activated.add(f.fault_id); self._event("fault",client,{"fault_type":kind.value,"fault_id":str(f.fault_id)}); return f
        return None
    def _event(self,kind,client,details):
        if kind!="fault" and self._fault(InternalPaperFaultType.SEQUENCE_GAP,client):self._sequence+=1
        self._sequence+=1; row={"sequence":self._sequence,"kind":kind,"client_order_id":client,"details":dict(details),"record_sha256":sha256_payload({"sequence":self._sequence,"kind":kind,"client":client,"details":details})}; self._events.append(row); return row
    @property
    def events(self):return tuple(self._events)
    @property
    def sequence(self):return self._sequence
    def submit_order(self,s):
        existing=self._orders.get(s.client_order_id)
        if existing is not None:
            if existing.economics_sha256!=s.economics_sha256:raise EconomicConflictError("client order ID economics conflict")
            self._event("duplicate_submission",s.client_order_id,{"venue_order_id":existing.venue_order_id}); return existing
        venue_id=str(deterministic_paper_uuid("internal-venue-order",{"run":s.paper_run_id,"client_order_id":s.client_order_id}))
        state=VenueOrderState.UNKNOWN_PENDING_RECOVERY if self._fault(InternalPaperFaultType.UNKNOWN_SUBMISSION,s.client_order_id) else VenueOrderState.PENDING_ACK
        self._event("submitted",s.client_order_id,{"venue_order_id":venue_id})
        order=VenueOrder(s.paper_run_id,s.submission_id,s.client_order_id,venue_id,s.series_identity,s.side,s.order_type,s.time_in_force,s.accounting_mode,s.quantity,Decimal(0),None,state,s.submitted_at_utc,s.submitted_at_utc,self._sequence,s.economics_sha256,s.limit_price,s.stop_price)
        self._orders[s.client_order_id]=order; self._venue_ids[venue_id]=s.client_order_id
        if state is VenueOrderState.UNKNOWN_PENDING_RECOVERY:raise UnknownSubmissionResult("submission outcome is unknown; query original client order ID")
        if self._fault(InternalPaperFaultType.ACK_TIMEOUT,s.client_order_id):raise VenueTimeout("acknowledgement timeout; submission remains pending")
        return order
    def acknowledge(self,client,at):
        order=self._orders[client]
        if order.state in (VenueOrderState.ACKNOWLEDGED,VenueOrderState.PARTIALLY_FILLED):
            self._event("duplicate_ack",client,{"venue_order_id":order.venue_order_id}); return order
        if order.state not in (VenueOrderState.PENDING_ACK,VenueOrderState.UNKNOWN_PENDING_RECOVERY):raise ValueError("order cannot be acknowledged from terminal state")
        self._event("acknowledged",client,{"venue_order_id":order.venue_order_id}); order=replace(order,state=VenueOrderState.ACKNOWLEDGED,updated_at_utc=at,venue_sequence=self._sequence); self._orders[client]=order
        if self._fault(InternalPaperFaultType.DUPLICATE_ACK,client):self._event("duplicate_ack",client,{"venue_order_id":order.venue_order_id})
        return order
    def reject(self,client,at,reason="configured_rejection"):
        order=self._orders[client]; self._event("rejected",client,{"reason":reason}); order=replace(order,state=VenueOrderState.REJECTED,updated_at_utc=at,venue_sequence=self._sequence,reject_reason=reason); self._orders[client]=order; return order
    def expire(self,client,at):
        order=self._orders[client]; self._event("expired",client,{}); order=replace(order,state=VenueOrderState.EXPIRED,updated_at_utc=at,venue_sequence=self._sequence); self._orders[client]=order; return order
    def _assets(self,order):
        parts=order.series_identity.canonical_symbol.replace("/","-").split("-")
        if len(parts)<2:raise ValueError("internal paper Spot symbol must identify base and quote")
        return parts[0].upper(),parts[1].upper()
    def _apply_spot_fill(self,order,quantity,price,fee):
        base,quote=self._assets(order); base_before=self._balances.get(base,Decimal(0)); quote_before=self._balances.get(quote,Decimal(0)); notional=quantity*price
        key=order.series_identity.series_identity_sha256; old=self._positions.get(key,VenuePosition(order.series_identity,AccountingMode.SPOT,Decimal(0),None))
        if order.side is OrderSide.BUY:
            if quote_before<notional+fee:raise ValueError("internal paper venue insufficient quote balance")
            self._balances[quote]=quote_before-notional-fee; self._balances[base]=base_before+quantity
            new_qty=old.quantity+quantity; avg=(old.quantity*(old.average_entry_price or Decimal(0))+quantity*price)/new_qty; realized=old.realized_pnl
        else:
            if base_before<quantity or old.quantity<quantity:raise ValueError("internal paper venue negative Spot inventory prohibited")
            self._balances[base]=base_before-quantity; self._balances[quote]=quote_before+notional-fee
            realized=old.realized_pnl+quantity*(price-(old.average_entry_price or price)); new_qty=old.quantity-quantity; avg=None if new_qty==0 else old.average_entry_price
        self._positions[key]=VenuePosition(order.series_identity,AccountingMode.SPOT,new_qty,avg,realized)
    def fill(self,client,quantity,price,at,*,venue_fill_id=None):
        order=self._orders[client]
        if order.state not in (VenueOrderState.ACKNOWLEDGED,VenueOrderState.PARTIALLY_FILLED):raise ValueError("only acknowledged active order may fill")
        quantity=Decimal(quantity); price=Decimal(price)
        if quantity<=0 or quantity>order.remaining_quantity:raise ValueError("invalid partial fill quantity")
        venue_fill_id=venue_fill_id or f"{order.venue_order_id}:{len([f for f in self._fills.values() if f.venue_order_id==order.venue_order_id])+1}"
        if venue_fill_id in self._fills:
            existing=self._fills[venue_fill_id]
            if existing.quantity!=quantity or existing.price!=price:raise EconomicConflictError("duplicate fill ID changed economics")
            self._event("duplicate_fill",client,{"venue_fill_id":venue_fill_id}); return order,existing,False
        if self._fault(InternalPaperFaultType.DELAYED_FILL,client):self._event("fill_delayed",client,{"venue_fill_id":venue_fill_id}); return order,None,False
        fee=quantity*price*self.fee_bps/Decimal(10000); self._event("fill",client,{"venue_fill_id":venue_fill_id,"quantity":str(quantity),"price":str(price)})
        fill=VenueFill(order.paper_run_id,order.submission_id,client,order.venue_order_id,venue_fill_id,order.series_identity,order.side,order.accounting_mode,quantity,price,fee,"USDT",at,self._sequence,PaperEnvironment.PAPER_INTERNAL)
        self._fills[venue_fill_id]=fill
        if order.accounting_mode is AccountingMode.SPOT:self._apply_spot_fill(order,quantity,price,fee)
        else:
            key=order.series_identity.series_identity_sha256; old=self._positions.get(key,VenuePosition(order.series_identity,AccountingMode.LINEAR_PERPETUAL,Decimal(0),None)); signed=quantity*order.side.sign; new=old.quantity+signed; avg=price if old.quantity==0 or old.quantity*new<0 else ((abs(old.quantity)*(old.average_entry_price or price)+quantity*price)/(abs(old.quantity)+quantity) if abs(new)>abs(old.quantity) else old.average_entry_price); self._positions[key]=VenuePosition(order.series_identity,AccountingMode.LINEAR_PERPETUAL,new,None if new==0 else avg,old.realized_pnl)
        cumulative=order.cumulative_filled_quantity+quantity; avg_price=((order.cumulative_filled_quantity*(order.average_fill_price or Decimal(0)))+quantity*price)/cumulative; state=VenueOrderState.FILLED if cumulative==order.quantity else VenueOrderState.PARTIALLY_FILLED; order=replace(order,cumulative_filled_quantity=cumulative,average_fill_price=avg_price,state=state,updated_at_utc=at,venue_sequence=self._sequence); self._orders[client]=order
        if self._fault(InternalPaperFaultType.DUPLICATE_FILL,client):self._event("duplicate_fill",client,{"venue_fill_id":venue_fill_id})
        return order,fill,True
    def on_market_event(self,*,at_utc,prices):
        results=[]
        for order in list(self.list_open_orders()):
            if order.state is not VenueOrderState.ACKNOWLEDGED:continue
            price=Decimal(prices[order.series_identity.canonical_symbol]); trigger=True
            if order.order_type is OrderType.LIMIT:trigger=price<=order.limit_price if order.side is OrderSide.BUY else price>=order.limit_price
            elif order.order_type is OrderType.STOP:trigger=price>=order.stop_price if order.side is OrderSide.BUY else price<=order.stop_price
            elif order.order_type is OrderType.STOP_LIMIT:
                stopped=price>=order.stop_price if order.side is OrderSide.BUY else price<=order.stop_price; limited=price<=order.limit_price if order.side is OrderSide.BUY else price>=order.limit_price; trigger=stopped and limited
            if trigger:results.append(self.fill(order.client_order_id,order.remaining_quantity,price,at_utc))
            elif order.time_in_force is TimeInForce.IOC:results.append((self.expire(order.client_order_id,at_utc),None,False))
        return tuple(results)
    def cancel_order(self,client,at_utc):
        order=self._orders[client]
        if order.state in (VenueOrderState.CANCELLED,VenueOrderState.FILLED,VenueOrderState.REJECTED,VenueOrderState.EXPIRED):return order
        if self._fault(InternalPaperFaultType.CANCEL_TIMEOUT,client):self._event("cancel_timeout",client,{}); order=replace(order,state=VenueOrderState.CANCEL_PENDING,updated_at_utc=at_utc,venue_sequence=self._sequence); self._orders[client]=order; raise VenueTimeout("cancel timeout; cancellation outcome is unknown")
        self._event("cancel_pending",client,{}); order=replace(order,state=VenueOrderState.CANCEL_PENDING,updated_at_utc=at_utc,venue_sequence=self._sequence); self._orders[client]=order; return order
    def complete_cancel(self,client,at_utc):
        order=self._orders[client]
        if order.state is VenueOrderState.CANCELLED:return order
        if order.state is not VenueOrderState.CANCEL_PENDING:raise ValueError("cancel acknowledgement requires cancel_pending")
        self._event("cancelled",client,{}); order=replace(order,state=VenueOrderState.CANCELLED,updated_at_utc=at_utc,venue_sequence=self._sequence); self._orders[client]=order; return order
    def query_order(self,client_order_id):return self._orders.get(client_order_id)
    def list_open_orders(self):
        active={VenueOrderState.PENDING_ACK,VenueOrderState.ACKNOWLEDGED,VenueOrderState.PARTIALLY_FILLED,VenueOrderState.CANCEL_PENDING,VenueOrderState.UNKNOWN_PENDING_RECOVERY}
        return tuple(sorted((o for o in self._orders.values() if o.state in active),key=lambda o:(o.created_at_utc,o.client_order_id)))
    def fetch_balances(self):return tuple(VenueBalance(k,v,v,Decimal(0)) for k,v in sorted(self._balances.items()))
    def fetch_positions(self):return tuple(sorted(self._positions.values(),key=lambda p:p.series_identity.series_identity_sha256))
    def fetch_fills(self):return tuple(sorted(self._fills.values(),key=lambda f:(f.filled_at_utc,f.venue_sequence,f.venue_fill_id)))
    def fetch_account_snapshot(self,paper_run_id,at_utc):
        stale=self._fault(InternalPaperFaultType.STALE_SNAPSHOT,None); lag=self._fault(InternalPaperFaultType.BALANCE_LAG,None) or self._fault(InternalPaperFaultType.POSITION_LAG,None)
        if lag and self._last_snapshot:return self._last_snapshot
        as_of=at_utc-timedelta(seconds=120) if stale else at_utc; status=AccountSnapshotStatus.STALE if stale else AccountSnapshotStatus.FRESH
        snap=PaperAccountSnapshot(paper_run_id,self.account_reference,status,at_utc,as_of,"spot",self.fetch_balances(),self.fetch_positions(),tuple(o.client_order_id for o in self.list_open_orders()),self._sequence,"internal_paper_venue"); self._last_snapshot=snap; return snap
    def reconstruct(self,orders,fills,events):
        self._orders={o.client_order_id:o for o in orders}; self._venue_ids={o.venue_order_id:o.client_order_id for o in orders}; self._fills={f.venue_fill_id:f for f in fills}; self._events=list(events); self._sequence=max((e["sequence"] for e in events),default=0); return self
