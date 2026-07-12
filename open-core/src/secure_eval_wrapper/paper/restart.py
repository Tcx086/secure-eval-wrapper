"""Brand-new-process reconstruction of a persisted internal paper runtime."""
from __future__ import annotations
from decimal import Decimal
from types import MappingProxyType
from .approval import ApprovalController
from .broker import PaperBroker
from .configuration import PaperRunConfiguration
from .engine import PaperTradingEngine
from .enums import ApprovalState,KillSwitchReason,KillSwitchState,PaperRunState
from .kill_switch import PaperKillSwitchController
from .models import CredentialReference,PaperApproval,PaperKillSwitch,PaperRun,PaperRunManifest
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

__all__=["reconstruct_internal_paper_runtime"]
