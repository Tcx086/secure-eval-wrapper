"""Typed query-first recovery with incident semantics for any external side effect."""
from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from decimal import Decimal, InvalidOperation

from secure_eval_wrapper.data_collection.hashing import sha256_payload

from .models import LiveObservationBundle, LiveRecoveryOutcome


def _rows(value) -> tuple[Mapping[str, object], ...]:
    if value is None:
        return ()
    if not isinstance(value, (tuple, list)) or not all(isinstance(row, Mapping) for row in value):
        raise ValueError("provider order/fill collection must be a list of objects")
    return tuple(value)


def _decimal(value: object, field: str, *, positive: bool = False) -> Decimal:
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValueError(f"observed provider {field} is invalid") from exc
    if not parsed.is_finite() or (positive and parsed <= 0):
        raise ValueError(f"observed provider {field} is invalid")
    return parsed


def _intent_value(intent, name: str):
    if isinstance(intent, Mapping):
        return intent[name]
    value = getattr(intent, name)
    return value.value if hasattr(value, "value") else value


def _validate_order(row: Mapping[str, object], *, instrument: str, client_order_id: str, expected_intent=None) -> None:
    required = ("clOrdId", "ordId", "instId", "state")
    if any(not str(row.get(name, "")) for name in required):
        raise ValueError("observed provider order lacks exact identity or state")
    if str(row["clOrdId"]) != client_order_id:
        raise ValueError("query observation client order identity mismatch")
    if str(row["instId"]) != instrument:
        raise ValueError("query observation instrument mismatch")
    if expected_intent is not None:
        if str(row.get("side", "")) != str(_intent_value(expected_intent, "side")):
            raise ValueError("observed provider order side mismatch")
        if _decimal(row.get("sz"), "order quantity", positive=True) != Decimal(str(_intent_value(expected_intent, "quantity"))):
            raise ValueError("observed provider order quantity mismatch")
        if _decimal(row.get("px"), "order price", positive=True) != Decimal(str(_intent_value(expected_intent, "limit_price"))):
            raise ValueError("observed provider order price mismatch")


def _validate_fill(
    row: Mapping[str, object],
    *,
    instrument: str,
    client_order_id: str,
    order_ids: set[str],
    expected_intent=None,
) -> None:
    required = ("clOrdId", "ordId", "instId", "side", "fillSz", "fillPx")
    if any(not str(row.get(name, "")) for name in required):
        raise ValueError("observed fill lacks exact order identity or economics")
    if str(row["clOrdId"]) != client_order_id or str(row["instId"]) != instrument:
        raise ValueError("fill identity mismatch")
    if not order_ids or str(row["ordId"]) not in order_ids:
        raise ValueError("fill does not belong to an observed provider order")
    if not str(row.get("tradeId") or row.get("fillId") or ""):
        raise ValueError("fill lacks provider fill identity")
    quantity = _decimal(row["fillSz"], "fill quantity", positive=True)
    _decimal(row["fillPx"], "fill price", positive=True)
    _decimal(row.get("fee", "0"), "fill fee")
    if not str(row.get("feeCcy", "")):
        raise ValueError("fill lacks fee currency")
    if expected_intent is not None:
        if str(row["side"]) != str(_intent_value(expected_intent, "side")):
            raise ValueError("fill side mismatch")
        if quantity > Decimal(str(_intent_value(expected_intent, "quantity"))):
            raise ValueError("fill quantity exceeds order quantity")


def _parse_timestamp(value: object) -> datetime:
    if isinstance(value, datetime):
        return value
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("recovery query timestamp is invalid") from exc


