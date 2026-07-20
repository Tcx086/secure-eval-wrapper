"""Canonical validation for durable Phase 8B shadow assurance bundles."""
from __future__ import annotations

import json
from decimal import Decimal
from collections.abc import Mapping, Sequence
from typing import Any
from uuid import UUID

from secure_eval_wrapper.data_collection.hashing import sha256_payload

from .shadow_models import (
    SHADOW_RUNTIME_VERSION,
    ShadowDataProvenance,
    ShadowOrderIntent,
    ShadowSafetyFacts,
)


class ShadowBundleValidationError(ValueError):
    """A stored bundle or manifest row does not match canonical authority."""


_TOP_LEVEL_FIELDS = frozenset({
    "schema_version",
    "operation",
    "status",
    "runtime_version",
    "decision",
    "summary",
    "bundle_hash",
})
_DECISION_FIELDS = frozenset({
    "shadow_run_id",
    "scenario_id",
    "input_hash",
    "market_snapshot_hash",
    "synthetic_account_snapshot_hash",
    "configuration_hash",
    "preflight_hash",
    "approval_hash",
    "manifest_hash",
    "live_risk_decision_hash",
    "accepted",
    "blockers",
    "shadow_intent",
    "shadow_intent_hash",
    "safety_facts",
    "safety_facts_hash",
    "data_provenance",
    "data_provenance_hash",
    "repository_commit_sha",
    "parent_input_hash",
    "decision_hash",
})
_SUMMARY_FIELDS = frozenset({
    "shadow_run_id",
    "scenario_id",
    "input_hash",
    "decision_hash",
    "manifest_hash",
    "accepted",
    "blockers",
    "shadow_intent_count",
    "network_read_count",
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
    "data_provenance_hash",
    "summary_hash",
})
_SAFETY_FIELDS = frozenset(ShadowSafetyFacts.__dataclass_fields__)
_PROVENANCE_FIELDS = frozenset(ShadowDataProvenance.__dataclass_fields__)
_INTENT_FIELDS = frozenset(ShadowOrderIntent.__dataclass_fields__)
_REQUIRED_ROW_FIELDS = frozenset({
    "run_id",
    "run_mode",
    "data_sha256",
    "config_sha256",
    "code_sha256",
    "artifact_sha256",
    "storage_ref",
    "manifest_jsonb",
})


def _fail(message: str) -> None:
    raise ShadowBundleValidationError(message)


