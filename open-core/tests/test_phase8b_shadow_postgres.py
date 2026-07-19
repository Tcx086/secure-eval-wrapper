from __future__ import annotations

import json
import os
import subprocess
import sys
import unittest
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from pathlib import Path
from uuid import UUID, uuid4

from secure_eval_wrapper.live.identity import RuntimeRepositoryIdentity
from secure_eval_wrapper.live.shadow_repository import (
    PostgresShadowRepository,
    ShadowInjectedCrash,
    ShadowPersistenceConflict,
    ShadowPostCommitCrash,
    validate_shadow_database_name,
)
from secure_eval_wrapper.live.shadow_runtime import (
    RUNTIME_CRASH_POINTS,
    FixtureShadowMarketSource,
    ShadowAssuranceRuntime,
)
from secure_eval_wrapper.live.shadow_scenarios import ShadowScenarioSpec, scenario_by_id


RUN = os.environ.get("RUN_POSTGRES_INTEGRATION", "").lower() == "true"
ROOT = Path(__file__).resolve().parents[2]
MIGRATOR = ROOT / "open-core" / "scripts" / "apply_postgres_migrations.py"
REPOSITORY_SHA = "a" * 40


def _modified_market():
    base = scenario_by_id("clean_flat_account")
    market = deepcopy(dict(base.market_payload))
    market["last_price"] = "50001"
    return ShadowScenarioSpec(
        "postgres_modified_market",
        base.category,
        dict(base.account_payload),
        market,
        dict(base.request_payload),
        "accepted",
        (),
        1,
    )