def normalize_recovery_observation(
    bundle: LiveObservationBundle,
    *,
    expected_intent,
    account_fingerprint: str,
) -> LiveObservationBundle:
    """Re-derive the outcome from exact evidence; the caller's outcome is ignored."""
    if not isinstance(bundle, LiveObservationBundle):
        raise TypeError("recovery persistence requires LiveObservationBundle")
    instrument = str(_intent_value(expected_intent, "instrument"))
    client_order_id = str(_intent_value(expected_intent, "client_order_id"))
    if bundle.client_order_id != client_order_id:
        raise ValueError("recovery bundle client order identity mismatch")

    recent = _rows(bundle.recent_orders)
    open_orders = _rows(bundle.open_orders)
    fills = _rows(bundle.fills)
    matching_recent = tuple(row for row in recent if str(row.get("clOrdId", "")) == client_order_id)
    matching_open = tuple(row for row in open_orders if str(row.get("clOrdId", "")) == client_order_id)
    queried = bundle.queried_order
    if queried is not None:
        _validate_order(queried, instrument=instrument, client_order_id=client_order_id, expected_intent=expected_intent)
    for row in matching_recent + matching_open:
        _validate_order(row, instrument=instrument, client_order_id=client_order_id, expected_intent=expected_intent)
    order_rows = ((queried,) if queried is not None else ()) + matching_recent + matching_open
    order_ids = {str(row["ordId"]) for row in order_rows}
    matching_fills = tuple(row for row in fills if str(row.get("clOrdId", "")) == client_order_id)
    for fill in matching_fills:
        _validate_fill(
            fill,
            instrument=instrument,
            client_order_id=client_order_id,
            order_ids=order_ids,
            expected_intent=expected_intent,
        )

    account = dict(bundle.account_observation)
    if account.get("provider_rejection"):
        rejection_hash = str(account.get("response_hash", ""))
        if len(rejection_hash) != 64 or order_rows or matching_fills:
            raise ValueError("provider-rejected recovery evidence is inconsistent")
        outcome = LiveRecoveryOutcome.PROVIDER_REJECTED
    else:
        if account.get("account_fingerprint") != account_fingerprint:
            raise ValueError("recovery account fingerprint mismatch")
        if _parse_timestamp(account.get("query_timestamp_utc")) != bundle.queried_at_utc:
            raise ValueError("recovery query timestamp mismatch")
        hashes = account.get("response_hashes")
        if not isinstance(hashes, Mapping):
            raise ValueError("recovery response hashes are missing")
        account_without_hashes = dict(account)
        account_without_hashes.pop("response_hashes", None)
        expected_hashes = {
            "queried_order": sha256_payload(None if queried is None else dict(queried)),
            "recent_orders": sha256_payload(tuple(dict(row) for row in recent)),
            "open_orders": sha256_payload(tuple(dict(row) for row in open_orders)),
            "fills": sha256_payload(tuple(dict(row) for row in fills)),
            "account": sha256_payload(account_without_hashes),
        }
        if dict(hashes) != expected_hashes:
            raise ValueError("recovery response hash mismatch")
        if matching_fills:
            outcome = LiveRecoveryOutcome.OBSERVED_EXTERNAL_FILL
        elif order_rows:
            outcome = LiveRecoveryOutcome.OBSERVED_EXTERNAL_ORDER
        elif account.get("inconclusive") is True:
            outcome = LiveRecoveryOutcome.INCONCLUSIVE
        else:
            outcome = LiveRecoveryOutcome.CONFIRMED_ABSENT

    return LiveObservationBundle(
        bundle.live_run_id,
        bundle.client_order_id,
        queried,
        recent,
        open_orders,
        fills,
        account,
        bundle.queried_at_utc,
        outcome,
    )



