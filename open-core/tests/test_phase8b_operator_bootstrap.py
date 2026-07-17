from __future__ import annotations

import contextlib
import inspect
import io
import json
import socket
import subprocess
import sys
import tempfile
import unittest
from dataclasses import fields, replace
from pathlib import Path
from unittest.mock import patch

from secure_eval_wrapper.data_collection.hashing import sha256_payload
from secure_eval_wrapper.live.bootstrap import (
    BootstrapOperationError,
    BootstrapSafetyError,
    DatabaseInspection,
    DatabaseReference,
    EXPECTED_MIGRATION_CATALOG,
    Phase8BOperatorBootstrap,
    PostgresAdminTarget,
    _expected_database_objects,
    derive_bootstrap_record_hash,
    verify_local_migration_files,
)
from secure_eval_wrapper.live.bootstrap_cli import build_parser, main
from secure_eval_wrapper.live.configuration import (
    phase8b_authenticated_readonly_configuration,
)
from secure_eval_wrapper.live.identity import RuntimeRepositoryIdentity
from secure_eval_wrapper.live.provider_identity import (
    OKX_PRODUCTION_SPOT_ADAPTER_IMPLEMENTATION_HASH,
)


SHA = "a" * 40
FINGERPRINT = "1234567890abcdef"


def _reference(
    target: PostgresAdminTarget,
    *,
    system_identifier: str = "7612345678901234567",
    database_exists: bool = False,
    database_oid: int | None = None,
) -> DatabaseReference:
    core = {
        "target_host": target.host,
        "target_port": target.port,
        "target_database": target.database,
        "admin_database": target.admin_database,
        "admin_user": target.admin_user,
        "postgres_current_user": target.admin_user,
        "postgres_system_identifier": system_identifier,
        "postgres_server_version": "16.9",
        "database_exists": database_exists,
        "target_database_oid": database_oid,
    }
    return DatabaseReference(**core, database_identity_sha256=sha256_payload(core))


class _PlanBootstrap(Phase8BOperatorBootstrap):
    def __init__(self, target: PostgresAdminTarget, reference: DatabaseReference):
        super().__init__(
            target,
            connector=lambda **kwargs: (_ for _ in ()).throw(
                AssertionError("database connection attempted")
            ),
            identity_resolver=lambda: RuntimeRepositoryIdentity(SHA, "git_checkout"),
        )
        self.reference = reference

    def _database_reference(self, connection=None):
        return self.reference

    def inspect(self, *, expected_configuration=None, reference=None, admin_connection=None):
        reference = reference or self.reference
        return DatabaseInspection(
            reference=reference,
            database_exists=reference.database_exists,
            database_oid=reference.target_database_oid,
            database_identity_sha256=reference.database_identity_sha256,
            catalog_state="absent",
            catalog=(),
            latest_migration=None,
            application_row_count=0,
            configuration_row_count=0,
            production_write_count=0,
            non_system_object_count=0,
            non_system_object_kinds=(),
            blockers=(),
        )


class _FakeCliBootstrap:
    calls = []

    def __init__(self, target):
        self.target = target

    def inspect_public(self):
        self.calls.append(("inspect", self.target.database))
        return {
            "action": "inspect", "credentials_accessed": False,
            "network_reads_occurred": False, "network_writes_occurred": False,
            "real_proof_executed": False,
        }

    def plan(self, **kwargs):
        self.calls.append(("plan", kwargs))
        return {
            "action": "plan", "plan_hash": "b" * 64, "credentials_accessed": False,
            "network_reads_occurred": False, "network_writes_occurred": False,
            "real_proof_executed": False,
        }

    def verify(self, **kwargs):
        self.calls.append(("verify", kwargs))
        return {
            "action": "verify", "ready_for_operator_authorization": False,
            "credentials_accessed": False, "network_reads_occurred": False,
            "network_writes_occurred": False, "real_proof_executed": False,
        }

    def initialize(self, **kwargs):
        self.calls.append(("initialize", kwargs))
        return {
            "action": "initialize", "ready_for_operator_authorization": True,
            "credentials_accessed": False, "network_reads_occurred": False,
            "network_writes_occurred": False, "real_proof_executed": False,
        }


