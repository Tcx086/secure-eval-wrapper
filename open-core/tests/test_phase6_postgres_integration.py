from __future__ import annotations
import os,unittest
from dataclasses import replace
from datetime import datetime,timezone
from secure_eval_wrapper.fix.messages import logon
from secure_eval_wrapper.fix.models import FixDirection,FixSessionConfiguration
from secure_eval_wrapper.fix.session import SimulatedFixSession
from secure_eval_wrapper.monitoring.cli import build_demo_bundle
from secure_eval_wrapper.monitoring.persistence import MonitoringBundlePersistenceError,persist_fix_transition,persist_monitoring_bundle
from secure_eval_wrapper.storage.postgres.phase6_repositories import Phase6ConflictError,PostgresPhase6Repository
T=datetime(2026,1,1,tzinfo=timezone.utc)
@unittest.skipUnless(os.environ.get("RUN_POSTGRES_INTEGRATION","").lower()=="true","real PostgreSQL integration is explicitly gated")
class RealPostgresPhase6Tests(unittest.TestCase):
 @classmethod
 def setUpClass(cls):
  import psycopg
  cls.connection=psycopg.connect(host=os.environ["POSTGRES_HOST"],port=int(os.environ["POSTGRES_PORT"]),dbname=os.environ["POSTGRES_DB"],user=os.environ["POSTGRES_USER"],password=os.environ["POSTGRES_PASSWORD"],sslmode=os.environ.get("POSTGRES_SSLMODE","disable")); cls.bundle=build_demo_bundle(); cls.repo=PostgresPhase6Repository(cls.connection)
 @classmethod
 def tearDownClass(cls): cls.cleanup(); cls.connection.close()
 @classmethod
 def cleanup(cls):
  try:
   with cls.connection.cursor() as c:
    c.execute("DELETE FROM monitoring.monitoring_runs WHERE monitoring_run_id=%s",(cls.bundle.run.monitoring_run_id,)); c.execute("DELETE FROM monitoring.incidents WHERE monitored_identity=%s",(cls.bundle.run.reference.monitored_identity,)); c.execute("DELETE FROM monitoring.fix_sessions WHERE session_key=%s",("CLIENT->SERVER",))
   cls.connection.commit()
  except Exception: cls.connection.rollback()
 def setUp(self): self.cleanup()
 def test_bundle_write_read_idempotency_and_half_open(self):
  first=persist_monitoring_bundle(self.repo,self.bundle); second=persist_monitoring_bundle(self.repo,self.bundle); self.assertEqual(first,second); row=self.repo.latest_health_by_component("overall"); self.assertEqual(row["health_status"],self.bundle.run.overall_status.value); self.assertEqual(len(self.repo.list_health_history("overall",T.replace(year=2025),T.replace(year=2027))),1)
 def test_same_identity_different_hash_conflicts(self):
  persist_monitoring_bundle(self.repo,self.bundle); changed=replace(self.bundle.check_results[0],explanation="different public explanation")
  with self.assertRaises(Phase6ConflictError): self.repo.record_health_check_result(changed)
  self.connection.rollback()
 def test_incident_persistence(self):
  persist_monitoring_bundle(self.repo,self.bundle); rows=self.repo.list_open_incidents(); self.assertGreaterEqual(len(rows),1)
 def test_fix_session_message_state_atomic(self):
  session=SimulatedFixSession(FixSessionConfiguration("CLIENT","SERVER")); outbound=session.connect(T); inbound=logon(1,"SERVER","CLIENT",T); session.receive(inbound,T); persist_fix_transition(self.repo,session=session,at_utc=T,inbound_messages=(inbound,),outbound_messages=(outbound,),session_events=tuple(session.events)); row=self.repo.get_fix_session(session.fix_session_id); self.assertEqual(row["state"],"established"); self.assertEqual(len(self.repo.list_fix_messages(session.fix_session_id,FixDirection.INBOUND,1,2)),1)
 def test_bundle_rollback(self):
  class Failing:
   def __init__(self,repo): self.repo=repo
   def __getattr__(self,n):
    if n=="record_health_snapshot": return lambda v: (_ for _ in ()).throw(RuntimeError("injected child failure"))
    return getattr(self.repo,n)
  with self.assertRaises(MonitoringBundlePersistenceError): persist_monitoring_bundle(Failing(self.repo),self.bundle)
  with self.connection.cursor() as c: c.execute("SELECT count(*) FROM monitoring.monitoring_runs WHERE monitoring_run_id=%s",(self.bundle.run.monitoring_run_id,)); self.assertEqual(c.fetchone()[0],0)
if __name__=="__main__": unittest.main()