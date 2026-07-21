from __future__ import annotations

import unittest
from copy import deepcopy
from uuid import UUID

from secure_eval_wrapper.data_collection.hashing import sha256_payload
from secure_eval_wrapper.live.shadow_bundle import (
    ShadowBundleValidationError,
    validate_shadow_bundle_payload,
    validate_shadow_manifest_row,
)
from secure_eval_wrapper.live.shadow_models import (
    ShadowDataProvenance,
    ShadowDecisionRecord,
    ShadowSafetyFacts,
)
from secure_eval_wrapper.live.shadow_repository import (
    MemoryShadowRepository,
    ShadowMemoryStore,
    ShadowPersistenceConflict,
)
from phase8b_shadow_test_support import TEST_REPOSITORY_SHA, runtime


_ENDPOINTS = (
    "GET /api/v5/public/instruments",
    "GET /api/v5/market/history-trades",
)


def _public_decision(
    run_id: UUID,
    *,
    classification: str = "public_network",
    network_read_count: int = 2,
    response_hashes: tuple[str, ...] = ("1" * 64, "2" * 64),
    source_instance_id: str = "3" * 64,
    failure_kind: str | None = None,
) -> ShadowDecisionRecord:
    provenance = ShadowDataProvenance(
        classification,
        _ENDPOINTS[:network_read_count],
        network_read_count,
        response_hashes,
        source_instance_id,
        "4" * 64,
        failure_kind,
    )
    return ShadowDecisionRecord(
        shadow_run_id=run_id,
        scenario_id=f"test_{classification}_{network_read_count}",
        input_hash="5" * 64,
        market_snapshot_hash=None,
        synthetic_account_snapshot_hash=None,
        configuration_hash="6" * 64,
        preflight_hash="7" * 64,
        approval_hash=None,
        manifest_hash=None,
        live_risk_decision_hash=None,
        accepted=False,
        blockers=("test_only_blocker",),
        shadow_intent=None,
        safety_facts=ShadowSafetyFacts(network_read_count),
        data_provenance=provenance,
        repository_commit_sha=TEST_REPOSITORY_SHA,
    )


def _fixture_bundle() -> tuple[dict[str, object], UUID]:
    store = ShadowMemoryStore()
    summary = runtime(store=store).run_fixture("clean_flat_account")
    return deepcopy(store.bundles[summary.shadow_run_id]), summary.shadow_run_id


def _row(bundle: dict[str, object], run_id: UUID) -> dict[str, object]:
    decision = bundle["decision"]
    return {
        "run_id": run_id,
        "run_mode": "simulation",
        "data_sha256": decision["input_hash"],
        "config_sha256": decision["configuration_hash"],
        "code_sha256": sha256_payload({
            "repository_commit_sha": decision["repository_commit_sha"]
        }),
        "artifact_sha256": bundle["bundle_hash"],
        "storage_ref": "phase8b_shadow_assurance",
        "manifest_jsonb": bundle,
    }


