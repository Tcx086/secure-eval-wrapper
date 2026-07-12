"""Provider-neutral PaperBroker using the shared Broker contract."""
from __future__ import annotations
from dataclasses import replace
import copy
from decimal import Decimal
from secure_eval_wrapper.data_collection.hashing import sha256_payload
from secure_eval_wrapper.execution.broker import Broker,BrokerResult
from secure_eval_wrapper.execution.models import OrderIntent,RiskDecisionStatus
from .accounting import PaperAccounting
from .enums import PaperOrderState,VenueOrderState
from .models import PaperOrderSubmission,PaperReconciliationBundle,PaperRecoveryObservationBundle,deterministic_paper_uuid
from .rate_limits import PaperRateLimiter
from .venue import ExplicitVenueRejection,UnknownSubmissionResult,VenueTimeout
from .durable_repository import DispatchNotClaimable

class PaperBroker(Broker):
    def __init__(self,*,configuration,manifest,approval,venue,accounting:PaperAccounting,kill_switch,clock,audit_callback=None,repository=None,fixture_mode=False,worker_id="paper-worker",crash_hook=None):
        if manifest.approval_id!=approval.approval_id or manifest.configuration_sha256!=configuration.config_sha256:raise ValueError("PaperBroker requires an approved bound manifest")
        if configuration.persistence_required and repository is None and not fixture_mode:raise ValueError("persistent PaperBroker requires PostgreSQL durable repository")
        self.repository=repository;self.fixture_mode=fixture_mode;self.worker_id=worker_id;self.crash_hook=crash_hook
        self.configuration=configuration; self.manifest=manifest; self.approval=approval; self.venue=venue; self.accounting=accounting; self.kill_switch=kill_switch; self.clock=clock; self.audit_callback=audit_callback; self._submissions={}; self._orders={}; self._fills={}; self.daily_submitted_notional=Decimal(0); self.rate_limiter=PaperRateLimiter(orders_per_minute=configuration.maximum_orders_per_minute,cancellations_per_minute=configuration.maximum_cancellations_per_minute,clock=clock)
        if repository is not None and hasattr(venue,"bind_persistence"):venue.bind_persistence(repository,manifest.paper_run_id)
    def _audit(self,kind,value):
        if self.audit_callback:self.audit_callback(kind,value)
    def _prepare(self,intent):
        client="sew"+deterministic_paper_uuid("client-order",{"run":self.manifest.paper_run_id,"intent":intent.order_intent_id}).hex[:29]; economics=sha256_payload({"series_identity":intent.series_identity.as_dict(),"side":intent.side,"order_type":intent.order_type,"time_in_force":intent.time_in_force,"accounting_mode":intent.accounting_mode,"quantity":intent.quantity,"limit_price":intent.limit_price,"stop_price":intent.stop_price})
        return PaperOrderSubmission(self.manifest.paper_run_id,self.manifest.manifest_id,self.approval.approval_id,intent.order_intent_id,client,client,intent.series_identity,intent.side,intent.order_type,intent.time_in_force,intent.accounting_mode,intent.quantity,intent.reference_price,intent.quantity*intent.reference_price,self.clock(),economics,limit_price=intent.limit_price,stop_price=intent.stop_price)
    def submit_order_intent(self,intent,risk_decision,market_evidence=None):
        if not self.kill_switch.accepts_new_orders:raise PermissionError("paper kill switch rejects new submissions")
        if self.repository is not None:return self._durable_submit(intent,risk_decision,market_evidence=market_evidence)
        if not self.fixture_mode:raise RuntimeError("venue side effects require PostgreSQL durable dispatch; use explicit fixture_mode only for offline fixtures")
        # Explicit fixture-only path; operational modes always return above.
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
        query_started=self.clock();order=self.venue.query_order(client_order_id)
        if order is None:
            if self.repository is None:return None
            submission=next((s for s in self.repository.typed_submissions(self.manifest.paper_run_id) if s.client_order_id==client_order_id),None)
            if submission is None:return None
            bundle=self._recovery_evidence(client_order_id,query_started_at_utc=query_started);self.repository.record_recovery_observation_bundle(bundle);dispatch=self.repository._fetchone("SELECT * FROM execution.paper_dispatch_outbox WHERE submission_id=%s",(submission.submission_id,))
            if dispatch and dispatch["state"] in ("dispatch_claimed","unknown"):
                active=dispatch["state"]=="dispatch_claimed" and dispatch.get("claim_lease_expires_at_utc") and dispatch["claim_lease_expires_at_utc"]>bundle.query_completed_at_utc and dispatch.get("claimed_by")==self.worker_id
                if active:self.repository.complete_dispatch(submission,claim_token=dispatch.get("claim_token"),outcome="unknown",at_utc=bundle.query_completed_at_utc,classification="active_claim_query_inconclusive",evidence_sha256=bundle.record_sha256,worker_id=self.worker_id)
                else:
                    try:token=self.repository.claim_dispatch_recovery(submission,worker_id=self.worker_id,at_utc=bundle.query_completed_at_utc)
                    except DispatchNotClaimable:self._audit("dispatch_recovery_deferred",{"submission_id":submission.submission_id});token=None
                    if token is not None:self.repository.complete_dispatch_recovery(submission,recovery_claim_token=token,at_utc=bundle.query_completed_at_utc,order=None,evidence_sha256=bundle.record_sha256)
            cancel=self.repository._fetchone("SELECT * FROM execution.paper_cancel_outbox WHERE submission_id=%s",(submission.submission_id,))
            if cancel and cancel["state"] in ("cancel_claimed","cancel_unknown"):
                active=cancel["state"]=="cancel_claimed" and cancel.get("claim_lease_expires_at_utc") and cancel["claim_lease_expires_at_utc"]>bundle.query_completed_at_utc and cancel.get("claimed_by")==self.worker_id
                if active:self.repository.complete_cancel(submission,claim_token=cancel.get("claim_token"),confirmed=False,at_utc=bundle.query_completed_at_utc,evidence_sha256=bundle.record_sha256,worker_id=self.worker_id)
                else:
                    try:token=self.repository.claim_cancel_recovery(submission,worker_id=self.worker_id,at_utc=bundle.query_completed_at_utc)
                    except DispatchNotClaimable:self._audit("cancel_recovery_deferred",{"submission_id":submission.submission_id});token=None
                    if token is not None:self.repository.complete_cancel_recovery(submission,recovery_claim_token=token,at_utc=bundle.query_completed_at_utc,order=None,evidence_sha256=bundle.record_sha256)
            return None
        prior=self._orders.get(client_order_id)
        if prior and order.cumulative_filled_quantity<prior.cumulative_filled_quantity:raise ValueError("venue cumulative fill quantity decreased")
        if prior and prior.state in (VenueOrderState.FILLED,VenueOrderState.CANCELLED,VenueOrderState.REJECTED,VenueOrderState.EXPIRED) and order.state!=prior.state:raise ValueError("venue terminal order reverted to active")
        self._orders[client_order_id]=order
        mapping={VenueOrderState.PENDING_ACK:PaperOrderState.PENDING_ACK,VenueOrderState.ACKNOWLEDGED:PaperOrderState.ACKNOWLEDGED,VenueOrderState.PARTIALLY_FILLED:PaperOrderState.PARTIALLY_FILLED,VenueOrderState.FILLED:PaperOrderState.FILLED,VenueOrderState.CANCEL_PENDING:PaperOrderState.CANCEL_PENDING,VenueOrderState.CANCELLED:PaperOrderState.CANCELLED,VenueOrderState.REJECTED:PaperOrderState.REJECTED,VenueOrderState.EXPIRED:PaperOrderState.EXPIRED,VenueOrderState.UNKNOWN_PENDING_RECOVERY:PaperOrderState.PENDING_RECOVERY}
        if client_order_id in self._submissions:self._submissions[client_order_id]=replace(self._submissions[client_order_id],state=mapping[order.state])
        if self.repository is None:
            if order.state in (VenueOrderState.CANCELLED,VenueOrderState.REJECTED,VenueOrderState.EXPIRED):self.accounting.release(client_order_id)
            return order
        submission=next((s for s in self.repository.typed_submissions(self.manifest.paper_run_id) if s.client_order_id==client_order_id),None)
        if submission is None:raise ValueError("venue observation has no durable submission")
        bundle=self._recovery_evidence(client_order_id,queried_order=order,query_started_at_utc=query_started)
        if bundle.fill_evidence_complete:self._durable_sync_fills(recovery_bundle=bundle)
        else:
            self.repository.record_recovery_observation_bundle(bundle);self.repository.persist_order_observation(submission,order,observed_at_utc=bundle.query_completed_at_utc,source="ordinary_query",query_id=bundle.query_id,evidence_sha256=bundle.record_sha256,internal_venue_event_id=getattr(self.venue,"_latest_internal_event_id",None))
        dispatch=self.repository._fetchone("SELECT * FROM execution.paper_dispatch_outbox WHERE submission_id=%s",(submission.submission_id,))
        if dispatch and dispatch["state"] in ("dispatch_claimed","unknown") and order.state is not VenueOrderState.UNKNOWN_PENDING_RECOVERY:
            active=dispatch["state"]=="dispatch_claimed" and dispatch.get("claim_lease_expires_at_utc") and dispatch["claim_lease_expires_at_utc"]>bundle.query_completed_at_utc and dispatch.get("claimed_by")==self.worker_id
            if active:self.repository.complete_dispatch(submission,claim_token=dispatch.get("claim_token"),outcome="recovered",at_utc=bundle.query_completed_at_utc,order=order,classification="active_claim_query_evidence",evidence_sha256=bundle.record_sha256,worker_id=self.worker_id)
            else:
                try:token=self.repository.claim_dispatch_recovery(submission,worker_id=self.worker_id,at_utc=bundle.query_completed_at_utc)
                except DispatchNotClaimable:self._audit("dispatch_recovery_deferred",{"submission_id":submission.submission_id});token=None
                if token is not None:self.repository.complete_dispatch_recovery(submission,recovery_claim_token=token,at_utc=bundle.query_completed_at_utc,order=order,evidence_sha256=bundle.record_sha256)
        cancel=self.repository._fetchone("SELECT * FROM execution.paper_cancel_outbox WHERE submission_id=%s",(submission.submission_id,))
        if cancel and cancel["state"] in ("cancel_claimed","cancel_unknown"):
            active=cancel["state"]=="cancel_claimed" and cancel.get("claim_lease_expires_at_utc") and cancel["claim_lease_expires_at_utc"]>bundle.query_completed_at_utc and cancel.get("claimed_by")==self.worker_id
            if active and order.state in (VenueOrderState.CANCELLED,VenueOrderState.FILLED,VenueOrderState.EXPIRED,VenueOrderState.REJECTED):self.repository.complete_cancel(submission,claim_token=cancel.get("claim_token"),confirmed=order.state is VenueOrderState.CANCELLED,at_utc=bundle.query_completed_at_utc,evidence_sha256=bundle.record_sha256,worker_id=self.worker_id)
            elif not active:
                try:token=self.repository.claim_cancel_recovery(submission,worker_id=self.worker_id,at_utc=bundle.query_completed_at_utc)
                except DispatchNotClaimable:self._audit("cancel_recovery_deferred",{"submission_id":submission.submission_id});token=None
                if token is not None:self.repository.complete_cancel_recovery(submission,recovery_claim_token=token,at_utc=bundle.query_completed_at_utc,order=order,evidence_sha256=bundle.record_sha256)
        if not bundle.fill_evidence_complete:self._audit("recovery_blocked_incomplete_fill_evidence",bundle)
        self.hydrate_from_postgres();return order
    def list_open_orders(self):return self.venue.list_open_orders()
    def active_orders(self,*,series_identity=None):
        values=self.list_open_orders()
        if series_identity is not None:values=tuple(o for o in values if o.series_identity.series_identity_sha256==series_identity.series_identity_sha256)
        return values
    def cancel_paper_order(self,client_order_id,*,at_utc,reason):
        self.rate_limiter.acquire("cancel"); self._audit("cancel_intent",{"client_order_id":client_order_id,"at_utc":at_utc,"reason":reason})
        if self.repository is not None:return self._durable_cancel(client_order_id,at_utc=at_utc,reason=reason)
        if not self.fixture_mode:raise RuntimeError("venue cancellation requires PostgreSQL durable cancel outbox")
        try:
            order=self.venue.cancel_order(client_order_id,at_utc)
        except Exception:
            self.rate_limiter.record_result(False)
            raise
        self._orders[client_order_id]=order; self.rate_limiter.record_result(True); return order
    def cancel_order(self,order_id,*,cancelled_at_utc,reason):
        client=next((c for c,o in self._orders.items() if o.venue_order_id==str(order_id) or o.submission_id==order_id),str(order_id)); return BrokerResult((self.cancel_paper_order(client,at_utc=cancelled_at_utc,reason=reason),))
    def sync_fills(self):
        if self.repository is not None:return self._durable_sync_fills()
        if not self.fixture_mode:raise RuntimeError("fill synchronization requires PostgreSQL accounting authority")
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
        venue_snapshot=self.fetch_account_snapshot()
        local_snapshot=self.accounting.snapshot(at_utc=self.clock(),venue_sequence=venue_snapshot.venue_sequence)
        local_orders=tuple(self._orders.values())
        venue_orders=tuple(self.venue._orders.values()) if hasattr(self.venue,"_orders") else self.list_open_orders()
        local_fills=tuple(self._fills.values())
        venue_fills=self.fetch_fills()
        evaluated_at=self.clock()
        authority_checks=() if self.repository is None else self.repository.reconciliation_authority_checks(self.manifest.paper_run_id,accounting_reservations=self.accounting.reservations,venue_reservations=getattr(self.venue,"_reservations",{}),venue_sequence=getattr(self.venue,"sequence",venue_snapshot.venue_sequence or 0))
        reconciliation,differences=reconciliation_engine.reconcile(paper_run_id=self.manifest.paper_run_id,local_snapshot=local_snapshot,venue_snapshot=venue_snapshot,local_orders=local_orders,venue_orders=venue_orders,local_fills=local_fills,venue_fills=venue_fills,at_utc=evaluated_at,maximum_snapshot_age_seconds=self.configuration.maximum_reconciliation_age_seconds,authority_checks=authority_checks)
        return PaperReconciliationBundle(local_snapshot,venue_snapshot,local_orders,venue_orders,local_fills,venue_fills,reconciliation,differences,evaluated_at,{"maximum_snapshot_age_seconds":self.configuration.maximum_reconciliation_age_seconds},{"prior_state":self.kill_switch.current.state.value,"trigger_required":reconciliation.status.value in ("blocked","unknown")})
    def process_bar_open(self,*,series_identity,timestamp_utc,open_price,risk_check=None):
        if hasattr(self.venue,"on_market_event"):self.venue.on_market_event(at_utc=timestamp_utc,prices={series_identity.canonical_symbol:open_price})
        return BrokerResult(tuple(self._orders.values()),self.sync_fills())
    def process_completed_bar(self,*,series_identity,timestamp_utc,open_price,high,low,close,risk_check=None):return self.process_bar_open(series_identity=series_identity,timestamp_utc=timestamp_utc,open_price=close,risk_check=risk_check)
    def expire_remaining_orders(self,*,expired_at_utc):
        updates=[]
        for order in self.list_open_orders():
            if not hasattr(self.venue,"expire"):continue
            if self.repository is None:
                if not self.fixture_mode:raise RuntimeError("persistent expiry requires a durable PostgreSQL command")
                updates.append(self.venue.expire(order.client_order_id,expired_at_utc));self.accounting.release(order.client_order_id);continue
            submission=self._submissions.get(order.client_order_id) or next((s for s in self.repository.typed_submissions(self.manifest.paper_run_id) if s.client_order_id==order.client_order_id),None)
            if submission is None:raise ValueError("expiry has no durable submission")
            self.repository.prepare_expiry(submission,at_utc=expired_at_utc);self._crash("after_expiry_intent_before_claim");token=self.repository.claim_expiry(submission,worker_id=self.worker_id,at_utc=expired_at_utc);self._crash("after_expiry_claim_before_venue")
            try:
                observed=self.venue.expire(order.client_order_id,expired_at_utc);self._crash("after_venue_expiry_before_outcome");self.repository.complete_expiry(submission,claim_token=token,confirmed=True,at_utc=expired_at_utc,order=observed,evidence_sha256=observed.record_sha256);updates.append(observed)
            except Exception as exc:
                evidence=sha256_payload({"type":type(exc).__name__,"classification":"expiry_ambiguous"});self.repository.complete_expiry(submission,claim_token=token,confirmed=False,at_utc=expired_at_utc,evidence_sha256=evidence);raise
        if self.repository is not None:self.hydrate_from_postgres()
        return BrokerResult(tuple(updates))
    @property
    def submissions(self):return tuple(self._submissions.values())
    @property
    def local_orders(self):return tuple(self._orders.values())
    @property
    def local_fills(self):return tuple(self._fills.values())
    def _crash(self,point):
        if self.crash_hook:self.crash_hook(point)
    def _durable_submit(self,intent,risk_decision,*,market_evidence=None):
        submission,replay=self.repository.prepare_submission(configuration=self.configuration,approval=self.approval,manifest=self.manifest,intent=intent,risk_decision=risk_decision,now=self.clock(),market_evidence=market_evidence,evidence={"maximum_fee_bps":getattr(self.venue,"fee_bps",Decimal("10"))})
        self._submissions[submission.client_order_id]=submission;self.accounting=self.repository.hydrate_accounting(self.manifest.paper_run_id)
        if replay:
            orders={o.client_order_id:o for o in self.repository.typed_orders(self.manifest.paper_run_id)};self._orders.update(orders)
            return self.resume_submission(submission)
        self._crash("after_durable_intent_before_claim");token=self.repository.claim_dispatch(submission,worker_id=self.worker_id,at_utc=self.clock());self._crash("after_dispatch_claim_before_venue")
        try:
            order=self.venue.submit_order(submission);self._crash("after_venue_accept_before_outcome");self.repository.complete_dispatch(submission,claim_token=token,outcome="acknowledged",at_utc=self.clock(),order=order,classification="venue_ack",evidence_sha256=order.record_sha256,worker_id=self.worker_id);self.repository.persist_order_observation(submission,order,observed_at_utc=self.clock(),source="submit_response",evidence_sha256=order.record_sha256,internal_venue_event_id=getattr(self.venue,"_latest_internal_event_id",None));self._orders[submission.client_order_id]=order;self._submissions[submission.client_order_id]=replace(submission,state=PaperOrderState.PENDING_ACK);return BrokerResult((order,))
        except ExplicitVenueRejection as exc:
            evidence=sha256_payload({"type":type(exc).__name__,"message":str(exc)});self.repository.complete_dispatch(submission,claim_token=token,outcome="explicitly_rejected",at_utc=self.clock(),classification="explicit_venue_rejection",evidence_sha256=evidence,worker_id=self.worker_id);self._submissions[submission.client_order_id]=replace(submission,state=PaperOrderState.REJECTED);self.accounting=self.repository.hydrate_accounting(self.manifest.paper_run_id);return BrokerResult()
        except (UnknownSubmissionResult,VenueTimeout,Exception) as exc:
            evidence=sha256_payload({"type":type(exc).__name__,"classification":"ambiguous_after_claim"});self._crash("after_ambiguous_transport_before_outcome");self.repository.complete_dispatch(submission,claim_token=token,outcome="unknown",at_utc=self.clock(),classification="ambiguous_transport",evidence_sha256=evidence,worker_id=self.worker_id);self._submissions[submission.client_order_id]=replace(submission,state=PaperOrderState.SUBMISSION_UNKNOWN);return BrokerResult()
    def _recovery_evidence(self,client_order_id,*,queried_order=None,query_started_at_utc=None):
        started=query_started_at_utc or self.clock();order=queried_order if queried_order is not None else self.venue.query_order(client_order_id);recent=tuple(self.venue.list_recent_orders()) if hasattr(self.venue,"list_recent_orders") else tuple(self.venue._orders.values()) if hasattr(self.venue,"_orders") else tuple(self.venue.list_open_orders());fills=tuple(sorted((f for f in self.venue.fetch_fills() if f.client_order_id==client_order_id),key=lambda f:(f.venue_sequence,f.filled_at_utc,f.venue_fill_id)));open_orders=tuple(o for o in self.venue.list_open_orders() if o.client_order_id==client_order_id);balances=tuple(self.venue.fetch_balances());positions=tuple(self.venue.fetch_positions());completed=self.clock();snapshot=self.venue.fetch_account_snapshot(self.manifest.paper_run_id,completed);submission=next((s for s in self.repository.typed_submissions(self.manifest.paper_run_id) if s.client_order_id==client_order_id),None)
        if submission is None:raise ValueError("recovery evidence has no durable submission")
        observed=Decimal(0) if order is None else order.cumulative_filled_quantity;fill_quantity=sum((f.quantity for f in fills),Decimal(0));complete=order is None or order.state not in (VenueOrderState.PARTIALLY_FILLED,VenueOrderState.FILLED) or fill_quantity==observed;reason=None if complete else f"venue cumulative fill {observed} differs from complete fill evidence {fill_quantity}"
        return PaperRecoveryObservationBundle(self.manifest.paper_run_id,submission.submission_id,client_order_id,order,recent,open_orders,fills,balances,positions,snapshot,started,completed,complete,reason)
    def resume_submission(self,submission):
        dispatch=self.repository._fetchone("SELECT * FROM execution.paper_dispatch_outbox WHERE submission_id=%s",(submission.submission_id,))
        if dispatch is None:raise RuntimeError("persisted submission has no dispatch outbox")
        if dispatch["state"]=="prepared":
            token=self.repository.claim_dispatch(submission,worker_id=self.worker_id,at_utc=self.clock())
            try:
                order=self.venue.submit_order(submission);self.repository.complete_dispatch(submission,claim_token=token,outcome="acknowledged",at_utc=self.clock(),order=order,classification="restart_prepared_dispatch",evidence_sha256=order.record_sha256,worker_id=self.worker_id);self.repository.persist_order_observation(submission,order,observed_at_utc=self.clock(),source="restart_submit_response",evidence_sha256=order.record_sha256,internal_venue_event_id=getattr(self.venue,"_latest_internal_event_id",None));self._orders[submission.client_order_id]=order;return BrokerResult((order,))
            except ExplicitVenueRejection as exc:
                evidence=sha256_payload({"type":type(exc).__name__,"message":str(exc)});self.repository.complete_dispatch(submission,claim_token=token,outcome="explicitly_rejected",at_utc=self.clock(),classification="restart_explicit_rejection",evidence_sha256=evidence,worker_id=self.worker_id);return BrokerResult()
            except Exception as exc:
                evidence=sha256_payload({"type":type(exc).__name__,"classification":"restart_ambiguous"});self.repository.complete_dispatch(submission,claim_token=token,outcome="unknown",at_utc=self.clock(),classification="restart_ambiguous",evidence_sha256=evidence,worker_id=self.worker_id);return BrokerResult()
        if dispatch["state"] in ("dispatch_claimed","unknown"):
            order=self.query_order(submission.client_order_id);return BrokerResult((order,) if order is not None else ())
        orders={o.client_order_id:o for o in self.repository.typed_orders(self.manifest.paper_run_id)};self._orders.update(orders);return BrokerResult((orders[submission.client_order_id],) if submission.client_order_id in orders else ())
    def recover_unresolved(self):
        results=[]
        for submission in self.repository.typed_submissions(self.manifest.paper_run_id):
            dispatch=self.repository._fetchone("SELECT * FROM execution.paper_dispatch_outbox WHERE submission_id=%s",(submission.submission_id,));cancel=self.repository._fetchone("SELECT * FROM execution.paper_cancel_outbox WHERE submission_id=%s",(submission.submission_id,))
            try:
                if cancel and cancel["state"]=="cancel_requested":
                    token=self.repository.claim_cancel(submission,worker_id=self.worker_id,at_utc=self.clock());order=self.venue.cancel_order(submission.client_order_id,self.clock());confirmed=order.state is VenueOrderState.CANCELLED;self.repository.complete_cancel(submission,claim_token=token,confirmed=confirmed,at_utc=self.clock(),evidence_sha256=order.record_sha256,worker_id=self.worker_id);results.append((submission.submission_id,"cancel_confirmed" if confirmed else "cancel_unknown"));continue
                if cancel and cancel["state"] in ("cancel_claimed","cancel_unknown"):
                    order=self.query_order(submission.client_order_id);confirmed=order is not None and order.state in (VenueOrderState.CANCELLED,VenueOrderState.FILLED,VenueOrderState.EXPIRED,VenueOrderState.REJECTED);results.append((submission.submission_id,"cancel_recovered" if confirmed else "cancel_unknown"));continue
                if dispatch and dispatch["state"] in ("prepared","dispatch_claimed","unknown"):
                    self.resume_submission(submission);results.append((submission.submission_id,"dispatch_"+str(dispatch["state"])))
            except Exception as exc:
                results.append((submission.submission_id,"deferred:"+type(exc).__name__))
        self.hydrate_from_postgres();return tuple(results)
    def _durable_cancel(self,client_order_id,*,at_utc,reason):
        submission=self._submissions.get(client_order_id) or next((s for s in self.repository.typed_submissions(self.manifest.paper_run_id) if s.client_order_id==client_order_id),None)
        if submission is None:raise ValueError("cancel has no persisted submission")
        self.repository.prepare_cancel(submission,at_utc=at_utc,maximum_cancellations_per_minute=self.configuration.maximum_cancellations_per_minute);self._crash("after_cancel_intent_before_claim");token=self.repository.claim_cancel(submission,worker_id=self.worker_id,at_utc=at_utc);self._crash("after_cancel_claim_before_venue")
        try:
            order=self.venue.cancel_order(client_order_id,at_utc);self._crash("after_venue_cancel_before_outcome");self.repository.persist_order_observation(submission,order,observed_at_utc=self.clock(),source="cancel_response",evidence_sha256=order.record_sha256,internal_venue_event_id=getattr(self.venue,"_latest_internal_event_id",None));confirmed=order.state is VenueOrderState.CANCELLED;self.repository.complete_cancel(submission,claim_token=token,confirmed=confirmed,at_utc=self.clock(),evidence_sha256=order.record_sha256,worker_id=self.worker_id);self._orders[client_order_id]=order
            if confirmed:self.accounting=self.repository.hydrate_accounting(self.manifest.paper_run_id)
            return order
        except Exception as exc:
            evidence=sha256_payload({"type":type(exc).__name__,"classification":"cancel_ambiguous"});self.repository.complete_cancel(submission,claim_token=token,confirmed=False,at_utc=self.clock(),evidence_sha256=evidence,worker_id=self.worker_id);raise
    def _durable_sync_fills(self,recovery_bundle=None):
        try:
            with self.repository.transaction():
                if recovery_bundle is not None:self.repository.record_recovery_observation_bundle(recovery_bundle)
                result=self._durable_sync_fills_uncommitted(recovery_bundle=recovery_bundle)
                if recovery_bundle is not None and recovery_bundle.queried_order is not None:
                    submission=next(s for s in self.repository.typed_submissions(self.manifest.paper_run_id) if s.submission_id==recovery_bundle.submission_id);self.repository.persist_order_observation(submission,recovery_bundle.queried_order,observed_at_utc=recovery_bundle.query_completed_at_utc,source="recovery_query",query_id=recovery_bundle.query_id,evidence_sha256=recovery_bundle.record_sha256,internal_venue_event_id=getattr(self.venue,"_latest_internal_event_id",None))
                return result
        except Exception:
            self.hydrate_from_postgres();raise
    def _durable_sync_fills_uncommitted(self,recovery_bundle=None):
        from .models import PaperLifecycleEvent
        from .reconciliation import PaperReconciliationEngine
        applied=[];fills=tuple(self.venue.fetch_fills()) if recovery_bundle is None else recovery_bundle.fills;persisted={o.client_order_id:o for o in self.repository.typed_orders(self.manifest.paper_run_id)};applied_economics={}
        for value in self.repository.typed_fills(self.manifest.paper_run_id):
            quantity,notional=applied_economics.get(value.client_order_id,(Decimal(0),Decimal(0)));applied_economics[value.client_order_id]=(quantity+value.quantity,notional+value.quantity*value.price)
        for fill in sorted(fills,key=lambda f:(f.venue_sequence,f.filled_at_utc,f.venue_fill_id)):
            if fill.fill_id in self.accounting.applied_fill_ids:continue
            candidate=copy.deepcopy(self.accounting)
            if not candidate.apply_fill(fill):continue
            venue_order=(recovery_bundle.queried_order if recovery_bundle is not None and recovery_bundle.queried_order is not None and recovery_bundle.queried_order.client_order_id==fill.client_order_id else self.venue.query_order(fill.client_order_id))
            if venue_order is None:continue
            prior_quantity,prior_notional=applied_economics.get(fill.client_order_id,(Decimal(0),Decimal(0)));cumulative=prior_quantity+fill.quantity;average=(prior_notional+fill.quantity*fill.price)/cumulative;state=VenueOrderState.FILLED if cumulative==venue_order.quantity else VenueOrderState.PARTIALLY_FILLED;order=replace(venue_order,cumulative_filled_quantity=cumulative,average_fill_price=average,state=state,updated_at_utc=fill.filled_at_utc,venue_sequence=fill.venue_sequence)
            candidate_snapshot=candidate.snapshot(at_utc=self.clock(),venue_sequence=order.venue_sequence);venue_snapshot=recovery_bundle.account_snapshot if recovery_bundle is not None else self.venue.fetch_account_snapshot(self.manifest.paper_run_id,self.clock());local_orders=tuple({**persisted,fill.client_order_id:order}.values());venue_orders=recovery_bundle.recent_orders if recovery_bundle is not None else tuple(self.venue._orders.values()) if hasattr(self.venue,"_orders") else self.venue.list_open_orders();reconciliation,differences=PaperReconciliationEngine().reconcile(paper_run_id=self.manifest.paper_run_id,local_snapshot=candidate_snapshot,venue_snapshot=venue_snapshot,local_orders=local_orders,venue_orders=venue_orders,local_fills=tuple((*self._fills.values(),fill)),venue_fills=fills,at_utc=self.clock());state_bundle=self.repository.load_state_bundle(self.manifest.paper_run_id);sequence=int(state_bundle["risk_state"]["lifecycle_sequence"])+1;event=PaperLifecycleEvent(self.manifest.paper_run_id,"confirmed_fill_applied",self.clock(),sequence,{"fill_id":str(fill.fill_id),"venue_fill_id":fill.venue_fill_id,"recovery_observation_bundle_id":None if recovery_bundle is None else str(recovery_bundle.bundle_id)},(fill.fill_id,))
            if self.repository.persist_fill_bundle(fill=fill,order=order,local_snapshot=candidate_snapshot,venue_snapshot=venue_snapshot,reconciliation=reconciliation,differences=differences,lifecycle_event=event,recovery_observation_bundle_id=None if recovery_bundle is None else recovery_bundle.bundle_id):self.accounting=candidate;self._fills[fill.venue_fill_id]=fill;self._orders[fill.client_order_id]=order;persisted[fill.client_order_id]=order;applied_economics[fill.client_order_id]=(cumulative,prior_notional+fill.quantity*fill.price);applied.append(fill)
        for submission in self.repository.typed_submissions(self.manifest.paper_run_id):
            if submission.client_order_id not in {f.client_order_id for f in fills}:continue
            accounting_reservation=self.accounting.reservations.get(submission.client_order_id);accounting_amount=Decimal(0) if accounting_reservation is None else accounting_reservation.amount;venue_reservation=getattr(self.venue,"_reservations",{}).get(submission.client_order_id);venue_amount=Decimal(0) if venue_reservation is None else Decimal(venue_reservation["amount"]);self.repository.assert_reservation_consistency(submission.submission_id,accounting_amount=accounting_amount,venue_amount=venue_amount)
        return tuple(applied)
    def hydrate_from_postgres(self):
        self.accounting=self.repository.hydrate_accounting(self.manifest.paper_run_id);self._submissions={s.client_order_id:s for s in self.repository.typed_submissions(self.manifest.paper_run_id)};self._orders={o.client_order_id:o for o in self.repository.typed_orders(self.manifest.paper_run_id)};self._fills={f.venue_fill_id:f for f in self.repository.typed_fills(self.manifest.paper_run_id)};state=self.repository.load_state_bundle(self.manifest.paper_run_id);self.daily_submitted_notional=Decimal(str(state["risk_state"]["daily_submitted_notional"]));self.rate_limiter.consecutive_failures=int(state["risk_state"]["consecutive_transport_failures"])
        if hasattr(self.venue,"reconstruct"):
            durable_events=self.repository.load_internal_venue_events(self.manifest.paper_run_id) if hasattr(self.repository,"load_internal_venue_events") else ()
            if durable_events and hasattr(self.venue,"reconstruct_durable"):self.venue.reconstruct_durable(tuple(self._submissions.values()),durable_events);self._orders={o.client_order_id:o for o in self.venue.list_recent_orders()};self._fills={f.venue_fill_id:f for f in self.venue.fetch_fills()}
            else:
                events=tuple({"sequence":o.venue_sequence,"kind":"postgres_reconstruction","client_order_id":o.client_order_id,"details":{},"record_sha256":o.record_sha256} for o in self._orders.values());self.venue.reconstruct(tuple(self._orders.values()),tuple(self._fills.values()),events,balances=self.accounting.balances,positions=self.accounting.positions,reservations=self.accounting.reservations)
        if not hasattr(self.venue,"reconstruct") and hasattr(self.venue,"register_persisted_submission"):
            for submission in self._submissions.values():self.venue.register_persisted_submission(submission,self._orders.get(submission.client_order_id))
            if hasattr(self.venue,"_fills"):self.venue._fills=dict(self._fills)
        return self
