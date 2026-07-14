"""Immutable Phase 8A live-domain evidence contracts."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from enum import Enum
from types import MappingProxyType
from typing import Mapping
from uuid import NAMESPACE_URL, UUID, uuid5

from secure_eval_wrapper.alpha.identity import SeriesIdentity
from secure_eval_wrapper.data_collection.hashing import sha256_payload
from secure_eval_wrapper.data_collection.time_utils import require_utc_datetime
from secure_eval_wrapper.execution.models import AccountingMode, OrderSide, OrderType, TimeInForce

_SHA = re.compile(r"^[0-9a-f]{64}$")


def live_uuid(kind: str, payload: object) -> UUID:
    return uuid5(NAMESPACE_URL, f"secure-eval-wrapper:live:{kind}:{sha256_payload(payload)}")


def _text(value: str, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be non-empty")
    return value.strip()


def _hash(value: str, name: str) -> str:
    if not isinstance(value, str) or not _SHA.fullmatch(value):
        raise ValueError(f"{name} must be lowercase SHA-256")
    return value


def _utc(value: datetime, name: str) -> datetime:
    return require_utc_datetime(value, field_name=name)


def _decimal(value: Decimal, name: str, *, positive: bool = False, nonnegative: bool = False) -> Decimal:
    if not isinstance(value, Decimal) or not value.is_finite():
        raise ValueError(f"{name} must be a finite Decimal")
    if positive and value <= 0:
        raise ValueError(f"{name} must be positive")
    if nonnegative and value < 0:
        raise ValueError(f"{name} must be non-negative")
    return value


def _map(value: Mapping[str, object]) -> Mapping[str, object]:
    return MappingProxyType(dict(value))


class LivePreflightStatus(str, Enum):
    PASSED = "passed"
    PASSED_FOR_RESET = "passed_for_reset"
    BLOCKED = "blocked"

class LivePreflightPurpose(str, Enum):
    RUN_START = "run_start"
    RUN_CONTINUE = "run_continue"
    KILL_RESET = "kill_reset"



class LiveOrderState(str, Enum):
    DRY_RUN_PREPARED = "dry_run_prepared"
    DRY_RUN_BLOCKED = "dry_run_blocked"
    DRY_RUN_SUPPRESSED = "dry_run_suppressed"
    PENDING_RECOVERY = "pending_recovery"
    UNEXPECTED_EXTERNAL_SIDE_EFFECT = "unexpected_external_side_effect"
    INCIDENT_BLOCKED = "incident_blocked"


class LiveKillState(str, Enum):
    ARMED = "armed"
    TRIGGERED = "triggered"
    CANCELLATION_IN_PROGRESS = "cancellation_in_progress"
    CANCELLATION_AMBIGUOUS = "cancellation_ambiguous"
    STOPPED = "stopped"
    RESET_PENDING = "reset_pending"
    RESET = "reset"


class LiveReconciliationStatus(str, Enum):
    RECONCILED = "reconciled"
    BLOCKED = "blocked"
    UNKNOWN = "unknown"

class LiveRecoveryOutcome(str, Enum):
    CONFIRMED_ABSENT = "confirmed_absent"
    OBSERVED_EXTERNAL_ORDER = "observed_external_order"
    OBSERVED_EXTERNAL_FILL = "observed_external_fill"
    INCONCLUSIVE = "inconclusive"
    PROVIDER_REJECTED = "provider_rejected"



@dataclass(frozen=True)
class LiveCredentialReference:
    provider: str
    alias: str
    source_type: str
    account_fingerprint: str
    loaded: bool
    verified_at_utc: datetime | None
    permission_summary: tuple[str, ...]
    reference_id: UUID | None = None

    def __post_init__(self) -> None:
        for name in ("provider", "alias", "source_type", "account_fingerprint"):
            object.__setattr__(self, name, _text(getattr(self, name), name))
        if self.verified_at_utc is not None:
            _utc(self.verified_at_utc, "verified_at_utc")
        summary = tuple(sorted({_text(value, "permission_summary") for value in self.permission_summary}))
        object.__setattr__(self, "permission_summary", summary)
        expected = live_uuid("credential-reference", {"provider": self.provider, "alias": self.alias, "source": self.source_type, "account": self.account_fingerprint})
        if self.reference_id is not None and self.reference_id != expected:
            raise ValueError("credential reference identity mismatch")
        object.__setattr__(self, "reference_id", expected)

    @property
    def record_hash(self) -> str:
        return sha256_payload({name: getattr(self, name) for name in self.__dataclass_fields__ if name != "reference_id"})


@dataclass(frozen=True)
class LiveAccountSnapshot:
    live_run_id: UUID
    account_fingerprint: str
    fetched_at_utc: datetime
    venue_time_at_utc: datetime
    balances: Mapping[str, Mapping[str, Decimal]]
    positions: Mapping[str, Mapping[str, object]]
    open_order_count: int
    total_equity: Decimal
    available_equity: Decimal
    reserved_equity: Decimal
    account_mode: str
    snapshot_id: UUID | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "account_fingerprint", _text(self.account_fingerprint, "account_fingerprint"))
        _utc(self.fetched_at_utc, "fetched_at_utc"); _utc(self.venue_time_at_utc, "venue_time_at_utc")
        if self.open_order_count < 0:
            raise ValueError("open_order_count cannot be negative")
        for name in ("total_equity", "available_equity", "reserved_equity"):
            _decimal(getattr(self, name), name, nonnegative=True)
        if self.available_equity + self.reserved_equity > self.total_equity:
            raise ValueError("available plus reserved equity exceeds total equity")
        object.__setattr__(self, "account_mode", _text(self.account_mode, "account_mode").lower())
        object.__setattr__(self, "balances", _map(self.balances)); object.__setattr__(self, "positions", _map(self.positions))
        expected = live_uuid("account-snapshot", {"run": self.live_run_id, "account": self.account_fingerprint, "fetched": self.fetched_at_utc, "payload": self.record_hash})
        if self.snapshot_id is not None and self.snapshot_id != expected:
            raise ValueError("account snapshot identity mismatch")
        object.__setattr__(self, "snapshot_id", expected)

    @property
    def record_hash(self) -> str:
        return sha256_payload({"run": self.live_run_id, "account": self.account_fingerprint, "fetched": self.fetched_at_utc, "venue": self.venue_time_at_utc, "balances": dict(self.balances), "positions": dict(self.positions), "open_orders": self.open_order_count, "total": self.total_equity, "available": self.available_equity, "reserved": self.reserved_equity, "mode": self.account_mode})


@dataclass(frozen=True)
class LivePreflightCheck:
    check_name: str
    passed: bool
    required: bool
    evaluated_at_utc: datetime
    explanation: str
    evidence_hash: str
    source_timestamp_utc: datetime | None = None
    check_id: UUID | None = None
    source_ids: tuple[UUID, ...] = ()
    source_hashes: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "check_name", _text(self.check_name, "check_name")); object.__setattr__(self, "explanation", _text(self.explanation, "explanation"))
        _utc(self.evaluated_at_utc, "evaluated_at_utc"); _hash(self.evidence_hash, "evidence_hash")
        if self.source_timestamp_utc is not None:
            _utc(self.source_timestamp_utc, "source_timestamp_utc")
        object.__setattr__(self, "source_ids", tuple(self.source_ids))
        object.__setattr__(self, "source_hashes", tuple(self.source_hashes))
        if len(self.source_ids) != len(self.source_hashes):
            raise ValueError("preflight check source IDs and hashes must align")
        for digest in self.source_hashes:
            _hash(digest, "source_hash")

        expected = live_uuid("preflight-check", {"name": self.check_name, "at": self.evaluated_at_utc, "evidence": self.evidence_hash, "sources": self.source_ids})
        if self.check_id is not None and self.check_id != expected:
            raise ValueError("preflight check identity mismatch")
        object.__setattr__(self, "check_id", expected)

    @property
    def record_hash(self) -> str:
        return sha256_payload({name: getattr(self, name) for name in self.__dataclass_fields__ if name != "check_id"})


@dataclass(frozen=True)
class LivePreflightReport:
    live_run_id: UUID
    configuration_hash: str
    implementation_hash: str
    repository_commit_sha: str
    endpoint_catalog_hash: str
    credential_reference_hash: str
    account_snapshot_hash: str
    evaluated_at_utc: datetime
    checks: tuple[LivePreflightCheck, ...]
    blockers: tuple[str, ...]
    warnings: tuple[str, ...]
    status: LivePreflightStatus
    report_id: UUID | None = None
    purpose: LivePreflightPurpose = LivePreflightPurpose.RUN_START

    def __post_init__(self) -> None:
        for name in ("configuration_hash", "implementation_hash", "endpoint_catalog_hash", "credential_reference_hash", "account_snapshot_hash"):
            _hash(getattr(self, name), name)
        object.__setattr__(self, "repository_commit_sha", _text(self.repository_commit_sha, "repository_commit_sha"))
        _utc(self.evaluated_at_utc, "evaluated_at_utc")
        object.__setattr__(self, "status", LivePreflightStatus(self.status)); object.__setattr__(self, "checks", tuple(self.checks))
        object.__setattr__(self, "purpose", LivePreflightPurpose(self.purpose))
        expected_blockers = tuple(check.check_name for check in self.checks if check.required and not check.passed)
        if tuple(self.blockers) != expected_blockers:
            raise ValueError("preflight blockers do not match required failed checks")
        expected_status = (
            LivePreflightStatus.PASSED_FOR_RESET
            if self.purpose is LivePreflightPurpose.KILL_RESET and not self.blockers
            else LivePreflightStatus.PASSED if not self.blockers else LivePreflightStatus.BLOCKED
        )
        if self.status is not expected_status:
            raise ValueError("preflight status does not match blockers")
        expected = live_uuid("preflight-report", {"run": self.live_run_id, "purpose": self.purpose, "configuration": self.configuration_hash, "checks": tuple(check.record_hash for check in self.checks)})
        if self.report_id is not None and self.report_id != expected:
            raise ValueError("preflight report identity mismatch")
        object.__setattr__(self, "report_id", expected)

    @property
    def record_hash(self) -> str:
        return sha256_payload({"run": self.live_run_id, "purpose": self.purpose, "configuration": self.configuration_hash, "implementation": self.implementation_hash, "commit": self.repository_commit_sha, "catalog": self.endpoint_catalog_hash, "credential": self.credential_reference_hash, "account": self.account_snapshot_hash, "at": self.evaluated_at_utc, "checks": tuple(check.record_hash for check in self.checks), "blockers": self.blockers, "warnings": self.warnings, "status": self.status})


@dataclass(frozen=True)
class LiveApproval:
    live_run_id: UUID
    configuration_hash: str
    account_fingerprint: str
    provider: str
    environment: str
    allowed_instruments: tuple[str, ...]
    maximum_total_approved_notional: Decimal
    created_at_utc: datetime
    expires_at_utc: datetime
    manifest_hash: str
    repository_commit_sha: str
    nonce: str
    approving_actor: str
    confirmation_challenge_hash: str
    preflight_report_id: UUID
    approval_id: UUID | None = None

    def __post_init__(self) -> None:
        _hash(self.configuration_hash, "configuration_hash"); _hash(self.manifest_hash, "manifest_hash"); _hash(self.confirmation_challenge_hash, "confirmation_challenge_hash")
        for name in ("account_fingerprint", "provider", "environment", "repository_commit_sha", "nonce", "approving_actor"):
            object.__setattr__(self, name, _text(getattr(self, name), name))
        object.__setattr__(self, "allowed_instruments", tuple(sorted({_text(value, "allowed_instruments") for value in self.allowed_instruments})))
        _decimal(self.maximum_total_approved_notional, "maximum_total_approved_notional", positive=True)
        _utc(self.created_at_utc, "created_at_utc"); _utc(self.expires_at_utc, "expires_at_utc")
        if self.expires_at_utc <= self.created_at_utc:
            raise ValueError("approval expiry must follow creation")
        expected = live_uuid("approval", {"run": self.live_run_id, "configuration": self.configuration_hash, "manifest": self.manifest_hash, "nonce": self.nonce})
        if self.approval_id is not None and self.approval_id != expected:
            raise ValueError("approval identity mismatch")
        object.__setattr__(self, "approval_id", expected)

    @property
    def record_hash(self) -> str:
        return sha256_payload({name: getattr(self, name) for name in self.__dataclass_fields__ if name != "approval_id"})


@dataclass(frozen=True)
class LiveRunManifest:
    live_run_id: UUID
    provider: str
    environment: str
    account_fingerprint: str
    configuration_hash: str
    implementation_hash: str
    repository_commit_sha: str
    endpoint_catalog_hash: str
    credential_reference_hash: str
    preflight_report_id: UUID
    approval_id: UUID
    initial_account_snapshot_id: UUID
    initial_account_snapshot_hash: str
    allowed_instruments: tuple[str, ...]
    risk_limits: Mapping[str, object]
    dry_run: bool
    production_write_enabled: bool
    expected_maximum_duration_seconds: int
    kill_switch_policy: Mapping[str, object]
    parent_evidence_ids: tuple[UUID, ...]
    manifest_hash: str
    manifest_id: UUID | None = None

    def __post_init__(self) -> None:
        for name in ("configuration_hash", "implementation_hash", "endpoint_catalog_hash", "credential_reference_hash", "initial_account_snapshot_hash", "manifest_hash"):
            _hash(getattr(self, name), name)
        if not self.dry_run or self.production_write_enabled:
            raise ValueError("Phase 8A manifests must be dry-run with writes disabled")
        if self.expected_maximum_duration_seconds <= 0:
            raise ValueError("manifest duration must be positive")
        object.__setattr__(self, "risk_limits", _map(self.risk_limits)); object.__setattr__(self, "kill_switch_policy", _map(self.kill_switch_policy))
        expected = live_uuid("manifest", {"run": self.live_run_id, "manifest_hash": self.manifest_hash, "approval": self.approval_id})
        if self.manifest_id is not None and self.manifest_id != expected:
            raise ValueError("manifest identity mismatch")
        object.__setattr__(self, "manifest_id", expected)

    @property
    def record_hash(self) -> str:
        payload = {name: getattr(self, name) for name in self.__dataclass_fields__ if name != "manifest_id"}
        payload["risk_limits"] = dict(self.risk_limits)
        payload["kill_switch_policy"] = dict(self.kill_switch_policy)
        return sha256_payload(payload)


@dataclass(frozen=True)
class LiveOrderIntent:
    live_run_id: UUID
    manifest_id: UUID
    series_identity: SeriesIdentity
    side: OrderSide
    quantity: Decimal
    reference_price: Decimal
    limit_price: Decimal
    created_at_utc: datetime
    market_evidence_id: UUID
    market_evidence_hash: str
    instrument_metadata_hash: str
    account_snapshot_hash: str
    reconciliation_hash: str
    instrument_metadata_source_id: UUID | None = None
    order_type: OrderType = OrderType.LIMIT
    time_in_force: TimeInForce = TimeInForce.GTC
    accounting_mode: AccountingMode = AccountingMode.SPOT
    order_intent_id: UUID | None = None
    client_order_id: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "side", OrderSide(self.side)); object.__setattr__(self, "order_type", OrderType(self.order_type)); object.__setattr__(self, "time_in_force", TimeInForce(self.time_in_force)); object.__setattr__(self, "accounting_mode", AccountingMode(self.accounting_mode))
        if self.order_type is not OrderType.LIMIT or self.accounting_mode is not AccountingMode.SPOT or self.time_in_force is not TimeInForce.GTC:
            raise ValueError("Phase 8A accepts only SPOT limit GTC intents")
        _decimal(self.quantity, "quantity", positive=True); _decimal(self.reference_price, "reference_price", positive=True); _decimal(self.limit_price, "limit_price", positive=True)
        _utc(self.created_at_utc, "created_at_utc")
        for name in ("market_evidence_hash", "instrument_metadata_hash", "account_snapshot_hash", "reconciliation_hash"):
            _hash(getattr(self, name), name)
        payload = {"run": self.live_run_id, "manifest": self.manifest_id, "series": self.series_identity.series_identity_sha256, "side": self.side, "quantity": self.quantity, "limit": self.limit_price, "evidence": self.market_evidence_hash, "metadata_source": self.instrument_metadata_source_id, "metadata_hash": self.instrument_metadata_hash}
        expected = live_uuid("order-intent", payload)
        if self.order_intent_id is not None and self.order_intent_id != expected:
            raise ValueError("live order intent identity mismatch")
        object.__setattr__(self, "order_intent_id", expected)
        client = f"sew{expected.hex[:29]}"
        if self.client_order_id is not None and self.client_order_id != client:
            raise ValueError("client order identity mismatch")
        object.__setattr__(self, "client_order_id", client)

    @property
    def economic_hash(self) -> str:
        return sha256_payload({"series": self.series_identity.series_identity_sha256, "side": self.side, "quantity": self.quantity, "limit": self.limit_price, "type": self.order_type, "tif": self.time_in_force})

    @property
    def record_hash(self) -> str:
        payload = {
            name: getattr(self, name)
            for name in self.__dataclass_fields__
            if name not in {"order_intent_id", "client_order_id", "series_identity"}
        }
        payload["series_identity_sha256"] = self.series_identity.series_identity_sha256
        return sha256_payload(payload)


@dataclass(frozen=True)
class LiveRiskDecision:
    order_intent_id: UUID
    accepted: bool
    reasons: tuple[str, ...]
    market_evidence_price: Decimal
    risk_reference_price: Decimal
    worst_case_order_price: Decimal
    risk_notional: Decimal
    reservation_notional: Decimal
    price_deviation_bps: Decimal
    price_source_hash: str
    calculator_version: str
    evaluated_at_utc: datetime
    decision_id: UUID | None = None

    def __post_init__(self) -> None:
        for name in ("market_evidence_price", "risk_reference_price", "worst_case_order_price", "risk_notional", "reservation_notional"):
            _decimal(getattr(self, name), name, positive=True)
        _decimal(self.price_deviation_bps, "price_deviation_bps", nonnegative=True); _hash(self.price_source_hash, "price_source_hash"); _utc(self.evaluated_at_utc, "evaluated_at_utc")
        if self.accepted == bool(self.reasons):
            raise ValueError("risk decision acceptance and reasons disagree")
        expected = live_uuid("risk-decision", {"intent": self.order_intent_id, "risk": self.risk_notional, "reasons": self.reasons})
        if self.decision_id is not None and self.decision_id != expected:
            raise ValueError("risk decision identity mismatch")
        object.__setattr__(self, "decision_id", expected)

    @property
    def record_hash(self) -> str:
        return sha256_payload({name: getattr(self, name) for name in self.__dataclass_fields__ if name != "decision_id"})


@dataclass(frozen=True)
class LiveTransportPlan:
    live_run_id: UUID
    order_intent_id: UUID
    operation: str
    method: str
    path: str
    request_body: Mapping[str, object]
    provider_request_hash: str
    created_at_utc: datetime
    external_write_suppressed: bool = True
    plan_id: UUID | None = None

    def __post_init__(self) -> None:
        for name in ("operation", "method", "path"):
            object.__setattr__(self, name, _text(getattr(self, name), name))
        object.__setattr__(self, "request_body", _map(self.request_body)); _hash(self.provider_request_hash, "provider_request_hash"); _utc(self.created_at_utc, "created_at_utc")
        if not self.external_write_suppressed:
            raise ValueError("Phase 8A transport plans must suppress writes")
        expected = live_uuid("transport-plan", {"run": self.live_run_id, "intent": self.order_intent_id, "request": self.provider_request_hash})
        if self.plan_id is not None and self.plan_id != expected:
            raise ValueError("transport plan identity mismatch")
        object.__setattr__(self, "plan_id", expected)

    @property
    def record_hash(self) -> str:
        return sha256_payload({name: getattr(self, name) for name in self.__dataclass_fields__ if name != "plan_id"})


@dataclass(frozen=True)
class LiveObservationBundle:
    live_run_id: UUID
    client_order_id: str
    queried_order: Mapping[str, object] | None
    recent_orders: tuple[Mapping[str, object], ...]
    open_orders: tuple[Mapping[str, object], ...]
    fills: tuple[Mapping[str, object], ...]
    account_observation: Mapping[str, object]
    queried_at_utc: datetime
    outcome: LiveRecoveryOutcome
    bundle_id: UUID | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "client_order_id", _text(self.client_order_id, "client_order_id"))
        _utc(self.queried_at_utc, "queried_at_utc")
        if self.queried_order is not None:
            object.__setattr__(self, "queried_order", _map(self.queried_order))
        for name in ("recent_orders", "open_orders", "fills"):
            object.__setattr__(self, name, tuple(_map(row) for row in getattr(self, name)))
        object.__setattr__(self, "account_observation", _map(self.account_observation))
        object.__setattr__(self, "outcome", LiveRecoveryOutcome(self.outcome))
        expected = live_uuid("observation-bundle", {"run": self.live_run_id, "client": self.client_order_id, "at": self.queried_at_utc, "hash": self.record_hash})
        if self.bundle_id is not None and self.bundle_id != expected:
            raise ValueError("observation bundle identity mismatch")
        object.__setattr__(self, "bundle_id", expected)

    @property
    def record_hash(self) -> str:
        return sha256_payload({"run": self.live_run_id, "client": self.client_order_id, "queried_order": None if self.queried_order is None else dict(self.queried_order), "recent": tuple(dict(row) for row in self.recent_orders), "open": tuple(dict(row) for row in self.open_orders), "fills": tuple(dict(row) for row in self.fills), "account": dict(self.account_observation), "at": self.queried_at_utc, "outcome": self.outcome})


@dataclass(frozen=True)
class LiveReconciliation:
    live_run_id: UUID
    evaluated_at_utc: datetime
    status: LiveReconciliationStatus
    input_bundle_hash: str
    differences: tuple[Mapping[str, object], ...]
    reconciliation_id: UUID | None = None
    local_projection_as_of_utc: datetime | None = None
    venue_observation_as_of_utc: datetime | None = None
    query_started_at_utc: datetime | None = None
    query_completed_at_utc: datetime | None = None
    response_bundle_id: UUID | None = None
    local_sequence: int | None = None
    venue_sequence: int | None = None
    producer_classification: str = "legacy_untrusted"

    def __post_init__(self) -> None:
        _utc(self.evaluated_at_utc, "evaluated_at_utc"); object.__setattr__(self, "status", LiveReconciliationStatus(self.status)); _hash(self.input_bundle_hash, "input_bundle_hash")
        expected = live_uuid("reconciliation", {"run": self.live_run_id, "at": self.evaluated_at_utc, "input": self.input_bundle_hash})
        timestamps = (
            self.local_projection_as_of_utc, self.venue_observation_as_of_utc,
            self.query_started_at_utc, self.query_completed_at_utc,
        )
        if any(value is not None for value in timestamps):
            if any(value is None for value in timestamps):
                raise ValueError("operational reconciliation timestamps must be complete")
            for name in (
                "local_projection_as_of_utc", "venue_observation_as_of_utc",
                "query_started_at_utc", "query_completed_at_utc",
            ):
                _utc(getattr(self, name), name)
            if self.query_completed_at_utc < self.query_started_at_utc:
                raise ValueError("reconciliation query completion precedes its start")
        if self.producer_classification == "operational_collector":
            if self.response_bundle_id is None or self.local_sequence is None or self.venue_sequence is None:
                raise ValueError("operational reconciliation provenance is incomplete")
            if min(self.local_sequence, self.venue_sequence) < 0:
                raise ValueError("reconciliation sequences cannot be negative")
        if self.reconciliation_id is not None and self.reconciliation_id != expected:
            raise ValueError("reconciliation identity mismatch")
        object.__setattr__(self, "reconciliation_id", expected)

    @property
    def record_hash(self) -> str:
        return sha256_payload({
            "run": self.live_run_id, "at": self.evaluated_at_utc, "status": self.status,
            "input": self.input_bundle_hash, "differences": self.differences,
            "local_as_of": self.local_projection_as_of_utc,
            "venue_as_of": self.venue_observation_as_of_utc,
            "query_started": self.query_started_at_utc,
            "query_completed": self.query_completed_at_utc,
            "response_bundle": self.response_bundle_id,
            "local_sequence": self.local_sequence, "venue_sequence": self.venue_sequence,
            "producer_classification": self.producer_classification,
        })


@dataclass(frozen=True)
class LiveKillSwitch:
    live_run_id: UUID
    state: LiveKillState
    reason: str | None
    updated_at_utc: datetime
    evidence_hash: str
    requires_fresh_preflight: bool = False
    requires_new_approval: bool = False
    kill_switch_id: UUID | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "state", LiveKillState(self.state)); _utc(self.updated_at_utc, "updated_at_utc"); _hash(self.evidence_hash, "evidence_hash")
        if self.state not in {LiveKillState.ARMED, LiveKillState.RESET} and not self.reason:
            raise ValueError("triggered kill state requires a reason")
        if self.state is LiveKillState.RESET and not (self.requires_fresh_preflight and self.requires_new_approval):
            raise ValueError("kill reset requires fresh preflight and approval")
        expected = live_uuid("kill-switch", {"run": self.live_run_id})
        if self.kill_switch_id is not None and self.kill_switch_id != expected:
            raise ValueError("kill switch identity mismatch")
        object.__setattr__(self, "kill_switch_id", expected)

    @property
    def record_hash(self) -> str:
        return sha256_payload({name: getattr(self, name) for name in self.__dataclass_fields__ if name != "kill_switch_id"})


@dataclass(frozen=True)
class LiveRiskSummary:
    live_run_id: UUID
    summary_type: str
    generated_at_utc: datetime
    public_payload: Mapping[str, object]
    evidence_ids: tuple[UUID, ...]
    summary_id: UUID | None = None

    def __post_init__(self) -> None:
        if self.summary_type not in {"pre_run", "post_run"}:
            raise ValueError("summary_type must be pre_run or post_run")
        _utc(self.generated_at_utc, "generated_at_utc"); object.__setattr__(self, "public_payload", _map(self.public_payload))
        expected = live_uuid("risk-summary", {"run": self.live_run_id, "type": self.summary_type, "at": self.generated_at_utc, "payload": dict(self.public_payload)})
        if self.summary_id is not None and self.summary_id != expected:
            raise ValueError("summary identity mismatch")
        object.__setattr__(self, "summary_id", expected)

    @property
    def record_hash(self) -> str:
        return sha256_payload({"run": self.live_run_id, "type": self.summary_type, "at": self.generated_at_utc, "payload": dict(self.public_payload), "evidence": self.evidence_ids})


__all__ = [name for name in globals() if name.startswith("Live") or name == "live_uuid"]