def normalize_verified_recovery_observation(
    okx_bundle,
    *,
    expected_intent,
    account_fingerprint: str,
) -> LiveObservationBundle:
    """Derive recovery outcome only from exact approved-adapter envelopes."""
    from .collector_evidence import (
        ObservationClassification,
        QueryDisposition,
        VerifiedOkxReadObservationBundle,
    )

    if not isinstance(okx_bundle, VerifiedOkxReadObservationBundle):
        raise TypeError("operational recovery requires a collector-issued OKX bundle")
    if (
        okx_bundle.purpose != "recovery"
        or okx_bundle.classification is not ObservationClassification.OPERATIONAL
        or okx_bundle.account_fingerprint != account_fingerprint
    ):
        raise PermissionError("fixture/imported or cross-account recovery evidence is forbidden")
    instrument = str(_intent_value(expected_intent, "instrument"))
    client_order_id = str(_intent_value(expected_intent, "client_order_id"))
    envelopes = {item.endpoint_kind: item for item in okx_bundle.envelopes}

    def normalized(kind, default):
        envelope = envelopes.get(kind)
        return default if envelope is None or not envelope.completed else envelope.normalized_payload

    queried = normalized("order_details", None)
    recent = _rows(normalized("order_history", ()))
    open_orders = _rows(normalized("pending_orders", ()))
    fills = _rows(normalized("fills", ()))
    matching_recent = tuple(row for row in recent if str(row.get("clOrdId", "")) == client_order_id)
    matching_open = tuple(row for row in open_orders if str(row.get("clOrdId", "")) == client_order_id)
    if queried is not None:
        _validate_order(queried, instrument=instrument, client_order_id=client_order_id, expected_intent=expected_intent)
    for row in matching_recent + matching_open:
        _validate_order(row, instrument=instrument, client_order_id=client_order_id, expected_intent=expected_intent)
    order_rows = ((queried,) if queried is not None else ()) + matching_recent + matching_open
    order_ids = {str(row["ordId"]) for row in order_rows}
    matching_fills = tuple(row for row in fills if str(row.get("clOrdId", "")) == client_order_id)
    for fill in matching_fills:
        _validate_fill(
            fill,
            instrument=instrument,
            client_order_id=client_order_id,
            order_ids=order_ids,
            expected_intent=expected_intent,
        )

    if matching_fills:
        outcome = LiveRecoveryOutcome.OBSERVED_EXTERNAL_FILL
    elif order_rows:
        outcome = LiveRecoveryOutcome.OBSERVED_EXTERNAL_ORDER
    elif any(item.disposition is QueryDisposition.EXPLICIT_PROVIDER_REJECTION for item in okx_bundle.envelopes):
        outcome = LiveRecoveryOutcome.PROVIDER_REJECTED
    elif not okx_bundle.complete:
        outcome = LiveRecoveryOutcome.INCONCLUSIVE
    else:
        outcome = LiveRecoveryOutcome.CONFIRMED_ABSENT

    query_completed = max(item.query_completed_at_utc for item in okx_bundle.envelopes)
    account = {
        "account_fingerprint": account_fingerprint,
        "query_timestamp_utc": query_completed,
        "response_bundle_id": str(okx_bundle.bundle_id),
        "endpoint_matrix_sha256": okx_bundle.endpoint_matrix_hash,
        "response_hashes": {
            item.endpoint_kind: item.canonical_response_hash
            for item in okx_bundle.envelopes
        },
        "classification": okx_bundle.classification.value,
    }
    return LiveObservationBundle(
        okx_bundle.live_run_id,
        client_order_id,
        queried,
        recent,
        open_orders,
        fills,
        account,
        query_completed,
        outcome,
    )

def query_first_recovery(*, live_run_id, venue, instrument: str, client_order_id: str, queried_at_utc, expected_intent=None, account_fingerprint: str | None = None):
    try:
        queried = venue.query_order(instrument=instrument, client_order_id=client_order_id)
        recent = _rows(venue.recent_orders(instrument=instrument))
        open_orders = _rows(venue.open_orders(instrument=instrument))
        fills = _rows(venue.fills(instrument=instrument))
        account = {"config": venue.read_account_config(), "balances": venue.read_balances(), "positions": venue.read_positions()}
    except Exception as exc:
        account = {
            "transport_ambiguous": type(exc).__name__,
            "response_hash": sha256_payload({"type": type(exc).__name__, "message": str(exc)}),
            "account_fingerprint": account_fingerprint,
            "query_timestamp_utc": queried_at_utc,
        }
        return LiveObservationBundle(live_run_id, client_order_id, None, (), (), (), account, queried_at_utc, LiveRecoveryOutcome.INCONCLUSIVE)

    account["account_fingerprint"] = account_fingerprint
    account["query_timestamp_utc"] = queried_at_utc
    account["response_hashes"] = {
        "queried_order": sha256_payload(queried),
        "recent_orders": sha256_payload(recent),
        "open_orders": sha256_payload(open_orders),
        "fills": sha256_payload(fills),
        "account": sha256_payload(account),
    }
    provisional = LiveObservationBundle(
        live_run_id,
        client_order_id,
        queried,
        recent,
        open_orders,
        fills,
        account,
        queried_at_utc,
        LiveRecoveryOutcome.INCONCLUSIVE,
    )
    if expected_intent is None or account_fingerprint is None:
        raise ValueError("exact expected intent and account fingerprint are required for recovery")
    return normalize_recovery_observation(provisional, expected_intent=expected_intent, account_fingerprint=account_fingerprint)


__all__ = [
    "normalize_recovery_observation", "normalize_verified_recovery_observation",
    "query_first_recovery",
]
