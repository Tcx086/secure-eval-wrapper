from __future__ import annotations

import random
import unittest
from datetime import timedelta
from decimal import Decimal
from uuid import NAMESPACE_URL, uuid4, uuid5

from secure_eval_wrapper.alpha.identity import SeriesIdentity
from secure_eval_wrapper.backtesting.engine import BacktestEngine
from secure_eval_wrapper.backtesting.models import BacktestRequest
from secure_eval_wrapper.data_collection.models import InstrumentKey, InstrumentType, NormalizedBar
from secure_eval_wrapper.execution.models import OrderStatus, OrderType
from secure_eval_wrapper.signals.models import SignalDirection, StandardizedSignal

from test_phase5_execution import H, RUN, T0, bar, config, run_engine, signal


class AntiLookaheadAndInvarianceTests(unittest.TestCase):
    def baseline(self):
        bars = [bar(0, "100", "101", "99", "100"), bar(1, "105", "106", "104", "105"), bar(2, "110", "111", "109", "110")]
        return bars, [signal(1, "long")]

    @staticmethod
    def historical_signature(result, cutoff):
        fills = tuple((row.fill_id, row.record_sha256, row.filled_at_utc, row.price) for row in result.fills if row.filled_at_utc <= cutoff)
        equity = tuple((row.equity_curve_id, row.record_sha256, row.timestamp_utc, row.equity) for row in result.equity_curve if row.timestamp_utc <= cutoff)
        events = tuple((row.execution_event_id, row.record_sha256) for row in result.events if row.event_timestamp_utc <= cutoff)
        return fills, equity, events

    def test_future_append_does_not_change_historical_records(self):
        bars, signals = self.baseline()
        first = run_engine(bars, signals)
        second = run_engine(bars + [bar(4, "150", "151", "149", "150")], signals)
        self.assertEqual(self.historical_signature(first, T0 + timedelta(minutes=3)), self.historical_signature(second, T0 + timedelta(minutes=3)))

    def test_future_mutation_does_not_change_historical_records(self):
        bars, signals = self.baseline()
        first = run_engine(bars + [bar(4, "150", "151", "149", "150")], signals)
        second = run_engine(bars + [bar(4, "250", "260", "240", "255")], signals)
        self.assertEqual(self.historical_signature(first, T0 + timedelta(minutes=3)), self.historical_signature(second, T0 + timedelta(minutes=3)))

    def test_future_deletion_does_not_change_historical_records(self):
        bars, signals = self.baseline()
        with_future = run_engine(bars + [bar(4, "150", "151", "149", "150")], signals)
        without_future = run_engine(bars, signals)
        self.assertEqual(self.historical_signature(with_future, T0 + timedelta(minutes=3)), self.historical_signature(without_future, T0 + timedelta(minutes=3)))

    def test_recollection_source_ids_do_not_change_execution(self):
        bars, signals = self.baseline()
        recollected = [bar(index, str(value.open), str(value.high), str(value.low), str(value.close), source_id=uuid4()) for index, value in enumerate(bars)]
        first, second = run_engine(bars, signals), run_engine(recollected, signals)
        self.assertEqual([(row.fill_id, row.record_sha256) for row in first.fills], [(row.fill_id, row.record_sha256) for row in second.fills])
        self.assertEqual(first.run.data_sha256, second.run.data_sha256)

    def test_shuffled_inputs_are_identical(self):
        bars, signals = self.baseline()
        shuffled = list(bars)
        random.Random(7).shuffle(shuffled)
        first, second = run_engine(bars, signals), run_engine(shuffled, list(reversed(signals)))
        self.assertEqual(first.fills, second.fills)
        self.assertEqual(first.equity_curve, second.equity_curve)
        self.assertEqual(first.events, second.events)

    def test_duplicate_bar_and_signal_events_fail(self):
        bars, signals = self.baseline()
        with self.assertRaises(ValueError): run_engine(bars + [bars[0]], signals)
        with self.assertRaises(ValueError): run_engine(bars, signals + signals)

    def test_nonfinal_bars_fail(self):
        value = bar(0, "100", "101", "99", "100")
        invalid = NormalizedBar(value.bar_id, value.symbol, value.exchange, value.timeframe, value.bar_open_time_utc, value.open, value.high, value.low, value.close, value.volume, value.source_observation_ids, value.bar_close_time_utc, False, value.provenance, value.instrument_key)
        with self.assertRaises(ValueError): run_engine([invalid], [])

    def test_no_fills_means_no_trading_pnl(self):
        result = run_engine([bar(0, "100", "101", "99", "100")], [])
        self.assertEqual((result.metrics.fill_count, result.metrics.net_pnl, result.metrics.gross_pnl), (0, Decimal(0), Decimal(0)))

    def test_missing_candle_stale_age_is_explicit(self):
        result = run_engine([bar(0, "100", "101", "99", "100"), bar(3, "105", "106", "104", "105")], [signal(1, "long")])
        at_fill = [row for row in result.position_snapshots if row.source_fill_id is not None][0]
        self.assertEqual(at_fill.stale_mark_age_seconds, Decimal("120.0"))
        self.assertTrue(any(row.stale_mark_count > 0 for row in result.account_snapshots))

    def test_superseding_signal_cancels_active_order(self):
        bars = [bar(0, "100", "101", "99", "100"), bar(1, "100", "101", "99", "100"), bar(2, "100", "101", "99", "100")]
        result = run_engine(bars, [signal(1, "long"), signal(2, "flat")], configuration=config(order_type=OrderType.LIMIT, limit="5000"))
        self.assertEqual(result.metrics.cancel_count, 1)
        self.assertEqual(result.orders[0].status, OrderStatus.CANCELLED)


