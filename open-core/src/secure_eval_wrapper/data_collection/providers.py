"""Abstract market-data provider boundary.

No exchange adapter or network client is implemented in Phase 2A.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Sequence

from secure_eval_wrapper.data_collection.models import (
    DataRequest,
    ProviderSpec,
    RawObservation,
)


class MarketDataProvider(ABC):
    """Contract future public market-data provider adapters must implement."""

    @property
    @abstractmethod
    def spec(self) -> ProviderSpec:
        """Return inert metadata describing this provider adapter."""

    @abstractmethod
    def fetch_ohlcv(self, request: DataRequest) -> Sequence[RawObservation]:
        """Fetch raw OHLCV observations for a provider-neutral request."""

    @abstractmethod
    def fetch_trades(self, request: DataRequest) -> Sequence[RawObservation]:
        """Fetch raw public trade observations for a provider-neutral request."""

    @abstractmethod
    def fetch_funding_rates(self, request: DataRequest) -> Sequence[RawObservation]:
        """Fetch raw public funding-rate observations for a provider-neutral request."""

    @abstractmethod
    def fetch_instruments(self, request: DataRequest) -> Sequence[RawObservation]:
        """Fetch raw public instrument metadata for a provider-neutral request."""
