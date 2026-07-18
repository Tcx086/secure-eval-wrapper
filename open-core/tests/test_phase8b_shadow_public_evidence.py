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


ROOT = Path(__file__).resolve().parents[2]
EVIDENCE = ROOT / "docs" / "evidence" / "phase8b_shadow_assurance_public.json"
SHA = "a" * 40


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
            "credential_read_count",
            "production_write_count",
        ):
            with self.subTest(key=key):
                attacked = deepcopy(self.payload)
                attacked[key] = 1
                core = {
                    item: attacked[item]
                    for item in PUBLIC_EVIDENCE_KEYS
                    if item != "evidence_payload_sha256"
                }
                attacked["evidence_payload_sha256"] = sha256_payload(core)
                with self.assertRaises(PermissionError):
                    validate_public_shadow_evidence(attacked)

    def test_checked_in_public_evidence_is_valid_when_present(self):
        if not EVIDENCE.exists():
            self.skipTest("public evidence is generated after implementation validation")
        payload = json.loads(EVIDENCE.read_text(encoding="utf-8"))
        validate_public_shadow_evidence(payload)


if __name__ == "__main__":
    unittest.main()
