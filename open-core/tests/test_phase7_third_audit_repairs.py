import copy
import dataclasses
import unittest
from datetime import timedelta
from decimal import Decimal

from secure_eval_wrapper.data_collection.hashing import sha256_payload
from secure_eval_wrapper.execution.models import AccountingMode,OrderSide,OrderType,TimeInForce
from secure_eval_wrapper.paper.accounting import PaperAccounting
from secure_eval_wrapper.paper.approval import ApprovalController
from secure_eval_wrapper.paper.broker import PaperBroker
from secure_eval_wrapper.paper.engine import PaperTradingEngine
from secure_eval_wrapper.paper.enums import KillSwitchState,PaperOrderState,PaperProvider
from secure_eval_wrapper.paper.kill_switch import PaperKillSwitchController
from secure_eval_wrapper.paper.manifests import create_manifest
from secure_eval_wrapper.paper.models import PaperKillSwitch,PaperMarketDataEvidence,PaperOrderSubmission,PaperReconciliationBundle
from secure_eval_wrapper.paper.preflight import PaperPreflightEngine,PaperPreflightEvidence
from secure_eval_wrapper.paper.reconciliation import PaperReconciliationEngine
from secure_eval_wrapper.paper.venue import EconomicConflictError
from secure_eval_wrapper.paper.venues.internal import InternalPaperVenue
from phase7_test_support import H,ID,T0,config,credential,run_id


def submission(*,client="c1",side=OrderSide.BUY,quantity="1",reference="100",limit=None,stop=None):
    run=run_id();economics=sha256_payload({"series_identity":ID.as_dict(),"side":side,"order_type":OrderType.MARKET,"time_in_force":TimeInForce.GTC,"accounting_mode":AccountingMode.SPOT,"quantity":Decimal(quantity),"limit_price":None if limit is None else Decimal(limit),"stop_price":None if stop is None else Decimal(stop)})
    return PaperOrderSubmission(run,run,run,run,client,client,ID,side,OrderType.MARKET,TimeInForce.GTC,AccountingMode.SPOT,Decimal(quantity),Decimal(reference),Decimal(quantity)*Decimal(reference),T0,economics,state=PaperOrderState.PREPARED,limit_price=None if limit is None else Decimal(limit),stop_price=None if stop is None else Decimal(stop))


def venue_state(venue):
    return (venue.sequence,copy.deepcopy(venue.events),copy.deepcopy(venue._fills),copy.deepcopy(venue._orders),copy.deepcopy(venue._balances),copy.deepcopy(venue._positions),copy.deepcopy(venue._reservations))


class InternalVenueAtomicityTests(unittest.TestCase):
    def test_buy_principal_without_fee_is_rejected_before_order_state(self):
        venue=InternalPaperVenue(initial_balances={"USDT":Decimal("100")});before=venue_state(venue)
        with self.assertRaises(ValueError):venue.submit_order(submission())
        self.assertEqual(venue_state(venue),before)
    def test_invalid_fee_currency_leaves_complete_state_unchanged(self):
        venue=InternalPaperVenue(initial_balances={"USDT":Decimal("1000")});value=submission();venue.submit_order(value);venue.acknowledge(value.client_order_id,T0);before=venue_state(venue)
        with self.assertRaises(ValueError):venue.fill(value.client_order_id,1,100,T0,fee_currency="BTC")
        self.assertEqual(venue_state(venue),before)
    def test_duplicate_fill_changed_economics_leaves_state_unchanged(self):
        venue=InternalPaperVenue(initial_balances={"USDT":Decimal("1000")});value=submission();venue.submit_order(value);venue.acknowledge(value.client_order_id,T0);venue.fill(value.client_order_id,Decimal("0.4"),100,T0,venue_fill_id="same");before=venue_state(venue)
        with self.assertRaises(EconomicConflictError):venue.fill(value.client_order_id,Decimal("0.3"),100,T0,venue_fill_id="same")
        self.assertEqual(venue_state(venue),before)
    def test_sell_without_fill_derived_inventory_is_atomic(self):
        venue=InternalPaperVenue(initial_balances={"BTC":Decimal("1"),"USDT":Decimal("1000")});value=submission(side=OrderSide.SELL);venue.submit_order(value);venue.acknowledge(value.client_order_id,T0);before=venue_state(venue)
        with self.assertRaises(ValueError):venue.fill(value.client_order_id,1,100,T0)
        self.assertEqual(venue_state(venue),before)
    def test_second_partial_fill_insufficient_cash_is_atomic(self):
        venue=InternalPaperVenue(initial_balances={"USDT":Decimal("1000")});value=submission(quantity="2");venue.submit_order(value);venue.acknowledge(value.client_order_id,T0);venue.fill(value.client_order_id,1,100,T0,venue_fill_id="first");venue._balances["USDT"]=Decimal("100");before=venue_state(venue)
        with self.assertRaises(ValueError):venue.fill(value.client_order_id,1,100,T0,venue_fill_id="second")
        self.assertEqual(venue_state(venue),before)


