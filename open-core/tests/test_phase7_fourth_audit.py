"""Phase 7 fourth-audit reservation and PostgreSQL crash-integrity regressions."""
import dataclasses
import os
import subprocess
import unittest
from datetime import timedelta
from decimal import Decimal

from secure_eval_wrapper.data_collection.hashing import sha256_payload
from secure_eval_wrapper.execution.models import OrderType
from secure_eval_wrapper.paper.accounting import PaperAccounting
from secure_eval_wrapper.paper.approval import ApprovalController
from secure_eval_wrapper.paper.broker import PaperBroker
from secure_eval_wrapper.paper.configuration import internal_demo_configuration
from secure_eval_wrapper.paper.durable_repository import DurablePostgresPaperRepository,Phase7ConflictError
from secure_eval_wrapper.paper.engine import PaperTradingEngine
from secure_eval_wrapper.paper.enums import CredentialSourceType,KillSwitchState,PaperProvider,PaperRunState,VenueOrderState
from secure_eval_wrapper.paper.kill_switch import PaperKillSwitchController
from secure_eval_wrapper.paper.manifests import create_manifest
from secure_eval_wrapper.paper.models import CredentialReference,PaperKillSwitch,PaperRun,deterministic_paper_uuid
from secure_eval_wrapper.paper.preflight import PaperPreflightEngine,PaperPreflightEvidence
from secure_eval_wrapper.paper.reconciliation import PaperReconciliationEngine
from secure_eval_wrapper.paper.reservations import calculate_reservation,reduce_reservation
from secure_eval_wrapper.paper.restart import reconstruct_internal_paper_runtime
from secure_eval_wrapper.paper.venues.internal import InternalPaperVenue
from phase7_test_support import H,T0,intent,market_evidence,risk

RUN=os.environ.get("RUN_POSTGRES_INTEGRATION","").lower()=="true"


class Clock:
    def __init__(self):self.value=T0
    def __call__(self):return self.value
    def advance(self,seconds=1):self.value+=timedelta(seconds=seconds);return self.value


class MigrationImmutabilityTests(unittest.TestCase):
    def test_migrations_0001_through_0018_match_audited_starting_blobs(self):
        expected={"0001":"e472c4c945d263aee76b4ab1a97314f255f5ce29","0002":"fb20c865e4bb8e7beac1ce1e1b2016f9312bed7d","0003":"f4b8aed7a87c865fa2d4e677a371b1fb4e10d412","0004":"ae1fe0c681edd59b2699189a355e2e43d55f0315","0005":"79eca58156a37732e9ad1904089cd4d571975f48","0006":"1e884b35e53e0d92d979a1c57a90a43f2739e4da","0007":"b9c3c5cdffd62c2bfc887a6fe926ba2c62a7bb71","0008":"6dd81d8fda134bef480f0e5d8cb81da6c8e3e050","0009":"e8d3695a5d1f59fe887722fca3a21c61e2e855c4","0010":"9c6c5827885cb979fedf5a20c6a2b74867360acd","0011":"68d1b78c9f95dda28338a8bf94b21aa31f09005c","0012":"6363c23899eaef9eeae62c5e4be149670dd3e1a6","0013":"54fa63eb2f5cfc309fa2647df2cc4f3819c945b0","0014":"dcff6e38e14628ac6813fa877aef0f303a8047dd","0015":"4744c25650f33b2a488df1fb0dd98fe1ae3b4cf7","0016":"3f48e81288164c997f29a4be868ad34bf1aece23","0017":"f5d692677dbd6a92821acacd9a614efde9022706","0018":"a8e38d70c685005dfd78a4a313a6cb6e3a7c7072"}
        root=os.path.dirname(os.path.dirname(os.path.dirname(__file__)));migration_dir=os.path.join(root,"open-core","db","migrations")
        for migration_id,blob in expected.items():
            path=next(name for name in os.listdir(migration_dir) if name.startswith(migration_id+"_"));actual=subprocess.run(["git","hash-object",os.path.join(migration_dir,path)],cwd=root,capture_output=True,text=True,check=True).stdout.strip();self.assertEqual(actual,blob,migration_id)

