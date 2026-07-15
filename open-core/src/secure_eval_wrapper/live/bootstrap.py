"""Audited local PostgreSQL bootstrap for Phase 8B operator authorization setup.

This module deliberately has no credential-provider or OKX transport dependency and never
invokes provider HTTP or sockets.  It creates only a dedicated PostgreSQL database, installs the accepted
immutable migrations, and persists one fixed read-only guarded-live configuration.
"""
from __future__ import annotations

import hashlib
import importlib.util
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from types import MappingProxyType
from typing import Callable, Mapping

from secure_eval_wrapper.data_collection.hashing import sha256_payload

from .configuration import phase8b_authenticated_readonly_configuration
from .durable_repository import DurablePostgresLiveRepository
from .identity import resolve_runtime_repository_identity, validate_git_commit_sha
from .models import live_uuid


BOOTSTRAP_VERSION = "phase8b-operator-bootstrap-v1"
DEFAULT_DATABASE = "secure_eval_phase8b"
FORBIDDEN_OPERATOR_DATABASE = "secure_eval_wrapper"
LATEST_MIGRATION = "0026_phase8b_authenticated_readonly_preflight"
CONFIRMATION_FLAG = "--confirm-readonly-bootstrap"
_IDENTIFIER = re.compile(r"^[a-z][a-z0-9_]{0,62}$")

EXPECTED_MIGRATION_CATALOG: Mapping[str, str] = MappingProxyType({
    "0001_initial_schema": "598486e6af2eed4559564593adc0b66deff9e21ea91dbda560980c208a2950c5",
    "0002_schema_migrations": "36c91efa851e10fcc6039ebd8715af1c985237af6ff556e6943e10329458f76f",
    "0003_data_quality_quarantine": "d0b32a72ad98a9d1361bfa57770a9b7d58ae2323816e8b3d77c3d05f66b35a9a",
    "0004_reconciliation_persistence": "efe77fa89b25f90dea3f49a70b22b8cc376c434333abbff6fd17cc9eb75fd7ba",
    "0005_trade_funding_instrument_hardening": "b18d66f37df55923a1e1cfba709784de55ab90d0c5ff250b8d683dc6029f9d48",
    "0006_phase2_final_hardening": "af507329f29e63ab260317b879da5e82917aafd7368d692b343a09ccafdace5d",
    "0007_alpha_signal_library": "0a355d3238afcf8691b5366e46332c3e1e6862a9ed574e740e3435479d8883a4",
    "0008_phase3_phase4_audit_repairs": "a59dff645009c117a5146d2bd4102a9ed048126ca77b61566f8d31bf1fcba64b",
    "0009_phase5_simulated_execution_backtesting": "9b49718ee48e45dda42916568f815723f94578eff814ffe0e0b236aa3523c0d5",
    "0010_phase5_second_audit_repairs": "1387ccf65a7a7ac8c2c7b4d93de8443e47963740dcefbf30a0ae248ea5e978a0",
    "0011_phase5_run_membership_repairs": "0c0a0ed26ec7419e773e69e8c1ab07d4e220377059e0bf2358b519055e6540a8",
    "0012_phase5_run_scoped_projection_repairs": "2a55979b6419bc3eb464d2374d68a40d8cc559fac1a984d2ebcada5974d82d4d",
    "0013_phase6_monitoring_simulated_fix": "5e7eb61540507ce4c0f7fb92b78fdebf2fb551a770c129b45d82d19cff592761",
    "0014_phase6_first_audit_repairs": "30971466069b6dbcb29f7b08568ebd7791097d996ccbc742cb7f3aa8096ba4fe",
    "0015_phase6_concurrency_and_audit_integrity": "5ae8bcfa8db52110978dddd4864700dac6a8e549000dde50065969910b24aec1",
    "0016_phase7_safe_paper_trading": "866179dc6a95bf65a416c62d891cd06ce34cf28bceaeb8f29223ad70ef863b0f",
    "0017_phase7_durable_paper_recovery": "c2a2e4ca347775898c11443da89552685b0d723a094335719ef07515e4639302",
    "0018_phase7_recovery_state_machine_integrity": "c49fad9ed9b5cf3eeee6ae071f6a8b6e4d73c67c80571b09030c4b21b519d59d",
    "0019_phase7_venue_event_and_accounting_integrity": "7a139eb65b7ed66fd16b2e7e20794e57f28dc59ab5992c468cb21bae22d68457",
    "0020_phase7_price_terminal_and_expiry_integrity": "ce24b36b2ff6e276ce69edeef3044ab7f891e154fa389d55e53619deab990ad5",
    "0021_phase7_cancel_terminal_accounting_integrity": "a9a088b497addb45353a3b906caafd5e3532bb389a8ddbfe18626d26597c7506",
    "0022_phase8_guarded_live_foundation": "b01c0c0c7801247594ee75009055f899c8902b6cfa1b44ed91ad8451e478e434",
    "0023_phase8a_authority_recovery_and_cli_integrity": "cd06abb25ef7a9c178b5aad8c6378f982c879b2e3b52eb4667e067554b987eef",
    "0024_phase8a_evidence_reconciliation_metadata_integrity": "3f5671e34d312770dd05763116ce0102da1534061df887dc0c6754f0cc48b214",
    "0025_phase8a_okx_credential_permission_authority": "773b2cc2cfb8fcdc9cd9ce022904c096e1f9520915ad8549c5a92d3067d7fc61",
    LATEST_MIGRATION: "698772fb68c5c4981256682d064c3be641193ab10c8dbf55e1a5b390ca7c504a",
})

