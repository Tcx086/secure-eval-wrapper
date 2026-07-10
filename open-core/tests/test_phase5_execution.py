from __future__ import annotations

import unittest
from dataclasses import FrozenInstanceError
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

from secure_eval_wrapper.alpha.identity import SeriesIdentity
from secure_eval_wrapper.backtesting.engine import BacktestEngine
from secure_eval_wrapper.backtesting.models import BacktestConfiguration, BacktestRequest
from secure_eval_wrapper.data_collection.hashing import sha256_payload
from secure_eval_wrapper.data_collection.models import (
    FundingIntervalSource, FundingRate, InstrumentKey, InstrumentType, NormalizedBar,
)
from secure_eval_wrapper.execution.accounting import Portfolio
from secure_eval_wrapper.execution.brokers.simulated import SimulatedBroker
from secure_eval_wrapper.execution.fees import FixedBasisPointFeeModel, ZeroFeeModel
from secure_eval_wrapper.execution.funding import funding_payment_for_position
from secure_eval_wrapper.execution.models import (
    AccountingMode, BrokerConfiguration, FeeConfiguration, LedgerEntryType, LiquidityFlag,
    OrderIntent, OrderIntentStatus, OrderSide, OrderStatus, OrderType, PositionState,
    RiskDecision, RiskDecisionStatus, RiskLimitConfiguration, RiskStage,
    SlippageConfiguration, TimeInForce,
)
from secure_eval_wrapper.execution.positions import apply_fill_to_position, empty_position, unrealized_pnl
from secure_eval_wrapper.execution.risk.guard import PortfolioRiskView, RiskGuard
from secure_eval_wrapper.execution.sizing import SizingConfiguration, SizingMode, size_signal
from secure_eval_wrapper.execution.slippage import FixedAdverseBasisPointSlippage, ZeroSlippage
from secure_eval_wrapper.signals.models import SignalDirection, StandardizedSignal

UTC = timezone.utc
T0 = datetime(2025, 1, 1, tzinfo=UTC)
H = sha256_payload({"phase": 5})
RUN = uuid5(NAMESPACE_URL, "phase5-test-run")


def instrument(kind=InstrumentType.SPOT, *, provider="fixture", exchange="fixture-x", provider_id="BTC-USDT", timeframe="1m"):
    settlement = "USDT" if kind is InstrumentType.PERPETUAL_SWAP else None
    key = InstrumentKey(provider, exchange, provider_id, "BTC", "USDT", kind, "BTC-USDT", settlement)
    identity = SeriesIdentity(provider, exchange, provider_id, "BTC-USDT", kind, timeframe, settlement)
    return key, identity


def bar(index, open_, high, low, close, *, kind=InstrumentType.SPOT, provider="fixture", exchange="fixture-x", provider_id="BTC-USDT", source_id=None):
    key, _ = instrument(kind, provider=provider, exchange=exchange, provider_id=provider_id)
    opened = T0 + timedelta(minutes=index)
    return NormalizedBar(
        uuid5(NAMESPACE_URL, f"bar:{provider}:{provider_id}:{index}:{source_id}"), "BTC-USDT", exchange, "1m",
        opened, Decimal(open_), Decimal(high), Decimal(low), Decimal(close), Decimal("10"),
        (source_id or uuid5(NAMESPACE_URL, f"source:{provider}:{provider_id}:{index}"),),
        opened + timedelta(minutes=1), True,
        {"provider_name": provider, "provider_instrument_id": provider_id, "instrument_type": kind.value, "settlement_asset": "USDT" if kind is InstrumentType.PERPETUAL_SWAP else None}, key,
    )


def signal(index, direction, *, kind=InstrumentType.SPOT, provider="fixture", exchange="fixture-x", provider_id="BTC-USDT"):
    _, identity = instrument(kind, provider=provider, exchange=exchange, provider_id=provider_id)
    timestamp = T0 + timedelta(minutes=index)
    sid = uuid5(NAMESPACE_URL, f"signal:{identity.series_identity_sha256}:{index}:{direction}")
    return StandardizedSignal(sid, uuid5(NAMESPACE_URL, "signal-run"), ("momentum:1.1.0",), (uuid5(NAMESPACE_URL, "alpha-run"),), identity.canonical_symbol, timestamp, SignalDirection(direction), Decimal(1) if direction == "long" else Decimal(-1) if direction == "short" else Decimal(0), Decimal(1) if direction == "long" else Decimal(-1) if direction == "short" else Decimal(0), None, None, Decimal("0.8") if direction != "flat" else Decimal(0), "1m", (uuid5(NAMESPACE_URL, f"alpha-value:{index}"),), H, H, H, {"public_safe": True}, identity)


