"""Provider-neutral guarded-live venue boundary."""
from __future__ import annotations

from abc import ABC, abstractmethod


class ProductionWriteSuppressed(PermissionError):
    pass


class GuardedLiveVenue(ABC):
    @abstractmethod
    def read_account_config(self): raise NotImplementedError
    @abstractmethod
    def read_balances(self): raise NotImplementedError
    @abstractmethod
    def read_positions(self): raise NotImplementedError
    @abstractmethod
    def query_order(self, *, instrument: str, client_order_id: str): raise NotImplementedError
    @abstractmethod
    def recent_orders(self, *, instrument: str): raise NotImplementedError
    @abstractmethod
    def open_orders(self, *, instrument: str): raise NotImplementedError
    @abstractmethod
    def fills(self, *, instrument: str): raise NotImplementedError

    def submit_order(self, request_body):
        raise ProductionWriteSuppressed("Phase 8A suppresses production order submission")

    def cancel_order(self, request_body):
        raise ProductionWriteSuppressed("Phase 8A suppresses production cancellation")


__all__ = ["GuardedLiveVenue", "ProductionWriteSuppressed"]