PHASE8_REQUIRED_TABLES = frozenset({
    "live_configuration_snapshots", "live_credential_references", "live_account_snapshots",
    "live_okx_response_bundles", "live_okx_response_envelopes", "live_preflight_sources",
    "live_preflight_reports", "live_preflight_checks", "live_preflight_check_sources",
    "live_approvals", "live_run_manifests", "live_runs", "live_kill_switches",
    "live_kill_events", "live_run_risk_state", "live_order_intents",
    "live_runtime_risk_decisions", "live_reservations", "live_dispatch_outbox",
    "live_dispatch_events", "live_cancel_outbox", "live_transport_attempts",
    "live_order_observations", "live_order_projections", "live_fill_observations",
    "live_reconciliations", "live_reconciliation_differences", "live_recovery_records",
    "live_lifecycle_events", "live_pre_run_summaries", "live_post_run_summaries",
    "live_market_source_bindings", "live_instrument_metadata_sources",
    "live_reconciliation_input_bundles", "live_recovery_query_completions",
    "live_authenticated_readonly_proofs",
})
PHASE8_REQUIRED_INDEXES = frozenset({
    "idx_live_preflight_sources_run_kind", "idx_live_risk_state_day",
    "idx_live_reservation_balance", "idx_live_dispatch_claimable",
    "idx_live_recovery_claims", "idx_live_okx_bundle_run_purpose",
    "idx_live_metadata_run_instrument", "idx_live_recovery_query_matrix",
    "idx_live_authenticated_readonly_account_time",
})
PHASE8_REQUIRED_TRIGGERS = frozenset({
    "trg_guard_live_preflight_authority", "trg_guard_live_manifest_chain",
    "trg_live_approval_consumption", "trg_live_intent_mutation",
    "trg_live_dispatch_request_immutable", "trg_live_cancel_request_immutable",
    "trg_live_dispatch_monotonic", "trg_live_reservation_monotonic",
    "trg_live_projection_monotonic", "trg_live_collector_source",
    "trg_guard_live_okx_response_payload_hash", "trg_validate_live_okx_bundle_matrix",
    "trg_validate_live_okx_envelope_matrix", "trg_validate_live_preflight_graph",
    "trg_validate_live_0024_source_details", "trg_validate_live_0025_credential_permission_source",
    "trg_validate_live_0025_permission_report", "trg_guard_live_0024_reconciliation",
    "trg_validate_live_0024_reconciliation_exact", "trg_guard_live_0024_intent_metadata",
    "trg_guard_live_0024_outbox_metadata", "trg_validate_live_0024_recovery_outcome",
    "trg_guard_live_0024_kill_reset", "trg_validate_live_authenticated_readonly_proof",
    "trg_live_authenticated_readonly_proofs_immutable",
})


class BootstrapSafetyError(PermissionError):
    """A public-safe, fail-closed bootstrap refusal."""


