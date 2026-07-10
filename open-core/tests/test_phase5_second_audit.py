from __future__ import annotations

import json
import unittest
from dataclasses import replace
from datetime import timedelta
from decimal import Decimal
from pathlib import Path
from uuid import NAMESPACE_URL, uuid4, uuid5

from secure_eval_wrapper.backtesting.engine import BacktestEngine
from secure_eval_wrapper.backtesting.models import BacktestConfiguration, BacktestRequest, BacktestRunStatus
from secure_eval_wrapper.data_collection.hashing import sha256_payload
from secure_eval_wrapper.data_collection.models import FundingIntervalSource, FundingRate, InstrumentKey, InstrumentType, NormalizedBar
from secure_eval_wrapper.execution.accounting import Portfolio
from secure_eval_wrapper.execution.models import (
    AccountingMode, FeeConfiguration, Fill, LedgerEntryType, LiquidityFlag, MarkSource,
    OrderSide, PositionSnapshotKind, RiskDecisionStatus, RiskStage,
)
from secure_eval_wrapper.execution.sizing import SizingConfiguration, SizingMode
from secure_eval_wrapper.storage.postgres.phase5_repositories import Phase5ConflictError, PostgresPhase5Repository
from secure_eval_wrapper.validation import content_boundary_findings, tracked_path_boundary_findings

from test_phase5_execution import H, RUN, T0, bar, config, instrument, run_engine, signal
from test_phase5_persistence_package import FakeConnection

ROOT = Path(__file__).resolve().parents[2]


def funding_rate(minute: int, rate: str = "0.001") -> FundingRate:
    key, _ = instrument(InstrumentType.PERPETUAL_SWAP)
    return FundingRate(
        uuid5(NAMESPACE_URL, f"second-audit-funding:{minute}:{rate}"),
        "BTC-USDT", "fixture-x", T0 + timedelta(minutes=minute), Decimal(rate),
        (uuid5(NAMESPACE_URL, f"second-audit-funding-source:{minute}:{rate}"),),
        "1h", FundingIntervalSource.METADATA_REPORTED, instrument_key=key,
    )


def custom_currency_bar(*, kind: InstrumentType, quote_asset: str, settlement_asset: str | None) -> NormalizedBar:
    symbol = f"BTC-{quote_asset}"
    key = InstrumentKey("fixture", "fixture-x", symbol, "BTC", quote_asset, kind, symbol, settlement_asset)
    return NormalizedBar(
        uuid4(), symbol, "fixture-x", "1m", T0, Decimal(100), Decimal(101), Decimal(99),
        Decimal(100), Decimal(1), (uuid4(),), T0 + timedelta(minutes=1), True,
        {"provider_name": "fixture", "provider_instrument_id": symbol, "instrument_type": kind.value, "settlement_asset": settlement_asset},
        key,
    )