def config(*, mode=SizingMode.FIXED_QUANTITY, target="1", order_type=OrderType.MARKET, tif=TimeInForce.GTC, fees="0", slip="0", risk=None, initial="1000", base_currency="USDT", fee_currency=None, account_ref="public-simulation", **offsets):
    return BacktestConfiguration(
        Decimal(initial), base_currency, SizingConfiguration(mode, Decimal(target)),
        broker=BrokerConfiguration(account_ref=account_ref),
        fees=FeeConfiguration(Decimal(fees), Decimal(fees), fee_currency or base_currency),
        slippage=SlippageConfiguration(Decimal(slip)), risk_limits=risk or RiskLimitConfiguration(),
        order_type=order_type, time_in_force=tif,
        limit_offset_bps=Decimal(offsets.get("limit", 0)), stop_offset_bps=Decimal(offsets.get("stop", 0)),
        stop_limit_offset_bps=Decimal(offsets.get("stop_limit", 0)),
    )


def run_engine(bars, signals, *, funding=(), configuration=None, run_id=None, implementation_code_sha256=H, repository_commit_sha="test-tree"):
    request = BacktestRequest(run_id, tuple(bars), tuple(signals), tuple(funding), configuration or config(), implementation_code_sha256, repository_commit_sha, signals[0].signal_run_id if signals else None)
    return BacktestEngine().run(request)


def intent(order_type=OrderType.MARKET, *, side=OrderSide.BUY, qty="1", limit=None, stop=None, tif=TimeInForce.GTC, kind=InstrumentType.SPOT, submitted=T0 + timedelta(minutes=1)):
    _, identity = instrument(kind)
    delta = Decimal(qty) * side.sign
    return OrderIntent(RUN, uuid5(NAMESPACE_URL, "intent-signal"), identity, submitted, side, order_type, Decimal(qty), delta, Decimal(0), delta, Decimal("100"), AccountingMode.SPOT if kind is InstrumentType.SPOT else AccountingMode.LINEAR_PERPETUAL, tif, H, H, H, "test-tree", None if limit is None else Decimal(limit), None if stop is None else Decimal(stop))


def accepted(value):
    return RiskDecision(value.run_id, value.order_intent_id, value.series_identity, value.event_timestamp_utc, RiskStage.PRE_SUBMIT, RiskDecisionStatus.ACCEPTED, "accepted", "accepted", RiskLimitConfiguration().config_sha256)


def always_accept(order, price, liquidity, fee):
    return RiskDecision(order.run_id, order.order_intent_id, order.series_identity, T0 + timedelta(minutes=2), RiskStage.PRE_FILL, RiskDecisionStatus.ACCEPTED, "accepted", "accepted", RiskLimitConfiguration().config_sha256, order_id=order.order_id)


