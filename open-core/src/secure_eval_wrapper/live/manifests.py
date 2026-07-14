"""Immutable live manifest construction and binding checks."""
from __future__ import annotations

from .approval import LiveApprovalController, manifest_preview_hash
from .models import LiveRunManifest


def create_live_manifest(*, configuration, report, approval, account_snapshot, credential_reference, at_utc):
    preview = manifest_preview_hash(live_run_id=report.live_run_id, configuration=configuration, credential_reference_hash=credential_reference.record_hash, preflight_report_id=report.report_id, account_snapshot_hash=account_snapshot.record_hash, repository_commit_sha=report.repository_commit_sha)
    LiveApprovalController().validate(approval, report=report, configuration=configuration, manifest_hash=preview, account_snapshot=account_snapshot, at_utc=at_utc)
    return LiveRunManifest(report.live_run_id, configuration.provider, configuration.environment, configuration.account_fingerprint, configuration.configuration_hash, configuration.provider_implementation_hash, report.repository_commit_sha, configuration.endpoint_catalog_hash, credential_reference.record_hash, report.report_id, approval.approval_id, account_snapshot.snapshot_id, account_snapshot.record_hash, configuration.allowed_instruments, configuration.risk_limits, configuration.dry_run, configuration.production_write_enabled, configuration.maximum_run_duration_seconds, {"cancel_open_orders_on_kill": configuration.cancel_open_orders_on_kill, "automatic_flatten": False}, (report.report_id, approval.approval_id, account_snapshot.snapshot_id, credential_reference.reference_id), preview)


def validate_live_manifest(manifest, *, configuration, report, approval, account_snapshot, credential_reference):
    expected = manifest_preview_hash(live_run_id=report.live_run_id, configuration=configuration, credential_reference_hash=credential_reference.record_hash, preflight_report_id=report.report_id, account_snapshot_hash=account_snapshot.record_hash, repository_commit_sha=report.repository_commit_sha)
    if manifest.manifest_hash != expected or manifest.configuration_hash != configuration.configuration_hash or manifest.repository_commit_sha != report.repository_commit_sha:
        raise ValueError("live manifest economic or configuration binding mismatch")
    if manifest.preflight_report_id != report.report_id or manifest.approval_id != approval.approval_id:
        raise ValueError("live manifest preflight or approval binding mismatch")
    if manifest.initial_account_snapshot_hash != account_snapshot.record_hash or manifest.credential_reference_hash != credential_reference.record_hash:
        raise ValueError("live manifest account or credential binding mismatch")
    if not manifest.dry_run or manifest.production_write_enabled:
        raise ValueError("Phase 8A manifest cannot enable writes")
    return manifest


__all__ = ["create_live_manifest", "validate_live_manifest"]
