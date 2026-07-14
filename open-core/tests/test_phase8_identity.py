from __future__ import annotations

import json
import unittest
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import uuid4

from secure_eval_wrapper.live.authorities import LiveLocalProjection
from secure_eval_wrapper.live.configuration import phase8a_dry_run_configuration
from secure_eval_wrapper.live.credentials import (
    EnvironmentLiveCredentialProvider,
    InjectedLocalCredentialProvider,
    LiveCredentialMaterial,
)
from secure_eval_wrapper.live.endpoints import endpoint_catalog_hash
from secure_eval_wrapper.live.identity import (
    RepositoryIdentityError,
    build_repository_metadata_payload,
    collect_build_repository_metadata,
    derive_okx_account_fingerprint,
    resolve_runtime_repository_identity,
)
from secure_eval_wrapper.live.preflight import (
    OperationalPreflightError,
    collect_operational_preflight_evidence,
)
from secure_eval_wrapper.live.reconciliation import reconcile_live
from secure_eval_wrapper.live.venues.okx_live import OkxProductionSpotAdapter

from test_phase8_guarded_live import (
    ACCOUNT_FINGERPRINT,
    COMMIT,
    OKX_UID,
    T0,
    ExactOkxTransport,
    account,
    config,
    credential,
    exact_okx_bundle,
)


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
FORGED_SHA = "0" * 40


class NoDatabaseAccess:
    def cursor(self):
        raise AssertionError("identity rejection must happen before PostgreSQL access")


def collect_until_identity_guard(*, run, cfg, snapshot, reference, bundle, expected_sha=COMMIT):
    return collect_operational_preflight_evidence(
        connection=NoDatabaseAccess(), live_run_id=run, configuration=cfg,
        credential_reference=reference, account_snapshot=snapshot,
        market_evidence=None, reconciliation=None, kill_switch=None,
        okx_bundle=bundle, expected_repository_commit_sha=expected_sha,
        collected_at_utc=T0,
    )


class OkxAccountIdentityTests(unittest.TestCase):
    def test_exact_uid_has_one_canonical_deterministic_fingerprint(self):
        expected = derive_okx_account_fingerprint(OKX_UID)
        self.assertEqual(expected, ACCOUNT_FINGERPRINT)
        self.assertEqual(expected, derive_okx_account_fingerprint(OKX_UID))
        self.assertEqual(len(expected), 16)
        self.assertNotEqual(expected, derive_okx_account_fingerprint("different-okx-uid"))

    def test_account_config_requires_and_returns_exact_uid(self):
        payload = {"code": "0", "data": [{
            "uid": OKX_UID, "mainUid": OKX_UID, "perm": "read_only", "acctLv": "1",
            "posMode": "long_short_mode", "autoLoan": "false",
            "enableSpotBorrow": "false",
        }]}
        parsed = OkxProductionSpotAdapter.parse_account_config(payload)
        self.assertEqual(parsed["uid"], OKX_UID)
        self.assertEqual(parsed["account_fingerprint"], ACCOUNT_FINGERPRINT)
        for uid in (None, "", f" {OKX_UID}"):
            attacked = json.loads(json.dumps(payload))
            attacked["data"][0]["uid"] = uid
            with self.assertRaises(ValueError):
                OkxProductionSpotAdapter.parse_account_config(attacked)

    def test_response_uid_a_expected_fingerprint_b_rejects_after_identity_read(self):
        adapter = OkxProductionSpotAdapter(
            transport=ExactOkxTransport(uid="uid-a"),
            credential_material=LiveCredentialMaterial("key", "secret", "passphrase"),
            clock=lambda: T0,
        )
        with self.assertRaises(PermissionError):
            adapter.collect_read_observation_bundle(
                live_run_id=uuid4(), purpose="preflight", instrument="BTC-USDT",
                expected_account_fingerprint=derive_okx_account_fingerprint("uid-b"),
            )
        self.assertEqual(adapter.network_reads, 1)

    def test_credential_a_response_uid_b_rejects_before_database(self):
        run = uuid4(); uid_b = "uid-b"; fingerprint_b = derive_okx_account_fingerprint(uid_b)
        with self.assertRaises(OperationalPreflightError):
            collect_until_identity_guard(
                run=run, cfg=config(fingerprint=fingerprint_b),
                snapshot=account(run, fingerprint=fingerprint_b),
                reference=credential(fingerprint=derive_okx_account_fingerprint("uid-a")),
                bundle=exact_okx_bundle(run, "preflight", uid=uid_b),
            )

    def test_snapshot_a_response_uid_b_rejects_before_database(self):
        run = uuid4(); uid_b = "uid-b"; fingerprint_b = derive_okx_account_fingerprint(uid_b)
        with self.assertRaises(OperationalPreflightError):
            collect_until_identity_guard(
                run=run, cfg=config(fingerprint=fingerprint_b),
                snapshot=account(run, fingerprint=derive_okx_account_fingerprint("uid-a")),
                reference=credential(fingerprint=fingerprint_b),
                bundle=exact_okx_bundle(run, "preflight", uid=uid_b),
            )

    def test_configured_subaccount_must_be_proven_by_uid_main_uid(self):
        run = uuid4(); cfg = replace(config(), subaccount_fingerprint=ACCOUNT_FINGERPRINT)
        bundle = exact_okx_bundle(run, "preflight")
        with self.assertRaises(OperationalPreflightError):
            collect_until_identity_guard(
                run=run, cfg=cfg, snapshot=account(run), reference=credential(), bundle=bundle,
            )
        proven = exact_okx_bundle(
            run, "preflight", uid=OKX_UID, main_uid="main-account-uid",
            expected_subaccount_fingerprint=ACCOUNT_FINGERPRINT,
        )
        self.assertTrue(proven.envelope("account_config").normalized_payload["is_subaccount"])

    def test_placeholder_and_omitted_expected_identity_fail_closed(self):
        with self.assertRaises(TypeError):
            phase8a_dry_run_configuration(
                endpoint_catalog_hash=endpoint_catalog_hash(),
                provider_implementation_hash=OkxProductionSpotAdapter.provider_implementation_hash,
            )
        with self.assertRaises(ValueError):
            config(fingerprint="0000000000000000")
        with self.assertRaises(TypeError):
            EnvironmentLiveCredentialProvider()
        with self.assertRaises(TypeError):
            InjectedLocalCredentialProvider("key", "secret", "passphrase")
        with self.assertRaises(ValueError):
            EnvironmentLiveCredentialProvider(expected_account_fingerprint="0000000000000000")

    def test_cross_account_reconciliation_rejects_before_result_or_persistence(self):
        run = uuid4(); snapshot = account(run)
        local = LiveLocalProjection(
            run, snapshot.account_fingerprint, (), (), dict(snapshot.balances),
            dict(snapshot.positions), 1, T0, (snapshot.snapshot_id,),
        )
        bundle = exact_okx_bundle(run, "reconciliation", uid="other-okx-uid")
        with self.assertRaises(PermissionError):
            reconcile_live(
                local_projection=local, okx_bundle=bundle, evaluated_at_utc=T0,
                freshness_seconds=30, maximum_clock_skew_seconds=5,
            )