class ContractSizingFeeSlippageTests(unittest.TestCase):
    def test_contracts_are_frozen_and_ids_are_deterministic(self):
        value = intent()
        with self.assertRaises(FrozenInstanceError):
            value.quantity = Decimal(2)
        self.assertEqual(value.order_intent_id, intent().order_intent_id)
        self.assertEqual(value.record_sha256, intent().record_sha256)

    def test_mutable_provenance_does_not_change_intent_hash(self):
        first = intent()
        second = OrderIntent(**{**first.__dict__, "provenance": {"source_observation_id": str(uuid4())}, "order_intent_id": None})
        self.assertEqual(first.record_sha256, second.record_sha256)

    def test_fixed_quantity_long_short_flat(self):
        spot_signal = signal(1, "long")
        sized = size_signal(spot_signal, current_quantity=Decimal(0), reference_price=Decimal(100), accounting_mode=AccountingMode.SPOT, configuration=SizingConfiguration(SizingMode.FIXED_QUANTITY, Decimal(2)))
        self.assertEqual((sized.target_quantity, sized.delta_quantity, sized.side), (Decimal(2), Decimal(2), OrderSide.BUY))
        flat = size_signal(signal(1, "flat"), current_quantity=Decimal(2), reference_price=Decimal(100), accounting_mode=AccountingMode.SPOT, configuration=SizingConfiguration(SizingMode.FIXED_QUANTITY, Decimal(2)))
        self.assertEqual((flat.target_quantity, flat.delta_quantity, flat.side), (Decimal(0), Decimal(-2), OrderSide.SELL))
        short = size_signal(signal(1, "short", kind=InstrumentType.PERPETUAL_SWAP), current_quantity=Decimal(0), reference_price=Decimal(100), accounting_mode=AccountingMode.LINEAR_PERPETUAL, configuration=SizingConfiguration(SizingMode.FIXED_QUANTITY, Decimal(2)))
        self.assertEqual(short.target_quantity, Decimal(-2))

    def test_fixed_notional_rounds_down_and_can_no_action(self):
        sized = size_signal(signal(1, "long"), current_quantity=Decimal(0), reference_price=Decimal(30), accounting_mode=AccountingMode.SPOT, configuration=SizingConfiguration(SizingMode.FIXED_NOTIONAL, Decimal(100), Decimal("0.1")))
        self.assertEqual(sized.target_quantity, Decimal("3.3"))
        zero = size_signal(signal(1, "long"), current_quantity=Decimal(0), reference_price=Decimal(1000), accounting_mode=AccountingMode.SPOT, configuration=SizingConfiguration(SizingMode.FIXED_NOTIONAL, Decimal(1), Decimal("0.1")))
        self.assertTrue(zero.is_no_action)
        self.assertEqual(zero.no_action_reason, "rounded_target_zero")

    def test_spot_short_sizing_preserves_requested_negative_target_for_risk(self):
        sized = size_signal(signal(1, "short"), current_quantity=Decimal(0), reference_price=Decimal(100), accounting_mode=AccountingMode.SPOT, configuration=SizingConfiguration(SizingMode.FIXED_QUANTITY, Decimal(1)))
        self.assertEqual((sized.target_quantity, sized.delta_quantity, sized.side), (Decimal(-1), Decimal(-1), OrderSide.SELL))

    def test_fee_and_slippage_exact(self):
        fee = FixedBasisPointFeeModel(FeeConfiguration(Decimal(1), Decimal(2), "USDT"))
        self.assertEqual(fee.calculate(price=Decimal(100), quantity=Decimal(2), liquidity=LiquidityFlag.MAKER), Decimal("0.02"))
        self.assertEqual(fee.calculate(price=Decimal(100), quantity=Decimal(2), liquidity=LiquidityFlag.TAKER), Decimal("0.04"))
        slip = FixedAdverseBasisPointSlippage(SlippageConfiguration(Decimal(100)))
        self.assertEqual(slip.apply(base_price=Decimal(100), side=OrderSide.BUY), (Decimal(101), Decimal(1)))
        self.assertEqual(slip.apply(base_price=Decimal(100), side=OrderSide.SELL), (Decimal(99), Decimal(1)))

    def test_negative_nonfinite_configs_fail(self):
        for constructor in (lambda: FeeConfiguration(Decimal(-1), Decimal(0)), lambda: SlippageConfiguration(Decimal("NaN")), lambda: SizingConfiguration(SizingMode.FIXED_QUANTITY, Decimal(0))):
            with self.subTest(constructor=constructor), self.assertRaises(ValueError): constructor()