class OpenMarkAndCurrencyTests(unittest.TestCase):
    def _gap_result(self, gap_open: str):
        bars = [
            bar(0, "100", "101", "99", "100", kind=InstrumentType.PERPETUAL_SWAP),
            bar(1, "100", "101", "99", "100", kind=InstrumentType.PERPETUAL_SWAP),
            bar(2, gap_open, str(Decimal(gap_open) + 1), str(Decimal(gap_open) - 1), gap_open, kind=InstrumentType.PERPETUAL_SWAP),
        ]
        signals = [
            signal(1, "long", kind=InstrumentType.PERPETUAL_SWAP),
            signal(2, "flat", kind=InstrumentType.PERPETUAL_SWAP),
        ]
        return run_engine(bars, signals)

    def test_up_gap_open_marks_before_prefill_fill_account_and_equity(self):
        result = self._gap_result("120")
        closing_fill = next(row for row in result.fills if row.filled_at_utc == T0 + timedelta(minutes=2))
        prefill = next(row for row in result.risk_decisions if row.stage is RiskStage.PRE_FILL and row.order_id == closing_fill.order_id)
        self.assertEqual(prefill.provenance["assessed_price"], "120")
        self.assertEqual(prefill.provenance["portfolio_equity"], "1020")
        fill_snapshot = next(row for row in result.position_snapshots if row.source_fill_id == closing_fill.fill_id)
        self.assertEqual((fill_snapshot.mark_price, fill_snapshot.mark_source, fill_snapshot.snapshot_kind), (Decimal(120), MarkSource.BAR_OPEN, PositionSnapshotKind.FILL))
        account = next(row for row in result.account_snapshots if row.snapshot_at_utc == closing_fill.filled_at_utc)
        point = next(row for row in result.equity_curve if row.timestamp_utc == closing_fill.filled_at_utc)
        self.assertEqual((account.equity, account.stale_mark_count), (Decimal(1020), 0))
        self.assertEqual((point.equity, point.drawdown_amount), (Decimal(1020), Decimal(0)))

    def test_down_gap_existing_perpetual_position_has_correct_drawdown(self):
        result = self._gap_result("80")
        closing_fill = next(row for row in result.fills if row.filled_at_utc == T0 + timedelta(minutes=2))
        prefill = next(row for row in result.risk_decisions if row.stage is RiskStage.PRE_FILL and row.order_id == closing_fill.order_id)
        snapshot = next(row for row in result.position_snapshots if row.source_fill_id == closing_fill.fill_id)
        account = next(row for row in result.account_snapshots if row.snapshot_at_utc == closing_fill.filled_at_utc)
        point = next(row for row in result.equity_curve if row.timestamp_utc == closing_fill.filled_at_utc)
        self.assertEqual(prefill.provenance["portfolio_equity"], "980")
        self.assertEqual((snapshot.mark_price, snapshot.stale_mark_age_seconds), (Decimal(80), Decimal("0.0")))
        self.assertEqual((account.equity, account.stale_mark_count), (Decimal(980), 0))
        self.assertEqual((point.drawdown_amount, point.drawdown_fraction), (Decimal(20), Decimal("0.02")))

    def test_funding_keeps_close_mark_priority_before_new_open(self):
        rate = funding_rate(2, "0.01")
        result = run_engine(
            [
                bar(0, "100", "101", "99", "100", kind=InstrumentType.PERPETUAL_SWAP),
                bar(1, "100", "111", "99", "110", kind=InstrumentType.PERPETUAL_SWAP),
                bar(2, "120", "121", "119", "120", kind=InstrumentType.PERPETUAL_SWAP),
            ],
            [signal(1, "long", kind=InstrumentType.PERPETUAL_SWAP)],
            funding=(rate,),
        )
        self.assertEqual(result.funding_payments[0].mark_price, Decimal(110))
        open_mark = next(row for row in result.position_snapshots if row.snapshot_at_utc == T0 + timedelta(minutes=2) and row.snapshot_kind is PositionSnapshotKind.BAR_OPEN_MARK)
        self.assertEqual(open_mark.mark_price, Decimal(120))

    def test_fee_base_and_instrument_currency_contracts(self):
        with self.assertRaisesRegex(ValueError, "fee_currency must equal base_currency"):
            BacktestConfiguration(Decimal(1000), "USD", SizingConfiguration(SizingMode.FIXED_QUANTITY, Decimal(1)), fees=FeeConfiguration(fee_currency="USDT"))
        request = BacktestRequest(None, (custom_currency_bar(kind=InstrumentType.SPOT, quote_asset="CAD", settlement_asset=None),), (), (), config(), H, "tree")
        with self.assertRaisesRegex(ValueError, "Spot quote asset"):
            BacktestEngine().run(request)
        request = BacktestRequest(None, (custom_currency_bar(kind=InstrumentType.PERPETUAL_SWAP, quote_asset="CAD", settlement_asset="CAD"),), (), (), config(), H, "tree")
        with self.assertRaisesRegex(ValueError, "perpetual settlement asset"):
            BacktestEngine().run(request)

    def test_fill_and_fee_ledger_currencies_are_identical(self):
        result = run_engine([bar(0, "100", "101", "99", "100"), bar(1, "100", "101", "99", "100")], [signal(1, "long")], configuration=config(fees="10"))
        fill = result.fills[0]
        fee = next(row for row in result.cash_ledger_entries if row.entry_type is LedgerEntryType.FEE)
        self.assertEqual(fill.fee_currency, result.run.base_currency)
        self.assertEqual(fee.currency, fill.fee_currency)


