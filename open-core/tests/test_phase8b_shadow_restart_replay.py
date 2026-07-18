from __future__ import annotations

import unittest
from copy import deepcopy

from secure_eval_wrapper.live.shadow_repository import ShadowMemoryStore
from secure_eval_wrapper.live.shadow_scenarios import ShadowScenarioSpec, scenario_by_id
from phase8b_shadow_test_support import runtime


def modified(base, label, *, account=None, market=None, request=None):
    return ShadowScenarioSpec(
        f"modified_{label}",
        base.category,
        deepcopy(dict(base.account_payload)) if account is None else account,
        deepcopy(dict(base.market_payload)) if market is None else market,
        deepcopy(dict(base.request_payload)) if request is None else request,
        "accepted",
        (),
        1,
    )


class Phase8BShadowRestartReplayTests(unittest.TestCase):
    def test_fresh_repository_instance_reloads_exact_complete_bundle(self):
        store = ShadowMemoryStore()
        first_runtime = runtime(store=store)
        first = first_runtime.run_fixture("clean_flat_account")
        restarted = runtime(store=store)
        bundle = restarted.repository.load_bundle(first.shadow_run_id)
        self.assertEqual(bundle["decision"]["input_hash"], first.input_hash)
        self.assertEqual(bundle["decision"]["decision_hash"], first.decision_hash)
        self.assertEqual(bundle["decision"]["manifest_hash"], first.manifest_hash)
        self.assertEqual(tuple(bundle["decision"]["blockers"]), first.blockers)
        self.assertEqual(len(bundle["decision"]["configuration_hash"]), 64)
        self.assertEqual(len(bundle["decision"]["market_snapshot_hash"]), 64)
        self.assertEqual(len(bundle["decision"]["synthetic_account_snapshot_hash"]), 64)
        self.assertEqual(bundle["status"], "complete")
        self.assertEqual(bundle["summary"]["decision_hash"], first.decision_hash)

    def test_identical_replay_is_idempotent_and_hash_stable(self):
        store = ShadowMemoryStore()
        service = runtime(store=store)
        first = service.run_fixture("clean_flat_account")
        before = service.repository.load_bundle(first.shadow_run_id)
        replay = runtime(store=store).run_fixture("clean_flat_account")
        after = service.repository.load_bundle(first.shadow_run_id)
        self.assertTrue(replay.replayed)
        self.assertEqual(replay.input_hash, first.input_hash)
        self.assertEqual(replay.decision_hash, first.decision_hash)
        self.assertEqual(replay.manifest_hash, first.manifest_hash)
        self.assertEqual(replay.blockers, first.blockers)
        self.assertEqual(after["decision"]["shadow_intent"], before["decision"]["shadow_intent"])
        self.assertEqual(service.repository.row_counts()["audit.run_manifests"], 1)

    def test_five_single_field_modifications_preserve_lineage_and_old_evidence(self):
        base = scenario_by_id("clean_flat_account")
        store = ShadowMemoryStore()
        base_summary = runtime(store=store).run_scenario(base)
        variants = []

        market = deepcopy(dict(base.market_payload)); market["last_price"] = "50001"
        variants.append(modified(base, "price", market=market))
        market = deepcopy(dict(base.market_payload)); market["public_timestamp_utc"] = "2026-07-18T11:59:59+00:00"
        variants.append(modified(base, "timestamp", market=market))
        account = deepcopy(dict(base.account_payload)); account["balances"][0]["available"] = "9999"
        variants.append(modified(base, "balance", account=account))
        account = deepcopy(dict(base.account_payload)); account["kill_switch_active"] = True
        variants.append(modified(base, "kill", account=account))
        account = deepcopy(dict(base.account_payload)); account["pending_orders"] = [{"instrument": "BTC-USDT", "side": "buy", "quantity": "0.001", "reserved_notional": "50"}]
        variants.append(modified(base, "pending", account=account))

        for variant in variants:
            with self.subTest(variant=variant.scenario_id):
                summary = runtime(store=store).run_scenario(
                    variant,
                    parent_input_hash=base_summary.input_hash,
                )
                self.assertNotEqual(summary.input_hash, base_summary.input_hash)
                self.assertNotEqual(summary.decision_hash, base_summary.decision_hash)
                bundle = runtime(store=store).repository.load_bundle(summary.shadow_run_id)
                self.assertEqual(
                    bundle["decision"]["parent_input_hash"], base_summary.input_hash
                )
        self.assertIsNotNone(
            runtime(store=store).repository.load_bundle(base_summary.shadow_run_id)
        )
        self.assertEqual(
            runtime(store=store).repository.row_counts()["audit.run_manifests"], 6
        )


if __name__ == "__main__":
    unittest.main()
