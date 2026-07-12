"""Complete deterministic offline Phase 7 demonstration."""
from __future__ import annotations
from datetime import datetime,timedelta,timezone
from decimal import Decimal
from uuid import NAMESPACE_URL,uuid5
from secure_eval_wrapper.alpha.identity import SeriesIdentity
from secure_eval_wrapper.data_collection.hashing import sha256_payload
from secure_eval_wrapper.data_collection.models import InstrumentType
from secure_eval_wrapper.execution.models import AccountingMode,OrderIntent,OrderSide,OrderType,RiskDecision,RiskDecisionStatus,RiskStage,TimeInForce
from .accounting import PaperAccounting
from .approval import ApprovalController
from .broker import PaperBroker
from .configuration import internal_demo_configuration
from .credentials import CredentialSourceType
from .engine import PaperTradingEngine
from .enums import KillSwitchReason,KillSwitchState,PaperProvider,PaperRunState
from .kill_switch import PaperKillSwitchController
from .manifests import create_manifest
from .models import CredentialReference,PaperKillSwitch,PaperMarketDataEvidence,deterministic_paper_uuid
from .preflight import PaperPreflightEngine,PaperPreflightEvidence
from .reconciliation import PaperReconciliationEngine
from .recovery import PaperRecoveryEngine
from .venues.internal import InternalFault,InternalPaperVenue
from .enums import InternalPaperFaultType

class DeterministicClock:
    def __init__(self,value):self.value=value
    def __call__(self):return self.value
    def advance(self,seconds=1):self.value+=timedelta(seconds=seconds); return self.value

def _intent(run,identity,at,*,side,quantity,current,target,kind=OrderType.MARKET,limit=None,stop=None,signal="signal"):
    H=sha256_payload("phase7-public-implementation"); delta=Decimal(quantity)*(Decimal(1) if side is OrderSide.BUY else Decimal(-1))
    return OrderIntent(run,uuid5(NAMESPACE_URL,signal),identity,at,side,kind,Decimal(quantity),Decimal(target),Decimal(current),delta,Decimal("100"),AccountingMode.SPOT,TimeInForce.GTC,H,H,H,"phase7-public",None if limit is None else Decimal(limit),None if stop is None else Decimal(stop))
def _risk(intent,at):
    return RiskDecision(intent.run_id,intent.order_intent_id,intent.series_identity,at,RiskStage.PRE_SUBMIT,RiskDecisionStatus.ACCEPTED,"accepted","paper demo pre-submit risk passed",sha256_payload("phase7-risk"))
def _client(intent,run):return "sew"+deterministic_paper_uuid("client-order",{"run":run,"intent":intent.order_intent_id}).hex[:29]

