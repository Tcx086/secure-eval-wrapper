"""Brand-new-process reconstruction of a persisted internal paper runtime."""
from __future__ import annotations
from decimal import Decimal
from types import MappingProxyType
from .accounting import PaperAccounting
from .approval import ApprovalController
from .broker import PaperBroker
from .configuration import PaperRunConfiguration
from .engine import PaperTradingEngine
from .enums import AccountSnapshotStatus,ApprovalState,KillSwitchReason,KillSwitchState,PaperRunState
from .kill_switch import PaperKillSwitchController
from .manifests import create_manifest
from .models import CredentialReference,PaperAccountSnapshot,PaperApproval,PaperKillSwitch,PaperPreflightCheck,PaperPreflightReport,PaperRun,PaperRunManifest,VenueBalance
from .reconciliation import PaperReconciliationEngine
from .venues.internal import InternalPaperVenue

def _decimal(value):return Decimal(str(value))
def _configuration(row,repository):
    data=repository._map(row["configuration_jsonb"])
    if data.get("legacy_phase7_snapshot"):raise ValueError("legacy 0016 configuration is audit-only and cannot restart operational dispatch")
    decimals=("maximum_order_notional","maximum_position_notional_per_instrument","maximum_gross_exposure","maximum_net_exposure","maximum_daily_submitted_notional","maximum_daily_realized_loss","maximum_current_drawdown")
    for name in decimals:data[name]=_decimal(data[name])
    data["allowed_instruments"]=tuple(data["allowed_instruments"]);data["allowed_instrument_types"]=tuple(data["allowed_instrument_types"]);data["allowed_settlement_assets"]=tuple(data["allowed_settlement_assets"]);data["allowed_order_types"]=tuple(data["allowed_order_types"])
    return PaperRunConfiguration(**data)
def reconstruct_internal_paper_runtime(*,repository,paper_run_id,clock):
    """Create new configuration/accounting/venue/broker/engine objects from PostgreSQL only."""
    state=repository.load_state_bundle(paper_run_id)
    required=("run","configuration","preflight","approval","manifest","kill_switch","risk_state")
    missing=[name for name in required if state.get(name) is None]
    if missing:raise ValueError("persisted paper runtime is incomplete: "+", ".join(missing))
    configuration=_configuration(state["configuration"],repository);a=state["approval"];m=state["manifest"];k=state["kill_switch"];r=state["run"]
    credential_row=repository._fetchone("SELECT * FROM execution.paper_credential_references WHERE credential_reference_sha256=%s",(m["credential_reference_sha256"],))
    if credential_row is None:raise ValueError("manifest credential reference is missing")
    credential=CredentialReference(credential_row["provider"],str(credential_row["alias"]),credential_row["source_type"],str(credential_row["public_key_fingerprint"]),bool(credential_row["loaded"]),credential_row["verified_at_utc"],tuple(repository._map(credential_row["permissions_summary_jsonb"]) if isinstance(credential_row["permissions_summary_jsonb"],dict) else credential_row["permissions_summary_jsonb"]))
    approval=PaperApproval(a["paper_run_id"],a["preflight_report_id"],str(a["configuration_sha256"]),str(a["account_snapshot_sha256"]),str(a["credential_reference_sha256"]),a["provider"],a["environment"],tuple(a["allowed_instruments_jsonb"]),_decimal(a["maximum_approved_total_notional"]),a["created_at_utc"],a["expires_at_utc"],str(a["approving_actor"]),str(a["approval_nonce"]),state=ApprovalState(a["state"]),approval_id=a["approval_id"])
    manifest=PaperRunManifest(m["paper_run_id"],m["provider"],m["environment"],str(m["account_reference"]),str(m["implementation_sha256"]),str(m["repository_commit_sha"]),str(m["configuration_sha256"]),str(m["endpoint_catalog_sha256"]),m["preflight_report_id"],m["approval_id"],m["initial_account_snapshot_id"],str(m["initial_account_snapshot_sha256"]),credential,str(m["strategy_run_reference"]),tuple(m["allowed_instruments_jsonb"]),repository._map(m["risk_limits_jsonb"]),m["start_at_utc"],int(m["expected_maximum_duration_seconds"]),bool(m["persistence_required"]),repository._map(m["kill_switch_configuration_jsonb"]),tuple(m["parent_ids"]),manifest_id=m["manifest_id"])
    if manifest.manifest_sha256!=str(m["manifest_sha256"]):raise ValueError("persisted manifest hash failed reconstruction")
    kill=PaperKillSwitch(k["paper_run_id"],k["state"],None if k["reason"] is None else KillSwitchReason(k["reason"]),k["updated_at_utc"],k["triggered_at_utc"],k["evidence_sha256"],k["incident_id"],k["kill_switch_id"])
    controller=PaperKillSwitchController(kill,persist=lambda value,event:repository.persist_kill_event(value,event));accounting=repository.hydrate_accounting(paper_run_id);venue=InternalPaperVenue(account_reference=configuration.account_reference,initial_balances=accounting.balances);broker=PaperBroker(configuration=configuration,manifest=manifest,approval=approval,venue=venue,accounting=accounting,kill_switch=controller,clock=clock,repository=repository).hydrate_from_postgres();engine=PaperTradingEngine(configuration=configuration,broker=broker,reconciliation_engine=PaperReconciliationEngine(),kill_switch=controller,repository=repository,clock=clock);engine.run=PaperRun(r["paper_run_id"],r["manifest_id"],PaperRunState(r["state"]),r["started_at_utc"],r["updated_at_utc"],r["ended_at_utc"],repository._map(r["summary_jsonb"]));engine._sequence=int(state["risk_state"]["lifecycle_sequence"]);return engine

