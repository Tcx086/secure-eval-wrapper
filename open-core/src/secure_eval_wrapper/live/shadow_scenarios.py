"""Deterministic Phase 8B synthetic-account and public-market matrices."""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from types import MappingProxyType
from typing import Mapping

from secure_eval_wrapper.data_collection.hashing import sha256_payload


SHADOW_FIXTURE_TIME = datetime(2026, 7, 18, 12, 0, tzinfo=timezone.utc)


@dataclass(frozen=True)
class ShadowScenarioSpec:
    scenario_id: str
    category: str
    account_payload: Mapping[str, object]
    market_payload: Mapping[str, object]
    request_payload: Mapping[str, object]
    expected_result: str
    expected_blockers: tuple[str, ...]
    expected_shadow_intent_count: int
    expected_network_reads: int = 0
    expected_network_writes: int = 0
    expected_persistence_result: str = "persisted"

    def __post_init__(self) -> None:
        if self.category not in {"account", "market"}:
            raise ValueError("shadow scenario category must be account or market")
        if self.expected_result not in {"accepted", "blocked"}:
            raise ValueError("shadow scenario result must be accepted or blocked")
        if self.expected_shadow_intent_count not in (0, 1):
            raise ValueError("shadow scenario intent count must be zero or one")
        if self.expected_network_writes != 0:
            raise PermissionError("shadow scenarios cannot expect network writes")
        object.__setattr__(self, "account_payload", MappingProxyType(deepcopy(dict(self.account_payload))))
        object.__setattr__(self, "market_payload", MappingProxyType(deepcopy(dict(self.market_payload))))
        object.__setattr__(self, "request_payload", MappingProxyType(deepcopy(dict(self.request_payload))))
        object.__setattr__(self, "expected_blockers", tuple(self.expected_blockers))

    @property
    def input_hash(self) -> str:
        return sha256_payload({
            "scenario_id": self.scenario_id,
            "category": self.category,
            "account": dict(self.account_payload),
            "market": dict(self.market_payload),
            "request": dict(self.request_payload),
        })


def _base_account() -> dict[str, object]:
    return {
        "synthetic_account": True,
        "account_classification": "synthetic_spot",
        "balances": [
            {"asset": "USDT", "total": "10000", "available": "10000", "reserved": "0"},
            {"asset": "BTC", "total": "1", "available": "1", "reserved": "0"},
        ],
        "positions": [],
        "pending_orders": [],
        "reserved_notional": "0",
        "permissions": ["shadow_decide", "synthetic_trade_profile"],
        "daily_realized_pnl": "0",
        "current_equity": "10000",
        "high_watermark_equity": "10000",
        "kill_switch_active": False,
        "risk_limits": {"profile": "guarded_live_shared"},
    }


def _base_market() -> dict[str, object]:
    return {
        "provider": "okx",
        "instrument": "BTC-USDT",
        "instrument_type": "spot",
        "bid": "49999.9",
        "ask": "50000.1",
        "last_price": "50000",
        "public_timestamp_utc": SHADOW_FIXTURE_TIME.isoformat(),
        "instrument_status": "live",
        "settlement_asset": "USDT",
        "tick_size": "0.1",
        "lot_size": "0.0001",
        "minimum_quantity": "0.0001",
        "maximum_quantity": "0.1",
        "source_identity": "okx-public-fixture-v1",
        "classification": "fixture",
        "network_read_count": 0,
        "response_rows": 1,
        "metadata_present": True,
        "response_complete": True,
        "provider_code": "0",
        "replayed": False,
        "cached": False,
        "conflicting_sources": False,
        "fixture_declared_operational": False,
        "operational_declared_fixture": False,
        "failure_kind": None,
    }


def _base_request() -> dict[str, object]:
    return {
        "direction": "long",
        "quantity": "0.01",
        "limit_price": "50000",
        "order_type": "limit",
        "instrument": "BTC-USDT",
        "decision_at_utc": SHADOW_FIXTURE_TIME.isoformat(),
    }


def _scenario(
    scenario_id: str,
    category: str,
    *,
    account: Mapping[str, object] | None = None,
    market: Mapping[str, object] | None = None,
    request: Mapping[str, object] | None = None,
    accepted: bool = False,
    blockers: tuple[str, ...] = (),
    intents: int | None = None,
) -> ShadowScenarioSpec:
    return ShadowScenarioSpec(
        scenario_id,
        category,
        _base_account() if account is None else account,
        _base_market() if market is None else market,
        _base_request() if request is None else request,
        "accepted" if accepted else "blocked",
        blockers,
        (1 if accepted else 0) if intents is None else intents,
    )


