"""PostgreSQL-authoritative live kill-switch state transitions."""
from __future__ import annotations

from secure_eval_wrapper.data_collection.hashing import sha256_payload

from .models import LiveKillState, LiveKillSwitch, LivePreflightStatus

TRIGGER_REASONS = frozenset({"stale_market_data", "stale_account_snapshot", "reconciliation_blocked", "reconciliation_unknown", "unexpected_venue_order", "unexpected_fill", "position_mismatch", "balance_mismatch", "unknown_order_age", "unacknowledged_order_age", "transport_failure_limit", "clock_skew_limit", "daily_loss", "drawdown", "risk_limit_breach", "manifest_mismatch", "credential_permission_mismatch", "endpoint_catalog_violation", "production_write_attempt_in_ci", "manual"})


def arm_kill_switch(*, live_run_id, at_utc) -> LiveKillSwitch:
    return LiveKillSwitch(live_run_id, LiveKillState.ARMED, None, at_utc, sha256_payload({"state": "armed", "run": live_run_id}))


def trigger_kill_switch(current: LiveKillSwitch, *, reason: str, at_utc, evidence: object) -> LiveKillSwitch:
    if reason not in TRIGGER_REASONS:
        raise ValueError("unknown kill-switch reason")
    if current.state not in {LiveKillState.ARMED, LiveKillState.RESET}:
        return current
    return LiveKillSwitch(current.live_run_id, LiveKillState.STOPPED, reason, at_utc, sha256_payload({"reason": reason, "evidence": evidence}), True, True)


def reset_kill_switch(current: LiveKillSwitch, *, fresh_preflight, new_approval, at_utc) -> LiveKillSwitch:
    if current.state is not LiveKillState.STOPPED:
        raise ValueError("only a stopped kill switch can reset")
    if fresh_preflight.status is not LivePreflightStatus.PASSED or new_approval.preflight_report_id != fresh_preflight.report_id or at_utc >= new_approval.expires_at_utc:
        raise PermissionError("reset requires a fresh passed preflight and new valid approval")
    return LiveKillSwitch(current.live_run_id, LiveKillState.RESET, "manual", at_utc, sha256_payload({"preflight": fresh_preflight.record_hash, "approval": new_approval.record_hash}), True, True)


__all__ = ["TRIGGER_REASONS", "arm_kill_switch", "trigger_kill_switch", "reset_kill_switch"]
