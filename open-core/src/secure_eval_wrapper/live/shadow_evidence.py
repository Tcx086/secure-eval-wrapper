"""Verifier-derived, allowlisted public evidence for Phase 8B shadow assurance."""
from __future__ import annotations

import re
from enum import Enum
from typing import Mapping

from secure_eval_wrapper.data_collection.hashing import sha256_payload

from .migration_catalog import CANONICAL_MIGRATION_CATALOG, LATEST_MIGRATION, MIGRATION_0026_SHA256
from .shadow_models import SHADOW_RUNTIME_VERSION
from .shadow_scenarios import account_scenarios, all_shadow_scenarios, market_failure_scenarios
from .shadow_verifier import (
    POSTGRESQL_VERIFIER_NOT_EXECUTED,
    SHADOW_VERIFIER_VERSION,
    passed_case_count,
    run_offline_assurance_verifier,
    validate_offline_assurance_verifier_result,
)


class PublicNetworkSmokeStatus(str, Enum):
    NOT_EXECUTED = "PUBLIC_NETWORK_SMOKE_NOT_EXECUTED"
    BLOCKED_TIMEOUT = "PUBLIC_NETWORK_SMOKE_BLOCKED_TIMEOUT"
    BLOCKED_RATE_LIMIT = "PUBLIC_NETWORK_SMOKE_BLOCKED_RATE_LIMIT"
    BLOCKED_CONNECTION = "PUBLIC_NETWORK_SMOKE_BLOCKED_CONNECTION"
    SUCCESS = "PUBLIC_NETWORK_SMOKE_SUCCESS"