def account_scenarios() -> tuple[ShadowScenarioSpec, ...]:
    scenarios: list[ShadowScenarioSpec] = []
    scenarios.append(_scenario("clean_flat_account", "account", accepted=True))

    account = _base_account()
    account["balances"][0] = {"asset": "USDT", "total": "100", "available": "100", "reserved": "0"}
    scenarios.append(_scenario("insufficient_quote_balance", "account", account=account, blockers=("insufficient_quote_balance",), intents=1))

    account = _base_account()
    account["balances"][1] = {"asset": "BTC", "total": "0", "available": "0", "reserved": "0"}
    account["positions"] = [{"instrument": "BTC-USDT", "instrument_type": "spot", "quantity": "0.01", "notional": "510", "settlement_asset": "USDT"}]
    request = _base_request(); request.update(direction="short", quantity="0.01")
    scenarios.append(_scenario("insufficient_base_balance", "account", account=account, request=request, blockers=("insufficient_base_balance",), intents=1))

    account = _base_account()
    account["positions"] = [{"instrument": "BTC-USDT", "instrument_type": "spot", "quantity": "0.02", "notional": "1000", "settlement_asset": "USDT"}]
    request = _base_request(); request.update(direction="short", quantity="0.01")
    scenarios.append(_scenario("existing_long_spot_position", "account", account=account, request=request, accepted=True))

    account = _base_account()
    account["positions"] = [{"instrument": "BTC-USDT", "instrument_type": "spot", "quantity": "-0.01", "notional": "-500", "settlement_asset": "USDT"}]
    scenarios.append(_scenario("synthetic_short_position", "account", account=account, blockers=("synthetic_short_position",)))

    for scenario_id, instrument_type in (
        ("synthetic_perpetual_position", "perpetual_swap"),
        ("synthetic_futures_position", "dated_future"),
        ("synthetic_options_exposure", "option"),
    ):
        account = _base_account()
        account["positions"] = [{"instrument": "BTC-USDT", "instrument_type": instrument_type, "quantity": "1", "notional": "500", "settlement_asset": "USDT"}]
        scenarios.append(_scenario(scenario_id, "account", account=account, blockers=("synthetic_derivative_exposure",)))

    account = _base_account()
    account["pending_orders"] = [{"instrument": "BTC-USDT", "side": "buy", "quantity": "0.001", "reserved_notional": "50"}]
    account["reserved_notional"] = "50"
    account["balances"][0] = {"asset": "USDT", "total": "10000", "available": "9950", "reserved": "50"}
    scenarios.append(_scenario("pending_buy_order", "account", account=account, accepted=True))

    account = _base_account()
    account["pending_orders"] = [{"instrument": "BTC-USDT", "side": "sell", "quantity": "0.001", "reserved_notional": "50"}]
    account["reserved_notional"] = "50"
    account["balances"][1] = {"asset": "BTC", "total": "1", "available": "0.999", "reserved": "0.001"}
    scenarios.append(_scenario("pending_sell_order", "account", account=account, accepted=True))

    account = _base_account(); account["reserved_notional"] = "9500"
    account["balances"][0] = {"asset": "USDT", "total": "10000", "available": "500", "reserved": "9500"}
    scenarios.append(_scenario("excessive_reserved_notional", "account", account=account, blockers=("excessive_reserved_notional",)))

    request = _base_request(); request["quantity"] = "0.019"
    scenarios.append(_scenario("near_limit_notional", "account", request=request, accepted=True))

    account = _base_account(); account["daily_realized_pnl"] = "-600"; account["current_equity"] = "9400"; account["high_watermark_equity"] = "9400"
    scenarios.append(_scenario("breached_daily_loss_guard", "account", account=account, blockers=("maximum_daily_realized_loss",), intents=1))

    account = _base_account(); account["kill_switch_active"] = True
    scenarios.append(_scenario("kill_switch_active", "account", account=account, blockers=("kill_switch_not_armed",), intents=1))

    account = _base_account(); account["permissions"] = ["shadow_read_only"]
    scenarios.append(_scenario("permission_read_only", "account", account=account, blockers=("synthetic_permission_not_trade_enabled",)))

    scenarios.append(_scenario("permission_trade_enabled_synthetic_profile", "account", accepted=True))

    account = _base_account(); account["account_classification"] = "conflicting_real_like"
    scenarios.append(_scenario("conflicting_account_classification", "account", account=account, blockers=("conflicting_account_classification",)))

    account = _base_account(); del account["balances"]
    scenarios.append(_scenario("malformed_account_snapshot", "account", account=account, blockers=("malformed_account_snapshot",)))

    account = _base_account()
    duplicate = {"instrument": "BTC-USDT", "instrument_type": "spot", "quantity": "0.01", "notional": "500", "settlement_asset": "USDT"}
    account["positions"] = [duplicate, dict(duplicate)]
    scenarios.append(_scenario("duplicate_positions", "account", account=account, blockers=("duplicate_synthetic_position",)))

    account = _base_account(); account["balances"][0] = {"asset": "USDT", "total": "-1", "available": "-1", "reserved": "0"}
    scenarios.append(_scenario("negative_balance", "account", account=account, blockers=("negative_synthetic_balance",)))

    request = _base_request(); request["quantity"] = "NaN"
    scenarios.append(_scenario("nan_or_infinity_quantity", "account", request=request, blockers=("quantity_not_finite",)))

    account = _base_account(); account["positions"] = [{"instrument": "BTC-USDT", "instrument_type": "spot", "quantity": "0.01", "notional": "500", "settlement_asset": "USDC"}]
    scenarios.append(_scenario("wrong_settlement_asset", "account", account=account, blockers=("wrong_settlement_asset",)))

    market = _base_market(); market["instrument"] = "ETH-USDT"
    request = _base_request(); request["instrument"] = "ETH-USDT"
    scenarios.append(_scenario("non_btc_usdt_instrument", "account", market=market, request=request, blockers=("instrument_not_allowed",)))

    request = _base_request(); request["order_type"] = "market"
    scenarios.append(_scenario("unsupported_order_type", "account", request=request, blockers=("only_limit_orders_allowed",)))

    request = _base_request(); request["quantity"] = "0"
    scenarios.append(_scenario("zero_quantity", "account", request=request, blockers=("quantity_must_be_positive",)))

    request = _base_request(); request["quantity"] = "0.00001"
    scenarios.append(_scenario("quantity_rounding_below_minimum", "account", request=request, blockers=("quantity_below_minimum_after_rounding",)))

    request = _base_request(); request["quantity"] = "0.2"
    scenarios.append(_scenario("quantity_rounding_above_maximum", "account", request=request, blockers=("quantity_above_maximum_after_rounding",)))

    if len(scenarios) != 27:
        raise AssertionError("Phase 8B account catalog must contain exactly 27 scenarios")
    return tuple(scenarios)


