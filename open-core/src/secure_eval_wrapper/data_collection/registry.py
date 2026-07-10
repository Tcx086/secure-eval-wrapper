"""Concrete provider-component registry and exchange-level capability summaries.

Registry values contain names and capability states only. They do not contain URLs, credentials,
API clients, or fetch behavior.
"""

from __future__ import annotations

from collections.abc import Mapping
from types import MappingProxyType

from secure_eval_wrapper.data_collection.models import (
    MarketDataType,
    ProviderCapabilityStatus,
    ProviderSpec,
)


def _capabilities(
    *,
    ohlcv: ProviderCapabilityStatus = ProviderCapabilityStatus.PLANNED,
    trades: ProviderCapabilityStatus = ProviderCapabilityStatus.PLANNED,
    funding_rates: ProviderCapabilityStatus = ProviderCapabilityStatus.PLANNED,
    instruments: ProviderCapabilityStatus = ProviderCapabilityStatus.PLANNED,
) -> Mapping[MarketDataType, ProviderCapabilityStatus]:
    return MappingProxyType(
        {
            MarketDataType.OHLCV: ohlcv,
            MarketDataType.TRADES: trades,
            MarketDataType.FUNDING_RATES: funding_rates,
            MarketDataType.INSTRUMENTS: instruments,
        }
    )


PROVIDER_SPECS: Mapping[str, ProviderSpec] = MappingProxyType(
    {
        "binance": ProviderSpec(
            name="binance",
            display_name="Binance Spot",
            exchange_name="Binance",
            capabilities=_capabilities(
                ohlcv=ProviderCapabilityStatus.IMPLEMENTED,
                trades=ProviderCapabilityStatus.IMPLEMENTED,
                instruments=ProviderCapabilityStatus.IMPLEMENTED,
            ),
        ),
        "binance_usdm": ProviderSpec(
            name="binance_usdm",
            display_name="Binance USDⓈ-M",
            exchange_name="Binance",
            capabilities=_capabilities(
                funding_rates=ProviderCapabilityStatus.IMPLEMENTED,
                instruments=ProviderCapabilityStatus.IMPLEMENTED,
            ),
        ),
        "okx": ProviderSpec(
            name="okx",
            display_name="OKX V5 Public",
            exchange_name="OKX",
            capabilities=_capabilities(
                ohlcv=ProviderCapabilityStatus.IMPLEMENTED,
                trades=ProviderCapabilityStatus.IMPLEMENTED,
                funding_rates=ProviderCapabilityStatus.IMPLEMENTED,
                instruments=ProviderCapabilityStatus.IMPLEMENTED,
            ),
        ),
        "bybit": ProviderSpec(
            name="bybit",
            display_name="Bybit",
            exchange_name="Bybit",
            capabilities=_capabilities(),
        ),
        "coinbase": ProviderSpec(
            name="coinbase",
            display_name="Coinbase",
            exchange_name="Coinbase",
            capabilities=_capabilities(
                funding_rates=ProviderCapabilityStatus.UNKNOWN,
            ),
        ),
    }
)

CONCRETE_PROVIDER_SPECS: Mapping[str, ProviderSpec] = MappingProxyType(
    {name: PROVIDER_SPECS[name] for name in ("binance", "binance_usdm", "okx")}
)

EXCHANGE_CAPABILITY_SUMMARIES: Mapping[str, ProviderSpec] = MappingProxyType(
    {
        "binance": ProviderSpec(
            name="binance",
            display_name="Binance",
            exchange_name="Binance",
            capabilities=_capabilities(
                ohlcv=ProviderCapabilityStatus.IMPLEMENTED,
                trades=ProviderCapabilityStatus.IMPLEMENTED,
                funding_rates=ProviderCapabilityStatus.IMPLEMENTED,
                instruments=ProviderCapabilityStatus.IMPLEMENTED,
            ),
        ),
        "okx": ProviderSpec(
            name="okx",
            display_name="OKX",
            exchange_name="OKX",
            capabilities=PROVIDER_SPECS["okx"].capabilities,
        ),
        "bybit": PROVIDER_SPECS["bybit"],
        "coinbase": PROVIDER_SPECS["coinbase"],
    }
)

# Backward-compatible Phase 2A exchange-level summary export.
PLANNED_PROVIDER_SPECS = EXCHANGE_CAPABILITY_SUMMARIES


def get_provider_spec(name: str) -> ProviderSpec:
    """Return a concrete or planned provider-component specification."""

    return PROVIDER_SPECS[name.strip().lower()]


def get_exchange_capability_summary(name: str) -> ProviderSpec:
    """Return aggregate exchange capabilities without implying one component owns them all."""

    return EXCHANGE_CAPABILITY_SUMMARIES[name.strip().lower()]


def list_provider_specs() -> tuple[ProviderSpec, ...]:
    """Return provider-component specifications in stable registry order."""

    return tuple(PROVIDER_SPECS.values())
