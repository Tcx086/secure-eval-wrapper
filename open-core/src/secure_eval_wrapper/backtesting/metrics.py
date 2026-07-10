"""Metrics derived only from fills, ledger, positions, funding, and equity."""

from __future__ import annotations

from decimal import Decimal

from secure_eval_wrapper.backtesting.models import BacktestMetric, BacktestMetrics, MetricStatus
from secure_eval_wrapper.execution.models import LedgerEntryType, OrderIntentStatus, OrderStatus


def _round_trip_pnls(fills, position_snapshots, ledger) -> list[Decimal]:
    """Return completed round-trip PnL net of allocated fees and realized funding."""

    fills_by_id = {row.fill_id: row for row in fills}
    fees_by_fill: dict[object, Decimal] = {}
    timeline = []
    for row in ledger:
        if row.entry_type is LedgerEntryType.FEE and row.fill_id is not None:
            fees_by_fill[row.fill_id] = fees_by_fill.get(row.fill_id, Decimal(0)) + row.amount
        elif row.entry_type is LedgerEntryType.FUNDING and row.series_identity is not None:
            timeline.append((row.event_timestamp_utc, 0, row.ledger_sequence, row.series_identity.series_identity_sha256, "funding", row))
    for row in position_snapshots:
        if row.source_fill_id is not None:
            timeline.append((row.snapshot_at_utc, 1, row.logical_sequence, row.series_identity.series_identity_sha256, "fill", row))
    timeline.sort(key=lambda item: (item[0], item[1], item[2], item[3]))

    quantities: dict[str, Decimal] = {}
    realized: dict[str, Decimal] = {}
    accumulated: dict[str, Decimal] = {}
    results: list[Decimal] = []
    for _, _, _, key, event_type, row in timeline:
        old_q = quantities.get(key, Decimal(0))
        if event_type == "funding":
            if old_q != 0:
                accumulated[key] = accumulated.get(key, Decimal(0)) + row.amount
            continue

        new_q = row.quantity
        realized_delta = row.realized_pnl - realized.get(key, Decimal(0))
        fee = fees_by_fill.get(row.source_fill_id, Decimal(0))
        fill = fills_by_id.get(row.source_fill_id)
        if fill is None:
            raise ValueError("round-trip metrics require every fill snapshot to reference a fill")

        if old_q == 0:
            accumulated[key] = fee
        elif old_q * new_q < 0:
            closing_fraction = abs(old_q) / fill.quantity
            closing_fee = fee * closing_fraction
            opening_fee = fee - closing_fee
            accumulated[key] = accumulated.get(key, Decimal(0)) + realized_delta + closing_fee
            results.append(accumulated[key])
            accumulated[key] = opening_fee
        else:
            accumulated[key] = accumulated.get(key, Decimal(0)) + realized_delta + fee
            if new_q == 0:
                results.append(accumulated[key])
                accumulated[key] = Decimal(0)
        quantities[key] = new_q
        realized[key] = row.realized_pnl
    return results