class SimulatedBrokerOrderSemanticsTests(unittest.TestCase):
    def broker(self, slip="0"):
        return SimulatedBroker(BrokerConfiguration(), fee_model=ZeroFeeModel(), slippage_model=FixedAdverseBasisPointSlippage(SlippageConfiguration(Decimal(slip))))

    def submit(self, value, slip="0"):
        broker = self.broker(slip)
        broker.submit_order_intent(value, accepted(value))
        return broker

    def test_market_buy_and_sell_fill_next_open_with_adverse_slippage(self):
        buy = intent(side=OrderSide.BUY)
        result = self.submit(buy, "100").process_bar_open(series_identity=buy.series_identity, timestamp_utc=buy.event_timestamp_utc, open_price=Decimal(100), risk_check=always_accept)
        self.assertEqual((result.fills[0].price, result.fills[0].liquidity_flag), (Decimal(101), LiquidityFlag.TAKER))
        sell = intent(side=OrderSide.SELL, kind=InstrumentType.PERPETUAL_SWAP)
        result = self.submit(sell, "100").process_bar_open(series_identity=sell.series_identity, timestamp_utc=sell.event_timestamp_utc, open_price=Decimal(100), risk_check=always_accept)
        self.assertEqual(result.fills[0].price, Decimal(99))

    def test_limit_favorable_gap_and_intrabar_cross(self):
        value = intent(OrderType.LIMIT, limit="100")
        gap = self.submit(value).process_bar_open(series_identity=value.series_identity, timestamp_utc=value.event_timestamp_utc, open_price=Decimal(90), risk_check=always_accept)
        self.assertEqual((gap.fills[0].price, gap.fills[0].fill_reason), (Decimal(90), "limit_open_gap"))
        broker = self.submit(value)
        broker.process_bar_open(series_identity=value.series_identity, timestamp_utc=value.event_timestamp_utc, open_price=Decimal(110), risk_check=always_accept)
        crossed = broker.process_completed_bar(series_identity=value.series_identity, timestamp_utc=value.event_timestamp_utc + timedelta(minutes=1), open_price=Decimal(110), high=Decimal(112), low=Decimal(99), close=Decimal(105), risk_check=always_accept)
        self.assertEqual((crossed.fills[0].price, crossed.fills[0].liquidity_flag), (Decimal(100), LiquidityFlag.MAKER))

    def test_unfilled_limit_stays_gtc_and_ioc_expires(self):
        gtc = intent(OrderType.LIMIT, limit="90")
        broker = self.submit(gtc)
        result = broker.process_bar_open(series_identity=gtc.series_identity, timestamp_utc=gtc.event_timestamp_utc, open_price=Decimal(100), risk_check=always_accept)
        self.assertFalse(result.fills)
        self.assertEqual(len(broker.active_orders()), 1)
        ioc = intent(OrderType.LIMIT, limit="90", tif=TimeInForce.IOC)
        result = self.submit(ioc).process_bar_open(series_identity=ioc.series_identity, timestamp_utc=ioc.event_timestamp_utc, open_price=Decimal(100), risk_check=always_accept)
        self.assertEqual(result.order_updates[-1].status, OrderStatus.EXPIRED)

    def test_stop_gap_is_never_improved_and_intrabar_uses_stop(self):
        value = intent(OrderType.STOP, stop="100")
        gap = self.submit(value, "100").process_bar_open(series_identity=value.series_identity, timestamp_utc=value.event_timestamp_utc, open_price=Decimal(110), risk_check=always_accept)
        self.assertEqual(gap.fills[0].price, Decimal("111.1"))
        broker = self.submit(value)
        broker.process_bar_open(series_identity=value.series_identity, timestamp_utc=value.event_timestamp_utc, open_price=Decimal(95), risk_check=always_accept)
        crossed = broker.process_completed_bar(series_identity=value.series_identity, timestamp_utc=value.event_timestamp_utc + timedelta(minutes=1), open_price=Decimal(95), high=Decimal(101), low=Decimal(94), close=Decimal(99), risk_check=always_accept)
        self.assertEqual(crossed.fills[0].base_price, Decimal(100))

    def test_stop_limit_open_and_intrabar_deferred(self):
        value = intent(OrderType.STOP_LIMIT, stop="100", limit="105")
        opened = self.submit(value).process_bar_open(series_identity=value.series_identity, timestamp_utc=value.event_timestamp_utc, open_price=Decimal(102), risk_check=always_accept)
        self.assertEqual((opened.fills[0].price, opened.fills[0].fill_reason), (Decimal(102), "stop_limit_open"))
        broker = self.submit(value)
        broker.process_bar_open(series_identity=value.series_identity, timestamp_utc=value.event_timestamp_utc, open_price=Decimal(95), risk_check=always_accept)
        triggered = broker.process_completed_bar(series_identity=value.series_identity, timestamp_utc=value.event_timestamp_utc + timedelta(minutes=1), open_price=Decimal(95), high=Decimal(103), low=Decimal(94), close=Decimal(101), risk_check=always_accept)
        self.assertFalse(triggered.fills)
        self.assertEqual(triggered.order_updates[0].activation_reason, "intrabar_trigger_deferred")
        filled = broker.process_bar_open(series_identity=value.series_identity, timestamp_utc=value.event_timestamp_utc + timedelta(minutes=1), open_price=Decimal(104), risk_check=always_accept)
        self.assertEqual(filled.fills[0].price, Decimal(104))

    def test_superseding_cancel_and_run_end_expiration(self):
        value = intent(OrderType.LIMIT, limit="90")
        broker = self.submit(value)
        cancelled = broker.cancel_order(value=1, cancelled_at_utc=value.event_timestamp_utc, reason="bad") if False else broker.cancel_order(next(iter(broker.active_orders())).order_id, cancelled_at_utc=value.event_timestamp_utc, reason="superseded")
        self.assertEqual(cancelled.order_updates[0].status, OrderStatus.CANCELLED)
        broker = self.submit(value)
        expired = broker.expire_remaining_orders(expired_at_utc=value.event_timestamp_utc + timedelta(minutes=2))
        self.assertEqual(expired.order_updates[0].status, OrderStatus.EXPIRED)

    def test_blocked_prefill_creates_no_fill(self):
        value = intent()
        broker = self.submit(value)
        def block(order, price, liquidity, fee):
            return RiskDecision(order.run_id, order.order_intent_id, order.series_identity, value.event_timestamp_utc, RiskStage.PRE_FILL, RiskDecisionStatus.BLOCKED, "gap", "blocked", RiskLimitConfiguration().config_sha256, order_id=order.order_id)
        result = broker.process_bar_open(series_identity=value.series_identity, timestamp_utc=value.event_timestamp_utc, open_price=Decimal(100), risk_check=block)
        self.assertFalse(result.fills)
        self.assertEqual(result.order_updates[0].status, OrderStatus.REJECTED)