def run_internal_demo(*,persist_repository=None):
    clock=DeterministicClock(datetime(2026,1,1,tzinfo=timezone.utc)); config=internal_demo_configuration(persistence_required=persist_repository is not None); run_id=deterministic_paper_uuid("run",{"configuration":config.config_sha256,"start":clock()}); identity=SeriesIdentity("internal","internal-paper","BTC-USDT","BTC-USDT",InstrumentType.SPOT,"paper","USDT")
    durable=persist_repository is not None and hasattr(persist_repository,"prepare_submission")
    buy=_intent(run_id,identity,clock(),side=OrderSide.BUY,quantity="1",current="0",target="1",signal="paper-buy"); sell=_intent(run_id,identity,clock(),side=OrderSide.SELL,quantity="0.5",current="1",target="0.5",kind=OrderType.LIMIT,limit="120",signal="paper-sell"); unknown=_intent(run_id,identity,clock(),side=OrderSide.BUY,quantity="0.1",current="1",target="1.1",signal="paper-unknown")
    venue=InternalPaperVenue(initial_balances={"USDT":Decimal("10000")},faults=(InternalFault(InternalPaperFaultType.ACK_TIMEOUT,_client(unknown,run_id)),))
    initial=venue.fetch_account_snapshot(run_id,clock()); credential=CredentialReference(PaperProvider.INTERNAL,"no-credential-internal",CredentialSourceType.INJECTED_TEST,sha256_payload("no-credential-internal")[:16],False)
    implementation=sha256_payload("phase7-public-implementation"); evidence=PaperPreflightEvidence.verified_internal(clock(),postgresql_required=config.persistence_required)
    report=PaperPreflightEngine().evaluate(paper_run_id=run_id,configuration=config,account_snapshot=initial,credential_reference=credential,evidence=evidence,evaluated_at_utc=clock(),implementation_sha256=implementation)
    approvals=ApprovalController(); approval=approvals.create(report=report,configuration=config,snapshot=initial,credential_reference=credential,created_at_utc=clock(),ttl_seconds=300,actor="deterministic-fixture",nonce="phase7-offline-demo",maximum_total_notional=Decimal("1000")); manifest=create_manifest(configuration=config,report=report,approval=approval,snapshot=initial,credential_reference=credential,implementation_sha256=implementation,repository_commit_sha="phase7-public",strategy_run_reference="public-fixture-signals",start_at_utc=clock())
    accounting=PaperAccounting(paper_run_id=run_id,account_reference=config.account_reference,balances={b.currency:b.total for b in initial.balances}); kill=PaperKillSwitch(run_id,KillSwitchState.ARMED,None,clock()); persist_kill=(lambda value,event:persist_repository.persist_kill_event(value,event)) if persist_repository else None; kill_controller=PaperKillSwitchController(kill,persist=persist_kill)
    broker=PaperBroker(configuration=config,manifest=manifest,approval=approval,venue=venue,accounting=accounting,kill_switch=kill_controller,clock=clock,repository=persist_repository if durable else None,fixture_mode=not durable); reconciliation_engine=PaperReconciliationEngine(); engine=PaperTradingEngine(configuration=config,broker=broker,reconciliation_engine=reconciliation_engine,kill_switch=kill_controller,repository=persist_repository,clock=clock); engine.start(report=report,approval=approval,snapshot=initial,credential_reference=credential,approval_controller=approvals)
    if durable:
        market_evidence=PaperMarketDataEvidence(identity,"internal","BTC-USDT","bar_close","demo-bar-1",clock(),clock(),True,"accepted",sha256_payload("phase7-demo-market-source"),sha256_payload({"bar":"demo-bar-1","at":clock()}));persist_repository.record_market_data_evidence(run_id,market_evidence,recorded_at_utc=clock())
    persisted_fills=[]
    def persist_submission(intent,risk):
        if not persist_repository:return
        sub=next(s for s in broker.submissions if s.order_intent_id==intent.order_intent_id); persist_repository.record_submission_intent(sub,risk.record_sha256); order=broker.query_order(sub.client_order_id); current=next(s for s in broker.submissions if s.client_order_id==sub.client_order_id); request_id=deterministic_paper_uuid("internal-request",{"submission":sub.submission_id}); attempt={"transport_attempt_id":deterministic_paper_uuid("internal-attempt",{"request":request_id}),"request_id":request_id,"request_type":"submit","method":"INPROCESS","approved_origin":"internal-paper://inprocess","approved_path":"submit","idempotency_key":sub.idempotency_key,"attempted_at_utc":sub.submitted_at_utc,"result_type":"succeeded","retryable":False,"record_sha256":sha256_payload({"request":request_id,"result":"succeeded"})}; persist_repository.persist_submission_outcome(submission=current,order=order,transport_attempt=attempt)
    buy_risk=_risk(buy,clock()); engine.submit(buy,buy_risk); persist_submission(buy,buy_risk); clock.advance(); venue.acknowledge(_client(buy,run_id),clock()); broker.query_order(_client(buy,run_id))
    clock.advance(); _,first_fill,_=venue.fill(_client(buy,run_id),Decimal("0.4"),Decimal("100"),clock(),venue_fill_id="demo-fill-1"); polled=engine.poll(); persisted_fills.extend(f.fill_id for f in polled if durable)
    if persist_repository and not durable:
        local=accounting.snapshot(at_utc=clock(),venue_sequence=venue.sequence); remote=venue.fetch_account_snapshot(run_id,clock()); rec,diffs=broker.reconcile(reconciliation_engine); event=engine._event("partial_fill_persisted",{"venue_fill_id":first_fill.venue_fill_id},(first_fill.fill_id,)); persist_repository.persist_fill_bundle(fill=first_fill,order=broker.query_order(first_fill.client_order_id),local_snapshot=local,venue_snapshot=remote,reconciliation=rec,differences=diffs,lifecycle_event=event); persisted_fills.append(first_fill.fill_id)
    clock.advance(); _,second_fill,_=venue.fill(_client(buy,run_id),Decimal("0.6"),Decimal("100"),clock(),venue_fill_id="demo-fill-2"); polled=engine.poll(); persisted_fills.extend(f.fill_id for f in polled if durable)
    if persist_repository and not durable:
        local=accounting.snapshot(at_utc=clock(),venue_sequence=venue.sequence); remote=venue.fetch_account_snapshot(run_id,clock()); rec,diffs=broker.reconcile(reconciliation_engine); event=engine._event("full_fill_persisted",{"venue_fill_id":second_fill.venue_fill_id},(second_fill.fill_id,)); persist_repository.persist_fill_bundle(fill=second_fill,order=broker.query_order(second_fill.client_order_id),local_snapshot=local,venue_snapshot=remote,reconciliation=rec,differences=diffs,lifecycle_event=event); persisted_fills.append(second_fill.fill_id)
    clock.advance(); sell_risk=_risk(sell,clock()); engine.submit(sell,sell_risk); persist_submission(sell,sell_risk); clock.advance(); venue.acknowledge(_client(sell,run_id),clock()); broker.query_order(_client(sell,run_id)); clock.advance(); broker.cancel_paper_order(_client(sell,run_id),at_utc=clock(),reason="demo_cancel"); clock.advance(); venue.complete_cancel(_client(sell,run_id),clock()); broker.query_order(_client(sell,run_id))
    clock.advance(); unknown_risk=_risk(unknown,clock()); engine.submit(unknown,unknown_risk); unknown_submission=next(s for s in broker.submissions if s.client_order_id==_client(unknown,run_id))
    if persist_repository and not durable:
        persist_repository.record_submission_intent(unknown_submission,unknown_risk.record_sha256); request_id=deterministic_paper_uuid("internal-request",{"submission":unknown_submission.submission_id}); attempt={"transport_attempt_id":deterministic_paper_uuid("internal-attempt",{"request":request_id}),"request_id":request_id,"request_type":"submit","method":"INPROCESS","approved_origin":"internal-paper://inprocess","approved_path":"submit","idempotency_key":unknown_submission.idempotency_key,"attempted_at_utc":unknown_submission.submitted_at_utc,"result_type":"unknown","retryable":False,"record_sha256":sha256_payload({"request":request_id,"result":"unknown"})}; persist_repository.persist_submission_outcome(submission=unknown_submission,transport_attempt=attempt)
    recovery=PaperRecoveryEngine().recover_unknown(broker=broker,client_order_id=unknown_submission.client_order_id,started_at_utc=unknown_submission.submitted_at_utc,at_utc=clock.advance(),maximum_unknown_seconds=config.maximum_unknown_order_duration_seconds,kill_switch=kill_controller)
    if persist_repository:persist_repository.record_recovery(recovery)
    clock.advance(); kill_controller.trigger(KillSwitchReason.MANUAL,at_utc=clock(),evidence={"operator":"deterministic-fixture","reason":"demo emergency"}); cancel_intents=[]; kill_controller.cancel_open_orders(broker,at_utc=clock.advance(),durable_cancel_intent=lambda order,at:cancel_intents.append((order.client_order_id,at)))
    for order in venue.list_open_orders():
        if order.state.value=="cancel_pending":venue.complete_cancel(order.client_order_id,clock.advance()); broker.query_order(order.client_order_id)
    if persist_repository and not durable:
        for final_submission in broker.submissions:
            final_order=broker.query_order(final_submission.client_order_id); persist_repository.persist_submission_outcome(submission=next(s for s in broker.submissions if s.client_order_id==final_submission.client_order_id),order=final_order)
    final_reconciliation,final_differences=engine.reconcile(); kill_controller.finalize(at_utc=clock.advance(),terminal_handling_documented=True)
    summary={"paper_run_id":str(run_id),"provider":config.provider.value,"environment":config.environment.value,"preflight_status":report.status.value,"approval_status":"consumed" if durable else approval.state.value,"order_counts":{"submitted":len(broker.submissions),"venue":len(venue._orders)},"fill_counts":{"confirmed":len(broker.local_fills),"persisted":len(persisted_fills) if persist_repository else 0},"reconciliation_status":final_reconciliation.status.value,"reconciliation_difference_count":len(final_differences),"kill_switch_state":kill_controller.current.state.value,"final_balances":{k:format(v,"f") for k,v in sorted(broker.accounting.balances.items())},"final_positions":{p.series_identity.canonical_symbol:{"quantity":format(p.quantity,"f"),"average_entry_price":None if p.average_entry_price is None else format(p.average_entry_price,"f")} for p in broker.accounting.positions.values()},"unknown_submission_recovery":recovery.status.value,"cancel_intent_count":len(cancel_intents),"persistence_status":"postgresql" if persist_repository else "disabled","fixture_mode":not durable,"live_mode":False,"external_network":False}
    if persist_repository:
        for rate_event in broker.rate_limiter.events:persist_repository.record_rate_limit_event(run_id,config.provider,rate_event,broker.rate_limiter.consecutive_failures)
    engine.complete(summary=summary); return summary
