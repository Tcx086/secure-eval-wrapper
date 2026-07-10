from __future__ import annotations
import dataclasses,unittest
from datetime import datetime,timedelta,timezone
from decimal import Decimal
from secure_eval_wrapper.monitoring.configuration import MonitoringConfiguration
from secure_eval_wrapper.monitoring.data_health import DataHealthInput,DataRecordSummary
from secure_eval_wrapper.monitoring.engine import MonitoringEngine,MonitoringInputs
from secure_eval_wrapper.monitoring.execution_health import ExecutionHealthInput
from secure_eval_wrapper.monitoring.health import aggregate_health
from secure_eval_wrapper.monitoring.models import *
from secure_eval_wrapper.monitoring.risk_health import RiskHealthInput
from secure_eval_wrapper.monitoring.signal_health import SignalHealthInput
from secure_eval_wrapper.monitoring.system_health import SystemHealthInput
T=datetime(2026,1,1,tzinfo=timezone.utc); P=PublicSafeProvenance("a"*64,"commit","tree")

def engine(inputs,previous=()): return MonitoringEngine().evaluate(configuration=MonitoringConfiguration(maximum_data_age_seconds={"bar:1m":Decimal("60")}),as_of_utc=T,inputs=inputs,reference=MonitoredRunReference("demo"),provenance=P,previous_incidents=previous)
class ContractTests(unittest.TestCase):
 def test_frozen(self):
  c=MonitoringConfiguration(); self.assertTrue(dataclasses.is_dataclass(c)); self.assertRaises(dataclasses.FrozenInstanceError,setattr,c,"configuration_version","x")
 def test_naive_run_rejected(self): self.assertRaises(ValueError,MonitoringEngine().evaluate,configuration=MonitoringConfiguration(),as_of_utc=datetime(2026,1,1),inputs=MonitoringInputs(),reference=MonitoredRunReference("x"),provenance=P)
 def test_hash_validation(self): self.assertRaises(ValueError,PublicSafeProvenance,"x","c","s")
 def test_operational_provenance_excluded(self):
  a=PublicSafeProvenance("a"*64,"c","s",{"host":"one"}); b=PublicSafeProvenance("a"*64,"c","s",{"host":"two"}); self.assertEqual(a.stable_payload,b.stable_payload)
 def test_threshold_inclusive(self): self.assertTrue(ThresholdComparison.GREATER_THAN_OR_EQUAL.evaluate(Decimal("1"),Decimal("1")))
class HealthTests(unittest.TestCase):
 def test_missing_is_unknown(self): self.assertEqual(engine(MonitoringInputs()).run.overall_status,HealthStatus.UNKNOWN)
 def test_fresh_and_gap(self):
  r=DataRecordSummary("a","b"*64,T-timedelta(seconds=10),open_time_utc=T-timedelta(minutes=1),open=Decimal("1"),high=Decimal("2"),low=Decimal("1"),close=Decimal("2"),volume=Decimal("1")); b=engine(MonitoringInputs(data=DataHealthInput("bar","1m",(r,),T-timedelta(minutes=1),T))); self.assertIn("data_fresh",[x.reason_code for x in b.check_results]); self.assertIn("no_bar_gap",[x.reason_code for x in b.check_results])
 def test_stale_future_duplicate_conflict(self):
  rows=(DataRecordSummary("a","1"*64,T-timedelta(minutes=2)),DataRecordSummary("a","2"*64,T+timedelta(seconds=1))); b=engine(MonitoringInputs(data=DataHealthInput("bar","1m",rows))); reasons={x.reason_code for x in b.check_results}; self.assertIn("future_timestamp",reasons); self.assertIn("economic_content_conflict",reasons)
 def test_invalid_ohlcv(self):
  r=DataRecordSummary("a","b"*64,T,open=Decimal("2"),high=Decimal("1"),low=Decimal("2"),close=Decimal("2"),volume=Decimal("-1")); self.assertIn("invalid_ohlcv",[x.reason_code for x in engine(MonitoringInputs(data=DataHealthInput("bar","1m",(r,)))).check_results])
 def test_signal_is_operational_not_pnl(self):
  b=engine(MonitoringInputs(signals=SignalHealthInput(T,"completed","completed",1,1))); self.assertTrue(all("profit" not in x.reason_code for x in b.check_results if x.category is MonitoringCategory.SIGNAL))
 def test_blocked_fill_critical(self):
  b=engine(MonitoringInputs(execution=ExecutionHealthInput(blocked_order_fill_count=1))); x=next(x for x in b.check_results if x.reason_code=="blocked_order_filled"); self.assertEqual(x.severity,Severity.CRITICAL)
 def test_reconciliation_unavailable_unknown(self):
  b=engine(MonitoringInputs(execution=ExecutionHealthInput())); x=next(x for x in b.check_results if x.check_name=="bundle_reconciliation"); self.assertEqual(x.health_status,HealthStatus.UNKNOWN)
 def test_risk_boundaries(self):
  b=engine(MonitoringInputs(risk=RiskHealthInput(decision_count=10,blocked_decision_count=5,maximum_limit_utilization=Decimal("1"),gross_exposure_utilization=Decimal("1"),current_drawdown=Decimal(".2"),equity=Decimal("100")))); self.assertEqual(next(x for x in b.check_results if x.check_name=="blocked_decision_rate").health_status,HealthStatus.UNHEALTHY)
 def test_system_unchecked_unknown(self):
  b=engine(MonitoringInputs(system=SystemHealthInput())); self.assertEqual(next(x for x in b.check_results if x.check_name=="postgresql_availability").health_status,HealthStatus.UNKNOWN)
 def test_aggregation_precedence_and_causes(self):
  b=engine(MonitoringInputs(execution=ExecutionHealthInput(blocked_order_fill_count=1),system=SystemHealthInput())); self.assertEqual(b.run.overall_status,HealthStatus.UNHEALTHY); self.assertTrue(next(s for s in b.snapshots if s.component=="overall").causing_check_ids)
class IncidentTests(unittest.TestCase):
 def test_open_update_resolve_new_episode(self):
  first=engine(MonitoringInputs(execution=ExecutionHealthInput(blocked_order_fill_count=1))); incident=next(i for i in first.incidents if i.reason_code=="blocked_order_filled"); self.assertEqual(incident.state,IncidentState.OPEN)
  second=engine(MonitoringInputs(execution=ExecutionHealthInput(blocked_order_fill_count=1)),(incident,)); updated=next(i for i in second.incidents if i.reason_code=="blocked_order_filled"); self.assertEqual(updated.incident_id,incident.incident_id); self.assertEqual(updated.occurrence_count,2)
  resolved=engine(MonitoringInputs(execution=ExecutionHealthInput(position_reconciliation_ok=True,cash_reconciliation_ok=True,account_equity_reconciliation_ok=True,complete_reconstruction_ok=True)),(updated,)); self.assertEqual(next(i for i in resolved.incidents if i.incident_id==incident.incident_id).state,IncidentState.RESOLVED)
 def test_deterministic_run(self): self.assertEqual(engine(MonitoringInputs()).run.monitoring_run_id,engine(MonitoringInputs()).run.monitoring_run_id)
if __name__=="__main__": unittest.main()