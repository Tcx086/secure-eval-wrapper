from __future__ import annotations

import unittest
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from uuid import UUID

from secure_eval_wrapper.live.shadow_repository import (
    ShadowInjectedCrash,
    ShadowMemoryStore,
    ShadowPersistenceConflict,
    ShadowPostCommitCrash,
)
from secure_eval_wrapper.live.shadow_runtime import RUNTIME_CRASH_POINTS
from secure_eval_wrapper.live.shadow_scenarios import ShadowScenarioSpec, scenario_by_id
from phase8b_shadow_test_support import runtime


def _variant(base: ShadowScenarioSpec, scenario_id: str, *, market=None, account=None):
    return ShadowScenarioSpec(
        scenario_id,
        base.category,
        deepcopy(dict(base.account_payload)) if account is None else account,
        deepcopy(dict(base.market_payload)) if market is None else market,
        deepcopy(dict(base.request_payload)),
        "accepted",
        (),
        1,
    )


class Phase8BShadowConcurrencyCrashTests(unittest.TestCase):
    def test_seven_concurrency_cases_have_explicit_idempotency_or_conflict(self):
        base = scenario_by_id("clean_flat_account")

        # 1. Two identical runs execute simultaneously.
        store = ShadowMemoryStore()
        run_id = UUID("00000000-0000-5000-8000-000000008b01")
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = tuple(pool.map(
                lambda _: runtime(store=store)._run_fixture_scenario_for_test(base, shadow_run_id=run_id),
                range(2),
            ))
        self.assertEqual(sum(item.replayed for item in results), 1)
        self.assertEqual(len({item.decision_hash for item in results}), 1)

        # 2. Different market snapshots remain separate.
        store = ShadowMemoryStore()
        market = deepcopy(dict(base.market_payload)); market["last_price"] = "50001"
        market_variant = _variant(base, "concurrent_market_variant", market=market)
        with ThreadPoolExecutor(max_workers=2) as pool:
            market_results = (
                pool.submit(runtime(store=store)._run_fixture_scenario_for_test, base),
                pool.submit(runtime(store=store)._run_fixture_scenario_for_test, market_variant),
            )
            market_results = tuple(item.result() for item in market_results)
        self.assertEqual(len({item.input_hash for item in market_results}), 2)

        # 3. Different synthetic accounts remain separate.
        store = ShadowMemoryStore()
        account = deepcopy(dict(base.account_payload)); account["balances"][0]["available"] = "9999"
        account_variant = _variant(base, "concurrent_account_variant", account=account)
        with ThreadPoolExecutor(max_workers=2) as pool:
            account_results = (
                pool.submit(runtime(store=store)._run_fixture_scenario_for_test, base),
                pool.submit(runtime(store=store)._run_fixture_scenario_for_test, account_variant),
            )
            account_results = tuple(item.result() for item in account_results)
        self.assertEqual(len({item.input_hash for item in account_results}), 2)

        # 4. The same run ID cannot silently overwrite a different payload.
        store = ShadowMemoryStore()
        conflict_id = UUID("00000000-0000-5000-8000-000000008b04")
        runtime(store=store)._run_fixture_scenario_for_test(base, shadow_run_id=conflict_id)
        with self.assertRaises(ShadowPersistenceConflict):
            runtime(store=store)._run_fixture_scenario_for_test(market_variant, shadow_run_id=conflict_id)

        # 5. Different run IDs may intentionally carry the same payload.
        store = ShadowMemoryStore()
        same_payload = tuple(
            runtime(store=store)._run_fixture_scenario_for_test(
                base,
                shadow_run_id=UUID(f"00000000-0000-5000-8000-000000008b0{index}"),
            )
            for index in (5, 6)
        )
        self.assertEqual(len({item.input_hash for item in same_payload}), 1)
        self.assertEqual(len({item.shadow_run_id for item in same_payload}), 2)

        # 6. An idempotent replay and a new run can proceed together.
        store = ShadowMemoryStore()
        replay_id = UUID("00000000-0000-5000-8000-000000008b07")
        runtime(store=store)._run_fixture_scenario_for_test(base, shadow_run_id=replay_id)
        with ThreadPoolExecutor(max_workers=2) as pool:
            replay_future = pool.submit(
                runtime(store=store)._run_fixture_scenario_for_test, base, shadow_run_id=replay_id
            )
            new_future = pool.submit(runtime(store=store)._run_fixture_scenario_for_test, market_variant)
            replay, new = replay_future.result(), new_future.result()
        self.assertTrue(replay.replayed)
        self.assertFalse(new.replayed)

        # 7. A restarted reader can load committed evidence while another run writes.
        store = ShadowMemoryStore()
        committed = runtime(store=store)._run_fixture_scenario_for_test(base)
        with ThreadPoolExecutor(max_workers=2) as pool:
            read_future = pool.submit(
                runtime(store=store).repository.load_bundle, committed.shadow_run_id
            )
            write_future = pool.submit(runtime(store=store)._run_fixture_scenario_for_test, account_variant)
            loaded, written = read_future.result(), write_future.result()
        self.assertEqual(loaded["decision"]["decision_hash"], committed.decision_hash)
        self.assertNotEqual(written.input_hash, committed.input_hash)
        self.assertEqual(runtime(store=store).repository.row_counts()["audit.run_manifests"], 2)

        for bundle in store.bundles.values():
            self.assertEqual(bundle["status"], "complete")
            safety = bundle["decision"]["safety_facts"]
            self.assertEqual(safety["production_write_count"], 0)

    def test_all_nine_crash_points_roll_back_or_recover_complete_bundle(self):
        self.assertEqual(len(RUNTIME_CRASH_POINTS), 9)
        post_commit = "after_transaction_commit_before_response"
        for index, crash_point in enumerate(sorted(RUNTIME_CRASH_POINTS), start=1):
            with self.subTest(crash_point=crash_point):
                store = ShadowMemoryStore()
                run_id = UUID(f"00000000-0000-5000-8000-000000008c{index:02d}")
                exception = ShadowPostCommitCrash if crash_point == post_commit else ShadowInjectedCrash
                with self.assertRaises(exception):
                    runtime(store=store).run_fixture(
                        "clean_flat_account", shadow_run_id=run_id, crash_at=crash_point
                    )
                bundle = runtime(store=store).repository.load_bundle(run_id)
                if crash_point == post_commit:
                    self.assertEqual(bundle["status"], "complete")
                    recovered = runtime(store=store).run_fixture(
                        "clean_flat_account", shadow_run_id=run_id
                    )
                    self.assertTrue(recovered.replayed)
                else:
                    self.assertIsNone(bundle)
                    recovered = runtime(store=store).run_fixture(
                        "clean_flat_account", shadow_run_id=run_id
                    )
                    self.assertFalse(recovered.replayed)
                complete = runtime(store=store).repository.load_bundle(run_id)
                self.assertEqual(complete["status"], "complete")
                self.assertEqual(
                    complete["decision"]["safety_facts"]["production_write_count"], 0
                )


if __name__ == "__main__":
    unittest.main()