class _ObjectOnlyBootstrap(Phase8BOperatorBootstrap):
    class Connection:
        def close(self):
            pass

    def __init__(self, kind: str, reference: DatabaseReference):
        super().__init__(
            PostgresAdminTarget(),
            connector=lambda **kwargs: self.Connection(),
            identity_resolver=lambda: RuntimeRepositoryIdentity(SHA, "git_checkout"),
        )
        self.kind = kind
        self.reference = reference

    def _verify_target_connection_identity(self, connection, reference):
        return None

    def _fetchone(self, connection, statement, params=()):
        if "to_regclass" in statement:
            return (False,)
        raise AssertionError(statement)

    def _inspect_database_objects(self, connection, *, exact_catalog):
        self.assert_not_exact(exact_catalog)
        return 1, (self.kind,), [], []

    @staticmethod
    def assert_not_exact(value):
        if value:
            raise AssertionError("uncatalogued database treated as exact")


class _InitializeHarness(Phase8BOperatorBootstrap):
    def __init__(self, plan, reference, *, fail_create=False):
        super().__init__(
            PostgresAdminTarget(),
            connector=lambda **kwargs: (_ for _ in ()).throw(
                AssertionError("unexpected database connection")
            ),
            identity_resolver=lambda: RuntimeRepositoryIdentity(SHA, "git_checkout"),
        )
        self.plan_result = plan
        self.reference = reference
        self.fail_create = fail_create
        self.created = False

    @contextlib.contextmanager
    def _locked_admin_connection(self):
        yield object()

    def plan(self, **kwargs):
        return self.plan_result

    def _database_reference(self, connection=None):
        return self.reference

    def _create_database(self, admin_connection):
        if self.fail_create:
            raise RuntimeError("injected create failure")
        self.created = True


