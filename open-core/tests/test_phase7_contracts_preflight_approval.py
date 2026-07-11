import dataclasses,unittest
from datetime import timedelta
from decimal import Decimal
from secure_eval_wrapper.data_collection.hashing import sha256_payload
from secure_eval_wrapper.paper.approval import ApprovalController,ApprovalError
from secure_eval_wrapper.paper.configuration import PaperRunConfiguration
from secure_eval_wrapper.paper.credentials import CredentialMaterial,InjectedCredentialProvider,redact
from secure_eval_wrapper.paper.endpoints import catalog_sha256,validate_endpoint_request
from secure_eval_wrapper.paper.enums import PaperEnvironment,PaperProvider,PreflightStatus
from secure_eval_wrapper.paper.preflight import PaperPreflightEngine,PaperPreflightEvidence
from secure_eval_wrapper.paper.venues.internal import InternalPaperVenue
from phase7_test_support import T0,H,config,credential,run_id

def setup_report(**changes):
    c=config(); run=run_id(c); snap=InternalPaperVenue().fetch_account_snapshot(run,T0); ev=dataclasses.replace(PaperPreflightEvidence.verified_internal(T0),**changes); report=PaperPreflightEngine().evaluate(paper_run_id=run,configuration=c,account_snapshot=snap,credential_reference=credential(),evidence=ev,evaluated_at_utc=T0,implementation_sha256=H); return c,run,snap,report
class ContractTests(unittest.TestCase):
    def test_configuration_is_frozen_and_hash_deterministic(self):
        c=config(); self.assertEqual(c.config_sha256,config().config_sha256)
        with self.assertRaises(dataclasses.FrozenInstanceError):c.allow_short=True
    def test_all_limits_must_be_finite_positive(self):
        for name in ("maximum_order_notional","maximum_daily_realized_loss","maximum_current_drawdown"):
            with self.subTest(name=name),self.assertRaises(ValueError):dataclasses.replace(config(),**{name:Decimal("Infinity")})
    def test_live_and_invalid_pair_rejected(self):
        with self.assertRaisesRegex(ValueError,"live"):dataclasses.replace(config(),environment=PaperEnvironment.LIVE)
        with self.assertRaises(ValueError):dataclasses.replace(config(),provider=PaperProvider.OKX_DEMO)
    def test_frozen_utc_contracts_reject_naive(self):
        c,run,snap,_=setup_report()
        with self.assertRaises(ValueError):dataclasses.replace(snap,fetched_at_utc=T0.replace(tzinfo=None),snapshot_id=None)
    def test_secret_material_has_redacted_repr(self):
        value=CredentialMaterial("KEY","top-secret","pass-secret"); self.assertNotIn("secret",repr(value).lower().replace("redacted","")); self.assertFalse(dataclasses.is_dataclass(value))
    def test_redaction_covers_headers_and_query(self):
        value=redact({"Authorization":"Bearer secret","url":"https://x/?signature=secret","nested":{"OK-ACCESS-SIGN":"secret"}}); text=str(value); self.assertNotIn("Bearer secret",text); self.assertNotIn("=secret",text)
    def test_endpoint_requires_demo_marker_and_allowlisted_route(self):
        with self.assertRaises(ValueError):validate_endpoint_request("okx_demo","paper_exchange_sandbox","GET","https://openapi.okx.com/api/v5/account/balance",{})
        self.assertEqual(validate_endpoint_request("okx_demo","paper_exchange_sandbox","GET","https://openapi.okx.com/api/v5/account/balance",{"x-simulated-trading":"1"}).value,"balances")
    def test_production_and_arbitrary_endpoints_rejected(self):
        for url in ("https://www.okx.com/api/v5/trade/order","http://openapi.okx.com/api/v5/trade/order","https://user@openapi.okx.com/api/v5/trade/order","https://openapi.okx.com:8443/api/v5/trade/order"):
            with self.subTest(url=url),self.assertRaises(ValueError):validate_endpoint_request("okx_demo","paper_exchange_sandbox","POST",url,{"x-simulated-trading":"1"})
