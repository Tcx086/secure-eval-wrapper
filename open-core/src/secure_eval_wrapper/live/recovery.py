"""Query-first recovery for ambiguous live results."""
from __future__ import annotations

from .models import LiveObservationBundle


def query_first_recovery(*, live_run_id, venue, instrument: str, client_order_id: str, queried_at_utc):
    queried = venue.query_order(instrument=instrument, client_order_id=client_order_id)
    recent = tuple(venue.recent_orders(instrument=instrument))
    open_orders = tuple(venue.open_orders(instrument=instrument))
    fills = tuple(venue.fills(instrument=instrument))
    account = {"config": venue.read_account_config(), "balances": venue.read_balances(), "positions": venue.read_positions()}
    complete = queried is not None or any(str(row.get("clOrdId")) == client_order_id for row in recent)
    return LiveObservationBundle(live_run_id, client_order_id, queried, recent, open_orders, fills, account, queried_at_utc, complete)


__all__ = ["query_first_recovery"]