def load_persisted_preflight_authority(*,repository,paper_run_id):
    """Rebuild a CLI-created internal preflight authority from PostgreSQL only."""
    state=repository.load_state_bundle(paper_run_id);required=("run","configuration","preflight","approval");missing=[name for name in required if state.get(name) is None]
    if missing:raise ValueError("persisted preflight authority is incomplete: "+", ".join(missing))
    if state["manifest"] is not None:raise ValueError("persisted preflight already has a manifest")
    configuration=_configuration(state["configuration"],repository);report_row=state["preflight"];approval_row=state["approval"];snapshot_row=repository._fetchone("SELECT * FROM execution.paper_account_snapshots WHERE paper_run_id=%s ORDER BY fetched_at_utc DESC,snapshot_id DESC LIMIT 1",(paper_run_id,))
    if snapshot_row is None:raise ValueError("persisted preflight snapshot is missing")
    position_rows=repository._fetchall("SELECT * FROM execution.paper_position_snapshots WHERE snapshot_id=%s",(snapshot_row["snapshot_id"],))
    if position_rows:raise ValueError("persisted preflight position identities are not reconstructable from the public snapshot schema")
    balances=tuple(VenueBalance(str(x["currency"]),_decimal(x["total"]),_decimal(x["available"]),_decimal(x["reserved"])) for x in repository._fetchall("SELECT * FROM execution.paper_balance_snapshots WHERE snapshot_id=%s ORDER BY currency",(snapshot_row["snapshot_id"],)))
    open_ids=tuple(str(x["client_order_id"]) for x in repository._fetchall("SELECT client_order_id FROM execution.paper_open_order_snapshots WHERE snapshot_id=%s ORDER BY client_order_id",(snapshot_row["snapshot_id"],)))
    snapshot=PaperAccountSnapshot(paper_run_id,str(snapshot_row["account_reference"]),AccountSnapshotStatus(snapshot_row["status"]),snapshot_row["fetched_at_utc"],snapshot_row["venue_as_of_utc"],str(snapshot_row["account_mode"]),balances,(),open_ids,snapshot_row["venue_sequence"],str(snapshot_row["source"]),snapshot_id=snapshot_row["snapshot_id"])
    check_order=("requested_mode_is_paper","live_mode_false","provider_environment_pair","endpoint_allowlist","external_sandbox_gate","credential_reference","credential_fields","sandbox_credentials","credential_permissions","market_data_available","market_data_fresh","series_identity","currency_identity","account_exists","account_mode","sandbox_status","balances_positions","existing_positions","existing_orders","venue_clock_skew","account_snapshot_fresh","limits_complete","spot_short_boundary","derivative_boundary","postgresql","migration_catalog","audit_writable","monitoring","kill_switch","reconciliation_repository");check_rows=repository._fetchall("SELECT * FROM execution.paper_preflight_checks WHERE report_id=%s",(report_row["report_id"],));rank={name:i for i,name in enumerate(check_order)};check_rows=sorted(check_rows,key=lambda x:(rank.get(str(x["check_name"]),len(rank)),str(x["check_id"])))
    checks=tuple(PaperPreflightCheck(str(x["check_name"]),x["status"],x["checked_at_utc"],str(x["reason_code"]),str(x["explanation"]),bool(x["required"]),str(x["evidence_sha256"]),check_id=x["check_id"]) for x in check_rows)
    report=PaperPreflightReport(paper_run_id,report_row["evaluated_at_utc"],report_row["status"],checks,tuple(report_row["blockers_jsonb"]),tuple(report_row["warnings_jsonb"]),str(report_row["configuration_sha256"]),str(report_row["account_snapshot_sha256"]),str(report_row["implementation_sha256"]),str(report_row["endpoint_catalog_sha256"]),str(report_row["credential_reference_sha256"]),report_id=report_row["report_id"])
    credential_row=repository._fetchone("SELECT * FROM execution.paper_credential_references WHERE credential_reference_sha256=%s",(report.credential_reference_sha256,));permissions=credential_row["permissions_summary_jsonb"];credential=CredentialReference(credential_row["provider"],str(credential_row["alias"]),credential_row["source_type"],str(credential_row["public_key_fingerprint"]),bool(credential_row["loaded"]),credential_row["verified_at_utc"],tuple(repository._map(permissions) if isinstance(permissions,dict) else permissions))
    approval=PaperApproval(approval_row["paper_run_id"],approval_row["preflight_report_id"],str(approval_row["configuration_sha256"]),str(approval_row["account_snapshot_sha256"]),str(approval_row["credential_reference_sha256"]),approval_row["provider"],approval_row["environment"],tuple(approval_row["allowed_instruments_jsonb"]),_decimal(approval_row["maximum_approved_total_notional"]),approval_row["created_at_utc"],approval_row["expires_at_utc"],str(approval_row["approving_actor"]),str(approval_row["approval_nonce"]),state=ApprovalState(approval_row["state"]),approval_id=approval_row["approval_id"])
    return configuration,snapshot,report,approval,credential

