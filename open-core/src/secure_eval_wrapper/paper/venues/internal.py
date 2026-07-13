"""Deterministic in-process paper venue; it is not an exchange emulator."""
from __future__ import annotations
from dataclasses import asdict,dataclass,replace
import copy
from datetime import timedelta
from decimal import Decimal
from secure_eval_wrapper.data_collection.hashing import sha256_payload
from secure_eval_wrapper.execution.models import AccountingMode,OrderSide,OrderType,TimeInForce
from ..enums import AccountSnapshotStatus,InternalPaperFaultType,PaperEnvironment,VenueOrderState
from ..models import PaperAccountSnapshot,VenueBalance,VenueFill,VenueOrder,VenuePosition,deterministic_paper_uuid
from ..reservations import CALCULATOR_VERSION,DEFAULT_MAXIMUM_ADVERSE_SLIPPAGE_BPS,calculate_reservation,reduce_reservation
from ..venue import EconomicConflictError,PaperVenue,UnknownSubmissionResult,VenueTimeout

@dataclass(frozen=True)
class InternalFault:
    fault_type:InternalPaperFaultType; client_order_id:str|None=None; activation_ordinal:int=0
    @property
    def fault_id(self):return deterministic_paper_uuid("internal-fault",{"type":self.fault_type,"client_order_id":self.client_order_id,"ordinal":self.activation_ordinal})