PUBLIC_EVIDENCE_KEYS = (
    "schema_version",
    "operation",
    "status",
    "repository_sha",
    "shadow_runtime_version",
    "verifier_version",
    "assurance_verifier_result",
    "verifier_result_sha256",
    "scenario_catalog_sha256",
    "runtime_implementation_sha256",
    "postgresql_verification_classification",
    "fixture_scenario_count",
    "mock_account_scenario_count",
    "public_market_failure_scenario_count",
    "restart_scenarios_passed",
    "replay_scenarios_passed",
    "concurrency_scenarios_passed",
    "crash_recovery_scenarios_passed",
    "postgresql_restart_scenarios_passed",
    "postgresql_replay_scenarios_passed",
    "postgresql_concurrency_scenarios_passed",
    "postgresql_crash_recovery_scenarios_passed",
    "accepted_shadow_decision_count",
    "blocked_shadow_decision_count",
    "blocker_frequencies",
    "stale_data_rejection_count",
    "malformed_data_rejection_count",
    "synthetic_exposure_rejection_count",
    "network_write_count",
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
    "public_network_smoke_read_count",
    "public_network_smoke_source_hashes",
    "public_network_smoke_provenance_hash",
    "public_network_smoke_result_hash",
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
    result = run_offline_assurance_verifier(repository_sha)
    return {
        "scenario_count": len(result["scenario_results"]),
        "accepted_count": result["accepted_shadow_decision_count"],
        "blocked_count": result["blocked_shadow_decision_count"],
        "blocker_frequencies": result["blocker_frequencies"],
    }


def build_public_shadow_evidence(
    *,
    repository_sha: str,
) -> dict[str, object]:
    verifier = run_offline_assurance_verifier(repository_sha)
    blockers = verifier["blocker_frequencies"]
    zero = verifier["zero_write_facts"]
    postgres = verifier["postgresql_verification"]
    core: dict[str, object] = {
        "schema_version": 2,
        "operation": "phase8b_shadow_assurance",
        "status": "implemented_pending_independent_audit",
        "repository_sha": repository_sha,
        "shadow_runtime_version": SHADOW_RUNTIME_VERSION,
        "verifier_version": SHADOW_VERIFIER_VERSION,
        "assurance_verifier_result": verifier,
        "verifier_result_sha256": verifier["verifier_result_sha256"],
        "scenario_catalog_sha256": verifier["scenario_catalog_hash"],
        "runtime_implementation_sha256": verifier["runtime_implementation_hash"],
        "postgresql_verification_classification": postgres["classification"],
        "fixture_scenario_count": len(verifier["scenario_results"]),
        "mock_account_scenario_count": len(account_scenarios()),
        "public_market_failure_scenario_count": len(market_failure_scenarios()),
        "restart_scenarios_passed": passed_case_count(verifier, "restart_results"),
        "replay_scenarios_passed": passed_case_count(verifier, "replay_results"),
        "concurrency_scenarios_passed": passed_case_count(verifier, "concurrency_results"),
        "crash_recovery_scenarios_passed": passed_case_count(verifier, "crash_results"),
        "postgresql_restart_scenarios_passed": passed_case_count(postgres, "restart_results"),
        "postgresql_replay_scenarios_passed": passed_case_count(postgres, "replay_results"),
        "postgresql_concurrency_scenarios_passed": passed_case_count(postgres, "concurrency_results"),
        "postgresql_crash_recovery_scenarios_passed": passed_case_count(postgres, "crash_results"),
        "accepted_shadow_decision_count": verifier["accepted_shadow_decision_count"],
        "blocked_shadow_decision_count": verifier["blocked_shadow_decision_count"],
        "blocker_frequencies": blockers,
        "stale_data_rejection_count": sum(
            blockers.get(key, 0) for key in ("stale_market_data", "stale_cached_response")
        ),
        "malformed_data_rejection_count": sum(
            blockers.get(key, 0) for key in (
                "malformed_account_snapshot", "malformed_public_response",
                "quantity_not_finite", "market_price_not_finite",
            )
        ),
        "synthetic_exposure_rejection_count": sum(
            blockers.get(key, 0) for key in (
                "synthetic_derivative_exposure", "synthetic_short_position",
            )
        ),
        "network_write_count": zero["network_write_count"],
        "production_transport_call_count": zero["production_transport_call_count"],
        "authenticated_endpoint_call_count": zero["authenticated_endpoint_call_count"],
        "credential_read_count": zero["credential_read_count"],
        "production_write_count": zero["production_write_count"],
        "production_submit_reachable": False,
        "production_cancel_reachable": False,
        "real_account_data_used": False,
        "operator_database_accessed": False,
        "authenticated_proof_executed": False,
        "public_network_smoke_status": PublicNetworkSmokeStatus.NOT_EXECUTED.value,
        "public_network_smoke_read_count": 0,
        "public_network_smoke_source_hashes": (),
        "public_network_smoke_provenance_hash": None,
        "public_network_smoke_result_hash": None,
        "migration_count": len(CANONICAL_MIGRATION_CATALOG),
        "latest_migration": LATEST_MIGRATION[:4],
        "migration_0026_sha256": MIGRATION_0026_SHA256,
        "migration_0027_exists": False,
        "independent_audit_status": "pending",
    }
    values = dict(core)
    values["evidence_payload_sha256"] = sha256_payload(core)
    payload = {key: values[key] for key in PUBLIC_EVIDENCE_KEYS}
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
    hash_field = (
        key.endswith(("_sha", "_sha256", "_hash", "_classification"))
        or "relevant_hashes" in path
        or key in {
            "repository_sha", "result_hash", "evidence_hash",
            "case_id", "verifier_version", "classification",
        }
    )
    if not hash_field and _HIGH_ENTROPY.fullmatch(value):
        raise ValueError("unclassified high-entropy value in public shadow evidence")


def validate_public_shadow_evidence(payload: Mapping[str, object]) -> None:
    if tuple(payload.keys()) != PUBLIC_EVIDENCE_KEYS:
        raise ValueError("public shadow evidence keys/order differ from the fixed allowlist")
    core = {key: payload[key] for key in PUBLIC_EVIDENCE_KEYS if key != "evidence_payload_sha256"}
    if payload["evidence_payload_sha256"] != sha256_payload(core):
        raise ValueError("public shadow evidence payload hash mismatch")
    if (
        payload["schema_version"] != 2
        or payload["shadow_runtime_version"] != SHADOW_RUNTIME_VERSION
        or payload["operation"] != "phase8b_shadow_assurance"
        or payload["status"] != "implemented_pending_independent_audit"
        or payload["independent_audit_status"] != "pending"
    ):
        raise ValueError("public shadow evidence authority status is invalid")
    verifier = payload["assurance_verifier_result"]
    if not isinstance(verifier, Mapping):
        raise ValueError("public shadow evidence lacks a machine-readable verifier result")
    validate_offline_assurance_verifier_result(verifier, repository_sha=str(payload["repository_sha"]))
    postgres = verifier["postgresql_verification"]
    blockers = verifier["blocker_frequencies"]
    derived = {
        "verifier_version": verifier["verifier_version"],
        "verifier_result_sha256": verifier["verifier_result_sha256"],
        "scenario_catalog_sha256": verifier["scenario_catalog_hash"],
        "runtime_implementation_sha256": verifier["runtime_implementation_hash"],
        "postgresql_verification_classification": postgres["classification"],
        "fixture_scenario_count": len(verifier["scenario_results"]),
        "mock_account_scenario_count": len(account_scenarios()),
        "public_market_failure_scenario_count": len(market_failure_scenarios()),
        "restart_scenarios_passed": passed_case_count(verifier, "restart_results"),
        "replay_scenarios_passed": passed_case_count(verifier, "replay_results"),
        "concurrency_scenarios_passed": passed_case_count(verifier, "concurrency_results"),
        "crash_recovery_scenarios_passed": passed_case_count(verifier, "crash_results"),
        "postgresql_restart_scenarios_passed": passed_case_count(postgres, "restart_results"),
        "postgresql_replay_scenarios_passed": passed_case_count(postgres, "replay_results"),
        "postgresql_concurrency_scenarios_passed": passed_case_count(postgres, "concurrency_results"),
        "postgresql_crash_recovery_scenarios_passed": passed_case_count(postgres, "crash_results"),
        "accepted_shadow_decision_count": verifier["accepted_shadow_decision_count"],
        "blocked_shadow_decision_count": verifier["blocked_shadow_decision_count"],
        "blocker_frequencies": blockers,
        "stale_data_rejection_count": sum(
            blockers.get(key, 0) for key in ("stale_market_data", "stale_cached_response")
        ),
        "malformed_data_rejection_count": sum(
            blockers.get(key, 0) for key in (
                "malformed_account_snapshot", "malformed_public_response",
                "quantity_not_finite", "market_price_not_finite",
            )
        ),
        "synthetic_exposure_rejection_count": sum(
            blockers.get(key, 0)
            for key in ("synthetic_derivative_exposure", "synthetic_short_position")
        ),
    }
    if any(payload[key] != value for key, value in derived.items()):
        raise ValueError("public evidence claims are not derived from the verifier result")
    if payload["postgresql_verification_classification"] == POSTGRESQL_VERIFIER_NOT_EXECUTED and any(
        payload[key] != 0 for key in (
            "postgresql_restart_scenarios_passed", "postgresql_replay_scenarios_passed",
            "postgresql_concurrency_scenarios_passed", "postgresql_crash_recovery_scenarios_passed",
        )
    ):
        raise ValueError("unexecuted PostgreSQL verifier cannot report passed cases")
    if any(payload[key] != 0 for key in (
        "network_write_count", "production_transport_call_count",
        "authenticated_endpoint_call_count", "credential_read_count", "production_write_count",
    )):
        raise PermissionError("public shadow evidence reports a forbidden call/write")
    if any(payload[key] is not False for key in (
        "production_submit_reachable", "production_cancel_reachable", "real_account_data_used",
        "operator_database_accessed", "authenticated_proof_executed", "migration_0027_exists",
    )):
        raise PermissionError("public shadow evidence reports forbidden authority/data")
    smoke = PublicNetworkSmokeStatus(payload["public_network_smoke_status"])
    smoke_hashes = payload["public_network_smoke_source_hashes"]
    if smoke is not PublicNetworkSmokeStatus.NOT_EXECUTED:
        raise ValueError("checked public smoke evidence was not verifier-executed")
    else:
        if any((payload["public_network_smoke_read_count"], smoke_hashes,
                payload["public_network_smoke_provenance_hash"], payload["public_network_smoke_result_hash"])):
            raise ValueError("NOT_EXECUTED public smoke cannot carry run facts")
    if (
        payload["migration_count"] != len(CANONICAL_MIGRATION_CATALOG)
        or payload["latest_migration"] != "0026"
        or payload["migration_0026_sha256"] != MIGRATION_0026_SHA256
    ):
        raise ValueError("public shadow evidence migration boundary mismatch")
    if payload["fixture_scenario_count"] != len(all_shadow_scenarios()):
        raise ValueError("public shadow evidence scenario count is stale")
    _scan(payload)


__all__ = [
    "PUBLIC_EVIDENCE_KEYS",
    "PublicNetworkSmokeStatus",
    "build_public_shadow_evidence",
    "scenario_metrics",
    "validate_public_shadow_evidence",
]
