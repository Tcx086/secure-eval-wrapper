"""Immutable public contracts for deterministic simulated execution."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from enum import Enum
from types import MappingProxyType
from typing import Mapping
from uuid import NAMESPACE_URL, UUID, uuid5

from secure_eval_wrapper.alpha.identity import SeriesIdentity
from secure_eval_wrapper.data_collection.hashing import sha256_payload
from secure_eval_wrapper.data_collection.time_utils import require_utc_datetime

_SHA256 = re.compile(r"^[0-9a-f]{64}$")


def _finite(value: Decimal, name: str, *, positive: bool = False, non_negative: bool = False) -> Decimal:
    if not isinstance(value, Decimal) or not value.is_finite():
        raise ValueError(f"{name} must be a finite Decimal")
    if positive and value <= 0:
        raise ValueError(f"{name} must be positive")
    if non_negative and value < 0:
        raise ValueError(f"{name} must be non-negative")
    return value


def _optional_finite(value: Decimal | None, name: str, *, positive: bool = False) -> Decimal | None:
    if value is not None:
        _finite(value, name, positive=positive)
    return value


def _hash(value: str, name: str) -> str:
    if not isinstance(value, str) or not _SHA256.fullmatch(value):
        raise ValueError(f"{name} must be a lowercase SHA-256 digest")
    return value


def _text(value: str, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be non-empty")
    return value.strip()


def _public_map(value: Mapping[str, object]) -> Mapping[str, object]:
    return MappingProxyType(dict(value))


def deterministic_uuid(kind: str, payload: object) -> UUID:
    return uuid5(NAMESPACE_URL, f"secure-eval-wrapper:{kind}:{sha256_payload(payload)}")


class ExecutionMode(str, Enum):
    BACKTEST = "backtest"


class AccountingMode(str, Enum):
    SPOT = "spot"
    LINEAR_PERPETUAL = "linear_perpetual"


class OrderSide(str, Enum):
    BUY = "buy"
    SELL = "sell"

    @property
    def sign(self) -> Decimal:
        return Decimal(1) if self is OrderSide.BUY else Decimal(-1)


class OrderType(str, Enum):
    MARKET = "market"
    LIMIT = "limit"
    STOP = "stop"
    STOP_LIMIT = "stop_limit"


class OrderIntentStatus(str, Enum):
    CREATED = "created"
    NO_ACTION = "no_action"
    BLOCKED = "blocked"
    SUBMITTED = "submitted"
    CANCELLED = "cancelled"


class OrderStatus(str, Enum):
    ACKNOWLEDGED = "acknowledged"
    TRIGGERED = "triggered"
    FILLED = "filled"
    CANCELLED = "cancelled"
    REJECTED = "rejected"
    EXPIRED = "expired"


class TimeInForce(str, Enum):
    GTC = "gtc"
    IOC = "ioc"


class LiquidityFlag(str, Enum):
    MAKER = "maker"
    TAKER = "taker"


class RejectReason(str, Enum):
    RISK_BLOCKED = "risk_blocked"
    UNSUPPORTED = "unsupported"
    INVALID_ORDER = "invalid_order"
    INSUFFICIENT_CASH = "insufficient_cash"
    SPOT_SHORT = "spot_short_prohibited"
    LIMIT_BREACH = "limit_breach"


class RiskDecisionStatus(str, Enum):
    ACCEPTED = "accepted"
    BLOCKED = "blocked"


class RiskStage(str, Enum):
    PRE_SUBMIT = "pre_submit"
    PRE_FILL = "pre_fill"


class ExecutionEventType(str, Enum):
    BAR_COMPLETED_EXECUTION = "bar_completed_execution"
    MARK_UPDATE = "mark_update"
    FUNDING = "funding"
    SIGNAL = "signal"
    BAR_OPEN_EXECUTION = "bar_open_execution"
    INTENT = "intent"
    RISK_DECISION = "risk_decision"
    ORDER_ACKNOWLEDGED = "order_acknowledged"
    ORDER_CANCELLED = "order_cancelled"
    ORDER_REJECTED = "order_rejected"
    ORDER_TRIGGERED = "order_triggered"
    ORDER_EXPIRED = "order_expired"
    FILL = "fill"
    POSITION = "position"
    ACCOUNT = "account"
    NO_ACTION = "no_action"


class MarkSource(str, Enum):
    BAR_OPEN = "bar_open"
    BAR_CLOSE = "bar_close"


class PositionSnapshotKind(str, Enum):
    FILL = "fill"
    BAR_OPEN_MARK = "bar_open_mark"
    BAR_CLOSE_MARK = "bar_close_mark"


class LedgerEntryType(str, Enum):
    INITIAL_CASH = "initial_cash"
    SPOT_NOTIONAL = "spot_notional"
    REALIZED_PNL = "realized_pnl"
    FEE = "fee"
    FUNDING = "funding"


@dataclass(frozen=True)
class BrokerConfiguration:
    execution_mode: ExecutionMode = ExecutionMode.BACKTEST
    account_ref: str = "public-simulation"

    def __post_init__(self) -> None:
        object.__setattr__(self, "execution_mode", ExecutionMode(self.execution_mode))
        object.__setattr__(self, "account_ref", _text(self.account_ref, "account_ref"))

    @property
    def config_sha256(self) -> str:
        return sha256_payload({"execution_mode": self.execution_mode, "account_ref": self.account_ref})


@dataclass(frozen=True)
class FeeConfiguration:
    maker_bps: Decimal = Decimal(0)
    taker_bps: Decimal = Decimal(0)
    fee_currency: str = "USDT"

    def __post_init__(self) -> None:
        _finite(self.maker_bps, "maker_bps", non_negative=True)
        _finite(self.taker_bps, "taker_bps", non_negative=True)
        object.__setattr__(self, "fee_currency", _text(self.fee_currency, "fee_currency").upper())

    @property
    def config_sha256(self) -> str:
        return sha256_payload({"maker_bps": self.maker_bps, "taker_bps": self.taker_bps, "fee_currency": self.fee_currency})


@dataclass(frozen=True)
class SlippageConfiguration:
    adverse_bps: Decimal = Decimal(0)

    def __post_init__(self) -> None:
        _finite(self.adverse_bps, "adverse_bps", non_negative=True)

    @property
    def config_sha256(self) -> str:
        return sha256_payload({"adverse_bps": self.adverse_bps})


@dataclass(frozen=True)
class RiskLimitConfiguration:
    max_order_notional: Decimal | None = None
    max_position_notional_per_series: Decimal | None = None
    max_gross_exposure: Decimal | None = None
    max_net_exposure: Decimal | None = None
    max_open_orders_per_series: int = 1
    max_gross_exposure_to_equity: Decimal | None = None
    max_drawdown_fraction: Decimal | None = None
    prohibit_spot_shorts: bool = True

    def __post_init__(self) -> None:
        for name in ("max_order_notional", "max_position_notional_per_series", "max_gross_exposure", "max_net_exposure", "max_gross_exposure_to_equity"):
            value = getattr(self, name)
            if value is not None:
                _finite(value, name, positive=True)
        if self.max_drawdown_fraction is not None:
            _finite(self.max_drawdown_fraction, "max_drawdown_fraction", non_negative=True)
            if self.max_drawdown_fraction > 1:
                raise ValueError("max_drawdown_fraction must be in [0, 1]")
        if isinstance(self.max_open_orders_per_series, bool) or self.max_open_orders_per_series < 1:
            raise ValueError("max_open_orders_per_series must be a positive integer")

    @property
    def config_sha256(self) -> str:
        return sha256_payload({name: getattr(self, name) for name in self.__dataclass_fields__})


@dataclass(frozen=True)
class OrderIntent:
    run_id: UUID
    signal_id: UUID
    series_identity: SeriesIdentity
    event_timestamp_utc: datetime
    side: OrderSide
    order_type: OrderType
    quantity: Decimal
    target_quantity: Decimal
    current_quantity: Decimal
    delta_quantity: Decimal
    reference_price: Decimal
    accounting_mode: AccountingMode
    time_in_force: TimeInForce
    config_sha256: str
    data_sha256: str
    implementation_code_sha256: str
    repository_commit_sha: str
    limit_price: Decimal | None = None
    stop_price: Decimal | None = None
    status: OrderIntentStatus = OrderIntentStatus.CREATED
    parent_ids: tuple[UUID, ...] = ()
    provenance: Mapping[str, object] = field(default_factory=dict)
    order_intent_id: UUID | None = None

    def __post_init__(self) -> None:
        require_utc_datetime(self.event_timestamp_utc, field_name="OrderIntent event_timestamp_utc")
        object.__setattr__(self, "side", OrderSide(self.side))
        object.__setattr__(self, "order_type", OrderType(self.order_type))
        object.__setattr__(self, "accounting_mode", AccountingMode(self.accounting_mode))
        object.__setattr__(self, "time_in_force", TimeInForce(self.time_in_force))
        object.__setattr__(self, "status", OrderIntentStatus(self.status))
        _finite(self.quantity, "quantity", positive=True)
        for name in ("target_quantity", "current_quantity", "delta_quantity"):
            _finite(getattr(self, name), name)
        _finite(self.reference_price, "reference_price", positive=True)
        _optional_finite(self.limit_price, "limit_price", positive=True)
        _optional_finite(self.stop_price, "stop_price", positive=True)
        if self.side.sign * self.delta_quantity <= 0:
            raise ValueError("side must agree with signed delta_quantity")
        if abs(self.delta_quantity) != self.quantity:
            raise ValueError("quantity must equal absolute delta_quantity")
        if self.order_type in (OrderType.LIMIT, OrderType.STOP_LIMIT) and self.limit_price is None:
            raise ValueError("limit and stop-limit orders require limit_price")
        if self.order_type in (OrderType.STOP, OrderType.STOP_LIMIT) and self.stop_price is None:
            raise ValueError("stop and stop-limit orders require stop_price")
        for name in ("config_sha256", "data_sha256", "implementation_code_sha256"):
            _hash(getattr(self, name), name)
        object.__setattr__(self, "repository_commit_sha", _text(self.repository_commit_sha, "repository_commit_sha"))
        object.__setattr__(self, "provenance", _public_map(self.provenance))
        expected = deterministic_uuid("order-intent", self.economic_payload)
        if self.order_intent_id is not None and self.order_intent_id != expected:
            raise ValueError("order_intent_id does not match deterministic economic identity")
        object.__setattr__(self, "order_intent_id", expected)

    @property
    def economic_payload(self) -> dict[str, object]:
        return {"run_id": self.run_id, "signal_id": self.signal_id, "series_identity_sha256": self.series_identity.series_identity_sha256, "event_timestamp_utc": self.event_timestamp_utc, "side": self.side, "order_type": self.order_type, "quantity": self.quantity, "target_quantity": self.target_quantity, "current_quantity": self.current_quantity, "delta_quantity": self.delta_quantity, "reference_price": self.reference_price, "limit_price": self.limit_price, "stop_price": self.stop_price, "accounting_mode": self.accounting_mode, "time_in_force": self.time_in_force, "config_sha256": self.config_sha256, "data_sha256": self.data_sha256, "implementation_code_sha256": self.implementation_code_sha256, "repository_commit_sha": self.repository_commit_sha}

    @property
    def record_sha256(self) -> str:
        return sha256_payload(self.economic_payload | {"status": self.status})


@dataclass(frozen=True)
class RiskDecision:
    run_id: UUID
    order_intent_id: UUID
    series_identity: SeriesIdentity
    decision_timestamp_utc: datetime
    stage: RiskStage
    status: RiskDecisionStatus
    reason_code: str
    explanation: str
    config_sha256: str
    observed_value: Decimal | None = None
    configured_limit: Decimal | None = None
    relevant_limit: str | None = None
    order_id: UUID | None = None
    parent_ids: tuple[UUID, ...] = ()
    provenance: Mapping[str, object] = field(default_factory=dict)
    risk_decision_id: UUID | None = None

    def __post_init__(self) -> None:
        require_utc_datetime(self.decision_timestamp_utc, field_name="RiskDecision decision_timestamp_utc")
        object.__setattr__(self, "stage", RiskStage(self.stage))
        object.__setattr__(self, "status", RiskDecisionStatus(self.status))
        object.__setattr__(self, "reason_code", _text(self.reason_code, "reason_code"))
        object.__setattr__(self, "explanation", _text(self.explanation, "explanation"))
        _hash(self.config_sha256, "config_sha256")
        _optional_finite(self.observed_value, "observed_value")
        _optional_finite(self.configured_limit, "configured_limit")
        if self.stage is RiskStage.PRE_FILL and self.order_id is None:
            raise ValueError("pre-fill RiskDecision requires order_id")
        if self.stage is RiskStage.PRE_SUBMIT and self.order_id is not None:
            raise ValueError("pre-submit RiskDecision must not have order_id")
        required_parents = (self.order_intent_id,) if self.order_id is None else (self.order_intent_id, self.order_id)
        if self.parent_ids:
            if not set(required_parents).issubset(self.parent_ids):
                raise ValueError("RiskDecision parent_ids must contain its order lineage")
        else:
            object.__setattr__(self, "parent_ids", required_parents)
        object.__setattr__(self, "provenance", _public_map(self.provenance))
        expected = deterministic_uuid("risk-decision", self.economic_payload)
        if self.risk_decision_id is not None and self.risk_decision_id != expected:
            raise ValueError("risk_decision_id does not match deterministic identity")
        object.__setattr__(self, "risk_decision_id", expected)

    @property
    def economic_payload(self) -> dict[str, object]:
        return {"run_id": self.run_id, "order_intent_id": self.order_intent_id, "order_id": self.order_id, "series_identity_sha256": self.series_identity.series_identity_sha256, "decision_timestamp_utc": self.decision_timestamp_utc, "stage": self.stage, "status": self.status, "reason_code": self.reason_code, "relevant_limit": self.relevant_limit, "observed_value": self.observed_value, "configured_limit": self.configured_limit, "config_sha256": self.config_sha256}

    @property
    def record_sha256(self) -> str:
        return sha256_payload(self.economic_payload | {"explanation": self.explanation})


@dataclass(frozen=True)
class SimulatedOrder:
    run_id: UUID
    order_intent_id: UUID
    series_identity: SeriesIdentity
    submitted_at_utc: datetime
    side: OrderSide
    order_type: OrderType
    quantity: Decimal
    accounting_mode: AccountingMode
    time_in_force: TimeInForce
    status: OrderStatus
    config_sha256: str
    limit_price: Decimal | None = None
    stop_price: Decimal | None = None
    triggered_at_utc: datetime | None = None
    activation_reason: str | None = None
    reject_reason: RejectReason | None = None
    parent_ids: tuple[UUID, ...] = ()
    provenance: Mapping[str, object] = field(default_factory=dict)
    order_id: UUID | None = None

    def __post_init__(self) -> None:
        require_utc_datetime(self.submitted_at_utc, field_name="SimulatedOrder submitted_at_utc")
        if self.triggered_at_utc is not None:
            require_utc_datetime(self.triggered_at_utc, field_name="SimulatedOrder triggered_at_utc")
        for name, enum in (("side", OrderSide), ("order_type", OrderType), ("accounting_mode", AccountingMode), ("time_in_force", TimeInForce), ("status", OrderStatus)):
            object.__setattr__(self, name, enum(getattr(self, name)))
        if self.reject_reason is not None:
            object.__setattr__(self, "reject_reason", RejectReason(self.reject_reason))
        _finite(self.quantity, "quantity", positive=True)
        _optional_finite(self.limit_price, "limit_price", positive=True)
        _optional_finite(self.stop_price, "stop_price", positive=True)
        _hash(self.config_sha256, "config_sha256")
        object.__setattr__(self, "provenance", _public_map(self.provenance))
        expected = deterministic_uuid("simulated-order", {"run_id": self.run_id, "order_intent_id": self.order_intent_id})
        if self.order_id is not None and self.order_id != expected:
            raise ValueError("order_id does not match deterministic identity")
        object.__setattr__(self, "order_id", expected)

    @property
    def record_sha256(self) -> str:
        return sha256_payload({"order_id": self.order_id, "run_id": self.run_id, "order_intent_id": self.order_intent_id, "series_identity_sha256": self.series_identity.series_identity_sha256, "submitted_at_utc": self.submitted_at_utc, "side": self.side, "order_type": self.order_type, "quantity": self.quantity, "limit_price": self.limit_price, "stop_price": self.stop_price, "accounting_mode": self.accounting_mode, "time_in_force": self.time_in_force, "status": self.status, "triggered_at_utc": self.triggered_at_utc, "activation_reason": self.activation_reason, "reject_reason": self.reject_reason, "config_sha256": self.config_sha256})


@dataclass(frozen=True)
class Fill:
    run_id: UUID
    order_id: UUID
    order_intent_id: UUID
    series_identity: SeriesIdentity
    filled_at_utc: datetime
    side: OrderSide
    quantity: Decimal
    base_price: Decimal
    price: Decimal
    accounting_mode: AccountingMode
    liquidity_flag: LiquidityFlag
    fee_amount: Decimal
    fee_currency: str
    slippage_amount: Decimal
    slippage_bps: Decimal
    fill_reason: str
    config_sha256: str
    parent_ids: tuple[UUID, ...] = ()
    provenance: Mapping[str, object] = field(default_factory=dict)
    fill_id: UUID | None = None

    def __post_init__(self) -> None:
        require_utc_datetime(self.filled_at_utc, field_name="Fill filled_at_utc")
        for name, enum in (("side", OrderSide), ("accounting_mode", AccountingMode), ("liquidity_flag", LiquidityFlag)):
            object.__setattr__(self, name, enum(getattr(self, name)))
        for name in ("quantity", "base_price", "price"):
            _finite(getattr(self, name), name, positive=True)
        for name in ("fee_amount", "slippage_amount", "slippage_bps"):
            _finite(getattr(self, name), name, non_negative=True)
        object.__setattr__(self, "fee_currency", _text(self.fee_currency, "fee_currency").upper())
        object.__setattr__(self, "fill_reason", _text(self.fill_reason, "fill_reason"))
        _hash(self.config_sha256, "config_sha256")
        object.__setattr__(self, "provenance", _public_map(self.provenance))
        expected = deterministic_uuid("fill", self.economic_payload)
        if self.fill_id is not None and self.fill_id != expected:
            raise ValueError("fill_id does not match deterministic identity")
        object.__setattr__(self, "fill_id", expected)

    @property
    def notional(self) -> Decimal:
        return self.quantity * self.price

    @property
    def economic_payload(self) -> dict[str, object]:
        return {"run_id": self.run_id, "order_id": self.order_id, "order_intent_id": self.order_intent_id, "series_identity_sha256": self.series_identity.series_identity_sha256, "filled_at_utc": self.filled_at_utc, "side": self.side, "quantity": self.quantity, "base_price": self.base_price, "price": self.price, "accounting_mode": self.accounting_mode, "liquidity_flag": self.liquidity_flag, "fee_amount": self.fee_amount, "fee_currency": self.fee_currency, "slippage_amount": self.slippage_amount, "slippage_bps": self.slippage_bps, "fill_reason": self.fill_reason, "config_sha256": self.config_sha256}

    @property
    def record_sha256(self) -> str:
        return sha256_payload(self.economic_payload)


@dataclass(frozen=True)
class PositionState:
    run_id: UUID
    account_ref: str
    series_identity: SeriesIdentity
    accounting_mode: AccountingMode
    quantity: Decimal
    average_entry_price: Decimal | None
    realized_pnl: Decimal
    updated_at_utc: datetime
    config_sha256: str
    source_fill_ids: tuple[UUID, ...] = ()
    position_id: UUID | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "account_ref", _text(self.account_ref, "account_ref"))
        object.__setattr__(self, "accounting_mode", AccountingMode(self.accounting_mode))
        require_utc_datetime(self.updated_at_utc, field_name="PositionState updated_at_utc")
        _finite(self.quantity, "quantity")
        _finite(self.realized_pnl, "realized_pnl")
        _optional_finite(self.average_entry_price, "average_entry_price", positive=True)
        if self.quantity == 0 and self.average_entry_price is not None:
            raise ValueError("zero quantity requires null average_entry_price")
        if self.quantity != 0 and self.average_entry_price is None:
            raise ValueError("non-zero quantity requires average_entry_price")
        if self.accounting_mode is AccountingMode.SPOT and self.quantity < 0:
            raise ValueError("Spot position cannot be negative")
        _hash(self.config_sha256, "config_sha256")
        expected = deterministic_uuid("position", {"run_id": self.run_id, "account_ref": self.account_ref, "series_identity_sha256": self.series_identity.series_identity_sha256})
        if self.position_id is not None and self.position_id != expected:
            raise ValueError("position_id does not match deterministic identity")
        object.__setattr__(self, "position_id", expected)

    @property
    def record_sha256(self) -> str:
        return sha256_payload({"position_id": self.position_id, "run_id": self.run_id, "account_ref": self.account_ref, "series_identity_sha256": self.series_identity.series_identity_sha256, "accounting_mode": self.accounting_mode, "quantity": self.quantity, "average_entry_price": self.average_entry_price, "realized_pnl": self.realized_pnl, "updated_at_utc": self.updated_at_utc, "source_fill_ids": self.source_fill_ids, "config_sha256": self.config_sha256})


@dataclass(frozen=True)
class PositionSnapshot:
    run_id: UUID
    account_ref: str
    position_id: UUID
    series_identity: SeriesIdentity
    accounting_mode: AccountingMode
    snapshot_at_utc: datetime
    quantity: Decimal
    average_entry_price: Decimal | None
    mark_price: Decimal | None
    realized_pnl: Decimal
    unrealized_pnl: Decimal
    stale_mark_age_seconds: Decimal | None
    config_sha256: str
    snapshot_kind: PositionSnapshotKind
    mark_source: MarkSource | None
    source_event_id: UUID
    logical_sequence: int
    source_fill_id: UUID | None = None
    parent_ids: tuple[UUID, ...] = ()
    position_snapshot_id: UUID | None = None

    def __post_init__(self) -> None:
        require_utc_datetime(self.snapshot_at_utc, field_name="PositionSnapshot snapshot_at_utc")
        object.__setattr__(self, "account_ref", _text(self.account_ref, "account_ref"))
        object.__setattr__(self, "accounting_mode", AccountingMode(self.accounting_mode))
        object.__setattr__(self, "snapshot_kind", PositionSnapshotKind(self.snapshot_kind))
        if self.mark_source is not None:
            object.__setattr__(self, "mark_source", MarkSource(self.mark_source))
        for name in ("quantity", "realized_pnl", "unrealized_pnl"):
            _finite(getattr(self, name), name)
        _optional_finite(self.average_entry_price, "average_entry_price", positive=True)
        _optional_finite(self.mark_price, "mark_price", positive=True)
        if self.stale_mark_age_seconds is not None:
            _finite(self.stale_mark_age_seconds, "stale_mark_age_seconds", non_negative=True)
        if isinstance(self.logical_sequence, bool) or self.logical_sequence < 0:
            raise ValueError("logical_sequence must be a non-negative integer")
        if self.snapshot_kind is PositionSnapshotKind.FILL and self.source_fill_id is None:
            raise ValueError("fill snapshot requires source_fill_id")
        if self.snapshot_kind is not PositionSnapshotKind.FILL and self.source_fill_id is not None:
            raise ValueError("mark snapshot cannot have source_fill_id")
        expected_mark_source = {
            PositionSnapshotKind.BAR_OPEN_MARK: MarkSource.BAR_OPEN,
            PositionSnapshotKind.BAR_CLOSE_MARK: MarkSource.BAR_CLOSE,
        }.get(self.snapshot_kind)
        if expected_mark_source is not None and self.mark_source is not expected_mark_source:
            raise ValueError("mark snapshot kind and mark_source disagree")
        if self.mark_price is not None and self.mark_source is None:
            raise ValueError("marked snapshot requires mark_source provenance")
        _hash(self.config_sha256, "config_sha256")
        required_parents = (self.position_id, self.source_event_id) + (() if self.source_fill_id is None else (self.source_fill_id,))
        if self.parent_ids:
            if not set(required_parents).issubset(self.parent_ids):
                raise ValueError("PositionSnapshot parent_ids must contain logical source lineage")
        else:
            object.__setattr__(self, "parent_ids", required_parents)
        expected = deterministic_uuid("position-snapshot", self.logical_identity_payload)
        if self.position_snapshot_id is not None and self.position_snapshot_id != expected:
            raise ValueError("position_snapshot_id does not match deterministic logical identity")
        object.__setattr__(self, "position_snapshot_id", expected)

    @property
    def logical_identity_payload(self) -> dict[str, object]:
        return {
            "run_id": self.run_id,
            "account_ref": self.account_ref,
            "position_id": self.position_id,
            "snapshot_at_utc": self.snapshot_at_utc,
            "snapshot_kind": self.snapshot_kind,
            "source_event_id": self.source_event_id,
            "logical_sequence": self.logical_sequence,
            "source_fill_id": self.source_fill_id,
        }

    @property
    def economic_payload(self) -> dict[str, object]:
        return self.logical_identity_payload | {
            "series_identity_sha256": self.series_identity.series_identity_sha256,
            "accounting_mode": self.accounting_mode,
            "quantity": self.quantity,
            "average_entry_price": self.average_entry_price,
            "mark_price": self.mark_price,
            "mark_source": self.mark_source,
            "realized_pnl": self.realized_pnl,
            "unrealized_pnl": self.unrealized_pnl,
            "stale_mark_age_seconds": self.stale_mark_age_seconds,
            "config_sha256": self.config_sha256,
        }

    @property
    def record_sha256(self) -> str:
        return sha256_payload(self.economic_payload)

@dataclass(frozen=True)
class CashLedgerEntry:
    run_id: UUID
    event_timestamp_utc: datetime
    entry_type: LedgerEntryType
    amount: Decimal
    balance_after: Decimal
    currency: str
    config_sha256: str
    ledger_sequence: int
    series_identity: SeriesIdentity | None = None
    fill_id: UUID | None = None
    funding_payment_id: UUID | None = None
    parent_ids: tuple[UUID, ...] = ()
    cash_ledger_entry_id: UUID | None = None

    def __post_init__(self) -> None:
        require_utc_datetime(self.event_timestamp_utc, field_name="CashLedgerEntry event_timestamp_utc")
        object.__setattr__(self, "entry_type", LedgerEntryType(self.entry_type))
        _finite(self.amount, "amount")
        _finite(self.balance_after, "balance_after")
        object.__setattr__(self, "currency", _text(self.currency, "currency").upper())
        if isinstance(self.ledger_sequence, bool) or self.ledger_sequence < 0:
            raise ValueError("ledger_sequence must be a non-negative integer")
        _hash(self.config_sha256, "config_sha256")
        expected = deterministic_uuid("cash-ledger", self.logical_identity_payload)
        if self.cash_ledger_entry_id is not None and self.cash_ledger_entry_id != expected:
            raise ValueError("cash_ledger_entry_id does not match deterministic logical identity")
        object.__setattr__(self, "cash_ledger_entry_id", expected)

    @property
    def logical_identity_payload(self) -> dict[str, object]:
        return {
            "run_id": self.run_id,
            "ledger_sequence": self.ledger_sequence,
            "event_timestamp_utc": self.event_timestamp_utc,
            "entry_type": self.entry_type,
            "series_identity_sha256": None if self.series_identity is None else self.series_identity.series_identity_sha256,
            "fill_id": self.fill_id,
            "funding_payment_id": self.funding_payment_id,
        }

    @property
    def economic_payload(self) -> dict[str, object]:
        return self.logical_identity_payload | {
            "amount": self.amount,
            "balance_after": self.balance_after,
            "currency": self.currency,
            "config_sha256": self.config_sha256,
        }

    @property
    def record_sha256(self) -> str:
        return sha256_payload(self.economic_payload)

@dataclass(frozen=True)
class FundingPayment:
    run_id: UUID
    series_identity: SeriesIdentity
    funding_rate_id: UUID
    funding_timestamp_utc: datetime
    signed_quantity: Decimal
    mark_price: Decimal
    funding_rate: Decimal
    cash_flow: Decimal
    funding_interval: str
    funding_interval_source: str
    config_sha256: str
    source_observation_ids: tuple[UUID, ...] = ()
    parent_ids: tuple[UUID, ...] = ()
    provenance: Mapping[str, object] = field(default_factory=dict)
    funding_payment_id: UUID | None = None

    def __post_init__(self) -> None:
        require_utc_datetime(self.funding_timestamp_utc, field_name="FundingPayment funding_timestamp_utc")
        for name in ("signed_quantity", "funding_rate", "cash_flow"):
            _finite(getattr(self, name), name)
        _finite(self.mark_price, "mark_price", positive=True)
        object.__setattr__(self, "funding_interval", _text(self.funding_interval, "funding_interval"))
        object.__setattr__(self, "funding_interval_source", _text(self.funding_interval_source, "funding_interval_source"))
        _hash(self.config_sha256, "config_sha256")
        object.__setattr__(self, "provenance", _public_map(self.provenance))
        expected = deterministic_uuid("funding-payment", self.economic_payload)
        if self.funding_payment_id is not None and self.funding_payment_id != expected:
            raise ValueError("funding_payment_id does not match deterministic identity")
        object.__setattr__(self, "funding_payment_id", expected)

    @property
    def economic_payload(self) -> dict[str, object]:
        return {"run_id": self.run_id, "series_identity_sha256": self.series_identity.series_identity_sha256, "funding_rate_id": self.funding_rate_id, "funding_timestamp_utc": self.funding_timestamp_utc, "signed_quantity": self.signed_quantity, "mark_price": self.mark_price, "funding_rate": self.funding_rate, "cash_flow": self.cash_flow, "funding_interval": self.funding_interval, "funding_interval_source": self.funding_interval_source, "config_sha256": self.config_sha256}

    @property
    def record_sha256(self) -> str:
        return sha256_payload(self.economic_payload)


@dataclass(frozen=True)
class AccountSnapshot:
    run_id: UUID
    account_ref: str
    snapshot_at_utc: datetime
    cash: Decimal
    equity: Decimal
    gross_exposure: Decimal
    net_exposure: Decimal
    realized_pnl: Decimal
    unrealized_pnl: Decimal
    total_fees: Decimal
    total_funding: Decimal
    config_sha256: str
    stale_mark_count: int = 0
    parent_ids: tuple[UUID, ...] = ()
    account_snapshot_id: UUID | None = None

    def __post_init__(self) -> None:
        require_utc_datetime(self.snapshot_at_utc, field_name="AccountSnapshot snapshot_at_utc")
        object.__setattr__(self, "account_ref", _text(self.account_ref, "account_ref"))
        for name in ("cash", "equity", "gross_exposure", "net_exposure", "realized_pnl", "unrealized_pnl", "total_fees", "total_funding"):
            _finite(getattr(self, name), name)
        if self.gross_exposure < 0 or self.total_fees < 0:
            raise ValueError("gross exposure and total fees must be non-negative")
        if self.stale_mark_count < 0:
            raise ValueError("stale_mark_count must be non-negative")
        _hash(self.config_sha256, "config_sha256")
        expected = deterministic_uuid("account-snapshot", {"run_id": self.run_id, "account_ref": self.account_ref, "snapshot_at_utc": self.snapshot_at_utc})
        if self.account_snapshot_id is not None and self.account_snapshot_id != expected:
            raise ValueError("account_snapshot_id does not match deterministic logical identity")
        object.__setattr__(self, "account_snapshot_id", expected)

    @property
    def economic_payload(self) -> dict[str, object]:
        return {"run_id": self.run_id, "account_ref": self.account_ref, "snapshot_at_utc": self.snapshot_at_utc, "cash": self.cash, "equity": self.equity, "gross_exposure": self.gross_exposure, "net_exposure": self.net_exposure, "realized_pnl": self.realized_pnl, "unrealized_pnl": self.unrealized_pnl, "total_fees": self.total_fees, "total_funding": self.total_funding, "stale_mark_count": self.stale_mark_count, "config_sha256": self.config_sha256}

    @property
    def record_sha256(self) -> str:
        return sha256_payload(self.economic_payload)

@dataclass(frozen=True)
class ExecutionEvent:
    run_id: UUID
    sequence: int
    event_timestamp_utc: datetime
    priority: int
    event_type: ExecutionEventType
    event_sha256: str
    config_sha256: str
    series_identity: SeriesIdentity | None = None
    parent_record_id: UUID | None = None
    metadata: Mapping[str, object] = field(default_factory=dict)
    execution_event_id: UUID | None = None

    def __post_init__(self) -> None:
        require_utc_datetime(self.event_timestamp_utc, field_name="ExecutionEvent event_timestamp_utc")
        if self.sequence < 0 or self.priority < 0:
            raise ValueError("event sequence and priority must be non-negative")
        object.__setattr__(self, "event_type", ExecutionEventType(self.event_type))
        _hash(self.event_sha256, "event_sha256")
        _hash(self.config_sha256, "config_sha256")
        object.__setattr__(self, "metadata", _public_map(self.metadata))
        expected = deterministic_uuid("execution-event", {"run_id": self.run_id, "sequence": self.sequence, "event_sha256": self.event_sha256})
        if self.execution_event_id is not None and self.execution_event_id != expected:
            raise ValueError("execution_event_id does not match deterministic identity")
        object.__setattr__(self, "execution_event_id", expected)

    @property
    def record_sha256(self) -> str:
        return sha256_payload({"run_id": self.run_id, "sequence": self.sequence, "event_timestamp_utc": self.event_timestamp_utc, "priority": self.priority, "event_type": self.event_type, "event_sha256": self.event_sha256, "series_identity_sha256": None if self.series_identity is None else self.series_identity.series_identity_sha256, "parent_record_id": self.parent_record_id, "config_sha256": self.config_sha256, "metadata": dict(self.metadata)})


__all__ = [name for name in tuple(globals()) if not name.startswith("_")]