class MarketEvidenceTests(unittest.TestCase):
    def evidence(self,**changes):
        values=dict(series_identity=ID,provider="internal",instrument="BTC-USDT",event_type="bar_close",observation_id="bar-1",observed_at_utc=T0,available_at_utc=T0,is_final=True,validation_status="accepted",source_sha256=H,record_sha256=sha256_payload("bar-1"));values.update(changes);return PaperMarketDataEvidence(**values)
    def test_boundary_and_one_second_over_threshold(self):
        self.assertEqual(self.evidence().rejection_reasons(series_identity=ID,at_utc=T0+timedelta(seconds=60),maximum_age_seconds=60),())
        self.assertIn("market_data_stale",self.evidence().rejection_reasons(series_identity=ID,at_utc=T0+timedelta(seconds=61),maximum_age_seconds=60))
    def test_future_non_final_and_quarantined_fail_closed(self):
        self.assertIn("market_data_future",self.evidence(observed_at_utc=T0+timedelta(seconds=1),available_at_utc=T0+timedelta(seconds=1)).rejection_reasons(series_identity=ID,at_utc=T0,maximum_age_seconds=60))
        self.assertIn("market_data_non_final",self.evidence(is_final=False).rejection_reasons(series_identity=ID,at_utc=T0,maximum_age_seconds=60))
        self.assertIn("market_data_quarantined",self.evidence(validation_status="quarantined").rejection_reasons(series_identity=ID,at_utc=T0,maximum_age_seconds=60))


class AdvancingClock:
    def __init__(self):self.value=T0
    def __call__(self):self.value+=timedelta(microseconds=1);return self.value

class CountingVenue(InternalPaperVenue):
    def __init__(self,**kwargs):super().__init__(**kwargs);self.snapshot_calls=0
    def fetch_account_snapshot(self,paper_run_id,at_utc):self.snapshot_calls+=1;return super().fetch_account_snapshot(paper_run_id,at_utc)

class BundleRepository:
    def __init__(self):self.bundle=None
    def persist_reconciliation_bundle(self,**kwargs):self.bundle=kwargs["bundle"]

class ReconciliationBundleTests(unittest.TestCase):
    def test_engine_persists_the_identical_bundle_without_refetch(self):
        clock=AdvancingClock();configuration=config();run=run_id(configuration);venue=CountingVenue();snapshot=venue.fetch_account_snapshot(run,clock());report=PaperPreflightEngine().evaluate(paper_run_id=run,configuration=configuration,account_snapshot=snapshot,credential_reference=credential(),evidence=PaperPreflightEvidence.verified_internal(clock()),evaluated_at_utc=clock(),implementation_sha256=H);approval=ApprovalController().create(report=report,configuration=configuration,snapshot=snapshot,credential_reference=credential(),created_at_utc=clock(),ttl_seconds=300,actor="audit",nonce="bundle",maximum_total_notional=Decimal("1000"));manifest=create_manifest(configuration=configuration,report=report,approval=approval,snapshot=snapshot,credential_reference=credential(),implementation_sha256=H,repository_commit_sha="audit",strategy_run_reference="bundle",start_at_utc=clock());kill=PaperKillSwitch(run,KillSwitchState.ARMED,None,clock());controller=PaperKillSwitchController(kill);accounting=PaperAccounting(paper_run_id=run,account_reference=configuration.account_reference,balances={b.currency:b.total for b in snapshot.balances});broker=PaperBroker(configuration=configuration,manifest=manifest,approval=approval,venue=venue,accounting=accounting,kill_switch=controller,clock=clock,fixture_mode=True);engine=PaperTradingEngine(configuration=configuration,broker=broker,reconciliation_engine=PaperReconciliationEngine(),kill_switch=controller,clock=clock);engine.start(report=report,approval=approval,snapshot=snapshot,credential_reference=credential(),approval_controller=ApprovalController());repository=BundleRepository();engine.repository=repository;before=venue.snapshot_calls;bundle=engine.reconcile();self.assertIsInstance(bundle,PaperReconciliationBundle);self.assertIs(repository.bundle,bundle);self.assertEqual(venue.snapshot_calls,before+1);self.assertEqual(bundle.reconciliation.local_snapshot_id,bundle.local_snapshot.snapshot_id);self.assertEqual(bundle.reconciliation.venue_snapshot_id,bundle.venue_snapshot.snapshot_id)

if __name__=="__main__":unittest.main()