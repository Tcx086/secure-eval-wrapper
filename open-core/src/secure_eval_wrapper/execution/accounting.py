"""Cash, position, funding, mark, and equity accounting driven only by fills."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from uuid import UUID

from secure_eval_wrapper.alpha.identity import SeriesIdentity
from secure_eval_wrapper.execution.models import (
    AccountSnapshot,
    AccountingMode,
    CashLedgerEntry,
    Fill,
    FundingPayment,
    LedgerEntryType,
    MarkSource,
    PositionSnapshot,
    PositionSnapshotKind,
    PositionState,
)
from secure_eval_wrapper.execution.positions import apply_fill_to_position, empty_position, unrealized_pnl


class Portfolio:
    """Mutable run-local accounting state; emitted records remain immutable."""

    def __init__(
        self,
        *,
        run_id,
        account_ref: str,
        initial_cash: Decimal,
        base_currency: str,
        config_sha256: str,
        started_at_utc: datetime,
    ) -> None:
        if not initial_cash.is_finite() or initial_cash < 0:
            raise ValueError("initial_cash must be finite and non-negative")
        if not isinstance(account_ref, str) or not account_ref.strip():
            raise ValueError("account_ref must be non-empty")
        self.run_id = run_id
        self.account_ref = account_ref.strip()
        self.initial_cash = initial_cash
        self.cash = Decimal(0)
        self.base_currency = base_currency.upper()
        self.config_sha256 = config_sha256
        self.positions: dict[str, PositionState] = {}
        self.marks: dict[str, tuple[SeriesIdentity, Decimal, datetime, MarkSource, UUID, int]] = {}
        self.ledger: list[CashLedgerEntry] = []
        self.position_snapshots: list[PositionSnapshot] = []
        self.account_snapshots: list[AccountSnapshot] = []
        self.applied_fill_ids: set[UUID] = set()
        self.total_fees = Decimal(0)
        self.total_funding = Decimal(0)
        self.peak_equity = initial_cash
        self._next_ledger_sequence = 0
        self._ledger(LedgerEntryType.INITIAL_CASH, initial_cash, started_at_utc)

    def _ledger(
        self,
        entry_type: LedgerEntryType,
        amount: Decimal,
        timestamp: datetime,
        *,
        series_identity: SeriesIdentity | None = None,
        fill_id=None,
        funding_payment_id=None,
    ) -> CashLedgerEntry:
        self.cash += amount
        row = CashLedgerEntry(
            run_id=self.run_id,
            event_timestamp_utc=timestamp,
            entry_type=entry_type,
            amount=amount,
            balance_after=self.cash,
            currency=self.base_currency,
            config_sha256=self.config_sha256,
            ledger_sequence=self._next_ledger_sequence,
            series_identity=series_identity,
            fill_id=fill_id,
            funding_payment_id=funding_payment_id,
        )
        self._next_ledger_sequence += 1
        self.ledger.append(row)
        return row

    def position(self, identity: SeriesIdentity, mode: AccountingMode, timestamp: datetime) -> PositionState:
        key = identity.series_identity_sha256
        existing = self.positions.get(key)
        return existing if existing is not None else empty_position(
            run_id=self.run_id,
            account_ref=self.account_ref,
            series_identity=identity,
            accounting_mode=mode,
            timestamp_utc=timestamp,
            config_sha256=self.config_sha256,
        )

    def apply_fill(self, fill: Fill, *, source_event_id: UUID, logical_sequence: int) -> PositionSnapshot:
        if fill.fill_id in self.applied_fill_ids:
            raise ValueError("the same fill cannot be applied twice")
        if fill.fee_currency != self.base_currency:
            raise ValueError("fill fee currency must equal Portfolio base currency; FX conversion is not supported")
        state = self.position(fill.series_identity, fill.accounting_mode, fill.filled_at_utc)
        transition = apply_fill_to_position(state, fill)
        if fill.accounting_mode is AccountingMode.SPOT:
            notional_cash = -fill.notional if fill.side.value == "buy" else fill.notional
            self._ledger(LedgerEntryType.SPOT_NOTIONAL, notional_cash, fill.filled_at_utc, series_identity=fill.series_identity, fill_id=fill.fill_id)
        elif transition.realized_pnl_delta != 0:
            self._ledger(LedgerEntryType.REALIZED_PNL, transition.realized_pnl_delta, fill.filled_at_utc, series_identity=fill.series_identity, fill_id=fill.fill_id)
        if fill.fee_amount != 0:
            fee_row = self._ledger(LedgerEntryType.FEE, -fill.fee_amount, fill.filled_at_utc, series_identity=fill.series_identity, fill_id=fill.fill_id)
            if fee_row.currency != fill.fee_currency:
                raise AssertionError("fee ledger currency must equal fill fee currency")
        self.total_fees += fill.fee_amount
        self.positions[fill.series_identity.series_identity_sha256] = transition.state
        self.applied_fill_ids.add(fill.fill_id)
        return self.snapshot_position(
            transition.state,
            fill.filled_at_utc,
            snapshot_kind=PositionSnapshotKind.FILL,
            source_event_id=source_event_id,
            logical_sequence=logical_sequence,
            source_fill_id=fill.fill_id,
        )

    def set_mark(
        self,
        identity: SeriesIdentity,
        price: Decimal,
        timestamp: datetime,
        *,
        mark_source: MarkSource,
        source_event_id: UUID,
        logical_sequence: int,
    ) -> PositionSnapshot | None:
        if not price.is_finite() or price <= 0:
            raise ValueError("mark price must be finite and positive")
        mark_source = MarkSource(mark_source)
        self.marks[identity.series_identity_sha256] = (identity, price, timestamp, mark_source, source_event_id, logical_sequence)
        state = self.positions.get(identity.series_identity_sha256)
        if state is None:
            return None
        snapshot_kind = PositionSnapshotKind.BAR_OPEN_MARK if mark_source is MarkSource.BAR_OPEN else PositionSnapshotKind.BAR_CLOSE_MARK
        return self.snapshot_position(
            state,
            timestamp,
            snapshot_kind=snapshot_kind,
            source_event_id=source_event_id,
            logical_sequence=logical_sequence,
        )

    def snapshot_position(
        self,
        state: PositionState,
        timestamp: datetime,
        *,
        snapshot_kind: PositionSnapshotKind,
        source_event_id: UUID,
        logical_sequence: int,
        source_fill_id=None,
    ) -> PositionSnapshot:
        mark_info = self.marks.get(state.series_identity.series_identity_sha256)
        mark = None if mark_info is None else mark_info[1]
        age = None if mark_info is None else Decimal(str((timestamp - mark_info[2]).total_seconds()))
        mark_source = None if mark_info is None else mark_info[3]
        snap = PositionSnapshot(
            run_id=self.run_id,
            account_ref=self.account_ref,
            position_id=state.position_id,
            series_identity=state.series_identity,
            accounting_mode=state.accounting_mode,
            snapshot_at_utc=timestamp,
            quantity=state.quantity,
            average_entry_price=state.average_entry_price,
            mark_price=mark,
            realized_pnl=state.realized_pnl,
            unrealized_pnl=unrealized_pnl(state, mark),
            stale_mark_age_seconds=age,
            config_sha256=self.config_sha256,
            snapshot_kind=snapshot_kind,
            mark_source=mark_source,
            source_event_id=source_event_id,
            logical_sequence=logical_sequence,
            source_fill_id=source_fill_id,
        )
        self.position_snapshots.append(snap)
        return snap

    def apply_funding(self, payment: FundingPayment) -> CashLedgerEntry:
        self.total_funding += payment.cash_flow
        return self._ledger(LedgerEntryType.FUNDING, payment.cash_flow, payment.funding_timestamp_utc, series_identity=payment.series_identity, funding_payment_id=payment.funding_payment_id)

    def values(self, timestamp: datetime) -> tuple[Decimal, Decimal, Decimal, Decimal, Decimal, int]:
        equity = self.cash
        gross = Decimal(0)
        net = Decimal(0)
        unrealized = Decimal(0)
        stale = 0
        for key, state in self.positions.items():
            mark_info = self.marks.get(key)
            if mark_info is None:
                stale += 1
                continue
            mark = mark_info[1]
            exposure = state.quantity * mark
            gross += abs(exposure)
            net += exposure
            if mark_info[2] < timestamp:
                stale += 1
            value = unrealized_pnl(state, mark)
            unrealized += value
            if state.accounting_mode is AccountingMode.SPOT:
                equity += exposure
            else:
                equity += value
        realized = sum((state.realized_pnl for state in self.positions.values()), Decimal(0))
        return equity, gross, net, realized, unrealized, stale

    def snapshot_account(self, timestamp: datetime) -> AccountSnapshot:
        equity, gross, net, realized, unrealized, stale = self.values(timestamp)
        snapshot = AccountSnapshot(
            run_id=self.run_id,
            account_ref=self.account_ref,
            snapshot_at_utc=timestamp,
            cash=self.cash,
            equity=equity,
            gross_exposure=gross,
            net_exposure=net,
            realized_pnl=realized,
            unrealized_pnl=unrealized,
            total_fees=self.total_fees,
            total_funding=self.total_funding,
            config_sha256=self.config_sha256,
            stale_mark_count=stale,
        )
        self.account_snapshots.append(snapshot)
        self.peak_equity = max(self.peak_equity, equity)
        return snapshot
