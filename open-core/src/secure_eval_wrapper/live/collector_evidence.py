"""Collector-only OKX response envelopes for operational Phase 8A authority."""
from __future__ import annotations

from dataclasses import dataclass
import json
from datetime import datetime
from enum import Enum
from types import MappingProxyType
from typing import Mapping
from uuid import UUID

from secure_eval_wrapper.data_collection.hashing import sha256_payload
from secure_eval_wrapper.data_collection.time_utils import require_utc_datetime

from .models import live_uuid
from .identity import derive_okx_account_fingerprint, validate_okx_account_fingerprint


class ObservationClassification(str, Enum):
    OPERATIONAL = "operational_collector"
    FIXTURE = "fixture"
    IMPORTED = "imported"


class QueryDisposition(str, Enum):
    COMPLETED = "completed"
    TRANSPORT_AMBIGUOUS = "transport_ambiguous"
    PARSER_ERROR = "parser_error"
    RATE_LIMITED = "rate_limited"
    EXPLICIT_PROVIDER_REJECTION = "explicit_provider_rejection"


_ADAPTER_SEAL = object()
_ENDPOINT_PATHS = {
    "account_config": "/api/v5/account/config",
    "balances": "/api/v5/account/balance",
    "positions": "/api/v5/account/positions",
    "pending_orders": "/api/v5/trade/orders-pending",
    "order_history": "/api/v5/trade/orders-history",
    "fills": "/api/v5/trade/fills-history",
    "venue_time": "/api/v5/public/time",
    "instrument_metadata": "/api/v5/public/instruments",
    "order_details": "/api/v5/trade/order",
}


def expected_preflight_request_paths(instrument: str) -> tuple[str, ...]:
    """Return the exact ordered Phase 8B request-path contract."""
    if not isinstance(instrument, str) or not instrument or instrument != instrument.upper():
        raise ValueError("instrument must be an explicit uppercase OKX Spot instrument")
    return (
        _ENDPOINT_PATHS["account_config"],
        _ENDPOINT_PATHS["balances"],
        f'{_ENDPOINT_PATHS["instrument_metadata"]}?instId={instrument}&instType=SPOT',
        f'{_ENDPOINT_PATHS["pending_orders"]}?instId={instrument}&instType=SPOT',
        _ENDPOINT_PATHS["positions"],
        _ENDPOINT_PATHS["venue_time"],
    )


def _freeze_json(value):
    if isinstance(value, Mapping):
        return MappingProxyType({str(key): _freeze_json(item) for key, item in value.items()})
    if isinstance(value, (list, tuple)):
        return tuple(_freeze_json(item) for item in value)
    return value