def start_persisted_internal_preflight(*,repository,paper_run_id,clock,repository_commit_sha="local",strategy_run_reference="persisted-preflight"):
    configuration,snapshot,report,approval,credential=load_persisted_preflight_authority(repository=repository,paper_run_id=paper_run_id);now=clock();manifest=create_manifest(configuration=configuration,report=report,approval=approval,snapshot=snapshot,credential_reference=credential,implementation_sha256=report.implementation_sha256,repository_commit_sha=repository_commit_sha,strategy_run_reference=strategy_run_reference,start_at_utc=now);kill=PaperKillSwitch(paper_run_id,KillSwitchState.ARMED,None,now);controller=PaperKillSwitchController(kill,persist=lambda value,event:repository.persist_kill_event(value,event));accounting=PaperAccounting(paper_run_id=paper_run_id,account_reference=configuration.account_reference,balances={b.currency:b.total for b in snapshot.balances});venue=InternalPaperVenue(account_reference=configuration.account_reference,initial_balances=accounting.balances);broker=PaperBroker(configuration=configuration,manifest=manifest,approval=approval,venue=venue,accounting=accounting,kill_switch=controller,clock=clock,repository=repository);engine=PaperTradingEngine(configuration=configuration,broker=broker,reconciliation_engine=PaperReconciliationEngine(),kill_switch=controller,repository=repository,clock=clock);engine.start(report=report,approval=approval,snapshot=snapshot,credential_reference=credential,approval_controller=ApprovalController());return engine
__all__=["load_persisted_preflight_authority","reconstruct_internal_paper_runtime","start_persisted_internal_preflight"]
