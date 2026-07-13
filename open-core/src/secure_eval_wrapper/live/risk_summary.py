"""Public-safe pre-run and post-run live risk summaries."""
from __future__ import annotations

from .credentials import redact
from .models import LiveRiskSummary


def build_pre_run_summary(*, manifest, approval, account_snapshot, proposed_decisions, reconciliation, kill_switch, market_evidence_age_seconds, generated_at_utc):
    risk_notional = sum((row.risk_notional for row in proposed_decisions), start=0)
    fee_slippage = sum((row.reservation_notional - row.risk_notional for row in proposed_decisions), start=0)
    payload = {"mode": "DRY-RUN", "production_write_enabled": False, "provider": manifest.provider, "environment": manifest.environment, "account_fingerprint": manifest.account_fingerprint, "configuration_hash": manifest.configuration_hash, "manifest_hash": manifest.manifest_hash, "commit_sha": manifest.repository_commit_sha, "approval_expiry": approval.expires_at_utc, "allowed_instruments": manifest.allowed_instruments, "risk_limits": dict(manifest.risk_limits), "account_totals": {"total": account_snapshot.total_equity, "available": account_snapshot.available_equity, "reserved": account_snapshot.reserved_equity}, "existing_exposure": account_snapshot.positions, "existing_order_count": account_snapshot.open_order_count, "proposed_order_count": len(proposed_decisions), "proposed_risk_notional": risk_notional, "maximum_possible_fees_slippage": fee_slippage, "market_evidence_age_seconds": market_evidence_age_seconds, "account_evidence_age_seconds": (generated_at_utc - account_snapshot.fetched_at_utc).total_seconds(), "reconciliation_status": reconciliation.status.value, "kill_switch_status": kill_switch.state.value, "blockers": tuple(reason for row in proposed_decisions for reason in row.reasons), "warnings": ()}
    return LiveRiskSummary(manifest.live_run_id, "pre_run", generated_at_utc, redact(payload), (manifest.manifest_id, approval.approval_id, account_snapshot.snapshot_id, reconciliation.reconciliation_id, kill_switch.kill_switch_id))


def build_post_run_summary(*, manifest, generated_at_utc, suppressed: bool, transport_attempts: int, order_observations: int, fills: tuple, fees, ending_balances, ending_positions, realized_pnl, maximum_exposure, reconciliation, kill_switch, unresolved_recovery_items: tuple, evidence_ids: tuple):
    payload = {"mode": "DRY-RUN", "external_write_attempted": False, "external_write_suppressed": suppressed, "transport_attempts": transport_attempts, "order_observations": order_observations, "fills": fills, "fees": fees, "ending_balances": ending_balances, "ending_positions": ending_positions, "realized_pnl": realized_pnl, "maximum_exposure": maximum_exposure, "reconciliation_outcome": reconciliation.status.value, "kill_switch_outcome": kill_switch.state.value, "unresolved_recovery_items": unresolved_recovery_items, "evidence_ids": tuple(str(value) for value in evidence_ids)}
    return LiveRiskSummary(manifest.live_run_id, "post_run", generated_at_utc, redact(payload), evidence_ids)


__all__ = ["build_pre_run_summary", "build_post_run_summary"]
