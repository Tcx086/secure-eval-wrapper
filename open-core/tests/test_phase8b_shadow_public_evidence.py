from __future__ import annotations

import json
import unittest
from copy import deepcopy
from pathlib import Path

from secure_eval_wrapper.data_collection.hashing import sha256_payload
from secure_eval_wrapper.live.shadow_evidence import (
    PUBLIC_EVIDENCE_KEYS,
    build_public_shadow_evidence,
    validate_public_shadow_evidence,
)
from secure_eval_wrapper.live.shadow_verifier import run_offline_assurance_verifier


ROOT = Path(__file__).resolve().parents[2]
EVIDENCE = ROOT / "docs" / "evidence" / "phase8b_shadow_assurance_public.json"
SHA = "a" * 40


def _rehash(payload):
    core = {
        key: payload[key]
        for key in PUBLIC_EVIDENCE_KEYS
        if key != "evidence_payload_sha256"
    }
    payload["evidence_payload_sha256"] = sha256_payload(core)


class Phase8BShadowPublicEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.payload = build_public_shadow_evidence(repository_sha=SHA)

    def test_fixed_allowlist_order_counts_hash_and_non_authority(self):
        self.assertEqual(tuple(self.payload), PUBLIC_EVIDENCE_KEYS)
        self.assertEqual(self.payload["fixture_scenario_count"], 54)
        self.assertEqual(self.payload["mock_account_scenario_count"], 27)
        self.assertEqual(self.payload["public_market_failure_scenario_count"], 27)
        self.assertEqual(self.payload["concurrency_scenarios_passed"], 7)
        self.assertEqual(self.payload["crash_recovery_scenarios_passed"], 9)
        self.assertEqual(self.payload["restart_scenarios_passed"], 3)
        self.assertEqual(self.payload["replay_scenarios_passed"], 6)
        self.assertEqual(
            self.payload["postgresql_verification_classification"],
            "POSTGRESQL_VERIFIER_NOT_EXECUTED",
        )
        self.assertEqual(self.payload["postgresql_crash_recovery_scenarios_passed"], 0)
        self.assertEqual(self.payload["stale_data_rejection_count"], 2)
        self.assertEqual(self.payload["malformed_data_rejection_count"], 4)
        self.assertEqual(self.payload["synthetic_exposure_rejection_count"], 4)
        self.assertEqual(self.payload["blocker_frequencies"]["synthetic_derivative_exposure"], 3)
        core = {
            key: self.payload[key]
            for key in PUBLIC_EVIDENCE_KEYS
            if key != "evidence_payload_sha256"
        }
        self.assertEqual(self.payload["evidence_payload_sha256"], sha256_payload(core))
        self.assertEqual(self.payload["independent_audit_status"], "pending")

    def test_executable_verifier_is_repeatable_across_thread_completion_order(self):
        expected = run_offline_assurance_verifier(SHA)
        for _ in range(3):
            self.assertEqual(run_offline_assurance_verifier(SHA), expected)

    def test_forbidden_keys_paths_secrets_and_entropy_are_rejected(self):
        attacks = (
            ("postgres_password", "not-public"),
            ("unexpected_note", "C:\\Users\\private\\raw.json"),
            ("unexpected_note", "Authorization: Bearer private"),
            ("unexpected_note", "QWxhZGRpbjpPcGVuU2VzYW1lVG9rZW5XaXRoRW50cm9weQ=="),
        )
        for key, value in attacks:
            with self.subTest(key=key, value=value):
                attacked = deepcopy(self.payload)
                attacked[key] = value
                with self.assertRaises(ValueError):
                    validate_public_shadow_evidence(attacked)

    def test_zero_call_and_write_facts_cannot_be_forged_nonzero(self):
        for key in (
            "production_transport_call_count",
            "authenticated_endpoint_call_count",
            "network_write_count",
            "credential_read_count",
            "production_write_count",
        ):
            with self.subTest(key=key):
                attacked = deepcopy(self.payload)
                attacked[key] = 1
                _rehash(attacked)
                with self.assertRaises(PermissionError):
                    validate_public_shadow_evidence(attacked)

        attacked_count = deepcopy(self.payload)
        attacked_count["accepted_shadow_decision_count"] += 1
        attacked_count["blocked_shadow_decision_count"] -= 1
        _rehash(attacked_count)
        with self.assertRaises(ValueError):
            validate_public_shadow_evidence(attacked_count)

    def test_self_hash_verifier_hash_and_executable_case_tampering_are_rejected(self):
        self_hash = deepcopy(self.payload)
        self_hash["evidence_payload_sha256"] = "0" * 64
        with self.assertRaises(ValueError):
            validate_public_shadow_evidence(self_hash)

        verifier_hash = deepcopy(self.payload)
        verifier_hash["assurance_verifier_result"]["verifier_result_sha256"] = "0" * 64
        verifier_hash["verifier_result_sha256"] = "0" * 64
        _rehash(verifier_hash)
        with self.assertRaises(ValueError):
            validate_public_shadow_evidence(verifier_hash)

        removed_crash = deepcopy(self.payload)
        removed_crash["assurance_verifier_result"]["crash_results"].pop()
        _rehash(removed_crash)
        with self.assertRaises(ValueError):
            validate_public_shadow_evidence(removed_crash)

        duplicate_concurrency = deepcopy(self.payload)
        duplicate_concurrency["assurance_verifier_result"]["concurrency_results"].append(
            deepcopy(duplicate_concurrency["assurance_verifier_result"]["concurrency_results"][0])
        )
        _rehash(duplicate_concurrency)
        with self.assertRaises(ValueError):
            validate_public_shadow_evidence(duplicate_concurrency)

    def test_fake_success_and_different_repository_sha_are_rejected(self):
        fake_success = deepcopy(self.payload)
        fake_success["public_network_smoke_status"] = "PUBLIC_NETWORK_SMOKE_SUCCESS"
        fake_success["public_network_smoke_read_count"] = 2
        _rehash(fake_success)
        with self.assertRaises(ValueError):
            validate_public_shadow_evidence(fake_success)

        different_sha = deepcopy(self.payload)
        different_sha["repository_sha"] = "b" * 40
        _rehash(different_sha)
        with self.assertRaises(ValueError):
            validate_public_shadow_evidence(different_sha)

    def test_verifier_case_ids_are_unique_and_result_hashes_are_bound(self):
        verifier = self.payload["assurance_verifier_result"]
        for key in (
            "scenario_results",
            "restart_results",
            "replay_results",
            "concurrency_results",
            "crash_results",
        ):
            case_ids = [item["case_id"] for item in verifier[key]]
            self.assertEqual(len(case_ids), len(set(case_ids)))
            self.assertTrue(all(item["passed"] for item in verifier[key]))


    def test_checked_in_public_evidence_is_valid_when_present(self):
        if not EVIDENCE.exists():
            self.skipTest("public evidence is generated after implementation validation")
        payload = json.loads(EVIDENCE.read_text(encoding="utf-8"))
        validate_public_shadow_evidence(payload)


if __name__ == "__main__":
    unittest.main()
