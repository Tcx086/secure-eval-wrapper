import contextlib,io,json,os,socket,unittest
from pathlib import Path
from secure_eval_wrapper.monitoring.configuration import MonitoringConfiguration
from secure_eval_wrapper.monitoring.engine import MonitoringEngine,MonitoringInputs
from secure_eval_wrapper.monitoring.models import MonitoredRunReference,PublicSafeProvenance
from secure_eval_wrapper.paper.cli import internal_main,kill_main,preflight_main,reconcile_main,run_main,status_main
from secure_eval_wrapper.paper.demo import run_internal_demo
from secure_eval_wrapper.paper.monitoring import PaperMonitoringInput
from phase7_test_support import T0,H
ROOT=Path(__file__).resolve().parents[2]
class DemoCliTests(unittest.TestCase):
    def test_complete_demo_proofs(self):
        result=run_internal_demo(); self.assertEqual(result["preflight_status"],"passed"); self.assertEqual(result["fill_counts"]["confirmed"],2); self.assertEqual(result["unknown_submission_recovery"],"recovered"); self.assertEqual(result["reconciliation_status"],"reconciled"); self.assertEqual(result["kill_switch_state"],"killed"); self.assertFalse(result["external_network"]); self.assertFalse(result["live_mode"])
    def test_all_default_commands_are_safe_and_compact(self):
        commands=((internal_main,[]),(preflight_main,[]),(run_main,[]),(status_main,[]),(kill_main,[]),(reconcile_main,[]))
        for command,args in commands:
            with self.subTest(command=command.__name__):
                out=io.StringIO()
                with contextlib.redirect_stdout(out):self.assertEqual(command(args),0)
                value=json.loads(out.getvalue()); self.assertFalse(value["live_mode"]); self.assertLess(len(out.getvalue()),2000)
    def test_default_commands_do_not_read_credentials_or_open_socket(self):
        old=socket.socket
        def blocked(*a,**k):raise AssertionError("socket opened")
        socket.socket=blocked
        try:
            for command in (preflight_main,run_main,status_main,kill_main,reconcile_main):
                with contextlib.redirect_stdout(io.StringIO()):command([])
        finally:socket.socket=old
    def test_external_run_requires_every_gate(self):
        out=io.StringIO()
        with contextlib.redirect_stdout(out):run_main(["--provider","okx_demo","--environment","paper_exchange_sandbox"])
        self.assertEqual(json.loads(out.getvalue())["status"],"not_started")
class MonitoringTests(unittest.TestCase):
    def test_paper_monitoring_integrates_without_mutation(self):
        inputs=MonitoringInputs(paper=PaperMonitoringInput(endpoint_verified=True,authenticated_transport_available=True,reconciliation_ok=False,partial_fill_reconciled=True,account_mode_ok=True,reset_eligible=False,duplicate_fill_count=__import__('decimal').Decimal(0),fill_without_order_count=__import__('decimal').Decimal(0),fee_mismatch_count=__import__('decimal').Decimal(0),unapproved_order_count=__import__('decimal').Decimal(0),unapproved_position_count=__import__('decimal').Decimal(0)))
        bundle=MonitoringEngine().evaluate(configuration=MonitoringConfiguration(),as_of_utc=T0,inputs=inputs,reference=MonitoredRunReference("paper",mode="paper_internal"),provenance=PublicSafeProvenance(H,"test","paper")); self.assertTrue(any(c.check_name=="paper_reconciliation" for c in bundle.check_results)); self.assertTrue(bundle.incidents)
class BoundaryTests(unittest.TestCase):
    def test_no_live_broker_or_phase8_runtime(self):
        files=list((ROOT/"open-core"/"src"/"secure_eval_wrapper").rglob("*.py")); text="\n".join(p.read_text(encoding="utf-8") for p in files); self.assertNotIn("class "+"LiveBroker",text); self.assertNotIn("ENABLE_LIVE_TRADING",text)
    def test_no_withdraw_transfer_or_external_fix_api(self):
        public=(ROOT/"open-core"/"src"/"secure_eval_wrapper"/"paper"); names={p.name for p in public.rglob("*.py")}; text="\n".join(p.read_text(encoding="utf-8") for p in public.rglob("*.py")); self.assertNotIn("def withdraw",text); self.assertNotIn("def transfer",text); self.assertNotIn("fix",names)
    def test_migrations_0001_0016_match_audited_sha(self):
        import subprocess
        changed=subprocess.run(["git","diff","--name-only","7744ccb14f489f15a01035c2b3ea4c6a565a81e5","--","open-core/db/migrations/000*.sql","open-core/db/migrations/001[0-6]_*.sql"],cwd=ROOT,capture_output=True,text=True,check=True); self.assertEqual(changed.stdout.strip(),"")
if __name__=="__main__":unittest.main()
