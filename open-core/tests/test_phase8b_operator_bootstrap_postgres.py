from __future__ import annotations

import os
import unittest
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import datetime, timezone
from unittest.mock import patch
from uuid import uuid4

from secure_eval_wrapper.data_collection.hashing import sha256_payload
from secure_eval_wrapper.live.bootstrap import (
    BootstrapOperationError,
    BootstrapSafetyError,
    EXPECTED_MIGRATION_CATALOG,
    LATEST_MIGRATION,
    Phase8BOperatorBootstrap,
    PostgresAdminTarget,
    derive_bootstrap_record_hash,
)
from secure_eval_wrapper.live.configuration import (
    phase8a_dry_run_configuration,
    phase8b_authenticated_readonly_configuration,
)
from secure_eval_wrapper.live.durable_repository import (
    DurablePostgresLiveRepository,
    LiveConflictError,
    _public_payload,
)
from secure_eval_wrapper.live.endpoints import endpoint_catalog_hash
from secure_eval_wrapper.live.identity import RuntimeRepositoryIdentity
from secure_eval_wrapper.live.models import live_uuid
from secure_eval_wrapper.live.provider_identity import (
    OKX_PRODUCTION_SPOT_ADAPTER_IMPLEMENTATION_HASH,
)


RUN = os.environ.get("RUN_POSTGRES_INTEGRATION", "").lower() == "true"
SHA = "a" * 40
FINGERPRINT = "1234567890abcdef"


