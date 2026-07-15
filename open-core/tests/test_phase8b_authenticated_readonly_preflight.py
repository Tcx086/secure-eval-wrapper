from __future__ import annotations

import json
import os
import unittest
from dataclasses import replace
from datetime import timedelta
from unittest.mock import patch
from uuid import uuid4

from secure_eval_wrapper.live.credentials import InjectedLocalCredentialProvider
from secure_eval_wrapper.live.identity import RuntimeRepositoryIdentity
from secure_eval_wrapper.live.readonly_preflight import (
    AuthenticatedReadOnlyProof,
    run_authenticated_readonly_preflight,
)
from secure_eval_wrapper.live.venues.okx_live import OkxProductionSpotAdapter

from test_phase8_guarded_live import (
    ACCOUNT_FINGERPRINT,
    COMMIT,
    ExactOkxTransport,
    OKX_UID,
    T0,
    config,
)


class RecordingRepository:
    def __init__(self, configuration):
        self.configuration = configuration
        self.persisted = []
        self.storage_checks = 0

    def authenticated_readonly_storage_available(self):
        self.storage_checks += 1
        return True

    def load_guarded_live_configuration(self, configuration_hash):
        if configuration_hash != self.configuration.configuration_hash:
            raise LookupError("configuration not found")
        return self.configuration

    def persist_authenticated_readonly_proof(self, **kwargs):
        self.persisted.append(kwargs)
        return kwargs["proof"].proof_id


class CountingTransport(ExactOkxTransport):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.calls = []

    def execute(self, **kwargs):
        self.calls.append(kwargs["url"])
        return super().execute(**kwargs)


def local_configuration():
    return replace(config(), credential_source_policy=("injected_local",))


def provider():
    return InjectedLocalCredentialProvider(
        "placeholder-key",
        "placeholder-secret",
        "placeholder-passphrase",
        expected_account_fingerprint=ACCOUNT_FINGERPRINT,
    )


