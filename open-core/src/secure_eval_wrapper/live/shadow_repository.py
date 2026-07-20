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

from .migration_catalog import (
    MIGRATION_0026_SHA256,
    validate_migration_catalog_rows,
)
from .shadow_bundle import (
    ShadowBundleValidationError,
    decode_and_validate_shadow_bundle,
    validate_shadow_bundle_payload,
    validate_shadow_manifest_row,
)
from .shadow_models import SHADOW_RUNTIME_VERSION, ShadowDecisionRecord


SHADOW_DATABASE_PREFIX = "secure_eval_phase8b_shadow_"
LOCAL_SHADOW_POSTGRES_HOSTS = frozenset({"127.0.0.1", "::1"})
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


def validate_shadow_postgres_host(value: str) -> str:
    if type(value) is not str or value not in LOCAL_SHADOW_POSTGRES_HOSTS:
        raise PermissionError(
            "shadow persistence requires literal loopback host 127.0.0.1 or ::1"
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
        "data_provenance": _json_value(decision.data_provenance.public_payload()),
        "data_provenance_hash": decision.data_provenance.record_hash,
        "repository_commit_sha": decision.repository_commit_sha,
        "parent_input_hash": decision.parent_input_hash,
        "decision_hash": decision.decision_hash,
    }


def shadow_bundle_payload(decision: ShadowDecisionRecord) -> dict[str, object]:
    decision_payload = shadow_decision_payload(decision)
    safety = decision.safety_facts
    summary_core = {
        "shadow_run_id": str(decision.shadow_run_id),
        "scenario_id": decision.scenario_id,
        "input_hash": decision.input_hash,
        "decision_hash": decision.decision_hash,
        "manifest_hash": decision.manifest_hash,
        "accepted": decision.accepted,
        "blockers": list(decision.blockers),
        "shadow_intent_count": int(decision.shadow_intent is not None),
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
        "data_provenance_hash": decision.data_provenance.record_hash,
    }
    core = {
        "schema_version": 1,
        "operation": "phase8b_shadow_assurance",
        "status": "complete",
        "runtime_version": SHADOW_RUNTIME_VERSION,
        "decision": decision_payload,
        "summary": {
            **summary_core,
            "summary_hash": sha256_payload(summary_core),
        },
    }
    result = {**core, "bundle_hash": sha256_payload(core)}
    validate_shadow_bundle_payload(result)
    return result


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
                existing = validate_shadow_bundle_payload(existing)
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
            return None if payload is None else validate_shadow_bundle_payload(payload)

    def row_counts(self) -> Mapping[str, int]:
        with self.store.lock:
            return {"audit.run_manifests": len(self.store.bundles)}