class PositionAccountingFundingTests(unittest.TestCase):
    def fill(self, state, *, side, qty, price, minute):
        value = intent(side=side, qty=qty, kind=state.series_identity.instrument_type, submitted=T0 + timedelta(minutes=minute))
        from secure_eval_wrapper.execution.models import Fill
        return Fill(RUN, uuid5(NAMESPACE_URL, f"order:{minute}"), value.order_intent_id, state.series_identity, T0 + timedelta(minutes=minute), side, Decimal(qty), Decimal(price), Decimal(price), state.accounting_mode, LiquidityFlag.TAKER, Decimal(0), "USDT", Decimal(0), Decimal(0), "test", H)

    def test_perpetual_all_ten_position_transitions(self):
        _, identity = instrument(InstrumentType.PERPETUAL_SWAP)
        state = empty_position(run_id=RUN, series_identity=identity, accounting_mode=AccountingMode.LINEAR_PERPETUAL, timestamp_utc=T0, config_sha256=H)
        state = apply_fill_to_position(state, self.fill(state, side=OrderSide.BUY, qty="2", price="100", minute=1)).state
        self.assertEqual((state.quantity, state.average_entry_price), (Decimal(2), Decimal(100)))
        state = apply_fill_to_position(state, self.fill(state, side=OrderSide.BUY, qty="2", price="120", minute=2)).state
        self.assertEqual(state.average_entry_price, Decimal(110))
        reduced = apply_fill_to_position(state, self.fill(state, side=OrderSide.SELL, qty="1", price="130", minute=3)); state = reduced.state
        self.assertEqual(reduced.realized_pnl_delta, Decimal(20))
        reversed_ = apply_fill_to_position(state, self.fill(state, side=OrderSide.SELL, qty="4", price="90", minute=4)); state = reversed_.state
        self.assertEqual((state.quantity, state.average_entry_price, reversed_.realized_pnl_delta), (Decimal(-1), Decimal(90), Decimal(-60)))
        state = apply_fill_to_position(state, self.fill(state, side=OrderSide.SELL, qty="1", price="80", minute=5)).state
        self.assertEqual(state.average_entry_price, Decimal(85))
        state = apply_fill_to_position(state, self.fill(state, side=OrderSide.BUY, qty="1", price="70", minute=6)).state
        self.assertEqual(state.quantity, Decimal(-1))
        state = apply_fill_to_position(state, self.fill(state, side=OrderSide.BUY, qty="1", price="75", minute=7)).state
        self.assertEqual((state.quantity, state.average_entry_price), (Decimal(0), None))
        state = apply_fill_to_position(state, self.fill(state, side=OrderSide.SELL, qty="1", price="75", minute=8)).state
        reversed_ = apply_fill_to_position(state, self.fill(state, side=OrderSide.BUY, qty="2", price="65", minute=9))
        self.assertEqual((reversed_.state.quantity, reversed_.state.average_entry_price, reversed_.realized_pnl_delta), (Decimal(1), Decimal(65), Decimal(10)))

    def test_spot_cash_inventory_fee_and_replay(self):
        _, identity = instrument()
        portfolio = Portfolio(run_id=RUN, account_ref="public-simulation", initial_cash=Decimal(1000), base_currency="USDT", config_sha256=H, started_at_utc=T0)
        state = portfolio.position(identity, AccountingMode.SPOT, T0)
        from secure_eval_wrapper.execution.models import Fill
        buy = Fill(RUN, uuid4(), uuid4(), identity, T0 + timedelta(minutes=1), OrderSide.BUY, Decimal(2), Decimal(100), Decimal(100), AccountingMode.SPOT, LiquidityFlag.TAKER, Decimal(1), "USDT", Decimal(0), Decimal(0), "test", H)
        portfolio.apply_fill(buy, source_event_id=uuid4(), logical_sequence=1)
        self.assertEqual(portfolio.cash, Decimal(799))
        self.assertEqual(portfolio.positions[identity.series_identity_sha256].quantity, Decimal(2))
        self.assertEqual([row.entry_type for row in portfolio.ledger], [LedgerEntryType.INITIAL_CASH, LedgerEntryType.SPOT_NOTIONAL, LedgerEntryType.FEE])
        with self.assertRaises(ValueError): portfolio.apply_fill(buy, source_event_id=uuid4(), logical_sequence=2)

    def test_perpetual_realized_unrealized_and_fee_reconcile(self):
        _, identity = instrument(InstrumentType.PERPETUAL_SWAP)
        state = empty_position(run_id=RUN, series_identity=identity, accounting_mode=AccountingMode.LINEAR_PERPETUAL, timestamp_utc=T0, config_sha256=H)
        state = apply_fill_to_position(state, self.fill(state, side=OrderSide.BUY, qty="2", price="100", minute=1)).state
        self.assertEqual(unrealized_pnl(state, Decimal(110)), Decimal(20))
        closed = apply_fill_to_position(state, self.fill(state, side=OrderSide.SELL, qty="2", price="110", minute=2))
        self.assertEqual((closed.realized_pnl_delta, closed.state.quantity), (Decimal(20), Decimal(0)))

    def test_funding_long_short_negative_and_spot(self):
        key, identity = instrument(InstrumentType.PERPETUAL_SWAP)
        rate = FundingRate(uuid4(), "BTC-USDT", "fixture-x", T0 + timedelta(minutes=1), Decimal("0.001"), (uuid4(),), "1h", FundingIntervalSource.METADATA_REPORTED, instrument_key=key)
        long = PositionState(RUN, "public-simulation", identity, AccountingMode.LINEAR_PERPETUAL, Decimal(2), Decimal(100), Decimal(0), T0, H)
        short = PositionState(RUN, "public-simulation", identity, AccountingMode.LINEAR_PERPETUAL, Decimal(-2), Decimal(100), Decimal(0), T0, H)
        self.assertEqual(funding_payment_for_position(rate, position=long, mark_price=Decimal(100), config_sha256=H).cash_flow, Decimal("-0.2"))
        self.assertEqual(funding_payment_for_position(rate, position=short, mark_price=Decimal(100), config_sha256=H).cash_flow, Decimal("0.2"))
        negative = FundingRate(uuid4(), "BTC-USDT", "fixture-x", T0 + timedelta(minutes=1), Decimal("-0.001"), (uuid4(),), "4h", FundingIntervalSource.PROVIDER_REPORTED, instrument_key=key)
        self.assertEqual(funding_payment_for_position(negative, position=long, mark_price=Decimal(100), config_sha256=H).cash_flow, Decimal("0.2"))
        spot_key, spot_identity = instrument()
        spot = PositionState(RUN, "public-simulation", spot_identity, AccountingMode.SPOT, Decimal(1), Decimal(100), Decimal(0), T0, H)
        self.assertIsNone(funding_payment_for_position(rate, position=spot, mark_price=Decimal(100), config_sha256=H))


