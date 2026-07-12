import dataclasses,unittest
from datetime import timedelta
from decimal import Decimal
from secure_eval_wrapper.data_collection.hashing import sha256_payload
from secure_eval_wrapper.execution.models import AccountingMode,OrderSide,OrderType,TimeInForce
from secure_eval_wrapper.paper.accounting import PaperAccounting
from secure_eval_wrapper.paper.enums import *
from secure_eval_wrapper.paper.kill_switch import PaperKillSwitchController
from secure_eval_wrapper.paper.models import PaperKillSwitch,PaperOrderSubmission,deterministic_paper_uuid
from secure_eval_wrapper.paper.reconciliation import PaperReconciliationEngine
from secure_eval_wrapper.paper.venue import EconomicConflictError,UnknownSubmissionResult,VenueTimeout
from secure_eval_wrapper.paper.venues.internal import InternalFault,InternalPaperVenue
from phase7_test_support import ID,T0,intent,run_id

def submission(*,client="c1",side=OrderSide.BUY,qty="1",kind=OrderType.MARKET,limit=None,stop=None,tif=TimeInForce.GTC):
    economics={"series_identity":ID.as_dict(),"side":side,"order_type":kind,"time_in_force":tif,"accounting_mode":AccountingMode.SPOT,"quantity":Decimal(qty),"limit_price":None if limit is None else Decimal(limit),"stop_price":None if stop is None else Decimal(stop)}; run=run_id(); return PaperOrderSubmission(run,deterministic_paper_uuid("manifest",run),deterministic_paper_uuid("approval",run),deterministic_paper_uuid("intent",client),client,client,ID,side,kind,tif,AccountingMode.SPOT,Decimal(qty),Decimal("100"),Decimal(qty)*100,T0,sha256_payload(economics),limit_price=None if limit is None else Decimal(limit),stop_price=None if stop is None else Decimal(stop))
