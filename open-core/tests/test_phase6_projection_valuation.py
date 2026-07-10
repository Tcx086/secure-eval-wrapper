from __future__ import annotations
import unittest
from contextlib import contextmanager
from datetime import datetime,timedelta,timezone
from decimal import Decimal
from types import SimpleNamespace
from uuid import UUID
from secure_eval_wrapper.alpha.identity import SeriesIdentity
from secure_eval_wrapper.data_collection.models import InstrumentType
from secure_eval_wrapper.execution.models import AccountingMode,MarkSource,PositionSnapshot,PositionSnapshotKind,PositionState
from secure_eval_wrapper.storage.backtest_bundle import persist_backtest_bundle
from secure_eval_wrapper.storage.postgres.phase5_rows import position_state_row
T=datetime(2026,1,1,tzinfo=timezone.utc); RUN=UUID("00000000-0000-5000-8000-000000000001"); BTR=UUID("00000000-0000-5000-8000-000000000002"); EVENT=UUID("00000000-0000-5000-8000-000000000003"); H="a"*64
ID=SeriesIdentity("fixture","fixture","BTCUSDT","BTC/USDT",InstrumentType.SPOT,"1m")
def position(q=Decimal("1"),mode=AccountingMode.SPOT): return PositionState(RUN,"acct",ID,mode,q,None if q==0 else Decimal("100"),Decimal("2"),T,H)
def snapshot(p,*,at=T,mark=Decimal("110"),q=None,unrealized=Decimal("10"),seq=1): return PositionSnapshot(RUN,"acct",p.position_id,ID,p.accounting_mode,at,p.quantity if q is None else q,None if (p.quantity if q is None else q)==0 else Decimal("100"),mark,Decimal("2"),unrealized,Decimal("0"),H,PositionSnapshotKind.BAR_CLOSE_MARK,MarkSource.BAR_CLOSE,EVENT,seq)
class ProjectionTests(unittest.TestCase):
 def test_profitable_spot_marked(self):
  p=position(); row=position_state_row(p,backtest_run_id=BTR,deterministic_ordinal=0,final_snapshot=snapshot(p)); self.assertEqual(row["valuation_status"],"marked"); self.assertEqual(row["mark_price"],Decimal("110")); self.assertGreater(row["unrealized_pnl"],0)
 def test_perpetual_marked(self):
  identity=SeriesIdentity("fixture","fixture","BTCUSDT-PERP","BTC/USDT-PERP",InstrumentType.PERPETUAL_SWAP,"1m","USDT"); p=PositionState(RUN,"acct",identity,AccountingMode.LINEAR_PERPETUAL,Decimal("-2"),Decimal("100"),Decimal("1"),T,H); s=PositionSnapshot(RUN,"acct",p.position_id,identity,AccountingMode.LINEAR_PERPETUAL,T,Decimal("-2"),Decimal("100"),Decimal("90"),Decimal("1"),Decimal("20"),Decimal("0"),H,PositionSnapshotKind.BAR_CLOSE_MARK,MarkSource.BAR_CLOSE,EVENT,1); row=position_state_row(p,backtest_run_id=BTR,deterministic_ordinal=0,final_snapshot=s); self.assertEqual(row["valuation_status"],"marked"); self.assertEqual(row["mark_price"],Decimal("90")); self.assertEqual(row["unrealized_pnl"],Decimal("20"))
 def test_losing_spot_marked(self):
  p=position(); row=position_state_row(p,backtest_run_id=BTR,deterministic_ordinal=0,final_snapshot=snapshot(p,mark=Decimal("90"),unrealized=Decimal("-10"))); self.assertLess(row["unrealized_pnl"],0)
 def test_flat_status(self):
  p=position(Decimal("0")); row=position_state_row(p,backtest_run_id=BTR,deterministic_ordinal=0,final_snapshot=snapshot(p,q=Decimal("0"),unrealized=Decimal("0"))); self.assertEqual(row["valuation_status"],"flat")
 def test_unmarked_status(self):
  p=position(); s=PositionSnapshot(RUN,"acct",p.position_id,ID,AccountingMode.SPOT,T,Decimal("1"),Decimal("100"),None,Decimal("2"),Decimal("0"),None,H,PositionSnapshotKind.FILL,None,EVENT,1,source_fill_id=UUID("00000000-0000-5000-8000-000000000004")); row=position_state_row(p,backtest_run_id=BTR,deterministic_ordinal=0,final_snapshot=s); self.assertEqual(row["valuation_status"],"unmarked"); self.assertIsNone(row["mark_price"])
 def test_hash_includes_valuation(self):
  p=position(); a=position_state_row(p,backtest_run_id=BTR,deterministic_ordinal=0,final_snapshot=snapshot(p)); b=position_state_row(p,backtest_run_id=BTR,deterministic_ordinal=0,final_snapshot=snapshot(p,mark=Decimal("111"),unrealized=Decimal("11"))); self.assertNotEqual(a["final_record_sha256"],b["final_record_sha256"])
 def test_bundle_selects_latest_snapshot(self):
  p=position(); early=snapshot(p,at=T,mark=Decimal("101"),unrealized=Decimal("1"),seq=1); late=snapshot(p,at=T+timedelta(minutes=1),mark=Decimal("110"),unrealized=Decimal("10"),seq=2)
  class Repo:
   def __init__(self): self.final=None
   @contextmanager
   def transaction(self): yield self
   def __getattr__(self,name):
    if name=="upsert_position": return lambda value,**kwargs:setattr(self,"final",kwargs["final_snapshot"])
    return lambda *args,**kwargs:None
  run=SimpleNamespace(run_id=RUN,backtest_run_id=BTR); result=SimpleNamespace(run=run,order_intents=(),risk_decisions=(),orders=(),fills=(),positions=(p,),position_snapshots=(late,early),cash_ledger_entries=(),funding_payments=(),account_snapshots=(),events=(),equity_curve=(),metric_records=()); repo=Repo(); persist_backtest_bundle(repo,result); self.assertEqual(repo.final.position_snapshot_id,late.position_snapshot_id)
if __name__=="__main__": unittest.main()