class Phase8BShadowBundleValidationTests(unittest.TestCase):
    def test_canonical_validator_rejects_authority_and_hash_chain_attacks(self):
        bundle, run_id = _fixture_bundle()
        attacks = {}

        forged_bundle = deepcopy(bundle)
        forged_bundle["bundle_hash"] = "0" * 64
        attacks["forged_bundle_hash"] = forged_bundle

        forged_decision = deepcopy(bundle)
        forged_decision["decision"]["decision_hash"] = "0" * 64
        attacks["forged_decision_hash"] = forged_decision

        forged_safety = deepcopy(bundle)
        forged_safety["decision"]["safety_facts_hash"] = "0" * 64
        attacks["forged_safety_facts_hash"] = forged_safety

        production_write = deepcopy(bundle)
        production_write["decision"]["safety_facts"]["production_write_count"] = 1
        attacks["nonzero_production_write_count"] = production_write

        safety_type_confusion = deepcopy(bundle)
        safety_type_confusion["decision"]["safety_facts"]["production_write_enabled"] = 0
        attacks["safety_boolean_type_confusion"] = safety_type_confusion

        intent_submit = deepcopy(bundle)
        intent_submit["decision"]["shadow_intent"]["submit_reachable"] = True
        attacks["intent_submit_reachable"] = intent_submit

        summary_mismatch = deepcopy(bundle)
        summary_mismatch["summary"]["accepted"] = not bundle["summary"]["accepted"]
        attacks["summary_decision_mismatch"] = summary_mismatch

        wrong_run = deepcopy(bundle)
        wrong_run["summary"]["shadow_run_id"] = str(
            UUID("00000000-0000-5000-8000-00000000ffff")
        )
        attacks["wrong_run_id"] = wrong_run

        missing = deepcopy(bundle)
        missing.pop("runtime_version")
        attacks["missing_field"] = missing

        extra = deepcopy(bundle)
        extra["authority_override"] = True
        attacks["extra_authority_field"] = extra

        preparing = deepcopy(bundle)
        preparing["status"] = "preparing"
        attacks["preparing_spoof"] = preparing

        malformed = []
        attacks["malformed_shape"] = malformed

        for name, attacked in attacks.items():
            with self.subTest(name=name), self.assertRaises(ShadowBundleValidationError):
                validate_shadow_bundle_payload(attacked)

        row_attacks = {}
        artifact = _row(bundle, run_id)
        artifact["artifact_sha256"] = "0" * 64
        row_attacks["artifact_sha256"] = artifact
        for column in ("data_sha256", "config_sha256", "code_sha256"):
            attacked = _row(bundle, run_id)
            attacked[column] = "0" * 64
            row_attacks[column] = attacked
        wrong_column_id = _row(bundle, run_id)
        wrong_column_id["run_id"] = UUID(
            "00000000-0000-5000-8000-00000000fffe"
        )
        row_attacks["run_id"] = wrong_column_id

        for name, attacked in row_attacks.items():
            with self.subTest(row_attack=name), self.assertRaises(ShadowBundleValidationError):
                validate_shadow_manifest_row(attacked)

    def test_memory_load_and_replay_revalidate_without_repairing_invalid_bundle(self):
        bundle, run_id = _fixture_bundle()
        bundle["decision"]["data_provenance"]["classification"] = "public_network"
        store = ShadowMemoryStore(bundles={run_id: bundle})
        repository = MemoryShadowRepository(store)

        with self.assertRaises(ShadowBundleValidationError):
            repository.load_bundle(run_id)
        self.assertEqual(repository.row_counts()["audit.run_manifests"], 1)
        self.assertEqual(
            store.bundles[run_id]["decision"]["data_provenance"]["classification"],
            "public_network",
        )

    def test_durable_public_success_and_failure_provenance_survives_restart(self):
        store = ShadowMemoryStore()
        repository = MemoryShadowRepository(store)
        cases = (
            (
                UUID("00000000-0000-5000-8000-00000000b001"),
                _public_decision(
                    UUID("00000000-0000-5000-8000-00000000b001")
                ),
                "public_network",
                2,
                2,
            ),
            (
                UUID("00000000-0000-5000-8000-00000000b002"),
                _public_decision(
                    UUID("00000000-0000-5000-8000-00000000b002"),
                    classification="unavailable",
                    network_read_count=1,
                    response_hashes=(),
                    failure_kind="timeout",
                ),
                "unavailable",
                1,
                0,
            ),
            (
                UUID("00000000-0000-5000-8000-00000000b003"),
                _public_decision(
                    UUID("00000000-0000-5000-8000-00000000b003"),
                    classification="unavailable",
                    network_read_count=2,
                    response_hashes=("1" * 64,),
                    failure_kind="connection_failure",
                ),
                "unavailable",
                2,
                1,
            ),
        )
        for run_id, decision, classification, reads, hash_count in cases:
            with self.subTest(run_id=run_id):
                self.assertFalse(repository.persist_bundle(decision))
                loaded = MemoryShadowRepository(store).load_bundle(run_id)
                provenance = loaded["decision"]["data_provenance"]
                self.assertEqual(provenance["classification"], classification)
                self.assertEqual(provenance["network_read_count"], reads)
                self.assertEqual(len(provenance["response_source_hashes"]), hash_count)
                self.assertEqual(
                    loaded["summary"]["data_provenance_hash"],
                    loaded["decision"]["data_provenance_hash"],
                )
                self.assertEqual(
                    loaded["summary"]["network_read_count"],
                    reads,
                )

    def test_same_run_different_provenance_conflicts_and_tampering_fails(self):
        run_id = UUID("00000000-0000-5000-8000-00000000b004")
        store = ShadowMemoryStore()
        repository = MemoryShadowRepository(store)
        first = _public_decision(run_id, source_instance_id="3" * 64)
        second = _public_decision(run_id, source_instance_id="8" * 64)
        repository.persist_bundle(first)
        with self.assertRaises(ShadowPersistenceConflict):
            repository.persist_bundle(second)
        self.assertEqual(repository.row_counts()["audit.run_manifests"], 1)

        for field, value in (
            ("classification", "fixture"),
            ("endpoint_identities", []),
            ("response_source_hashes", ["9" * 64, "2" * 64]),
        ):
            with self.subTest(field=field):
                attacked = deepcopy(store.bundles[run_id])
                attacked["decision"]["data_provenance"][field] = value
                with self.assertRaises(ShadowBundleValidationError):
                    validate_shadow_bundle_payload(attacked)

    def test_fixture_provenance_cannot_carry_public_fields(self):
        bundle, _ = _fixture_bundle()
        provenance = bundle["decision"]["data_provenance"]
        provenance["endpoint_identities"] = list(_ENDPOINTS)
        provenance["network_read_count"] = 2
        provenance["response_source_hashes"] = ["1" * 64, "2" * 64]
        provenance["source_instance_id"] = "3" * 64
        provenance["payload_hash"] = "4" * 64
        provenance["provenance_hash"] = "5" * 64
        with self.assertRaises(ShadowBundleValidationError):
            validate_shadow_bundle_payload(bundle)


if __name__ == "__main__":
    unittest.main()
