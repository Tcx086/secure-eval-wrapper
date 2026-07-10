"""Explicit provider and canonical instrument identity helpers."""

from __future__ import annotations

import re

from secure_eval_wrapper.data_collection.models import InstrumentKey, InstrumentType
from secure_eval_wrapper.data_collection.symbols import normalize_symbol, split_base_quote


_ASSET = re.compile(r"^[A-Z0-9]+$")


def canonical_instrument_symbol(
    base_asset: str,
    quote_asset: str,
    instrument_type: InstrumentType,
    *,
    settlement_asset: str | None = None,
) -> str:
    """Build a display symbol that cannot conflate spot and derivatives."""

    base = base_asset.strip().upper()
    quote = quote_asset.strip().upper()
    if not _ASSET.fullmatch(base) or not _ASSET.fullmatch(quote):
        raise ValueError("instrument assets must contain only ASCII letters and digits")
    resolved_type = InstrumentType(instrument_type)
    if resolved_type is InstrumentType.SPOT:
        if settlement_asset is not None:
            raise ValueError("spot instruments must not declare a settlement asset")
        return normalize_symbol(f"{base}-{quote}")
    if resolved_type not in (
        InstrumentType.PERPETUAL_SWAP,
        InstrumentType.DATED_FUTURE,
    ):
        raise ValueError("canonical derivative symbols support perpetual swaps or dated futures")
    if not isinstance(settlement_asset, str) or not settlement_asset.strip():
        raise ValueError("derivative instruments require a settlement asset")
    settlement = settlement_asset.strip().upper()
    if not _ASSET.fullmatch(settlement):
        raise ValueError("settlement asset must contain only ASCII letters and digits")
    return f"{base}-{quote}:{settlement}:{resolved_type.value.upper()}"


def spot_instrument_key(
    *,
    provider_name: str,
    exchange_name: str,
    provider_instrument_id: str,
    symbol: str,
) -> InstrumentKey:
    normalized = normalize_symbol(symbol)
    base, quote = split_base_quote(normalized)
    return InstrumentKey(
        provider_name=provider_name,
        exchange_name=exchange_name,
        provider_instrument_id=provider_instrument_id,
        base_asset=base,
        quote_asset=quote,
        instrument_type=InstrumentType.SPOT,
        canonical_symbol=normalized,
    )


def perpetual_instrument_key(
    *,
    provider_name: str,
    exchange_name: str,
    provider_instrument_id: str,
    base_asset: str,
    quote_asset: str,
    settlement_asset: str,
    contract_type: str | None = None,
    margin_type: str | None = None,
) -> InstrumentKey:
    return InstrumentKey(
        provider_name=provider_name,
        exchange_name=exchange_name,
        provider_instrument_id=provider_instrument_id,
        base_asset=base_asset,
        quote_asset=quote_asset,
        settlement_asset=settlement_asset,
        instrument_type=InstrumentType.PERPETUAL_SWAP,
        canonical_symbol=canonical_instrument_symbol(
            base_asset,
            quote_asset,
            InstrumentType.PERPETUAL_SWAP,
            settlement_asset=settlement_asset,
        ),
        contract_type=contract_type,
        margin_type=margin_type,
    )


__all__ = [
    "canonical_instrument_symbol",
    "perpetual_instrument_key",
    "spot_instrument_key",
]