class RepositoryIdentityTests(unittest.TestCase):
    def resolve_without_metadata(self, *, environment=None):
        with TemporaryDirectory() as directory:
            return resolve_runtime_repository_identity(
                source_root=REPOSITORY_ROOT,
                build_metadata_path=Path(directory) / "missing.json",
                environment={} if environment is None else environment,
            )

    def test_git_checkout_resolves_actual_head_and_exact_format(self):
        identity = self.resolve_without_metadata()
        self.assertEqual(identity.observed_commit_sha, COMMIT)
        self.assertEqual(identity.identity_source, "git_checkout")
        self.assertRegex(identity.observed_commit_sha, r"^[0-9a-f]{40}$")

    def test_expected_reviewed_sha_mismatch_rejects_before_database(self):
        run = uuid4()
        with self.assertRaises(OperationalPreflightError):
            collect_until_identity_guard(
                run=run, cfg=config(), snapshot=account(run), reference=credential(),
                bundle=exact_okx_bundle(run, "preflight"), expected_sha=FORGED_SHA,
            )

    def test_forged_environment_sha_different_from_head_rejects(self):
        with self.assertRaises(RepositoryIdentityError):
            self.resolve_without_metadata(environment={"GITHUB_SHA": FORGED_SHA})

    def test_missing_git_and_build_metadata_fails_closed(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            with self.assertRaises(RepositoryIdentityError):
                resolve_runtime_repository_identity(
                    source_root=root, build_metadata_path=root / "missing.json", environment={},
                )

    def test_build_metadata_and_git_head_disagreement_rejects(self):
        with TemporaryDirectory() as directory:
            metadata = Path(directory) / "identity.json"
            metadata.write_text(json.dumps(build_repository_metadata_payload(FORGED_SHA)), encoding="utf-8")
            with self.assertRaises(RepositoryIdentityError):
                resolve_runtime_repository_identity(
                    source_root=REPOSITORY_ROOT, build_metadata_path=metadata, environment={},
                )

    def test_matching_build_metadata_has_priority(self):
        with TemporaryDirectory() as directory:
            metadata = Path(directory) / "identity.json"
            metadata.write_text(json.dumps(collect_build_repository_metadata(source_root=REPOSITORY_ROOT)), encoding="utf-8")
            identity = resolve_runtime_repository_identity(
                source_root=REPOSITORY_ROOT, build_metadata_path=metadata, environment={},
            )
        self.assertEqual(identity.observed_commit_sha, COMMIT)
        self.assertEqual(identity.identity_source, "build_metadata")

    def test_merge_ref_and_source_branch_ci_record_checked_out_head(self):
        environments = (
            {"GITHUB_SHA": COMMIT, "GITHUB_REF": "refs/pull/3/merge", "GITHUB_EVENT_NAME": "pull_request"},
            {"GITHUB_SHA": COMMIT, "GITHUB_REF": "refs/heads/codex/phase8-guarded-live-foundation", "GITHUB_EVENT_NAME": "push"},
        )
        for environment in environments:
            with self.subTest(ref=environment["GITHUB_REF"]):
                identity = self.resolve_without_metadata(environment=environment)
                self.assertEqual(identity.observed_commit_sha, COMMIT)
                self.assertEqual(identity.identity_source, "verified_ci")


if __name__ == "__main__":
    unittest.main()