def market_failure_scenarios() -> tuple[ShadowScenarioSpec, ...]:
    scenarios = [_scenario("normal_public_snapshot", "market", accepted=True)]

    def failure(scenario_id: str, blocker: str, **updates: object) -> None:
        market = _base_market(); market.update(updates)
        scenarios.append(_scenario(scenario_id, "market", market=market, blockers=(blocker,)))

    failure("stale_data", "stale_market_data", public_timestamp_utc=(SHADOW_FIXTURE_TIME - timedelta(seconds=31)).isoformat())
    failure("future_timestamp", "public_market_future_timestamp", public_timestamp_utc=(SHADOW_FIXTURE_TIME + timedelta(seconds=1)).isoformat())
    failure("clock_skew", "maximum_clock_skew", public_timestamp_utc=(SHADOW_FIXTURE_TIME + timedelta(seconds=6)).isoformat())
    failure("crossed_bid_ask", "crossed_bid_ask", bid="50001", ask="50000")
    failure("bid_zero", "bid_must_be_positive", bid="0")
    failure("ask_zero", "ask_must_be_positive", ask="0")
    failure("negative_price", "market_price_must_be_positive", last_price="-1")
    failure("nan_or_infinity_price", "market_price_not_finite", last_price="Infinity")
    failure("missing_instrument_metadata", "missing_instrument_metadata", metadata_present=False)
    failure("delisted_instrument", "instrument_delisted", instrument_status="delisted")
    failure("instrument_not_live", "instrument_not_live", instrument_status="suspended")
    failure("wrong_instrument_type", "wrong_instrument_type", instrument_type="index")
    failure("perpetual_instead_of_spot", "wrong_instrument_type", instrument_type="perpetual_swap")
    failure("duplicate_response_rows", "duplicate_public_response_rows", response_rows=2)
    failure("malformed_json", "malformed_public_response", failure_kind="malformed_json")
    failure("incomplete_response", "incomplete_public_response", response_complete=False)
    failure("provider_error_code", "public_provider_error", provider_code="51000")
    failure("timeout", "public_network_timeout", failure_kind="timeout")
    failure("connection_failure", "public_network_connection_failure", failure_kind="connection_failure")
    failure("rate_limit", "public_network_rate_limit", failure_kind="rate_limit")
    failure("partial_page", "partial_public_response", failure_kind="partial_page")
    failure("conflicting_public_sources", "conflicting_public_sources", conflicting_sources=True)
    failure("public_response_replay", "public_response_replay", replayed=True)
    failure("stale_cached_response", "stale_cached_response", cached=True)
    failure("fixture_marked_operational", "fixture_classification_mismatch", fixture_declared_operational=True)
    failure("operational_response_marked_fixture", "operational_classification_mismatch", operational_declared_fixture=True)

    if len(scenarios) != 27:
        raise AssertionError("Phase 8B market catalog must contain exactly 27 scenarios")
    return tuple(scenarios)


def all_shadow_scenarios() -> tuple[ShadowScenarioSpec, ...]:
    scenarios = account_scenarios() + market_failure_scenarios()
    identifiers = tuple(scenario.scenario_id for scenario in scenarios)
    if len(set(identifiers)) != len(identifiers):
        raise AssertionError("Phase 8B shadow scenario identifiers must be globally unique")
    return scenarios


def scenario_by_id(scenario_id: str) -> ShadowScenarioSpec:
    for scenario in all_shadow_scenarios():
        if scenario.scenario_id == scenario_id:
            return scenario
    raise KeyError(f"unknown Phase 8B shadow fixture: {scenario_id}")


__all__ = [
    "SHADOW_FIXTURE_TIME",
    "ShadowScenarioSpec",
    "account_scenarios",
    "all_shadow_scenarios",
    "market_failure_scenarios",
    "scenario_by_id",
]
