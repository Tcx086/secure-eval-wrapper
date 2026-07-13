"""Typed PostgreSQL-local versus exact venue-bundle reconciliation."""
from __future__ import annotations

from datetime import datetime

from secure_eval_wrapper.data_collection.hashing import sha256_payload

from .authorities import LiveLocalProjection, LiveVenueObservation
from .models import LiveReconciliation, LiveReconciliationStatus


def _payload(projection) -> dict:
    payload = {
        "live_run_id": projection.live_run_id,
        "account_fingerprint": projection.account_fingerprint,
        "orders": tuple(dict(row) for row in projection.orders),
        "fills": tuple(dict(row) for row in projection.fills),
        "balances": dict(projection.balances),
        "positions": dict(projection.positions),
        "sequence": projection.sequence,
        "observed_at_utc": projection.observed_at_utc,
    }
    if isinstance(projection, LiveLocalProjection):
        payload["source_ids"] = projection.source_ids
    else:
        payload["response_hashes"] = projection.response_hashes
    return payload


def reconcile_live(*, local_projection: LiveLocalProjection, venue_observation: LiveVenueObservation, evaluated_at_utc: datetime) -> LiveReconciliation:
    if not isinstance(local_projection, LiveLocalProjection) or not isinstance(venue_observation, LiveVenueObservation):
        raise TypeError("live reconciliation requires typed PostgreSQL local and exact venue authorities")
    if local_projection.live_run_id != venue_observation.live_run_id:
        raise ValueError("reconciliation authorities belong to different runs")
    if not local_projection.source_ids or not venue_observation.response_hashes:
        raise ValueError("empty caller dictionaries are not reconciliation authority")
    for digest in venue_observation.response_hashes:
        if len(digest) != 64:
            raise ValueError("venue response hash is invalid")
    local_payload = _payload(local_projection)
    venue_payload = _payload(venue_observation)
    input_hash = sha256_payload({"local": local_payload, "venue": venue_payload})
    differences = []
    for field in ("orders", "fills", "balances", "positions", "account_fingerprint", "sequence", "observed_at_utc"):
        local_value = local_payload[field]
        venue_value = venue_payload[field]
        if local_value != venue_value:
            differences.append({"field": field, "local": local_value, "venue": venue_value, "material": True})
    status = LiveReconciliationStatus.RECONCILED if not differences else LiveReconciliationStatus.BLOCKED
    return LiveReconciliation(local_projection.live_run_id, evaluated_at_utc, status, input_hash, tuple(differences))


def build_and_reconcile(*, repository, live_run_id, venue_observation: LiveVenueObservation, evaluated_at_utc: datetime) -> tuple[LiveReconciliation, dict]:
    raw = repository.build_local_projection(live_run_id, observed_at_utc=evaluated_at_utc)
    local = LiveLocalProjection(
        raw["live_run_id"], raw["account_fingerprint"], tuple(raw["orders"]), tuple(raw["fills"]),
        raw["balances"], raw["positions"], raw["sequence"], raw["timestamp_utc"], tuple(raw["source_ids"]),
    )
    reconciliation = reconcile_live(local_projection=local, venue_observation=venue_observation, evaluated_at_utc=evaluated_at_utc)
    exact_input = {"local": _payload(local), "venue": _payload(venue_observation)}
    repository.persist_reconciliation(reconciliation, exact_input=exact_input)
    return reconciliation, exact_input


__all__ = ["reconcile_live", "build_and_reconcile"]
