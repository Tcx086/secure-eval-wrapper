from __future__ import annotations

import unittest

from secure_eval_wrapper.live.shadow_scenarios import market_failure_scenarios
from phase8b_shadow_test_support import runtime


class Phase8BShadowMarketFailureTests(unittest.TestCase):
    def test_all_27_public_market_cases_fail_closed_as_catalogued(self):
        scenarios = market_failure_scenarios()
        self.assertEqual(len(scenarios), 27)
        self.assertEqual(len({scenario.scenario_id for scenario in scenarios}), 27)
        for scenario in scenarios:
            with self.subTest(scenario=scenario.scenario_id):
                summary = runtime().run_scenario(scenario)
                self.assertEqual(
                    "accepted" if summary.accepted else "blocked",
                    scenario.expected_result,
                )
                self.assertEqual(summary.blockers, scenario.expected_blockers)
                self.assertEqual(
                    summary.shadow_intent_count,
                    scenario.expected_shadow_intent_count,
                )
                self.assertEqual(summary.safety_facts.production_write_count, 0)
                self.assertEqual(summary.safety_facts.authenticated_endpoint_call_count, 0)

    def test_only_normal_public_fixture_is_accepted(self):
        accepted = [
            scenario.scenario_id
            for scenario in market_failure_scenarios()
            if runtime().run_scenario(scenario).accepted
        ]
        self.assertEqual(accepted, ["normal_public_snapshot"])


if __name__ == "__main__":
    unittest.main()
