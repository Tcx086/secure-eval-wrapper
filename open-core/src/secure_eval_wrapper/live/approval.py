"""Short-lived exact-challenge approval authority for guarded live runs."""
from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal

from secure_eval_wrapper.data_collection.hashing import sha256_payload

from .models import LiveApproval, LivePreflightStatus


class LiveApprovalError(ValueError):
    pass


def manifest_preview_hash(*, live_run_id, configuration, credential_reference_hash: str, preflight_report_id, account_snapshot_hash: str, repository_commit_sha: str) -> str:
    return sha256_payload({"live_run_id": live_run_id, "provider": configuration.provider, "environment": configuration.environment, "account_fingerprint": configuration.account_fingerprint, "configuration_hash": configuration.configuration_hash, "implementation_hash": configuration.provider_implementation_hash, "endpoint_catalog_hash": configuration.endpoint_catalog_hash, "credential_reference_hash": credential_reference_hash, "preflight_report_id": preflight_report_id, "account_snapshot_hash": account_snapshot_hash, "allowed_instruments": configuration.allowed_instruments, "risk_limits": dict(configuration.risk_limits), "dry_run": configuration.dry_run, "production_write_enabled": configuration.production_write_enabled, "maximum_duration": configuration.maximum_run_duration_seconds, "repository_commit_sha": repository_commit_sha})


def confirmation_challenge_hash(*, live_run_id, configuration, account_fingerprint: str, manifest_hash: str, repository_commit_sha: str, nonce: str, approving_actor: str, created_at_utc: datetime, expires_at_utc: datetime, maximum_total_approved_notional: Decimal) -> str:
    return sha256_payload({"live_run_id": live_run_id, "configuration_hash": configuration.configuration_hash, "account_fingerprint": account_fingerprint, "provider": configuration.provider, "environment": configuration.environment, "allowed_instruments": configuration.allowed_instruments, "maximum_total_approved_notional": maximum_total_approved_notional, "created_at_utc": created_at_utc, "expires_at_utc": expires_at_utc, "manifest_hash": manifest_hash, "repository_commit_sha": repository_commit_sha, "nonce": nonce, "approving_actor": approving_actor})


class LiveApprovalController:
    def create(self, *, report, configuration, account_snapshot, manifest_hash: str, repository_commit_sha: str, created_at_utc: datetime, ttl_seconds: int, nonce: str, approving_actor: str, maximum_total_approved_notional: Decimal, exact_confirmation_challenge_hash: str) -> LiveApproval:
        if report.status is not LivePreflightStatus.PASSED:
            raise LiveApprovalError("a blocked preflight cannot be approved")
        if report.configuration_hash != configuration.configuration_hash or report.account_snapshot_hash != account_snapshot.record_hash:
            raise LiveApprovalError("approval inputs do not match the persisted preflight")
        if ttl_seconds <= 0 or ttl_seconds > 900:
            raise LiveApprovalError("approval TTL must be between 1 and 900 seconds")
        if maximum_total_approved_notional <= 0 or maximum_total_approved_notional > configuration.maximum_daily_submitted_notional:
            raise LiveApprovalError("approval notional exceeds the configured daily limit")
        expires = created_at_utc + timedelta(seconds=ttl_seconds)
        expected = confirmation_challenge_hash(live_run_id=report.live_run_id, configuration=configuration, account_fingerprint=account_snapshot.account_fingerprint, manifest_hash=manifest_hash, repository_commit_sha=repository_commit_sha, nonce=nonce, approving_actor=approving_actor, created_at_utc=created_at_utc, expires_at_utc=expires, maximum_total_approved_notional=maximum_total_approved_notional)
        if exact_confirmation_challenge_hash != expected:
            raise LiveApprovalError("exact confirmation challenge mismatch")
        return LiveApproval(report.live_run_id, configuration.configuration_hash, account_snapshot.account_fingerprint, configuration.provider, configuration.environment, configuration.allowed_instruments, maximum_total_approved_notional, created_at_utc, expires, manifest_hash, repository_commit_sha, nonce, approving_actor, expected, report.report_id)

    def validate(self, approval, *, report, configuration, manifest_hash: str, account_snapshot, at_utc: datetime, requested_notional: Decimal = Decimal(0)):
        if at_utc >= approval.expires_at_utc:
            raise LiveApprovalError("approval expired")
        if approval.live_run_id != report.live_run_id or approval.preflight_report_id != report.report_id:
            raise LiveApprovalError("approval run or preflight mismatch")
        if approval.configuration_hash != configuration.configuration_hash or approval.manifest_hash != manifest_hash:
            raise LiveApprovalError("configuration or manifest changed after approval")
        if approval.account_fingerprint != account_snapshot.account_fingerprint:
            raise LiveApprovalError("account changed after approval")
        if requested_notional > approval.maximum_total_approved_notional:
            raise LiveApprovalError("requested notional exceeds approval")
        return approval


__all__ = ["LiveApprovalError", "LiveApprovalController", "manifest_preview_hash", "confirmation_challenge_hash"]