class ReservationAuthorityTests(unittest.TestCase):
    def test_limit_stop_and_stop_limit_use_one_conservative_formula(self):
        limit=calculate_reservation(intent(kind=OrderType.LIMIT,limit="110"));self.assertEqual(limit.reserve_price,Decimal("110"));self.assertEqual(limit.amount,Decimal("110.110"))
        stop=calculate_reservation(intent(kind=OrderType.STOP,stop="120"));self.assertEqual(stop.reserve_price,Decimal("120"));self.assertEqual(stop.amount,Decimal("120.120"))
        stop_limit=calculate_reservation(intent(kind=OrderType.STOP_LIMIT,limit="115",stop="120"));self.assertEqual(stop_limit.reserve_price,Decimal("120"));self.assertEqual(stop_limit.amount,Decimal("120.120"))
    def test_partial_fill_uses_actual_price_fee_and_quantity(self):
        required=calculate_reservation(intent(kind=OrderType.LIMIT,limit="110"));reduced=reduce_reservation(current_amount=required.amount,current_quantity=Decimal("1"),fill_quantity=Decimal("0.4"),fill_price=Decimal("105"),fill_fee=Decimal("0.042"),fee_currency="USDT",reservation_currency="USDT",side="buy",accounting_mode="spot")
        self.assertEqual(reduced.amount,Decimal("68.068"));self.assertEqual(reduced.quantity,Decimal("0.6"))