def _mapping(value: object, name: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        _fail(f"{name} must be an object")
    return {str(key): item for key, item in value.items()}


def _exact_fields(value: Mapping[str, object], expected: frozenset[str], name: str) -> None:
    observed = frozenset(value)
    if observed != expected:
        missing = sorted(expected - observed)
        extra = sorted(observed - expected)
        _fail(f"{name} fields mismatch; missing={missing}, extra={extra}")


def _sequence(value: object, name: str) -> tuple[object, ...]:
    if isinstance(value, (str, bytes, bytearray)) or not isinstance(value, Sequence):
        _fail(f"{name} must be an array")
    return tuple(value)


def _digest(value: object, name: str, *, length: int = 64, nullable: bool = False) -> str | None:
    if value is None and nullable:
        return None
    if not isinstance(value, str) or len(value) != length:
        _fail(f"{name} must be a {length}-character digest")
    try:
        int(value, 16)
    except ValueError:
        _fail(f"{name} must be hexadecimal")
    return value


def _boolean(value: object, name: str) -> bool:
    if type(value) is not bool:
        _fail(f"{name} must be a boolean")
    return value


def _integer(value: object, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        _fail(f"{name} must be an integer")
    return value


def _text(value: object, name: str) -> str:
    if not isinstance(value, str) or not value:
        _fail(f"{name} must be non-empty text")
    return value


def _decode(value: object) -> dict[str, Any]:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (TypeError, ValueError) as exc:
            raise ShadowBundleValidationError("manifest_jsonb is not valid JSON") from exc
    return _mapping(value, "shadow bundle")


def validate_shadow_bundle_payload(payload: object) -> dict[str, Any]:
    """Validate the complete canonical JSON bundle and every authority hash."""

    bundle = _decode(payload)
    _exact_fields(bundle, _TOP_LEVEL_FIELDS, "shadow bundle")
    if bundle["schema_version"] != 1:
        _fail("shadow bundle schema_version mismatch")
    if bundle["operation"] != "phase8b_shadow_assurance":
        _fail("shadow bundle operation mismatch")
    if bundle["status"] != "complete":
        _fail("shadow bundle is not complete")
    if bundle["runtime_version"] != SHADOW_RUNTIME_VERSION:
        _fail("shadow bundle runtime_version mismatch")

    decision = _mapping(bundle["decision"], "shadow decision")
    _exact_fields(decision, _DECISION_FIELDS, "shadow decision")
    try:
        UUID(_text(decision["shadow_run_id"], "decision.shadow_run_id"))
    except ValueError as exc:
        raise ShadowBundleValidationError("decision.shadow_run_id is invalid") from exc
    _text(decision["scenario_id"], "decision.scenario_id")
    for name in ("input_hash", "configuration_hash", "preflight_hash"):
        _digest(decision[name], f"decision.{name}")
    for name in (
        "market_snapshot_hash",
        "synthetic_account_snapshot_hash",
        "approval_hash",
        "manifest_hash",
        "live_risk_decision_hash",
        "parent_input_hash",
    ):
        _digest(decision[name], f"decision.{name}", nullable=True)
    _digest(decision["repository_commit_sha"], "decision.repository_commit_sha", length=40)
    blockers = _sequence(decision["blockers"], "decision.blockers")
    if any(not isinstance(item, str) or not item for item in blockers):
        _fail("decision.blockers must contain non-empty text")
    accepted = _boolean(decision["accepted"], "decision.accepted")
    if accepted == bool(blockers):
        _fail("decision acceptance and blockers disagree")

    safety_payload = _mapping(decision["safety_facts"], "decision.safety_facts")
    _exact_fields(safety_payload, _SAFETY_FIELDS, "decision.safety_facts")
    try:
        safety = ShadowSafetyFacts(**safety_payload)
    except (PermissionError, TypeError, ValueError) as exc:
        raise ShadowBundleValidationError("shadow safety facts are invalid") from exc
    if decision["safety_facts_hash"] != safety.record_hash:
        _fail("decision.safety_facts_hash mismatch")

    provenance_payload = _mapping(
        decision["data_provenance"], "decision.data_provenance"
    )
    _exact_fields(
        provenance_payload,
        _PROVENANCE_FIELDS,
        "decision.data_provenance",
    )
    try:
        provenance = ShadowDataProvenance(**provenance_payload)
    except (PermissionError, TypeError, ValueError) as exc:
        raise ShadowBundleValidationError("shadow data provenance is invalid") from exc
    if decision["data_provenance_hash"] != provenance.record_hash:
        _fail("decision.data_provenance_hash mismatch")
    if safety.network_read_count != provenance.network_read_count:
        _fail("safety and provenance read counts disagree")

    intent_payload = decision["shadow_intent"]
    if intent_payload is None:
        if decision["shadow_intent_hash"] is not None:
            _fail("shadow_intent_hash exists without an intent")
        intent_hash = None
    else:
        intent = _mapping(intent_payload, "decision.shadow_intent")
        _exact_fields(intent, _INTENT_FIELDS, "decision.shadow_intent")
        intent_blockers = _sequence(intent["blockers"], "decision.shadow_intent.blockers")
        for name in (
            "risk_accepted",
            "shadow_only",
            "production_write_enabled",
            "submit_reachable",
            "cancel_reachable",
            "transport_called",
        ):
            _boolean(intent[name], f"decision.shadow_intent.{name}")
        try:
            authoritative_intent = ShadowOrderIntent(
                **{
                    **intent,
                    "quantity": Decimal(str(intent["quantity"])),
                    "limit_price": Decimal(str(intent["limit_price"])),
                    "expected_notional": Decimal(str(intent["expected_notional"])),
                    "blockers": intent_blockers,
                }
            )
        except (PermissionError, TypeError, ValueError) as exc:
            raise ShadowBundleValidationError("shadow intent authority is invalid") from exc
        intent_hash = authoritative_intent.record_hash
        if decision["shadow_intent_hash"] != intent_hash:
            _fail("decision.shadow_intent_hash mismatch")
    if accepted and intent_payload is None:
        _fail("accepted decision requires a shadow intent")

    expected_decision_hash = sha256_payload({
        "scenario_id": decision["scenario_id"],
        "input_hash": decision["input_hash"],
        "market_snapshot_hash": decision["market_snapshot_hash"],
        "synthetic_account_snapshot_hash": decision["synthetic_account_snapshot_hash"],
        "configuration_hash": decision["configuration_hash"],
        "preflight_hash": decision["preflight_hash"],
        "approval_hash": decision["approval_hash"],
        "manifest_hash": decision["manifest_hash"],
        "live_risk_decision_hash": decision["live_risk_decision_hash"],
        "accepted": accepted,
        "blockers": blockers,
        "shadow_intent_hash": intent_hash,
        "safety_facts_hash": safety.record_hash,
        "data_provenance_hash": provenance.record_hash,
        "repository_commit_sha": decision["repository_commit_sha"],
        "parent_input_hash": decision["parent_input_hash"],
    })
    if decision["decision_hash"] != expected_decision_hash:
        _fail("decision.decision_hash mismatch")

    summary = _mapping(bundle["summary"], "shadow summary")
    _exact_fields(summary, _SUMMARY_FIELDS, "shadow summary")
    for name in (
        "shadow_intent_count",
        "network_read_count",
        "network_write_count",
        "production_transport_call_count",
        "authenticated_endpoint_call_count",
        "credential_read_count",
        "production_write_count",
    ):
        _integer(summary[name], f"summary.{name}")
    for name in (
        "accepted",
        "production_submit_reachable",
        "production_cancel_reachable",
        "real_account_data_used",
        "operator_database_accessed",
        "authenticated_proof_executed",
    ):
        _boolean(summary[name], f"summary.{name}")
    expected_summary_values = {
        "shadow_run_id": decision["shadow_run_id"],
        "scenario_id": decision["scenario_id"],
        "input_hash": decision["input_hash"],
        "decision_hash": decision["decision_hash"],
        "manifest_hash": decision["manifest_hash"],
        "accepted": accepted,
        "blockers": list(blockers),
        "shadow_intent_count": int(intent_payload is not None),
        "network_read_count": safety.network_read_count,
        "network_write_count": safety.network_write_count,
        "production_transport_call_count": safety.production_transport_call_count,
        "authenticated_endpoint_call_count": safety.authenticated_endpoint_call_count,
        "credential_read_count": safety.credential_read_count,
        "production_write_count": safety.production_write_count,
        "production_submit_reachable": safety.production_submit_reachable,
        "production_cancel_reachable": safety.production_cancel_reachable,
        "real_account_data_used": safety.real_account_data_used,
        "operator_database_accessed": safety.operator_database_accessed,
        "authenticated_proof_executed": safety.authenticated_proof_executed,
        "data_provenance_hash": provenance.record_hash,
    }
    for name, expected in expected_summary_values.items():
        observed = summary[name]
        if name == "blockers":
            observed = list(_sequence(observed, "summary.blockers"))
        if observed != expected:
            _fail(f"summary.{name} disagrees with decision")
    expected_summary_hash = sha256_payload(expected_summary_values)
    if summary["summary_hash"] != expected_summary_hash:
        _fail("summary.summary_hash mismatch")

    expected_bundle_hash = sha256_payload({
        key: value for key, value in bundle.items() if key != "bundle_hash"
    })
    if bundle["bundle_hash"] != expected_bundle_hash:
        _fail("shadow bundle hash mismatch")
    return json.loads(json.dumps(bundle))


def decode_and_validate_shadow_bundle(payload: object) -> dict[str, Any]:
    return validate_shadow_bundle_payload(payload)


def validate_shadow_manifest_row(row: Mapping[str, object]) -> dict[str, Any]:
    """Validate SQL identity columns and the complete JSON bundle."""

    values = _mapping(row, "shadow manifest row")
    missing = _REQUIRED_ROW_FIELDS - frozenset(values)
    if missing:
        _fail(f"shadow manifest row is missing columns: {sorted(missing)}")
    if values["run_mode"] != "simulation":
        _fail("shadow manifest run_mode mismatch")
    if values["storage_ref"] != "phase8b_shadow_assurance":
        _fail("shadow manifest storage_ref mismatch")
    bundle = validate_shadow_bundle_payload(values["manifest_jsonb"])
    decision = bundle["decision"]
    if str(values["run_id"]) != decision["shadow_run_id"]:
        _fail("row run_id disagrees with JSON shadow_run_id")
    if str(values["data_sha256"]) != decision["input_hash"]:
        _fail("row data_sha256 disagrees with decision input hash")
    if str(values["config_sha256"]) != decision["configuration_hash"]:
        _fail("row config_sha256 disagrees with decision configuration hash")
    expected_code_hash = sha256_payload({
        "repository_commit_sha": decision["repository_commit_sha"]
    })
    if str(values["code_sha256"]) != expected_code_hash:
        _fail("row code_sha256 disagrees with repository SHA derivation")
    if str(values["artifact_sha256"]) != bundle["bundle_hash"]:
        _fail("row artifact_sha256 disagrees with canonical bundle hash")
    return bundle


__all__ = [
    "ShadowBundleValidationError",
    "decode_and_validate_shadow_bundle",
    "validate_shadow_bundle_payload",
    "validate_shadow_manifest_row",
]
