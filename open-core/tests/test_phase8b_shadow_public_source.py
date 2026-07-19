from __future__ import annotations

import json
import unittest
from copy import deepcopy
from dataclasses import replace
from datetime import timedelta
from unittest.mock import patch

from secure_eval_wrapper.data_collection import HttpResponse
from secure_eval_wrapper.live.identity import RuntimeRepositoryIdentity
from secure_eval_wrapper.live.shadow_repository import MemoryShadowRepository, ShadowMemoryStore
from secure_eval_wrapper.live.shadow_runtime import (
    FixtureShadowMarketSource,
    OkxPublicShadowMarketSource,
    ShadowAssuranceRuntime,
    ShadowAuthorityError,
)
from secure_eval_wrapper.live.shadow_scenarios import (
    SHADOW_FIXTURE_TIME,
    ShadowScenarioSpec,
    scenario_by_id,
)


SHA = "a" * 40


def _response(payload: object) -> HttpResponse:
    return HttpResponse(200, json.dumps(payload).encode("utf-8"), {})


def _instrument_response() -> HttpResponse:
    return _response({
        "code": "0",
        "msg": "",
        "data": [{
            "instType": "SPOT",
            "instId": "BTC-USDT",
            "baseCcy": "BTC",
            "quoteCcy": "USDT",
            "settleCcy": "",
            "state": "live",
            "tickSz": "0.1",
            "lotSz": "0.0001",
            "minSz": "0.0001",
            "ctVal": "",
            "ctMult": "",
            "ctType": "",
            "ctValCcy": "",
            "instFamily": "",
            "uly": "",
            "listTime": "1609459200000",
            "expTime": "",
            "ruleType": "normal",
            "upcChg": [],
        }],
    })


def _trade_response() -> HttpResponse:
    observed_ms = int(SHADOW_FIXTURE_TIME.timestamp() * 1000)
    return _response({
        "code": "0",
        "msg": "",
        "data": [{
            "instId": "BTC-USDT",
            "side": "buy",
            "sz": "0.001",
            "source": "0",
            "px": "50000",
            "tradeId": "phase8b-public-1",
            "ts": str(observed_ms),
        }],
    })


class QueueTransport:
    def __init__(self, *actions: object) -> None:
        self.actions = list(actions)
        self.requests = []

    def send(self, request):
        self.requests.append(request)
        action = self.actions[len(self.requests) - 1]
        if isinstance(action, BaseException):
            raise action
        return action


def _runtime(source, store: ShadowMemoryStore | None = None) -> ShadowAssuranceRuntime:
    return ShadowAssuranceRuntime(
        repository=MemoryShadowRepository(store),
        market_source=source,
        identity_resolver=lambda: RuntimeRepositoryIdentity(SHA, "git_checkout"),
    )


def _scenario_from_load(load, *, market: dict[str, object] | None = None) -> ShadowScenarioSpec:
    base = scenario_by_id("normal_public_snapshot")
    request = dict(base.request_payload)
    request["decision_at_utc"] = SHADOW_FIXTURE_TIME.isoformat()
    payload = dict(load.payload) if market is None else market
    failure = payload.get("failure_kind")
    return ShadowScenarioSpec(
        "public_network_okx_btc_usdt",
        "market",
        base.account_payload,
        payload,
        request,
        "accepted" if failure is None else "blocked",
        (),
        1 if failure is None else 0,
        int(payload["network_read_count"]),
        0,
        "persisted",
    )