class SpotShortAndAccountingTests(unittest.TestCase):
    def test_zero_inventory_spot_short_is_auditable_block_not_exception(self):
        result = run_engine([bar(0, "100", "101", "99", "100"), bar(1, "100", "101", "99", "100")], [signal(1, "short")])
        self.assertEqual(result.run.status, BacktestRunStatus.COMPLETED)
        self.assertEqual((result.metrics.blocked_intent_count, result.metrics.order_count, result.metrics.fill_count), (1, 0, 0))
        self.assertEqual(result.order_intents[0].target_quantity, Decimal(-1))
        self.assertEqual(result.risk_decisions[0].reason_code, "spot_short_prohibited")
        self.assertEqual(result.risk_decisions[0].status, RiskDecisionStatus.BLOCKED)

    def test_positive_spot_inventory_crossing_below_zero_is_blocked(self):
        result = run_engine(
            [bar(0, "100", "101", "99", "100"), bar(1, "100", "101", "99", "100"), bar(2, "100", "101", "99", "100")],
            [signal(1, "long"), signal(2, "short")],
        )
        self.assertEqual((result.metrics.blocked_intent_count, result.metrics.fill_count), (1, 1))
        blocked = next(row for row in result.risk_decisions if row.status is RiskDecisionStatus.BLOCKED)
        self.assertEqual(blocked.reason_code, "spot_short_prohibited")
        self.assertEqual(len(result.orders), 1)

    def test_spot_open_profit_loss_and_fee_reconciliation(self):
        profitable = run_engine([bar(0, "100", "101", "99", "100"), bar(1, "100", "121", "99", "120")], [signal(1, "long")])
        losing = run_engine([bar(0, "100", "101", "99", "100"), bar(1, "100", "101", "79", "80")], [signal(1, "long")])
        self.assertEqual((profitable.metrics.unrealized_pnl, profitable.metrics.final_equity, profitable.metrics.net_pnl), (Decimal(20), Decimal(1020), Decimal(20)))
        self.assertEqual((losing.metrics.unrealized_pnl, losing.metrics.final_equity, losing.metrics.net_pnl), (Decimal(-20), Decimal(980), Decimal(-20)))
        self.assertEqual(profitable.account_snapshots[-1].unrealized_pnl, Decimal(20))
        self.assertEqual(profitable.position_snapshots[-1].unrealized_pnl, Decimal(20))

        with_fees = run_engine([bar(0, "100", "101", "99", "100"), bar(1, "100", "121", "99", "120")], [signal(1, "long")], configuration=config(fees="100"))
        self.assertEqual(with_fees.metrics.net_pnl, with_fees.metrics.realized_pnl + with_fees.metrics.unrealized_pnl + with_fees.metrics.total_funding - with_fees.metrics.total_fees)
        self.assertEqual(with_fees.metrics.gross_pnl, with_fees.metrics.realized_pnl + with_fees.metrics.unrealized_pnl + with_fees.metrics.total_funding)

    def test_spot_partial_sale_and_stale_mark_are_hand_calculated(self):
        _, identity = instrument()
        portfolio = Portfolio(run_id=RUN, account_ref="audit-account", initial_cash=Decimal(1000), base_currency="USDT", config_sha256=H, started_at_utc=T0)
        portfolio.set_mark(identity, Decimal(100), T0, mark_source=MarkSource.BAR_CLOSE, source_event_id=uuid4(), logical_sequence=1)

        def make_fill(side, quantity, price, minute, fee):
            order_id = uuid5(NAMESPACE_URL, f"partial-order:{minute}:{side.value}")
            return Fill(RUN, order_id, uuid5(NAMESPACE_URL, f"partial-intent:{minute}:{side.value}"), identity, T0 + timedelta(minutes=minute), side, Decimal(quantity), Decimal(price), Decimal(price), AccountingMode.SPOT, LiquidityFlag.TAKER, Decimal(fee), "USDT", Decimal(0), Decimal(0), "hand", H)

        buy = make_fill(OrderSide.BUY, "2", "100", 1, "2")
        stale_fill = portfolio.apply_fill(buy, source_event_id=uuid4(), logical_sequence=2)
        self.assertEqual((stale_fill.unrealized_pnl, stale_fill.stale_mark_age_seconds, stale_fill.mark_source), (Decimal(0), Decimal("60.0"), MarkSource.BAR_CLOSE))
        portfolio.set_mark(identity, Decimal(120), T0 + timedelta(minutes=2), mark_source=MarkSource.BAR_CLOSE, source_event_id=uuid4(), logical_sequence=3)
        sell = make_fill(OrderSide.SELL, "1", "120", 3, "1.2")
        remaining = portfolio.apply_fill(sell, source_event_id=uuid4(), logical_sequence=4)
        account = portfolio.snapshot_account(T0 + timedelta(minutes=3))
        self.assertEqual((remaining.quantity, remaining.realized_pnl, remaining.unrealized_pnl), (Decimal(1), Decimal(20), Decimal(20)))
        self.assertEqual((account.cash, account.equity), (Decimal("916.8"), Decimal("1036.8")))
        self.assertEqual(account.equity - portfolio.initial_cash, remaining.realized_pnl + remaining.unrealized_pnl - portfolio.total_fees)


