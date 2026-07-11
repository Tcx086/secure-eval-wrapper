"""Provider-neutral PaperBroker using the shared Broker contract."""
from __future__ import annotations
from dataclasses import replace
from decimal import Decimal
from secure_eval_wrapper.data_collection.hashing import sha256_payload
from secure_eval_wrapper.execution.broker import Broker,BrokerResult
from secure_eval_wrapper.execution.models import OrderIntent,RiskDecisionStatus
from .accounting import PaperAccounting
from .enums import PaperOrderState,VenueOrderState
from .models import PaperOrderSubmission,deterministic_paper_uuid
from .rate_limits import PaperRateLimiter
from .venue import UnknownSubmissionResult,VenueTimeout

class PaperBroker(Broker):
    def __init__(self,*,configuration,manifest,approval,venue,accounting:PaperAccounting,kill_switch,clock,audit_callback=None):
        if manifest.approval_id!=approval.approval_id or manifest.configuration_sha256!=configuration.config_sha256:raise ValueError("PaperBroker requires an approved bound manifest")
        self.configuration=configuration; self.manifest=manifest; self.approval=approval; self.venue=venue; self.accounting=accounting; self.kill_switch=kill_switch; self.clock=clock; self.audit_callback=audit_callback; self._submissions={}; self._orders={}; self._fills={}; self.daily_submitted_notional=Decimal(0); self.rate_limiter=PaperRateLimiter(orders_per_minute=configuration.maximum_orders_per_minute,cancellations_per_minute=configuration.maximum_cancellations_per_minute,clock=clock)
    def _audit(self,kind,value):
        if self.audit_callback:self.audit_callback(kind,value)
    def _prepare(self,intent):
        client="sew"+deterministic_paper_uuid("client-order",{"run":self.manifest.paper_run_id,"intent":intent.order_intent_id}).hex[:29]; economics=sha256_payload({"series_identity":intent.series_identity.as_dict(),"side":intent.side,"order_type":intent.order_type,"time_in_force":intent.time_in_force,"accounting_mode":intent.accounting_mode,"quantity":intent.quantity,"limit_price":intent.limit_price,"stop_price":intent.stop_price})
        return PaperOrderSubmission(self.manifest.paper_run_id,self.manifest.manifest_id,self.approval.approval_id,intent.order_intent_id,client,client,intent.series_identity,intent.side,intent.order_type,intent.time_in_force,intent.accounting_mode,intent.quantity,intent.reference_price,intent.quantity*intent.reference_price,self.clock(),economics,limit_price=intent.limit_price,stop_price=intent.stop_price)
    def submit_order_intent(self,intent,risk_decision):
        if not self.kill_switch.accepts_new_orders:raise PermissionError("paper kill switch rejects new submissions")
        if risk_decision.status is not RiskDecisionStatus.ACCEPTED:raise PermissionError("paper order requires accepted pre-submit risk")
        if intent.series_identity.canonical_symbol not in self.configuration.allowed_instruments or intent.order_type not in self.configuration.allowed_order_types:raise PermissionError("paper order is outside manifest allowlist")
        submission=self._prepare(intent); notional=submission.submitted_notional
        if notional>self.configuration.maximum_order_notional or self.daily_submitted_notional+notional>min(self.configuration.maximum_daily_submitted_notional,self.approval.maximum_approved_total_notional):raise PermissionError("paper notional limit exceeded")
        existing=self._submissions.get(submission.client_order_id)
        if existing:
            if existing.economics_sha256!=submission.economics_sha256:raise ValueError("stable client order ID economics conflict")
            return BrokerResult((self._orders.get(submission.client_order_id),) if submission.client_order_id in self._orders else ())
        self.rate_limiter.acquire("submit"); self.accounting.reserve(submission); self._submissions[submission.client_order_id]=submission; self.daily_submitted_notional+=notional; self._audit("submission_attempt",submission)
        try:
            order=self.venue.submit_order(submission); self._orders[submission.client_order_id]=order; self._submissions[submission.client_order_id]=replace(submission,state=PaperOrderState.PENDING_ACK); self.rate_limiter.record_result(True); self._audit("submission_result",order); return BrokerResult((order,))
        except UnknownSubmissionResult:
            self._submissions[submission.client_order_id]=replace(submission,state=PaperOrderState.SUBMISSION_UNKNOWN); self.rate_limiter.record_result(False); self._audit("submission_unknown",self._submissions[submission.client_order_id]); return BrokerResult()
        except VenueTimeout:
            self._submissions[submission.client_order_id]=replace(submission,state=PaperOrderState.SUBMISSION_UNKNOWN); self.rate_limiter.record_result(False); self._audit("submission_unknown",self._submissions[submission.client_order_id]); return BrokerResult()
        except Exception:
            self.accounting.release(submission.client_order_id); self.rate_limiter.record_result(False); raise
    def query_order(self,client_order_id):
        order=self.venue.query_order(client_order_id)
        if order is not None:
            prior=self._orders.get(client_order_id)
            if prior and order.cumulative_filled_quantity<prior.cumulative_filled_quantity:raise ValueError("venue cumulative fill quantity decreased")
            if prior and prior.state in (VenueOrderState.FILLED,VenueOrderState.CANCELLED,VenueOrderState.REJECTED,VenueOrderState.EXPIRED) and order.state!=prior.state:raise ValueError("venue terminal order reverted to active")
            self._orders[client_order_id]=order
            mapping={VenueOrderState.PENDING_ACK:PaperOrderState.PENDING_ACK,VenueOrderState.ACKNOWLEDGED:PaperOrderState.ACKNOWLEDGED,VenueOrderState.PARTIALLY_FILLED:PaperOrderState.PARTIALLY_FILLED,VenueOrderState.FILLED:PaperOrderState.FILLED,VenueOrderState.CANCEL_PENDING:PaperOrderState.CANCEL_PENDING,VenueOrderState.CANCELLED:PaperOrderState.CANCELLED,VenueOrderState.REJECTED:PaperOrderState.REJECTED,VenueOrderState.EXPIRED:PaperOrderState.EXPIRED,VenueOrderState.UNKNOWN_PENDING_RECOVERY:PaperOrderState.PENDING_RECOVERY}
            if client_order_id in self._submissions:self._submissions[client_order_id]=replace(self._submissions[client_order_id],state=mapping[order.state])
            if order.state in (VenueOrderState.CANCELLED,VenueOrderState.REJECTED,VenueOrderState.EXPIRED):self.accounting.release(client_order_id)
        return order
    def list_open_orders(self):return self.venue.list_open_orders()
    def active_orders(self,*,series_identity=None):
        values=self.list_open_orders()
        if series_identity is not None:values=tuple(o for o in values if o.series_identity.series_identity_sha256==series_identity.series_identity_sha256)
        return values
    def cancel_paper_order(self,client_order_id,*,at_utc,reason):
        self.rate_limiter.acquire("cancel"); self._audit("cancel_intent",{"client_order_id":client_order_id,"at_utc":at_utc,"reason":reason})
        try:
            order=self.venue.cancel_order(client_order_id,at_utc)
        except Exception:
            self.rate_limiter.record_result(False)
            raise
        self._orders[client_order_id]=order; self.rate_limiter.record_result(True); return order
    def cancel_order(self,order_id,*,cancelled_at_utc,reason):
        client=next((c for c,o in self._orders.items() if o.venue_order_id==str(order_id) or o.submission_id==order_id),str(order_id)); return BrokerResult((self.cancel_paper_order(client,at_utc=cancelled_at_utc,reason=reason),))
    def sync_fills(self):
        applied=[]
        for fill in self.venue.fetch_fills():
            if fill.venue_fill_id not in self._fills:
                if self.accounting.apply_fill(fill):applied.append(fill); self._audit("confirmed_fill",fill)
                self._fills[fill.venue_fill_id]=fill
            self.query_order(fill.client_order_id)
        return tuple(applied)
    def fetch_balances(self):return self.venue.fetch_balances()
    def fetch_positions(self):return self.venue.fetch_positions()
    def fetch_fills(self):return self.venue.fetch_fills()
    def fetch_account_snapshot(self):return self.venue.fetch_account_snapshot(self.manifest.paper_run_id,self.clock())
    def reconcile(self,reconciliation_engine):
        venue=self.fetch_account_snapshot(); local=self.accounting.snapshot(at_utc=self.clock(),venue_sequence=venue.venue_sequence); return reconciliation_engine.reconcile(paper_run_id=self.manifest.paper_run_id,local_snapshot=local,venue_snapshot=venue,local_orders=tuple(self._orders.values()),venue_orders=tuple(self.venue._orders.values()) if hasattr(self.venue,"_orders") else self.list_open_orders(),local_fills=tuple(self._fills.values()),venue_fills=self.fetch_fills(),at_utc=self.clock())
    def process_bar_open(self,*,series_identity,timestamp_utc,open_price,risk_check=None):
        if hasattr(self.venue,"on_market_event"):self.venue.on_market_event(at_utc=timestamp_utc,prices={series_identity.canonical_symbol:open_price})
        return BrokerResult(tuple(self._orders.values()),self.sync_fills())
    def process_completed_bar(self,*,series_identity,timestamp_utc,open_price,high,low,close,risk_check=None):return self.process_bar_open(series_identity=series_identity,timestamp_utc=timestamp_utc,open_price=close,risk_check=risk_check)
    def expire_remaining_orders(self,*,expired_at_utc):
        updates=[]
        for o in self.list_open_orders():
            if hasattr(self.venue,"expire"):updates.append(self.venue.expire(o.client_order_id,expired_at_utc)); self.accounting.release(o.client_order_id)
        return BrokerResult(tuple(updates))
    @property
    def submissions(self):return tuple(self._submissions.values())
    @property
    def local_orders(self):return tuple(self._orders.values())
    @property
    def local_fills(self):return tuple(self._fills.values())
