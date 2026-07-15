"""Credential-free redaction and permission policy helpers."""
from __future__ import annotations

import re
from collections.abc import Mapping


_SECRET_KEY = re.compile(
    r"(api.?key|secret|passphrase|signature|authorization|cookie|token|"
    r"ok-access-(?:key|sign|passphrase))",
    re.I,
)
_SECRET_VALUE = re.compile(
    r"(?i)(authorization:\s*|OK-ACCESS-(?:KEY|SIGN|PASSPHRASE)\s*[:=]\s*)[^\s,;]+"
)
_SECRET_QUERY = re.compile(
    r"([?&](?:api_?key|signature|token|passphrase|secret)=)[^&]+", re.I
)
_EXPECTED_PERMISSION_NORMALIZATION = {
    "read": "read",
    "read_only": "read",
    "trade": "trade",
    "withdraw": "withdraw",
}


def redact(value):
    if isinstance(value, Mapping):
        return {
            str(key): ("[REDACTED]" if _SECRET_KEY.search(str(key)) else redact(item))
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return type(value)(redact(item) for item in value)
    if isinstance(value, str):
        return _SECRET_QUERY.sub(
            r"\1[REDACTED]", _SECRET_VALUE.sub(r"\1[REDACTED]", value)
        )
    return value


def normalize_expected_permission_summary(
    permissions: tuple[str, ...],
) -> tuple[str, ...]:
    values = tuple(permissions)
    if not values:
        return ()
    normalized = []
    for value in values:
        if (
            not isinstance(value, str)
            or not value
            or value != value.strip()
            or value != value.lower()
        ):
            raise PermissionError("expected credential permissions are malformed")
        mapped = _EXPECTED_PERMISSION_NORMALIZATION.get(value)
        if mapped is None:
            raise PermissionError("expected credential permissions are unrecognized")
        normalized.append(mapped)
    return tuple(sorted(set(normalized)))


def validate_permission_summary(permissions: tuple[str, ...]) -> tuple[str, ...]:
    normalized = normalize_expected_permission_summary(permissions)
    if normalized != ("read",):
        raise PermissionError("Phase 8A credential expectation must be exactly read-only")
    return normalized


__all__ = ["normalize_expected_permission_summary", "redact", "validate_permission_summary"]