class InternalPaperVenue(PaperVenue):
    IMPLEMENTATION_SHA256=sha256_payload({"component":"InternalPaperVenue","version":"phase7-price-terminal-expiry-integrity-v1"})
    FEE_CURRENCY_POLICY="spot_quote_or_perpetual_settlement"
    FILL_PRICE_POLICY="explicit_internal_market_event_or_test_fill"
    def __init__(self,*,account_reference="public-internal-paper",initial_balances=None,fee_bps=Decimal("10"),maximum_adverse_slippage_bps=DEFAULT_MAXIMUM_ADVERSE_SLIPPAGE_BPS,faults=()):
        self.account_reference=account_reference; self.fee_bps=Decimal(fee_bps); self.maximum_adverse_slippage_bps=Decimal(maximum_adverse_slippage_bps); self.reservation_calculator_version=CALCULATOR_VERSION; self.fee_currency_policy=self.FEE_CURRENCY_POLICY; self.fill_price_policy=self.FILL_PRICE_POLICY; self.implementation_sha256=self.IMPLEMENTATION_SHA256; self._initial_balances={k.upper():Decimal(v) for k,v in (initial_balances or {"USDT":Decimal("10000")}).items()}; self._balances=dict(self._initial_balances); self._positions={}; self._reservations={}; self._orders={}; self._venue_ids={}; self._fills={}; self._events=[]; self._sequence=0; self._faults=tuple(faults); self._activated=set(); self._last_snapshot=None; self._repository=None; self._paper_run_id=None; self._active_command_id=None; self._active_submission_id=None; self._active_command_at=None; self._latest_internal_event_id=None
        self.submit_call_count=0;self.cancel_call_count=0;self.query_call_count=0
    def _fault(self,kind,client=None):
        for f in self._faults:
            if f.fault_id not in self._activated and f.fault_type is kind and (f.client_order_id is None or f.client_order_id==client):
                self._activated.add(f.fault_id); prior=(self._active_command_id,self._active_submission_id,self._active_command_at); self._begin_command("fault",client,self._active_command_at,{"fault_type":kind.value,"fault_id":str(f.fault_id)},idempotency_key=str(f.fault_id)); self._event("fault",client,{"fault_type":kind.value,"fault_id":str(f.fault_id)}); self._active_command_id,self._active_submission_id,self._active_command_at=prior; return f
        return None
    def bind_persistence(self,repository,paper_run_id):
        self._repository=repository;self._paper_run_id=paper_run_id;return self
    def _begin_command(self,kind,client,at,payload,submission_id=None,idempotency_key=None):
        self._active_command_at=at;self._active_submission_id=submission_id
        if self._repository is None:self._active_command_id=None;return None
        key=idempotency_key or sha256_payload({"kind":kind,"client":client,"at":at,"payload":payload})
        self._active_command_id=self._repository.record_internal_venue_command(paper_run_id=self._paper_run_id,submission_id=submission_id,client_order_id=client,command_type=kind,idempotency_key=key,at_utc=at,payload=payload);return self._active_command_id
    def _event(self,kind,client,details,at=None):
        at=at or self._active_command_at
        if kind!="fault" and self._fault(InternalPaperFaultType.SEQUENCE_GAP,client):self._sequence+=1
        if self._repository is not None:
            if self._active_command_id is None:raise RuntimeError("persistent internal venue event requires a durable command")
            self._sequence,self._latest_internal_event_id=self._repository.append_internal_venue_event(paper_run_id=self._paper_run_id,command_id=self._active_command_id,submission_id=self._active_submission_id,client_order_id=client,event_type=kind,at_utc=at,details=details)
        else:self._sequence+=1
        row={"sequence":self._sequence,"kind":kind,"client_order_id":client,"details":dict(details),"occurred_at_utc":at,"internal_venue_event_id":self._latest_internal_event_id,"record_sha256":sha256_payload({"sequence":self._sequence,"kind":kind,"client":client,"details":details})}; self._events.append(row); return row
    @property
    def events(self):return tuple(self._events)
    @property
    def sequence(self):return self._sequence
    def _reserve(self,s):
        required=calculate_reservation(s,maximum_fee_bps=self.fee_bps,maximum_adverse_slippage_bps=self.maximum_adverse_slippage_bps);reserved=sum((r["amount"] for r in self._reservations.values() if r["currency"]==required.currency),Decimal(0))
        if self._balances.get(required.currency,Decimal(0))-reserved<required.amount:raise ValueError("internal venue reservation exceeds balance")
        self._reservations[s.client_order_id]={"currency":required.currency,"amount":required.amount,"original_quantity":s.quantity,"remaining_quantity":s.quantity}
    def _reduce_reservation(self,client,quantity):
        r=self._reservations.get(client)
        if not r:return
        remaining=max(Decimal(0),r["remaining_quantity"]-quantity)
        if remaining==0:self._reservations.pop(client,None)
        else:self._reservations[client]={**r,"remaining_quantity":remaining,"amount":r["amount"]*remaining/r["original_quantity"]}
    def _release_reservation(self,client):self._reservations.pop(client,None)
    def submit_order(self,s):
        self.submit_call_count+=1;before=(self._sequence,copy.deepcopy(self._events),copy.deepcopy(self._orders),copy.deepcopy(self._venue_ids),copy.deepcopy(self._reservations),copy.deepcopy(self._activated))
        try:
            self._begin_command("submit",s.client_order_id,s.submitted_at_utc,{"economics_sha256":s.economics_sha256,"idempotency_key":s.idempotency_key},submission_id=s.submission_id,idempotency_key=s.idempotency_key);existing=self._orders.get(s.client_order_id)
            if existing is not None:
                if existing.economics_sha256!=s.economics_sha256:raise EconomicConflictError("client order ID economics conflict")
                self._event("duplicate_submission",s.client_order_id,{"venue_order_id":existing.venue_order_id});return existing
            venue_id=str(deterministic_paper_uuid("internal-venue-order",{"run":s.paper_run_id,"client_order_id":s.client_order_id}));self._reserve(s);state=VenueOrderState.UNKNOWN_PENDING_RECOVERY if self._fault(InternalPaperFaultType.UNKNOWN_SUBMISSION,s.client_order_id) else VenueOrderState.PENDING_ACK;self._event("submitted",s.client_order_id,{"venue_order_id":venue_id});order=VenueOrder(s.paper_run_id,s.submission_id,s.client_order_id,venue_id,s.series_identity,s.side,s.order_type,s.time_in_force,s.accounting_mode,s.quantity,Decimal(0),None,state,s.submitted_at_utc,s.submitted_at_utc,self._sequence,s.economics_sha256,s.limit_price,s.stop_price);self._orders[s.client_order_id]=order;self._venue_ids[venue_id]=s.client_order_id
            if state is VenueOrderState.UNKNOWN_PENDING_RECOVERY:raise UnknownSubmissionResult("submission outcome is unknown; query original client order ID")
            if self._fault(InternalPaperFaultType.ACK_TIMEOUT,s.client_order_id):raise VenueTimeout("acknowledgement timeout; submission remains pending")
            return order
        except (UnknownSubmissionResult,VenueTimeout):raise
        except Exception:
            self._sequence,self._events,self._orders,self._venue_ids,self._reservations,self._activated=before;raise
    def acknowledge(self,client,at):
        order=self._orders[client];self._begin_command("acknowledge",client,at,{"venue_order_id":order.venue_order_id},submission_id=order.submission_id,idempotency_key=f"ack:{order.venue_order_id}")
        if order.state in (VenueOrderState.ACKNOWLEDGED,VenueOrderState.PARTIALLY_FILLED):
            self._event("duplicate_ack",client,{"venue_order_id":order.venue_order_id}); return order
        if order.state not in (VenueOrderState.PENDING_ACK,VenueOrderState.UNKNOWN_PENDING_RECOVERY):raise ValueError("order cannot be acknowledged from terminal state")
        self._event("acknowledged",client,{"venue_order_id":order.venue_order_id}); order=replace(order,state=VenueOrderState.ACKNOWLEDGED,updated_at_utc=at,venue_sequence=self._sequence); self._orders[client]=order
        if self._fault(InternalPaperFaultType.DUPLICATE_ACK,client):self._event("duplicate_ack",client,{"venue_order_id":order.venue_order_id})
        return order
    def reject(self,client,at,reason="configured_rejection"):
        order=self._orders[client]; self._begin_command("reject",client,at,{"reason":reason},submission_id=order.submission_id,idempotency_key=f"reject:{order.venue_order_id}:{reason}"); self._event("rejected",client,{"reason":reason}); order=replace(order,state=VenueOrderState.REJECTED,updated_at_utc=at,venue_sequence=self._sequence,reject_reason=reason); self._orders[client]=order; self._release_reservation(client); return order
    def expire(self,client,at):
        order=self._orders[client]; self._begin_command("expire",client,at,{"venue_order_id":order.venue_order_id},submission_id=order.submission_id,idempotency_key=f"expire:{order.venue_order_id}"); self._event("expired",client,{}); order=replace(order,state=VenueOrderState.EXPIRED,updated_at_utc=at,venue_sequence=self._sequence); self._orders[client]=order; self._release_reservation(client); return order
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
    def fill(self,client,quantity,price,at,*,venue_fill_id=None,fee_currency=None):
        before=(self._sequence,copy.deepcopy(self._events),copy.deepcopy(self._fills),copy.deepcopy(self._orders),copy.deepcopy(self._balances),copy.deepcopy(self._positions),copy.deepcopy(self._reservations),copy.deepcopy(self._activated))
        try:
            order=self._orders[client]
            if order.state not in (VenueOrderState.ACKNOWLEDGED,VenueOrderState.PARTIALLY_FILLED):raise ValueError("only acknowledged active order may fill")
            quantity=Decimal(quantity);price=Decimal(price)
            if quantity<=0 or quantity>order.remaining_quantity:raise ValueError("invalid partial fill quantity")
            venue_fill_id=venue_fill_id or f"{order.venue_order_id}:{len([f for f in self._fills.values() if f.venue_order_id==order.venue_order_id])+1}"
            self._begin_command("fill",client,at,{"venue_fill_id":venue_fill_id,"quantity":str(quantity),"price":str(price),"fee_currency":fee_currency},submission_id=order.submission_id,idempotency_key=f"fill:{order.venue_order_id}:{venue_fill_id}")
            if venue_fill_id in self._fills:
                existing=self._fills[venue_fill_id]
                expected_fee=quantity*price*self.fee_bps/Decimal(10000)
                if existing.quantity!=quantity or existing.price!=price or existing.fee_amount!=expected_fee:raise EconomicConflictError("duplicate fill ID changed economics")
                self._event("duplicate_fill",client,{"venue_fill_id":venue_fill_id});return order,existing,False
            if self._fault(InternalPaperFaultType.DELAYED_FILL,client):self._event("fill_delayed",client,{"venue_fill_id":venue_fill_id});return order,None,False
            fee=quantity*price*self.fee_bps/Decimal(10000);base,quote=self._assets(order);expected_fee_currency=quote if order.accounting_mode is AccountingMode.SPOT else order.series_identity.settlement_asset.upper();fee_currency=(fee_currency or expected_fee_currency).upper()
            if fee_currency!=expected_fee_currency:raise ValueError("internal paper fill fee currency does not match settlement authority")
            reservation=self._reservations.get(client)
            if reservation is None:raise ValueError("internal paper fill lacks reservation coverage")
            required=quantity*price+fee if order.side is OrderSide.BUY else quantity
            coverage=reservation["amount"] if order.side is OrderSide.BUY else reservation["remaining_quantity"]
            if coverage<required:raise ValueError("internal paper reservation does not cover fill and fee")
            balances=dict(self._balances);positions=dict(self._positions);reservations=copy.deepcopy(self._reservations);key=order.series_identity.series_identity_sha256;old=positions.get(key,VenuePosition(order.series_identity,order.accounting_mode,Decimal(0),None));notional=quantity*price
            if order.accounting_mode is AccountingMode.SPOT:
                base_before=balances.get(base,Decimal(0));quote_before=balances.get(quote,Decimal(0))
                if order.side is OrderSide.BUY:
                    if quote_before<notional+fee:raise ValueError("internal paper venue insufficient quote balance")
                    balances[quote]=quote_before-notional-fee;balances[base]=base_before+quantity;new_qty=old.quantity+quantity;avg=(old.quantity*(old.average_entry_price or Decimal(0))+quantity*price)/new_qty;realized=old.realized_pnl
                else:
                    if base_before<quantity or old.quantity<quantity:raise ValueError("internal paper venue negative Spot inventory prohibited")
                    balances[base]=base_before-quantity;balances[quote]=quote_before+notional-fee;realized=old.realized_pnl+quantity*(price-(old.average_entry_price or price));new_qty=old.quantity-quantity;avg=None if new_qty==0 else old.average_entry_price
                if balances[base]<0 or balances[quote]<0 or new_qty<0:raise ValueError("internal paper candidate state would be negative")
                positions[key]=VenuePosition(order.series_identity,AccountingMode.SPOT,new_qty,avg,realized)
            else:
                signed=quantity*order.side.sign;new_qty=old.quantity+signed;avg=price if old.quantity==0 or old.quantity*new_qty<0 else ((abs(old.quantity)*(old.average_entry_price or price)+quantity*price)/(abs(old.quantity)+quantity) if abs(new_qty)>abs(old.quantity) else old.average_entry_price);positions[key]=VenuePosition(order.series_identity,AccountingMode.LINEAR_PERPETUAL,new_qty,None if new_qty==0 else avg,old.realized_pnl)
            reduced=reduce_reservation(current_amount=reservation["amount"],current_quantity=reservation["remaining_quantity"],fill_quantity=quantity,fill_price=price,fill_fee=fee,fee_currency=fee_currency,reservation_currency=reservation["currency"],side=order.side,accounting_mode=order.accounting_mode)
            if reduced.quantity==0:reservations.pop(client,None)
            else:reservations[client]={**reservation,"remaining_quantity":reduced.quantity,"amount":reduced.amount}
            cumulative=order.cumulative_filled_quantity+quantity;avg_price=((order.cumulative_filled_quantity*(order.average_fill_price or Decimal(0)))+quantity*price)/cumulative;state=VenueOrderState.FILLED if cumulative==order.quantity else VenueOrderState.PARTIALLY_FILLED
            balance_deltas={currency:str(balances.get(currency,Decimal(0))-self._balances.get(currency,Decimal(0))) for currency in sorted(set(balances)|set(self._balances)) if balances.get(currency,Decimal(0))!=self._balances.get(currency,Decimal(0))};reservation_after=reservations.get(client);details={"venue_fill_id":venue_fill_id,"quantity":str(quantity),"price":str(price),"fee":str(fee),"fee_currency":fee_currency,"reservation_consumed":str(required),"reservation_released":str(max(Decimal(0),reduced.amount_consumed-required)),"balance_deltas":balance_deltas,"position_before_sha256":sha256_payload(asdict(old)),"position_after_sha256":sha256_payload(asdict(positions[key])),"reservation_before_sha256":sha256_payload(reservation),"reservation_after_sha256":sha256_payload(reservation_after)};self._event("fill",client,details)
            fill=VenueFill(order.paper_run_id,order.submission_id,client,order.venue_order_id,venue_fill_id,order.series_identity,order.side,order.accounting_mode,quantity,price,fee,fee_currency,at,self._sequence,PaperEnvironment.PAPER_INTERNAL);updated=replace(order,cumulative_filled_quantity=cumulative,average_fill_price=avg_price,state=state,updated_at_utc=at,venue_sequence=self._sequence)
            self._balances=balances;self._positions=positions;self._reservations=reservations;self._fills[venue_fill_id]=fill;self._orders[client]=updated
            if self._fault(InternalPaperFaultType.DUPLICATE_FILL,client):self._event("duplicate_fill",client,{"venue_fill_id":venue_fill_id})
            return updated,fill,True
        except Exception:
            self._sequence,self._events,self._fills,self._orders,self._balances,self._positions,self._reservations,self._activated=before
            raise
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
        self.cancel_call_count+=1
        order=self._orders[client];self._begin_command("cancel_request",client,at_utc,{"venue_order_id":order.venue_order_id},submission_id=order.submission_id,idempotency_key=f"cancel:{order.venue_order_id}")
        if order.state in (VenueOrderState.CANCELLED,VenueOrderState.FILLED,VenueOrderState.REJECTED,VenueOrderState.EXPIRED):return order
        if self._fault(InternalPaperFaultType.CANCEL_TIMEOUT,client):self._event("cancel_timeout",client,{}); order=replace(order,state=VenueOrderState.CANCEL_PENDING,updated_at_utc=at_utc,venue_sequence=self._sequence); self._orders[client]=order; raise VenueTimeout("cancel timeout; cancellation outcome is unknown")
        self._event("cancel_pending",client,{}); order=replace(order,state=VenueOrderState.CANCEL_PENDING,updated_at_utc=at_utc,venue_sequence=self._sequence); self._orders[client]=order; return order
    def complete_cancel(self,client,at_utc):
        order=self._orders[client];self._begin_command("cancel_confirm",client,at_utc,{"venue_order_id":order.venue_order_id},submission_id=order.submission_id,idempotency_key=f"cancel-confirm:{order.venue_order_id}")
        if order.state is VenueOrderState.CANCELLED:return order
        if order.state is not VenueOrderState.CANCEL_PENDING:raise ValueError("cancel acknowledgement requires cancel_pending")
        self._event("cancelled",client,{}); order=replace(order,state=VenueOrderState.CANCELLED,updated_at_utc=at_utc,venue_sequence=self._sequence); self._orders[client]=order; self._release_reservation(client); return order
    def query_order(self,client_order_id):
        self.query_call_count+=1
        return self._orders.get(client_order_id)
    def list_recent_orders(self):return tuple(sorted(self._orders.values(),key=lambda o:(o.updated_at_utc,o.client_order_id)))
    def list_open_orders(self):
        active={VenueOrderState.PENDING_ACK,VenueOrderState.ACKNOWLEDGED,VenueOrderState.PARTIALLY_FILLED,VenueOrderState.CANCEL_PENDING,VenueOrderState.UNKNOWN_PENDING_RECOVERY}
        return tuple(sorted((o for o in self._orders.values() if o.state in active),key=lambda o:(o.created_at_utc,o.client_order_id)))
    def fetch_balances(self):
        reserved={currency:sum((r["amount"] for r in self._reservations.values() if r["currency"]==currency),Decimal(0)) for currency in self._balances}
        return tuple(VenueBalance(k,v,v-reserved[k],reserved[k]) for k,v in sorted(self._balances.items()))
    def fetch_positions(self):return tuple(sorted(self._positions.values(),key=lambda p:p.series_identity.series_identity_sha256))
    def fetch_fills(self):return tuple(sorted(self._fills.values(),key=lambda f:(f.filled_at_utc,f.venue_sequence,f.venue_fill_id)))
    def fetch_account_snapshot(self,paper_run_id,at_utc):
        stale=self._fault(InternalPaperFaultType.STALE_SNAPSHOT,None); lag=self._fault(InternalPaperFaultType.BALANCE_LAG,None) or self._fault(InternalPaperFaultType.POSITION_LAG,None)
        if lag and self._last_snapshot:return self._last_snapshot
        as_of=at_utc-timedelta(seconds=120) if stale else at_utc; status=AccountSnapshotStatus.STALE if stale else AccountSnapshotStatus.FRESH
        snap=PaperAccountSnapshot(paper_run_id,self.account_reference,status,at_utc,as_of,"spot",self.fetch_balances(),self.fetch_positions(),tuple(o.client_order_id for o in self.list_open_orders()),self._sequence,"internal_paper_venue"); self._last_snapshot=snap; return snap
    def reconstruct_durable(self,submissions,events):
        """Replay only PostgreSQL internal-venue events into a fresh process."""
        submission_by_client={s.client_order_id:s for s in submissions};self._balances=dict(self._initial_balances);self._positions={};self._reservations={};self._orders={};self._venue_ids={};self._fills={};self._events=[];self._sequence=0
        unknown_clients=set();repository=self._repository;faults=self._faults;self._repository=None;self._faults=()
        try:
            for row in events:
                kind=str(row["event_type"]);client=None if row.get("client_order_id") is None else str(row["client_order_id"]);details=dict(row.get("details_jsonb") or {});sequence=int(row["venue_sequence"]);at=row["occurred_at_utc"]
                if kind=="fault" and details.get("fault_type")==InternalPaperFaultType.UNKNOWN_SUBMISSION.value:unknown_clients.add(client)
                elif kind=="submitted":
                    submission=submission_by_client[client];venue_id=str(details["venue_order_id"]);self._reserve(submission);state=VenueOrderState.UNKNOWN_PENDING_RECOVERY if client in unknown_clients else VenueOrderState.PENDING_ACK;order=VenueOrder(submission.paper_run_id,submission.submission_id,client,venue_id,submission.series_identity,submission.side,submission.order_type,submission.time_in_force,submission.accounting_mode,submission.quantity,Decimal(0),None,state,submission.submitted_at_utc,at,sequence,submission.economics_sha256,submission.limit_price,submission.stop_price);self._orders[client]=order;self._venue_ids[venue_id]=client
                elif kind=="acknowledged" and client in self._orders:self._orders[client]=replace(self._orders[client],state=VenueOrderState.ACKNOWLEDGED,updated_at_utc=at,venue_sequence=sequence)
                elif kind=="fill" and client in self._orders:
                    self._sequence=sequence-1;self.fill(client,Decimal(str(details["quantity"])),Decimal(str(details["price"])),at,venue_fill_id=str(details["venue_fill_id"]),fee_currency=str(details["fee_currency"]))
                elif kind=="rejected" and client in self._orders:self._orders[client]=replace(self._orders[client],state=VenueOrderState.REJECTED,updated_at_utc=at,venue_sequence=sequence,reject_reason=str(details.get("reason") or "rejected"));self._release_reservation(client)
                elif kind=="expired" and client in self._orders:self._orders[client]=replace(self._orders[client],state=VenueOrderState.EXPIRED,updated_at_utc=at,venue_sequence=sequence);self._release_reservation(client)
                elif kind in ("cancel_pending","cancel_timeout") and client in self._orders:self._orders[client]=replace(self._orders[client],state=VenueOrderState.CANCEL_PENDING,updated_at_utc=at,venue_sequence=sequence)
                elif kind=="cancelled" and client in self._orders:self._orders[client]=replace(self._orders[client],state=VenueOrderState.CANCELLED,updated_at_utc=at,venue_sequence=sequence);self._release_reservation(client)
                self._sequence=max(self._sequence,sequence)
        finally:self._repository=repository;self._faults=faults
        self._events=[{"sequence":int(x["venue_sequence"]),"kind":str(x["event_type"]),"client_order_id":x.get("client_order_id"),"details":dict(x.get("details_jsonb") or {}),"occurred_at_utc":x["occurred_at_utc"],"internal_venue_event_id":x["internal_venue_event_id"],"record_sha256":str(x["record_sha256"])} for x in events];self._sequence=max((int(x["venue_sequence"]) for x in events),default=0);return self
    def reconstruct(self,orders,fills,events,*,balances=None,positions=None,reservations=None):
        self._orders={o.client_order_id:o for o in orders}; self._venue_ids={o.venue_order_id:o.client_order_id for o in orders}; self._fills={f.venue_fill_id:f for f in fills}; self._events=list(events)
        self._sequence=max((*[e["sequence"] for e in events],*[o.venue_sequence for o in orders],*[f.venue_sequence for f in fills]),default=0)
        if balances is not None:self._balances={str(k).upper():Decimal(v) for k,v in balances.items()}
        if positions is not None:self._positions=dict(positions)
        if reservations is not None:
            self._reservations={str(k):{"currency":v.currency,"amount":Decimal(v.amount),"original_quantity":Decimal(v.original_quantity),"remaining_quantity":Decimal(v.remaining_quantity)} for k,v in reservations.items()}
        return self
