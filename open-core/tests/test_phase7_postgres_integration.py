import os,unittest
from dataclasses import replace
from datetime import timedelta
from decimal import Decimal
from secure_eval_wrapper.data_collection.hashing import sha256_payload
from secure_eval_wrapper.paper.approval import ApprovalController
from secure_eval_wrapper.paper.configuration import internal_demo_configuration
from secure_eval_wrapper.paper.demo import run_internal_demo
from secure_eval_wrapper.paper.enums import CredentialSourceType,KillSwitchState,PaperProvider,PaperRunState
from secure_eval_wrapper.paper.kill_switch import PaperKillSwitchController
from secure_eval_wrapper.paper.manifests import create_manifest
from secure_eval_wrapper.paper.models import CredentialReference,PaperKillSwitch,PaperRun,deterministic_paper_uuid
from secure_eval_wrapper.paper.persistence import PostgresPaperRepository
from secure_eval_wrapper.paper.preflight import PaperPreflightEngine,PaperPreflightEvidence
from secure_eval_wrapper.paper.venues.internal import InternalPaperVenue
from phase7_test_support import H,T0
RUN=os.environ.get("RUN_POSTGRES_INTEGRATION","").lower()=="true"
@unittest.skipUnless(RUN,"requires real PostgreSQL 16")
class Phase7PostgresTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import psycopg
        cls.connection=psycopg.connect(host=os.environ["POSTGRES_HOST"],port=int(os.environ["POSTGRES_PORT"]),dbname=os.environ["POSTGRES_DB"],user=os.environ["POSTGRES_USER"],password=os.environ["POSTGRES_PASSWORD"],sslmode=os.environ.get("POSTGRES_SSLMODE","disable")); cls.repo=PostgresPaperRepository(cls.connection)
    @classmethod
    def tearDownClass(cls):cls.connection.close()
    def setUp(self):
        with self.connection.cursor() as c:c.execute("DELETE FROM execution.paper_runs"); c.execute("DELETE FROM execution.paper_credential_references")
        self.connection.commit()
    def counts(self):
        names=("paper_runs","paper_run_manifests","paper_preflight_reports","paper_approvals","paper_order_submissions","paper_orders","paper_order_events","paper_fills","paper_fee_entries","paper_account_snapshots","paper_reconciliations","paper_recovery_records","paper_kill_switches","paper_kill_switch_events","paper_rate_limit_events","paper_transport_attempts")
        with self.connection.cursor() as c:
            result={}
            for n in names:c.execute(f"SELECT count(*) FROM execution.{n}"); result[n]=c.fetchone()[0]
        return result
    def bundle(self,label="ok"):
        c=replace(internal_demo_configuration(persistence_required=True),account_reference="paper-"+label); run=deterministic_paper_uuid("pg-run",{"label":label,"config":c.config_sha256}); venue=InternalPaperVenue(account_reference=c.account_reference); snap=venue.fetch_account_snapshot(run,T0); ref=CredentialReference(PaperProvider.INTERNAL,"none-"+label,CredentialSourceType.INJECTED_TEST,sha256_payload(label)[:16]); report=PaperPreflightEngine().evaluate(paper_run_id=run,configuration=c,account_snapshot=snap,credential_reference=ref,evidence=PaperPreflightEvidence.verified_internal(T0),evaluated_at_utc=T0,implementation_sha256=H); controller=ApprovalController(); approval=controller.create(report=report,configuration=c,snapshot=snap,credential_reference=ref,created_at_utc=T0,ttl_seconds=60,actor="pg-test",nonce=label,maximum_total_notional=Decimal("500")); manifest=create_manifest(configuration=c,report=report,approval=approval,snapshot=snap,credential_reference=ref,implementation_sha256=H,repository_commit_sha="test",strategy_run_reference="test",start_at_utc=T0); run_value=PaperRun(run,manifest.manifest_id,PaperRunState.RUNNING,T0,T0); kill=PaperKillSwitch(run,KillSwitchState.ARMED,None,T0); return dict(run=run_value,configuration=c,credential_reference=ref,snapshot=snap,report=report,approval=approval,manifest=manifest,kill_switch=kill)
    def test_complete_internal_cli_persistence_and_replay_idempotency(self):
        first=run_internal_demo(persist_repository=self.repo); before=self.counts(); second=run_internal_demo(persist_repository=self.repo); self.assertEqual(first["paper_run_id"],second["paper_run_id"]); self.assertEqual(before,self.counts()); self.assertGreater(before["paper_fills"],0); self.assertGreater(before["paper_transport_attempts"],0); self.assertGreater(before["paper_rate_limit_events"],0)
    def test_start_run_rollback_matrix(self):
        for fail in ("credential","paper_run","snapshot","balance","position","preflight","check","approval","manifest","kill_switch"):
            with self.subTest(fail=fail):
                self.setUp(); bundle=self.bundle(fail)
                with self.assertRaises(RuntimeError):self.repo.persist_start_run(**bundle,fail_at=fail)
                self.assertEqual(self.counts()["paper_runs"],0)
    def test_submission_rollback_matrix(self):
        class Failing(PostgresPaperRepository):
            fail="transport"
            def persist_submission_outcome(self,**kwargs):return super().persist_submission_outcome(**kwargs,fail_at=self.fail)
        for fail in ("submission","transport","venue_order","order_event"):
            with self.subTest(fail=fail):
                self.setUp(); repo=Failing(self.connection); repo.fail=fail
                with self.assertRaises(RuntimeError):run_internal_demo(persist_repository=repo)
                counts=self.counts(); self.assertEqual(counts["paper_orders"],0); self.assertEqual(counts["paper_order_events"],0); self.assertEqual(counts["paper_transport_attempts"],0)
    def test_fill_rollback_matrix(self):
        class Failing(PostgresPaperRepository):
            fail="fill"
            def persist_fill_bundle(self,**kwargs):return super().persist_fill_bundle(**kwargs,fail_at=self.fail)
        for fail in ("fill","fee","snapshot","balance","position","reconciliation","difference","lifecycle"):
            with self.subTest(fail=fail):
                self.setUp(); repo=Failing(self.connection); repo.fail=fail
                with self.assertRaises(RuntimeError):run_internal_demo(persist_repository=repo)
                counts=self.counts(); self.assertEqual(counts["paper_fills"],0); self.assertEqual(counts["paper_fee_entries"],0); self.assertEqual(counts["paper_reconciliations"],0)
    def test_kill_restart_recovery_and_complete_reconstruction(self):
        result=run_internal_demo(persist_repository=self.repo); run=__import__('uuid').UUID(result["paper_run_id"]); kill=self.repo.get_kill_switch(run); self.assertEqual(kill["state"],"killed"); self.assertEqual(self.repo.get_active_run(run)["state"],"killed"); self.assertIsNotNone(self.repo.get_manifest(run)); self.assertEqual(len(self.repo.list_unresolved_submissions(run)),0)
    def test_half_open_lifecycle_and_no_orphans(self):
        result=run_internal_demo(persist_repository=self.repo); run=__import__('uuid').UUID(result["paper_run_id"]); rows=self.repo.list_lifecycle(run,T0,T0+timedelta(seconds=100)); self.assertTrue(rows)
        with self.connection.cursor() as c:c.execute("SELECT count(*) FROM execution.paper_fills f LEFT JOIN execution.paper_order_submissions s ON s.submission_id=f.submission_id WHERE s.submission_id IS NULL"); self.assertEqual(c.fetchone()[0],0)
    def test_persisted_rows_contain_no_credentials(self):
        run_internal_demo(persist_repository=self.repo)
        with self.connection.cursor() as c:c.execute("SELECT string_agg(row_to_json(t)::text,' ') FROM execution.paper_credential_references t"); text=c.fetchone()[0] or ""
        for secret in ("injected-secret","passphrase","authorization","OK-ACCESS-SIGN"):self.assertNotIn(secret,text)
if __name__=="__main__":unittest.main()