class InternalVenueTests(unittest.TestCase):
    def test_ack_partial_full_and_duplicate_fill(self):
        v=InternalPaperVenue(); s=submission(); o=v.submit_order(s); self.assertEqual(o.state,VenueOrderState.PENDING_ACK); v.acknowledge("c1",T0); o,f,applied=v.fill("c1",Decimal("0.4"),Decimal("100"),T0,venue_fill_id="f1"); self.assertTrue(applied); self.assertEqual(o.state,VenueOrderState.PARTIALLY_FILLED); o2,f2,applied2=v.fill("c1",Decimal("0.4"),Decimal("100"),T0,venue_fill_id="f1"); self.assertFalse(applied2); self.assertEqual(f.fill_id,f2.fill_id); self.assertEqual(v.fill("c1",Decimal("0.6"),Decimal("100"),T0,venue_fill_id="f2")[0].state,VenueOrderState.FILLED)
    def test_same_client_id_idempotent_changed_economics_conflicts(self):
        v=InternalPaperVenue(); first=v.submit_order(submission()); self.assertEqual(v.submit_order(submission()).venue_order_id,first.venue_order_id)
        with self.assertRaises(EconomicConflictError):v.submit_order(submission(qty="2"))
    def test_cancel_after_partial_fill_and_repeat(self):
        v=InternalPaperVenue(); v.submit_order(submission()); v.acknowledge("c1",T0); v.fill("c1",Decimal("0.2"),Decimal("100"),T0); self.assertEqual(v.cancel_order("c1",T0).state,VenueOrderState.CANCEL_PENDING); done=v.complete_cancel("c1",T0); self.assertEqual(v.cancel_order("c1",T0),done)
    def test_rejection_expiry_and_ioc(self):
        v=InternalPaperVenue(); v.submit_order(submission(client="reject")); self.assertEqual(v.reject("reject",T0).state,VenueOrderState.REJECTED); v.submit_order(submission(client="expire")); self.assertEqual(v.expire("expire",T0).state,VenueOrderState.EXPIRED); v.submit_order(submission(client="ioc",kind=OrderType.LIMIT,limit="90",tif=TimeInForce.IOC)); v.acknowledge("ioc",T0); v.on_market_event(at_utc=T0,prices={"BTC-USDT":Decimal("100")}); self.assertEqual(v.query_order("ioc").state,VenueOrderState.EXPIRED)
    def test_stop_and_stop_limit(self):
        v=InternalPaperVenue()
        for s in (submission(client="stop",kind=OrderType.STOP,stop="105"),submission(client="sl",kind=OrderType.STOP_LIMIT,stop="105",limit="106")):
            v.submit_order(s); v.acknowledge(s.client_order_id,T0)
        self.assertEqual(len(v.on_market_event(at_utc=T0,prices={"BTC-USDT":Decimal("105")})),2)
    def test_unknown_timeout_duplicate_ack_and_sequence_gap_faults(self):
        v=InternalPaperVenue(faults=(InternalFault(InternalPaperFaultType.UNKNOWN_SUBMISSION,"u"),InternalFault(InternalPaperFaultType.ACK_TIMEOUT,"t"),InternalFault(InternalPaperFaultType.DUPLICATE_ACK,"d"),InternalFault(InternalPaperFaultType.SEQUENCE_GAP,"d")))
        with self.assertRaises(UnknownSubmissionResult):v.submit_order(submission(client="u"))
        with self.assertRaises(VenueTimeout):v.submit_order(submission(client="t"))
        v.submit_order(submission(client="d")); v.acknowledge("d",T0); self.assertTrue(any(e["kind"]=="duplicate_ack" for e in v.events)); self.assertGreater(v.sequence,len(v.events))
    def test_cancel_timeout_snapshot_lag_and_restart_reconstruction(self):
        v=InternalPaperVenue(faults=(InternalFault(InternalPaperFaultType.CANCEL_TIMEOUT,"c1"),InternalFault(InternalPaperFaultType.STALE_SNAPSHOT,None))); v.submit_order(submission()); v.acknowledge("c1",T0)
        with self.assertRaises(VenueTimeout):v.cancel_order("c1",T0)
        self.assertEqual(v.fetch_account_snapshot(run_id(),T0).status,AccountSnapshotStatus.STALE); restored=InternalPaperVenue().reconstruct(tuple(v._orders.values()),v.fetch_fills(),v.events); self.assertEqual(restored.query_order("c1").state,VenueOrderState.CANCEL_PENDING)
class AccountingTests(unittest.TestCase):
    def test_reservations_partial_release_and_no_overreserve(self):
        a=PaperAccounting(paper_run_id=run_id(),account_reference="a",balances={"USDT":Decimal("100"),"BTC":Decimal("1")}); s=submission(qty="0.5"); a.reserve(s); self.assertEqual(a.reserved("USDT"),Decimal("50"))
        with self.assertRaises(ValueError):a.reserve(submission(client="c2",qty="0.6"))
        v=InternalPaperVenue(initial_balances={"USDT":100,"BTC":1}); v.submit_order(s); v.acknowledge("c1",T0); _,f,_=v.fill("c1",Decimal("0.2"),Decimal("100"),T0); a.apply_fill(f); self.assertEqual(a.reserved("USDT"),Decimal("30")); a.release("c1"); self.assertEqual(a.reserved("USDT"),0)
    def test_spot_buy_sell_realized_pnl_and_duplicate_idempotency(self):
        a=PaperAccounting(paper_run_id=run_id(),account_reference="a",balances={"USDT":Decimal("1000")}); v=InternalPaperVenue(initial_balances={"USDT":1000}); buy=submission(); v.submit_order(buy); v.acknowledge("c1",T0); _,f,_=v.fill("c1",1,100,T0,venue_fill_id="buy"); self.assertTrue(a.apply_fill(f)); self.assertFalse(a.apply_fill(f)); sell=submission(client="sell",side=OrderSide.SELL,qty="0.5"); v.submit_order(sell); v.acknowledge("sell",T0); _,sf,_=v.fill("sell",Decimal("0.5"),Decimal("120"),T0,venue_fill_id="sell"); a.apply_fill(sf); pos=next(iter(a.positions.values())); self.assertEqual(pos.quantity,Decimal("0.5")); self.assertEqual(pos.realized_pnl,Decimal("10.0"))
    def test_negative_cash_and_inventory_forbidden(self):
        a=PaperAccounting(paper_run_id=run_id(),account_reference="a",balances={"USDT":Decimal("10")})
        with self.assertRaises(ValueError):a.reserve(submission())
