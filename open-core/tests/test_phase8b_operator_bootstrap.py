from __future__ import annotations

import contextlib
import io
import json
import socket
import subprocess
import sys
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from secure_eval_wrapper.live.bootstrap import (
    BootstrapSafetyError,
    EXPECTED_MIGRATION_CATALOG,
    Phase8BOperatorBootstrap,
    PostgresAdminTarget,
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


class _NoDatabaseBootstrap(Phase8BOperatorBootstrap):
    def __init__(self, plans):
        super().__init__(
            PostgresAdminTarget(),
            connector=lambda **kwargs: (_ for _ in ()).throw(AssertionError("database connection attempted")),
            identity_resolver=lambda: RuntimeRepositoryIdentity(SHA, "git_checkout"),
        )
        self.plans = list(plans)
        self.created = False
        self.migrated = False

    def plan(self, **kwargs):
        return self.plans.pop(0)

    def _create_database(self):
        self.created = True

    def _apply_all_migrations(self):
        self.migrated = True


class _FakeCliBootstrap:
    calls = []

    def __init__(self, target):
        self.target = target

    def inspect_public(self):
        self.calls.append(("inspect", self.target.database))
        return {"action": "inspect", "credentials_accessed": False, "network_reads_occurred": False, "network_writes_occurred": False, "real_proof_executed": False}

    def plan(self, **kwargs):
        self.calls.append(("plan", kwargs))
        return {"action": "plan", "plan_hash": "b" * 64, "credentials_accessed": False, "network_reads_occurred": False, "network_writes_occurred": False, "real_proof_executed": False}

    def verify(self, **kwargs):
        self.calls.append(("verify", kwargs))
        return {"action": "verify", "ready_for_operator_authorization": False, "credentials_accessed": False, "network_reads_occurred": False, "network_writes_occurred": False, "real_proof_executed": False}

    def initialize(self, **kwargs):
        self.calls.append(("initialize", kwargs))
        return {"action": "initialize", "ready_for_operator_authorization": True, "credentials_accessed": False, "network_reads_occurred": False, "network_writes_occurred": False, "real_proof_executed": False}


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
        for instrument in ("btc-usdt", "BTC-USDT-SWAP", "BTC-USDT-PERP", "ETH-USDT", "BTC/USDT", ""):
            with self.subTest(instrument=instrument), self.assertRaises(ValueError):
                phase8b_authenticated_readonly_configuration(FINGERPRINT, instrument)

    def test_forbidden_operator_database_and_no_destructive_cli_options(self):
        with self.assertRaises(BootstrapSafetyError):
            PostgresAdminTarget(database="secure_eval_wrapper")
        help_text = build_parser().format_help()
        self.assertNotIn("--yes", help_text)
        self.assertNotIn("drop", help_text.lower())
        self.assertNotIn("truncate", help_text.lower())
        with self.assertRaises(SystemExit):
            build_parser().parse_args(["initialize", "--yes"])

    def test_initialize_requires_confirmation_before_planning_or_mutation(self):
        service = _NoDatabaseBootstrap([])
        with self.assertRaises(BootstrapSafetyError):
            service.initialize(
                expected_reviewed_sha=SHA,
                account_fingerprint=FINGERPRINT,
                instrument="BTC-USDT",
                previous_plan_hash="b" * 64,
                confirm_readonly_bootstrap=False,
            )
        self.assertFalse(service.created)
        self.assertFalse(service.migrated)

    def test_wrong_plan_hash_and_plan_state_change_fail_before_mutation(self):
        base = {"plan_hash": "b" * 64, "blockers": [], "database_creation_required": True, "migrations_required": True, "database_identity_sha256": "d" * 64}
        wrong = _NoDatabaseBootstrap([base])
        with self.assertRaises(BootstrapSafetyError):
            wrong.initialize(
                expected_reviewed_sha=SHA,
                account_fingerprint=FINGERPRINT,
                instrument="BTC-USDT",
                previous_plan_hash="c" * 64,
                confirm_readonly_bootstrap=True,
            )
        self.assertFalse(wrong.created)
        changed = _NoDatabaseBootstrap([base, {**base, "database_creation_required": False}])
        with self.assertRaises(BootstrapSafetyError):
            changed.initialize(
                expected_reviewed_sha=SHA,
                account_fingerprint=FINGERPRINT,
                instrument="BTC-USDT",
                previous_plan_hash="b" * 64,
                confirm_readonly_bootstrap=True,
            )
        self.assertFalse(changed.created)
        identity_changed = _NoDatabaseBootstrap([base, base])
        identity_changed._database_reference = lambda: (False, None, "e" * 64)
        with self.assertRaises(BootstrapSafetyError):
            identity_changed.initialize(
                expected_reviewed_sha=SHA,
                account_fingerprint=FINGERPRINT,
                instrument="BTC-USDT",
                previous_plan_hash="b" * 64,
                confirm_readonly_bootstrap=True,
            )
        self.assertFalse(identity_changed.created)

    def test_plan_blocker_and_wrong_repository_sha_fail_before_database_connection(self):
        blocked = _NoDatabaseBootstrap([{"plan_hash": "b" * 64, "blockers": ["partial_catalog"]}])
        with self.assertRaises(BootstrapSafetyError):
            blocked.initialize(
                expected_reviewed_sha=SHA,
                account_fingerprint=FINGERPRINT,
                instrument="BTC-USDT",
                previous_plan_hash="b" * 64,
                confirm_readonly_bootstrap=True,
            )
        service = Phase8BOperatorBootstrap(
            PostgresAdminTarget(),
            connector=lambda **kwargs: (_ for _ in ()).throw(AssertionError("mutation or connection occurred")),
            identity_resolver=lambda: RuntimeRepositoryIdentity(SHA, "git_checkout"),
        )
        with self.assertRaises(BootstrapSafetyError):
            service.plan(
                expected_reviewed_sha="b" * 40,
                account_fingerprint=FINGERPRINT,
                instrument="BTC-USDT",
            )

    def test_pinned_migration_catalog_detects_altered_0026(self):
        self.assertEqual(len(verify_local_migration_files()), 26)
        self.assertEqual(
            EXPECTED_MIGRATION_CATALOG["0026_phase8b_authenticated_readonly_preflight"],
            "698772fb68c5c4981256682d064c3be641193ab10c8dbf55e1a5b390ca7c504a",
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

    def test_cli_read_commands_are_socket_free_and_public_safe_with_injected_service(self):
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
            for forbidden in ("api_secret", "passphrase", "ok-access-key", "balance", "account_uid"):
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
        self.assertNotEqual(replace(configuration, endpoint_catalog_hash="f" * 64), configuration)
        self.assertNotEqual(replace(configuration, provider_implementation_hash="f" * 64), configuration)


if __name__ == "__main__":
    unittest.main()
