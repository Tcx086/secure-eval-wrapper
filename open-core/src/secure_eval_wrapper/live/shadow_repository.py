"""PostgreSQL-authoritative persistence for isolated Phase 8B shadow runs."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from enum import Enum
from threading import RLock
from typing import Mapping
from uuid import UUID

from secure_eval_wrapper.data_collection.hashing import sha256_payload

from .shadow_models import ShadowDecisionRecord


SHADOW_DATABASE_PREFIX = "secure_eval_phase8b_shadow_"
MIGRATION_0026_SHA256 = "698772fb68c5c4981256682d064c3be641193ab10c8dbf55e1a5b390ca7c504a"
PERSISTENCE_CRASH_POINTS = frozenset({
    "after_decision_persist_before_summary",
    "before_transaction_commit",
    "after_transaction_commit_before_response",
})


class ShadowPersistenceConflict(RuntimeError):
    pass


class ShadowInjectedCrash(RuntimeError):
    def __init__(self, crash_point: str) -> None:
        self.crash_point = crash_point
        super().__init__(f"injected Phase 8B shadow crash at {crash_point}")


class ShadowPostCommitCrash(ShadowInjectedCrash):
    """The caller lost the response, but PostgreSQL committed a complete bundle."""


def validate_shadow_database_name(value: str) -> str:
    if not isinstance(value, str) or re.fullmatch(
        r"secure_eval_phase8b_shadow_[a-z0-9][a-z0-9_]{0,29}", value
    ) is None:
        raise PermissionError(
            "shadow persistence requires an explicit disposable "
            "secure_eval_phase8b_shadow_<suffix> database"
        )
    return value


def _json_value(value):
    if isinstance(value, Mapping):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [_json_value(item) for item in value]
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, Enum):
        return value.value
    return value


def shadow_decision_payload(decision: ShadowDecisionRecord) -> dict[str, object]:
    intent = decision.shadow_intent
    return {
        "shadow_run_id": str(decision.shadow_run_id),
        "scenario_id": decision.scenario_id,
        "input_hash": decision.input_hash,
        "market_snapshot_hash": decision.market_snapshot_hash,
        "synthetic_account_snapshot_hash": decision.synthetic_account_snapshot_hash,
        "configuration_hash": decision.configuration_hash,
        "preflight_hash": decision.preflight_hash,
        "approval_hash": decision.approval_hash,
        "manifest_hash": decision.manifest_hash,
        "live_risk_decision_hash": decision.live_risk_decision_hash,
        "accepted": decision.accepted,
        "blockers": list(decision.blockers),
        "shadow_intent": None if intent is None else _json_value({
            name: getattr(intent, name) for name in intent.__dataclass_fields__
        }),
        "shadow_intent_hash": None if intent is None else intent.record_hash,
        "safety_facts": _json_value({
            name: getattr(decision.safety_facts, name)
            for name in decision.safety_facts.__dataclass_fields__
        }),
        "safety_facts_hash": decision.safety_facts.record_hash,
        "repository_commit_sha": decision.repository_commit_sha,
        "parent_input_hash": decision.parent_input_hash,
        "decision_hash": decision.decision_hash,
    }


def shadow_bundle_payload(decision: ShadowDecisionRecord) -> dict[str, object]:
    decision_payload = shadow_decision_payload(decision)
    core = {
        "schema_version": 1,
        "operation": "phase8b_shadow_assurance",
        "status": "complete",
        "runtime_version": "phase8b-shadow-v1",
        "decision": decision_payload,
        "summary": {
            "shadow_run_id": str(decision.shadow_run_id),
            "scenario_id": decision.scenario_id,
            "input_hash": decision.input_hash,
            "decision_hash": decision.decision_hash,
            "manifest_hash": decision.manifest_hash,
            "accepted": decision.accepted,
            "blockers": list(decision.blockers),
            "shadow_intent_count": int(decision.shadow_intent is not None),
            "production_transport_call_count": 0,
            "authenticated_endpoint_call_count": 0,
            "credential_read_count": 0,
            "production_write_count": 0,
        },
    }
    return {**core, "bundle_hash": sha256_payload(core)}


def _preparing_payload(decision: ShadowDecisionRecord) -> dict[str, object]:
    return {
        "schema_version": 1,
        "operation": "phase8b_shadow_assurance",
        "status": "preparing",
        "decision": shadow_decision_payload(decision),
    }


@dataclass
class ShadowMemoryStore:
    bundles: dict[UUID, dict[str, object]] = field(default_factory=dict)
    lock: RLock = field(default_factory=RLock)


class MemoryShadowRepository:
    """Transaction-shaped offline repository used by socket-free suites."""

    authoritative_storage = "memory_test_double"

    def __init__(self, store: ShadowMemoryStore | None = None) -> None:
        self.store = ShadowMemoryStore() if store is None else store

    def persist_bundle(
        self,
        decision: ShadowDecisionRecord,
        *,
        crash_at: str | None = None,
    ) -> bool:
        if crash_at is not None and crash_at not in PERSISTENCE_CRASH_POINTS:
            raise ValueError("unknown persistence crash point")
        final = shadow_bundle_payload(decision)
        with self.store.lock:
            existing = self.store.bundles.get(decision.shadow_run_id)
            if existing is not None:
                if existing.get("bundle_hash") != final["bundle_hash"]:
                    raise ShadowPersistenceConflict(
                        "same shadow run ID has a different authoritative payload"
                    )
                return True
            staged = dict(self.store.bundles)
            staged[decision.shadow_run_id] = _preparing_payload(decision)
            if crash_at == "after_decision_persist_before_summary":
                raise ShadowInjectedCrash(crash_at)
            staged[decision.shadow_run_id] = final
            if crash_at == "before_transaction_commit":
                raise ShadowInjectedCrash(crash_at)
            self.store.bundles = staged
        if crash_at == "after_transaction_commit_before_response":
            raise ShadowPostCommitCrash(crash_at)
        return False

    def load_bundle(self, shadow_run_id: UUID) -> dict[str, object] | None:
        with self.store.lock:
            payload = self.store.bundles.get(shadow_run_id)
            return None if payload is None else json.loads(json.dumps(payload))

    def row_counts(self) -> Mapping[str, int]:
        with self.store.lock:
            return {"audit.run_manifests": len(self.store.bundles)}


class PostgresShadowRepository:
    """Use the existing generic audit manifest as one atomic shadow bundle."""

    authoritative_storage = "PostgreSQL"

    def __init__(self, connection, *, expected_database: str) -> None:
        self.connection = connection
        self.expected_database = validate_shadow_database_name(expected_database)
        self._verify_target()

    @staticmethod
    def _value(row, key: str, index: int):
        return row[key] if isinstance(row, Mapping) else row[index]

    def _verify_target(self) -> None:
        try:
            with self.connection.cursor() as cursor:
                cursor.execute(
                    "SELECT current_database(), "
                    "current_setting('server_version_num')::integer"
                )
                row = cursor.fetchone()
                actual_database = str(self._value(row, "current_database", 0))
                version = int(self._value(row, "current_setting", 1))
                if actual_database != self.expected_database:
                    raise PermissionError("shadow connection database identity mismatch")
                validate_shadow_database_name(actual_database)
                if version < 160000:
                    raise PermissionError("Phase 8B shadow persistence requires PostgreSQL 16")
                cursor.execute(
                    "SELECT migration_id,sha256 FROM audit.schema_migrations "
                    "ORDER BY migration_id"
                )
                rows = tuple(cursor.fetchall())
                catalog = {
                    str(self._value(item, "migration_id", 0)): str(
                        self._value(item, "sha256", 1)
                    )
                    for item in rows
                }
                if (
                    len(catalog) != 26
                    or not catalog
                    or max(key[:4] for key in catalog) != "0026"
                    or catalog.get("0026_phase8b_authenticated_readonly_preflight")
                    != MIGRATION_0026_SHA256
                    or any(key.startswith("0027") for key in catalog)
                ):
                    raise PermissionError(
                        "shadow database must expose the exact immutable 0001-0026 catalog"
                    )
                cursor.execute("SELECT to_regclass('audit.run_manifests')")
                row = cursor.fetchone()
                if self._value(row, "to_regclass", 0) is None:
                    raise PermissionError("generic audit manifest storage is unavailable")
        finally:
            self.connection.rollback()

    def persist_bundle(
        self,
        decision: ShadowDecisionRecord,
        *,
        crash_at: str | None = None,
    ) -> bool:
        if crash_at is not None and crash_at not in PERSISTENCE_CRASH_POINTS:
            raise ValueError("unknown persistence crash point")
        final = shadow_bundle_payload(decision)
        preparing = _preparing_payload(decision)
        inserted = False
        with self.connection.transaction():
            with self.connection.cursor() as cursor:
                cursor.execute(
                    "INSERT INTO audit.run_manifests "
                    "(run_id,run_mode,data_sha256,config_sha256,code_sha256,"
                    "artifact_sha256,seed,storage_ref,manifest_jsonb,created_at_utc) "
                    "VALUES (%s,'simulation',%s,%s,%s,%s,0,%s,%s::jsonb,%s) "
                    "ON CONFLICT (run_id) DO NOTHING RETURNING run_id",
                    (
                        decision.shadow_run_id,
                        decision.input_hash,
                        decision.configuration_hash,
                        sha256_payload({"repository_commit_sha": decision.repository_commit_sha}),
                        final["bundle_hash"],
                        "phase8b_shadow_assurance",
                        json.dumps(preparing, sort_keys=True, separators=(",", ":")),
                        datetime.fromisoformat("2026-07-18T00:00:00+00:00"),
                    ),
                )
                inserted = cursor.fetchone() is not None
                if not inserted:
                    cursor.execute(
                        "SELECT artifact_sha256,manifest_jsonb FROM audit.run_manifests "
                        "WHERE run_id=%s FOR SHARE",
                        (decision.shadow_run_id,),
                    )
                    row = cursor.fetchone()
                    payload = self._value(row, "manifest_jsonb", 1)
                    if isinstance(payload, str):
                        payload = json.loads(payload)
                    if (
                        str(self._value(row, "artifact_sha256", 0))
                        != final["bundle_hash"]
                        or payload.get("status") != "complete"
                        or payload.get("bundle_hash") != final["bundle_hash"]
                    ):
                        raise ShadowPersistenceConflict(
                            "same shadow run ID has a different authoritative payload"
                        )
                    return True
                if crash_at == "after_decision_persist_before_summary":
                    raise ShadowInjectedCrash(crash_at)
                cursor.execute(
                    "UPDATE audit.run_manifests SET manifest_jsonb=%s::jsonb "
                    "WHERE run_id=%s AND artifact_sha256=%s",
                    (
                        json.dumps(final, sort_keys=True, separators=(",", ":")),
                        decision.shadow_run_id,
                        final["bundle_hash"],
                    ),
                )
                if cursor.rowcount != 1:
                    raise ShadowPersistenceConflict("shadow bundle finalization lost authority")
                if crash_at == "before_transaction_commit":
                    raise ShadowInjectedCrash(crash_at)
        if inserted and crash_at == "after_transaction_commit_before_response":
            raise ShadowPostCommitCrash(crash_at)
        return False

    def load_bundle(self, shadow_run_id: UUID) -> dict[str, object] | None:
        with self.connection.cursor() as cursor:
            cursor.execute(
                "SELECT manifest_jsonb FROM audit.run_manifests "
                "WHERE run_id=%s AND storage_ref='phase8b_shadow_assurance'",
                (shadow_run_id,),
            )
            row = cursor.fetchone()
        self.connection.rollback()
        if row is None:
            return None
        payload = self._value(row, "manifest_jsonb", 0)
        return json.loads(payload) if isinstance(payload, str) else dict(payload)

    def row_counts(self) -> Mapping[str, int]:
        with self.connection.cursor() as cursor:
            cursor.execute(
                "SELECT count(*) FROM audit.run_manifests "
                "WHERE storage_ref='phase8b_shadow_assurance'"
            )
            row = cursor.fetchone()
            count = int(self._value(row, "count", 0))
        self.connection.rollback()
        return {"audit.run_manifests": count}


__all__ = [
    "MIGRATION_0026_SHA256",
    "MemoryShadowRepository",
    "PERSISTENCE_CRASH_POINTS",
    "PostgresShadowRepository",
    "SHADOW_DATABASE_PREFIX",
    "ShadowInjectedCrash",
    "ShadowMemoryStore",
    "ShadowPersistenceConflict",
    "ShadowPostCommitCrash",
    "shadow_bundle_payload",
    "shadow_decision_payload",
    "validate_shadow_database_name",
]