class ReconciliationTests(unittest.TestCase):
    def test_reconciled_then_balance_and_status_mismatch_blocked(self):
        v=InternalPaperVenue(); run=run_id(); s=submission(); v.submit_order(s); v.acknowledge("c1",T0); a=PaperAccounting(paper_run_id=run,account_reference=v.account_reference,balances={"USDT":Decimal("10000")}); a.reserve(s); local=a.snapshot(at_utc=T0,venue_sequence=v.sequence); remote=v.fetch_account_snapshot(run,T0); engine=PaperReconciliationEngine(); result,diffs=engine.reconcile(paper_run_id=run,local_snapshot=local,venue_snapshot=remote,local_orders=(v.query_order("c1"),),venue_orders=(v.query_order("c1"),),local_fills=(),venue_fills=(),at_utc=T0); self.assertEqual(result.status,ReconciliationStatus.RECONCILED); changed=dataclasses.replace(remote,balances=(dataclasses.replace(remote.balances[0],total=Decimal("9999"),available=Decimal("9899")),),snapshot_id=None); result,diffs=engine.reconcile(paper_run_id=run,local_snapshot=local,venue_snapshot=changed,local_orders=(),venue_orders=(v.query_order("c1"),),local_fills=(),venue_fills=(),at_utc=T0); self.assertEqual(result.status,ReconciliationStatus.BLOCKED); self.assertTrue({d.difference_type for d in diffs}.issuperset({ReconciliationDifferenceType.BALANCE_MISMATCH,ReconciliationDifferenceType.VENUE_ORDER_MISSING_LOCALLY}))
    def test_unknown_submission_is_unknown(self):
        v=InternalPaperVenue(faults=(InternalFault(InternalPaperFaultType.UNKNOWN_SUBMISSION,"c1"),)); s=submission()
        with self.assertRaises(UnknownSubmissionResult):v.submit_order(s)
        local=InternalPaperVenue().fetch_account_snapshot(run_id(),T0); remote=v.fetch_account_snapshot(run_id(),T0); sub=dataclasses.replace(s,state=PaperOrderState.SUBMISSION_UNKNOWN); result,diffs=PaperReconciliationEngine().reconcile(paper_run_id=run_id(),local_snapshot=local,venue_snapshot=remote,local_orders=(sub,),venue_orders=tuple(v._orders.values()),local_fills=(),venue_fills=(),at_utc=T0); self.assertEqual(result.status,ReconciliationStatus.UNKNOWN)
class KillTests(unittest.TestCase):
    def test_trigger_persists_across_controller_restart_and_requires_documented_final(self):
        initial=PaperKillSwitch(run_id(),KillSwitchState.ARMED,None,T0); c=PaperKillSwitchController(initial); killed=c.trigger(KillSwitchReason.MANUAL,at_utc=T0,evidence={"x":1}); restarted=PaperKillSwitchController(killed); self.assertFalse(restarted.accepts_new_orders)
        with self.assertRaises(ValueError):restarted.finalize(at_utc=T0,terminal_handling_documented=False)
        self.assertEqual(restarted.finalize(at_utc=T0,terminal_handling_documented=True).state,KillSwitchState.KILLED)
if __name__=="__main__":unittest.main()