class MultiSeriesIdentityTests(unittest.TestCase):
    def custom_bar(self, exchange, provider_id, index, timeframe="1m"):
        provider = exchange
        key = InstrumentKey(provider, exchange, provider_id, "BTC", "USDT", InstrumentType.SPOT, "BTC-USDT")
        opened = T0 + timedelta(minutes=index)
        duration = timedelta(minutes=1 if timeframe == "1m" else 5)
        return NormalizedBar(uuid5(NAMESPACE_URL, f"{exchange}:{provider_id}:{timeframe}:{index}"), "BTC-USDT", exchange, timeframe, opened, Decimal(100), Decimal(101), Decimal(99), Decimal(100), Decimal(10), (uuid4(),), opened + duration, True, {"provider_name": provider, "provider_instrument_id": provider_id, "instrument_type": "spot"}, key)

    def custom_signal(self, exchange, provider_id, timestamp, timeframe="1m"):
        identity = SeriesIdentity(exchange, exchange, provider_id, "BTC-USDT", InstrumentType.SPOT, timeframe)
        return StandardizedSignal(uuid5(NAMESPACE_URL, f"sig:{identity.series_identity_sha256}:{timestamp}"), uuid5(NAMESPACE_URL, "multi-signal-run"), ("a:1",), (uuid4(),), "BTC-USDT", timestamp, SignalDirection.LONG, Decimal(1), Decimal(1), None, None, Decimal(1), timeframe, (uuid4(),), H, H, H, {}, identity)

    def test_same_symbol_two_exchanges_remain_separate(self):
        bars = [self.custom_bar("exchange-a", "BTCUSDT", 0), self.custom_bar("exchange-a", "BTCUSDT", 1), self.custom_bar("exchange-b", "BTC-USDT", 0), self.custom_bar("exchange-b", "BTC-USDT", 1)]
        signals = [self.custom_signal("exchange-a", "BTCUSDT", T0 + timedelta(minutes=1)), self.custom_signal("exchange-b", "BTC-USDT", T0 + timedelta(minutes=1))]
        result = run_engine(bars, signals)
        self.assertEqual(len(result.positions), 2)
        self.assertEqual(len({row.series_identity.series_identity_sha256 for row in result.positions}), 2)
        self.assertEqual(result.metrics.maximum_gross_exposure, Decimal(200))

    def test_two_timeframes_remain_separate(self):
        one = self.custom_bar("exchange-a", "BTCUSDT", 0, "1m")
        one_next = self.custom_bar("exchange-a", "BTCUSDT", 1, "1m")
        five = self.custom_bar("exchange-a", "BTCUSDT", 0, "5m")
        five_next = self.custom_bar("exchange-a", "BTCUSDT", 5, "5m")
        signals = [self.custom_signal("exchange-a", "BTCUSDT", T0 + timedelta(minutes=1), "1m"), self.custom_signal("exchange-a", "BTCUSDT", T0 + timedelta(minutes=5), "5m")]
        result = run_engine([one, one_next, five, five_next], signals)
        self.assertEqual(len(result.positions), 2)


if __name__ == "__main__":
    unittest.main()
