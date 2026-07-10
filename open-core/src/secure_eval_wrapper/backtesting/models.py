"""Immutable backtest inputs, outputs, equity points, and metrics."""

from __future__ import annotations

from dataclasses import dataclass, field
import re
from datetime import datetime
from decimal import Decimal
from enum import Enum
from types import MappingProxyType
from typing import Mapping
from uuid import NAMESPACE_URL, UUID, uuid5

from secure_eval_wrapper.data_collection.hashing import sha256_payload
from secure_eval_wrapper.data_collection.models import FundingRate, NormalizedBar
from secure_eval_wrapper.data_collection.time_utils import require_utc_datetime
from secure_eval_wrapper.execution.models import (
    AccountSnapshot,
    BrokerConfiguration,
    CashLedgerEntry,
    ExecutionEvent,
    FeeConfiguration,
    Fill,
    FundingPayment,
    OrderIntent,
    OrderType,
    PositionSnapshot,
    PositionState,
    RiskDecision,
    RiskLimitConfiguration,
    SimulatedOrder,
    SlippageConfiguration,
    TimeInForce,
)
from secure_eval_wrapper.execution.sizing import SizingConfiguration
from secure_eval_wrapper.signals.models import StandardizedSignal


_SHA256 = re.compile(r"^[0-9a-f]{64}$")


def _hash(value: str, name: str) -> str:
    if not isinstance(value, str) or not _SHA256.fullmatch(value):
        raise ValueError(f"{name} must be a lowercase SHA-256 digest")
    return value


class BacktestRunStatus(str, Enum):
    COMPLETED = "completed"
    FAILED = "failed"


class MetricStatus(str, Enum):
    AVAILABLE = "available"
    UNAVAILABLE = "unavailable"


@dataclass(frozen=True)
class BacktestConfiguration:
    initial_cash: Decimal
    base_currency: str
    sizing: SizingConfiguration
    broker: BrokerConfiguration = field(default_factory=BrokerConfiguration)
    fees: FeeConfiguration = field(default_factory=FeeConfiguration)
    slippage: SlippageConfiguration = field(default_factory=SlippageConfiguration)
    risk_limits: RiskLimitConfiguration = field(default_factory=RiskLimitConfiguration)
    order_type: OrderType = OrderType.MARKET
    time_in_force: TimeInForce = TimeInForce.GTC
    limit_offset_bps: Decimal = Decimal(0)
    stop_offset_bps: Decimal = Decimal(0)
    stop_limit_offset_bps: Decimal = Decimal(0)
    record_zero_funding: bool = False

    def __post_init__(self) -> None:
        if not self.initial_cash.is_finite() or self.initial_cash < 0:
            raise ValueError("initial_cash must be finite and non-negative")
        if not isinstance(self.base_currency, str) or not self.base_currency.strip():
            raise ValueError("base_currency must be non-empty")
        object.__setattr__(self, "base_currency", self.base_currency.strip().upper())
        if self.fees.fee_currency != self.base_currency:
            raise ValueError("fee_currency must equal base_currency; FX conversion is not supported")
        object.__setattr__(self, "order_type", OrderType(self.order_type))
        object.__setattr__(self, "time_in_force", TimeInForce(self.time_in_force))
        for name in ("limit_offset_bps", "stop_offset_bps", "stop_limit_offset_bps"):
            value = getattr(self, name)
            if not value.is_finite() or value < 0:
                raise ValueError(f"{name} must be finite and non-negative")

    @property
    def config_sha256(self) -> str:
        return sha256_payload({"initial_cash": self.initial_cash, "base_currency": self.base_currency, "sizing": self.sizing.config_sha256, "broker": self.broker.config_sha256, "fees": self.fees.config_sha256, "slippage": self.slippage.config_sha256, "risk_limits": self.risk_limits.config_sha256, "order_type": self.order_type, "time_in_force": self.time_in_force, "limit_offset_bps": self.limit_offset_bps, "stop_offset_bps": self.stop_offset_bps, "stop_limit_offset_bps": self.stop_limit_offset_bps, "record_zero_funding": self.record_zero_funding})


