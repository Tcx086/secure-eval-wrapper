"""Local-only credential loading and redaction for guarded live preflight."""
from __future__ import annotations

import os
import re
from abc import ABC, abstractmethod
from collections.abc import Mapping
from datetime import datetime

from .gates import common_ci_indicators
from .identity import validate_okx_account_fingerprint
from .models import LiveCredentialReference

_SECRET_KEY = re.compile(r"(api.?key|secret|passphrase|signature|authorization|cookie|token|ok-access-(?:key|sign|passphrase))", re.I)
_SECRET_VALUE = re.compile(r"(?i)(authorization:\s*|OK-ACCESS-(?:KEY|SIGN|PASSPHRASE)\s*[:=]\s*)[^\s,;]+")
_SECRET_QUERY = re.compile(r"([?&](?:api_?key|signature|token|passphrase|secret)=)[^&]+", re.I)
_EXPECTED_PERMISSION_NORMALIZATION = {
    "read": "read",
    "read_only": "read",
    "trade": "trade",
    "withdraw": "withdraw",
}


def redact(value):
    if isinstance(value, Mapping):
        return {str(key): ("[REDACTED]" if _SECRET_KEY.search(str(key)) else redact(item)) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return type(value)(redact(item) for item in value)
    if isinstance(value, str):
        return _SECRET_QUERY.sub(r"\1[REDACTED]", _SECRET_VALUE.sub(r"\1[REDACTED]", value))
    return value


def normalize_expected_permission_summary(permissions: tuple[str, ...]) -> tuple[str, ...]:
    values = tuple(permissions)
    if not values:
        return ()
    normalized = []
    for value in values:
        if not isinstance(value, str) or not value or value != value.strip() or value != value.lower():
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


class LiveCredentialMaterial:
    __slots__ = ("_key", "_secret", "_passphrase")

    def __init__(self, key: str, secret: str, passphrase: str) -> None:
        if not all(isinstance(value, str) and value for value in (key, secret, passphrase)):
            raise ValueError("required local credential field is missing")
        self._key = key; self._secret = secret; self._passphrase = passphrase

    def request_values(self) -> tuple[str, str, str]:
        return self._key, self._secret, self._passphrase

    def __repr__(self) -> str:
        return "LiveCredentialMaterial([REDACTED])"

    __str__ = __repr__


class LiveCredentialProvider(ABC):
    @abstractmethod
    def reference(self, *, verified_at_utc: datetime | None = None, permissions: tuple[str, ...] = ()) -> LiveCredentialReference:
        raise NotImplementedError

    @abstractmethod
    def load(self, *, gates: Mapping[str, bool]) -> LiveCredentialMaterial:
        raise NotImplementedError


def _validate_load_gates(gates: Mapping[str, bool]) -> None:
    required = ("read_only_preflight", "provider_selected", "production_environment", "endpoint_catalog_valid", "configuration_valid", "production_writes_disabled", "kill_switch_armed", "postgresql_available")
    missing = [name for name in required if gates.get(name) is not True]
    if missing:
        raise PermissionError("credential loading blocked before all read-only gates pass")
    if common_ci_indicators():
        raise PermissionError("production credentials cannot be loaded in CI")


class EnvironmentLiveCredentialProvider(LiveCredentialProvider):
    def __init__(self, *, expected_account_fingerprint: str, alias: str = "OKX_LIVE_API_KEY", key_variable: str = "OKX_LIVE_API_KEY", secret_variable: str = "OKX_LIVE_SECRET_KEY", passphrase_variable: str = "OKX_LIVE_PASSPHRASE") -> None:
        self.alias = alias; self.key_variable = key_variable; self.secret_variable = secret_variable; self.passphrase_variable = passphrase_variable; self.account_fingerprint = validate_okx_account_fingerprint(expected_account_fingerprint, field_name="expected_account_fingerprint"); self.load_count = 0

    def reference(self, *, verified_at_utc: datetime | None = None, permissions: tuple[str, ...] = ()) -> LiveCredentialReference:
        return LiveCredentialReference("okx", self.alias, "environment", self.account_fingerprint, False, verified_at_utc, permissions)

    def load(self, *, gates: Mapping[str, bool]) -> LiveCredentialMaterial:
        _validate_load_gates(gates); self.load_count += 1
        return LiveCredentialMaterial(os.environ.get(self.key_variable, ""), os.environ.get(self.secret_variable, ""), os.environ.get(self.passphrase_variable, ""))


class InjectedLocalCredentialProvider(LiveCredentialProvider):
    """Explicit local injection boundary used by offline tests and operator integrations."""
    def __init__(self, key: str, secret: str, passphrase: str, *, expected_account_fingerprint: str, alias: str = "injected-local") -> None:
        self._values = (key, secret, passphrase); self.alias = alias; self.account_fingerprint = validate_okx_account_fingerprint(expected_account_fingerprint, field_name="expected_account_fingerprint"); self.load_count = 0

    def reference(self, *, verified_at_utc: datetime | None = None, permissions: tuple[str, ...] = ()) -> LiveCredentialReference:
        return LiveCredentialReference("okx", self.alias, "injected_local", self.account_fingerprint, False, verified_at_utc, permissions)

    def load(self, *, gates: Mapping[str, bool]) -> LiveCredentialMaterial:
        _validate_load_gates(gates); self.load_count += 1; return LiveCredentialMaterial(*self._values)


__all__ = ["redact", "normalize_expected_permission_summary", "validate_permission_summary", "LiveCredentialMaterial", "LiveCredentialProvider", "EnvironmentLiveCredentialProvider", "InjectedLocalCredentialProvider"]
