"""PostgreSQL-local versus collector-issued OKX reconciliation authority."""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Mapping

from secure_eval_wrapper.data_collection.hashing import sha256_payload
from secure_eval_wrapper.data_collection.time_utils import require_utc_datetime

from .authorities import LiveLocalProjection
from .collector_evidence import ObservationClassification, VerifiedOkxReadObservationBundle
from .models import LiveReconciliation, LiveReconciliationStatus


def _plain(value):
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, Mapping):
        return {str(key): _plain(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return tuple(_plain(item) for item in value)
    return value


def _local_payload(projection: LiveLocalProjection) -> dict:
    return {
        "live_run_id": projection.live_run_id,
        "account_fingerprint": projection.account_fingerprint,
        "orders": tuple(_plain(row) for row in projection.orders),
        "fills": tuple(_plain(row) for row in projection.fills),
        "balances": _plain(projection.balances),
        "positions": _plain(projection.positions),
        "sequence": projection.sequence,
        "observed_at_utc": projection.observed_at_utc,
        "source_ids": projection.source_ids,
    }


def _venue_payload(bundle: VerifiedOkxReadObservationBundle) -> dict:
    balance = dict(bundle.envelope("balances").normalized_payload)
    pending = tuple(bundle.envelope("pending_orders").normalized_payload)
    history = tuple(bundle.envelope("order_history").normalized_payload)
    fills = tuple(bundle.envelope("fills").normalized_payload)
    positions = tuple(bundle.envelope("positions").normalized_payload)
    orders_by_id = {str(row["ordId"]): _plain(row) for row in history + pending}
    balances = {
        str(row["ccy"]): {
            "total": str(row["equity"]),
            "available": str(row["available"]),
            "reserved": str(row["reserved"]),
        }
        for row in balance["details"]
    }
    position_map = {
        str(row["instId"]): {
            "quantity": str(row["quantity"]),
            "average_price": str(row["average_price"]),
            "unrealized_pnl": str(row["unrealized_pnl"]),
        }
        for row in positions
        if Decimal(str(row["quantity"])) != 0
    }
    return {
        "live_run_id": bundle.live_run_id,
        "account_fingerprint": bundle.account_fingerprint,
        "orders": tuple(orders_by_id[key] for key in sorted(orders_by_id)),
        "fills": tuple(_plain(row) for row in fills),
        "balances": balances,
        "positions": position_map,
        "sequence": bundle.venue_sequence,
        "observed_at_utc": bundle.venue_observed_at_utc,
        "response_bundle_id": bundle.bundle_id,
        "response_hashes": tuple(
            envelope.canonical_response_hash
            for envelope in bundle.envelopes
            if envelope.canonical_response_hash is not None
        ),
    }


def reconcile_live(
    *,
    local_projection: LiveLocalProjection,
    okx_bundle: VerifiedOkxReadObservationBundle | None = None,
    evaluated_at_utc: datetime,
    freshness_seconds: int,
    maximum_clock_skew_seconds: int,
    venue_observation=None,
) -> tuple[LiveReconciliation, dict]:
    """Reconcile only exact responses issued by the approved adapter."""
    if venue_observation is not None or not isinstance(okx_bundle, VerifiedOkxReadObservationBundle):
        raise TypeError("operational reconciliation requires a collector-issued OKX response bundle")
    if not isinstance(local_projection, LiveLocalProjection):
        raise TypeError("operational reconciliation requires a PostgreSQL local projection")
    if okx_bundle.purpose != "reconciliation" or okx_bundle.classification is not ObservationClassification.OPERATIONAL:
        raise PermissionError("fixture/imported observations cannot create operational reconciliation")
    if local_projection.live_run_id != okx_bundle.live_run_id:
        raise ValueError("reconciliation authorities belong to different runs")
    now = require_utc_datetime(evaluated_at_utc, field_name="evaluated_at_utc")
    if freshness_seconds <= 0 or maximum_clock_skew_seconds <= 0:
        raise ValueError("reconciliation freshness and clock-skew thresholds must be positive")

    starts = tuple(envelope.query_started_at_utc for envelope in okx_bundle.envelopes)
    completions = tuple(envelope.query_completed_at_utc for envelope in okx_bundle.envelopes)
    query_started = min(starts)
    query_completed = max(completions)
    if local_projection.observed_at_utc > now or okx_bundle.venue_observed_at_utc > now or query_completed > now:
        raise ValueError("future reconciliation evidence is forbidden")
    venue_age = (now - okx_bundle.venue_observed_at_utc).total_seconds()
    if venue_age > freshness_seconds:
        raise ValueError("venue reconciliation evidence is stale")
    skew = abs((query_completed - okx_bundle.venue_observed_at_utc).total_seconds())
    if skew > maximum_clock_skew_seconds:
        raise ValueError("venue observation clock skew exceeds the configured bound")

    local = _local_payload(local_projection)
    if not okx_bundle.complete:
        venue = {
            "response_bundle_id": okx_bundle.bundle_id,
            "endpoint_matrix_sha256": okx_bundle.endpoint_matrix_hash,
            "complete": False,
        }
        differences = ({"field": "endpoint_completion", "local": "required", "venue": "incomplete", "material": True},)
        status = LiveReconciliationStatus.UNKNOWN
    else:
        venue = _venue_payload(okx_bundle)
        differences_list = []
        for field in ("orders", "fills", "balances", "positions", "account_fingerprint"):
            if local[field] != venue[field]:
                differences_list.append({
                    "field": field, "local": local[field], "venue": venue[field], "material": True,
                })
        differences = tuple(differences_list)
        status = LiveReconciliationStatus.RECONCILED if not differences else LiveReconciliationStatus.BLOCKED

    exact_input = {
        "local": local,
        "venue": venue,
        "response_bundle_record_sha256": okx_bundle.record_hash,
        "endpoint_matrix_sha256": okx_bundle.endpoint_matrix_hash,
    }
    input_hash = sha256_payload(exact_input)
    reconciliation = LiveReconciliation(
        local_projection.live_run_id,
        now,
        status,
        input_hash,
        differences,
        local_projection_as_of_utc=local_projection.observed_at_utc,
        venue_observation_as_of_utc=okx_bundle.venue_observed_at_utc,
        query_started_at_utc=query_started,
        query_completed_at_utc=query_completed,
        response_bundle_id=okx_bundle.bundle_id,
        local_sequence=local_projection.sequence,
        venue_sequence=okx_bundle.venue_sequence,
        producer_classification=ObservationClassification.OPERATIONAL.value,
    )
    return reconciliation, exact_input


def build_and_reconcile(
    *,
    repository,
    live_run_id,
    okx_bundle: VerifiedOkxReadObservationBundle,
    configuration,
    evaluated_at_utc: datetime,
) -> tuple[LiveReconciliation, dict]:
    raw = repository.build_local_projection(live_run_id, observed_at_utc=evaluated_at_utc)
    local = LiveLocalProjection(
        raw["live_run_id"], raw["account_fingerprint"], tuple(raw["orders"]), tuple(raw["fills"]),
        raw["balances"], raw["positions"], raw["sequence"], raw["timestamp_utc"], tuple(raw["source_ids"]),
    )
    reconciliation, exact_input = reconcile_live(
        local_projection=local,
        okx_bundle=okx_bundle,
        evaluated_at_utc=evaluated_at_utc,
        freshness_seconds=configuration.reconciliation_freshness_seconds,
        maximum_clock_skew_seconds=configuration.maximum_clock_skew_seconds,
    )
    repository.persist_reconciliation(
        reconciliation,
        exact_input=exact_input,
        okx_bundle=okx_bundle,
    )
    return reconciliation, exact_input
