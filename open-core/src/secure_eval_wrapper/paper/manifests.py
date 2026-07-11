"""Immutable manifest construction and binding validation."""
from .approval import ApprovalController
from .models import PaperRunManifest

def create_manifest(*,configuration,report,approval,snapshot,credential_reference,implementation_sha256,repository_commit_sha,strategy_run_reference,start_at_utc):
    ApprovalController().validate(approval,paper_run_id=report.paper_run_id,report=report,configuration=configuration,snapshot=snapshot,credential_reference=credential_reference,at_utc=start_at_utc)
    return PaperRunManifest(report.paper_run_id,configuration.provider,configuration.environment,configuration.account_reference,implementation_sha256,repository_commit_sha,configuration.config_sha256,report.endpoint_catalog_sha256,report.report_id,approval.approval_id,snapshot.snapshot_id,snapshot.record_sha256,credential_reference,strategy_run_reference,configuration.allowed_instruments,configuration.risk_limits,start_at_utc,configuration.maximum_run_duration_seconds,configuration.persistence_required,{"cancel_open_orders":configuration.cancel_open_orders_on_kill,"automatic_flatten":False},(report.report_id,approval.approval_id,snapshot.snapshot_id))

def validate_manifest(manifest,*,configuration,report,approval,snapshot,credential_reference):
    if manifest.configuration_sha256!=configuration.config_sha256 or manifest.preflight_report_id!=report.report_id or manifest.approval_id!=approval.approval_id:raise ValueError("manifest preflight/approval/configuration binding failed")
    if manifest.initial_account_snapshot_sha256!=snapshot.record_sha256 or manifest.credential_reference.reference_sha256!=credential_reference.reference_sha256:raise ValueError("manifest account/credential binding failed")
    if manifest.environment.value=="live":raise ValueError("live manifest is forbidden")
    return manifest
