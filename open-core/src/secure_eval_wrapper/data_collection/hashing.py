"""Deterministic hashing helpers for market-data source provenance."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from datetime import datetime
from decimal import Decimal
from enum import Enum
from uuid import UUID

from secure_eval_wrapper.data_collection.time_utils import require_utc_datetime


def _canonical_json_default(value: object) -> object:
    if isinstance(value, datetime):
        utc_value = require_utc_datetime(value, field_name="canonical JSON datetime")
        return utc_value.isoformat().replace("+00:00", "Z")
    if isinstance(value, Decimal):
        return format(value, "f")
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, Enum):
        return value.value
    raise TypeError(f"Unsupported canonical JSON value: {type(value).__name__}")


def canonical_json_dumps(value: object) -> str:
    """Serialize a JSON-compatible value into a stable, compact representation.

    Mapping keys are sorted, insignificant whitespace is removed, non-finite floating-point
    values are rejected, and supported non-JSON domain values use deterministic encodings.
    """

    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
        default=_canonical_json_default,
    )


def sha256_payload(payload: object) -> str:
    """Return the lowercase SHA-256 digest of a canonicalized payload."""

    canonical_payload = canonical_json_dumps(payload).encode("utf-8")
    return hashlib.sha256(canonical_payload).hexdigest()


def sha256_observation_source(
    *,
    payload: object,
    request_metadata: Mapping[str, object],
) -> str:
    """Hash one source payload together with stable request provenance metadata."""

    return sha256_payload(
        {
            "payload": payload,
            "request_metadata": dict(request_metadata),
        }
    )
