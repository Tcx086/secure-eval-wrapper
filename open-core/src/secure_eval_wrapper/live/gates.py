"""Independent live-write gates with an irreversible Phase 8A prohibition."""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Mapping


_CI_KEYS = ("CI", "GITHUB_ACTIONS", "GITLAB_CI", "TF_BUILD", "JENKINS_URL", "BUILDKITE", "CIRCLECI")
_TRUE = frozenset({"1", "true", "yes", "on"})


def common_ci_indicators(environment: Mapping[str, str] | None = None) -> tuple[str, ...]:
    values = os.environ if environment is None else environment
    return tuple(key for key in _CI_KEYS if str(values.get(key, "")).strip().lower() in _TRUE or (key == "JENKINS_URL" and bool(values.get(key))))


@dataclass(frozen=True)
class LiveWriteAuthority:
    environment_gate: bool
    cli_gate: bool
    approval_gate: bool
    ci_prohibited: bool
    phase8a_prohibited: bool
    allowed: bool
    blockers: tuple[str, ...]


def evaluate_live_write_authority(*, configuration, cli_enable_live_execution: bool, approval, exact_confirmation_challenge_hash: str | None, at_utc, environment: Mapping[str, str] | None = None) -> LiveWriteAuthority:
    values = os.environ if environment is None else environment
    environment_gate = str(values.get("SECURE_EVAL_ENABLE_LIVE_EXECUTION", "")).strip().lower() == "true"
    cli_gate = cli_enable_live_execution is True
    approval_gate = bool(
        approval is not None
        and exact_confirmation_challenge_hash
        and exact_confirmation_challenge_hash == approval.confirmation_challenge_hash
        and at_utc < approval.expires_at_utc
        and approval.configuration_hash == configuration.configuration_hash
        and approval.account_fingerprint == configuration.account_fingerprint
        and approval.provider == configuration.provider
        and approval.environment == configuration.environment
        and tuple(approval.allowed_instruments) == tuple(configuration.allowed_instruments)
    )
    ci = bool(common_ci_indicators(values))
    blockers = []
    if not environment_gate: blockers.append("missing_process_environment_gate")
    if not cli_gate: blockers.append("missing_cli_gate")
    if not approval_gate: blockers.append("missing_or_invalid_exact_approval_challenge")
    if ci: blockers.append("ci_hard_prohibition")
    if not configuration.production_write_enabled: blockers.append("configuration_production_write_disabled")
    blockers.append("phase8a_production_writes_disabled")
    return LiveWriteAuthority(environment_gate, cli_gate, approval_gate, ci, True, False, tuple(blockers))


def require_fake_transport_in_ci(transport) -> None:
    if common_ci_indicators() and not getattr(transport, "is_fake", False):
        raise PermissionError("CI requires fake live transport")


__all__ = ["LiveWriteAuthority", "common_ci_indicators", "evaluate_live_write_authority", "require_fake_transport_in_ci"]