@unittest.skipUnless(RUN,"requires real PostgreSQL 16")
class FourthAuditPostgresTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import psycopg
        cls.connection=psycopg.connect(host=os.environ["POSTGRES_HOST"],port=int(os.environ["POSTGRES_PORT"]),dbname=os.environ["POSTGRES_DB"],user=os.environ["POSTGRES_USER"],password=os.environ["POSTGRES_PASSWORD"],sslmode=os.environ.get("POSTGRES_SSLMODE","disable"))
    @classmethod
    def tearDownClass(cls):cls.connection.close()
    def setUp(self):
        with self.connection.cursor() as cursor:cursor.execute("TRUNCATE execution.paper_runs,execution.paper_configuration_snapshots,execution.paper_credential_references CASCADE")
        self.connection.commit();self.repo=DurablePostgresPaperRepository(self.connection)
    def runtime(self,label):
        clock=Clock();configuration=dataclasses.replace(internal_demo_configuration(persistence_required=True),account_reference="fourth-"+label);run_id=deterministic_paper_uuid("fourth-audit-run",{"label":label,"config":configuration.config_sha256});venue=InternalPaperVenue(account_reference=configuration.account_reference,initial_balances={"USDT":Decimal("10000")});snapshot=venue.fetch_account_snapshot(run_id,clock());credential=CredentialReference(PaperProvider.INTERNAL,"none-"+label,CredentialSourceType.INJECTED_TEST,sha256_payload(label)[:16]);report=PaperPreflightEngine().evaluate(paper_run_id=run_id,configuration=configuration,account_snapshot=snapshot,credential_reference=credential,evidence=PaperPreflightEvidence.verified_internal(clock(),postgresql_required=True),evaluated_at_utc=clock(),implementation_sha256=H);approval=ApprovalController().create(report=report,configuration=configuration,snapshot=snapshot,credential_reference=credential,created_at_utc=clock(),ttl_seconds=600,actor="audit",nonce=label,maximum_total_notional=Decimal("1000"));manifest=create_manifest(configuration=configuration,report=report,approval=approval,snapshot=snapshot,credential_reference=credential,implementation_sha256=H,repository_commit_sha="audit",strategy_run_reference="public-test",start_at_utc=clock());kill=PaperKillSwitch(run_id,KillSwitchState.ARMED,None,clock());controller=PaperKillSwitchController(kill,persist=lambda value,event:self.repo.persist_kill_event(value,event));accounting=PaperAccounting(paper_run_id=run_id,account_reference=configuration.account_reference,balances={b.currency:b.total for b in snapshot.balances});broker=PaperBroker(configuration=configuration,manifest=manifest,approval=approval,venue=venue,accounting=accounting,kill_switch=controller,clock=clock,repository=self.repo,worker_id=label);engine=PaperTradingEngine(configuration=configuration,broker=broker,reconciliation_engine=PaperReconciliationEngine(),kill_switch=controller,repository=self.repo,clock=clock);engine.start(report=report,approval=approval,snapshot=snapshot,credential_reference=credential,approval_controller=ApprovalController());self.repo.record_market_data_evidence(run_id,market_evidence(at=clock()),recorded_at_utc=clock());return engine,venue,clock
    def test_ack_event_survives_process_loss_without_duplicate_ack(self):
        engine,venue,clock=self.runtime("ack-restart");value=intent(run=engine.run.paper_run_id,signal="ack-restart");engine.submit(value,risk(value));client=engine.broker.submissions[0].client_order_id;venue.acknowledge(client,clock.advance());run=engine.run.paper_run_id;del engine,venue
        fresh=reconstruct_internal_paper_runtime(repository=DurablePostgresPaperRepository(self.connection),paper_run_id=run,clock=clock);self.assertEqual(fresh.broker.venue.query_order(client).state,VenueOrderState.ACKNOWLEDGED);events=self.repo.load_internal_venue_events(run);self.assertEqual(sum(1 for row in events if row["event_type"]=="acknowledged"),1)
    def test_fill_before_sync_is_replayed_and_accounted_exactly_once(self):
        engine,venue,clock=self.runtime("fill-crash");value=intent(run=engine.run.paper_run_id,signal="fill-crash");engine.submit(value,risk(value));client=engine.broker.submissions[0].client_order_id;venue.acknowledge(client,clock.advance());engine.broker.query_order(client);venue.fill(client,Decimal("1"),Decimal("100"),clock.advance(),venue_fill_id="offline-full");run=engine.run.paper_run_id;del engine,venue
        fresh=reconstruct_internal_paper_runtime(repository=DurablePostgresPaperRepository(self.connection),paper_run_id=run,clock=clock);fresh.broker.query_order(client);state=self.repo.load_state_bundle(run);self.assertEqual(len(state["fills"]),1);self.assertEqual(state["risk_state"]["open_order_count"],0);self.assertEqual(state["reservations"][0]["state"],"consumed");fresh.broker.query_order(client);self.assertEqual(len(self.repo.load_state_bundle(run)["fills"]),1)
    def test_multi_fill_recovery_child_failure_rolls_back_all_accounting(self):
        engine,venue,clock=self.runtime("multi-fill-rollback");value=intent(run=engine.run.paper_run_id,quantity="2",target="2",signal="multi-fill-rollback");engine.submit(value,risk(value));client=engine.broker.submissions[0].client_order_id;venue.acknowledge(client,clock.advance());engine.broker.query_order(client);venue.fill(client,Decimal("1"),Decimal("100"),clock.advance(),venue_fill_id="recovery-1");venue.fill(client,Decimal("1"),Decimal("101"),clock.advance(),venue_fill_id="recovery-2");original=self.repo.persist_fill_bundle;calls={"value":0}
        def injected(**kwargs):
            calls["value"]+=1
            if calls["value"]==2:kwargs["fail_at"]="fee"
            return original(**kwargs)
        self.repo.persist_fill_bundle=injected
        try:
            with self.assertRaises(RuntimeError):engine.broker.query_order(client)
        finally:self.repo.persist_fill_bundle=original
        state=self.repo.load_state_bundle(engine.run.paper_run_id);self.assertEqual(len(state["fills"]),0);self.assertEqual(state["reservations"][0]["state"],"open");self.assertEqual(state["risk_state"]["open_order_count"],1)
    def test_incomplete_filled_evidence_keeps_reservation_and_budget_open(self):
        engine,venue,clock=self.runtime("incomplete-fill");value=intent(run=engine.run.paper_run_id,signal="incomplete-fill");engine.submit(value,risk(value));client=engine.broker.submissions[0].client_order_id;venue.acknowledge(client,clock.advance());engine.broker.query_order(client);venue.fill(client,Decimal("1"),Decimal("100"),clock.advance(),venue_fill_id="hidden-fill");hidden=venue._fills.pop("hidden-fill");engine.broker.query_order(client);state=self.repo.load_state_bundle(engine.run.paper_run_id);self.assertEqual(state["order_projections"][0]["authority_state"],"pending_recovery");self.assertEqual(state["reservations"][0]["state"],"open");self.assertEqual(state["risk_state"]["open_order_count"],1);venue._fills["hidden-fill"]=hidden;engine.broker.query_order(client);state=self.repo.load_state_bundle(engine.run.paper_run_id);self.assertEqual(state["order_projections"][0]["authority_state"],"filled");self.assertEqual(state["risk_state"]["open_order_count"],0)
    def test_durable_expiry_releases_only_after_confirmed_observation(self):
        engine,venue,clock=self.runtime("expiry");value=intent(run=engine.run.paper_run_id,signal="expiry");engine.submit(value,risk(value));client=engine.broker.submissions[0].client_order_id;venue.acknowledge(client,clock.advance());engine.broker.query_order(client);engine.broker.expire_remaining_orders(expired_at_utc=clock.advance());state=self.repo.load_state_bundle(engine.run.paper_run_id);self.assertEqual(state["expiry"][0]["state"],"expiry_confirmed");self.assertEqual(state["reservations"][0]["state"],"released");self.assertEqual(state["risk_state"]["open_order_count"],0)
    def test_expired_dispatch_claim_routes_ordinary_query_to_recovery_generation(self):
        engine,venue,clock=self.runtime("expired-dispatch");value=intent(run=engine.run.paper_run_id,signal="expired-dispatch");submission,_=self.repo.prepare_submission(configuration=engine.configuration,approval=engine.broker.approval,manifest=engine.broker.manifest,intent=value,risk_decision=risk(value),now=clock(),market_evidence=market_evidence(at=clock()),evidence={"maximum_fee_bps":venue.fee_bps});self.repo.claim_dispatch(submission,worker_id="expired-dispatch",at_utc=clock());clock.advance(31);self.assertIsNone(engine.broker.query_order(submission.client_order_id));row=self.repo._fetchone("SELECT state,recovery_generation FROM execution.paper_dispatch_outbox WHERE submission_id=%s",(submission.submission_id,));self.assertEqual(row["state"],"unknown");self.assertEqual(row["recovery_generation"],1);self.assertEqual(venue.submit_call_count,0)
    def test_expired_cancel_claim_routes_query_without_repeating_cancel(self):
        engine,venue,clock=self.runtime("expired-cancel");value=intent(run=engine.run.paper_run_id,signal="expired-cancel");engine.submit(value,risk(value));submission=engine.broker.submissions[0];venue.acknowledge(submission.client_order_id,clock.advance());engine.broker.query_order(submission.client_order_id);self.repo.prepare_cancel(submission,at_utc=clock(),maximum_cancellations_per_minute=engine.configuration.maximum_cancellations_per_minute);self.repo.claim_cancel(submission,worker_id="expired-cancel",at_utc=clock());clock.advance(31);engine.broker.query_order(submission.client_order_id);row=self.repo._fetchone("SELECT state,recovery_generation FROM execution.paper_cancel_outbox WHERE submission_id=%s",(submission.submission_id,));self.assertEqual(row["state"],"cancel_unknown");self.assertEqual(row["recovery_generation"],1);self.assertEqual(venue.cancel_call_count,0)
    def test_same_observation_identity_with_changed_state_conflicts(self):
        engine,venue,clock=self.runtime("observation-conflict");value=intent(run=engine.run.paper_run_id,signal="observation-conflict");engine.submit(value,risk(value));submission=engine.broker.submissions[0];order=venue.query_order(submission.client_order_id);changed=dataclasses.replace(order,state=VenueOrderState.ACKNOWLEDGED)
        with self.assertRaises(Phase7ConflictError):self.repo.persist_order_observation(submission,changed,observed_at_utc=clock(),source="conflict")


if __name__=="__main__":unittest.main()