@dataclass(frozen=True, init=False)
class VerifiedOkxResponseEnvelope:
    endpoint_kind: str
    request_identity: str
    request_method: str
    request_path: str
    top_level_provider_code: str | None
    query_started_at_utc: datetime
    query_completed_at_utc: datetime
    disposition: QueryDisposition
    raw_response: Mapping[str, object] | None
    normalized_payload: object
    parser_version: str
    canonical_response_hash: str | None
    normalized_payload_hash: str
    record_hash: str

    def __init__(
        self,
        *,
        endpoint_kind: str,
        request_identity: str,
        request_path: str,
        query_started_at_utc: datetime,
        query_completed_at_utc: datetime,
        disposition: QueryDisposition,
        raw_response: Mapping[str, object] | None,
        normalized_payload: object,
        parser_version: str,
        _seal: object | None = None,
    ) -> None:
        if _seal is not _ADAPTER_SEAL:
            raise PermissionError("OKX response envelopes can only be issued by the approved adapter")
        expected_path = _ENDPOINT_PATHS.get(endpoint_kind)
        if expected_path is None or request_path.split("?", 1)[0] != expected_path:
            raise ValueError("OKX envelope endpoint kind and request path do not match")
        expected_identity = sha256_payload({"method": "GET", "path": request_path})
        if request_identity != expected_identity:
            raise ValueError("OKX envelope request identity does not match its exact GET path")
        started = require_utc_datetime(query_started_at_utc, field_name="query_started_at_utc")
        completed = require_utc_datetime(query_completed_at_utc, field_name="query_completed_at_utc")
        if completed < started:
            raise ValueError("query completion precedes query start")
        disposition = QueryDisposition(disposition)
        raw_plain = None if raw_response is None else json.loads(json.dumps(dict(raw_response)))
        raw = None if raw_plain is None else _freeze_json(raw_plain)
        provider_code = None if raw is None else str(raw.get("code"))
        if disposition is QueryDisposition.COMPLETED:
            if raw is None or provider_code != "0":
                raise ValueError("completed OKX envelope requires an exact code=0 response")
        elif disposition is QueryDisposition.EXPLICIT_PROVIDER_REJECTION:
            if raw is None or provider_code in (None, "0"):
                raise ValueError("provider rejection must be parsed from an explicit non-zero OKX response")
        canonical_hash = None if raw_plain is None else sha256_payload(raw_plain)

        normalized_hash = sha256_payload(normalized_payload)
        normalized = _freeze_json(normalized_payload)
        record_hash = sha256_payload({
            "endpoint_kind": endpoint_kind,
            "request_identity": request_identity,
            "request_method": "GET",
            "request_path": request_path,
            "provider_code": provider_code,
            "started": started,
            "completed": completed,
            "disposition": disposition,
            "canonical_response_hash": canonical_hash,
            "normalized_payload_hash": normalized_hash,
            "parser_version": parser_version,
        })
        values = {
            "endpoint_kind": endpoint_kind, "request_identity": request_identity,
            "request_method": "GET", "request_path": request_path,
            "top_level_provider_code": provider_code, "query_started_at_utc": started,
            "query_completed_at_utc": completed, "disposition": disposition,
            "raw_response": raw, "normalized_payload": normalized,
            "parser_version": parser_version, "canonical_response_hash": canonical_hash,
            "normalized_payload_hash": normalized_hash, "record_hash": record_hash,
        }
        for name, value in values.items():
            object.__setattr__(self, name, value)

    @property
    def completed(self) -> bool:
        return self.disposition is QueryDisposition.COMPLETED


def _issue_okx_envelope(**kwargs: object) -> VerifiedOkxResponseEnvelope:
    return VerifiedOkxResponseEnvelope(_seal=_ADAPTER_SEAL, **kwargs)


_PURPOSE_ENDPOINTS = {
    "preflight": frozenset({
        "account_config", "balances", "positions", "pending_orders",
        "venue_time", "instrument_metadata",
    }),
    "reconciliation": frozenset({
        "account_config", "balances", "positions", "pending_orders",
        "order_history", "fills", "venue_time",
    }),
    "recovery": frozenset({
        "account_config", "balances", "positions", "pending_orders",
        "order_history", "fills", "order_details",
    }),
}


