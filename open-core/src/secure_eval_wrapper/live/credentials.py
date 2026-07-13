"""Local-only credential loading and redaction for guarded live preflight."""
from __future__ import annotations

import hashlib
import os
import re
from abc import ABC, abstractmethod
from collections.abc import Mapping
from datetime import datetime

from .gates import common_ci_indicators
from .models import LiveCredentialReference

_SECRET_KEY = re.compile(r"(api.?key|secret|passphrase|signature|authorization|cookie|token|ok-access-(?:key|sign|passphrase))", re.I)
_SECRET_VALUE = re.compile(r"(?i)(authorization:\s*|OK-ACCESS-(?:KEY|SIGN|PASSPHRASE)\s*[:=]\s*)[^\s,;]+")
_SECRET_QUERY = re.compile(r"([?&](?:api_?key|signature|token|passphrase|secret)=)[^&]+", re.I)
_FORBIDDEN_PERMISSIONS = frozenset({"withdraw", "withdrawal", "transfer", "subaccount_transfer", "borrow", "margin_admin", "leverage_admin", "account_admin"})
_ALLOWED_PERMISSIONS = frozenset({"read", "spot_trade"})


def redact(value):
    if isinstance(value, Mapping):
        return {str(key): ("[REDACTED]" if _SECRET_KEY.search(str(key)) else redact(item)) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return type(value)(redact(item) for item in value)
    if isinstance(value, str):
        return _SECRET_QUERY.sub(r"\1[REDACTED]", _SECRET_VALUE.sub(r"\1[REDACTED]", value))
    return value


def validate_permission_summary(permissions: tuple[str, ...]) -> tuple[str, ...]:
    normalized = tuple(sorted({str(value).strip().lower() for value in permissions if str(value).strip()}))
    if not normalized:
        raise PermissionError("credential permissions are unknown")
    forbidden = tuple(sorted(_FORBIDDEN_PERMISSIONS.intersection(normalized)))
    unknown = tuple(sorted(set(normalized).difference(_ALLOWED_PERMISSIONS).difference(_FORBIDDEN_PERMISSIONS)))
    if forbidden or unknown:
        raise PermissionError("credential permissions are forbidden or unrecognized")
    if "read" not in normalized:
        raise PermissionError("read permission is required for preflight")
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
    def __init__(self, *, alias: str = "OKX_LIVE_API_KEY", key_variable: str = "OKX_LIVE_API_KEY", secret_variable: str = "OKX_LIVE_SECRET_KEY", passphrase_variable: str = "OKX_LIVE_PASSPHRASE", account_fingerprint: str = "0000000000000000") -> None:
        self.alias = alias; self.key_variable = key_variable; self.secret_variable = secret_variable; self.passphrase_variable = passphrase_variable; self.account_fingerprint = account_fingerprint; self.load_count = 0

    def reference(self, *, verified_at_utc: datetime | None = None, permissions: tuple[str, ...] = ()) -> LiveCredentialReference:
        return LiveCredentialReference("okx", self.alias, "environment", self.account_fingerprint, False, verified_at_utc, permissions)

    def load(self, *, gates: Mapping[str, bool]) -> LiveCredentialMaterial:
        _validate_load_gates(gates); self.load_count += 1
        return LiveCredentialMaterial(os.environ.get(self.key_variable, ""), os.environ.get(self.secret_variable, ""), os.environ.get(self.passphrase_variable, ""))


class InjectedLocalCredentialProvider(LiveCredentialProvider):
    """Explicit local injection boundary used by offline tests and operator integrations."""
    def __init__(self, key: str, secret: str, passphrase: str, *, alias: str = "injected-local", account_fingerprint: str = "0000000000000000") -> None:
        self._values = (key, secret, passphrase); self.alias = alias; self.account_fingerprint = account_fingerprint; self.load_count = 0

    def reference(self, *, verified_at_utc: datetime | None = None, permissions: tuple[str, ...] = ()) -> LiveCredentialReference:
        public_fingerprint = hashlib.sha256(self._values[0].encode("utf-8")).hexdigest()[:16]
        return LiveCredentialReference("okx", self.alias, "injected_local", self.account_fingerprint or public_fingerprint, False, verified_at_utc, permissions)

    def load(self, *, gates: Mapping[str, bool]) -> LiveCredentialMaterial:
        _validate_load_gates(gates); self.load_count += 1; return LiveCredentialMaterial(*self._values)


__all__ = ["redact", "validate_permission_summary", "LiveCredentialMaterial", "LiveCredentialProvider", "EnvironmentLiveCredentialProvider", "InjectedLocalCredentialProvider"]