class Phase8BShadowPublicSourceTests(unittest.TestCase):
    def test_success_uses_exact_two_public_gets_and_bounded_trade_contract(self):
        transport = QueueTransport(_instrument_response(), _trade_response())
        source = OkxPublicShadowMarketSource._for_test(transport)
        summary = _runtime(source).run_public(
            provider="okx",
            instrument="BTC-USDT",
            at_utc=SHADOW_FIXTURE_TIME,
        )

        self.assertTrue(summary.accepted)
        self.assertEqual(summary.safety_facts.network_read_count, 2)
        self.assertEqual(len(summary.public_source_hashes), 2)
        self.assertIsNotNone(summary.public_provenance_hash)
        self.assertEqual(len(transport.requests), 2)
        self.assertEqual(
            [request.url for request in transport.requests],
            [
                "https://openapi.okx.com/api/v5/public/instruments",
                "https://openapi.okx.com/api/v5/market/history-trades",
            ],
        )
        for request in transport.requests:
            self.assertEqual(request.method, "GET")
            self.assertEqual(dict(request.headers), {})
        instruments, trades = transport.requests
        self.assertEqual(dict(instruments.query_params), {"instType": "SPOT", "instId": "BTC-USDT"})
        self.assertEqual(trades.query_params["instId"], "BTC-USDT")
        self.assertEqual(trades.query_params["type"], "2")
        self.assertLessEqual(int(trades.query_params["limit"]), 10)
        self.assertEqual(
            int(trades.query_params["after"]),
            int((SHADOW_FIXTURE_TIME + timedelta(milliseconds=1)).timestamp() * 1000),
        )
        self.assertEqual(summary.safety_facts.network_write_count, 0)
        self.assertEqual(summary.safety_facts.authenticated_endpoint_call_count, 0)

    def test_send_boundary_counts_are_0_1_1_2_2(self):
        cases = (
            (
                "first_local_validation",
                QueueTransport(),
                "fetch_instruments",
                ValueError("local"),
                0,
            ),
            ("first_http_failure", QueueTransport(TimeoutError("first")), None, None, 1),
            (
                "second_local_validation",
                QueueTransport(_instrument_response()),
                "fetch_trades",
                ValueError("local"),
                1,
            ),
            (
                "second_http_failure",
                QueueTransport(_instrument_response(), TimeoutError("second")),
                None,
                None,
                2,
            ),
            ("success", QueueTransport(_instrument_response(), _trade_response()), None, None, 2),
        )
        response_hash_counts = []
        observed = []
        for name, transport, patched_method, side_effect, expected in cases:
            with self.subTest(name=name):
                source = OkxPublicShadowMarketSource._for_test(transport)
                runtime = _runtime(source)
                if patched_method is None:
                    summary = runtime.run_public(
                        provider="okx", instrument="BTC-USDT", at_utc=SHADOW_FIXTURE_TIME
                    )
                else:
                    target = (
                        "secure_eval_wrapper.data_collection.okx_v5_public."
                        f"OkxPublicProvider.{patched_method}"
                    )
                    with patch(target, side_effect=side_effect):
                        summary = runtime.run_public(
                            provider="okx", instrument="BTC-USDT", at_utc=SHADOW_FIXTURE_TIME
                        )
                observed.append(summary.safety_facts.network_read_count)
                self.assertEqual(summary.safety_facts.network_read_count, expected)
                self.assertEqual(len(transport.requests), expected)
                self.assertEqual(summary.safety_facts.network_write_count, 0)
                response_hash_counts.append(len(summary.public_source_hashes))
                self.assertEqual(summary.safety_facts.authenticated_endpoint_call_count, 0)
        self.assertEqual(observed, [0, 1, 1, 2, 2])
        self.assertEqual(response_hash_counts, [0, 0, 1, 1, 2])

    def test_fixture_classification_and_read_count_attacks_fail_before_persistence(self):
        for mutation in (
            {"classification": "public_network"},
            {"classification": "unavailable"},
            {"network_read_count": 1},
        ):
            with self.subTest(mutation=mutation):
                base = scenario_by_id("normal_public_snapshot")
                market = deepcopy(dict(base.market_payload))
                market.update(mutation)
                attacked = replace(base, scenario_id=f"attack_{len(mutation)}", market_payload=market)
                store = ShadowMemoryStore()
                with self.assertRaises(ShadowAuthorityError):
                    _runtime(FixtureShadowMarketSource(), store)._run_fixture_scenario_for_test(attacked)
                self.assertEqual(MemoryShadowRepository(store).row_counts()["audit.run_manifests"], 0)

    def test_subclass_monkeypatch_forged_and_copied_provenance_are_rejected(self):
        transport = QueueTransport(_instrument_response(), _trade_response())
        source = OkxPublicShadowMarketSource._for_test(transport)
        with self.assertRaises(AttributeError):
            source.load = lambda *args, **kwargs: None

        class PublicSourceSubclass(OkxPublicShadowMarketSource):
            pass

        with self.assertRaises(ShadowAuthorityError):
            _runtime(PublicSourceSubclass._for_test(QueueTransport()))

        load = source.load("public_network_okx_btc_usdt", at_utc=SHADOW_FIXTURE_TIME)
        store = ShadowMemoryStore()
        runtime = _runtime(source, store)
        forged = replace(load.provenance, _capability=object())
        with self.assertRaises(ShadowAuthorityError):
            runtime._run_validated_input(
                _scenario_from_load(load),
                _source_mode="public",
                _public_provenance=forged,
            )
        self.assertEqual(MemoryShadowRepository(store).row_counts()["audit.run_manifests"], 0)

        copied_payload = deepcopy(dict(load.payload))
        copied_payload["last_price"] = "50001"
        with self.assertRaises(ShadowAuthorityError):
            runtime._run_validated_input(
                _scenario_from_load(load, market=copied_payload),
                _source_mode="public",
                _public_provenance=load.provenance,
            )
        self.assertEqual(MemoryShadowRepository(store).row_counts()["audit.run_manifests"], 0)

        class_patch_store = ShadowMemoryStore()
        class_patch_runtime = _runtime(source, class_patch_store)
        with patch.object(
            OkxPublicShadowMarketSource,
            "load",
            lambda self, scenario_id, *, at_utc: load,
        ):
            with self.assertRaises(ShadowAuthorityError):
                class_patch_runtime.run_public(
                    provider="okx", instrument="BTC-USDT", at_utc=SHADOW_FIXTURE_TIME
                )
        self.assertEqual(
            MemoryShadowRepository(class_patch_store).row_counts()["audit.run_manifests"], 0
        )

if __name__ == "__main__":
    unittest.main()
