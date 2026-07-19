from __future__ import annotations

import unittest

from secure_eval_wrapper.live.shadow_scenarios import account_scenarios
from phase8b_shadow_test_support import runtime


class Phase8BShadowAccountMatrixTests(unittest.TestCase):
    def test_all_27_stable_synthetic_account_scenarios(self):
        scenarios = account_scenarios()
        self.assertEqual(len(scenarios), 27)
        self.assertEqual(len({scenario.scenario_id for scenario in scenarios}), 27)
        self.assertEqual(len({scenario.input_hash for scenario in scenarios}), 27)
        for scenario in scenarios:
            with self.subTest(scenario=scenario.scenario_id):
                summary = runtime()._run_fixture_scenario_for_test(scenario)
                self.assertEqual(
                    "accepted" if summary.accepted else "blocked",
                    scenario.expected_result,
                )
                self.assertEqual(summary.blockers, scenario.expected_blockers)
                self.assertEqual(
                    summary.shadow_intent_count,
                    scenario.expected_shadow_intent_count,
                )
                self.assertEqual(scenario.expected_network_reads, 0)
                self.assertEqual(scenario.expected_network_writes, 0)
                self.assertEqual(summary.safety_facts.production_write_count, 0)
                self.assertIn(summary.persistence_result, {"persisted", "idempotent_replay"})


if __name__ == "__main__":
    unittest.main()
