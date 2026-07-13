"""Typed Phase 8A evidence, risk-state, reservation, and reconciliation authorities."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from types import MappingProxyType
from typing import ClassVar, Mapping
from uuid import UUID

from secure_eval_wrapper.data_collection.hashing import sha256_payload
from secure_eval_wrapper.data_collection.time_utils import require_utc_datetime

from .models import LiveReconciliationStatus, live_uuid


SOURCE_KINDS = frozenset({
    "repository", "migration_catalog", "postgresql_probe", "audit_rollback_probe",
    "credential_reference", "credential_permissions", "account_config", "account_fingerprint",
    "subaccount", "account_mode", "margin_borrowing", "balances", "positions", "open_orders",
    "venue_time", "market_data", "instrument_metadata", "reconciliation", "kill_switch",
})


@dataclass(frozen=True)
class LiveEvidenceSource:
    live_run_id: UUID
    source_kind: str
    collected_at_utc: datetime
    payload: Mapping[str, object]
    operational: bool = True
    source_id: UUID | None = None
    source_hash: str | None = None

    def __post_init__(self) -> None:
        if self.source_kind not in SOURCE_KINDS:
            raise ValueError("unsupported live evidence source kind")
        require_utc_datetime(self.collected_at_utc, field_name="collected_at_utc")
        object.__setattr__(self, "payload", MappingProxyType(dict(self.payload)))
        digest = sha256_payload({
            "run": self.live_run_id,
            "kind": self.source_kind,
            "collected_at": self.collected_at_utc,
            "payload": dict(self.payload),
            "operational": self.operational,
        })
        if self.source_hash is not None and self.source_hash != digest:
            raise ValueError("live evidence source hash mismatch")
        object.__setattr__(self, "source_hash", digest)
        expected_id = live_uuid("preflight-source", {"run": self.live_run_id, "kind": self.source_kind, "hash": digest})
        if self.source_id is not None and self.source_id != expected_id:
            raise ValueError("live evidence source identity mismatch")
        object.__setattr__(self, "source_id", expected_id)

    @property
    def record_hash(self) -> str:
        return sha256_payload({
            "run": self.live_run_id,
            "kind": self.source_kind,
            "collected_at": self.collected_at_utc,
            "payload": dict(self.payload),
            "operational": self.operational,
            "source_hash": self.source_hash,
        })


class _TypedEvidence:
    KIND: ClassVar[str]

    @classmethod
    def source(cls, *, live_run_id: UUID, collected_at_utc: datetime, **payload: object) -> LiveEvidenceSource:
        return LiveEvidenceSource(live_run_id, cls.KIND, collected_at_utc, payload, True)


class RepositoryCommitEvidence(_TypedEvidence): KIND = "repository"
class MigrationCatalogEvidence(_TypedEvidence): KIND = "migration_catalog"
class PostgreSQLProbeEvidence(_TypedEvidence): KIND = "postgresql_probe"
class AuditRollbackProbeEvidence(_TypedEvidence): KIND = "audit_rollback_probe"
class CredentialReferenceEvidence(_TypedEvidence): KIND = "credential_reference"
class CredentialPermissionEvidence(_TypedEvidence): KIND = "credential_permissions"
class AccountConfigEvidence(_TypedEvidence): KIND = "account_config"
class AccountFingerprintEvidence(_TypedEvidence): KIND = "account_fingerprint"
class SubaccountEvidence(_TypedEvidence): KIND = "subaccount"
class AccountModeEvidence(_TypedEvidence): KIND = "account_mode"
class MarginBorrowingEvidence(_TypedEvidence): KIND = "margin_borrowing"
class BalanceEvidence(_TypedEvidence): KIND = "balances"
class PositionEvidence(_TypedEvidence): KIND = "positions"
class OpenOrderEvidence(_TypedEvidence): KIND = "open_orders"
class VenueTimeEvidence(_TypedEvidence): KIND = "venue_time"
class Phase7MarketDataEvidence(_TypedEvidence): KIND = "market_data"
class InstrumentMetadataEvidence(_TypedEvidence): KIND = "instrument_metadata"
class PostgreSQLReconciliationEvidence(_TypedEvidence): KIND = "reconciliation"
class PostgreSQLKillSwitchEvidence(_TypedEvidence): KIND = "kill_switch"


@dataclass(frozen=True)
class OperationalPreflightEvidence:
    """Exact operational sources. There are no caller-selected pass/fail fields."""

    live_run_id: UUID
    sources: tuple[LiveEvidenceSource, ...]
    warnings: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "sources", tuple(self.sources))
        by_kind: dict[str, LiveEvidenceSource] = {}
        for source in self.sources:
            if source.live_run_id != self.live_run_id:
                raise ValueError("preflight evidence source belongs to another run")
            if not source.operational:
                raise ValueError("fixture evidence cannot enter operational preflight")
            if source.source_kind in by_kind:
                raise ValueError(f"duplicate operational source: {source.source_kind}")
            by_kind[source.source_kind] = source
        missing = SOURCE_KINDS.difference(by_kind)
        if missing:
            raise ValueError("operational preflight evidence is incomplete: " + ", ".join(sorted(missing)))

    def get(self, kind: str) -> LiveEvidenceSource:
        for source in self.sources:
            if source.source_kind == kind:
                return source
        raise KeyError(kind)


@dataclass(frozen=True)
class FixtureOnlyPreflightEvidence:
    """Offline-test fixture only. It can never create an operational passed report."""

    live_run_id: UUID
    claims: Mapping[str, object]
    fake_transport: bool = True


@dataclass(frozen=True)
class LiveRuntimeRiskState:
    live_run_id: UUID
    trading_day: date
    current_equity: Decimal
    high_watermark_equity: Decimal
    daily_submitted_notional: Decimal
    daily_realized_pnl: Decimal
    gross_exposure: Decimal
    net_exposure: Decimal
    order_timestamps_utc: tuple[datetime, ...]
    cancellation_timestamps_utc: tuple[datetime, ...]
    open_order_count: int
    oldest_unknown_order_at_utc: datetime | None
    oldest_unacknowledged_order_at_utc: datetime | None
    latest_market_data_at_utc: datetime
    latest_account_snapshot_at_utc: datetime
    latest_reconciliation_at_utc: datetime
    latest_reconciliation_status: LiveReconciliationStatus
    clock_skew_seconds: Decimal
    run_started_at_utc: datetime
    transport_failure_count: int
    balances: Mapping[str, Mapping[str, object]]
    positions: Mapping[str, Mapping[str, object]]
    version: int

    def __post_init__(self) -> None:
        decimal_names = (
            "current_equity", "high_watermark_equity", "daily_submitted_notional",
            "daily_realized_pnl", "gross_exposure", "net_exposure", "clock_skew_seconds",
        )
        for name in decimal_names:
            value = getattr(self, name)
            if not isinstance(value, Decimal) or not value.is_finite():
                raise ValueError(f"{name} must be a finite Decimal")
        if self.current_equity < 0 or self.high_watermark_equity < self.current_equity:
            raise ValueError("runtime equity state is invalid")
        if self.daily_submitted_notional < 0 or self.gross_exposure < 0 or self.clock_skew_seconds < 0:
            raise ValueError("runtime risk magnitudes cannot be negative")
        if min(self.open_order_count, self.transport_failure_count, self.version) < 0:
            raise ValueError("runtime counters cannot be negative")
        for name in ("latest_market_data_at_utc", "latest_account_snapshot_at_utc", "latest_reconciliation_at_utc", "run_started_at_utc"):
            require_utc_datetime(getattr(self, name), field_name=name)
        for name in ("oldest_unknown_order_at_utc", "oldest_unacknowledged_order_at_utc"):
            value = getattr(self, name)
            if value is not None:
                require_utc_datetime(value, field_name=name)
        object.__setattr__(self, "latest_reconciliation_status", LiveReconciliationStatus(self.latest_reconciliation_status))
        object.__setattr__(self, "balances", MappingProxyType(dict(self.balances)))
        object.__setattr__(self, "positions", MappingProxyType(dict(self.positions)))


@dataclass(frozen=True)
class LiveReservationAuthority:
    live_run_id: UUID
    order_intent_id: UUID
    currency: str
    original_amount: Decimal
    remaining_amount: Decimal
    original_quantity: Decimal
    remaining_quantity: Decimal
    worst_case_price: Decimal
    maximum_fee_bps: Decimal
    maximum_fee_amount: Decimal
    fee_currency_policy: str
    risk_notional: Decimal
    reservation_notional: Decimal
    calculator_version: str
    source_hashes: Mapping[str, str]
    reservation_id: UUID | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "currency", str(self.currency).strip().upper())
        if not self.currency or not self.fee_currency_policy or not self.calculator_version:
            raise ValueError("reservation text fields are required")
        positive = ("original_amount", "original_quantity", "worst_case_price", "risk_notional", "reservation_notional")
        nonnegative = ("remaining_amount", "remaining_quantity", "maximum_fee_bps", "maximum_fee_amount")
        for name in positive + nonnegative:
            value = getattr(self, name)
            if not isinstance(value, Decimal) or not value.is_finite() or (name in positive and value <= 0) or (name in nonnegative and value < 0):
                raise ValueError(f"invalid reservation {name}")
        if self.remaining_amount > self.original_amount or self.remaining_quantity > self.original_quantity:
            raise ValueError("reservation remainder exceeds original authority")
        hashes = dict(self.source_hashes)
        if not hashes or any(len(value) != 64 for value in hashes.values()):
            raise ValueError("reservation source hashes are incomplete")
        object.__setattr__(self, "source_hashes", MappingProxyType(hashes))
        expected = live_uuid("reservation", {"intent": self.order_intent_id})
        if self.reservation_id is not None and self.reservation_id != expected:
            raise ValueError("reservation identity mismatch")
        object.__setattr__(self, "reservation_id", expected)

    @property
    def record_hash(self) -> str:
        return sha256_payload({
            "run": self.live_run_id, "intent": self.order_intent_id, "currency": self.currency,
            "original_amount": self.original_amount, "original_quantity": self.original_quantity,
            "worst_case_price": self.worst_case_price, "maximum_fee_bps": self.maximum_fee_bps,
            "maximum_fee_amount": self.maximum_fee_amount, "fee_currency_policy": self.fee_currency_policy,
            "risk_notional": self.risk_notional, "reservation_notional": self.reservation_notional,
            "calculator_version": self.calculator_version, "source_hashes": dict(self.source_hashes),
        })


@dataclass(frozen=True)
class LiveLocalProjection:
    live_run_id: UUID
    account_fingerprint: str
    orders: tuple[Mapping[str, object], ...]
    fills: tuple[Mapping[str, object], ...]
    balances: Mapping[str, object]
    positions: Mapping[str, object]
    sequence: int
    observed_at_utc: datetime
    source_ids: tuple[UUID, ...]

    def __post_init__(self) -> None:
        if not self.account_fingerprint or self.sequence < 0:
            raise ValueError("local projection identity and sequence are required")
        require_utc_datetime(self.observed_at_utc, field_name="observed_at_utc")
        object.__setattr__(self, "orders", tuple(MappingProxyType(dict(row)) for row in self.orders))
        object.__setattr__(self, "fills", tuple(MappingProxyType(dict(row)) for row in self.fills))
        object.__setattr__(self, "balances", MappingProxyType(dict(self.balances)))
        object.__setattr__(self, "positions", MappingProxyType(dict(self.positions)))
        object.__setattr__(self, "source_ids", tuple(self.source_ids))


@dataclass(frozen=True)
class LiveVenueObservation:
    live_run_id: UUID
    account_fingerprint: str
    orders: tuple[Mapping[str, object], ...]
    fills: tuple[Mapping[str, object], ...]
    balances: Mapping[str, object]
    positions: Mapping[str, object]
    sequence: int
    observed_at_utc: datetime
    response_hashes: tuple[str, ...]

    def __post_init__(self) -> None:
        if not self.account_fingerprint or self.sequence < 0:
            raise ValueError("venue observation identity and sequence are required")
        require_utc_datetime(self.observed_at_utc, field_name="observed_at_utc")
        object.__setattr__(self, "orders", tuple(MappingProxyType(dict(row)) for row in self.orders))
        object.__setattr__(self, "fills", tuple(MappingProxyType(dict(row)) for row in self.fills))
        object.__setattr__(self, "balances", MappingProxyType(dict(self.balances)))
        object.__setattr__(self, "positions", MappingProxyType(dict(self.positions)))
        object.__setattr__(self, "response_hashes", tuple(self.response_hashes))


__all__ = [name for name in globals() if name.endswith("Evidence") or name.startswith("Live") or name == "SOURCE_KINDS"]
