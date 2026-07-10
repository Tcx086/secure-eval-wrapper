"""Provider registry for public market-data adapters and planned capabilities.

The registry contains names and capability states only. It does not contain URLs, credentials,
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

# Backward-compatible Phase 2A export. The mapping now includes implemented capability states.
PLANNED_PROVIDER_SPECS = PROVIDER_SPECS


def get_provider_spec(name: str) -> ProviderSpec:
    """Return a provider specification by canonical, case-insensitive name."""

    return PROVIDER_SPECS[name.strip().lower()]


def list_provider_specs() -> tuple[ProviderSpec, ...]:
    """Return provider specifications in stable registry order."""

    return tuple(PROVIDER_SPECS.values())
