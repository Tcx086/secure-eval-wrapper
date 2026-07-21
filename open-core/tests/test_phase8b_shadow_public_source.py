from __future__ import annotations

import inspect
import json
import unittest
from copy import deepcopy
from dataclasses import replace
from datetime import timedelta
from unittest.mock import patch
from uuid import uuid4

from secure_eval_wrapper.data_collection import HttpResponse
from secure_eval_wrapper.data_collection.hashing import sha256_payload
from secure_eval_wrapper.data_collection.http_transport import UrlLibHttpTransport
from secure_eval_wrapper.data_collection.models import (
    DataRequest,
    MarketDataType,
)
from secure_eval_wrapper.data_collection.okx_v5_public import (
    OkxPublicProvider,
    okx_spot_instrument_key,
)
from secure_eval_wrapper.live import shadow_runtime as runtime_module
from secure_eval_wrapper.live.identity import RuntimeRepositoryIdentity
from secure_eval_wrapper.live.shadow_models import ShadowDataProvenance
from secure_eval_wrapper.live.shadow_repository import (
    MemoryShadowRepository,
    ShadowMemoryStore,
)
from secure_eval_wrapper.live.shadow_runtime import (
    FixtureShadowMarketSource,
    OkxPublicShadowMarketSource,
    ShadowAssuranceRuntime,
    ShadowAuthorityError,
)
from secure_eval_wrapper.live.shadow_scenarios import (
    SHADOW_FIXTURE_TIME,
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


def _protocol_contract_harness(transport: QueueTransport) -> dict[str, object]:
    """Exercise only the provider request contract; never create runtime authority."""

    counting = runtime_module._CountingPublicTransport(transport)
    provider = OkxPublicProvider(
        transport=counting,
        timeout=3.0,
        max_pages=1,
        clock=lambda: SHADOW_FIXTURE_TIME,
    )
    key = okx_spot_instrument_key("BTC-USDT")
    collection_id = uuid4()
    try:
        provider.fetch_instruments(
            DataRequest(
                collection_id,
                "okx",
                MarketDataType.INSTRUMENTS,
                (),
                limit=1,
                instruments=(key,),
            )
        )
        provider.fetch_trades(
            DataRequest(
                collection_id,
                "okx",
                MarketDataType.TRADES,
                ("BTC-USDT",),
                start_at_utc=SHADOW_FIXTURE_TIME - timedelta(minutes=5),
                end_at_utc=SHADOW_FIXTURE_TIME + timedelta(milliseconds=1),
                limit=10,
                max_pages=1,
            )
        )
        outcome = "success"
    except Exception:
        outcome = "failure"
    return {
        "classification": "fixture_protocol_test",
        "outcome": outcome,
        "actual_send_count": counting.actual_send_count,
        "endpoint_identities": tuple(counting.endpoint_identities),
        "response_hashes": tuple(counting.response_hashes),
        "requests": tuple(transport.requests),
    }


class Phase8BShadowPublicSourceTests(unittest.TestCase):
    def test_production_source_has_no_transport_injection_and_is_immutable(self):
        parameters = inspect.signature(OkxPublicShadowMarketSource).parameters
        self.assertEqual(set(parameters), {"allow_public_network", "timeout_seconds"})
        self.assertFalse(hasattr(OkxPublicShadowMarketSource, "_for_test"))
        self.assertFalse(hasattr(OkxPublicShadowMarketSource, "_issue_provenance"))

        source = OkxPublicShadowMarketSource(allow_public_network=True)
        transport = object.__getattribute__(
            source, "_OkxPublicShadowMarketSource__transport"
        )
        self.assertIs(type(transport), UrlLibHttpTransport)
        for name, value in (
            ("_transport", QueueTransport()),
            ("_last_provenance", object()),
            ("timeout_seconds", 9.0),
            ("_OkxPublicShadowMarketSource__transport", QueueTransport()),
            ("_OkxPublicShadowMarketSource__source_instance_id", "0" * 64),
        ):
            with self.subTest(name=name), self.assertRaises(AttributeError):
                setattr(source, name, value)

    def test_protocol_harness_uses_exact_two_gets_without_public_authority(self):
        transport = QueueTransport(_instrument_response(), _trade_response())
        result = _protocol_contract_harness(transport)

        self.assertEqual(result["classification"], "fixture_protocol_test")
        self.assertEqual(result["outcome"], "success")
        self.assertEqual(result["actual_send_count"], 2)
        self.assertEqual(
            result["endpoint_identities"],
            (
                "GET /api/v5/public/instruments",
                "GET /api/v5/market/history-trades",
            ),
        )
        requests = result["requests"]
        self.assertEqual(
            [request.url for request in requests],
            [
                "https://openapi.okx.com/api/v5/public/instruments",
                "https://openapi.okx.com/api/v5/market/history-trades",
            ],
        )
        for request in requests:
            self.assertEqual(request.method, "GET")
            self.assertEqual(dict(request.headers), {})
        instruments, trades = requests
        self.assertEqual(
            dict(instruments.query_params),
            {"instType": "SPOT", "instId": "BTC-USDT"},
        )
        self.assertEqual(trades.query_params["instId"], "BTC-USDT")
        self.assertEqual(trades.query_params["type"], "2")
        self.assertLessEqual(int(trades.query_params["limit"]), 10)
        self.assertEqual(
            int(trades.query_params["after"]),
            int((SHADOW_FIXTURE_TIME + timedelta(milliseconds=1)).timestamp() * 1000),
        )

    def test_protocol_harness_send_boundary_counts_remain_0_1_1_2_2(self):
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
        observed = []
        response_hash_counts = []
        for name, transport, patched_method, side_effect, expected in cases:
            with self.subTest(name=name):
                target = (
                    "secure_eval_wrapper.data_collection.okx_v5_public."
                    f"OkxPublicProvider.{patched_method}"
                )
                context = (
                    patch(target, side_effect=side_effect)
                    if patched_method is not None
                    else unittest.mock.patch.dict({}, {})
                )
                with context:
                    result = _protocol_contract_harness(transport)
                observed.append(result["actual_send_count"])
                response_hash_counts.append(len(result["response_hashes"]))
                self.assertEqual(result["classification"], "fixture_protocol_test")
        self.assertEqual(observed, [0, 1, 1, 2, 2])
        self.assertEqual(response_hash_counts, [0, 0, 1, 1, 2])

    def test_fake_protocol_success_and_fixture_promotion_fail_before_persistence(self):
        harness = _protocol_contract_harness(
            QueueTransport(_instrument_response(), _trade_response())
        )
        self.assertEqual(harness["outcome"], "success")
        self.assertEqual(harness["classification"], "fixture_protocol_test")

        base = scenario_by_id("normal_public_snapshot")
        market = deepcopy(dict(base.market_payload))
        market.update(
            classification="public_network",
            network_read_count=2,
            public_source_hashes=("1" * 64, "2" * 64),
        )
        attacked = replace(base, scenario_id="fake_transport_promotion", market_payload=market)
        store = ShadowMemoryStore()
        with self.assertRaises(ShadowAuthorityError):
            _runtime(FixtureShadowMarketSource(), store)._run_fixture_scenario_for_test(attacked)
        self.assertEqual(
            MemoryShadowRepository(store).row_counts()["audit.run_manifests"],
            0,
        )

    def test_cross_instance_copy_subclass_and_monkeypatch_attacks_fail_closed(self):
        source_a = OkxPublicShadowMarketSource(allow_public_network=True)
        source_b = OkxPublicShadowMarketSource(allow_public_network=True)
        payload = dict(scenario_by_id("normal_public_snapshot").market_payload)
        payload.update(
            classification="unavailable",
            failure_kind="timeout",
            source_identity="okx-public-network-unavailable",
            network_read_count=0,
            public_timestamp_utc=SHADOW_FIXTURE_TIME.isoformat(),
            public_source_hashes=(),
        )
        source_instance_id = object.__getattribute__(
            source_a, "_OkxPublicShadowMarketSource__source_instance_id"
        )
        durable = ShadowDataProvenance(
            "unavailable",
            (),
            0,
            (),
            source_instance_id,
            sha256_payload(payload),
            "timeout",
        )
        source_type = (
            f"{OkxPublicShadowMarketSource.__module__}."
            f"{OkxPublicShadowMarketSource.__qualname__}"
        )
        provenance = runtime_module._PublicSourceProvenance(
            source_type,
            (),
            0,
            (),
            "BTC-USDT",
            source_instance_id,
            "unavailable",
            sha256_payload(payload),
            "timeout",
            durable.provenance_hash,
            object.__getattribute__(
                source_a,
                "_OkxPublicShadowMarketSource__provenance_capability",
            ),
        )
        runtime_module._validate_public_source_provenance(
            source_a, provenance, payload
        )
        store = ShadowMemoryStore()
        with self.assertRaises(ShadowAuthorityError):
            runtime_module._validate_public_source_provenance(
                source_b, provenance, payload
            )
        self.assertEqual(
            MemoryShadowRepository(store).row_counts()["audit.run_manifests"],
            0,
        )

        class PublicSourceSubclass(OkxPublicShadowMarketSource):
            pass

        with self.assertRaises(ShadowAuthorityError):
            _runtime(PublicSourceSubclass(allow_public_network=True), store)

        with self.assertRaises(AttributeError):
            source_a.load = lambda *args, **kwargs: None

        with patch.object(OkxPublicShadowMarketSource, "load", lambda *args, **kwargs: None):
            with self.assertRaises(ShadowAuthorityError):
                _runtime(source_a, store).run_public(
                    provider="okx",
                    instrument="BTC-USDT",
                    at_utc=SHADOW_FIXTURE_TIME,
                )

        with patch.object(
            OkxPublicShadowMarketSource,
            "__setattr__",
            object.__setattr__,
        ):
            with self.assertRaises(ShadowAuthorityError):
                _runtime(source_a, store).run_public(
                    provider="okx",
                    instrument="BTC-USDT",
                    at_utc=SHADOW_FIXTURE_TIME,
                )

        with patch.object(UrlLibHttpTransport, "send", lambda *args, **kwargs: _instrument_response()):
            with self.assertRaises(ShadowAuthorityError):
                _runtime(source_a, store).run_public(
                    provider="okx",
                    instrument="BTC-USDT",
                    at_utc=SHADOW_FIXTURE_TIME,
                )
        self.assertEqual(
            MemoryShadowRepository(store).row_counts()["audit.run_manifests"],
            0,
        )


if __name__ == "__main__":
    unittest.main()
