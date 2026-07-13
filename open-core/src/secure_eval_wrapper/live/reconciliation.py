"""Exact-bundle live reconciliation; API success alone is never sufficient."""
from __future__ import annotations

from datetime import datetime

from secure_eval_wrapper.data_collection.hashing import sha256_payload

from .models import LiveReconciliation, LiveReconciliationStatus


def reconcile_live(*, live_run_id, local_projection: dict, venue_observation: dict, evaluated_at_utc: datetime) -> LiveReconciliation:
    input_hash = sha256_payload({"local": local_projection, "venue": venue_observation})
    differences = []
    fields = ("orders", "fills", "balances", "positions", "average_prices", "realized_pnl", "fees", "sequence")
    for field in fields:
        local_value = local_projection.get(field)
        venue_value = venue_observation.get(field)
        if local_value != venue_value:
            differences.append({"field": field, "local": local_value, "venue": venue_value, "material": True})
    local_time = local_projection.get("timestamp_utc")
    venue_time = venue_observation.get("timestamp_utc")
    if local_time != venue_time:
        differences.append({"field": "timestamp_utc", "local": local_time, "venue": venue_time, "material": True})
    status = LiveReconciliationStatus.RECONCILED if not differences else LiveReconciliationStatus.BLOCKED
    return LiveReconciliation(live_run_id, evaluated_at_utc, status, input_hash, tuple(differences))


__all__ = ["reconcile_live"]