class RiskAndEngineTests(unittest.TestCase):
    def risk(self, limits, *, value=None, price="100", cash="1000", equity="1000", peak="1000", positions=None, fee="0", stage=RiskStage.PRE_SUBMIT):
        value = value or intent()
        return RiskGuard(limits).assess(value, price=Decimal(price), stage=stage, decision_timestamp_utc=value.event_timestamp_utc, portfolio=PortfolioRiskView(Decimal(cash), Decimal(equity), Decimal(peak), positions or {}, {}, {}), fee_amount=Decimal(fee))

    def test_risk_accept_and_order_position_limits(self):
        self.assertEqual(self.risk(RiskLimitConfiguration()).status, RiskDecisionStatus.ACCEPTED)
        self.assertEqual(self.risk(RiskLimitConfiguration(max_order_notional=Decimal(50))).reason_code, "max_order_notional")
        self.assertEqual(self.risk(RiskLimitConfiguration(max_position_notional_per_series=Decimal(50))).reason_code, "max_position_notional")

    def test_risk_spot_short_cash_and_drawdown(self):
        sell = intent(side=OrderSide.SELL)
        self.assertEqual(self.risk(RiskLimitConfiguration(), value=sell).reason_code, "spot_short_prohibited")
        self.assertEqual(self.risk(RiskLimitConfiguration(), cash="99", fee="1").reason_code, "insufficient_cash")
        self.assertEqual(self.risk(RiskLimitConfiguration(max_drawdown_fraction=Decimal("0.1")), equity="80", peak="100").reason_code, "max_drawdown")

    def test_prefill_gap_catches_order_notional_breach(self):
        result = run_engine([bar(0, "100", "110", "90", "100"), bar(1, "120", "125", "115", "120")], [signal(1, "long")], configuration=config(risk=RiskLimitConfiguration(max_order_notional=Decimal(110))))
        self.assertEqual((result.metrics.fill_count, result.metrics.reject_count), (0, 1))
        self.assertTrue(any(row.stage is RiskStage.PRE_FILL and row.status is RiskDecisionStatus.BLOCKED for row in result.risk_decisions))

    def test_engine_no_same_bar_and_hand_calculated_spot_metrics(self):
        bars = [bar(0, "100", "112", "98", "110"), bar(1, "120", "125", "118", "120"), bar(2, "130", "132", "128", "130")]
        result = run_engine(bars, [signal(1, "long"), signal(2, "flat")], configuration=config(fees="10", slip="100"))
        self.assertEqual([row.filled_at_utc for row in result.fills], [T0 + timedelta(minutes=1), T0 + timedelta(minutes=2)])
        self.assertEqual([row.price for row in result.fills], [Decimal("121.2"), Decimal("128.7")])
        self.assertEqual(result.metrics.final_cash, Decimal("1007.2501"))
        self.assertEqual(result.metrics.net_pnl, Decimal("7.2501"))
        self.assertEqual(result.metrics.total_fees, Decimal("0.2499"))
        self.assertEqual(result.metrics.final_open_position_count, 0)

    def test_funding_precedes_same_timestamp_signal_open(self):
        key, _ = instrument(InstrumentType.PERPETUAL_SWAP)
        rate = FundingRate(uuid4(), "BTC-USDT", "fixture-x", T0 + timedelta(minutes=1), Decimal("0.001"), (uuid4(),), "1h", FundingIntervalSource.METADATA_REPORTED, instrument_key=key)
        result = run_engine([bar(0, "100", "101", "99", "100", kind=InstrumentType.PERPETUAL_SWAP), bar(1, "100", "101", "99", "100", kind=InstrumentType.PERPETUAL_SWAP)], [signal(1, "long", kind=InstrumentType.PERPETUAL_SWAP)], funding=[rate])
        self.assertEqual(result.metrics.fill_count, 1)
        self.assertEqual(result.metrics.funding_payment_count, 0)

    def test_missing_candle_waits_and_final_position_is_not_liquidated(self):
        bars = [bar(0, "100", "101", "99", "100"), bar(3, "105", "106", "104", "105")]
        result = run_engine(bars, [signal(1, "long")])
        self.assertEqual(result.fills[0].filled_at_utc, T0 + timedelta(minutes=3))
        self.assertEqual(result.metrics.final_open_position_count, 1)

    def test_market_without_later_bar_expires(self):
        result = run_engine([bar(0, "100", "101", "99", "100")], [signal(1, "long")])
        self.assertEqual((result.metrics.fill_count, result.metrics.expired_order_count), (0, 1))

    def test_metrics_have_undefined_values_not_convenience_zero(self):
        result = run_engine([bar(0, "100", "101", "99", "100")], [])
        self.assertIsNone(result.metrics.win_rate)
        self.assertIsNone(result.metrics.profit_factor)
        self.assertEqual(result.metrics.net_pnl, 0)


if __name__ == "__main__":
    unittest.main()
