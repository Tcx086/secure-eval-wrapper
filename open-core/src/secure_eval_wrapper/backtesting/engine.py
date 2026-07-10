"""Point-in-time event loop for deterministic bar-level backtesting.

At equal timestamps the priority is completed-bar execution, close mark, funding, signal/order
submission, and finally the next bar open.  This makes a close-derived signal eligible for the
next real open at the same timestamp without exposing its own completed bar to execution.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime
from decimal import Decimal
from itertools import groupby

from secure_eval_wrapper.alpha.identity import bar_available_at_utc, series_identity_from_record, stable_economic_record
from secure_eval_wrapper.backtesting.metrics import calculate_metrics, metric_records
from secure_eval_wrapper.backtesting.models import (
    BacktestRequest,
    BacktestResult,
    BacktestRun,
    BacktestRunStatus,
    EquityCurvePoint,
)
from secure_eval_wrapper.data_collection.hashing import sha256_payload
from secure_eval_wrapper.data_collection.models import FundingRate, InstrumentType, NormalizedBar
from secure_eval_wrapper.execution.accounting import Portfolio
from secure_eval_wrapper.execution.broker import BrokerResult
from secure_eval_wrapper.execution.brokers.simulated import SimulatedBroker
from secure_eval_wrapper.execution.fees import FixedBasisPointFeeModel
from secure_eval_wrapper.execution.funding import funding_payment_for_position
from secure_eval_wrapper.execution.models import (
    AccountingMode,
    ExecutionEvent,
    ExecutionEventType,
    MarkSource,
    OrderIntent,
    OrderIntentStatus,
    OrderSide,
    OrderStatus,
    OrderType,
    RiskDecisionStatus,
    RiskStage,
)
from secure_eval_wrapper.execution.risk.guard import PortfolioRiskView, RiskGuard
from secure_eval_wrapper.execution.sizing import size_signal
from secure_eval_wrapper.execution.slippage import FixedAdverseBasisPointSlippage


@dataclass(frozen=True)
class _Event:
    timestamp: datetime
    priority: int
    event_type: ExecutionEventType
    series_hash: str
    payload_hash: str
    payload: object


class BacktestEngine:
    def __init__(self, *, persistence_repository=None) -> None:
        self.persistence_repository = persistence_repository

    @staticmethod
    def _accounting_mode(identity) -> AccountingMode:
        if identity.instrument_type is InstrumentType.SPOT:
            return AccountingMode.SPOT
        if identity.instrument_type is InstrumentType.PERPETUAL_SWAP:
            return AccountingMode.LINEAR_PERPETUAL
        raise ValueError(f"unsupported Phase 5 instrument type: {identity.instrument_type.value}")

    @staticmethod
    def _same_instrument(left, right) -> bool:
        return (
            left.provider_name,
            left.exchange,
            left.provider_instrument_id,
            left.canonical_symbol,
            left.instrument_type,
            left.settlement_asset,
        ) == (
            right.provider_name,
            right.exchange,
            right.provider_instrument_id,
            right.canonical_symbol,
            right.instrument_type,
            right.settlement_asset,
        )

    def _validate(self, request: BacktestRequest) -> tuple[dict[str, object], tuple[_Event, ...]]:
        if not request.bars:
            raise ValueError("backtest requires at least one completed validated bar")
        identities: dict[str, object] = {}
        bar_keys: set[tuple[str, datetime]] = set()
        close_times: dict[str, list[datetime]] = {}
        events: list[_Event] = []
        for bar in request.bars:
            if not isinstance(bar, NormalizedBar):
                raise TypeError("bars must contain NormalizedBar records")
            identity = series_identity_from_record(bar)
            if identity.provider_name == "legacy":
                raise ValueError("backtest bars require complete non-legacy series identity")
            opened = bar.bar_open_time_utc
            closed = bar_available_at_utc(bar)
            if bar.is_final is not True:
                raise ValueError("backtest accepts final bars only")
            if not (bar.low <= bar.open <= bar.high and bar.low <= bar.close <= bar.high and bar.volume >= 0):
                raise ValueError("bar OHLCV values are invalid")
            key = (identity.series_identity_sha256, opened)
            if key in bar_keys:
                raise ValueError("duplicate logical bar event")
            bar_keys.add(key)
            identities[identity.series_identity_sha256] = identity
            close_times.setdefault(identity.series_identity_sha256, []).append(closed)
            stable = stable_economic_record(bar)
            digest = sha256_payload(stable)
            events.extend((
                _Event(closed, 1, ExecutionEventType.BAR_COMPLETED_EXECUTION, identity.series_identity_sha256, digest, bar),
                _Event(closed, 2, ExecutionEventType.MARK_UPDATE, identity.series_identity_sha256, digest, bar),
                _Event(opened, 5, ExecutionEventType.BAR_OPEN_EXECUTION, identity.series_identity_sha256, digest, bar),
            ))
        for identity in identities.values():
            if identity.instrument_type is InstrumentType.SPOT:
                normalized = identity.canonical_symbol.replace("/", "-")
                if "-" not in normalized:
                    raise ValueError("Spot canonical_symbol must expose a quote asset")
                _, quote_asset = normalized.rsplit("-", 1)
                if not quote_asset or quote_asset.upper() != request.configuration.base_currency:
                    raise ValueError("Spot quote asset must equal base_currency; FX conversion is not supported")
            elif identity.instrument_type is InstrumentType.PERPETUAL_SWAP:
                if identity.settlement_asset is None or identity.settlement_asset.upper() != request.configuration.base_currency:
                    raise ValueError("perpetual settlement asset must equal base_currency; FX conversion is not supported")

        signal_keys: set[tuple[str, datetime]] = set()
        for signal in request.signals:
            digest = signal.series_identity.series_identity_sha256
            if digest not in identities:
                raise ValueError("signal references a series absent from backtest bars")
            key = (digest, signal.timestamp_utc)
            if key in signal_keys:
                raise ValueError("duplicate logical signal event")
            signal_keys.add(key)
            if not any(value <= signal.timestamp_utc for value in close_times[digest]):
                raise ValueError("signal occurs before its underlying completed data is available")
            events.append(_Event(signal.timestamp_utc, 4, ExecutionEventType.SIGNAL, digest, signal.record_sha256, signal))

        funding_keys: set[tuple[str, datetime]] = set()
        for rate in request.funding_rates:
            identity = series_identity_from_record(rate)
            if identity.instrument_type is not InstrumentType.PERPETUAL_SWAP:
                raise ValueError("funding records require perpetual series identity")
            if identity.settlement_asset is None or identity.settlement_asset.upper() != request.configuration.base_currency:
                raise ValueError("funding settlement asset must equal base_currency; FX conversion is not supported")
            if not any(self._same_instrument(identity, bar_identity) for bar_identity in identities.values()):
                raise ValueError("funding record references an instrument absent from backtest bars")
            key = (identity.series_identity_sha256, rate.funding_time_utc)
            if key in funding_keys:
                raise ValueError("duplicate logical funding event")
            funding_keys.add(key)
            events.append(_Event(rate.funding_time_utc, 3, ExecutionEventType.FUNDING, identity.series_identity_sha256, sha256_payload(stable_economic_record(rate)), rate))

        events.sort(key=lambda item: (item.timestamp, item.priority, item.series_hash, item.payload_hash))
        logical = [(item.timestamp, item.priority, item.series_hash, item.payload_hash) for item in events]
        if len(logical) != len(set(logical)):
            raise ValueError("duplicate logical backtest event")
        return identities, tuple(events)

    @staticmethod
    def _risk_view(portfolio: Portfolio, broker: SimulatedBroker, timestamp: datetime) -> PortfolioRiskView:
        equity, _, _, _, _, _ = portfolio.values(timestamp)
        counts: dict[str, int] = {}
        for order in broker.active_orders():
            key = order.series_identity.series_identity_sha256
            counts[key] = counts.get(key, 0) + 1
        return PortfolioRiskView(
            cash=portfolio.cash,
            equity=equity,
            peak_equity=portfolio.peak_equity,
            positions=dict(portfolio.positions),
            marks={key: value[1] for key, value in portfolio.marks.items()},
            open_orders_per_series=counts,
        )

    @staticmethod
    def _order_prices(configuration, side: OrderSide, reference: Decimal) -> tuple[Decimal | None, Decimal | None]:
        limit_price = stop_price = None
        ten_k = Decimal(10_000)
        if configuration.order_type is OrderType.LIMIT:
            offset = reference * configuration.limit_offset_bps / ten_k
            limit_price = reference - offset if side is OrderSide.BUY else reference + offset
        elif configuration.order_type is OrderType.STOP:
            offset = reference * configuration.stop_offset_bps / ten_k
            stop_price = reference + offset if side is OrderSide.BUY else reference - offset
        elif configuration.order_type is OrderType.STOP_LIMIT:
            stop_offset = reference * configuration.stop_offset_bps / ten_k
            stop_price = reference + stop_offset if side is OrderSide.BUY else reference - stop_offset
            limit_offset = stop_price * configuration.stop_limit_offset_bps / ten_k
            limit_price = stop_price + limit_offset if side is OrderSide.BUY else stop_price - limit_offset
        return limit_price, stop_price

    def run(self, request: BacktestRequest) -> BacktestResult:
        identities, internal_events = self._validate(request)
        started_at = internal_events[0].timestamp
        completed_at = internal_events[-1].timestamp
        execution_run_id = request.execution_lineage_id
        fee_model = FixedBasisPointFeeModel(request.configuration.fees)
        slippage_model = FixedAdverseBasisPointSlippage(request.configuration.slippage)
        broker = SimulatedBroker(request.configuration.broker, fee_model=fee_model, slippage_model=slippage_model)
        risk_guard = RiskGuard(request.configuration.risk_limits)
        portfolio = Portfolio(run_id=execution_run_id, account_ref=request.configuration.broker.account_ref, initial_cash=request.configuration.initial_cash, base_currency=request.configuration.base_currency, config_sha256=request.configuration.config_sha256, started_at_utc=started_at)

        intents = []
        intent_by_id = {}
        risk_decisions = []
        order_by_id = {}
        fills = []
        funding_payments = []
        audit_events = []
        equity_curve = []
        event_sequence = 0

        def record_event(event_type, timestamp, priority, *, identity=None, parent=None, metadata=None, economic=None):
            nonlocal event_sequence
            event_sequence += 1
            payload = economic if economic is not None else {"event_type": event_type, "timestamp": timestamp, "series_identity_sha256": None if identity is None else identity.series_identity_sha256, "parent": parent, "metadata": metadata or {}}
            row = ExecutionEvent(execution_run_id, event_sequence, timestamp, priority, event_type, sha256_payload(payload), request.configuration.config_sha256, identity, parent, metadata or {})
            audit_events.append(row)
            return row

        def handle_broker_result(result: BrokerResult, timestamp: datetime, priority: int) -> None:
            for risk in result.risk_decisions:
                risk_decisions.append(risk)
                record_event(ExecutionEventType.RISK_DECISION, timestamp, priority, identity=risk.series_identity, parent=risk.risk_decision_id, metadata={"stage": risk.stage.value, "status": risk.status.value, "reason_code": risk.reason_code}, economic=risk.economic_payload)
            for order in result.order_updates:
                order_by_id[order.order_id] = order
                event_type = {
                    OrderStatus.ACKNOWLEDGED: ExecutionEventType.ORDER_ACKNOWLEDGED,
                    OrderStatus.TRIGGERED: ExecutionEventType.ORDER_TRIGGERED,
                    OrderStatus.FILLED: ExecutionEventType.FILL,
                    OrderStatus.CANCELLED: ExecutionEventType.ORDER_CANCELLED,
                    OrderStatus.REJECTED: ExecutionEventType.ORDER_REJECTED,
                    OrderStatus.EXPIRED: ExecutionEventType.ORDER_EXPIRED,
                }[order.status]
                record_event(event_type, timestamp, priority, identity=order.series_identity, parent=order.order_id, metadata={"status": order.status.value, "reason": order.activation_reason}, economic={"order_id": order.order_id, "status": order.status, "triggered_at_utc": order.triggered_at_utc, "reason": order.activation_reason})
            for fill in result.fills:
                fills.append(fill)
                fill_event = record_event(ExecutionEventType.FILL, timestamp, priority, identity=fill.series_identity, parent=fill.fill_id, metadata={"fill_reason": fill.fill_reason, "liquidity": fill.liquidity_flag.value}, economic=fill.economic_payload)
                snap = portfolio.apply_fill(fill, source_event_id=fill_event.execution_event_id, logical_sequence=fill_event.sequence)
                record_event(ExecutionEventType.POSITION, timestamp, priority, identity=fill.series_identity, parent=snap.position_snapshot_id, metadata={"source": "fill", "mark_source": None if snap.mark_source is None else snap.mark_source.value, "snapshot_kind": snap.snapshot_kind.value}, economic=snap.economic_payload)

        for timestamp, grouped in groupby(internal_events, key=lambda item: item.timestamp):
            for event in grouped:
                identity = identities.get(event.series_hash)
                if event.event_type is ExecutionEventType.BAR_COMPLETED_EXECUTION:
                    bar = event.payload
                    record_event(event.event_type, timestamp, event.priority, identity=identity, metadata={"bar_open_time_utc": bar.bar_open_time_utc}, economic=stable_economic_record(bar))

                    def prefill(order, price, liquidity, fee):
                        return risk_guard.assess(intent_by_id[order.order_intent_id], price=price, stage=RiskStage.PRE_FILL, decision_timestamp_utc=timestamp, portfolio=self._risk_view(portfolio, broker, timestamp), fee_amount=fee, order_id=order.order_id)

                    handle_broker_result(broker.process_completed_bar(series_identity=identity, timestamp_utc=timestamp, open_price=bar.open, high=bar.high, low=bar.low, close=bar.close, risk_check=prefill), timestamp, event.priority)
                elif event.event_type is ExecutionEventType.MARK_UPDATE:
                    bar = event.payload
                    mark_event = record_event(event.event_type, timestamp, event.priority, identity=identity, metadata={"close": str(bar.close), "mark_source": MarkSource.BAR_CLOSE.value}, economic={"series_identity_sha256": event.series_hash, "timestamp": timestamp, "close": bar.close, "mark_source": MarkSource.BAR_CLOSE})
                    snap = portfolio.set_mark(identity, bar.close, timestamp, mark_source=MarkSource.BAR_CLOSE, source_event_id=mark_event.execution_event_id, logical_sequence=mark_event.sequence)
                    if snap is not None:
                        record_event(ExecutionEventType.POSITION, timestamp, event.priority, identity=identity, parent=snap.position_snapshot_id, metadata={"source": "mark", "mark_source": snap.mark_source.value, "snapshot_kind": snap.snapshot_kind.value}, economic=snap.economic_payload)
                elif event.event_type is ExecutionEventType.FUNDING:
                    rate: FundingRate = event.payload
                    funding_identity = series_identity_from_record(rate)
                    record_event(event.event_type, timestamp, event.priority, identity=funding_identity, metadata={"interval": rate.funding_interval, "interval_source": rate.funding_interval_source.value}, economic=stable_economic_record(rate))
                    for state in sorted(portfolio.positions.values(), key=lambda row: row.series_identity.series_identity_sha256):
                        if not self._same_instrument(state.series_identity, funding_identity):
                            continue
                        mark_info = portfolio.marks.get(state.series_identity.series_identity_sha256)
                        if mark_info is None:
                            continue
                        payment = funding_payment_for_position(rate, position=state, mark_price=mark_info[1], config_sha256=request.configuration.config_sha256, record_zero=request.configuration.record_zero_funding)
                        if payment is not None:
                            funding_payments.append(payment)
                            portfolio.apply_funding(payment)
                            record_event(ExecutionEventType.FUNDING, timestamp, event.priority, identity=state.series_identity, parent=payment.funding_payment_id, metadata={"cash_flow": str(payment.cash_flow)}, economic=payment.economic_payload)
                elif event.event_type is ExecutionEventType.SIGNAL:
                    signal = event.payload
                    record_event(event.event_type, timestamp, event.priority, identity=identity, parent=signal.signal_id, metadata={"direction": signal.direction.value}, economic={"signal_id": signal.signal_id, "record_sha256": signal.record_sha256})
                    for active in broker.active_orders(series_identity=identity):
                        handle_broker_result(broker.cancel_order(active.order_id, cancelled_at_utc=timestamp, reason="superseded_by_new_target"), timestamp, event.priority)
                    mark_info = portfolio.marks.get(event.series_hash)
                    if mark_info is None:
                        raise ValueError("signal has no point-in-time completed close reference price")
                    mode = self._accounting_mode(identity)
                    current = portfolio.position(identity, mode, timestamp)
                    sizing = size_signal(signal, current_quantity=current.quantity, reference_price=mark_info[1], accounting_mode=mode, configuration=request.configuration.sizing)
                    if sizing.is_no_action:
                        record_event(ExecutionEventType.NO_ACTION, timestamp, event.priority, identity=identity, parent=signal.signal_id, metadata={"reason": sizing.no_action_reason, "target_quantity": str(sizing.target_quantity)}, economic={"signal_id": signal.signal_id, "target_quantity": sizing.target_quantity, "current_quantity": sizing.current_quantity, "reason": sizing.no_action_reason, "sizing_config_sha256": sizing.config_sha256})
                        continue
                    limit_price, stop_price = self._order_prices(request.configuration, sizing.side, sizing.reference_price)
                    intent = OrderIntent(execution_run_id, signal.signal_id, identity, timestamp, sizing.side, request.configuration.order_type, abs(sizing.delta_quantity), sizing.target_quantity, sizing.current_quantity, sizing.delta_quantity, sizing.reference_price, mode, request.configuration.time_in_force, request.configuration.config_sha256, signal.record_sha256, request.implementation_code_sha256, request.repository_commit_sha, limit_price, stop_price, parent_ids=(signal.signal_id,), provenance={"sizing_config_sha256": sizing.config_sha256})
                    risk = risk_guard.assess(intent, price=sizing.reference_price, stage=RiskStage.PRE_SUBMIT, decision_timestamp_utc=timestamp, portfolio=self._risk_view(portfolio, broker, timestamp))
                    intent = replace(intent, status=OrderIntentStatus.SUBMITTED if risk.status is RiskDecisionStatus.ACCEPTED else OrderIntentStatus.BLOCKED)
                    intents.append(intent); intent_by_id[intent.order_intent_id] = intent; risk_decisions.append(risk)
                    record_event(ExecutionEventType.INTENT, timestamp, event.priority, identity=identity, parent=intent.order_intent_id, metadata={"status": intent.status.value}, economic=intent.economic_payload)
                    record_event(ExecutionEventType.RISK_DECISION, timestamp, event.priority, identity=identity, parent=risk.risk_decision_id, metadata={"stage": risk.stage.value, "status": risk.status.value, "reason_code": risk.reason_code}, economic=risk.economic_payload)
                    if risk.status is RiskDecisionStatus.ACCEPTED:
                        handle_broker_result(broker.submit_order_intent(intent, risk), timestamp, event.priority)
                elif event.event_type is ExecutionEventType.BAR_OPEN_EXECUTION:
                    bar = event.payload
                    open_event = record_event(event.event_type, timestamp, event.priority, identity=identity, metadata={"open": str(bar.open), "mark_source": MarkSource.BAR_OPEN.value}, economic={"series_identity_sha256": event.series_hash, "timestamp": timestamp, "open": bar.open, "mark_source": MarkSource.BAR_OPEN})
                    open_snapshot = portfolio.set_mark(identity, bar.open, timestamp, mark_source=MarkSource.BAR_OPEN, source_event_id=open_event.execution_event_id, logical_sequence=open_event.sequence)
                    if open_snapshot is not None:
                        record_event(ExecutionEventType.POSITION, timestamp, event.priority, identity=identity, parent=open_snapshot.position_snapshot_id, metadata={"source": "mark", "mark_source": open_snapshot.mark_source.value, "snapshot_kind": open_snapshot.snapshot_kind.value}, economic=open_snapshot.economic_payload)

                    def prefill(order, price, liquidity, fee):
                        return risk_guard.assess(intent_by_id[order.order_intent_id], price=price, stage=RiskStage.PRE_FILL, decision_timestamp_utc=timestamp, portfolio=self._risk_view(portfolio, broker, timestamp), fee_amount=fee, order_id=order.order_id)

                    handle_broker_result(broker.process_bar_open(series_identity=identity, timestamp_utc=timestamp, open_price=bar.open, risk_check=prefill), timestamp, event.priority)

            account = portfolio.snapshot_account(timestamp)
            record_event(ExecutionEventType.ACCOUNT, timestamp, 6, parent=account.account_snapshot_id, metadata={"equity": str(account.equity)}, economic=account.economic_payload)
            peak = max((row.equity for row in portfolio.account_snapshots), default=account.equity)
            drawdown = max(Decimal(0), peak - account.equity)
            drawdown_fraction = None if peak <= 0 else drawdown / peak
            equity_curve.append(EquityCurvePoint(execution_run_id, timestamp, account.cash, account.equity, drawdown, drawdown_fraction, account.gross_exposure, account.net_exposure, account.stale_mark_count, request.configuration.config_sha256))

        handle_broker_result(broker.expire_remaining_orders(expired_at_utc=completed_at), completed_at, 7)
        positions = tuple(sorted(portfolio.positions.values(), key=lambda row: row.series_identity.series_identity_sha256))
        orders = tuple(sorted(order_by_id.values(), key=lambda row: (row.submitted_at_utc, str(row.order_id))))
        metrics = calculate_metrics(initial_cash=request.configuration.initial_cash, fills=tuple(fills), intents=tuple(intents), orders=orders, positions=positions, snapshots=tuple(portfolio.position_snapshots), ledger=tuple(portfolio.ledger), funding_payments=tuple(funding_payments), equity_curve=tuple(equity_curve))
        metric_rows = metric_records(execution_run_id, metrics, request.configuration.config_sha256)
        run_payload = {"backtest_run_id": request.run_id, "run_id": execution_run_id, "signal_run_id": request.signal_run_id, "started_at_utc": started_at, "completed_at_utc": completed_at, "initial_cash": request.configuration.initial_cash, "base_currency": request.configuration.base_currency, "fee_currency": request.configuration.fees.fee_currency, "account_ref": request.configuration.broker.account_ref, "config_sha256": request.configuration.config_sha256, "data_sha256": request.data_sha256, "implementation_code_sha256": request.implementation_code_sha256, "repository_commit_sha": request.repository_commit_sha, "status": BacktestRunStatus.COMPLETED}
        run = BacktestRun(
            backtest_run_id=request.run_id,
            run_id=execution_run_id,
            signal_run_id=request.signal_run_id,
            started_at_utc=started_at,
            completed_at_utc=completed_at,
            status=BacktestRunStatus.COMPLETED,
            initial_cash=request.configuration.initial_cash,
            base_currency=request.configuration.base_currency,
            fee_currency=request.configuration.fees.fee_currency,
            account_ref=request.configuration.broker.account_ref,
            config_sha256=request.configuration.config_sha256,
            data_sha256=request.data_sha256,
            implementation_code_sha256=request.implementation_code_sha256,
            repository_commit_sha=request.repository_commit_sha,
            record_sha256=sha256_payload(run_payload),
            metadata={"public_safe": True, "run_identity_version": "phase5-backtest-run-v2"},
        )
        result = BacktestResult(run, tuple(intents), tuple(risk_decisions), orders, tuple(fills), positions, tuple(portfolio.position_snapshots), tuple(portfolio.ledger), tuple(funding_payments), tuple(portfolio.account_snapshots), tuple(audit_events), tuple(equity_curve), metrics, metric_rows)
        if self.persistence_repository is not None:
            self.persistence_repository.persist(result)
        return result