class BootstrapOperationError(BootstrapSafetyError):
    """A public-safe bootstrap refusal with exact completed-stage provenance."""

    def __init__(self, message: str, *, last_completed_stage: str) -> None:
        super().__init__(message)
        self.last_completed_stage = last_completed_stage


@dataclass(frozen=True)
class PostgresAdminTarget:
    database: str = DEFAULT_DATABASE
    host: str = "127.0.0.1"
    port: int = 5432
    admin_database: str = "postgres"
    admin_user: str = "postgres"
    sslmode: str = "disable"

    def __post_init__(self) -> None:
        for name in ("database", "admin_database"):
            value = getattr(self, name)
            if not isinstance(value, str) or _IDENTIFIER.fullmatch(value) is None:
                raise ValueError(f"{name} must be a conservative PostgreSQL identifier")
        if self.database == FORBIDDEN_OPERATOR_DATABASE:
            raise BootstrapSafetyError("the existing secure_eval_wrapper database is never a bootstrap target")
        if not isinstance(self.host, str) or not self.host.strip():
            raise ValueError("host must be non-empty")
        if isinstance(self.port, bool) or not isinstance(self.port, int) or not 1 <= self.port <= 65535:
            raise ValueError("port must be an integer from 1 through 65535")
        if not isinstance(self.admin_user, str) or not self.admin_user.strip():
            raise ValueError("admin_user must be non-empty")
        if self.sslmode not in {"disable", "require", "verify-ca", "verify-full"}:
            raise ValueError("unsupported PostgreSQL sslmode")

    def connection_kwargs(self, database: str, *, read_only: bool) -> dict[str, object]:
        result: dict[str, object] = {
            "host": self.host,
            "port": self.port,
            "dbname": database,
            "user": self.admin_user,
            "sslmode": self.sslmode,
        }
        if read_only:
            result["options"] = "-c default_transaction_read_only=on"
        return result

    @property
    def public_identity(self) -> str:
        return sha256_payload({
            "host": self.host,
            "port": self.port,
            "database": self.database,
            "admin_database": self.admin_database,
            "sslmode": self.sslmode,
        })


@dataclass(frozen=True)
class DatabaseInspection:
    database_exists: bool
    database_oid: int | None
    database_identity_sha256: str
    catalog_state: str
    catalog: tuple[tuple[str, str, str], ...]
    latest_migration: str | None
    application_row_count: int
    configuration_row_count: int
    production_write_count: int
    blockers: tuple[str, ...]


def _public_safety_flags() -> dict[str, bool]:
    return {
        "credentials_accessed": False,
        "network_reads_occurred": False,
        "network_writes_occurred": False,
        "real_proof_executed": False,
    }


def _migration_root() -> Path:
    return Path(__file__).resolve().parents[3] / "db" / "migrations"


def verify_local_migration_files(root: Path | None = None) -> tuple[Path, ...]:
    migration_root = _migration_root() if root is None else Path(root)
    paths = tuple(sorted(migration_root.glob("[0-9][0-9][0-9][0-9]_*.sql")))
    observed = {
        path.stem: hashlib.sha256(path.read_bytes().replace(b"\r\n", b"\n")).hexdigest()
        for path in paths
    }
    if observed != dict(EXPECTED_MIGRATION_CATALOG):
        raise BootstrapSafetyError("local immutable migration files do not match accepted 0001-0026 hashes")
    return paths


def _default_connector(**kwargs):
    try:
        import psycopg
    except ImportError as exc:
        raise RuntimeError("operator bootstrap requires the PostgreSQL package extra") from exc
    return psycopg.connect(**kwargs)


def _default_identity():
    return resolve_runtime_repository_identity(environment={})


def _quoted(identifier: str) -> str:
    return '"' + identifier.replace('"', '""') + '"'


