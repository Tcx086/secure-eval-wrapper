"""Planned provider registry for future public market-data adapters.

The registry contains names and capability planning states only. It does not contain URLs,
credentials, API clients, or fetch behavior.
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


PLANNED_PROVIDER_SPECS: Mapping[str, ProviderSpec] = MappingProxyType(
    {
        "binance": ProviderSpec(
            name="binance",
            display_name="Binance",
            exchange_name="Binance",
            capabilities=_capabilities(),
        ),
        "okx": ProviderSpec(
            name="okx",
            display_name="OKX",
            exchange_name="OKX",
            capabilities=_capabilities(),
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


def get_provider_spec(name: str) -> ProviderSpec:
    """Return a planned provider specification by canonical, case-insensitive name."""

    return PLANNED_PROVIDER_SPECS[name.strip().lower()]


def list_provider_specs() -> tuple[ProviderSpec, ...]:
    """Return planned provider specifications in stable registry order."""

    return tuple(PLANNED_PROVIDER_SPECS.values())