class IdentityHashAccountAndMetricTests(unittest.TestCase):
    def test_snapshot_and_ledger_logical_identity_conflicts(self):
        result = run_engine([bar(0, "100", "101", "99", "100"), bar(1, "100", "101", "99", "100")], [signal(1, "long")], configuration=config(fees="10"))
        snapshot = next(row for row in result.position_snapshots if row.source_fill_id is not None)
        ledger = next(row for row in result.cash_ledger_entries if row.entry_type is LedgerEntryType.FEE)
        for method, value, identity in (
            ("record_position_snapshot", snapshot, snapshot.position_snapshot_id),
            ("record_cash_ledger_entry", ledger, ledger.cash_ledger_entry_id),
        ):
            with self.subTest(method=method):
                kwargs = {"backtest_run_id": result.run.backtest_run_id, "membership_ordinal": 0}
                same = FakeConnection(None, (identity, value.record_sha256))
                self.assertEqual(getattr(PostgresPhase5Repository(same), method)(value, **kwargs), identity)
                conflict = FakeConnection(None, (identity, "f" * 64))
                with self.assertRaises(Phase5ConflictError):
                    getattr(PostgresPhase5Repository(conflict), method)(value, **kwargs)

    def test_same_timestamp_fill_open_and_close_mark_snapshots_remain_distinct(self):
        result = run_engine(
            [bar(0, "100", "101", "99", "100"), bar(1, "100", "101", "99", "100"), bar(2, "100", "101", "99", "100")],
            [signal(1, "long"), signal(2, "flat")],
        )
        at_two = [row for row in result.position_snapshots if row.snapshot_at_utc == T0 + timedelta(minutes=2)]
        kinds = {row.snapshot_kind for row in at_two}
        self.assertTrue({PositionSnapshotKind.BAR_CLOSE_MARK, PositionSnapshotKind.BAR_OPEN_MARK, PositionSnapshotKind.FILL}.issubset(kinds))
        self.assertEqual(len({row.position_snapshot_id for row in at_two}), len(at_two))

    def test_legitimate_fill_ledger_rows_have_distinct_logical_sequences(self):
        spot = run_engine([bar(0, "100", "101", "99", "100"), bar(1, "100", "101", "99", "100")], [signal(1, "long")], configuration=config(fees="10"))
        fill_id = spot.fills[0].fill_id
        spot_rows = [row for row in spot.cash_ledger_entries if row.fill_id == fill_id]
        self.assertEqual({row.entry_type for row in spot_rows}, {LedgerEntryType.SPOT_NOTIONAL, LedgerEntryType.FEE})
        self.assertEqual(len({row.ledger_sequence for row in spot_rows}), 2)

        perpetual = run_engine(
            [bar(0, "100", "101", "99", "100", kind=InstrumentType.PERPETUAL_SWAP), bar(1, "100", "101", "99", "100", kind=InstrumentType.PERPETUAL_SWAP), bar(2, "110", "111", "109", "110", kind=InstrumentType.PERPETUAL_SWAP)],
            [signal(1, "long", kind=InstrumentType.PERPETUAL_SWAP), signal(2, "flat", kind=InstrumentType.PERPETUAL_SWAP)],
            configuration=config(fees="10"),
        )
        close_fill = perpetual.fills[-1].fill_id
        rows = [row for row in perpetual.cash_ledger_entries if row.fill_id == close_fill]
        self.assertEqual({row.entry_type for row in rows}, {LedgerEntryType.REALIZED_PNL, LedgerEntryType.FEE})

    def test_every_phase5_repository_writer_detects_different_stored_hash(self):
        rate = funding_rate(2)
        result = run_engine(
            [bar(0, "100", "101", "99", "100", kind=InstrumentType.PERPETUAL_SWAP), bar(1, "100", "101", "99", "100", kind=InstrumentType.PERPETUAL_SWAP), bar(2, "100", "101", "99", "100", kind=InstrumentType.PERPETUAL_SWAP)],
            [signal(1, "long", kind=InstrumentType.PERPETUAL_SWAP)], funding=(rate,),
        )
        matrix = (
            ("record_backtest_run", result.run, result.run.backtest_run_id),
            ("record_order_intent", result.order_intents[0], result.order_intents[0].order_intent_id),
            ("record_risk_decision", result.risk_decisions[0], result.risk_decisions[0].risk_decision_id),
            ("record_order", result.orders[0], result.orders[0].order_id),
            ("record_fill", result.fills[0], result.fills[0].fill_id),
            ("upsert_position", result.positions[0], result.positions[0].position_id),
            ("record_position_snapshot", result.position_snapshots[0], result.position_snapshots[0].position_snapshot_id),
            ("record_funding_payment", result.funding_payments[0], result.funding_payments[0].funding_payment_id),
            ("record_cash_ledger_entry", result.cash_ledger_entries[0], result.cash_ledger_entries[0].cash_ledger_entry_id),
            ("record_account_snapshot", result.account_snapshots[0], result.account_snapshots[0].account_snapshot_id),
            ("record_backtest_event", result.events[0], result.events[0].execution_event_id),
            ("record_equity_curve_point", result.equity_curve[0], result.equity_curve[0].equity_curve_id),
            ("record_backtest_metric", result.metric_records[0], result.metric_records[0].backtest_metric_id),
        )
        for method, value, identity in matrix:
            with self.subTest(method=method):
                connection = FakeConnection(None, (identity, "f" * 64))
                kwargs = {}
                if method != "record_backtest_run":
                    kwargs["backtest_run_id"] = result.run.backtest_run_id
                if method not in {"record_backtest_run", "record_backtest_metric"}:
                    kwargs["membership_ordinal"] = 0
                with self.assertRaises(Phase5ConflictError):
                    getattr(PostgresPhase5Repository(connection), method)(value, **kwargs)

    def test_lowercase_sha256_contract_and_no_signal_path(self):
        bars = (bar(0, "100", "101", "99", "100"),)
        with self.assertRaises(ValueError):
            BacktestRequest(None, bars, (), (), config(), "z" * 64, "tree")
        with self.assertRaises(ValueError):
            BacktestRequest(None, bars, (), (), config(), H.upper(), "tree")
        request = BacktestRequest(None, bars, (), (), config(), H, "tree")
        self.assertEqual(BacktestEngine().run(request).metrics.fill_count, 0)

    def test_deterministic_complete_run_and_stable_historical_child_identities(self):
        bars = [bar(0, "100", "101", "99", "100"), bar(1, "100", "101", "99", "100"), bar(2, "110", "111", "109", "110")]
        signals = [signal(1, "long")]
        first = run_engine(bars, signals)
        shuffled = run_engine(list(reversed(bars)), list(reversed(signals)))
        recollected = run_engine([bar(i, str(row.open), str(row.high), str(row.low), str(row.close), source_id=uuid4()) for i, row in enumerate(bars)], signals)
        self.assertEqual(first.run.backtest_run_id, shuffled.run.backtest_run_id)
        self.assertEqual(first.run.backtest_run_id, recollected.run.backtest_run_id)
        appended = run_engine(bars + [bar(4, "150", "151", "149", "150")], signals)
        self.assertNotEqual(first.run.backtest_run_id, appended.run.backtest_run_id)
        self.assertEqual(first.run.run_id, appended.run.run_id)
        cutoff = T0 + timedelta(minutes=3)
        signature = lambda result: (
            tuple((row.fill_id, row.record_sha256) for row in result.fills if row.filled_at_utc <= cutoff),
            tuple((row.equity_curve_id, row.record_sha256) for row in result.equity_curve if row.timestamp_utc <= cutoff),
            tuple((row.execution_event_id, row.record_sha256) for row in result.events if row.event_timestamp_utc <= cutoff),
        )
        self.assertEqual(signature(first), signature(appended))
        changed_config = run_engine(bars, signals, configuration=config(initial="2000"))
        changed_impl = run_engine(bars, signals, implementation_code_sha256=sha256_payload({"impl": 2}))
        self.assertNotEqual(first.run.backtest_run_id, changed_config.run.backtest_run_id)
        self.assertNotEqual(first.run.backtest_run_id, changed_impl.run.backtest_run_id)
        with self.assertRaisesRegex(ValueError, "run_id does not match"):
            run_engine(bars, signals, run_id=RUN)

    def test_account_ref_and_prefill_order_lineage_are_consistent(self):
        result = run_engine([bar(0, "100", "101", "99", "100"), bar(1, "100", "101", "99", "100")], [signal(1, "long")], configuration=config(account_ref="audit-simulation"))
        self.assertEqual(result.run.account_ref, "audit-simulation")
        self.assertTrue(all(row.account_ref == "audit-simulation" for row in result.positions))
        self.assertTrue(all(row.account_ref == "audit-simulation" for row in result.position_snapshots))
        self.assertTrue(all(row.account_ref == "audit-simulation" for row in result.account_snapshots))
        prefill = next(row for row in result.risk_decisions if row.stage is RiskStage.PRE_FILL)
        self.assertIsNotNone(prefill.order_id)
        self.assertIn(prefill.order_intent_id, prefill.parent_ids)
        self.assertIn(prefill.order_id, prefill.parent_ids)

    def test_round_trip_metrics_are_net_of_fees_and_funding(self):
        fee_loss = run_engine(
            [bar(0, "100", "101", "99", "100"), bar(1, "100", "101", "99", "100"), bar(2, "101", "102", "100", "101")],
            [signal(1, "long"), signal(2, "flat")], configuration=config(fees="100"),
        )
        self.assertEqual((fee_loss.metrics.completed_round_trip_count, fee_loss.metrics.winning_round_trips, fee_loss.metrics.losing_round_trips), (1, 0, 1))
        self.assertEqual(fee_loss.metrics.gross_loss, Decimal("-1.01"))
        self.assertEqual(fee_loss.metrics.profit_factor, Decimal(0))

        rate = funding_rate(2, "0.02")
        funding_loss = run_engine(
            [bar(0, "100", "101", "99", "100", kind=InstrumentType.PERPETUAL_SWAP), bar(1, "100", "101", "99", "100", kind=InstrumentType.PERPETUAL_SWAP), bar(2, "101", "102", "100", "101", kind=InstrumentType.PERPETUAL_SWAP)],
            [signal(1, "long", kind=InstrumentType.PERPETUAL_SWAP), signal(2, "flat", kind=InstrumentType.PERPETUAL_SWAP)],
            funding=(rate,),
        )
        self.assertEqual((funding_loss.metrics.realized_pnl, funding_loss.metrics.total_funding, funding_loss.metrics.net_pnl), (Decimal(1), Decimal(-2), Decimal(-1)))
        self.assertEqual((funding_loss.metrics.winning_round_trips, funding_loss.metrics.losing_round_trips, funding_loss.metrics.gross_loss), (0, 1, Decimal(-1)))
        gross_record = next(row for row in funding_loss.metric_records if row.name == "gross_pnl")
        round_trip_record = next(row for row in funding_loss.metric_records if row.name == "profit_factor")
        self.assertEqual(gross_record.details["semantics"], "realized_plus_unrealized_plus_funding_before_fees")
        self.assertEqual(round_trip_record.details["round_trip_semantics"], "net_of_fees_and_realized_funding")

    def test_strengthened_boundary_patterns_and_phase6_boundary(self):
        self.assertIn("non-placeholder token/API-key/secret assignment", content_boundary_findings(Path("runtime.py"), 'API_KEY = "real-value-123"'))
        self.assertEqual(content_boundary_findings(Path("runtime.py"), 'API_KEY = "placeholder"'), [])
        self.assertIn("authenticated exchange account/order endpoint", content_boundary_findings(Path("runtime.py"), 'path = "/api/v3/account"'))
        path_findings = tracked_path_boundary_findings(["private_strategies/edge.py", "exports/real_trade_log.csv", "backup/pg_dump.sql", ".env", "var/postgres/PG_VERSION"])
        self.assertEqual(len(path_findings), 6)
        status = json.loads((ROOT / ".project" / "implementation_status.json").read_text(encoding="utf-8"))
        phases = {row["id"]: row for row in status["phases"]}
        self.assertEqual(phases["phase_6_monitoring_simulated_fix_api"]["status"], "todo")


if __name__ == "__main__":
    unittest.main()
