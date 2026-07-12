import dataclasses,json,os,subprocess,sys,unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime,timedelta,timezone
from decimal import Decimal
from secure_eval_wrapper.data_collection.hashing import sha256_payload
from secure_eval_wrapper.alpha.identity import SeriesIdentity
from secure_eval_wrapper.data_collection.models import InstrumentType
from secure_eval_wrapper.execution.models import AccountingMode,OrderSide,RiskDecisionStatus
from secure_eval_wrapper.paper.accounting import PaperAccounting
from secure_eval_wrapper.paper.approval import ApprovalController
from secure_eval_wrapper.paper.broker import PaperBroker
from secure_eval_wrapper.paper.configuration import internal_demo_configuration
from secure_eval_wrapper.paper.durable_repository import DurablePostgresPaperRepository,DispatchNotClaimable,RuntimeRiskBlocked
from secure_eval_wrapper.paper.engine import PaperTradingEngine
from secure_eval_wrapper.paper.enums import CredentialSourceType,InternalPaperFaultType,KillSwitchReason,KillSwitchState,PaperProvider,PaperRunState,VenueOrderState
from secure_eval_wrapper.paper.kill_switch import PaperKillSwitchController
from secure_eval_wrapper.paper.manifests import create_manifest
from secure_eval_wrapper.paper.models import CredentialReference,PaperKillSwitch,PaperRun,deterministic_paper_uuid
from secure_eval_wrapper.paper.preflight import PaperPreflightEngine,PaperPreflightEvidence
from secure_eval_wrapper.paper.reconciliation import PaperReconciliationEngine
from secure_eval_wrapper.paper.restart import reconstruct_internal_paper_runtime
from secure_eval_wrapper.paper.venue import UnknownSubmissionResult
from secure_eval_wrapper.paper.venues.internal import InternalFault,InternalPaperVenue
from phase7_test_support import H,ID,T0,intent,market_evidence,risk
RUN=os.environ.get("RUN_POSTGRES_INTEGRATION","").lower()=="true"
class Clock:
    def __init__(self):self.value=T0
    def __call__(self):return self.value
    def advance(self,seconds=1):self.value+=timedelta(seconds=seconds);return self.value