@unittest.skipUnless(RUN, "requires real PostgreSQL 16")
class Phase8BShadowPostgresTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import psycopg
        from psycopg import sql

        suffix = uuid4().hex[:8]
        cls.databases = {
            "primary": validate_shadow_database_name(
                f"secure_eval_phase8b_shadow_primary_{suffix}"
            ),
            "restart": validate_shadow_database_name(
                f"secure_eval_phase8b_shadow_restart_{suffix}"
            ),
            "concurrent": validate_shadow_database_name(
                f"secure_eval_phase8b_shadow_concurrent_{suffix}"
            ),
        }
        cls.base = {
            "host": os.environ["POSTGRES_HOST"],
            "port": int(os.environ["POSTGRES_PORT"]),
            "user": os.environ["POSTGRES_USER"],
            "password": os.environ["POSTGRES_PASSWORD"],
            "sslmode": os.environ.get("POSTGRES_SSLMODE", "disable"),
        }
        subprocess.run(
            [
                sys.executable,
                str(MIGRATOR),
                "--database",
                cls.databases["primary"],
                "--create-database",
            ],
            cwd=ROOT,
            env=os.environ.copy(),
            check=True,
            capture_output=True,
            text=True,
        )
        admin = psycopg.connect(**cls.base, dbname="postgres", autocommit=True)
        try:
            with admin.cursor() as cursor:
                for key in ("restart", "concurrent"):
                    cursor.execute(
                        sql.SQL("CREATE DATABASE {} TEMPLATE {}").format(
                            sql.Identifier(cls.databases[key]),
                            sql.Identifier(cls.databases["primary"]),
                        )
                    )
        finally:
            admin.close()

    @classmethod
    def tearDownClass(cls):
        import psycopg
        from psycopg import sql

        admin = psycopg.connect(**cls.base, dbname="postgres", autocommit=True)
        try:
            with admin.cursor() as cursor:
                for name in cls.databases.values():
                    validate_shadow_database_name(name)
                    cursor.execute(
                        "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                        "WHERE datname=%s AND pid<>pg_backend_pid()",
                        (name,),
                    )
                    cursor.execute(
                        sql.SQL("DROP DATABASE IF EXISTS {}").format(sql.Identifier(name))
                    )
        finally:
            admin.close()

    @classmethod
    def connect(cls, key):
        import psycopg
        from psycopg.rows import dict_row

        return psycopg.connect(
            **cls.base, dbname=cls.databases[key], row_factory=dict_row
        )

    @classmethod
    def service(cls, key):
        connection = cls.connect(key)
        repository = PostgresShadowRepository(
            connection,
            expected_database=cls.databases[key],
            expected_host=cls.base["host"],
        )
        runtime = ShadowAssuranceRuntime(
            repository=repository,
            market_source=FixtureShadowMarketSource(),
            identity_resolver=lambda: RuntimeRepositoryIdentity(
                REPOSITORY_SHA, "git_checkout"
            ),
        )
        return connection, runtime

    def setUp(self):
        for key in self.databases:
            connection = self.connect(key)
            try:
                with connection.cursor() as cursor:
                    cursor.execute(
                        "DELETE FROM audit.run_manifests "
                        "WHERE storage_ref='phase8b_shadow_assurance'"
                    )
                connection.commit()
            finally:
                connection.close()

    def test_exact_catalog_initial_restart_replay_and_fresh_process_inspect(self):
        run_id = UUID("00000000-0000-5000-8000-000000008d01")
        connection, service = self.service("primary")
        initial = service.run_fixture("clean_flat_account", shadow_run_id=run_id)
        connection.close()

        env = os.environ.copy()
        env["PGPASSWORD"] = self.base["password"]
        command = [
            sys.executable,
            "-m",
            "secure_eval_wrapper.live.shadow_cli",
            "inspect",
            "--run-id",
            str(run_id),
            "--postgres-database",
            self.databases["primary"],
            "--postgres-host",
            self.base["host"],
            "--postgres-port",
            str(self.base["port"]),
            "--postgres-user",
            self.base["user"],
            "--postgres-sslmode",
            self.base["sslmode"],
        ]
        completed = subprocess.run(
            command, cwd=ROOT, env=env, capture_output=True, text=True, check=True
        )
        inspected = json.loads(completed.stdout)
        self.assertEqual(inspected["input_hash"], initial.input_hash)
        self.assertEqual(inspected["decision_hash"], initial.decision_hash)
        self.assertEqual(inspected["manifest_hash"], initial.manifest_hash)
        self.assertEqual(len(inspected["configuration_hash"]), 64)
        self.assertEqual(len(inspected["market_snapshot_hash"]), 64)
        self.assertEqual(len(inspected["synthetic_account_snapshot_hash"]), 64)
        self.assertEqual(len(inspected["bundle_hash"]), 64)
        self.assertEqual(inspected["shadow_intent_count"], 1)
        self.assertEqual(inspected["persistence_result"], "loaded_complete")

        connection, restarted = self.service("primary")
        replay = restarted.run_fixture("clean_flat_account", shadow_run_id=run_id)
        self.assertTrue(replay.replayed)
        self.assertEqual(replay.decision_hash, initial.decision_hash)
        self.assertEqual(
            restarted.repository.row_counts()["audit.run_manifests"], 1
        )
        connection.close()

    def test_modified_input_preserves_parent_and_old_authoritative_evidence(self):
        connection, service = self.service("restart")
        base = service.run_fixture(
            "clean_flat_account",
            shadow_run_id=UUID("00000000-0000-5000-8000-000000008d02"),
        )
        changed = service._run_fixture_scenario_for_test(
            _modified_market(),
            shadow_run_id=UUID("00000000-0000-5000-8000-000000008d03"),
            parent_input_hash=base.input_hash,
        )
        connection.close()

        connection, restarted = self.service("restart")
        old = restarted.repository.load_bundle(base.shadow_run_id)
        new = restarted.repository.load_bundle(changed.shadow_run_id)
        self.assertNotEqual(old["decision"]["input_hash"], new["decision"]["input_hash"])
        self.assertNotEqual(old["decision"]["decision_hash"], new["decision"]["decision_hash"])
        self.assertEqual(new["decision"]["parent_input_hash"], base.input_hash)
        self.assertEqual(restarted.repository.row_counts()["audit.run_manifests"], 2)
        connection.close()

    def test_all_nine_crash_points_rollback_or_restart_complete(self):
        post_commit = "after_transaction_commit_before_response"
        for index, crash_point in enumerate(sorted(RUNTIME_CRASH_POINTS), start=1):
            with self.subTest(crash_point=crash_point):
                run_id = UUID(f"00000000-0000-5000-8000-000000008e{index:02d}")
                connection, service = self.service("restart")
                exception = ShadowPostCommitCrash if crash_point == post_commit else ShadowInjectedCrash
                with self.assertRaises(exception):
                    service.run_fixture(
                        "clean_flat_account", shadow_run_id=run_id, crash_at=crash_point
                    )
                connection.close()

                connection, recovered_service = self.service("restart")
                bundle = recovered_service.repository.load_bundle(run_id)
                if crash_point == post_commit:
                    self.assertEqual(bundle["status"], "complete")
                else:
                    self.assertIsNone(bundle)
                recovered = recovered_service.run_fixture(
                    "clean_flat_account", shadow_run_id=run_id
                )
                self.assertEqual(recovered.replayed, crash_point == post_commit)
                complete = recovered_service.repository.load_bundle(run_id)
                self.assertEqual(complete["status"], "complete")
                connection.close()

    def test_real_two_connection_concurrency_is_idempotent_or_conflicting(self):
        base = scenario_by_id("clean_flat_account")
        different = _modified_market()

        def execute(scenario, run_id):
            connection, service = self.service("concurrent")
            try:
                try:
                    summary = service._run_fixture_scenario_for_test(scenario, shadow_run_id=run_id)
                    return "replay" if summary.replayed else "persisted"
                except ShadowPersistenceConflict:
                    return "conflict"
            finally:
                connection.close()

        identical_id = UUID("00000000-0000-5000-8000-000000008f01")
        with ThreadPoolExecutor(max_workers=2) as pool:
            identical = tuple(pool.map(
                lambda _: execute(base, identical_id), range(2)
            ))
        self.assertCountEqual(identical, ("persisted", "replay"))

        conflict_id = UUID("00000000-0000-5000-8000-000000008f02")
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = (
                pool.submit(execute, base, conflict_id),
                pool.submit(execute, different, conflict_id),
            )
            conflict = tuple(item.result() for item in futures)
        self.assertEqual(conflict.count("persisted"), 1)
        self.assertEqual(conflict.count("conflict"), 1)

        with ThreadPoolExecutor(max_workers=2) as pool:
            different_runs = tuple(pool.map(
                lambda index: execute(
                    base,
                    UUID(f"00000000-0000-5000-8000-000000008f0{index}"),
                ),
                (3, 4),
            ))
        self.assertEqual(different_runs, ("persisted", "persisted"))

        connection, service = self.service("concurrent")
        self.assertEqual(service.repository.row_counts()["audit.run_manifests"], 4)
        bundle = service.repository.load_bundle(conflict_id)
        self.assertEqual(bundle["status"], "complete")
        self.assertEqual(bundle["decision"]["safety_facts"]["production_write_count"], 0)
        connection.close()

    def test_catalog_attacks_fail_before_persistence_and_are_rolled_back(self):
        attacks = (
            (
                "old_hash",
                "UPDATE audit.schema_migrations SET sha256=%s WHERE migration_id=%s",
                ("0" * 64, "0001_initial_schema"),
            ),
            (
                "filename",
                "UPDATE audit.schema_migrations SET filename=%s WHERE migration_id=%s",
                ("0001_old_name.sql", "0001_initial_schema"),
            ),
            (
                "same_count_unknown_id",
                "UPDATE audit.schema_migrations SET migration_id=%s WHERE migration_id=%s",
                ("0099_unknown", "0001_initial_schema"),
            ),
            (
                "partial",
                "DELETE FROM audit.schema_migrations WHERE migration_id=%s",
                ("0026_phase8b_authenticated_readonly_preflight",),
            ),
            (
                "0027",
                "INSERT INTO audit.schema_migrations "
                "(migration_id,filename,sha256,description) VALUES (%s,%s,%s,%s)",
                ("0027_attack", "0027_attack.sql", "f" * 64, "attack"),
            ),
        )
        for name, statement, parameters in attacks:
            with self.subTest(name=name):
                connection = self.connect("primary")
                try:
                    with connection.cursor() as cursor:
                        cursor.execute(statement, parameters)
                    with self.assertRaises(PermissionError):
                        PostgresShadowRepository(
                            connection,
                            expected_database=self.databases["primary"],
                            expected_host=self.base["host"],
                        )
                    with connection.cursor() as cursor:
                        cursor.execute(
                            "SELECT count(*) FROM audit.schema_migrations"
                        )
                        self.assertEqual(cursor.fetchone()["count"], 26)
                        cursor.execute(
                            "SELECT count(*) FROM audit.run_manifests "
                            "WHERE storage_ref='phase8b_shadow_assurance'"
                        )
                        self.assertEqual(cursor.fetchone()["count"], 0)
                finally:
                    connection.close()

    def test_non_shadow_application_row_contamination_fails_and_is_not_preserved(self):
        connection = self.connect("primary")
        try:
            with connection.cursor() as cursor:
                cursor.execute("CREATE TABLE public.phase8b_attack_marker (id integer PRIMARY KEY)")
                cursor.execute("INSERT INTO public.phase8b_attack_marker VALUES (1)")
            with self.assertRaises(PermissionError):
                PostgresShadowRepository(
                    connection,
                    expected_database=self.databases["primary"],
                    expected_host=self.base["host"],
                )
            with connection.cursor() as cursor:
                cursor.execute("SELECT to_regclass('public.phase8b_attack_marker')")
                self.assertIsNone(cursor.fetchone()["to_regclass"])
                cursor.execute(
                    "SELECT count(*) FROM audit.run_manifests "
                    "WHERE storage_ref='phase8b_shadow_assurance'"
                )
                self.assertEqual(cursor.fetchone()["count"], 0)
        finally:
            connection.close()

if __name__ == "__main__":
    unittest.main()