class Phase8BOperatorBootstrap:
    """Plan, initialize, and verify one dedicated local PostgreSQL target."""

    def __init__(
        self,
        target: PostgresAdminTarget,
        *,
        connector: Callable[..., object] = _default_connector,
        identity_resolver: Callable[[], object] = _default_identity,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.target = target
        self._connector = connector
        self._identity_resolver = identity_resolver
        self._clock = clock or (lambda: datetime.now(timezone.utc))

    def _connect(self, database: str, *, read_only: bool):
        return self._connector(**self.target.connection_kwargs(database, read_only=read_only))

    @staticmethod
    def _fetchone(connection, statement: str, params=()):
        with connection.cursor() as cursor:
            cursor.execute(statement, params)
            return cursor.fetchone()

    @staticmethod
    def _fetchall(connection, statement: str, params=()):
        with connection.cursor() as cursor:
            cursor.execute(statement, params)
            return cursor.fetchall()

    def _repository_sha(self, expected_reviewed_sha: str | None = None) -> str:
        identity = self._identity_resolver()
        observed = validate_git_commit_sha(identity.observed_commit_sha, field_name="observed_repository_sha")
        if expected_reviewed_sha is not None:
            expected = validate_git_commit_sha(expected_reviewed_sha, field_name="expected_reviewed_sha")
            if observed != expected:
                raise BootstrapSafetyError("observed repository SHA does not match the exact expected SHA")
        return observed

    def _database_reference(self) -> tuple[bool, int | None, str]:
        connection = self._connect(self.target.admin_database, read_only=True)
        try:
            row = self._fetchone(
                connection,
                "SELECT oid::bigint FROM pg_database WHERE datname=%s",
                (self.target.database,),
            )
        finally:
            connection.close()
        exists = row is not None
        oid = None if row is None else int(row[0])
        identity = sha256_payload({
            "target": self.target.public_identity,
            "database_exists": exists,
            "database_oid": oid,
        })
        return exists, oid, identity

    def _inspect_existing_database(
        self,
        oid: int,
        database_identity: str,
        expected_configuration=None,
    ) -> DatabaseInspection:
        blockers: list[str] = []
        connection = self._connect(self.target.database, read_only=True)
        try:
            catalog_ready = bool(self._fetchone(
                connection,
                "SELECT to_regclass('audit.schema_migrations') IS NOT NULL",
            )[0])
            user_tables = self._fetchall(
                connection,
                "SELECT table_schema,table_name FROM information_schema.tables "
                "WHERE table_type='BASE TABLE' AND table_schema NOT IN "
                "('pg_catalog','information_schema') ORDER BY table_schema,table_name",
            )
            catalog: tuple[tuple[str, str, str], ...] = ()
            if catalog_ready:
                catalog = tuple((str(a), str(b), str(c)) for a, b, c in self._fetchall(
                    connection,
                    "SELECT migration_id,filename,sha256::text FROM audit.schema_migrations "
                    "ORDER BY migration_id",
                ))

            expected_rows = tuple(
                (migration_id, migration_id + ".sql", digest)
                for migration_id, digest in EXPECTED_MIGRATION_CATALOG.items()
            )
            if not catalog_ready and not user_tables:
                catalog_state = "empty"
            elif not catalog_ready:
                catalog_state = "legacy_or_unknown"
                blockers.append("existing_database_has_objects_without_migration_catalog")
            elif catalog == expected_rows:
                catalog_state = "exact_0001_0026"
            else:
                observed_ids = {row[0] for row in catalog}
                expected_ids = set(EXPECTED_MIGRATION_CATALOG)
                if observed_ids - expected_ids:
                    catalog_state = "unknown_migrations"
                    blockers.append("migration_catalog_contains_unknown_entries")
                elif observed_ids != expected_ids:
                    catalog_state = "partial_catalog"
                    blockers.append("partial_migration_catalog_is_never_auto_upgraded")
                else:
                    catalog_state = "hash_or_filename_mismatch"
                    blockers.append("immutable_migration_catalog_mismatch")

            application_rows = 0
            configuration_rows: list[tuple] = []
            if catalog_state == "exact_0001_0026":
                for schema, table in user_tables:
                    if (schema, table) == ("audit", "schema_migrations"):
                        continue
                    count = int(self._fetchone(
                        connection,
                        f"SELECT count(*)::bigint FROM {_quoted(schema)}.{_quoted(table)}",
                    )[0])
                    if (schema, table) == ("execution", "live_configuration_snapshots"):
                        if count:
                            configuration_rows = list(self._fetchall(
                                connection,
                                "SELECT configuration_snapshot_id,configuration_sha256,record_sha256,"
                                "account_fingerprint,dry_run,read_only_preflight,production_write_enabled "
                                "FROM execution.live_configuration_snapshots ORDER BY configuration_sha256",
                            ))
                    else:
                        application_rows += count
                if application_rows:
                    blockers.append("existing_database_contains_unsafe_application_rows")
                if configuration_rows:
                    if expected_configuration is None:
                        blockers.append("existing_configuration_requires_exact_plan_inputs")
                    else:
                        expected_id = live_uuid(
                            "configuration", {"hash": expected_configuration.configuration_hash}
                        )
                        exact = len(configuration_rows) == 1 and (
                            configuration_rows[0][0] == expected_id
                            and configuration_rows[0][1] == expected_configuration.configuration_hash
                            and configuration_rows[0][2] == expected_configuration.configuration_hash
                            and configuration_rows[0][3] == expected_configuration.account_fingerprint
                            and bool(configuration_rows[0][4])
                            and bool(configuration_rows[0][5])
                            and not bool(configuration_rows[0][6])
                        )
                        if not exact:
                            blockers.append("existing_guarded_live_configuration_conflicts")

            production_write_count = 0
            if catalog_state == "exact_0001_0026":
                production_write_count = int(self._fetchone(
                    connection,
                    "SELECT count(*)::bigint FROM execution.live_transport_attempts "
                    "WHERE external_write_attempted OR successful_write",
                )[0])
                if production_write_count:
                    blockers.append("production_write_history_is_not_zero")
        finally:
            connection.close()

        return DatabaseInspection(
            database_exists=True,
            database_oid=oid,
            database_identity_sha256=database_identity,
            catalog_state=catalog_state,
            catalog=catalog,
            latest_migration=None if not catalog else catalog[-1][0],
            application_row_count=application_rows,
            configuration_row_count=len(configuration_rows),
            production_write_count=production_write_count,
            blockers=tuple(sorted(set(blockers))),
        )

    def inspect(self, *, expected_configuration=None) -> DatabaseInspection:
        verify_local_migration_files()
        exists, oid, identity = self._database_reference()
        if not exists:
            return DatabaseInspection(
                database_exists=False,
                database_oid=None,
                database_identity_sha256=identity,
                catalog_state="absent",
                catalog=(),
                latest_migration=None,
                application_row_count=0,
                configuration_row_count=0,
                production_write_count=0,
                blockers=(),
            )
        return self._inspect_existing_database(oid, identity, expected_configuration)

    def inspect_public(self) -> dict[str, object]:
        observed_sha = self._repository_sha()
        state = self.inspect()
        return {
            "action": "inspect",
            "database": self.target.database,
            "database_exists": state.database_exists,
            "database_identity_sha256": state.database_identity_sha256,
            "catalog_state": state.catalog_state,
            "current_latest_migration": state.latest_migration,
            "expected_latest_migration": LATEST_MIGRATION,
            "expected_0026_sha256": EXPECTED_MIGRATION_CATALOG[LATEST_MIGRATION],
            "immutable_catalog_verified": state.catalog_state == "exact_0001_0026",
            "observed_repository_sha": observed_sha,
            "application_row_count": state.application_row_count,
            "configuration_row_count": state.configuration_row_count,
            "production_write_count": state.production_write_count,
            "blockers": list(state.blockers),
            **_public_safety_flags(),
        }

    def plan(
        self,
        *,
        expected_reviewed_sha: str,
        account_fingerprint: str,
        instrument: str,
    ) -> dict[str, object]:
        observed_sha = self._repository_sha(expected_reviewed_sha)
        configuration = phase8b_authenticated_readonly_configuration(
            account_fingerprint, instrument
        )
        state = self.inspect(expected_configuration=configuration)
        migrations_required = state.catalog_state in {"absent", "empty"}
        core = {
            "command": "secure-eval-live-bootstrap plan",
            "version": BOOTSTRAP_VERSION,
            "action": "plan",
            "database": self.target.database,
            "database_exists": state.database_exists,
            "database_identity_sha256": state.database_identity_sha256,
            "catalog_state": state.catalog_state,
            "current_migration_count": len(state.catalog),
            "current_latest_migration": state.latest_migration,
            "expected_latest_migration": LATEST_MIGRATION,
            "expected_0026_sha256": EXPECTED_MIGRATION_CATALOG[LATEST_MIGRATION],
            "immutable_catalog_verified": state.catalog_state == "exact_0001_0026",
            "observed_repository_sha": observed_sha,
            "expected_reviewed_sha": expected_reviewed_sha,
            "account_fingerprint": configuration.account_fingerprint,
            "instrument": instrument,
            "configuration_hash": configuration.configuration_hash,
            "current_endpoint_catalog_hash": configuration.endpoint_catalog_hash,
            "current_adapter_implementation_hash": configuration.provider_implementation_hash,
            "intended_credential_policy": list(configuration.credential_source_policy),
            "production_write_enabled": configuration.production_write_enabled,
            "database_creation_required": not state.database_exists,
            "migrations_required": migrations_required,
            "migration_count_to_apply": len(EXPECTED_MIGRATION_CATALOG) if migrations_required else 0,
            "configuration_insertion_required": state.configuration_row_count == 0,
            "configuration_replay": state.configuration_row_count == 1,
            "application_row_count": state.application_row_count,
            "production_write_count": state.production_write_count,
            "blockers": list(state.blockers),
            **_public_safety_flags(),
        }
        return {**core, "plan_hash": sha256_payload(core)}

    @staticmethod
    def _load_migration_runner():
        path = Path(__file__).resolve().parents[3] / "scripts" / "apply_postgres_migrations.py"
        spec = importlib.util.spec_from_file_location("secure_eval_phase8b_migration_runner", path)
        if spec is None or spec.loader is None:
            raise RuntimeError("accepted PostgreSQL migration runner is unavailable")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def _create_database(self) -> None:
        connection = self._connect(self.target.admin_database, read_only=False)
        try:
            connection.autocommit = True
            if self._fetchone(
                connection,
                "SELECT 1 FROM pg_database WHERE datname=%s",
                (self.target.database,),
            ) is not None:
                raise BootstrapSafetyError("database identity changed after the confirmed plan")
            try:
                from psycopg import sql
            except ImportError as exc:
                raise RuntimeError("operator bootstrap requires the PostgreSQL package extra") from exc
            with connection.cursor() as cursor:
                cursor.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(self.target.database)))
        finally:
            connection.close()

    def _apply_all_migrations(self) -> None:
        paths = verify_local_migration_files()
        runner = self._load_migration_runner()
        connection = self._connect(self.target.database, read_only=False)
        try:
            runner.bootstrap(connection)
            connection.commit()
            for path in paths:
                digest, _ = runner._apply_migration(connection, path)
                if digest != EXPECTED_MIGRATION_CATALOG[path.stem]:
                    raise BootstrapSafetyError("migration runner digest disagrees with accepted catalog")
        finally:
            connection.close()

    def _schema_contract(self, connection) -> dict[str, object]:
        tables = {row[0] for row in self._fetchall(
            connection,
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_schema='execution' AND table_type='BASE TABLE'",
        )}
        indexes = {row[0] for row in self._fetchall(
            connection,
            "SELECT indexname FROM pg_indexes WHERE schemaname='execution'",
        )}
        triggers = {row[0] for row in self._fetchall(
            connection,
            "SELECT tgname FROM pg_trigger t JOIN pg_class c ON c.oid=t.tgrelid "
            "JOIN pg_namespace n ON n.oid=c.relnamespace "
            "WHERE n.nspname='execution' AND NOT t.tgisinternal",
        )}
        return {
            "phase8_tables_verified": PHASE8_REQUIRED_TABLES <= tables,
            "phase8_indexes_verified": PHASE8_REQUIRED_INDEXES <= indexes,
            "phase8_triggers_verified": PHASE8_REQUIRED_TRIGGERS <= triggers,
        }

    def verify(
        self,
        *,
        expected_reviewed_sha: str,
        account_fingerprint: str,
        instrument: str,
    ) -> dict[str, object]:
        observed_sha = self._repository_sha(expected_reviewed_sha)
        configuration = phase8b_authenticated_readonly_configuration(
            account_fingerprint, instrument
        )
        state = self.inspect(expected_configuration=configuration)
        contract = {
            "phase8_tables_verified": False,
            "phase8_indexes_verified": False,
            "phase8_triggers_verified": False,
        }
        typed_reload_verified = False
        snapshot_id = live_uuid("configuration", {"hash": configuration.configuration_hash})
        if not state.blockers and state.catalog_state == "exact_0001_0026" and state.configuration_row_count == 1:
            connection = self._connect(self.target.database, read_only=True)
            try:
                contract = self._schema_contract(connection)
                repository = DurablePostgresLiveRepository(connection)
                typed_reload_verified = (
                    repository.load_guarded_live_configuration(configuration.configuration_hash)
                    == configuration
                )
            finally:
                connection.close()
        ready = all(contract.values()) and typed_reload_verified and (
            not state.blockers
            and state.latest_migration == LATEST_MIGRATION
            and state.production_write_count == 0
            and state.configuration_row_count == 1
        )
        return {
            "command": "secure-eval-live-bootstrap verify",
            "version": BOOTSTRAP_VERSION,
            "action": "verify",
            "database": self.target.database,
            "ready_for_operator_authorization": ready,
            "database_identity_sha256": state.database_identity_sha256,
            "catalog_state": state.catalog_state,
            "migration_count": len(state.catalog),
            "latest_migration": state.latest_migration,
            "immutable_catalog_verified": state.catalog_state == "exact_0001_0026",
            "migration_0026_installed": state.latest_migration == LATEST_MIGRATION,
            "migration_hashes_verified": state.catalog == tuple(
                (key, key + ".sql", value) for key, value in EXPECTED_MIGRATION_CATALOG.items()
            ),
            **contract,
            "configuration_snapshot_id": str(snapshot_id),
            "configuration_hash": configuration.configuration_hash,
            "bootstrap_record_hash": configuration.configuration_hash,
            "typed_configuration_reload_verified": typed_reload_verified,
            "current_endpoint_catalog_hash": configuration.endpoint_catalog_hash,
            "current_provider_implementation_hash": configuration.provider_implementation_hash,
            "observed_repository_sha": observed_sha,
            "expected_reviewed_sha": expected_reviewed_sha,
            "account_fingerprint": configuration.account_fingerprint,
            "instrument": instrument,
            "allowed_instruments": list(configuration.allowed_instruments),
            "allowed_instrument_types": list(configuration.allowed_instrument_types),
            "allowed_settlement_assets": list(configuration.allowed_settlement_assets),
            "base_currency": configuration.base_currency,
            "allowed_order_types": list(configuration.allowed_order_types),
            "credential_source_policy": list(configuration.credential_source_policy),
            "dry_run": configuration.dry_run,
            "read_only_preflight": configuration.read_only_preflight,
            "production_write_enabled": configuration.production_write_enabled,
            "automatic_flatten": configuration.automatic_flatten,
            "allow_short": configuration.allow_short,
            "allow_perpetual": configuration.allow_perpetual,
            "production_write_count": state.production_write_count,
            "blockers": list(state.blockers),
            **_public_safety_flags(),
        }

    def initialize(
        self,
        *,
        expected_reviewed_sha: str,
        account_fingerprint: str,
        instrument: str,
        previous_plan_hash: str,
        confirm_readonly_bootstrap: bool,
    ) -> dict[str, object]:
        progress = ["not_started"]
        try:
            return self._initialize_confirmed(
                expected_reviewed_sha=expected_reviewed_sha,
                account_fingerprint=account_fingerprint,
                instrument=instrument,
                previous_plan_hash=previous_plan_hash,
                confirm_readonly_bootstrap=confirm_readonly_bootstrap,
                progress=progress,
            )
        except BootstrapOperationError:
            raise
        except (BootstrapSafetyError, ValueError) as exc:
            raise BootstrapOperationError(
                str(exc), last_completed_stage=progress[0]
            ) from exc
        except Exception as exc:
            raise BootstrapOperationError(
                "local_postgresql_operation_failed",
                last_completed_stage=progress[0],
            ) from exc

    def _initialize_confirmed(
        self,
        *,
        expected_reviewed_sha: str,
        account_fingerprint: str,
        instrument: str,
        previous_plan_hash: str,
        confirm_readonly_bootstrap: bool,
        progress: list[str],
    ) -> dict[str, object]:
        if not confirm_readonly_bootstrap:
            raise BootstrapSafetyError(f"initialization requires exact {CONFIRMATION_FLAG} confirmation")
        if not isinstance(previous_plan_hash, str) or not re.fullmatch(r"[0-9a-f]{64}", previous_plan_hash):
            raise BootstrapSafetyError("previous plan hash must be an exact lowercase SHA-256")
        progress[0] = "confirmation_validated"

        first = self.plan(
            expected_reviewed_sha=expected_reviewed_sha,
            account_fingerprint=account_fingerprint,
            instrument=instrument,
        )
        if first["blockers"]:
            raise BootstrapSafetyError("confirmed plan contains blockers")
        if first["plan_hash"] != previous_plan_hash:
            raise BootstrapSafetyError("provided plan hash does not match current read-only plan")
        current = self.plan(
            expected_reviewed_sha=expected_reviewed_sha,
            account_fingerprint=account_fingerprint,
            instrument=instrument,
        )
        if current != first:
            raise BootstrapSafetyError("database or repository state changed after plan confirmation")
        _, _, immediate_database_identity = self._database_reference()
        if immediate_database_identity != current["database_identity_sha256"]:
            raise BootstrapSafetyError("database identity changed between plan and apply")
        progress[0] = "plan_revalidated"

        if current["database_creation_required"]:
            self._create_database()
        progress[0] = "database_ready"
        if current["migrations_required"]:
            self._apply_all_migrations()
        progress[0] = "migrations_ready"

        configuration = phase8b_authenticated_readonly_configuration(
            account_fingerprint, instrument
        )
        pre_persist = self.inspect(expected_configuration=configuration)
        if pre_persist.catalog_state != "exact_0001_0026" or pre_persist.blockers:
            raise BootstrapSafetyError("post-migration catalog is not safe for configuration persistence")
        schema_connection = self._connect(self.target.database, read_only=True)
        try:
            schema_contract = self._schema_contract(schema_connection)
        finally:
            schema_connection.close()
        if not all(schema_contract.values()):
            raise BootstrapSafetyError(
                "Phase 8 schema contract failed before configuration persistence"
            )
        progress[0] = "schema_verified"
        connection = self._connect(self.target.database, read_only=False)
        try:
            repository = DurablePostgresLiveRepository(connection)
            snapshot_id = repository.persist_guarded_live_configuration_snapshot(
                configuration=configuration,
                created_at_utc=self._clock(),
            )
            progress[0] = "configuration_persisted"
        finally:
            connection.close()

        result = self.verify(
            expected_reviewed_sha=expected_reviewed_sha,
            account_fingerprint=account_fingerprint,
            instrument=instrument,
        )
        if not result["ready_for_operator_authorization"]:
            raise BootstrapSafetyError("configuration persisted but final verification failed closed")
        progress[0] = "verification_completed"
        result_core = {
            "command": "secure-eval-live-bootstrap initialize",
            "version": BOOTSTRAP_VERSION,
            "target_database": self.target.database,
            "observed_repository_sha": result["observed_repository_sha"],
            "expected_reviewed_sha": result["expected_reviewed_sha"],
            "migration_count": result["migration_count"],
            "latest_migration": result["latest_migration"],
            "immutable_catalog_verified": result["immutable_catalog_verified"],
            "migration_0026_installed": result["migration_0026_installed"],
            "configuration_snapshot_id": str(snapshot_id),
            "configuration_hash": result["configuration_hash"],
            "account_fingerprint": result["account_fingerprint"],
            "instrument": instrument,
            "credential_policy": result["credential_source_policy"],
            "endpoint_catalog_hash": result["current_endpoint_catalog_hash"],
            "adapter_implementation_hash": result["current_provider_implementation_hash"],
            "dry_run": result["dry_run"],
            "read_only_preflight": result["read_only_preflight"],
            "production_write_enabled": result["production_write_enabled"],
            "credentials_accessed": result["credentials_accessed"],
            "network_reads_occurred": result["network_reads_occurred"],
            "network_writes_occurred": result["network_writes_occurred"],
            "real_proof_executed": result["real_proof_executed"],
        }
        return {
            **result_core,
            "bootstrap_record_hash": sha256_payload(result_core),
        }


__all__ = [
    "BOOTSTRAP_VERSION", "BootstrapOperationError", "BootstrapSafetyError",
    "CONFIRMATION_FLAG", "DEFAULT_DATABASE",
    "EXPECTED_MIGRATION_CATALOG", "LATEST_MIGRATION", "DatabaseInspection",
    "Phase8BOperatorBootstrap", "PostgresAdminTarget", "verify_local_migration_files",
]