class PostgresShadowRepository:
    """Use the existing generic audit manifest as one atomic shadow bundle."""

    authoritative_storage = "PostgreSQL"

    def __init__(
        self,
        connection,
        *,
        expected_database: str,
        expected_host: str,
    ) -> None:
        self.expected_host = validate_shadow_postgres_host(expected_host)
        self.connection = connection
        self.expected_database = validate_shadow_database_name(expected_database)
        self._verify_target()

    _ROW_COLUMNS = (
        "run_id",
        "run_mode",
        "data_sha256",
        "config_sha256",
        "code_sha256",
        "artifact_sha256",
        "storage_ref",
        "manifest_jsonb",
    )
    _ROW_SELECT = (
        "run_id,run_mode,data_sha256,config_sha256,code_sha256,"
        "artifact_sha256,storage_ref,manifest_jsonb"
    )

    @staticmethod
    def _value(row, key: str, index: int):
        return row[key] if isinstance(row, Mapping) else row[index]

    @classmethod
    def _manifest_row(cls, row) -> dict[str, object]:
        return {
            name: cls._value(row, name, index)
            for index, name in enumerate(cls._ROW_COLUMNS)
        }

    def _verify_target(self) -> None:
        try:
            connection_host = str(
                getattr(getattr(self.connection, "info", None), "host", "")
            )
            if connection_host != self.expected_host:
                raise PermissionError("shadow connection host identity mismatch")
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
                if not 160000 <= version < 170000:
                    raise PermissionError("Phase 8B shadow persistence requires exact PostgreSQL 16")
                cursor.execute(
                    "SELECT migration_id,filename,sha256::text FROM audit.schema_migrations "
                    "ORDER BY migration_id"
                )
                rows = tuple(cursor.fetchall())
                catalog = tuple(
                    (
                        str(self._value(item, "migration_id", 0)),
                        str(self._value(item, "filename", 1)),
                        str(self._value(item, "sha256", 2)),
                    )
                    for item in rows
                )
                validate_migration_catalog_rows(catalog)
                cursor.execute("SELECT to_regclass('audit.run_manifests')")
                row = cursor.fetchone()
                if self._value(row, "to_regclass", 0) is None:
                    raise PermissionError("generic audit manifest storage is unavailable")
                cursor.execute(
                    "SELECT count(*) FROM audit.run_manifests "
                    "WHERE run_mode IS DISTINCT FROM 'simulation' "
                    "OR storage_ref IS DISTINCT FROM 'phase8b_shadow_assurance' "
                    "OR manifest_jsonb->>'operation' IS DISTINCT FROM 'phase8b_shadow_assurance' "
                    "OR manifest_jsonb->>'status' IS DISTINCT FROM 'complete'"
                )
                row = cursor.fetchone()
                if int(self._value(row, "count", 0)):
                    raise PermissionError(
                        "shadow database contains non-shadow audit manifest rows"
                    )
                cursor.execute(
                    f"SELECT {self._ROW_SELECT} FROM audit.run_manifests "
                    "WHERE storage_ref='phase8b_shadow_assurance' ORDER BY run_id"
                )
                for manifest_row in cursor.fetchall():
                    validate_shadow_manifest_row(self._manifest_row(manifest_row))
                cursor.execute(
                    "SELECT table_schema,table_name FROM information_schema.tables "
                    "WHERE table_type='BASE TABLE' "
                    "AND table_schema NOT IN ('pg_catalog','information_schema') "
                    "ORDER BY table_schema,table_name"
                )
                for item in cursor.fetchall():
                    schema = str(self._value(item, "table_schema", 0))
                    table = str(self._value(item, "table_name", 1))
                    if (schema, table) in {
                        ("audit", "schema_migrations"),
                        ("audit", "run_manifests"),
                    }:
                        continue
                    if re.fullmatch(r"[a-z][a-z0-9_]*", schema) is None or re.fullmatch(
                        r"[a-z][a-z0-9_]*", table
                    ) is None:
                        raise PermissionError("shadow database exposes an unsafe table identity")
                    cursor.execute(f'SELECT EXISTS (SELECT 1 FROM "{schema}"."{table}")')
                    row = cursor.fetchone()
                    if bool(self._value(row, "exists", 0)):
                        raise PermissionError(
                            "shadow database contains non-shadow authoritative application rows"
                        )
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
        validate_shadow_bundle_payload(final)
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
                        f"SELECT {self._ROW_SELECT} FROM audit.run_manifests "
                        "WHERE run_id=%s FOR SHARE",
                        (decision.shadow_run_id,),
                    )
                    row = cursor.fetchone()
                    payload = validate_shadow_manifest_row(self._manifest_row(row))
                    if payload.get("bundle_hash") != final["bundle_hash"]:
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
                f"SELECT {self._ROW_SELECT} FROM audit.run_manifests "
                "WHERE run_id=%s AND storage_ref='phase8b_shadow_assurance'",
                (shadow_run_id,),
            )
            row = cursor.fetchone()
        self.connection.rollback()
        if row is None:
            return None
        return validate_shadow_manifest_row(self._manifest_row(row))

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
    "LOCAL_SHADOW_POSTGRES_HOSTS",
    "MIGRATION_0026_SHA256",
    "MemoryShadowRepository",
    "PERSISTENCE_CRASH_POINTS",
    "PostgresShadowRepository",
    "SHADOW_DATABASE_PREFIX",
    "ShadowBundleValidationError",
    "ShadowInjectedCrash",
    "ShadowMemoryStore",
    "ShadowPersistenceConflict",
    "ShadowPostCommitCrash",
    "shadow_bundle_payload",
    "shadow_decision_payload",
    "decode_and_validate_shadow_bundle",
    "validate_shadow_bundle_payload",
    "validate_shadow_database_name",
    "validate_shadow_manifest_row",
    "validate_shadow_postgres_host",
]