class Crash(BaseException):pass
@unittest.skipUnless(RUN,"requires real PostgreSQL 16")
class DurablePhase7PostgresTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import psycopg
        cls.psycopg=psycopg;cls.kw=dict(host=os.environ["POSTGRES_HOST"],port=int(os.environ["POSTGRES_PORT"]),dbname=os.environ["POSTGRES_DB"],user=os.environ["POSTGRES_USER"],password=os.environ["POSTGRES_PASSWORD"],sslmode=os.environ.get("POSTGRES_SSLMODE","disable"));cls.connection=psycopg.connect(**cls.kw)
    @classmethod
    def tearDownClass(cls):cls.connection.close()
    def setUp(self):
        with self.connection.cursor() as c:c.execute("TRUNCATE execution.paper_runs,execution.paper_configuration_snapshots,execution.paper_credential_references CASCADE")
        self.connection.commit();self.repo=DurablePostgresPaperRepository(self.connection)
    def runtime(self,label="runtime",faults=(),crash_hook=None,record_market=True):
        clock=Clock();c=dataclasses.replace(internal_demo_configuration(persistence_required=True),account_reference="audit-"+label);run_id=deterministic_paper_uuid("durable-test-run",{"label":label,"config":c.config_sha256});venue=InternalPaperVenue(account_reference=c.account_reference,initial_balances={"USDT":Decimal("10000")},faults=faults);snapshot=venue.fetch_account_snapshot(run_id,clock());credential=CredentialReference(PaperProvider.INTERNAL,"none-"+label,CredentialSourceType.INJECTED_TEST,sha256_payload(label)[:16]);report=PaperPreflightEngine().evaluate(paper_run_id=run_id,configuration=c,account_snapshot=snapshot,credential_reference=credential,evidence=PaperPreflightEvidence.verified_internal(clock(),postgresql_required=True),evaluated_at_utc=clock(),implementation_sha256=H);approval=ApprovalController().create(report=report,configuration=c,snapshot=snapshot,credential_reference=credential,created_at_utc=clock(),ttl_seconds=600,actor="audit",nonce=label,maximum_total_notional=Decimal("1000"));manifest=create_manifest(configuration=c,report=report,approval=approval,snapshot=snapshot,credential_reference=credential,implementation_sha256=H,repository_commit_sha="audit",strategy_run_reference="public-test",start_at_utc=clock());kill=PaperKillSwitch(run_id,KillSwitchState.ARMED,None,clock());controller=PaperKillSwitchController(kill,persist=lambda value,event:self.repo.persist_kill_event(value,event));accounting=PaperAccounting(paper_run_id=run_id,account_reference=c.account_reference,balances={b.currency:b.total for b in snapshot.balances});broker=PaperBroker(configuration=c,manifest=manifest,approval=approval,venue=venue,accounting=accounting,kill_switch=controller,clock=clock,repository=self.repo,worker_id=label,crash_hook=crash_hook);engine=PaperTradingEngine(configuration=c,broker=broker,reconciliation_engine=PaperReconciliationEngine(),kill_switch=controller,repository=self.repo,clock=clock);engine.start(report=report,approval=approval,snapshot=snapshot,credential_reference=credential,approval_controller=ApprovalController());
        if record_market:self.repo.record_market_data_evidence(run_id,market_evidence(at=clock()),recorded_at_utc=clock())
        return engine,venue,clock,approval,report,snapshot,credential
    def state(self,engine):return self.repo.load_state_bundle(engine.broker.manifest.paper_run_id)
    def test_durable_dispatch_crash_before_claim_and_after_claim(self):
        for point,expected,calls in (("after_durable_intent_before_claim","prepared",0),("after_dispatch_claim_before_venue","dispatch_claimed",0)):
            with self.subTest(point=point):
                self.setUp()
                def crash(value):
                    if value==point:raise Crash(point)
                engine,venue,clock,*_=self.runtime(point,crash_hook=crash);i=intent(run=engine.run.paper_run_id,signal=point)
                with self.assertRaises(Crash):engine.submit(i,risk(i))
                state=self.state(engine);self.assertEqual(state["dispatches"][0]["state"],expected);self.assertEqual(venue.submit_call_count,calls);self.assertEqual(state["reservations"][0]["state"],"open")
    def test_failure_before_durable_intent_has_no_venue_or_database_side_effect(self):
        engine,venue,clock,*_=self.runtime("before-durable");value=intent(run=engine.run.paper_run_id,signal="before-durable");blocked=dataclasses.replace(risk(value),status=RiskDecisionStatus.BLOCKED,risk_decision_id=None)
        with self.assertRaises(PermissionError):engine.submit(value,blocked)
        state=self.state(engine);self.assertEqual(venue.submit_call_count,0);self.assertEqual(state["submissions"],());self.assertEqual(state["dispatches"],());self.assertEqual(state["reservations"],())
    def test_http_5xx_after_venue_acceptance_recovers_without_resubmission(self):
        class AcceptedThenAmbiguous:
            def __init__(self,inner):self.inner=inner;self.status=None
            def submit_order(self,submission):self.inner.submit_order(submission);raise UnknownSubmissionResult("HTTP "+str(self.status)+" after acceptance")
            def __getattr__(self,name):return getattr(self.inner,name)
        engine,venue,clock,*_=self.runtime("accepted-5xx");wrapper=AcceptedThenAmbiguous(venue);engine.broker.venue=wrapper
        for status in (500,502,503,504):
            with self.subTest(status=status):
                wrapper.status=status;value=intent(run=engine.run.paper_run_id,signal="accepted-"+str(status));engine.submit(value,risk(value));state=self.state(engine);client=engine.broker.submissions[-1].client_order_id;before=venue.submit_call_count
                dispatch=next(x for x in state["dispatches"] if x["client_order_id"]==client);reservation=next(x for x in state["reservations"] if x["client_order_id"]==client)
                self.assertEqual(dispatch["state"],"unknown");self.assertEqual(reservation["state"],"open")
                fresh=reconstruct_internal_paper_runtime(repository=DurablePostgresPaperRepository(self.connection),paper_run_id=engine.run.paper_run_id,clock=clock);fresh.broker.venue=venue;self.assertIsNotNone(fresh.broker.query_order(client));self.assertEqual(venue.submit_call_count,before);recovered=next(x for x in self.state(fresh)["dispatches"] if x["client_order_id"]==client);self.assertEqual(recovered["state"],"recovered")
    def test_accepted_then_response_lost_and_ack_persistence_crash_recover_by_same_client(self):
        for point in (None,"after_venue_accept_before_outcome"):
            with self.subTest(point=point):
                self.setUp();holder={}
                def crash(value):
                    if value==point:raise Crash(value)
                engine,venue,clock,*_=self.runtime("accept-"+str(point),crash_hook=crash);i=intent(run=engine.run.paper_run_id,signal="accepted")
                if point:
                    with self.assertRaises(Crash):engine.submit(i,risk(i))
                else:engine.submit(i,risk(i))
                client="sew"+deterministic_paper_uuid("client-order",{"run":engine.run.paper_run_id,"intent":i.order_intent_id}).hex[:29];before=venue.submit_call_count;fresh=reconstruct_internal_paper_runtime(repository=DurablePostgresPaperRepository(self.connection),paper_run_id=engine.run.paper_run_id,clock=clock);fresh.broker.venue=venue;order=fresh.broker.query_order(client);self.assertIsNotNone(order);self.assertEqual(venue.submit_call_count,before);self.assertEqual(fresh.broker.submissions[0].client_order_id,client);self.assertIn(self.state(fresh)["dispatches"][0]["state"],("acknowledged","recovered"))
    def test_ambiguous_transport_crash_before_unknown_persistence_is_recoverable(self):
        def crash(point):
            if point=="after_ambiguous_transport_before_outcome":raise Crash(point)
        engine,venue,clock,*_=self.runtime("unknown-persist-crash",crash_hook=crash);value=intent(run=engine.run.paper_run_id,signal="unknown-persist-crash");client="sew"+deterministic_paper_uuid("client-order",{"run":engine.run.paper_run_id,"intent":value.order_intent_id}).hex[:29];venue._faults=(InternalFault(InternalPaperFaultType.ACK_TIMEOUT,client),)
        with self.assertRaises(Crash):engine.submit(value,risk(value))
        state=self.state(engine);self.assertEqual(state["dispatches"][0]["state"],"dispatch_claimed");self.assertEqual(state["reservations"][0]["state"],"open");self.assertEqual(state["recovery"],())
        fresh=reconstruct_internal_paper_runtime(repository=DurablePostgresPaperRepository(self.connection),paper_run_id=engine.run.paper_run_id,clock=clock);fresh.broker.venue=venue;self.assertIsNotNone(fresh.broker.query_order(client))
        self.assertEqual(self.state(fresh)["dispatches"][0]["state"],"recovered")
    def test_timeout_unknown_never_rejects_or_releases_reservation(self):
        engine,venue,clock,*_=self.runtime("unknown");i=intent(run=engine.run.paper_run_id,signal="unknown");client="sew"+deterministic_paper_uuid("client-order",{"run":engine.run.paper_run_id,"intent":i.order_intent_id}).hex[:29];venue._faults=(InternalFault(InternalPaperFaultType.ACK_TIMEOUT,client),);engine.submit(i,risk(i));state=self.state(engine);self.assertEqual(state["dispatches"][0]["state"],"unknown");self.assertEqual(state["submissions"][0]["state"],"submission_unknown");self.assertEqual(state["reservations"][0]["state"],"open");engine.broker.query_order(client);self.assertEqual(self.state(engine)["dispatches"][0]["state"],"recovered")
    def test_outcome_then_order_event_failure_rolls_back_to_claim(self):
        engine,venue,clock,*_=self.runtime("outcome-rollback");i=intent(run=engine.run.paper_run_id,signal="outcome");s,_=self.repo.prepare_submission(configuration=engine.configuration,approval=engine.broker.approval,manifest=engine.broker.manifest,intent=i,risk_decision=risk(i),now=clock());token=self.repo.claim_dispatch(s,worker_id="w",at_utc=clock());order=venue.submit_order(s)
        with self.assertRaises(RuntimeError):self.repo.complete_dispatch(s,claim_token=token,outcome="acknowledged",at_utc=clock(),order=order,fail_at="after_outcome_before_order")
        state=self.state(engine);self.assertEqual(state["dispatches"][0]["state"],"dispatch_claimed");self.assertEqual(len(state["orders"]),0);self.assertEqual(state["reservations"][0]["state"],"open")
    def test_cancel_intent_crash_before_claim_is_recoverable(self):
        def crash(point):
            if point=="after_cancel_intent_before_claim":raise Crash(point)
        engine,venue,clock,*_=self.runtime("cancel-intent-crash",crash_hook=crash);value=intent(run=engine.run.paper_run_id,signal="cancel-intent-crash");engine.submit(value,risk(value));client=engine.broker.submissions[0].client_order_id;venue.acknowledge(client,clock.advance());engine.broker.query_order(client)
        with self.assertRaises(Crash):engine.broker.cancel_paper_order(client,at_utc=clock(),reason="crash")
        state=self.state(engine);self.assertEqual(state["cancellations"][0]["state"],"cancel_requested");self.assertEqual(venue.cancel_call_count,0);self.assertEqual(state["reservations"][0]["state"],"open")
    def test_cancel_claim_crash_and_lost_response_recovery(self):
        marker={"point":None}
        def crash(value):
            if value==marker["point"]:raise Crash(value)
        engine,venue,clock,*_=self.runtime("cancel",crash_hook=crash);i=intent(run=engine.run.paper_run_id,signal="cancel");engine.submit(i,risk(i));client=engine.broker.submissions[0].client_order_id;venue.acknowledge(client,clock.advance());engine.broker.query_order(client);marker["point"]="after_cancel_claim_before_venue"
        with self.assertRaises(Crash):engine.broker.cancel_paper_order(client,at_utc=clock(),reason="crash")
        self.assertEqual(self.state(engine)["cancellations"][0]["state"],"cancel_claimed");self.assertEqual(venue.cancel_call_count,0)
        self.setUp();engine,venue,clock,*_=self.runtime("cancel-lost");i=intent(run=engine.run.paper_run_id,signal="cancel-lost");engine.submit(i,risk(i));client=engine.broker.submissions[0].client_order_id;venue.acknowledge(client,clock.advance());engine.broker.query_order(client);venue._faults=(InternalFault(InternalPaperFaultType.CANCEL_TIMEOUT,client),)
        with self.assertRaises(Exception):engine.broker.cancel_paper_order(client,at_utc=clock(),reason="lost")
        self.assertEqual(self.state(engine)["cancellations"][0]["state"],"cancel_unknown");self.assertEqual(self.state(engine)["reservations"][0]["state"],"open");venue.complete_cancel(client,clock.advance());fresh=reconstruct_internal_paper_runtime(repository=DurablePostgresPaperRepository(self.connection),paper_run_id=engine.run.paper_run_id,clock=clock);fresh.broker.venue=venue;fresh.broker.query_order(client);self.assertEqual(self.state(fresh)["cancellations"][0]["state"],"cancel_confirmed");self.assertEqual(self.state(fresh)["reservations"][0]["state"],"released")
    def test_restart_restores_partial_reservation_budget_fill_ids_kill_and_late_fill(self):
        engine,venue,clock,*_=self.runtime("restart");i=intent(run=engine.run.paper_run_id,quantity="1",signal="restart-buy");engine.submit(i,risk(i));client=engine.broker.submissions[0].client_order_id;venue.acknowledge(client,clock.advance());venue.fill(client,Decimal("0.4"),Decimal("100"),clock.advance(),venue_fill_id="restart-fill-1");engine.poll();engine.kill_switch.trigger(KillSwitchReason.MANUAL,at_utc=clock.advance(),evidence={"test":"restart"});before=self.state(engine);self.assertEqual(before["reservations"][0]["remaining_quantity"],Decimal("0.6"));del engine
        fresh=reconstruct_internal_paper_runtime(repository=DurablePostgresPaperRepository(self.connection),paper_run_id=before["run"]["paper_run_id"],clock=clock);self.assertFalse(fresh.kill_switch.accepts_new_orders);self.assertEqual(len(fresh.broker.accounting.applied_fill_ids),1);self.assertEqual(fresh.broker.daily_submitted_notional,Decimal("100"));self.assertEqual(fresh.broker.accounting.reservations[client].remaining_quantity,Decimal("0.6"));fresh.broker.venue=venue;venue.fill(client,Decimal("0.6"),Decimal("101"),clock.advance(),venue_fill_id="restart-fill-2");self.assertEqual(len(fresh.poll()),1);self.assertEqual(len(fresh.broker.accounting.applied_fill_ids),2);self.assertEqual(len(fresh.poll()),0);self.assertEqual(fresh.broker.daily_submitted_notional,Decimal("100"))
    def test_full_runtime_risk_decision_and_budget_lock_evidence(self):
        engine,venue,clock,*_=self.runtime("risk");i=intent(run=engine.run.paper_run_id,signal="risk");engine.submit(i,risk(i));row=self.repo._fetchone("SELECT evaluated_limits_jsonb FROM execution.paper_runtime_risk_decisions WHERE paper_run_id=%s",(engine.run.paper_run_id,));keys=set(row["evaluated_limits_jsonb"]);required={"allowed_instrument","allowed_instrument_type","allowed_settlement_asset","allowed_order_type","perpetual_policy","spot_short_prohibition","maximum_order_notional","maximum_position_notional_per_instrument","maximum_gross_exposure","maximum_net_exposure","maximum_open_order_count","maximum_orders_per_minute","maximum_daily_submitted_notional","approval_maximum_total_notional","maximum_daily_realized_loss","maximum_current_drawdown","market_data_staleness","account_snapshot_staleness","reconciliation_status","reconciliation_age","maximum_unknown_order_age","maximum_unacknowledged_order_age","maximum_consecutive_transport_failures","maximum_clock_skew","maximum_run_duration","durable_reservation_available"};self.assertTrue(required.issubset(keys));self.assertTrue(all(row["evaluated_limits_jsonb"][key]["passed"] for key in required))
    def test_every_declared_runtime_limit_has_boundary_and_over_limit_evidence(self):
        engine,venue,clock,*_=self.runtime("risk-boundaries");base=self.state(engine);i=intent(run=engine.run.paper_run_id,signal="risk-boundary")
        state=dict(base["risk_state"]);state["run_started_at_utc"]=engine.run.started_at_utc
        evidence={"market_data_at_utc":clock(),"account_snapshot_at_utc":clock(),"reconciliation_at_utc":clock(),"reconciliation_status":"reconciled","clock_skew_seconds":0,"oldest_unknown_age_seconds":0,"oldest_unacknowledged_age_seconds":0}
        def evaluate(*,value=i,configuration_changes=None,state_changes=None,evidence_changes=None,approval=None,balances=None):
            configuration=dataclasses.replace(engine.configuration,**(configuration_changes or {}))
            current_state={**state,**(state_changes or {})}
            current_evidence={**evidence,**(evidence_changes or {})}
            current_approval=approval or engine.broker.approval
            submission=engine.broker._prepare(value)
            return self.repo._risk(configuration,current_approval,value,submission,current_state,(),balances or base["balances"],(),current_evidence,clock())[0]
        self.assertEqual(evaluate(),())
        sell=intent(run=engine.run.paper_run_id,side=OrderSide.SELL,current="0",target="-1",signal="spot-short")
        perp_identity=SeriesIdentity("internal","internal-paper","BTC-USDT-SWAP","BTC-USDT-SWAP",InstrumentType.PERPETUAL_SWAP,"paper","USDT")
        perp=dataclasses.replace(i,series_identity=perp_identity,accounting_mode=AccountingMode.LINEAR_PERPETUAL,order_intent_id=None)
        cases=(
            ("allowed_instrument",{"configuration_changes":{"allowed_instruments":("ETH-USDT",)}}),
            ("allowed_instrument_type",{"configuration_changes":{"allowed_instrument_types":("perpetual_swap",)}}),
            ("allowed_settlement_asset",{"configuration_changes":{"allowed_settlement_assets":("BTC",)}}),
            ("allowed_order_type",{"configuration_changes":{"allowed_order_types":tuple(x for x in engine.configuration.allowed_order_types if x!=i.order_type)}}),
            ("perpetual_policy",{"value":perp,"configuration_changes":{"allowed_instruments":("BTC-USDT-SWAP",),"allowed_instrument_types":("perpetual_swap",),"allow_perpetual":False}}),
            ("spot_short_prohibition",{"value":sell}),
            ("maximum_order_notional",{"configuration_changes":{"maximum_order_notional":Decimal("99")}}),
            ("maximum_position_notional_per_instrument",{"configuration_changes":{"maximum_position_notional_per_instrument":Decimal("99")}}),
            ("maximum_gross_exposure",{"configuration_changes":{"maximum_gross_exposure":Decimal("99")}}),
            ("maximum_net_exposure",{"configuration_changes":{"maximum_net_exposure":Decimal("99")}}),
            ("maximum_open_order_count",{"state_changes":{"open_order_count":engine.configuration.maximum_open_order_count}}),
            ("maximum_orders_per_minute",{"state_changes":{"orders_in_current_minute":engine.configuration.maximum_orders_per_minute}}),
            ("maximum_daily_submitted_notional",{"state_changes":{"daily_submitted_notional":Decimal("9901")}}),
            ("approval_maximum_total_notional",{"approval":dataclasses.replace(engine.broker.approval,maximum_approved_total_notional=Decimal("99"),approval_id=None)}),
            ("maximum_daily_realized_loss",{"state_changes":{"daily_realized_pnl":Decimal("-1001")}}),
            ("maximum_current_drawdown",{"state_changes":{"high_watermark_equity":Decimal("10000"),"current_equity":Decimal("8999")}}),
            ("market_data_staleness",{"evidence_changes":{"market_data_at_utc":clock()-timedelta(seconds=61)}}),
            ("account_snapshot_staleness",{"evidence_changes":{"account_snapshot_at_utc":clock()-timedelta(seconds=61)}}),
            ("reconciliation_status",{"evidence_changes":{"reconciliation_status":"material_difference"}}),
            ("reconciliation_age",{"evidence_changes":{"reconciliation_at_utc":clock()-timedelta(seconds=61)}}),
            ("maximum_unknown_order_age",{"evidence_changes":{"oldest_unknown_age_seconds":31}}),
            ("maximum_unacknowledged_order_age",{"evidence_changes":{"oldest_unacknowledged_age_seconds":16}}),
            ("maximum_consecutive_transport_failures",{"state_changes":{"consecutive_transport_failures":4}}),
            ("maximum_clock_skew",{"evidence_changes":{"clock_skew_seconds":6}}),
            ("maximum_run_duration",{"state_changes":{"run_started_at_utc":clock()-timedelta(seconds=3601)}}),
            ("durable_reservation_available",{"balances":({"currency":"USDT","total":Decimal("99")},)}),
        )
        for reason,kwargs in cases:
            with self.subTest(reason=reason):
                reasons=evaluate(**kwargs)
                self.assertIn(reason,reasons)
    def test_manifest_configuration_and_submission_economics_are_database_immutable(self):
        engine,venue,clock,*_=self.runtime("immutable");i=intent(run=engine.run.paper_run_id,signal="immutable");engine.submit(i,risk(i))
        statements=(("UPDATE execution.paper_run_manifests SET strategy_run_reference='changed' WHERE paper_run_id=%s",),("DELETE FROM execution.paper_run_manifests WHERE paper_run_id=%s",),("UPDATE execution.paper_configuration_snapshots SET account_reference='changed' WHERE configuration_sha256=%s",engine.configuration.config_sha256),("UPDATE execution.paper_order_submissions SET quantity=quantity+1 WHERE paper_run_id=%s",))
        for item in statements:
            sql=item[0];param=item[1] if len(item)>1 else engine.run.paper_run_id
            with self.subTest(sql=sql),self.assertRaises(Exception):
                with self.connection.transaction():self.connection.execute(sql,(param,))
    def test_atomic_approval_consume_replay_does_not_reset_state(self):
        engine,venue,clock,approval,*_=self.runtime("approval");state=self.state(engine);self.assertEqual(state["approval"]["state"],"consumed");self.assertEqual(sum(1 for x in state["approval_events"] if x["next_state"]=="consumed"),1);before=state["risk_state"]["version"]
        bundle=dict(run=engine.run,configuration=engine.configuration,credential_reference=engine.broker.manifest.credential_reference,snapshot=engine.broker.venue.fetch_account_snapshot(engine.run.paper_run_id,clock()),report=None,approval=approval,manifest=engine.broker.manifest,kill_switch=engine.kill_switch.current)
        self.assertFalse(self.repo.persist_start_run(run=engine.run,configuration=engine.configuration,credential_reference=engine.broker.manifest.credential_reference,snapshot=bundle["snapshot"],report=type("R",(),{"report_id":approval.preflight_report_id})(),approval=approval,manifest=engine.broker.manifest,kill_switch=engine.kill_switch.current));self.assertEqual(self.state(engine)["risk_state"]["version"],before)
    def test_concurrent_workers_cannot_jointly_exceed_daily_or_approval_budget(self):
        engine,venue,clock,*_=self.runtime("concurrent-risk");run=engine.run.paper_run_id;config=engine.configuration;approval=engine.broker.approval;manifest=engine.broker.manifest;orders=(intent(run=run,quantity="6",target="6",signal="worker-a"),intent(run=run,quantity="6",target="6",signal="worker-b"))
        def submit(value):
            connection=self.psycopg.connect(**self.kw)
            try:
                repo=DurablePostgresPaperRepository(connection);repo.prepare_submission(configuration=config,approval=approval,manifest=manifest,intent=value,risk_decision=risk(value),now=clock());return "accepted"
            except Exception as exc:return type(exc).__name__
            finally:connection.close()
        with ThreadPoolExecutor(max_workers=2) as pool:results=tuple(pool.map(submit,orders))
        self.assertEqual(results.count("accepted"),1);state=self.state(engine);self.assertEqual(state["risk_state"]["daily_submitted_notional"],Decimal("600"));self.assertEqual(len(state["dispatches"]),1);self.assertEqual(len([r for r in state["reservations"] if r["state"]=="open"]),1)
    def test_concurrent_approval_consume_allows_one_start(self):
        engine,venue,clock,approval,report,snapshot,credential=self.runtime("concurrent-approval");bundle=dict(run=engine.run,configuration=engine.configuration,credential_reference=credential,snapshot=snapshot,report=report,approval=approval,manifest=engine.broker.manifest,kill_switch=PaperKillSwitch(engine.run.paper_run_id,KillSwitchState.ARMED,None,T0))
        with self.connection.cursor() as c:c.execute("TRUNCATE execution.paper_runs,execution.paper_configuration_snapshots,execution.paper_credential_references CASCADE")
        self.connection.commit()
        def start(_):
            connection=self.psycopg.connect(**self.kw)
            try:return DurablePostgresPaperRepository(connection).persist_start_run(**bundle)
            finally:connection.close()
        with ThreadPoolExecutor(max_workers=2) as pool:results=tuple(pool.map(start,(1,2)))
        self.assertEqual(results.count(True),1);self.assertEqual(results.count(False),1);state=self.repo.load_state_bundle(engine.run.paper_run_id);self.assertEqual(state["approval"]["state"],"consumed");self.assertEqual(sum(1 for x in state["approval_events"] if x["next_state"]=="consumed"),1)
    def test_submit_rejects_process_objects_changed_after_durable_start(self):
        engine,venue,clock,*_=self.runtime("authority-binding")
        variants=(
            ("configuration",dataclasses.replace(engine.configuration,maximum_order_notional=Decimal("99")),engine.broker.approval),
            ("approval-budget",engine.configuration,dataclasses.replace(engine.broker.approval,maximum_approved_total_notional=Decimal("999"),approval_id=None)),
            ("approval-snapshot",engine.configuration,dataclasses.replace(engine.broker.approval,account_snapshot_sha256="f"*64,approval_id=None)),
            ("approval-credential",engine.configuration,dataclasses.replace(engine.broker.approval,credential_reference_sha256="e"*64,approval_id=None)),
        )
        for label,configuration,approval in variants:
            with self.subTest(label=label):
                value=intent(run=engine.run.paper_run_id,signal="authority-"+label)
                with self.assertRaises(PermissionError):
                    self.repo.prepare_submission(configuration=configuration,approval=approval,manifest=engine.broker.manifest,intent=value,risk_decision=risk(value),now=clock())
        state=self.state(engine)
        self.assertEqual(state["submissions"],())
        self.assertEqual(state["dispatches"],())
    def test_missing_stale_evidence_expired_approval_and_cancel_rate_fail_closed(self):
        engine,venue,clock,*_=self.runtime("fail-closed");i=intent(run=engine.run.paper_run_id,signal="missing-evidence")
        with self.assertRaises(Exception):self.repo.prepare_submission(configuration=engine.configuration,approval=engine.broker.approval,manifest=engine.broker.manifest,intent=i,risk_decision=risk(i),now=clock(),evidence={"market_data_at_utc":None,"account_snapshot_at_utc":None,"reconciliation_at_utc":None,"reconciliation_status":None,"clock_skew_seconds":None})
        blocked=self.repo._fetchone("SELECT reason_codes_jsonb FROM execution.paper_runtime_risk_decisions WHERE order_intent_id=%s",(i.order_intent_id,));self.assertNotIn("market_data_evidence",blocked["reason_codes_jsonb"]);self.assertIn("reconciliation_evidence",blocked["reason_codes_jsonb"])
        self.setUp();engine,venue,clock,*_=self.runtime("expired");clock.advance(601);fresh=reconstruct_internal_paper_runtime(repository=self.repo,paper_run_id=engine.run.paper_run_id,clock=clock);expired=intent(run=engine.run.paper_run_id,at=clock(),signal="expired")
        with self.assertRaises(PermissionError):fresh.submit(expired,risk(expired,clock()))
        self.setUp();engine,venue,clock,*_=self.runtime("cancel-limit");active=intent(run=engine.run.paper_run_id,signal="cancel-limit");engine.submit(active,risk(active));client=engine.broker.submissions[0].client_order_id;venue.acknowledge(client,clock());engine.broker.query_order(client);self.repo._execute("UPDATE execution.paper_run_risk_state SET cancellations_in_current_minute=%s WHERE paper_run_id=%s",(engine.configuration.maximum_cancellations_per_minute,engine.run.paper_run_id))
        with self.assertRaises(Exception):engine.broker.cancel_paper_order(client,at_utc=clock(),reason="limit")
    def test_restart_worker_resumes_prepared_once_with_original_identity(self):
        def crash(point):
            if point=="after_durable_intent_before_claim":raise Crash(point)
        engine,venue,clock,*_=self.runtime("third-prepared",crash_hook=crash);value=intent(run=engine.run.paper_run_id,signal="third-prepared")
        with self.assertRaises(Crash):engine.submit(value,risk(value))
        client=engine.broker.submissions[0].client_order_id;run=engine.run.paper_run_id;del engine,venue
        fresh=reconstruct_internal_paper_runtime(repository=DurablePostgresPaperRepository(self.connection),paper_run_id=run,clock=clock);self.assertEqual(fresh.broker.venue.submit_call_count,0);result=fresh.recover_unresolved();self.assertTrue(result);self.assertEqual(fresh.broker.venue.submit_call_count,1);state=self.repo.load_state_bundle(run);self.assertEqual(state["dispatches"][0]["state"],"acknowledged");self.assertEqual(state["submissions"][0]["client_order_id"],client);self.assertEqual(state["risk_state"]["open_order_count"],1);fresh.recover_unresolved();self.assertEqual(fresh.broker.venue.submit_call_count,1);self.assertEqual(len(state["reservations"]),1)
    def test_restart_worker_resumes_cancel_requested_once(self):
        marker={"enabled":False}
        def crash(point):
            if marker["enabled"] and point=="after_cancel_intent_before_claim":raise Crash(point)
        engine,venue,clock,*_=self.runtime("third-cancel",crash_hook=crash);value=intent(run=engine.run.paper_run_id,signal="third-cancel");engine.submit(value,risk(value));client=engine.broker.submissions[0].client_order_id;venue.acknowledge(client,clock.advance());engine.broker.query_order(client);marker["enabled"]=True
        with self.assertRaises(Crash):engine.broker.cancel_paper_order(client,at_utc=clock(),reason="restart")
        run=engine.run.paper_run_id;del engine
        fresh=reconstruct_internal_paper_runtime(repository=DurablePostgresPaperRepository(self.connection),paper_run_id=run,clock=clock);fresh.recover_unresolved();self.assertEqual(fresh.broker.venue.cancel_call_count,1);state=self.repo.load_state_bundle(run);self.assertEqual(state["cancellations"][0]["state"],"cancel_unknown");self.assertEqual(state["reservations"][0]["state"],"open");fresh.recover_unresolved();self.assertEqual(fresh.broker.venue.cancel_call_count,1)
    def test_claim_tokens_leases_and_recovery_claim_are_strict(self):
        engine,venue,clock,*_=self.runtime("third-claims");value=intent(run=engine.run.paper_run_id,signal="third-claims");submission,_=self.repo.prepare_submission(configuration=engine.configuration,approval=engine.broker.approval,manifest=engine.broker.manifest,intent=value,risk_decision=risk(value),now=clock());token=self.repo.claim_dispatch(submission,worker_id="worker-a",at_utc=clock())
        for invalid in (None,deterministic_paper_uuid("wrong-token",{"submission":submission.submission_id})):
            with self.assertRaises(DispatchNotClaimable):self.repo.complete_dispatch(submission,claim_token=invalid,outcome="unknown",at_utc=clock(),worker_id="worker-a")
        clock.advance(31)
        with self.assertRaises(DispatchNotClaimable):self.repo.complete_dispatch(submission,claim_token=token,outcome="unknown",at_utc=clock(),worker_id="worker-a")
        recovery_token=self.repo.claim_dispatch_recovery(submission,worker_id="recovery-a",at_utc=clock())
        with self.assertRaises(DispatchNotClaimable):self.repo.claim_dispatch_recovery(submission,worker_id="recovery-b",at_utc=clock())
        self.assertFalse(self.repo.complete_dispatch_recovery(submission,recovery_claim_token=recovery_token,at_utc=clock(),order=None,evidence_sha256=H));self.assertFalse(self.repo.complete_dispatch_recovery(submission,recovery_claim_token=recovery_token,at_utc=clock(),order=None,evidence_sha256=H))
        self.setUp();engine,venue,clock,*_=self.runtime("third-cancel-claims");value=intent(run=engine.run.paper_run_id,signal="third-cancel-claims");engine.submit(value,risk(value));submission=engine.broker.submissions[0];venue.acknowledge(submission.client_order_id,clock());engine.broker.query_order(submission.client_order_id);self.repo.prepare_cancel(submission,at_utc=clock(),maximum_cancellations_per_minute=engine.configuration.maximum_cancellations_per_minute);token=self.repo.claim_cancel(submission,worker_id="worker-a",at_utc=clock())
        for invalid in (None,deterministic_paper_uuid("wrong-cancel-token",{"submission":submission.submission_id})):
            with self.assertRaises(DispatchNotClaimable):self.repo.complete_cancel(submission,claim_token=invalid,confirmed=False,at_utc=clock(),worker_id="worker-a")
        clock.advance(31)
        with self.assertRaises(DispatchNotClaimable):self.repo.complete_cancel(submission,claim_token=token,confirmed=False,at_utc=clock(),worker_id="worker-a")
        recovery_token=self.repo.claim_cancel_recovery(submission,worker_id="recovery-a",at_utc=clock())
        with self.assertRaises(DispatchNotClaimable):self.repo.claim_cancel_recovery(submission,worker_id="recovery-b",at_utc=clock())
        self.assertFalse(self.repo.complete_cancel_recovery(submission,recovery_claim_token=recovery_token,at_utc=clock(),order=venue.query_order(submission.client_order_id),evidence_sha256=H));self.assertFalse(self.repo.complete_cancel_recovery(submission,recovery_claim_token=recovery_token,at_utc=clock(),order=venue.query_order(submission.client_order_id),evidence_sha256=H))
    def test_operational_submission_uses_only_authoritative_market_evidence(self):
        engine,venue,clock,*_=self.runtime("third-market-missing",record_market=False);value=intent(run=engine.run.paper_run_id,signal="missing",at=clock())
        with self.assertRaises(RuntimeRiskBlocked) as caught:engine.submit(value,risk(value))
        self.assertIn("market_data_missing",caught.exception.reasons);self.assertEqual(venue.submit_call_count,0)
        self.setUp();engine,venue,clock,*_=self.runtime("third-market");bad=(dataclasses.replace(market_evidence(observation="non-final"),is_final=False,evidence_id=None),dataclasses.replace(market_evidence(observation="quarantined"),validation_status="quarantined",evidence_id=None),dataclasses.replace(market_evidence(observation="future"),observed_at_utc=T0+timedelta(seconds=1),available_at_utc=T0+timedelta(seconds=1),evidence_id=None),dataclasses.replace(market_evidence(observation="stale"),observed_at_utc=T0-timedelta(seconds=61),available_at_utc=T0-timedelta(seconds=61),evidence_id=None))
        wrong_identity=SeriesIdentity("internal","internal-paper","ETH-USDT","ETH-USDT",InstrumentType.SPOT,"paper","USDT");bad=(*bad,market_evidence(identity=wrong_identity,observation="wrong-series"));expected=("market_data_non_final","market_data_quarantined","market_data_future","market_data_stale","market_data_identity_mismatch")
        for n,(evidence,reason_code) in enumerate(zip(bad,expected)):
            value=intent(run=engine.run.paper_run_id,signal="bad-market-"+str(n),at=clock())
            with self.assertRaises(RuntimeRiskBlocked) as caught:engine.submit(value,risk(value),market_evidence=evidence)
            self.assertIn(reason_code,caught.exception.reasons)
        self.setUp();engine,venue,clock,*_=self.runtime("market-boundary");clock.advance(60);boundary=intent(run=engine.run.paper_run_id,signal="market-boundary",at=clock());engine.submit(boundary,risk(boundary,clock()));self.assertEqual(venue.submit_call_count,1)
        self.setUp();engine,venue,clock,*_=self.runtime("market-one-over");clock.advance(61);one_over=intent(run=engine.run.paper_run_id,signal="market-one-over",at=clock())
        with self.assertRaises(RuntimeRiskBlocked) as caught:engine.submit(one_over,risk(one_over,clock()))
        self.assertIn("market_data_stale",caught.exception.reasons);self.assertEqual(self.state(engine)["risk_state"]["latest_market_data_at_utc"],T0)
    def test_persisted_preflight_created_run_transitions_to_running(self):
        env=os.environ.copy();env["ENABLE_POSTGRES_PERSISTENCE"]="true";completed=subprocess.run([sys.executable,"open-core/scripts/run_paper_preflight.py","--create-approval","--persist"],cwd=os.getcwd(),env=env,text=True,capture_output=True);self.assertEqual(completed.returncode,0,completed.stdout+completed.stderr);payload=json.loads(completed.stdout);run_id=__import__("uuid").UUID(payload["paper_run_id"]);self.assertTrue(payload["approval_created"]);self.assertTrue(payload["manifest_eligible"]);self.assertEqual(self.repo.load_state_bundle(run_id)["run"]["state"],"created")
        from secure_eval_wrapper.paper.restart import start_persisted_internal_preflight
        now=datetime.now(timezone.utc);engine=start_persisted_internal_preflight(repository=self.repo,paper_run_id=run_id,clock=lambda:now,repository_commit_sha="audit",strategy_run_reference="cli-created-preflight");state=self.repo.load_state_bundle(run_id);self.assertEqual(state["run"]["state"],"running");self.assertEqual(state["approval"]["state"],"consumed");self.repo.record_market_data_evidence(run_id,market_evidence(at=now),recorded_at_utc=now);value=intent(run=run_id,signal="created-start-order",at=now);engine.submit(value,risk(value,now));fresh=reconstruct_internal_paper_runtime(repository=self.repo,paper_run_id=run_id,clock=lambda:now);self.assertEqual(fresh.run.state,PaperRunState.RUNNING);self.assertEqual(len(fresh.broker.submissions),1)
    def test_advancing_clock_reconciliation_and_operational_cli_persist_exact_bundle(self):
        engine,venue,clock,*_=self.runtime("third-reconcile");before=len(self.state(engine)["reconciliation_bundles"]);bundle=engine.reconcile();state=self.state(engine);self.assertEqual(len(state["reconciliation_bundles"]),before+1);row=state["reconciliation_bundles"][-1];self.assertEqual(row["local_snapshot_id"],bundle.local_snapshot.snapshot_id);self.assertEqual(row["venue_snapshot_id"],bundle.venue_snapshot.snapshot_id);env=os.environ.copy();env["ENABLE_POSTGRES_PERSISTENCE"]="true";completed=subprocess.run([sys.executable,"open-core/scripts/run_paper_reconcile.py","--run-id",str(engine.run.paper_run_id),"--persist"],cwd=os.getcwd(),env=env,text=True,capture_output=True);self.assertEqual(completed.returncode,0,completed.stdout+completed.stderr);self.assertIn('"postgresql_connected":true',completed.stdout);self.assertEqual(len(self.state(engine)["reconciliation_bundles"]),before+2)
    def test_cancel_confirmed_late_fill_closes_open_budget_only_once(self):
        engine,venue,clock,*_=self.runtime("third-open-count");a=intent(run=engine.run.paper_run_id,quantity="0.1",target="0.1",signal="open-a");b=intent(run=engine.run.paper_run_id,quantity="0.1",target="0.1",signal="open-b");engine.submit(a,risk(a));engine.submit(b,risk(b));clients=[s.client_order_id for s in engine.broker.submissions]
        for client in clients:venue.acknowledge(client,clock.advance());engine.broker.query_order(client)
        self.assertEqual(self.state(engine)["risk_state"]["open_order_count"],2);engine.broker.cancel_paper_order(clients[0],at_utc=clock.advance(),reason="test");venue.complete_cancel(clients[0],clock.advance());engine.broker.query_order(clients[0]);self.assertEqual(self.state(engine)["risk_state"]["open_order_count"],1)
        cancelled=venue._orders[clients[0]];venue._orders[clients[0]]=dataclasses.replace(cancelled,state=VenueOrderState.ACKNOWLEDGED,cumulative_filled_quantity=Decimal(0),average_fill_price=None);venue._reservations[clients[0]]={"currency":"USDT","amount":Decimal("10.2102"),"original_quantity":Decimal("0.1"),"remaining_quantity":Decimal("0.1")};venue.fill(clients[0],Decimal("0.1"),Decimal("100"),clock.advance(),venue_fill_id="late-after-cancel");self.assertEqual(len(engine.poll()),1);state=self.state(engine);self.assertEqual(state["risk_state"]["open_order_count"],1);self.assertFalse(next(x for x in state["submissions"] if x["client_order_id"]==clients[0])["counted_open"]);self.assertTrue(next(x for x in state["submissions"] if x["client_order_id"]==clients[1])["counted_open"]);a_submission=next(x for x in state["submissions"] if x["client_order_id"]==clients[0]);self.assertEqual(sum(1 for x in state["order_budget_events"] if x["event_type"]=="order_budget_closed" and x["submission_id"]==a_submission["submission_id"]),1)