def calculate_metrics(*, initial_cash, fills, intents, orders, positions, snapshots, ledger, funding_payments, equity_curve) -> BacktestMetrics:
    if not equity_curve:
        raise ValueError("metrics require an equity curve")
    final = equity_curve[-1]
    realized = sum((row.realized_pnl for row in positions), Decimal(0))
    latest_by_series = {}
    for row in snapshots:
        latest_by_series[row.series_identity.series_identity_sha256] = row
    unrealized = sum((row.unrealized_pnl for row in latest_by_series.values()), Decimal(0))
    fees = -sum((row.amount for row in ledger if row.entry_type is LedgerEntryType.FEE), Decimal(0))
    funding = sum((row.amount for row in ledger if row.entry_type is LedgerEntryType.FUNDING), Decimal(0))
    net = final.equity - initial_cash
    gross = realized + unrealized + funding
    if net != gross - fees:
        raise ValueError("net PnL must reconcile to realized plus unrealized plus funding minus fees")
    total_return = None if initial_cash == 0 else net / initial_cash
    max_dd_amount = max((row.drawdown_amount for row in equity_curve), default=Decimal(0))
    dd_fractions = [row.drawdown_fraction for row in equity_curve if row.drawdown_fraction is not None]
    max_dd_fraction = max(dd_fractions) if dd_fractions else None
    turnover = sum((row.notional for row in fills), Decimal(0))
    latest_orders = {row.order_id: row for row in orders}
    round_trips = _round_trip_pnls(fills, snapshots, ledger)
    winning = [value for value in round_trips if value > 0]
    losing = [value for value in round_trips if value < 0]
    gross_profit = sum(winning, Decimal(0))
    gross_loss = sum(losing, Decimal(0))
    win_rate = None if not round_trips else Decimal(len(winning)) / Decimal(len(round_trips))
    profit_factor = None if gross_loss == 0 else gross_profit / abs(gross_loss)
    return BacktestMetrics(
        initial_cash=initial_cash,
        final_cash=final.cash,
        final_equity=final.equity,
        gross_pnl=gross,
        net_pnl=net,
        realized_pnl=realized,
        unrealized_pnl=unrealized,
        total_fees=fees,
        total_funding=funding,
        total_return=total_return,
        maximum_drawdown_amount=max_dd_amount,
        maximum_drawdown_fraction=max_dd_fraction,
        maximum_gross_exposure=max((row.gross_exposure for row in equity_curve), default=Decimal(0)),
        maximum_net_exposure=max((abs(row.net_exposure) for row in equity_curve), default=Decimal(0)),
        turnover=turnover,
        submitted_intent_count=sum(row.status is OrderIntentStatus.SUBMITTED for row in intents),
        blocked_intent_count=sum(row.status is OrderIntentStatus.BLOCKED for row in intents),
        order_count=len(latest_orders),
        fill_count=len(fills),
        cancel_count=sum(row.status is OrderStatus.CANCELLED for row in latest_orders.values()),
        reject_count=sum(row.status is OrderStatus.REJECTED for row in latest_orders.values()),
        expired_order_count=sum(row.status is OrderStatus.EXPIRED for row in latest_orders.values()),
        funding_payment_count=len(funding_payments),
        final_open_position_count=sum(row.quantity != 0 for row in positions),
        completed_round_trip_count=len(round_trips),
        winning_round_trips=len(winning),
        losing_round_trips=len(losing),
        win_rate=win_rate,
        gross_profit=gross_profit,
        gross_loss=gross_loss,
        profit_factor=profit_factor,
        non_positive_equity=any(row.equity <= 0 for row in equity_curve),
    )


def metric_records(backtest_run_id, metrics: BacktestMetrics, config_sha256: str) -> tuple[BacktestMetric, ...]:
    count_names = {name for name in metrics.__dataclass_fields__ if name.endswith("_count") or name in {"winning_round_trips", "losing_round_trips"}}
    fraction_names = {"total_return", "maximum_drawdown_fraction", "win_rate", "profit_factor"}
    net_round_trip_names = {"completed_round_trip_count", "winning_round_trips", "losing_round_trips", "win_rate", "gross_profit", "gross_loss", "profit_factor"}
    rows = []
    for name, raw in metrics.as_dict().items():
        if isinstance(raw, bool):
            value, unit = Decimal(int(raw)), "boolean"
        elif isinstance(raw, int):
            value, unit = Decimal(raw), "count" if name in count_names else None
        else:
            value = raw
            unit = "fraction" if name in fraction_names else "base_currency"
        status = MetricStatus.UNAVAILABLE if value is None else MetricStatus.AVAILABLE
        details = {}
        if name in net_round_trip_names:
            details["round_trip_semantics"] = "net_of_fees_and_realized_funding"
        if name == "gross_pnl":
            details["semantics"] = "realized_plus_unrealized_plus_funding_before_fees"
        rows.append(BacktestMetric(backtest_run_id, name, value, status, unit, config_sha256, details))
    return tuple(rows)
