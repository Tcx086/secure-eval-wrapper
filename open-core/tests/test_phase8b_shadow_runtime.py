from __future__ import annotations

import io
import json
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch

from secure_eval_wrapper.live.identity import RuntimeRepositoryIdentity
from secure_eval_wrapper.live.shadow_cli import main as shadow_main
from secure_eval_wrapper.live.shadow_models import ShadowOrderIntent
from secure_eval_wrapper.live.shadow_repository import MemoryShadowRepository
from secure_eval_wrapper.live.shadow_runtime import (
    OkxPublicShadowMarketSource,
    ShadowAssuranceRuntime,
    ShadowAuthorityError,
)
from secure_eval_wrapper.live.shadow_scenarios import SHADOW_FIXTURE_TIME
from phase8b_shadow_test_support import runtime


class Phase8BShadowRuntimeTests(unittest.TestCase):
    def test_clean_fixture_uses_shared_policy_and_is_permanently_non_routable(self):
        service = runtime()
        summary = service.run_fixture("clean_flat_account")
        self.assertTrue(summary.accepted)
        self.assertEqual(summary.shadow_intent_count, 1)
        bundle = service.repository.load_bundle(summary.shadow_run_id)
        intent = bundle["decision"]["shadow_intent"]
        self.assertTrue(intent["shadow_only"])
        self.assertFalse(intent["production_write_enabled"])
        self.assertFalse(intent["submit_reachable"])
        self.assertFalse(intent["cancel_reachable"])
        self.assertFalse(intent["transport_called"])
        self.assertFalse(hasattr(ShadowOrderIntent, "submit_order"))
        self.assertFalse(hasattr(ShadowOrderIntent, "cancel_order"))

    def test_fixture_mode_is_socket_free(self):
        with patch("socket.socket", side_effect=AssertionError("socket forbidden")):
            summary = runtime().run_fixture("clean_flat_account")
        self.assertTrue(summary.accepted)
        self.assertEqual(summary.safety_facts.network_read_count, 0)

    def test_blocked_result_preserves_reason_and_no_executable_intent(self):
        summary = runtime().run_fixture("permission_read_only")
        self.assertFalse(summary.accepted)
        self.assertEqual(summary.blockers, ("synthetic_permission_not_trade_enabled",))
        self.assertEqual(summary.shadow_intent_count, 0)

    def test_cli_without_disposable_database_is_socket_free_and_fail_closed(self):
        for arguments in (
            ["run"],
            ["run", "--allow-public-network"],
            ["run", "--postgres-database", "secure_eval_phase8b"],
        ):
            with self.subTest(arguments=arguments):
                output = io.StringIO()
                with patch("socket.socket", side_effect=AssertionError("socket forbidden")):
                    with redirect_stdout(output):
                        result = shadow_main(arguments)
                payload = json.loads(output.getvalue())
                self.assertEqual(result, 2)
                self.assertEqual(payload["status"], "blocked")
                self.assertEqual(payload["production_write_count"], 0)
                self.assertFalse(payload["operator_database_accessed"])

    def test_public_source_requires_exact_network_opt_in(self):
        with self.assertRaises(ShadowAuthorityError):
            OkxPublicShadowMarketSource(allow_public_network=False)

    def test_local_public_failure_reports_zero_sends_without_leaking_exception(self):
        service = ShadowAssuranceRuntime(
            repository=MemoryShadowRepository(),
            market_source=OkxPublicShadowMarketSource(allow_public_network=True),
            identity_resolver=lambda: RuntimeRepositoryIdentity("a" * 40, "git_checkout"),
        )
        with patch(
            "secure_eval_wrapper.data_collection.okx_v5_public.OkxPublicProvider.fetch_instruments",
            side_effect=TimeoutError("private upstream diagnostic"),
        ):
            summary = service.run_public(
                provider="okx", instrument="BTC-USDT", at_utc=SHADOW_FIXTURE_TIME
            )
        self.assertFalse(summary.accepted)
        self.assertEqual(summary.blockers, ("public_network_timeout",))
        self.assertEqual(summary.safety_facts.network_read_count, 0)
        self.assertNotIn("private upstream diagnostic", str(summary.public_payload()))

    def test_summary_never_claims_account_or_authenticated_proof(self):
        payload = dict(runtime().run_fixture("clean_flat_account").public_payload())
        for key in (
            "production_submit_reachable",
            "production_cancel_reachable",
            "real_account_data_used",
            "operator_database_accessed",
            "authenticated_proof_executed",
        ):
            self.assertFalse(payload[key])
        for key in (
            "production_transport_call_count",
            "authenticated_endpoint_call_count",
            "credential_read_count",
            "production_write_count",
        ):
            self.assertEqual(payload[key], 0)


if __name__ == "__main__":
    unittest.main()
