"""One outer transaction for the complete persisted Phase 5 backtest bundle."""

from __future__ import annotations

from dataclasses import dataclass


class BacktestBundlePersistenceError(RuntimeError):
    pass


@dataclass(frozen=True)
class BacktestBundleSummary:
    order_intents: int
    risk_decisions: int
    orders: int
    fills: int
    positions: int
    position_snapshots: int
    cash_ledger_entries: int
    funding_payments: int
    account_snapshots: int
    events: int
    equity_points: int
    metrics: int


def persist_backtest_bundle(repository, result) -> BacktestBundleSummary:
    if repository is None or not hasattr(repository, "transaction"):
        raise TypeError("backtest persistence requires a transactional PostgreSQL repository")
    try:
        with repository.transaction():
            repository.record_backtest_run(result.run)
            for value in result.order_intents: repository.record_order_intent(value)
            for value in result.risk_decisions: repository.record_risk_decision(value)
            for value in result.orders: repository.record_order(value)
            for value in result.fills: repository.record_fill(value)
            for value in result.positions: repository.upsert_position(value)
            for value in result.position_snapshots: repository.record_position_snapshot(value)
            for value in result.funding_payments: repository.record_funding_payment(value)
            for value in result.cash_ledger_entries: repository.record_cash_ledger_entry(value)
            for value in result.account_snapshots: repository.record_account_snapshot(value)
            for value in result.events: repository.record_backtest_event(value)
            for value in result.equity_curve: repository.record_equity_curve_point(value)
            for value in result.metric_records: repository.record_backtest_metric(value)
    except Exception as exc:
        raise BacktestBundlePersistenceError(f"complete backtest persistence failed: {exc}") from exc
    return BacktestBundleSummary(
        len(result.order_intents), len(result.risk_decisions), len(result.orders), len(result.fills),
        len(result.positions), len(result.position_snapshots), len(result.cash_ledger_entries),
        len(result.funding_payments), len(result.account_snapshots), len(result.events),
        len(result.equity_curve), len(result.metric_records),
    )


class PersistingBacktestRepository:
    """Adapter accepted by BacktestEngine for optional one-call bundled persistence."""

    def __init__(self, repository) -> None:
        self.repository = repository
        self.last_summary = None

    def persist(self, result):
        self.last_summary = persist_backtest_bundle(self.repository, result)
        return self.last_summary


__all__ = ["BacktestBundlePersistenceError", "BacktestBundleSummary", "PersistingBacktestRepository", "persist_backtest_bundle"]