class Phase8BOperatorBootstrapOfflineTests(unittest.TestCase):
    def test_factory_is_exact_readonly_spot_profile_with_current_hashes(self):
        configuration = phase8b_authenticated_readonly_configuration(FINGERPRINT, "BTC-USDT")
        from secure_eval_wrapper.live.venues.okx_live import OkxProductionSpotAdapter

        self.assertEqual(configuration.allowed_instruments, ("BTC-USDT",))
        self.assertEqual(configuration.allowed_instrument_types, ("spot",))
        self.assertEqual(configuration.allowed_settlement_assets, ("USDT",))
        self.assertEqual(configuration.base_currency, "USDT")
        self.assertEqual(configuration.allowed_order_types, ("limit",))
        self.assertEqual(configuration.credential_source_policy, ("environment",))
        self.assertEqual(
            configuration.provider_implementation_hash,
            OKX_PRODUCTION_SPOT_ADAPTER_IMPLEMENTATION_HASH,
        )
        self.assertEqual(
            configuration.provider_implementation_hash,
            OkxProductionSpotAdapter.provider_implementation_hash,
        )
        self.assertTrue(configuration.dry_run)
        self.assertTrue(configuration.read_only_preflight)
        self.assertFalse(configuration.production_write_enabled)
        self.assertFalse(configuration.automatic_flatten)
        self.assertFalse(configuration.allow_short)
        self.assertFalse(configuration.allow_perpetual)

    def test_factory_rejects_bad_fingerprint_and_every_nonexact_instrument(self):
        for fingerprint in ("", "0" * 16, "A" * 16, "short"):
            with self.subTest(fingerprint=fingerprint), self.assertRaises(ValueError):
                phase8b_authenticated_readonly_configuration(fingerprint, "BTC-USDT")
        for instrument in (
            "btc-usdt", "BTC-USDT-SWAP", "BTC-USDT-PERP", "ETH-USDT", "BTC/USDT", "",
        ):
            with self.subTest(instrument=instrument), self.assertRaises(ValueError):
                phase8b_authenticated_readonly_configuration(FINGERPRINT, instrument)

    def test_only_literal_local_hosts_are_accepted_before_connection(self):
        for host in ("localhost", "db.internal", "192.168.1.20", "[::1]", " 127.0.0.1"):
            with self.subTest(host=host), self.assertRaises(BootstrapSafetyError):
                PostgresAdminTarget(host=host)
        self.assertEqual(PostgresAdminTarget(host="127.0.0.1").host, "127.0.0.1")
        self.assertEqual(PostgresAdminTarget(host="::1").host, "::1")

    def test_dedicated_database_and_maintenance_database_policy(self):
        for database in ("secure_eval_wrapper", "postgres", "template0", "template1"):
            with self.subTest(database=database), self.assertRaises(BootstrapSafetyError):
                PostgresAdminTarget(database=database)
        with self.assertRaises(BootstrapSafetyError):
            PostgresAdminTarget(database="arbitrary_database")
        with self.assertRaises(BootstrapSafetyError):
            PostgresAdminTarget(admin_database="template1")
        with self.assertRaises(BootstrapSafetyError):
            PostgresAdminTarget(
                database="secure_eval_phase8b",
                admin_database="secure_eval_phase8b",
            )
        self.assertEqual(
            PostgresAdminTarget(database="secure_eval_phase8b_operator_01").database,
            "secure_eval_phase8b_operator_01",
        )

    def test_production_database_policy_is_fixed_and_not_constructor_replaceable(self):
        expected_fields = (
            "database", "host", "port", "admin_database", "admin_user", "sslmode",
        )
        self.assertEqual(
            tuple(inspect.signature(PostgresAdminTarget).parameters), expected_fields
        )
        self.assertEqual(
            tuple(item.name for item in fields(PostgresAdminTarget)), expected_fields
        )
        post_init_source = inspect.getsource(PostgresAdminTarget.__post_init__)
        self.assertIn(
            r"secure_eval_phase8b(?:_[a-z0-9][a-z0-9_]{0,42})?", post_init_source
        )
        self.assertNotIn("callable", post_init_source)
        with self.assertRaises(TypeError):
            PostgresAdminTarget(**{
                "database": "arbitrary_database",
                "database_name_policy": lambda _: True,
            })

    def test_every_nonproduction_database_name_is_rejected_before_connection(self):
        for database in (
            "secure_eval_wrapper", "postgres", "template0", "template1",
            "arbitrary_database", "sew_phase8b_test", "secure_eval_phase8",
            "secure_eval_phase8b-bad", "Secure_eval_phase8b", "secure_eval_phase8b test",
        ):
            with self.subTest(database=database), self.assertRaises(
                (BootstrapSafetyError, ValueError)
            ):
                PostgresAdminTarget(database=database)

    def test_cli_rejects_nonproduction_database_before_bootstrap_construction(self):
        output = io.StringIO()

        def fail_if_constructed(_target):
            raise AssertionError("bootstrap constructed")

        with contextlib.redirect_stdout(output):
            result = main(
                ["inspect", "--database", "arbitrary_database"],
                bootstrap_factory=fail_if_constructed,
            )
        self.assertEqual(result, 2)
        self.assertEqual(json.loads(output.getvalue())["status"], "failed_closed")

    def test_plan_hash_binds_all_public_connection_and_cluster_identity_fields(self):
        target = PostgresAdminTarget()
        reference = _reference(target)
        service = _PlanBootstrap(target, reference)
        first = service.plan(
            expected_reviewed_sha=SHA,
            account_fingerprint=FINGERPRINT,
            instrument="BTC-USDT",
        )
        for key, value in reference.public_fields().items():
            self.assertEqual(first[key], value)
        changed_cluster = _PlanBootstrap(
            target,
            _reference(target, system_identifier="7612345678901234568"),
        ).plan(
            expected_reviewed_sha=SHA,
            account_fingerprint=FINGERPRINT,
            instrument="BTC-USDT",
        )
        changed_user_target = replace(target, admin_user="phase8_admin")
        changed_user = _PlanBootstrap(
            changed_user_target,
            _reference(changed_user_target),
        ).plan(
            expected_reviewed_sha=SHA,
            account_fingerprint=FINGERPRINT,
            instrument="BTC-USDT",
        )
        self.assertNotEqual(first["plan_hash"], changed_cluster["plan_hash"])
        self.assertNotEqual(first["plan_hash"], changed_user["plan_hash"])

    def test_every_existing_uncatalogued_database_fails_closed(self):
        reference = _reference(
            PostgresAdminTarget(), database_exists=True, database_oid=16384
        )
        for kind in ("view", "function_or_procedure", "relation", "event_trigger"):
            with self.subTest(kind=kind):
                service = _ObjectOnlyBootstrap(kind, reference)
                state = service._inspect_existing_database(reference)
                self.assertEqual(state.catalog_state, "uncatalogued")
                self.assertEqual(state.non_system_object_kinds, (kind,))
                self.assertIn(
                    "existing_uncatalogued_database_is_never_initialized",
                    state.blockers,
                )

    def test_verify_record_hash_is_independent_complete_and_deterministic(self):
        configuration_hash = "c" * 64
        core = {
            "database_identity_sha256": "d" * 64,
            "postgres_system_identifier": "7612345678901234567",
            "target_database_oid": 16384,
            "observed_repository_sha": "a" * 40,
            "expected_reviewed_sha": "a" * 40,
            "migration_catalog": [{"migration_id": "0026", "sha256": "e" * 64}],
            "phase8_tables_verified": True,
            "phase8_indexes_verified": True,
            "phase8_triggers_verified": True,
            "configuration_hash": configuration_hash,
            "account_fingerprint": FINGERPRINT,
            "instrument": "BTC-USDT",
            "ready_for_operator_authorization": True,
            "production_write_count": 0,
            "blockers": [],
            "credentials_accessed": False,
            "network_reads_occurred": False,
            "network_writes_occurred": False,
            "real_proof_executed": False,
        }
        first = derive_bootstrap_record_hash(core)
        self.assertNotEqual(first, configuration_hash)
        self.assertEqual(first, derive_bootstrap_record_hash(core))
        for key, value in (
            ("database_identity_sha256", "f" * 64),
            ("observed_repository_sha", "b" * 40),
            ("ready_for_operator_authorization", False),
            ("blockers", ["not_ready"]),
        ):
            with self.subTest(key=key):
                changed = dict(core)
                changed[key] = value
                self.assertNotEqual(first, derive_bootstrap_record_hash(changed))

    def test_initialize_requires_confirmation_before_connection_or_mutation(self):
        service = Phase8BOperatorBootstrap(
            PostgresAdminTarget(),
            connector=lambda **kwargs: (_ for _ in ()).throw(
                AssertionError("connection attempted")
            ),
            identity_resolver=lambda: RuntimeRepositoryIdentity(SHA, "git_checkout"),
        )
        with self.assertRaises(BootstrapSafetyError):
            service.initialize(
                expected_reviewed_sha=SHA,
                account_fingerprint=FINGERPRINT,
                instrument="BTC-USDT",
                previous_plan_hash="b" * 64,
                confirm_readonly_bootstrap=False,
            )

    def test_initialize_failure_reports_exact_locked_stage(self):
        reference = _reference(PostgresAdminTarget())
        plan = {
            **reference.public_fields(),
            "plan_hash": "b" * 64,
            "blockers": [],
            "database_creation_required": True,
            "migrations_required": True,
        }
        service = _InitializeHarness(plan, reference, fail_create=True)
        with self.assertRaises(BootstrapOperationError) as raised:
            service.initialize(
                expected_reviewed_sha=SHA,
                account_fingerprint=FINGERPRINT,
                instrument="BTC-USDT",
                previous_plan_hash="b" * 64,
                confirm_readonly_bootstrap=True,
            )
        self.assertEqual(raised.exception.last_completed_stage, "plan_revalidated")
        self.assertEqual(str(raised.exception), "local_postgresql_operation_failed")

        class _FailingCliBootstrap(_FakeCliBootstrap):
            def initialize(self, **kwargs):
                raise BootstrapOperationError(
                    "public_safe_failure", last_completed_stage="schema_verified"
                )

        output = io.StringIO()
        argv = [
            "initialize", "--expected-reviewed-sha", SHA,
            "--account-fingerprint", FINGERPRINT, "--instrument", "BTC-USDT",
            "--plan-hash", "b" * 64, "--confirm-readonly-bootstrap",
        ]
        with contextlib.redirect_stdout(output):
            self.assertEqual(main(argv, bootstrap_factory=_FailingCliBootstrap), 2)
        payload = json.loads(output.getvalue())
        self.assertEqual(payload["last_completed_stage"], "schema_verified")
        self.assertEqual(payload["error"], "public_safe_failure")

    def test_wrong_plan_hash_and_plan_blocker_fail_before_mutation(self):
        reference = _reference(PostgresAdminTarget())
        base = {
            **reference.public_fields(),
            "plan_hash": "b" * 64,
            "blockers": [],
            "database_creation_required": True,
            "migrations_required": True,
        }
        wrong = _InitializeHarness(base, reference)
        with self.assertRaises(BootstrapSafetyError):
            wrong.initialize(
                expected_reviewed_sha=SHA,
                account_fingerprint=FINGERPRINT,
                instrument="BTC-USDT",
                previous_plan_hash="c" * 64,
                confirm_readonly_bootstrap=True,
            )
        self.assertFalse(wrong.created)
        blocked = _InitializeHarness(
            {**base, "blockers": ["partial_catalog"]}, reference
        )
        with self.assertRaises(BootstrapSafetyError):
            blocked.initialize(
                expected_reviewed_sha=SHA,
                account_fingerprint=FINGERPRINT,
                instrument="BTC-USDT",
                previous_plan_hash="b" * 64,
                confirm_readonly_bootstrap=True,
            )
        self.assertFalse(blocked.created)

    def test_wrong_repository_sha_fails_before_database_connection(self):
        service = Phase8BOperatorBootstrap(
            PostgresAdminTarget(),
            connector=lambda **kwargs: (_ for _ in ()).throw(
                AssertionError("database connection occurred")
            ),
            identity_resolver=lambda: RuntimeRepositoryIdentity(SHA, "git_checkout"),
        )
        with self.assertRaises(BootstrapSafetyError):
            service.plan(
                expected_reviewed_sha="b" * 40,
                account_fingerprint=FINGERPRINT,
                instrument="BTC-USDT",
            )

    def test_pinned_migration_and_expected_object_catalog(self):
        paths = verify_local_migration_files()
        self.assertEqual(len(paths), 26)
        self.assertEqual(
            EXPECTED_MIGRATION_CATALOG["0026_phase8b_authenticated_readonly_preflight"],
            "698772fb68c5c4981256682d064c3be641193ab10c8dbf55e1a5b390ca7c504a",
        )
        expected = _expected_database_objects(paths)
        self.assertIn(("execution", "live_configuration_snapshots"), expected.tables)
        self.assertIn(
            (
                "execution", "live_configuration_snapshots",
                "trg_live_configuration_snapshots_immutable",
                "execution", "prevent_live_authority_mutation",
            ),
            expected.triggers,
        )
        source = Path(__file__).resolve().parents[1] / "db" / "migrations"
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for path in source.glob("*.sql"):
                (root / path.name).write_bytes(path.read_bytes())
            target = root / "0026_phase8b_authenticated_readonly_preflight.sql"
            target.write_bytes(target.read_bytes() + b"\n-- altered")
            with self.assertRaises(BootstrapSafetyError):
                verify_local_migration_files(root)

    def test_cli_has_no_destructive_options_and_read_commands_are_socket_free(self):
        help_text = build_parser().format_help()
        self.assertNotIn("--yes", help_text)
        self.assertNotIn("drop", help_text.lower())
        self.assertNotIn("truncate", help_text.lower())
        _FakeCliBootstrap.calls.clear()
        common = [
            "--expected-reviewed-sha", SHA,
            "--account-fingerprint", FINGERPRINT,
            "--instrument", "BTC-USDT",
        ]
        for command in ("inspect", "plan", "verify"):
            argv = [command] if command == "inspect" else [command, *common]
            output = io.StringIO()
            with patch.object(socket, "socket", side_effect=AssertionError("socket used")):
                with contextlib.redirect_stdout(output):
                    self.assertEqual(main(argv, bootstrap_factory=_FakeCliBootstrap), 0)
            payload = json.loads(output.getvalue())
            rendered = json.dumps(payload).lower()
            self.assertFalse(payload["credentials_accessed"])
            self.assertFalse(payload["network_reads_occurred"])
            for forbidden in (
                "api_secret", "passphrase", "ok-access-key", "balance", "account_uid",
            ):
                self.assertNotIn(forbidden, rendered)

    def test_initialize_cli_passes_only_exact_confirmation_and_plan_hash(self):
        output = io.StringIO()
        argv = [
            "initialize", "--expected-reviewed-sha", SHA,
            "--account-fingerprint", FINGERPRINT, "--instrument", "BTC-USDT",
            "--plan-hash", "b" * 64, "--confirm-readonly-bootstrap",
        ]
        with contextlib.redirect_stdout(output):
            self.assertEqual(main(argv, bootstrap_factory=_FakeCliBootstrap), 0)
        call = _FakeCliBootstrap.calls[-1]
        self.assertEqual(call[0], "initialize")
        self.assertTrue(call[1]["confirm_readonly_bootstrap"])
        self.assertEqual(call[1]["previous_plan_hash"], "b" * 64)

    def test_bootstrap_import_does_not_import_okx_transport_or_open_a_socket(self):
        code = """
import socket
import sys
socket.create_connection = lambda *a, **k: (_ for _ in ()).throw(AssertionError("socket"))
from secure_eval_wrapper.data_collection.hashing import sha256_payload
from secure_eval_wrapper.live.configuration import phase8b_authenticated_readonly_configuration
from secure_eval_wrapper.live.durable_repository import DurablePostgresLiveRepository
configuration = phase8b_authenticated_readonly_configuration("1234567890abcdef", "BTC-USDT")
payload = {name: getattr(configuration, name) for name in configuration.__dataclass_fields__}
repository = DurablePostgresLiveRepository(None)
repository._fetchone = lambda statement, params=(): {
    "configuration_sha256": configuration.configuration_hash,
    "configuration_jsonb": payload,
    "record_sha256": sha256_payload(payload),
    "dry_run": True,
    "read_only_preflight": True,
    "production_write_enabled": False,
}
assert repository.load_guarded_live_configuration(configuration.configuration_hash) == configuration
assert "secure_eval_wrapper.live.venues.okx_live" not in sys.modules
assert "secure_eval_wrapper.live.credentials" not in sys.modules
"""
        completed = subprocess.run(
            [sys.executable, "-c", code], text=True, capture_output=True, timeout=30
        )
        self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)

    def test_stale_code_hash_overrides_are_rejected_by_typed_profile(self):
        configuration = phase8b_authenticated_readonly_configuration(FINGERPRINT, "BTC-USDT")
        self.assertNotEqual(
            replace(configuration, endpoint_catalog_hash="f" * 64), configuration
        )
        self.assertNotEqual(
            replace(configuration, provider_implementation_hash="f" * 64), configuration
        )


if __name__ == "__main__":
    unittest.main()
