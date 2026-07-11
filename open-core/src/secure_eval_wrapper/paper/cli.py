"""Safe Phase 7 command entry points."""
from __future__ import annotations
import argparse,json,os,sys
from datetime import datetime,timezone
from decimal import Decimal
from uuid import UUID
from .demo import run_internal_demo

def _print(value):print(json.dumps(value,sort_keys=True,separators=(",",":"),default=str))
def _repository():
    if os.environ.get("ENABLE_POSTGRES_PERSISTENCE","").lower()!="true":raise RuntimeError("PostgreSQL persistence requires ENABLE_POSTGRES_PERSISTENCE=true")
    from secure_eval_wrapper.storage.postgres.config import load_postgres_config
    config=load_postgres_config(); import psycopg
    from .persistence import PostgresPaperRepository
    connection=psycopg.connect(**config.to_connection_kwargs()); return connection,PostgresPaperRepository(connection)
def internal_main(argv=None):
    p=argparse.ArgumentParser(prog="secure-eval-paper-internal"); p.add_argument("--persist",action="store_true"); args=p.parse_args(argv); connection=None
    try:
        repo=None
        if args.persist:connection,repo=_repository()
        _print(run_internal_demo(persist_repository=repo)); return 0
    finally:
        if connection:connection.close()
def preflight_main(argv=None):
    p=argparse.ArgumentParser(prog="secure-eval-paper-preflight"); p.add_argument("--provider",choices=("internal","okx_demo"),default="internal"); p.add_argument("--environment",choices=("paper_internal","paper_exchange_sandbox"),default="paper_internal"); p.add_argument("--create-approval",action="store_true"); args=p.parse_args(argv)
    if args.provider=="okx_demo":
        passed=args.environment=="paper_exchange_sandbox" and os.environ.get("ENABLE_PAPER_TRADING","").lower()=="true"
        _print({"provider":"okx_demo","environment":args.environment,"preflight_status":"requires_authenticated_evidence" if passed else "failed","approval_created":False,"credentials_loaded":False,"network_attempted":False,"live_mode":False}); return 0 if passed else 2
    if args.environment!="paper_internal":raise SystemExit("internal provider requires paper_internal")
    summary=run_internal_demo(); _print({"paper_run_id":summary["paper_run_id"],"provider":"internal","environment":"paper_internal","preflight_status":summary["preflight_status"],"approval_created":bool(args.create_approval),"approval_status":summary["approval_status"] if args.create_approval else None,"credentials_loaded":False,"network_attempted":False,"live_mode":False}); return 0
def run_main(argv=None):
    p=argparse.ArgumentParser(prog="secure-eval-paper-run"); p.add_argument("--provider",choices=("okx_demo",)); p.add_argument("--environment",choices=("paper_exchange_sandbox",)); p.add_argument("--manifest"); p.add_argument("--approval"); p.add_argument("--persist",action="store_true"); args=p.parse_args(argv)
    requested=all((args.provider,args.environment,args.manifest,args.approval,args.persist))
    if not requested:_print({"status":"not_started","reason":"explicit provider, environment, manifest, approval, and persistence are required","credentials_loaded":False,"network_attempted":False,"live_mode":False}); return 0
    if os.environ.get("ENABLE_PAPER_TRADING","").lower()!="true" or os.environ.get("ENABLE_POSTGRES_PERSISTENCE","").lower()!="true":raise SystemExit("external demo requires ENABLE_PAPER_TRADING=true and ENABLE_POSTGRES_PERSISTENCE=true")
    missing=[n for n in ("OKX_DEMO_API_KEY","OKX_DEMO_SECRET_KEY","OKX_DEMO_PASSPHRASE") if not os.environ.get(n)]
    if missing:raise SystemExit("required local OKX demo credential variables are missing")
    _print({"status":"ready_no_order_stream","provider":"okx_demo","environment":"paper_exchange_sandbox","manifest":args.manifest,"approval":args.approval,"persistence":"postgresql_required","credentials_loaded":False,"network_attempted":False,"live_mode":False}); return 0
def status_main(argv=None):
    p=argparse.ArgumentParser(prog="secure-eval-paper-status"); p.add_argument("--run-id"); p.add_argument("--persist",action="store_true"); args=p.parse_args(argv)
    if not args.run_id or not args.persist:_print({"status":"no_query","postgresql_connected":False,"live_mode":False}); return 0
    connection,repo=_repository()
    try:_print({"paper_run":repo.get_active_run(UUID(args.run_id)),"kill_switch":repo.get_kill_switch(UUID(args.run_id)),"live_mode":False}); return 0
    finally:connection.close()
def kill_main(argv=None):
    p=argparse.ArgumentParser(prog="secure-eval-paper-kill"); p.add_argument("--run-id"); p.add_argument("--persist",action="store_true"); args=p.parse_args(argv)
    if not args.run_id or not args.persist:_print({"status":"not_triggered","reason":"run ID and persistence are required","live_mode":False}); return 0
    connection,repo=_repository(); run_id=UUID(args.run_id)
    try:
        from .enums import KillSwitchReason,KillSwitchState
        from .models import PaperKillSwitch,deterministic_paper_uuid
        now=datetime.now(timezone.utc); existing=repo.get_kill_switch(run_id)
        if existing is None:raise RuntimeError("paper run has no persisted kill switch")
        value=PaperKillSwitch(run_id,KillSwitchState.TRIGGERED,KillSwitchReason.MANUAL,now,now,__import__("secure_eval_wrapper.data_collection.hashing",fromlist=["sha256_payload"]).sha256_payload({"run":run_id,"at":now}),kill_switch_id=UUID(str(existing["kill_switch_id"])))
        repo.persist_kill_event(value,{"source":"manual_cli","cancel_attempts":"recovery_required"}); unresolved=repo.list_unresolved_submissions(run_id); _print({"paper_run_id":str(run_id),"kill_switch_state":"triggered","unresolved_order_count":len(unresolved),"cancellation_status":"durable intent recorded; bounded recovery required","live_mode":False}); return 0
    finally:connection.close()
def reconcile_main(argv=None):
    p=argparse.ArgumentParser(prog="secure-eval-paper-reconcile"); p.add_argument("--run-id"); p.add_argument("--persist",action="store_true"); args=p.parse_args(argv)
    if not args.run_id or not args.persist:_print({"status":"no_reconciliation","postgresql_connected":False,"live_mode":False}); return 0
    connection,repo=_repository()
    try:_print({"paper_run_id":args.run_id,"unresolved_submission_count":len(repo.list_unresolved_submissions(UUID(args.run_id))),"status":"restart_recovery_required","live_mode":False}); return 0
    finally:connection.close()
