"""One outer transaction for a complete Phase 5 backtest and its memberships."""

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
    memberships: int


def _validate_bundle_lineage(result) -> None:
    execution_lineage_id = result.run.run_id
    for collection_name in (
        "order_intents",
        "risk_decisions",
        "orders",
        "fills",
        "positions",
        "position_snapshots",
        "cash_ledger_entries",
        "funding_payments",
        "account_snapshots",
        "events",
        "equity_curve",
    ):
        for value in getattr(result, collection_name):
            if value.run_id != execution_lineage_id:
                raise ValueError(
                    f"{collection_name} contains a record outside the run execution lineage"
                )
    for metric in result.metric_records:
        if metric.backtest_run_id != result.run.backtest_run_id:
            raise ValueError("metric is outside the complete deterministic backtest run")


def persist_backtest_bundle(repository, result) -> BacktestBundleSummary:
    if repository is None or not hasattr(repository, "transaction"):
        raise TypeError("backtest persistence requires a transactional PostgreSQL repository")
    _validate_bundle_lineage(result)
    backtest_run_id = result.run.backtest_run_id
    membership_count = 0
    try:
        with repository.transaction():
            repository.record_backtest_run(result.run)
            for ordinal, value in enumerate(result.order_intents):
                repository.record_order_intent(value, backtest_run_id=backtest_run_id, membership_ordinal=ordinal)
                membership_count += 1
            # Orders precede pre-fill decisions because those decisions carry an order FK.
            for ordinal, value in enumerate(result.orders):
                repository.record_order(value, backtest_run_id=backtest_run_id, membership_ordinal=ordinal)
            for ordinal, value in enumerate(result.risk_decisions):
                repository.record_risk_decision(value, backtest_run_id=backtest_run_id, membership_ordinal=ordinal)
                membership_count += 1
            for ordinal, value in enumerate(result.fills):
                repository.record_fill(value, backtest_run_id=backtest_run_id, membership_ordinal=ordinal)
                membership_count += 1
            for ordinal, value in enumerate(result.positions):
                repository.upsert_position(value, backtest_run_id=backtest_run_id, membership_ordinal=ordinal)
            for ordinal, value in enumerate(result.position_snapshots):
                repository.record_position_snapshot(value, backtest_run_id=backtest_run_id, membership_ordinal=ordinal)
                membership_count += 1
            for ordinal, value in enumerate(result.funding_payments):
                repository.record_funding_payment(value, backtest_run_id=backtest_run_id, membership_ordinal=ordinal)
                membership_count += 1
            for ordinal, value in enumerate(result.cash_ledger_entries):
                repository.record_cash_ledger_entry(value, backtest_run_id=backtest_run_id, membership_ordinal=ordinal)
                membership_count += 1
            for ordinal, value in enumerate(result.account_snapshots):
                repository.record_account_snapshot(value, backtest_run_id=backtest_run_id, membership_ordinal=ordinal)
                membership_count += 1
            for ordinal, value in enumerate(result.events):
                repository.record_backtest_event(value, backtest_run_id=backtest_run_id, membership_ordinal=ordinal)
                membership_count += 1
            for ordinal, value in enumerate(result.equity_curve):
                repository.record_equity_curve_point(value, backtest_run_id=backtest_run_id, membership_ordinal=ordinal)
                membership_count += 1
            for value in result.metric_records:
                repository.record_backtest_metric(value, backtest_run_id=backtest_run_id)
    except Exception as exc:
        raise BacktestBundlePersistenceError(f"complete backtest persistence failed: {exc}") from exc
    return BacktestBundleSummary(
        order_intents=len(result.order_intents),
        risk_decisions=len(result.risk_decisions),
        orders=len(result.orders),
        fills=len(result.fills),
        positions=len(result.positions),
        position_snapshots=len(result.position_snapshots),
        cash_ledger_entries=len(result.cash_ledger_entries),
        funding_payments=len(result.funding_payments),
        account_snapshots=len(result.account_snapshots),
        events=len(result.events),
        equity_points=len(result.equity_curve),
        metrics=len(result.metric_records),
        memberships=membership_count,
    )


class PersistingBacktestRepository:
    """Adapter accepted by BacktestEngine for optional one-call bundled persistence."""

    def __init__(self, repository) -> None:
        self.repository = repository
        self.last_summary = None

    def persist(self, result):
        self.last_summary = persist_backtest_bundle(self.repository, result)
        return self.last_summary


__all__ = [
    "BacktestBundlePersistenceError",
    "BacktestBundleSummary",
    "PersistingBacktestRepository",
    "persist_backtest_bundle",
]
