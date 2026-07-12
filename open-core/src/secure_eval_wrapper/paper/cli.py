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
    from .durable_repository import DurablePostgresPaperRepository as PostgresPaperRepository
    connection=psycopg.connect(**config.to_connection_kwargs()); return connection,PostgresPaperRepository(connection)
def internal_main(argv=None):
    p=argparse.ArgumentParser(prog="secure-eval-paper-internal"); p.add_argument("--persist",action="store_true"); args=p.parse_args(argv); connection=None
    try:
        repo=None
        if args.persist:connection,repo=_repository()
        _print(run_internal_demo(persist_repository=repo)); return 0
    finally:
        if connection:connection.close()
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

def preflight_main(argv=None):
    """Run only preflight; approval success is reported only after PostgreSQL persistence."""
    p=argparse.ArgumentParser(prog="secure-eval-paper-preflight");p.add_argument("--provider",choices=("internal","okx_demo"),default="internal");p.add_argument("--environment",choices=("paper_internal","paper_exchange_sandbox"),default="paper_internal");p.add_argument("--create-approval",action="store_true");p.add_argument("--persist",action="store_true");args=p.parse_args(argv)
    if args.provider=="okx_demo":
        _print({"provider":"okx_demo","environment":args.environment,"preflight_status":"not_started","reason":"authenticated external preflight is separately gated","approval_created":False,"report_id":None,"approval_id":None,"manifest_eligible":False,"credentials_loaded":False,"network_attempted":False,"live_mode":False});return 2 if args.create_approval else 0
    if args.environment!="paper_internal":raise SystemExit("internal provider requires paper_internal")
    from secure_eval_wrapper.data_collection.hashing import sha256_payload
    from .approval import ApprovalController
    from .configuration import internal_demo_configuration
    from .credentials import CredentialSourceType
    from .enums import PaperProvider
    from .models import CredentialReference,deterministic_paper_uuid
    from .preflight import PaperPreflightEngine,PaperPreflightEvidence
    from .venues.internal import InternalPaperVenue
    now=datetime.now(timezone.utc);configuration=internal_demo_configuration(persistence_required=args.persist);run_id=deterministic_paper_uuid("preflight-run",{"configuration":configuration.config_sha256,"at":now});snapshot=InternalPaperVenue().fetch_account_snapshot(run_id,now);credential=CredentialReference(PaperProvider.INTERNAL,"no-credential-internal",CredentialSourceType.INJECTED_TEST,sha256_payload("no-credential-internal")[:16]);report=PaperPreflightEngine().evaluate(paper_run_id=run_id,configuration=configuration,account_snapshot=snapshot,credential_reference=credential,evidence=PaperPreflightEvidence.verified_internal(now,postgresql_required=args.persist),evaluated_at_utc=now,implementation_sha256=sha256_payload("phase7-preflight-cli"));approval=None;created=False
    if args.create_approval:
        approval=ApprovalController().create(report=report,configuration=configuration,snapshot=snapshot,credential_reference=credential,created_at_utc=now,ttl_seconds=300,actor="local-paper-operator",nonce=str(run_id),maximum_total_notional=configuration.maximum_daily_submitted_notional)
        if args.persist:
            connection,repo=_repository()
            try:created=repo.persist_preflight_approval(configuration=configuration,credential_reference=credential,snapshot=snapshot,report=report,approval=approval)
            finally:connection.close()
    _print({"paper_run_id":str(run_id),"provider":"internal","environment":"paper_internal","preflight_status":report.status.value,"report_id":str(report.report_id),"approval_created":created,"approval_id":str(approval.approval_id) if created else None,"approval_status":"valid" if created else None,"manifest_eligible":bool(created and report.status.value=="passed"),"durability":"postgresql" if created else "not_created","credentials_loaded":False,"network_attempted":False,"live_mode":False});return 0 if not args.create_approval or created else 2

def run_main(argv=None):
    p=argparse.ArgumentParser(prog="secure-eval-paper-run");p.add_argument("--provider",choices=("okx_demo",));p.add_argument("--environment",choices=("paper_exchange_sandbox",));p.add_argument("--manifest");p.add_argument("--approval");p.add_argument("--persist",action="store_true");args=p.parse_args(argv)
    if not all((args.provider,args.environment,args.manifest,args.approval,args.persist)):_print({"status":"not_started","reason":"explicit provider, environment, persisted manifest, approval, and PostgreSQL are required","credentials_loaded":False,"network_attempted":False,"live_mode":False});return 0
    connection,repo=_repository()
    try:
        row=repo._fetchone("SELECT m.manifest_id,m.provider,m.environment,m.approval_id,m.manifest_sha256,a.state approval_state,a.expires_at_utc,r.state run_state FROM execution.paper_run_manifests m JOIN execution.paper_approvals a ON a.approval_id=m.approval_id JOIN execution.paper_runs r ON r.paper_run_id=m.paper_run_id WHERE m.manifest_id=%s AND a.approval_id=%s",(UUID(args.manifest),UUID(args.approval)))
        if row is None:raise RuntimeError("persisted manifest/approval binding was not found")
        if row["provider"]!="okx_demo" or row["environment"]!="paper_exchange_sandbox":raise RuntimeError("persisted authority does not match requested sandbox")
        _print({"status":"unsupported","run_state":row["run_state"],"approval_state":row["approval_state"],"manifest_sha256":row["manifest_sha256"],"reason":"external sandbox order-stream execution is not implemented; no readiness claim is made","credentials_loaded":False,"network_attempted":False,"live_mode":False});return 2
    finally:connection.close()

def reconcile_main(argv=None):
    p=argparse.ArgumentParser(prog="secure-eval-paper-reconcile");p.add_argument("--run-id");p.add_argument("--persist",action="store_true");args=p.parse_args(argv)
    if not args.run_id or not args.persist:_print({"status":"no_reconciliation","postgresql_connected":False,"live_mode":False});return 0
    connection,repo=_repository();run_id=UUID(args.run_id)
    try:
        state=repo.load_state_bundle(run_id)
        if state["run"] is None:raise RuntimeError("persisted paper run not found")
        if state["run"]["provider"]!="internal":_print({"paper_run_id":str(run_id),"status":"unsupported","reason":"external observation requires separately gated authenticated transport","live_mode":False});return 2
        from .restart import reconstruct_internal_paper_runtime
        engine=reconstruct_internal_paper_runtime(repository=repo,paper_run_id=run_id,clock=lambda:datetime.now(timezone.utc));result,differences=engine.reconcile();_print({"paper_run_id":str(run_id),"status":result.status.value,"reconciliation_id":str(result.reconciliation_id),"difference_count":len(differences),"material_difference_count":result.material_difference_count,"kill_switch_state":engine.kill_switch.current.state.value,"unresolved_dispatch_count":len(repo.list_unresolved_dispatches(run_id)),"postgresql_connected":True,"live_mode":False});return 0 if result.status.value in ("reconciled","warning") else 2
    finally:connection.close()
