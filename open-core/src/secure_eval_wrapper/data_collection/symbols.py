"""Conservative normalization for simple base/quote crypto symbols."""

from __future__ import annotations

import re


_PAIR_SEPARATORS = ("-", "/", "_")
_ASSET_PATTERN = re.compile(r"^[A-Z0-9]+$")


def normalize_symbol(symbol: str) -> str:
    """Normalize an explicitly delimited simple pair to ``BASE-QUOTE``.

    Concatenated symbols such as ``BTCUSDT`` and multi-part derivative symbols are rejected because
    splitting them would require provider-specific guessing.
    """

    if not isinstance(symbol, str):
        raise TypeError("symbol must be a string")
    candidate = symbol.strip().upper()
    if not candidate:
        raise ValueError("symbol must not be empty")

    separators = [separator for separator in _PAIR_SEPARATORS if separator in candidate]
    if len(separators) != 1 or candidate.count(separators[0]) != 1:
        raise ValueError(
            "symbol must be a simple pair with exactly one '-', '/', or '_' separator"
        )

    base_asset, quote_asset = candidate.split(separators[0])
    if not _ASSET_PATTERN.fullmatch(base_asset) or not _ASSET_PATTERN.fullmatch(quote_asset):
        raise ValueError("base and quote assets must contain only ASCII letters and digits")
    return f"{base_asset}-{quote_asset}"


def split_base_quote(symbol: str) -> tuple[str, str]:
    """Return normalized base and quote assets for an explicitly delimited simple pair."""

    normalized = normalize_symbol(symbol)
    base_asset, quote_asset = normalized.split("-", maxsplit=1)
    return base_asset, quote_asset
