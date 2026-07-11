"""Explicit bounded Phase 7 paper configuration."""
from __future__ import annotations
from dataclasses import dataclass
from decimal import Decimal
from types import MappingProxyType
from typing import Mapping
from secure_eval_wrapper.data_collection.hashing import sha256_payload
from secure_eval_wrapper.execution.models import OrderType
from .enums import PaperEnvironment, PaperProvider

def _positive_decimal(value: Decimal, name: str) -> None:
    if not isinstance(value, Decimal) or not value.is_finite() or value <= 0:
        raise ValueError(f"{name} must be a finite positive Decimal")

def _positive_int(value: int, name: str) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"{name} must be a positive integer")

@dataclass(frozen=True)
class PaperRunConfiguration:
    provider: PaperProvider
    environment: PaperEnvironment
    account_reference: str
    allowed_instruments: tuple[str, ...]
    allowed_instrument_types: tuple[str, ...]
    allowed_settlement_assets: tuple[str, ...]
    base_currency: str
    maximum_order_notional: Decimal
    maximum_position_notional_per_instrument: Decimal
    maximum_gross_exposure: Decimal
    maximum_net_exposure: Decimal
    maximum_open_order_count: int
    maximum_orders_per_minute: int
    maximum_cancellations_per_minute: int
    maximum_daily_submitted_notional: Decimal
    maximum_daily_realized_loss: Decimal
    maximum_current_drawdown: Decimal
    stale_market_data_threshold_seconds: int
    stale_account_snapshot_threshold_seconds: int
    maximum_reconciliation_age_seconds: int
    maximum_unknown_order_duration_seconds: int
    maximum_unacknowledged_order_duration_seconds: int
    maximum_consecutive_transport_failures: int
    maximum_clock_skew_seconds: int
    allowed_order_types: tuple[OrderType, ...]
    allow_short: bool
    allow_perpetual: bool
    persistence_required: bool
    approval_required: bool
    cancel_open_orders_on_kill: bool
    automatic_flatten_on_kill: bool
    maximum_run_duration_seconds: int
    external_sandbox_enabled: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(self,"provider",PaperProvider(self.provider)); object.__setattr__(self,"environment",PaperEnvironment(self.environment))
        if self.environment is PaperEnvironment.LIVE: raise ValueError("live is forbidden and unimplemented in Phase 7")
        expected=PaperEnvironment.PAPER_INTERNAL if self.provider is PaperProvider.INTERNAL else PaperEnvironment.PAPER_EXCHANGE_SANDBOX
        if self.environment is not expected: raise ValueError("invalid paper provider/environment pairing")
        if not isinstance(self.account_reference,str) or not self.account_reference.strip(): raise ValueError("account_reference must be non-empty")
        object.__setattr__(self,"account_reference",self.account_reference.strip())
        for name in ("allowed_instruments","allowed_instrument_types","allowed_settlement_assets"):
            values=tuple(sorted({str(v).strip() for v in getattr(self,name) if str(v).strip()}))
            if not values: raise ValueError(f"{name} must be non-empty")
            object.__setattr__(self,name,values)
        if not isinstance(self.base_currency,str) or not self.base_currency.strip(): raise ValueError("base_currency must be non-empty")
        object.__setattr__(self,"base_currency",self.base_currency.strip().upper())
        for name in ("maximum_order_notional","maximum_position_notional_per_instrument","maximum_gross_exposure","maximum_net_exposure","maximum_daily_submitted_notional","maximum_daily_realized_loss","maximum_current_drawdown"):
            _positive_decimal(getattr(self,name),name)
        for name in ("maximum_open_order_count","maximum_orders_per_minute","maximum_cancellations_per_minute","stale_market_data_threshold_seconds","stale_account_snapshot_threshold_seconds","maximum_reconciliation_age_seconds","maximum_unknown_order_duration_seconds","maximum_unacknowledged_order_duration_seconds","maximum_consecutive_transport_failures","maximum_clock_skew_seconds","maximum_run_duration_seconds"):
            _positive_int(getattr(self,name),name)
        values=tuple(sorted({OrderType(v) for v in self.allowed_order_types},key=lambda v:v.value))
        if not values: raise ValueError("allowed_order_types must be non-empty")
        object.__setattr__(self,"allowed_order_types",values)
        if self.provider is PaperProvider.OKX_DEMO and not self.external_sandbox_enabled: raise ValueError("OKX demo requires explicit external sandbox enablement")
        if self.automatic_flatten_on_kill: raise ValueError("automatic flattening is not implemented in Phase 7")

    @property
    def config_sha256(self)->str:
        return sha256_payload({name:getattr(self,name) for name in self.__dataclass_fields__})
    @property
    def risk_limits(self)->Mapping[str,object]:
        names=[n for n in self.__dataclass_fields__ if n.startswith("maximum_") or n.startswith("stale_") or n in {"allow_short","allow_perpetual"}]
        return MappingProxyType({n:getattr(self,n) for n in names})

def internal_demo_configuration(*, persistence_required: bool=False)->PaperRunConfiguration:
    D=Decimal
    return PaperRunConfiguration(PaperProvider.INTERNAL,PaperEnvironment.PAPER_INTERNAL,"public-internal-paper",("BTC-USDT",),("spot",),("USDT",),"USDT",D("1000"),D("2500"),D("5000"),D("5000"),10,30,30,D("10000"),D("1000"),D("1000"),60,60,60,30,15,3,5,(OrderType.MARKET,OrderType.LIMIT,OrderType.STOP,OrderType.STOP_LIMIT),False,False,persistence_required,True,True,False,3600,False)