@unittest.skipUnless(RUN, "requires real PostgreSQL 16")
class Phase8BOperatorBootstrapPostgresTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import psycopg

        cls.psycopg = psycopg
        cls.password = os.environ["POSTGRES_PASSWORD"]
        suffix = uuid4().hex[:10]
        cls.database = "sew_phase8b_bootstrap_" + suffix
        cls.partial_database = "sew_phase8b_partial_" + suffix
        cls.concurrent_database = "sew_phase8b_concurrent_" + suffix
        cls.oid_database = "sew_phase8b_oid_" + suffix
        cls.database_name_policy = lambda name: name.startswith("sew_phase8b_")
        cls.target = PostgresAdminTarget(
            database=cls.database,
            host=os.environ["POSTGRES_HOST"],
            port=int(os.environ["POSTGRES_PORT"]),
            admin_database="postgres",
            admin_user=os.environ["POSTGRES_USER"],
            sslmode=os.environ.get("POSTGRES_SSLMODE", "disable"),
            database_name_policy=cls.database_name_policy,
        )

        def connector(**kwargs):
            kwargs["password"] = cls.password
            return psycopg.connect(**kwargs)

        cls.connector = staticmethod(connector)
        cls.service = Phase8BOperatorBootstrap(
            cls.target,
            connector=connector,
            identity_resolver=lambda: RuntimeRepositoryIdentity(SHA, "git_checkout"),
            clock=lambda: datetime(2026, 7, 15, tzinfo=timezone.utc),
        )
        plan = cls.service.plan(
            expected_reviewed_sha=SHA,
            account_fingerprint=FINGERPRINT,
            instrument="BTC-USDT",
        )
        cls.initial_plan = plan
        cls.initialization_result = cls.service.initialize(
            expected_reviewed_sha=SHA,
            account_fingerprint=FINGERPRINT,
            instrument="BTC-USDT",
            previous_plan_hash=plan["plan_hash"],
            confirm_readonly_bootstrap=True,
        )
        cls.verification_result = cls.service.verify(
            expected_reviewed_sha=SHA,
            account_fingerprint=FINGERPRINT,
            instrument="BTC-USDT",
        )

    @classmethod
    def tearDownClass(cls):
        connection = cls.connector(
            **cls.target.connection_kwargs("postgres", read_only=False)
        )
        try:
            connection.autocommit = True
            with connection.cursor() as cursor:
                for database in (
                    cls.database,
                    cls.partial_database,
                    cls.concurrent_database,
                    cls.oid_database,
                ):
                    if not database.startswith("sew_phase8b_"):
                        raise AssertionError("refusing to clean non-test database")
                    cursor.execute(
                        "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                        "WHERE datname=%s AND pid<>pg_backend_pid()",
                        (database,),
                    )
                    from psycopg import sql
                    cursor.execute(sql.SQL("DROP DATABASE IF EXISTS {}").format(sql.Identifier(database)))
        finally:
            connection.close()

    def setUp(self):
        self.connection = self.connector(
            **self.target.connection_kwargs(self.database, read_only=False)
        )
        with self.connection.cursor() as cursor:
            cursor.execute("TRUNCATE execution.live_configuration_snapshots CASCADE")
        self.connection.commit()
        self.repository = DurablePostgresLiveRepository(self.connection)
        self.configuration = phase8b_authenticated_readonly_configuration(
            FINGERPRINT, "BTC-USDT"
        )

    def tearDown(self):
        self.connection.close()

    def persist(self, configuration=None, **kwargs):
        return self.repository.persist_guarded_live_configuration_snapshot(
            configuration=self.configuration if configuration is None else configuration,
            created_at_utc=datetime(2026, 7, 15, tzinfo=timezone.utc),
            **kwargs,
        )

    def count(self):
        with self.connection.cursor() as cursor:
            cursor.execute("SELECT count(*) FROM execution.live_configuration_snapshots")
            return cursor.fetchone()[0]

    def test_clean_dedicated_initialization_reached_exact_0026_and_ready(self):
        result = self.initialization_result
        self.assertTrue(self.initial_plan["database_creation_required"])
        self.assertEqual(self.initial_plan["migration_count_to_apply"], 26)
        self.assertEqual(result["migration_count"], 26)
        self.assertEqual(result["latest_migration"], LATEST_MIGRATION)
        self.assertTrue(result["immutable_catalog_verified"])
        self.assertTrue(result["migration_0026_installed"])
        self.assertFalse(result["credentials_accessed"])
        self.assertFalse(result["network_reads_occurred"])
        self.assertFalse(result["network_writes_occurred"])
        self.assertFalse(result["real_proof_executed"])
        self.assertTrue({
            "target_host", "target_port", "target_database", "admin_database",
            "admin_user", "postgres_current_user", "postgres_system_identifier",
            "postgres_server_version", "target_database_oid", "database_exists",
            "database_identity_sha256", "observed_repository_sha",
            "expected_reviewed_sha", "migration_count", "latest_migration",
            "immutable_catalog_verified", "migration_0026_installed",
            "configuration_snapshot_id", "configuration_hash", "account_fingerprint",
            "instrument", "credential_policy", "endpoint_catalog_hash",
            "adapter_implementation_hash", "dry_run", "read_only_preflight",
            "production_write_enabled", "credentials_accessed", "network_reads_occurred",
            "network_writes_occurred", "real_proof_executed", "bootstrap_record_hash",
        } <= set(result))
        result_core = {
            key: value for key, value in result.items() if key != "bootstrap_record_hash"
        }
        self.assertEqual(
            result["bootstrap_record_hash"], derive_bootstrap_record_hash(result_core)
        )
        self.assertNotEqual(result["bootstrap_record_hash"], result["configuration_hash"])
        verification = self.verification_result
        self.assertTrue(verification["ready_for_operator_authorization"])
        self.assertTrue(verification["migration_hashes_verified"])
        self.assertTrue(verification["phase8_tables_verified"])
        self.assertTrue(verification["phase8_indexes_verified"])
        self.assertTrue(verification["phase8_triggers_verified"])
        self.assertEqual(verification["production_write_count"], 0)
        verification_core = {
            key: value for key, value in verification.items()
            if key != "bootstrap_record_hash"
        }
        self.assertEqual(
            verification["bootstrap_record_hash"],
            derive_bootstrap_record_hash(verification_core),
        )
        self.assertNotEqual(
            verification["bootstrap_record_hash"], verification["configuration_hash"]
        )
        repeated = self.service.verify(
            expected_reviewed_sha=SHA,
            account_fingerprint=FINGERPRINT,
            instrument="BTC-USDT",
        )
        self.assertEqual(
            verification["bootstrap_record_hash"], repeated["bootstrap_record_hash"]
        )
        self.persist()

    def test_configuration_persistence_is_atomic_idempotent_and_exactly_reloadable(self):
        expected_id = live_uuid(
            "configuration", {"hash": self.configuration.configuration_hash}
        )
        self.assertEqual(self.persist(), expected_id)
        self.assertEqual(self.persist(), expected_id)
        self.assertEqual(self.count(), 1)
        self.assertEqual(
            self.repository.load_guarded_live_configuration(
                self.configuration.configuration_hash
            ),
            self.configuration,
        )

    def test_failures_before_and_after_insert_roll_back(self):
        for fail_at in ("before_insert", "after_insert"):
            with self.subTest(fail_at=fail_at):
                with self.connection.cursor() as cursor:
                    cursor.execute("TRUNCATE execution.live_configuration_snapshots CASCADE")
                self.connection.commit()
                with self.assertRaises(RuntimeError):
                    self.persist(fail_at=fail_at)
                self.connection.rollback()
                self.assertEqual(self.count(), 0)

    def test_schema_contract_is_verified_before_configuration_insert(self):
        plan_result = self.service.plan(
            expected_reviewed_sha=SHA,
            account_fingerprint=FINGERPRINT,
            instrument="BTC-USDT",
        )
        incomplete_contract = {
            "phase8_tables_verified": True,
            "phase8_indexes_verified": False,
            "phase8_triggers_verified": True,
        }
        with patch.object(
            self.service, "_schema_contract", return_value=incomplete_contract
        ):
            with self.assertRaises(BootstrapOperationError) as raised:
                self.service.initialize(
                    expected_reviewed_sha=SHA,
                    account_fingerprint=FINGERPRINT,
                    instrument="BTC-USDT",
                    previous_plan_hash=plan_result["plan_hash"],
                    confirm_readonly_bootstrap=True,
                )
        self.assertEqual(raised.exception.last_completed_stage, "migrations_ready")
        self.assertEqual(self.count(), 0)

    def test_stale_hashes_and_nonfactory_overrides_fail_before_insert(self):
        for attacked in (
            replace(self.configuration, endpoint_catalog_hash="f" * 64),
            replace(self.configuration, provider_implementation_hash="f" * 64),
        ):
            with self.subTest(hash=attacked.configuration_hash), self.assertRaises(
                (PermissionError, ValueError)
            ):
                self.persist(attacked)
        with self.assertRaises(ValueError):
            replace(self.configuration, production_write_enabled=True)
        self.assertEqual(self.count(), 0)

    def test_existing_conflicting_configuration_is_never_overwritten(self):
        conflicting = phase8a_dry_run_configuration(
            account_fingerprint=FINGERPRINT,
            endpoint_catalog_hash=endpoint_catalog_hash(),
            provider_implementation_hash=OKX_PRODUCTION_SPOT_ADAPTER_IMPLEMENTATION_HASH,
        )
        payload = {
            name: getattr(conflicting, name)
            for name in conflicting.__dataclass_fields__
        }
        with self.connection.cursor() as cursor:
            cursor.execute(
                "INSERT INTO execution.live_configuration_snapshots ("
                "configuration_snapshot_id,configuration_sha256,provider,environment,"
                "account_fingerprint,dry_run,read_only_preflight,production_write_enabled,"
                "configuration_jsonb,record_sha256,created_at_utc) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                (
                    live_uuid("configuration", {"hash": conflicting.configuration_hash}),
                    conflicting.configuration_hash, conflicting.provider,
                    conflicting.environment, conflicting.account_fingerprint,
                    conflicting.dry_run, conflicting.read_only_preflight,
                    conflicting.production_write_enabled, _public_payload(payload),
                    sha256_payload(payload), datetime(2026, 7, 15, tzinfo=timezone.utc),
                ),
            )
        self.connection.commit()
        with self.assertRaises(LiveConflictError):
            self.persist()
        self.connection.rollback()
        self.assertEqual(self.count(), 1)

    def test_concurrent_exact_replay_serializes_to_one_row(self):
        def worker():
            connection = self.connector(
                **self.target.connection_kwargs(self.database, read_only=False)
            )
            try:
                repository = DurablePostgresLiveRepository(connection)
                return repository.persist_guarded_live_configuration_snapshot(
                    configuration=self.configuration,
                    created_at_utc=datetime(2026, 7, 15, tzinfo=timezone.utc),
                )
            finally:
                connection.close()

        with ThreadPoolExecutor(max_workers=2) as executor:
            results = tuple(executor.map(lambda _: worker(), range(2)))
        self.assertEqual(results[0], results[1])
        self.assertEqual(self.count(), 1)

    def test_concurrent_two_valid_fingerprints_enforce_global_singleton(self):
        configuration_b = phase8b_authenticated_readonly_configuration(
            "abcdef1234567890", "BTC-USDT"
        )

        def worker(configuration):
            connection = self.connector(
                **self.target.connection_kwargs(self.database, read_only=False)
            )
            try:
                repository = DurablePostgresLiveRepository(connection)
                try:
                    repository.persist_guarded_live_configuration_snapshot(
                        configuration=configuration,
                        created_at_utc=datetime(2026, 7, 15, tzinfo=timezone.utc),
                    )
                except Exception as exc:
                    return "error", type(exc).__name__, configuration.account_fingerprint
                return "ok", "persisted", configuration.account_fingerprint
            finally:
                connection.close()

        with ThreadPoolExecutor(max_workers=2) as executor:
            outcomes = tuple(executor.map(worker, (self.configuration, configuration_b)))
        self.assertEqual([outcome[0] for outcome in outcomes].count("ok"), 1)
        self.assertEqual([outcome[0] for outcome in outcomes].count("error"), 1)
        self.assertEqual(
            [outcome[1] for outcome in outcomes if outcome[0] == "error"],
            ["LiveConflictError"],
        )
        self.assertEqual(self.count(), 1)
        winner = next(outcome[2] for outcome in outcomes if outcome[0] == "ok")
        verification = self.service.verify(
            expected_reviewed_sha=SHA,
            account_fingerprint=winner,
            instrument="BTC-USDT",
        )
        self.assertTrue(verification["ready_for_operator_authorization"])
        self.assertEqual(verification["production_write_count"], 0)

    def test_two_full_initialize_attempts_serialize_entire_operation(self):
        target = replace(self.target, database=self.concurrent_database)
        service = Phase8BOperatorBootstrap(
            target,
            connector=self.connector,
            identity_resolver=lambda: RuntimeRepositoryIdentity(SHA, "git_checkout"),
            clock=lambda: datetime(2026, 7, 15, tzinfo=timezone.utc),
        )
        plan = service.plan(
            expected_reviewed_sha=SHA,
            account_fingerprint=FINGERPRINT,
            instrument="BTC-USDT",
        )
        self.assertFalse(plan["database_exists"])

        def worker():
            try:
                result = service.initialize(
                    expected_reviewed_sha=SHA,
                    account_fingerprint=FINGERPRINT,
                    instrument="BTC-USDT",
                    previous_plan_hash=plan["plan_hash"],
                    confirm_readonly_bootstrap=True,
                )
            except Exception as exc:
                return "error", type(exc).__name__
            return "ok", result["bootstrap_record_hash"]

        with ThreadPoolExecutor(max_workers=2) as executor:
            outcomes = tuple(executor.map(lambda _: worker(), range(2)))
        self.assertEqual([outcome[0] for outcome in outcomes].count("ok"), 1)
        self.assertEqual([outcome[0] for outcome in outcomes].count("error"), 1)
        self.assertEqual(
            [outcome[1] for outcome in outcomes if outcome[0] == "error"],
            ["BootstrapOperationError"],
        )
        verification = service.verify(
            expected_reviewed_sha=SHA,
            account_fingerprint=FINGERPRINT,
            instrument="BTC-USDT",
        )
        self.assertTrue(verification["ready_for_operator_authorization"])
        self.assertEqual(verification["production_write_count"], 0)

    def test_target_oid_change_after_plan_is_rejected(self):
        target = replace(self.target, database=self.oid_database)
        service = Phase8BOperatorBootstrap(
            target,
            connector=self.connector,
            identity_resolver=lambda: RuntimeRepositoryIdentity(SHA, "git_checkout"),
        )
        admin = self.connector(**target.connection_kwargs("postgres", read_only=False))
        try:
            admin.autocommit = True
            from psycopg import sql
            with admin.cursor() as cursor:
                cursor.execute(
                    sql.SQL("CREATE DATABASE {}").format(sql.Identifier(self.oid_database))
                )
        finally:
            admin.close()
        plan = service.plan(
            expected_reviewed_sha=SHA,
            account_fingerprint=FINGERPRINT,
            instrument="BTC-USDT",
        )
        original_oid = plan["target_database_oid"]
        admin = self.connector(**target.connection_kwargs("postgres", read_only=False))
        try:
            admin.autocommit = True
            from psycopg import sql
            with admin.cursor() as cursor:
                cursor.execute(
                    sql.SQL("DROP DATABASE {}").format(sql.Identifier(self.oid_database))
                )
                cursor.execute(
                    sql.SQL("CREATE DATABASE {}").format(sql.Identifier(self.oid_database))
                )
        finally:
            admin.close()
        changed = service.plan(
            expected_reviewed_sha=SHA,
            account_fingerprint=FINGERPRINT,
            instrument="BTC-USDT",
        )
        self.assertNotEqual(original_oid, changed["target_database_oid"])
        with self.assertRaises(BootstrapOperationError):
            service.initialize(
                expected_reviewed_sha=SHA,
                account_fingerprint=FINGERPRINT,
                instrument="BTC-USDT",
                previous_plan_hash=plan["plan_hash"],
                confirm_readonly_bootstrap=True,
            )

    def test_unexpected_non_internal_trigger_is_rejected(self):
        with self.connection.cursor() as cursor:
            cursor.execute(
                "CREATE TRIGGER phase8b_unexpected_trigger BEFORE UPDATE ON "
                "execution.live_configuration_snapshots FOR EACH ROW EXECUTE FUNCTION "
                "execution.prevent_live_authority_mutation()"
            )
        self.connection.commit()
        try:
            plan = self.service.plan(
                expected_reviewed_sha=SHA,
                account_fingerprint=FINGERPRINT,
                instrument="BTC-USDT",
            )
            self.assertIn("non_internal_trigger_catalog_mismatch", plan["blockers"])
        finally:
            with self.connection.cursor() as cursor:
                cursor.execute(
                    "DROP TRIGGER phase8b_unexpected_trigger ON "
                    "execution.live_configuration_snapshots"
                )
            self.connection.commit()
    def test_altered_database_migration_hash_is_rejected_and_never_overwritten(self):
        migration_id = next(iter(EXPECTED_MIGRATION_CATALOG))
        expected_hash = EXPECTED_MIGRATION_CATALOG[migration_id]
        with self.connection.cursor() as cursor:
            cursor.execute(
                "UPDATE audit.schema_migrations SET sha256=%s WHERE migration_id=%s",
                ("f" * 64, migration_id),
            )
        self.connection.commit()
        try:
            plan_result = self.service.plan(
                expected_reviewed_sha=SHA,
                account_fingerprint=FINGERPRINT,
                instrument="BTC-USDT",
            )
            self.assertEqual(plan_result["catalog_state"], "hash_or_filename_mismatch")
            self.assertIn(
                "immutable_migration_catalog_mismatch", plan_result["blockers"]
            )
            self.assertEqual(self.count(), 0)
        finally:
            with self.connection.cursor() as cursor:
                cursor.execute(
                    "UPDATE audit.schema_migrations SET sha256=%s WHERE migration_id=%s",
                    (expected_hash, migration_id),
                )
            self.connection.commit()

    def test_unsafe_existing_application_rows_block_plan_without_overwrite(self):
        with self.connection.cursor() as cursor:
            cursor.execute("CREATE TABLE public.bootstrap_unsafe_test (value integer NOT NULL)")
            cursor.execute("INSERT INTO public.bootstrap_unsafe_test VALUES (1)")
        self.connection.commit()
        try:
            plan = self.service.plan(
                expected_reviewed_sha=SHA,
                account_fingerprint=FINGERPRINT,
                instrument="BTC-USDT",
            )
            self.assertIn("existing_database_contains_unsafe_application_rows", plan["blockers"])
            self.assertTrue(plan["configuration_insertion_required"])
            self.assertEqual(self.count(), 0)
        finally:
            with self.connection.cursor() as cursor:
                cursor.execute("DROP TABLE public.bootstrap_unsafe_test")
            self.connection.commit()
    def test_partial_catalog_and_existing_configuration_plan_fail_closed(self):
        partial_target = replace(self.target, database=self.partial_database)
        partial_service = Phase8BOperatorBootstrap(
            partial_target,
            connector=self.connector,
            identity_resolver=lambda: RuntimeRepositoryIdentity(SHA, "git_checkout"),
        )
        connection = self.connector(
            **partial_target.connection_kwargs("postgres", read_only=False)
        )
        try:
            connection.autocommit = True
            from psycopg import sql
            with connection.cursor() as cursor:
                cursor.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(self.partial_database)))
        finally:
            connection.close()
        connection = self.connector(
            **partial_target.connection_kwargs(self.partial_database, read_only=False)
        )
        try:
            with connection.cursor() as cursor:
                cursor.execute("CREATE SCHEMA audit")
                cursor.execute(
                    "CREATE TABLE audit.schema_migrations (migration_id text primary key,"
                    "filename text not null unique,sha256 char(64) not null,"
                    "applied_at_utc timestamptz not null default now(),description text not null)"
                )
                cursor.execute(
                    "INSERT INTO audit.schema_migrations (migration_id,filename,sha256,description) "
                    "VALUES ('0001_initial_schema','0001_initial_schema.sql',%s,'partial test')",
                    ("598486e6af2eed4559564593adc0b66deff9e21ea91dbda560980c208a2950c5",),
                )
            connection.commit()
        finally:
            connection.close()
        partial = partial_service.plan(
            expected_reviewed_sha=SHA,
            account_fingerprint=FINGERPRINT,
            instrument="BTC-USDT",
        )
        self.assertEqual(partial["catalog_state"], "partial_catalog")
        self.assertIn("partial_migration_catalog_is_never_auto_upgraded", partial["blockers"])

        self.persist()
        existing = self.service.plan(
            expected_reviewed_sha=SHA,
            account_fingerprint="abcdef1234567890",
            instrument="BTC-USDT",
        )
        self.assertIn("existing_guarded_live_configuration_conflicts", existing["blockers"])
        self.assertEqual(self.count(), 1)


if __name__ == "__main__":
    unittest.main()