@dataclass(frozen=True, init=False)
class VerifiedOkxReadObservationBundle:
    live_run_id: UUID
    purpose: str
    account_fingerprint: str
    envelopes: tuple[VerifiedOkxResponseEnvelope, ...]
    venue_observed_at_utc: datetime
    venue_sequence: int
    classification: ObservationClassification
    transport_is_fake: bool
    collector_kind: str
    collector_version: str
    parser_version: str
    endpoint_matrix_hash: str
    normalized_payload_hash: str
    bundle_id: UUID
    record_hash: str

    def __init__(
        self,
        *,
        live_run_id: UUID,
        purpose: str,
        account_fingerprint: str,
        envelopes: tuple[VerifiedOkxResponseEnvelope, ...],
        venue_observed_at_utc: datetime,
        venue_sequence: int,
        classification: ObservationClassification = ObservationClassification.OPERATIONAL,
        transport_is_fake: bool = False,
        _seal: object | None = None,
    ) -> None:
        if _seal is not _ADAPTER_SEAL:
            raise PermissionError("OKX observation bundles can only be issued by the approved adapter")
        if purpose not in _PURPOSE_ENDPOINTS or not account_fingerprint or venue_sequence < 0:
            raise ValueError("invalid OKX observation bundle identity")
        observed = require_utc_datetime(venue_observed_at_utc, field_name="venue_observed_at_utc")
        envelopes = tuple(envelopes)
        if any(not isinstance(item, VerifiedOkxResponseEnvelope) for item in envelopes):
            raise TypeError("bundle accepts only verified adapter envelopes")
        by_kind = {item.endpoint_kind: item for item in envelopes}
        if len(by_kind) != len(envelopes):
            raise ValueError("duplicate OKX endpoint envelope")
        if set(by_kind) != set(_PURPOSE_ENDPOINTS[purpose]):
            raise ValueError("OKX bundle does not contain its exact purpose endpoint matrix")
        account_envelope = by_kind["account_config"]
        account_payload = account_envelope.normalized_payload
        if not account_envelope.completed or not isinstance(account_payload, Mapping):
            raise ValueError("OKX bundle requires a completed account-config identity envelope")
        derived_account_fingerprint = derive_okx_account_fingerprint(account_payload.get("uid"))
        validate_okx_account_fingerprint(account_fingerprint)
        if account_fingerprint != derived_account_fingerprint:
            raise ValueError("OKX bundle account fingerprint is not derived from its exact response UID")
        classification = ObservationClassification(classification)
        matrix = {
            kind: {
                "completed": kind in by_kind and by_kind[kind].completed,
                "disposition": None if kind not in by_kind else by_kind[kind].disposition.value,
                "response_hash": None if kind not in by_kind else by_kind[kind].canonical_response_hash,
            }
            for kind in sorted(_PURPOSE_ENDPOINTS[purpose])
        }
        matrix_hash = sha256_payload(matrix)
        normalized_hash = sha256_payload({
            kind: by_kind[kind].normalized_payload for kind in sorted(by_kind)
        })
        record_hash = sha256_payload({
            "run": live_run_id, "purpose": purpose, "account": account_fingerprint,
            "observed": observed, "sequence": venue_sequence,
            "classification": classification, "matrix": matrix_hash,
            "transport_is_fake": bool(transport_is_fake),
            "normalized": normalized_hash,
        })
        bundle_id = live_uuid("okx-read-observation-bundle", {"run": live_run_id, "record": record_hash})
        values = {
            "live_run_id": live_run_id, "purpose": purpose,
            "account_fingerprint": account_fingerprint, "envelopes": envelopes,
            "venue_observed_at_utc": observed, "venue_sequence": venue_sequence,
            "classification": classification, "collector_kind": "okx_production_spot_read_adapter",
            "transport_is_fake": bool(transport_is_fake),
            "collector_version": "phase8a-0025-v1", "parser_version": "okx-v5-parser-v4",
            "endpoint_matrix_hash": matrix_hash, "normalized_payload_hash": normalized_hash,
            "bundle_id": bundle_id, "record_hash": record_hash,
        }
        for name, value in values.items():
            object.__setattr__(self, name, value)

    @property
    def complete(self) -> bool:
        by_kind = {item.endpoint_kind: item for item in self.envelopes}
        return all(kind in by_kind and by_kind[kind].completed for kind in _PURPOSE_ENDPOINTS[self.purpose])

    def envelope(self, kind: str) -> VerifiedOkxResponseEnvelope:
        return next(item for item in self.envelopes if item.endpoint_kind == kind)


def _issue_okx_bundle(**kwargs: object) -> VerifiedOkxReadObservationBundle:
    return VerifiedOkxReadObservationBundle(_seal=_ADAPTER_SEAL, **kwargs)


__all__ = [
    "ObservationClassification", "QueryDisposition",
    "VerifiedOkxResponseEnvelope", "VerifiedOkxReadObservationBundle",
    "expected_preflight_request_paths",
]