class PreflightTests(unittest.TestCase):
    def test_valid_internal_preflight(self):self.assertEqual(setup_report()[3].status,PreflightStatus.PASSED)
    def test_empty_evidence_fails_closed(self):
        c=config(); run=run_id(c); snap=InternalPaperVenue().fetch_account_snapshot(run,T0)
        report=PaperPreflightEngine().evaluate(paper_run_id=run,configuration=c,account_snapshot=snap,credential_reference=credential(),evidence=PaperPreflightEvidence(),evaluated_at_utc=T0,implementation_sha256=H)
        self.assertEqual(report.status,PreflightStatus.FAILED); self.assertIn("requested_mode_is_paper",report.blockers)
    def test_required_evidence_fails_closed(self):
        cases=("production_endpoint_absent","market_data_available","account_exists","balances_available","positions_available","limits_complete","monitoring_available","kill_switch_available","reconciliation_repository_available")
        for name in cases:
            with self.subTest(name=name):self.assertEqual(setup_report(**{name:False})[3].status,PreflightStatus.FAILED)
    def test_stale_market_and_account_fail(self):
        c,run,snap,_=setup_report(); old=T0-timedelta(seconds=120); report=PaperPreflightEngine().evaluate(paper_run_id=run,configuration=c,account_snapshot=dataclasses.replace(snap,fetched_at_utc=old,venue_as_of_utc=old,snapshot_id=None),credential_reference=credential(),evidence=dataclasses.replace(PaperPreflightEvidence.verified_internal(T0),latest_market_data_at_utc=old),evaluated_at_utc=T0,implementation_sha256=H); self.assertIn("market_data_fresh",report.blockers); self.assertIn("account_snapshot_fresh",report.blockers)
    def test_clock_skew_existing_state_and_kill_fail(self):
        report=setup_report(venue_time_at_utc=T0-timedelta(seconds=20),unexplained_existing_positions=True,unexplained_existing_orders=True,kill_switch_active=True)[3]
        self.assertTrue({"venue_clock_skew","existing_positions","existing_orders","kill_switch"}.issubset(report.blockers))
    def test_persistence_evidence_required_only_when_configured(self):
        c=config(True); run=run_id(c); snap=InternalPaperVenue().fetch_account_snapshot(run,T0); report=PaperPreflightEngine().evaluate(paper_run_id=run,configuration=c,account_snapshot=snap,credential_reference=credential(),evidence=dataclasses.replace(PaperPreflightEvidence.verified_internal(T0),postgres_reachable=False),evaluated_at_utc=T0,implementation_sha256=H); self.assertIn("postgresql",report.blockers)
class ApprovalTests(unittest.TestCase):
    def setUp(self):self.c,self.run,self.snap,self.report=setup_report(); self.controller=ApprovalController(); self.ref=credential(); self.approval=self.controller.create(report=self.report,configuration=self.c,snapshot=self.snap,credential_reference=self.ref,created_at_utc=T0,ttl_seconds=60,actor="tester",nonce="n1",maximum_total_notional=Decimal("500"))
    def test_valid_deterministic_approval(self):
        other=ApprovalController().create(report=self.report,configuration=self.c,snapshot=self.snap,credential_reference=self.ref,created_at_utc=T0,ttl_seconds=60,actor="tester",nonce="n1",maximum_total_notional=Decimal("500")); self.assertEqual(self.approval.approval_id,other.approval_id)
    def test_expired_wrong_run_and_notional_rejected(self):
        for kwargs in ({"at_utc":T0+timedelta(seconds=60)},{"paper_run_id":run_id(dataclasses.replace(self.c,account_reference="other"))},{"requested_total_notional":Decimal("501")}):
            base=dict(paper_run_id=self.run,report=self.report,configuration=self.c,snapshot=self.snap,credential_reference=self.ref,at_utc=T0); base.update(kwargs)
            with self.subTest(kwargs=kwargs),self.assertRaises(ApprovalError):self.controller.validate(self.approval,**base)
    def test_changed_bindings_rejected(self):
        with self.assertRaises(ApprovalError):self.controller.validate(self.approval,paper_run_id=self.run,report=self.report,configuration=dataclasses.replace(self.c,maximum_order_notional=Decimal("999")),snapshot=self.snap,credential_reference=self.ref,at_utc=T0)
        changed=dataclasses.replace(self.ref,alias="changed")
        with self.assertRaises(ApprovalError):self.controller.validate(self.approval,paper_run_id=self.run,report=self.report,configuration=self.c,snapshot=self.snap,credential_reference=changed,at_utc=T0)
    def test_reuse_rejected(self):
        args=dict(paper_run_id=self.run,report=self.report,configuration=self.c,snapshot=self.snap,credential_reference=self.ref,at_utc=T0); self.controller.validate(self.approval,consume=True,**args)
        with self.assertRaises(ApprovalError):self.controller.validate(self.approval,**args)
if __name__=="__main__":unittest.main()