class AuthenticatedReadOnlyPreflightTests(unittest.TestCase):
    def setUp(self):
        self.ci_environment = patch.dict(
            os.environ,
            {
                "CI": "false",
                "GITHUB_ACTIONS": "false",
                "GITLAB_CI": "false",
                "TF_BUILD": "false",
                "JENKINS_URL": "",
                "BUILDKITE": "false",
                "CIRCLECI": "false",
            },
            clear=False,
        )
        self.ci_environment.start()
        self.addCleanup(self.ci_environment.stop)

    def run_proof(self, *, permission="read_only", uid=OKX_UID):
        configuration = local_configuration()
        repository = RecordingRepository(configuration)
        transport = CountingTransport(at=T0, uid=uid, perm=permission)
        adapter_box = []

        def factory(material):
            adapter = OkxProductionSpotAdapter(
                transport=transport, credential_material=material, clock=lambda: T0
            )
            adapter_box.append(adapter)
            return adapter

        proof = run_authenticated_readonly_preflight(
            repository=repository,
            proof_session_id=uuid4(),
            configuration_hash=configuration.configuration_hash,
            expected_account_fingerprint=ACCOUNT_FINGERPRINT,
            expected_reviewed_sha=COMMIT,
            instrument="BTC-USDT",
            credential_provider=provider(),
            adapter_factory=factory,
            identity_resolver=lambda: RuntimeRepositoryIdentity(COMMIT, "git_checkout"),
        )
        return proof, repository, transport, adapter_box[0]

    def assert_transport_rejected(self, transport, *, clock=T0):
        configuration = local_configuration()
        repository = RecordingRepository(configuration)
        adapter_box = []

        def factory(material):
            adapter = OkxProductionSpotAdapter(
                transport=transport, credential_material=material, clock=lambda: clock
            )
            adapter_box.append(adapter)
            return adapter

        with self.assertRaises((PermissionError, ValueError)):
            run_authenticated_readonly_preflight(
                repository=repository,
                proof_session_id=uuid4(),
                configuration_hash=configuration.configuration_hash,
                expected_account_fingerprint=ACCOUNT_FINGERPRINT,
                expected_reviewed_sha=COMMIT,
                instrument="BTC-USDT",
                credential_provider=provider(),
                adapter_factory=factory,
                identity_resolver=lambda: RuntimeRepositoryIdentity(COMMIT, "git_checkout"),
            )
        self.assertEqual(repository.persisted, [])
        self.assertEqual(adapter_box[0].network_writes, 0)
        return adapter_box[0]

    def test_fake_transport_exercises_exact_six_reads_and_public_safe_proof(self):
        proof, repository, transport, adapter = self.run_proof()
        self.assertEqual(proof.status, "fixture_passed")
        self.assertEqual(proof.preflight_mode, "AUTHENTICATED READ-ONLY")
        self.assertEqual(adapter.network_reads, 6)
        self.assertEqual(adapter.network_writes, 0)
        self.assertEqual(len(transport.calls), 6)
        self.assertEqual(len(repository.persisted), 1)
        payload = proof.public_payload()
        encoded = json.dumps(payload, sort_keys=True)
        for forbidden in (
            OKX_UID,
            "mainUid",
            "availEq",
            "frozenBal",
            "placeholder-key",
            "placeholder-secret",
            "placeholder-passphrase",
        ):
            self.assertNotIn(forbidden, encoded)
        self.assertEqual(payload["provider_implementation_hash"], repository.configuration.provider_implementation_hash)
        self.assertEqual(payload["endpoint_catalog_hash"], repository.configuration.endpoint_catalog_hash)
        self.assertEqual(payload["balance_currencies"], ("BTC", "USDT"))
        self.assertEqual(payload["position_count"], 0)
        self.assertEqual(payload["open_order_count"], 0)
        self.assertEqual(
            AuthenticatedReadOnlyProof.from_public_payload(payload).record_hash,
            proof.record_hash,
        )

    def test_public_proof_rejects_non_boolean_network_facts(self):
        proof, _, _, _ = self.run_proof()
        payload = proof.public_payload()
        payload["network_writes_occurred"] = "false"
        with self.assertRaises(TypeError):
            AuthenticatedReadOnlyProof.from_public_payload(payload)

    def test_trade_or_withdraw_permission_stops_after_account_config(self):
        for permission in ("trade", "withdraw", "read_only,trade", "read_only,withdraw"):
            with self.subTest(permission=permission):
                configuration = local_configuration()
                repository = RecordingRepository(configuration)
                transport = CountingTransport(at=T0, perm=permission)
                adapter_box = []

                def factory(material):
                    adapter = OkxProductionSpotAdapter(
                        transport=transport, credential_material=material, clock=lambda: T0
                    )
                    adapter_box.append(adapter)
                    return adapter

                with self.assertRaises(PermissionError):
                    run_authenticated_readonly_preflight(
                        repository=repository,
                        proof_session_id=uuid4(),
                        configuration_hash=configuration.configuration_hash,
                        expected_account_fingerprint=ACCOUNT_FINGERPRINT,
                        expected_reviewed_sha=COMMIT,
                        instrument="BTC-USDT",
                        credential_provider=provider(),
                        adapter_factory=factory,
                        identity_resolver=lambda: RuntimeRepositoryIdentity(COMMIT, "git_checkout"),
                    )
                self.assertEqual(adapter_box[0].network_reads, 1)
                self.assertEqual(adapter_box[0].network_writes, 0)
                self.assertEqual(repository.persisted, [])

    def test_cross_account_stops_after_account_config_and_persists_nothing(self):
        configuration = local_configuration()
        repository = RecordingRepository(configuration)
        transport = CountingTransport(at=T0, uid="other-public-test-account")
        adapter_box = []

        def factory(material):
            adapter = OkxProductionSpotAdapter(
                transport=transport, credential_material=material, clock=lambda: T0
            )
            adapter_box.append(adapter)
            return adapter

        with self.assertRaises(PermissionError):
            run_authenticated_readonly_preflight(
                repository=repository,
                proof_session_id=uuid4(),
                configuration_hash=configuration.configuration_hash,
                expected_account_fingerprint=ACCOUNT_FINGERPRINT,
                expected_reviewed_sha=COMMIT,
                instrument="BTC-USDT",
                credential_provider=provider(),
                adapter_factory=factory,
                identity_resolver=lambda: RuntimeRepositoryIdentity(COMMIT, "git_checkout"),
            )
        self.assertEqual(adapter_box[0].network_reads, 1)
        self.assertEqual(repository.persisted, [])

    def test_parser_error_leaves_bundle_incomplete_and_persists_no_proof(self):
        transport = CountingTransport(
            at=T0,
            overrides={"/api/v5/account/balance": {"code": "0", "data": [{"details": "invalid"}]}},
        )
        adapter = self.assert_transport_rejected(transport)
        self.assertEqual(adapter.network_reads, 6)

    def test_clock_skew_violation_persists_no_proof(self):
        transport = CountingTransport(at=T0)
        adapter = self.assert_transport_rejected(transport, clock=T0 + timedelta(seconds=6))
        self.assertEqual(adapter.network_reads, 6)

    def test_ci_rejects_before_postgresql_identity_credentials_or_transport(self):
        configuration = local_configuration()
        repository = RecordingRepository(configuration)
        credential_provider = provider()
        with patch.dict(os.environ, {"CI": "true"}, clear=False):
            with self.assertRaises(PermissionError):
                run_authenticated_readonly_preflight(
                    repository=repository,
                    proof_session_id=uuid4(),
                    configuration_hash=configuration.configuration_hash,
                    expected_account_fingerprint=ACCOUNT_FINGERPRINT,
                    expected_reviewed_sha=COMMIT,
                    instrument="BTC-USDT",
                    credential_provider=credential_provider,
                    adapter_factory=lambda material: self.fail("transport must not be created in CI"),
                    identity_resolver=lambda: self.fail("identity must not resolve in CI"),
                )
        self.assertEqual(repository.storage_checks, 0)
        self.assertEqual(credential_provider.load_count, 0)

    def test_cli_ci_gate_fails_before_postgresql_access(self):
        from secure_eval_wrapper.live import cli

        arguments = [
            "--live-run-id", str(uuid4()),
            "--configuration-hash", "0" * 64,
            "--read-only-network-preflight",
            "--credential-source", "environment",
            "--expected-account-fingerprint", ACCOUNT_FINGERPRINT,
            "--expected-reviewed-sha", COMMIT,
            "--instrument", "BTC-USDT",
        ]
        with (
            patch.dict(os.environ, {"CI": "true"}, clear=False),
            patch.object(cli, "_connect", side_effect=AssertionError("database socket opened")),
            patch.object(cli, "_print") as emit,
        ):
            result = cli.preflight_main(arguments)
        self.assertEqual(result, 2)
        self.assertIn("prohibited in CI", emit.call_args.args[0]["blockers"][0])

    def test_wrong_reviewed_sha_fails_before_credential_load(self):
        configuration = local_configuration()
        repository = RecordingRepository(configuration)
        credential_provider = provider()
        with self.assertRaises(PermissionError):
            run_authenticated_readonly_preflight(
                repository=repository,
                proof_session_id=uuid4(),
                configuration_hash=configuration.configuration_hash,
                expected_account_fingerprint=ACCOUNT_FINGERPRINT,
                expected_reviewed_sha="1" * 40,
                instrument="BTC-USDT",
                credential_provider=credential_provider,
                adapter_factory=lambda material: self.fail("transport must not be created"),
                identity_resolver=lambda: RuntimeRepositoryIdentity(COMMIT, "git_checkout"),
            )
        self.assertEqual(credential_provider.load_count, 0)

    def test_cli_without_explicit_network_flag_is_socket_free(self):
        from secure_eval_wrapper.live import cli

        with patch.object(cli, "_connect", side_effect=AssertionError("database socket opened")):
            result = cli.preflight_main(["--live-run-id", str(uuid4())])
        self.assertEqual(result, 2)

    def test_cli_requires_every_explicit_choice_before_database_access(self):
        from secure_eval_wrapper.live import cli

        with (
            patch.object(cli, "_connect", side_effect=AssertionError("database socket opened")),
            patch.object(cli, "_print") as emit,
        ):
            result = cli.preflight_main([
                "--live-run-id", str(uuid4()), "--read-only-network-preflight",
            ])
        self.assertEqual(result, 2)
        blocker = emit.call_args.args[0]["blockers"][0]
        for flag in (
            "--configuration-hash", "--credential-source", "--expected-account-fingerprint",
            "--expected-reviewed-sha", "--instrument",
        ):
            self.assertIn(flag, blocker)

    def test_cli_success_prints_complete_public_safe_operator_result(self):
        from secure_eval_wrapper.live import cli

        proof, _, _, _ = self.run_proof()
        proof = replace(
            proof,
            evidence_classification="operational_collector",
            status="passed",
            record_hash=None,
        )
        arguments = [
            "--live-run-id", str(proof.proof_session_id),
            "--configuration-hash", proof.configuration_hash,
            "--read-only-network-preflight",
            "--credential-source", "environment",
            "--expected-account-fingerprint", proof.account_fingerprint,
            "--expected-reviewed-sha", proof.expected_reviewed_sha,
            "--instrument", proof.instrument_id,
        ]
        with (
            patch.object(cli, "_connect") as connect,
            patch.object(cli, "DurablePostgresLiveRepository"),
            patch.object(cli, "run_authenticated_readonly_preflight", return_value=proof),
            patch.object(cli, "_print") as emit,
        ):
            connect.return_value.__enter__.return_value = object()
            result = cli.preflight_main(arguments)
        self.assertEqual(result, 0)
        payload = emit.call_args.args[0]
        self.assertEqual(payload["provider"], "okx")
        self.assertEqual(payload["environment"], "production")
        self.assertEqual(payload["queried_endpoint_count"], 6)
        self.assertTrue(payload["private_evidence_persisted"])
        self.assertEqual(payload["provider_permissions"], ("read_only",))
        self.assertFalse(payload["network_writes_occurred"])
        encoded = json.dumps(payload, sort_keys=True)
        for forbidden in ("placeholder-key", "placeholder-secret", "placeholder-passphrase", OKX_UID):
            self.assertNotIn(forbidden, encoded)


if __name__ == "__main__":
    unittest.main()