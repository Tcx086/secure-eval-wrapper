"""Allowlisted public assurance evidence for the Phase 8B shadow runtime."""
from __future__ import annotations

import re
from collections import Counter
from typing import Mapping

from secure_eval_wrapper.data_collection.hashing import sha256_payload

from .identity import RuntimeRepositoryIdentity
from .shadow_models import SHADOW_RUNTIME_VERSION
from .shadow_repository import MIGRATION_0026_SHA256, MemoryShadowRepository
from .shadow_runtime import FixtureShadowMarketSource, ShadowAssuranceRuntime
from .shadow_scenarios import account_scenarios, all_shadow_scenarios, market_failure_scenarios


PUBLIC_EVIDENCE_KEYS = (
    "schema_version",
    "operation",
    "status",
    "repository_sha",
    "shadow_runtime_version",
    "fixture_scenario_count",
    "mock_account_scenario_count",
    "public_market_failure_scenario_count",
    "restart_scenarios_passed",
    "replay_scenarios_passed",
    "concurrency_scenarios_passed",
    "crash_recovery_scenarios_passed",
    "accepted_shadow_decision_count",
    "blocked_shadow_decision_count",
    "blocker_frequencies",
    "stale_data_rejection_count",
    "malformed_data_rejection_count",
    "synthetic_exposure_rejection_count",
    "production_transport_call_count",
    "authenticated_endpoint_call_count",
    "credential_read_count",
    "production_write_count",
    "production_submit_reachable",
    "production_cancel_reachable",
    "real_account_data_used",
    "operator_database_accessed",
    "authenticated_proof_executed",
    "public_network_smoke_status",
    "migration_count",
    "latest_migration",
    "migration_0026_sha256",
    "migration_0027_exists",
    "evidence_payload_sha256",
    "independent_audit_status",
)

_FORBIDDEN_KEY = re.compile(
    r"(?:api_?key|secret|passphrase|actual_?(?:balance|position)|pending_?orders?|"
    r"account_?fingerprint|real_?uid|postgres(?:ql)?_?password|dsn|"
    r"environment_?dump|raw_?provider_?response|private_?strategy)",
    re.I,
)
_SECRET_VALUE = re.compile(
    r"(?:authorization\s*[:=]|access[_-]?(?:key|sign|passphrase)\s*[:=])",
    re.I,
)
_RAW_PATH = re.compile(r"(?:[A-Za-z]:[\\/]|/(?:home|Users|var/private)/)")
_HIGH_ENTROPY = re.compile(r"^[A-Za-z0-9+/=_-]{40,}$")


def scenario_metrics(repository_sha: str) -> Mapping[str, object]:
    identity = lambda: RuntimeRepositoryIdentity(repository_sha, "git_checkout")
    accepted = 0
    blockers: Counter[str] = Counter()
    for scenario in all_shadow_scenarios():
        runtime = ShadowAssuranceRuntime(
            repository=MemoryShadowRepository(),
            market_source=FixtureShadowMarketSource(),
            identity_resolver=identity,
        )
        summary = runtime.run_scenario(scenario)
        accepted += int(summary.accepted)
        blockers.update(summary.blockers)
        if (
            ("accepted" if summary.accepted else "blocked") != scenario.expected_result
            or tuple(summary.blockers) != scenario.expected_blockers
            or summary.shadow_intent_count != scenario.expected_shadow_intent_count
        ):
            raise AssertionError(f"scenario catalog drift: {scenario.scenario_id}")
    return {
        "scenario_count": len(all_shadow_scenarios()),
        "accepted_count": accepted,
        "blocked_count": len(all_shadow_scenarios()) - accepted,
        "blocker_frequencies": dict(sorted(blockers.items())),
    }