@dataclass(frozen=True)
class BacktestRequest:
    run_id: UUID | None
    bars: tuple[NormalizedBar, ...]
    signals: tuple[StandardizedSignal, ...]
    funding_rates: tuple[FundingRate, ...]
    configuration: BacktestConfiguration
    implementation_code_sha256: str
    repository_commit_sha: str
    signal_run_id: UUID | None = None
    public_provenance: Mapping[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        _hash(self.implementation_code_sha256, "implementation_code_sha256")
        if not isinstance(self.repository_commit_sha, str) or not self.repository_commit_sha.strip():
            raise ValueError("repository_commit_sha must be non-empty")
        object.__setattr__(self, "repository_commit_sha", self.repository_commit_sha.strip())
        object.__setattr__(self, "public_provenance", MappingProxyType(dict(self.public_provenance)))
        expected = uuid5(NAMESPACE_URL, f"secure-eval-wrapper:backtest-run:v2:{sha256_payload(self.logical_identity_payload)}")
        if self.run_id is not None and self.run_id != expected:
            raise ValueError("run_id does not match deterministic backtest logical identity")
        object.__setattr__(self, "run_id", expected)

    @property
    def data_sha256(self) -> str:
        from secure_eval_wrapper.alpha.identity import stable_economic_record

        bars = sorted((stable_economic_record(item) for item in self.bars), key=lambda row: (str(row["series_identity"]), row["bar_open_time_utc"]))
        funding = sorted((stable_economic_record(item) for item in self.funding_rates), key=lambda row: (str(row["series_identity"]), row["funding_time_utc"]))
        signals = sorted(({"signal_id": item.signal_id, "record_sha256": item.record_sha256, "timestamp_utc": item.timestamp_utc, "series_identity_sha256": item.series_identity.series_identity_sha256} for item in self.signals), key=lambda row: (row["timestamp_utc"], row["series_identity_sha256"], str(row["signal_id"])))
        return sha256_payload({"bars": bars, "signals": signals, "funding": funding})

    @property
    def execution_lineage_id(self) -> UUID:
        payload = {
            "configuration_sha256": self.configuration.config_sha256,
            "implementation_code_sha256": self.implementation_code_sha256,
            "repository_commit_sha": self.repository_commit_sha,
            "signal_run_id": self.signal_run_id,
            "public_run_mode": "backtest",
            "lineage_version": "phase5-execution-lineage-v1",
        }
        return uuid5(NAMESPACE_URL, f"secure-eval-wrapper:execution-lineage:v1:{sha256_payload(payload)}")

    @property
    def logical_identity_payload(self) -> dict[str, object]:
        return {
            "economic_input_sha256": self.data_sha256,
            "configuration_sha256": self.configuration.config_sha256,
            "implementation_code_sha256": self.implementation_code_sha256,
            "repository_commit_sha": self.repository_commit_sha,
            "signal_run_id": self.signal_run_id,
            "public_run_mode": "backtest",
            "identity_version": "phase5-backtest-run-v2",
        }

@dataclass(frozen=True)
class BacktestRun:
    backtest_run_id: UUID
    run_id: UUID
    signal_run_id: UUID | None
    started_at_utc: datetime
    completed_at_utc: datetime
    status: BacktestRunStatus
    initial_cash: Decimal
    base_currency: str
    fee_currency: str
    account_ref: str
    config_sha256: str
    data_sha256: str
    implementation_code_sha256: str
    repository_commit_sha: str
    record_sha256: str
    metadata: Mapping[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        require_utc_datetime(self.started_at_utc, field_name="BacktestRun started_at_utc")
        require_utc_datetime(self.completed_at_utc, field_name="BacktestRun completed_at_utc")
        if self.completed_at_utc < self.started_at_utc:
            raise ValueError("BacktestRun completed_at_utc precedes started_at_utc")
        object.__setattr__(self, "status", BacktestRunStatus(self.status))

        if not self.initial_cash.is_finite() or self.initial_cash < 0:
            raise ValueError("BacktestRun initial_cash must be finite and non-negative")
        for name in ("base_currency", "fee_currency", "account_ref", "repository_commit_sha"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"BacktestRun {name} must be non-empty")
            object.__setattr__(self, name, value.strip().upper() if name in {"base_currency", "fee_currency"} else value.strip())
        if self.fee_currency != self.base_currency:
            raise ValueError("BacktestRun fee_currency must equal base_currency")
        for name in ("config_sha256", "data_sha256", "implementation_code_sha256", "record_sha256"):
            _hash(getattr(self, name), name)
        object.__setattr__(self, "metadata", MappingProxyType(dict(self.metadata)))
        expected = sha256_payload(self.economic_payload)
        if self.record_sha256 != expected:
            raise ValueError("BacktestRun record_sha256 does not match its economic payload")

    @property
    def economic_payload(self) -> dict[str, object]:
        return {
            "backtest_run_id": self.backtest_run_id,
            "run_id": self.run_id,
            "signal_run_id": self.signal_run_id,
            "started_at_utc": self.started_at_utc,
            "completed_at_utc": self.completed_at_utc,
            "initial_cash": self.initial_cash,
            "base_currency": self.base_currency,
            "fee_currency": self.fee_currency,
            "account_ref": self.account_ref,
            "config_sha256": self.config_sha256,
            "data_sha256": self.data_sha256,
            "implementation_code_sha256": self.implementation_code_sha256,
            "repository_commit_sha": self.repository_commit_sha,
            "status": self.status,
        }

@dataclass(frozen=True)
class EquityCurvePoint:
    run_id: UUID
    timestamp_utc: datetime
    cash: Decimal
    equity: Decimal
    drawdown_amount: Decimal
    drawdown_fraction: Decimal | None
    gross_exposure: Decimal
    net_exposure: Decimal
    stale_mark_count: int
    config_sha256: str
    equity_curve_id: UUID | None = None

    def __post_init__(self) -> None:
        require_utc_datetime(self.timestamp_utc, field_name="EquityCurvePoint timestamp_utc")
        _hash(self.config_sha256, "config_sha256")
        for name in ("cash", "equity", "drawdown_amount", "gross_exposure", "net_exposure"):
            value = getattr(self, name)
            if not value.is_finite():
                raise ValueError(f"{name} must be finite")
        if self.drawdown_amount < 0 or self.gross_exposure < 0:
            raise ValueError("drawdown and gross exposure must be non-negative")
        if self.drawdown_fraction is not None and (not self.drawdown_fraction.is_finite() or not Decimal(0) <= self.drawdown_fraction <= 1):
            raise ValueError("drawdown_fraction must be in [0, 1]")
        expected = uuid5(NAMESPACE_URL, f"equity-point:{sha256_payload(self.economic_payload)}")
        if self.equity_curve_id is not None and self.equity_curve_id != expected:
            raise ValueError("equity_curve_id does not match deterministic identity")
        object.__setattr__(self, "equity_curve_id", expected)

    @property
    def economic_payload(self) -> dict[str, object]:
        return {"run_id": self.run_id, "timestamp_utc": self.timestamp_utc, "cash": self.cash, "equity": self.equity, "drawdown_amount": self.drawdown_amount, "drawdown_fraction": self.drawdown_fraction, "gross_exposure": self.gross_exposure, "net_exposure": self.net_exposure, "stale_mark_count": self.stale_mark_count, "config_sha256": self.config_sha256}

    @property
    def record_sha256(self) -> str:
        return sha256_payload(self.economic_payload)


@dataclass(frozen=True)
class BacktestMetric:
    run_id: UUID
    name: str
    value: Decimal | None
    status: MetricStatus
    unit: str | None
    config_sha256: str
    details: Mapping[str, object] = field(default_factory=dict)
    backtest_metric_id: UUID | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "status", MetricStatus(self.status))
        _hash(self.config_sha256, "config_sha256")
        if self.value is not None and not self.value.is_finite():
            raise ValueError("metric value must be finite when present")
        if self.status is MetricStatus.UNAVAILABLE and self.value is not None:
            raise ValueError("unavailable metric must have a null value")
        object.__setattr__(self, "details", MappingProxyType(dict(self.details)))
        expected = uuid5(NAMESPACE_URL, f"backtest-metric:{sha256_payload({'run_id': self.run_id, 'name': self.name})}")
        if self.backtest_metric_id is not None and self.backtest_metric_id != expected:
            raise ValueError("backtest_metric_id does not match deterministic identity")
        object.__setattr__(self, "backtest_metric_id", expected)

    @property
    def record_sha256(self) -> str:
        return sha256_payload({"run_id": self.run_id, "name": self.name, "value": self.value, "status": self.status, "unit": self.unit, "config_sha256": self.config_sha256, "details": dict(self.details)})


@dataclass(frozen=True)
class BacktestMetrics:
    initial_cash: Decimal
    final_cash: Decimal
    final_equity: Decimal
    gross_pnl: Decimal
    net_pnl: Decimal
    realized_pnl: Decimal
    unrealized_pnl: Decimal
    total_fees: Decimal
    total_funding: Decimal
    total_return: Decimal | None
    maximum_drawdown_amount: Decimal
    maximum_drawdown_fraction: Decimal | None
    maximum_gross_exposure: Decimal
    maximum_net_exposure: Decimal
    turnover: Decimal
    submitted_intent_count: int
    blocked_intent_count: int
    order_count: int
    fill_count: int
    cancel_count: int
    reject_count: int
    expired_order_count: int
    funding_payment_count: int
    final_open_position_count: int
    completed_round_trip_count: int
    winning_round_trips: int
    losing_round_trips: int
    win_rate: Decimal | None
    gross_profit: Decimal
    gross_loss: Decimal
    profit_factor: Decimal | None
    non_positive_equity: bool

    def as_dict(self) -> dict[str, Decimal | int | bool | None]:
        return {name: getattr(self, name) for name in self.__dataclass_fields__}


@dataclass(frozen=True)
class BacktestResult:
    run: BacktestRun
    order_intents: tuple[OrderIntent, ...]
    risk_decisions: tuple[RiskDecision, ...]
    orders: tuple[SimulatedOrder, ...]
    fills: tuple[Fill, ...]
    positions: tuple[PositionState, ...]
    position_snapshots: tuple[PositionSnapshot, ...]
    cash_ledger_entries: tuple[CashLedgerEntry, ...]
    funding_payments: tuple[FundingPayment, ...]
    account_snapshots: tuple[AccountSnapshot, ...]
    events: tuple[ExecutionEvent, ...]
    equity_curve: tuple[EquityCurvePoint, ...]
    metrics: BacktestMetrics
    metric_records: tuple[BacktestMetric, ...]
