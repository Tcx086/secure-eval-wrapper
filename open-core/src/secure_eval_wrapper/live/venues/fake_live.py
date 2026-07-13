"""Deterministic fake transport/venue for Phase 8A state-machine tests."""
from __future__ import annotations

from collections import defaultdict
from copy import deepcopy

from ..venue import GuardedLiveVenue, ProductionWriteSuppressed


class FakeLiveVenue(GuardedLiveVenue):
    is_fake = True

    def __init__(self, *, account_config=None, balances=None, positions=None, orders=None, fills=None) -> None:
        self.account_config = deepcopy(account_config or {"acctLv": "1", "enableSpotBorrow": False, "autoLoan": False})
        self.balance_rows = deepcopy(balances or [])
        self.position_rows = deepcopy(positions or [])
        self.orders_by_client = {str(row.get("clOrdId")): deepcopy(row) for row in (orders or [])}
        self.fill_rows = deepcopy(fills or [])
        self.calls = []
        self.write_attempt_count = 0
        self.faults = defaultdict(list)

    def schedule_fault(self, operation: str, fault: Exception) -> None:
        self.faults[operation].append(fault)

    def _read(self, operation: str, payload, result):
        self.calls.append((operation, deepcopy(payload)))
        if self.faults[operation]:
            raise self.faults[operation].pop(0)
        return deepcopy(result)

    def read_account_config(self): return self._read("account_config", {}, self.account_config)
    def read_balances(self): return self._read("balances", {}, self.balance_rows)
    def read_positions(self): return self._read("positions", {}, self.position_rows)
    def query_order(self, *, instrument: str, client_order_id: str): return self._read("query_order", {"instId": instrument, "clOrdId": client_order_id}, self.orders_by_client.get(client_order_id))
    def recent_orders(self, *, instrument: str): return self._read("recent_orders", {"instType": "SPOT", "instId": instrument}, tuple(self.orders_by_client.values()))
    def open_orders(self, *, instrument: str): return self._read("open_orders", {"instType": "SPOT", "instId": instrument}, tuple(row for row in self.orders_by_client.values() if row.get("state") in {"live", "partially_filled"}))
    def fills(self, *, instrument: str): return self._read("fills", {"instType": "SPOT", "instId": instrument}, tuple(row for row in self.fill_rows if not row.get("instId") or row.get("instId") == instrument))

    def submit_order(self, request_body):
        self.write_attempt_count += 1
        raise ProductionWriteSuppressed("fake live venue refuses production order submission")

    def cancel_order(self, request_body):
        self.write_attempt_count += 1
        raise ProductionWriteSuppressed("fake live venue refuses production cancellation")


__all__ = ["FakeLiveVenue"]