def build_public_shadow_evidence(
    *,
    repository_sha: str,
    public_network_smoke_status: str = "PUBLIC_NETWORK_SMOKE_NOT_EXECUTED",
    restart_scenarios_passed: int = 3,
    replay_scenarios_passed: int = 6,
    concurrency_scenarios_passed: int = 7,
    crash_recovery_scenarios_passed: int = 9,
) -> dict[str, object]:
    metrics = scenario_metrics(repository_sha)
    core: dict[str, object] = {
        "schema_version": 1,
        "operation": "phase8b_shadow_assurance",
        "status": "implemented_pending_independent_audit",
        "repository_sha": repository_sha,
        "shadow_runtime_version": SHADOW_RUNTIME_VERSION,
        "fixture_scenario_count": metrics["scenario_count"],
        "mock_account_scenario_count": len(account_scenarios()),
        "public_market_failure_scenario_count": len(market_failure_scenarios()),
        "restart_scenarios_passed": restart_scenarios_passed,
        "replay_scenarios_passed": replay_scenarios_passed,
        "concurrency_scenarios_passed": concurrency_scenarios_passed,
        "crash_recovery_scenarios_passed": crash_recovery_scenarios_passed,
        "accepted_shadow_decision_count": metrics["accepted_count"],
        "blocked_shadow_decision_count": metrics["blocked_count"],
        "blocker_frequencies": metrics["blocker_frequencies"],
        "stale_data_rejection_count": sum(
            metrics["blocker_frequencies"].get(key, 0)
            for key in ("stale_market_data", "stale_cached_response")
        ),
        "malformed_data_rejection_count": sum(
            metrics["blocker_frequencies"].get(key, 0)
            for key in (
                "malformed_account_snapshot",
                "malformed_public_response",
                "quantity_not_finite",
                "market_price_not_finite",
            )
        ),
        "synthetic_exposure_rejection_count": sum(
            metrics["blocker_frequencies"].get(key, 0)
            for key in ("synthetic_derivative_exposure", "synthetic_short_position")
        ),
        "production_transport_call_count": 0,
        "authenticated_endpoint_call_count": 0,
        "credential_read_count": 0,
        "production_write_count": 0,
        "production_submit_reachable": False,
        "production_cancel_reachable": False,
        "real_account_data_used": False,
        "operator_database_accessed": False,
        "authenticated_proof_executed": False,
        "public_network_smoke_status": public_network_smoke_status,
        "migration_count": 26,
        "latest_migration": "0026",
        "migration_0026_sha256": MIGRATION_0026_SHA256,
        "migration_0027_exists": False,
        "independent_audit_status": "pending",
    }
    values = dict(core)
    values["evidence_payload_sha256"] = sha256_payload(core)
    payload = {
        key: values[key]
        for key in PUBLIC_EVIDENCE_KEYS
    }
    validate_public_shadow_evidence(payload)
    return payload


def _scan(value, *, path: tuple[str, ...] = ()) -> None:
    if isinstance(value, Mapping):
        for key, item in value.items():
            key_text = str(key)
            if _FORBIDDEN_KEY.search(key_text):
                raise ValueError(f"forbidden public evidence key: {'.'.join(path + (key_text,))}")
            _scan(item, path=path + (key_text,))
        return
    if isinstance(value, (list, tuple)):
        for index, item in enumerate(value):
            _scan(item, path=path + (str(index),))
        return
    if not isinstance(value, str):
        return
    if _SECRET_VALUE.search(value):
        raise ValueError("secret-shaped value in public shadow evidence")
    if _RAW_PATH.search(value):
        raise ValueError("raw local path in public shadow evidence")
    key = path[-1] if path else ""
    hash_field = key.endswith(("_sha", "_sha256", "_hash")) or key == "repository_sha"
    if not hash_field and _HIGH_ENTROPY.fullmatch(value):
        raise ValueError("unclassified high-entropy value in public shadow evidence")


def validate_public_shadow_evidence(payload: Mapping[str, object]) -> None:
    if tuple(payload.keys()) != PUBLIC_EVIDENCE_KEYS:
        raise ValueError("public shadow evidence keys/order differ from the fixed allowlist")
    core = {key: payload[key] for key in PUBLIC_EVIDENCE_KEYS if key != "evidence_payload_sha256"}
    if payload["evidence_payload_sha256"] != sha256_payload(core):
        raise ValueError("public shadow evidence payload hash mismatch")
    if (
        payload["operation"] != "phase8b_shadow_assurance"
        or payload["status"] != "implemented_pending_independent_audit"
        or payload["independent_audit_status"] != "pending"
    ):
        raise ValueError("public shadow evidence authority status is invalid")
    if any(payload[key] != 0 for key in (
        "production_transport_call_count",
        "authenticated_endpoint_call_count",
        "credential_read_count",
        "production_write_count",
    )):
        raise PermissionError("public shadow evidence reports a forbidden call/write")
    if any(payload[key] is not False for key in (
        "production_submit_reachable",
        "production_cancel_reachable",
        "real_account_data_used",
        "operator_database_accessed",
        "authenticated_proof_executed",
        "migration_0027_exists",
    )):
        raise PermissionError("public shadow evidence reports forbidden authority/data")
    if (
        payload["migration_count"] != 26
        or payload["latest_migration"] != "0026"
        or payload["migration_0026_sha256"] != MIGRATION_0026_SHA256
    ):
        raise ValueError("public shadow evidence migration boundary mismatch")
    if (
        payload["fixture_scenario_count"]
        != payload["accepted_shadow_decision_count"] + payload["blocked_shadow_decision_count"]
    ):
        raise ValueError("public shadow evidence scenario counts do not reconcile")
    _scan(payload)


__all__ = [
    "PUBLIC_EVIDENCE_KEYS",
    "build_public_shadow_evidence",
    "scenario_metrics",
    "validate_public_shadow_evidence",
]
