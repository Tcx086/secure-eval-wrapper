"""Immutable fail-closed Phase 8 guarded-live configuration."""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from types import MappingProxyType
from typing import Mapping

from secure_eval_wrapper.data_collection.hashing import sha256_payload

from .identity import validate_okx_account_fingerprint


def _text(value: str, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be non-empty")
    return value.strip()


def _positive_decimal(value: Decimal, name: str) -> Decimal:
    if not isinstance(value, Decimal) or not value.is_finite() or value <= 0:
        raise ValueError(f"{name} must be a finite positive Decimal")
    return value


def _positive_int(value: int, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"{name} must be a positive integer")
    return value


@dataclass(frozen=True)
class GuardedLiveConfiguration:
    provider: str
    environment: str
    account_fingerprint: str
    subaccount_fingerprint: str | None
    allowed_instruments: tuple[str, ...]
    allowed_instrument_types: tuple[str, ...]
    allowed_settlement_assets: tuple[str, ...]
    base_currency: str
    allowed_order_types: tuple[str, ...]
    maximum_order_notional: Decimal
    maximum_position_notional: Decimal
    maximum_gross_exposure: Decimal
    maximum_net_exposure: Decimal
    maximum_open_order_count: int
    maximum_daily_submitted_notional: Decimal
    maximum_daily_realized_loss: Decimal
    maximum_drawdown: Decimal
    maximum_orders_per_minute: int
    maximum_cancellations_per_minute: int
    market_data_freshness_seconds: int
    account_snapshot_freshness_seconds: int
    reconciliation_freshness_seconds: int
    maximum_unknown_order_duration_seconds: int
    maximum_unacknowledged_order_duration_seconds: int
    maximum_run_duration_seconds: int
    maximum_clock_skew_seconds: int
    maximum_transport_failures: int
    maximum_fee_bps: Decimal
    maximum_adverse_slippage_bps: Decimal
    maximum_reference_price_deviation_bps: Decimal
    cancel_open_orders_on_kill: bool
    credential_source_policy: tuple[str, ...]
    endpoint_catalog_hash: str
    provider_implementation_hash: str
    dry_run: bool = True
    read_only_preflight: bool = True
    production_write_enabled: bool = False
    automatic_flatten: bool = False
    allow_short: bool = False
    allow_perpetual: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(self, "provider", _text(self.provider, "provider").lower())
        object.__setattr__(self, "environment", _text(self.environment, "environment").lower())
        if (self.provider, self.environment) != ("okx", "production"):
            raise ValueError("Phase 8A supports only OKX production")
        fingerprint = validate_okx_account_fingerprint(self.account_fingerprint)
        object.__setattr__(self, "account_fingerprint", fingerprint)
        if self.subaccount_fingerprint is not None:
            sub = validate_okx_account_fingerprint(self.subaccount_fingerprint, field_name="subaccount_fingerprint")
            object.__setattr__(self, "subaccount_fingerprint", sub)
        for name, upper in (
            ("allowed_instruments", True),
            ("allowed_instrument_types", False),
            ("allowed_settlement_assets", True),
            ("allowed_order_types", False),
            ("credential_source_policy", False),
        ):
            values = tuple(sorted({_text(str(v), name).upper() if upper else _text(str(v), name).lower() for v in getattr(self, name)}))
            if not values:
                raise ValueError(f"{name} must be non-empty")
            object.__setattr__(self, name, values)
        object.__setattr__(self, "base_currency", _text(self.base_currency, "base_currency").upper())
        if self.allowed_instrument_types != ("spot",):
            raise ValueError("only SPOT instruments are permitted")
        if self.allowed_order_types != ("limit",):
            raise ValueError("only limit orders are permitted")
        if any(value not in {"environment", "os_credential_store", "injected_local"} for value in self.credential_source_policy):
            raise ValueError("unsupported credential source policy")
        decimal_fields = (
            "maximum_order_notional", "maximum_position_notional", "maximum_gross_exposure",
            "maximum_net_exposure", "maximum_daily_submitted_notional", "maximum_daily_realized_loss",
            "maximum_drawdown", "maximum_fee_bps", "maximum_adverse_slippage_bps",
            "maximum_reference_price_deviation_bps",
        )
        for name in decimal_fields:
            _positive_decimal(getattr(self, name), name)
        integer_fields = (
            "maximum_open_order_count", "maximum_orders_per_minute", "maximum_cancellations_per_minute",
            "market_data_freshness_seconds", "account_snapshot_freshness_seconds",
            "reconciliation_freshness_seconds", "maximum_unknown_order_duration_seconds",
            "maximum_unacknowledged_order_duration_seconds", "maximum_run_duration_seconds",
            "maximum_clock_skew_seconds", "maximum_transport_failures",
        )
        for name in integer_fields:
            _positive_int(getattr(self, name), name)
        for name in ("endpoint_catalog_hash", "provider_implementation_hash"):
            value = _text(getattr(self, name), name)
            if len(value) != 64 or any(ch not in "0123456789abcdef" for ch in value):
                raise ValueError(f"{name} must be lowercase SHA-256")
        if not self.dry_run or not self.read_only_preflight or self.production_write_enabled:
            raise ValueError("Phase 8A is dry-run/read-only and production writes are disabled")
        if self.automatic_flatten or self.allow_short or self.allow_perpetual:
            raise ValueError("flattening, shorting, and perpetuals are forbidden")

    @property
    def configuration_hash(self) -> str:
        return sha256_payload({name: getattr(self, name) for name in self.__dataclass_fields__})

    @property
    def risk_limits(self) -> Mapping[str, object]:
        names = tuple(name for name in self.__dataclass_fields__ if name.startswith("maximum_") or name.endswith("freshness_seconds"))
        return MappingProxyType({name: getattr(self, name) for name in names})


def phase8a_dry_run_configuration(
    *,
    account_fingerprint: str,
    endpoint_catalog_hash: str,
    provider_implementation_hash: str,
) -> GuardedLiveConfiguration:
    d = Decimal
    return GuardedLiveConfiguration(
        provider="okx", environment="production", account_fingerprint=account_fingerprint,
        subaccount_fingerprint=None, allowed_instruments=("BTC-USDT",),
        allowed_instrument_types=("spot",), allowed_settlement_assets=("USDT",),
        base_currency="USDT", allowed_order_types=("limit",), maximum_order_notional=d("1000"),
        maximum_position_notional=d("2500"), maximum_gross_exposure=d("5000"),
        maximum_net_exposure=d("5000"), maximum_open_order_count=5,
        maximum_daily_submitted_notional=d("10000"), maximum_daily_realized_loss=d("500"),
        maximum_drawdown=d("500"), maximum_orders_per_minute=10, maximum_cancellations_per_minute=10,
        market_data_freshness_seconds=30, account_snapshot_freshness_seconds=30,
        reconciliation_freshness_seconds=30, maximum_unknown_order_duration_seconds=30,
        maximum_unacknowledged_order_duration_seconds=15, maximum_run_duration_seconds=900,
        maximum_clock_skew_seconds=5, maximum_transport_failures=3, maximum_fee_bps=d("20"), maximum_adverse_slippage_bps=d("100"),
        maximum_reference_price_deviation_bps=d("500"), cancel_open_orders_on_kill=False,
        credential_source_policy=("environment",), endpoint_catalog_hash=endpoint_catalog_hash,
        provider_implementation_hash=provider_implementation_hash,
    )


__all__ = ["GuardedLiveConfiguration", "phase8a_dry_run_configuration"]
