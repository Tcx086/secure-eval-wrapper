"""Public-safe, non-routable contracts for Phase 8B shadow assurance."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import Enum
from types import MappingProxyType
from typing import Mapping
from uuid import NAMESPACE_URL, UUID, uuid5

from secure_eval_wrapper.data_collection.hashing import sha256_payload
from secure_eval_wrapper.data_collection.time_utils import require_utc_datetime
from secure_eval_wrapper.signals.models import StandardizedSignal


SHADOW_RUNTIME_VERSION = "phase8b-shadow-v2"


def shadow_uuid(kind: str, payload: object) -> UUID:
    return uuid5(
        NAMESPACE_URL,
        f"secure-eval-wrapper:phase8b-shadow:{kind}:{sha256_payload(payload)}",
    )


def _text(value: str, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be non-empty")
    return value.strip()


def _digest(value: str, name: str, *, length: int = 64) -> str:
    if (
        not isinstance(value, str)
        or len(value) != length
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise ValueError(f"{name} must be a lowercase {length}-character digest")
    return value


def _decimal(
    value: Decimal,
    name: str,
    *,
    positive: bool = False,
    nonnegative: bool = False,
) -> Decimal:
    if not isinstance(value, Decimal) or not value.is_finite():
        raise ValueError(f"{name} must be a finite Decimal")
    if positive and value <= 0:
        raise ValueError(f"{name} must be positive")
    if nonnegative and value < 0:
        raise ValueError(f"{name} must be non-negative")
    return value


def _freeze(value):
    if isinstance(value, Mapping):
        return MappingProxyType({str(key): _freeze(item) for key, item in value.items()})
    if isinstance(value, (list, tuple)):
        return tuple(_freeze(item) for item in value)
    return value


class ShadowEvidenceClassification(str, Enum):
    FIXTURE = "fixture"
    PUBLIC_NETWORK = "public_network"
    UNAVAILABLE = "unavailable"


@dataclass(frozen=True)
class ShadowMarketSnapshot:
    """Normalized public-only market evidence; it has no account-shaped fields."""

    provider: str
    instrument: str
    instrument_type: str
    bid: Decimal
    ask: Decimal
    last_price: Decimal
    public_timestamp_utc: datetime
    normalized_at_utc: datetime
    source_identity: str
    public_response_hash: str
    classification: ShadowEvidenceClassification
    instrument_status: str = "live"
    settlement_asset: str = "USDT"
    tick_size: Decimal = Decimal("0.1")
    lot_size: Decimal = Decimal("0.0001")
    minimum_quantity: Decimal = Decimal("0.0001")
    maximum_quantity: Decimal = Decimal("0.1")
    network_read_count: int = 0

    def __post_init__(self) -> None:
        object.__setattr__(self, "provider", _text(self.provider, "provider").lower())
        object.__setattr__(self, "instrument", _text(self.instrument, "instrument").upper())
        object.__setattr__(self, "instrument_type", _text(self.instrument_type, "instrument_type").lower())
        object.__setattr__(self, "instrument_status", _text(self.instrument_status, "instrument_status").lower())
        object.__setattr__(self, "settlement_asset", _text(self.settlement_asset, "settlement_asset").upper())
        object.__setattr__(self, "source_identity", _text(self.source_identity, "source_identity"))
        object.__setattr__(self, "classification", ShadowEvidenceClassification(self.classification))
        for name in (
            "bid",
            "ask",
            "last_price",
            "tick_size",
            "lot_size",
            "minimum_quantity",
            "maximum_quantity",
        ):
            _decimal(getattr(self, name), name, positive=True)
        if self.bid > self.ask:
            raise ValueError("public market bid cannot exceed ask")
        if self.minimum_quantity > self.maximum_quantity:
            raise ValueError("public instrument quantity bounds are inverted")
        require_utc_datetime(self.public_timestamp_utc, field_name="public_timestamp_utc")
        require_utc_datetime(self.normalized_at_utc, field_name="normalized_at_utc")
        _digest(self.public_response_hash, "public_response_hash")
        if isinstance(self.network_read_count, bool) or self.network_read_count < 0:
            raise ValueError("network_read_count must be non-negative")
        if self.classification is ShadowEvidenceClassification.FIXTURE and self.network_read_count:
            raise ValueError("fixture market evidence cannot report network reads")
        if self.classification is ShadowEvidenceClassification.PUBLIC_NETWORK and not self.network_read_count:
            raise ValueError("public-network evidence must report at least one read")

    @property
    def freshness_seconds(self) -> Decimal:
        return Decimal(str((self.normalized_at_utc - self.public_timestamp_utc).total_seconds()))

    @property
    def snapshot_hash(self) -> str:
        return sha256_payload({name: getattr(self, name) for name in self.__dataclass_fields__})


@dataclass(frozen=True)
class SyntheticBalance:
    asset: str
    total: Decimal
    available: Decimal
    reserved: Decimal

    def __post_init__(self) -> None:
        object.__setattr__(self, "asset", _text(self.asset, "asset").upper())
        for name in ("total", "available", "reserved"):
            _decimal(getattr(self, name), name, nonnegative=True)
        if self.available + self.reserved > self.total:
            raise ValueError("synthetic balance available plus reserved exceeds total")

    @property
    def record_hash(self) -> str:
        return sha256_payload({name: getattr(self, name) for name in self.__dataclass_fields__})


@dataclass(frozen=True)
class SyntheticPosition:
    instrument: str
    instrument_type: str
    quantity: Decimal
    notional: Decimal
    settlement_asset: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "instrument", _text(self.instrument, "instrument").upper())
        object.__setattr__(self, "instrument_type", _text(self.instrument_type, "instrument_type").lower())
        object.__setattr__(self, "settlement_asset", _text(self.settlement_asset, "settlement_asset").upper())
        _decimal(self.quantity, "quantity")
        _decimal(self.notional, "notional")

    @property
    def record_hash(self) -> str:
        return sha256_payload({name: getattr(self, name) for name in self.__dataclass_fields__})


@dataclass(frozen=True)
class SyntheticPendingOrder:
    instrument: str
    side: str
    quantity: Decimal
    reserved_notional: Decimal

    def __post_init__(self) -> None:
        object.__setattr__(self, "instrument", _text(self.instrument, "instrument").upper())
        object.__setattr__(self, "side", _text(self.side, "side").lower())
        if self.side not in {"buy", "sell"}:
            raise ValueError("synthetic pending order side must be buy or sell")
        _decimal(self.quantity, "quantity", positive=True)
        _decimal(self.reserved_notional, "reserved_notional", nonnegative=True)

    @property
    def record_hash(self) -> str:
        return sha256_payload({name: getattr(self, name) for name in self.__dataclass_fields__})


@dataclass(frozen=True)
class SyntheticAccountSnapshot:
    """Deterministic account state that cannot contain a real provider identity."""

    scenario_id: str
    synthetic_account_id: str
    observed_at_utc: datetime
    balances: tuple[SyntheticBalance, ...]
    positions: tuple[SyntheticPosition, ...]
    pending_orders: tuple[SyntheticPendingOrder, ...]
    reserved_notional: Decimal
    risk_limits: Mapping[str, object]
    permissions: tuple[str, ...]
    account_classification: str
    daily_realized_pnl: Decimal = Decimal(0)
    current_equity: Decimal = Decimal("10000")
    high_watermark_equity: Decimal = Decimal("10000")
    kill_switch_active: bool = False
    synthetic_account: bool = True

    def __post_init__(self) -> None:
        object.__setattr__(self, "scenario_id", _text(self.scenario_id, "scenario_id"))
        object.__setattr__(self, "synthetic_account_id", _digest(self.synthetic_account_id, "synthetic_account_id"))
        object.__setattr__(self, "account_classification", _text(self.account_classification, "account_classification").lower())
        require_utc_datetime(self.observed_at_utc, field_name="observed_at_utc")
        object.__setattr__(self, "balances", tuple(self.balances))
        object.__setattr__(self, "positions", tuple(self.positions))
        object.__setattr__(self, "pending_orders", tuple(self.pending_orders))
        if len({balance.asset for balance in self.balances}) != len(self.balances):
            raise ValueError("duplicate synthetic balances are forbidden")
        position_keys = tuple((position.instrument, position.instrument_type) for position in self.positions)
        if len(set(position_keys)) != len(position_keys):
            raise ValueError("duplicate synthetic positions are forbidden")
        _decimal(self.reserved_notional, "reserved_notional", nonnegative=True)
        _decimal(self.daily_realized_pnl, "daily_realized_pnl")
        _decimal(self.current_equity, "current_equity", nonnegative=True)
        _decimal(self.high_watermark_equity, "high_watermark_equity", nonnegative=True)
        if self.high_watermark_equity < self.current_equity:
            raise ValueError("synthetic high-watermark equity cannot trail current equity")
        permissions = tuple(sorted({_text(value, "permissions").lower() for value in self.permissions}))
        object.__setattr__(self, "permissions", permissions)
        object.__setattr__(self, "risk_limits", _freeze(self.risk_limits))
        if self.synthetic_account is not True:
            raise PermissionError("shadow account state must be explicitly synthetic")

    @property
    def balance_map(self) -> Mapping[str, SyntheticBalance]:
        return MappingProxyType({balance.asset: balance for balance in self.balances})

    @property
    def snapshot_hash(self) -> str:
        return sha256_payload({
            "scenario_id": self.scenario_id,
            "synthetic_account_id": self.synthetic_account_id,
            "observed_at_utc": self.observed_at_utc,
            "balances": tuple(balance.record_hash for balance in self.balances),
            "positions": tuple(position.record_hash for position in self.positions),
            "pending_orders": tuple(order.record_hash for order in self.pending_orders),
            "reserved_notional": self.reserved_notional,
            "risk_limits": dict(self.risk_limits),
            "permissions": self.permissions,
            "account_classification": self.account_classification,
            "daily_realized_pnl": self.daily_realized_pnl,
            "current_equity": self.current_equity,
            "high_watermark_equity": self.high_watermark_equity,
            "kill_switch_active": self.kill_switch_active,
            "synthetic_account": self.synthetic_account,
        })


@dataclass(frozen=True)
class ShadowDecisionRequest:
    shadow_run_id: UUID
    scenario_id: str
    market_snapshot: ShadowMarketSnapshot
    account_snapshot: SyntheticAccountSnapshot
    signal: StandardizedSignal
    quantity: Decimal
    limit_price: Decimal
    order_type: str
    decision_at_utc: datetime
    repository_commit_sha: str
    parent_input_hash: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "scenario_id", _text(self.scenario_id, "scenario_id"))
        object.__setattr__(self, "order_type", _text(self.order_type, "order_type").lower())
        _decimal(self.quantity, "quantity", positive=True)
        _decimal(self.limit_price, "limit_price", positive=True)
        require_utc_datetime(self.decision_at_utc, field_name="decision_at_utc")
        _digest(self.repository_commit_sha, "repository_commit_sha", length=40)
        if self.parent_input_hash is not None:
            _digest(self.parent_input_hash, "parent_input_hash")
        if self.market_snapshot.instrument != self.signal.series_identity.provider_instrument_id:
            raise ValueError("shadow request market and signal instruments differ")
        if self.account_snapshot.scenario_id != self.scenario_id:
            raise ValueError("shadow request scenario and account snapshot differ")

    @property
    def input_hash(self) -> str:
        return sha256_payload({
            "scenario_id": self.scenario_id,
            "market_snapshot_hash": self.market_snapshot.snapshot_hash,
            "account_snapshot_hash": self.account_snapshot.snapshot_hash,
            "signal_hash": self.signal.record_sha256,
            "quantity": self.quantity,
            "limit_price": self.limit_price,
            "order_type": self.order_type,
            "decision_at_utc": self.decision_at_utc,
            "repository_commit_sha": self.repository_commit_sha,
            "parent_input_hash": self.parent_input_hash,
        })


@dataclass(frozen=True)
class ShadowOrderIntent:
    """Description-only intent.  It intentionally has no submit/cancel interface."""

    instrument: str
    side: str
    order_type: str
    quantity: Decimal
    limit_price: Decimal
    expected_notional: Decimal
    risk_accepted: bool
    blockers: tuple[str, ...]
    approval_result: str
    manifest_hash: str
    live_intent_hash: str
    would_submit_classification: str
    shadow_only: bool = True
    production_write_enabled: bool = False
    submit_reachable: bool = False
    cancel_reachable: bool = False
    transport_called: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(self, "instrument", _text(self.instrument, "instrument").upper())
        object.__setattr__(self, "side", _text(self.side, "side").lower())
        object.__setattr__(self, "order_type", _text(self.order_type, "order_type").lower())
        object.__setattr__(self, "approval_result", _text(self.approval_result, "approval_result"))
        object.__setattr__(self, "would_submit_classification", _text(self.would_submit_classification, "would_submit_classification"))
        _decimal(self.quantity, "quantity", positive=True)
        _decimal(self.limit_price, "limit_price", positive=True)
        _decimal(self.expected_notional, "expected_notional", positive=True)
        _digest(self.manifest_hash, "manifest_hash")
        _digest(self.live_intent_hash, "live_intent_hash")
        object.__setattr__(self, "blockers", tuple(self.blockers))
        if (
            self.shadow_only is not True
            or self.production_write_enabled is not False
            or self.submit_reachable is not False
            or self.cancel_reachable is not False
            or self.transport_called is not False
        ):
            raise PermissionError("shadow order intent safety flags are immutable")
        if self.risk_accepted == bool(self.blockers):
            raise ValueError("shadow order intent risk result and blockers disagree")

    @property
    def record_hash(self) -> str:
        return sha256_payload({name: getattr(self, name) for name in self.__dataclass_fields__})


@dataclass(frozen=True)
class ShadowSafetyFacts:
    network_read_count: int
    network_write_count: int = 0
    production_transport_call_count: int = 0
    authenticated_endpoint_call_count: int = 0
    credential_read_count: int = 0
    production_write_count: int = 0
    production_write_enabled: bool = False
    production_submit_reachable: bool = False
    production_cancel_reachable: bool = False
    real_account_data_used: bool = False
    operator_database_accessed: bool = False
    authenticated_proof_executed: bool = False

    def __post_init__(self) -> None:
        for name in (
            "network_read_count",
            "network_write_count",
            "production_transport_call_count",
            "authenticated_endpoint_call_count",
            "credential_read_count",
            "production_write_count",
        ):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError(f"{name} must be a non-negative integer")
        for name in (
            "production_write_enabled",
            "production_submit_reachable",
            "production_cancel_reachable",
            "real_account_data_used",
            "operator_database_accessed",
            "authenticated_proof_executed",
        ):
            if getattr(self, name) is not False:
                raise PermissionError(f"{name} must be the boolean false")
        if any((
            self.network_write_count,
            self.production_transport_call_count,
            self.authenticated_endpoint_call_count,
            self.credential_read_count,
            self.production_write_count,
            self.production_write_enabled,
            self.production_submit_reachable,
            self.production_cancel_reachable,
            self.real_account_data_used,
            self.operator_database_accessed,
            self.authenticated_proof_executed,
        )):
            raise PermissionError("shadow safety facts cannot report production/account authority")

    @property
    def record_hash(self) -> str:
        return sha256_payload({name: getattr(self, name) for name in self.__dataclass_fields__})


_PUBLIC_ENDPOINT_IDENTITIES = (
    "GET /api/v5/public/instruments",
    "GET /api/v5/market/history-trades",
)


@dataclass(frozen=True)
class ShadowDataProvenance:
    """Durable source authority included in every shadow decision hash chain."""

    classification: str
    endpoint_identities: tuple[str, ...]
    network_read_count: int
    response_source_hashes: tuple[str, ...]
    source_instance_id: str | None
    payload_hash: str | None
    failure_kind: str | None
    provenance_hash: str | None = None
    record_hash: str | None = None

    def __post_init__(self) -> None:
        classification = _text(self.classification, "classification")
        object.__setattr__(self, "classification", classification)
        endpoints = tuple(self.endpoint_identities)
        hashes = tuple(self.response_source_hashes)
        object.__setattr__(self, "endpoint_identities", endpoints)
        object.__setattr__(self, "response_source_hashes", hashes)
        if (
            isinstance(self.network_read_count, bool)
            or not isinstance(self.network_read_count, int)
            or self.network_read_count not in (0, 1, 2)
        ):
            raise ValueError("shadow provenance network_read_count must be 0, 1, or 2")
        for digest in hashes:
            _digest(digest, "response_source_hash")
        token_core = {
            "classification": classification,
            "endpoint_identities": endpoints,
            "network_read_count": self.network_read_count,
            "response_source_hashes": hashes,
            "source_instance_id": self.source_instance_id,
            "payload_hash": self.payload_hash,
            "failure_kind": self.failure_kind,
        }
        if classification == "fixture":
            if any((
                endpoints,
                self.network_read_count,
                hashes,
                self.source_instance_id,
                self.payload_hash,
                self.failure_kind,
                self.provenance_hash,
            )):
                raise PermissionError("fixture provenance cannot carry public authority")
            expected_provenance_hash = None
        elif classification in ("public_network", "unavailable"):
            _digest(self.source_instance_id, "source_instance_id")
            _digest(self.payload_hash, "payload_hash")
            if endpoints != _PUBLIC_ENDPOINT_IDENTITIES[:self.network_read_count]:
                raise PermissionError("public endpoints must be the actual ordered send prefix")
            if len(hashes) > self.network_read_count:
                raise PermissionError("public response hashes exceed completed sends")
            if classification == "public_network":
                if (
                    self.network_read_count != 2
                    or len(hashes) != 2
                    or self.failure_kind is not None
                ):
                    raise PermissionError("public success provenance is incomplete")
            elif not isinstance(self.failure_kind, str) or not self.failure_kind:
                raise PermissionError("unavailable public provenance requires a failure kind")
            expected_provenance_hash = sha256_payload(token_core)
            if (
                self.provenance_hash is not None
                and self.provenance_hash != expected_provenance_hash
            ):
                raise ValueError("public provenance hash mismatch")
            object.__setattr__(self, "provenance_hash", expected_provenance_hash)
        else:
            raise PermissionError("unsupported shadow provenance classification")
        record_core = {**token_core, "provenance_hash": expected_provenance_hash}
        expected_record_hash = sha256_payload(record_core)
        if self.record_hash is not None and self.record_hash != expected_record_hash:
            raise ValueError("shadow provenance record hash mismatch")
        object.__setattr__(self, "record_hash", expected_record_hash)

    @classmethod
    def fixture(cls) -> "ShadowDataProvenance":
        return cls("fixture", (), 0, (), None, None, None)

    def public_payload(self) -> Mapping[str, object]:
        return MappingProxyType({
            name: getattr(self, name) for name in self.__dataclass_fields__
        })


@dataclass(frozen=True)
class ShadowDecisionRecord:
    shadow_run_id: UUID
    scenario_id: str
    input_hash: str
    market_snapshot_hash: str | None
    synthetic_account_snapshot_hash: str | None
    configuration_hash: str
    preflight_hash: str
    approval_hash: str | None
    manifest_hash: str | None
    live_risk_decision_hash: str | None
    accepted: bool
    blockers: tuple[str, ...]
    shadow_intent: ShadowOrderIntent | None
    safety_facts: ShadowSafetyFacts
    data_provenance: ShadowDataProvenance
    repository_commit_sha: str
    parent_input_hash: str | None = None
    decision_hash: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "scenario_id", _text(self.scenario_id, "scenario_id"))
        for name in ("input_hash", "configuration_hash", "preflight_hash"):
            _digest(getattr(self, name), name)
        for name in (
            "market_snapshot_hash",
            "synthetic_account_snapshot_hash",
            "approval_hash",
            "manifest_hash",
            "live_risk_decision_hash",
            "parent_input_hash",
        ):
            value = getattr(self, name)
            if value is not None:
                _digest(value, name)
        _digest(self.repository_commit_sha, "repository_commit_sha", length=40)
        blockers = tuple(dict.fromkeys(self.blockers))
        object.__setattr__(self, "blockers", blockers)
        if self.accepted == bool(blockers):
            raise ValueError("shadow decision acceptance and blockers disagree")
        if self.accepted and self.shadow_intent is None:
            raise ValueError("accepted shadow decision requires a description-only intent")
        if self.safety_facts.network_read_count != self.data_provenance.network_read_count:
            raise ValueError("shadow safety facts and durable provenance read counts disagree")
        expected = sha256_payload({
            "scenario_id": self.scenario_id,
            "input_hash": self.input_hash,
            "market_snapshot_hash": self.market_snapshot_hash,
            "synthetic_account_snapshot_hash": self.synthetic_account_snapshot_hash,
            "configuration_hash": self.configuration_hash,
            "preflight_hash": self.preflight_hash,
            "approval_hash": self.approval_hash,
            "manifest_hash": self.manifest_hash,
            "live_risk_decision_hash": self.live_risk_decision_hash,
            "accepted": self.accepted,
            "blockers": blockers,
            "shadow_intent_hash": None if self.shadow_intent is None else self.shadow_intent.record_hash,
            "safety_facts_hash": self.safety_facts.record_hash,
            "data_provenance_hash": self.data_provenance.record_hash,
            "repository_commit_sha": self.repository_commit_sha,
            "parent_input_hash": self.parent_input_hash,
        })
        if self.decision_hash is not None and self.decision_hash != expected:
            raise ValueError("shadow decision hash mismatch")
        object.__setattr__(self, "decision_hash", expected)


@dataclass(frozen=True)
class ShadowRunSummary:
    shadow_run_id: UUID
    scenario_id: str
    input_hash: str
    decision_hash: str
    manifest_hash: str | None
    accepted: bool
    blockers: tuple[str, ...]
    shadow_intent_count: int
    persistence_result: str
    replayed: bool
    safety_facts: ShadowSafetyFacts
    data_provenance: ShadowDataProvenance
    hypothetical: bool = True
    summary_hash: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "scenario_id", _text(self.scenario_id, "scenario_id"))
        object.__setattr__(self, "persistence_result", _text(self.persistence_result, "persistence_result"))
        for name in ("input_hash", "decision_hash"):
            _digest(getattr(self, name), name)
        if self.manifest_hash is not None:
            _digest(self.manifest_hash, "manifest_hash")
        if self.safety_facts.network_read_count != self.data_provenance.network_read_count:
            raise ValueError("summary safety facts and durable provenance read counts disagree")
        if self.shadow_intent_count not in (0, 1):
            raise ValueError("one shadow scenario can produce at most one intent")
        if self.hypothetical is not True:
            raise PermissionError("shadow summaries must remain hypothetical")
        expected = sha256_payload({
            "shadow_run_id": self.shadow_run_id,
            "scenario_id": self.scenario_id,
            "input_hash": self.input_hash,
            "decision_hash": self.decision_hash,
            "manifest_hash": self.manifest_hash,
            "accepted": self.accepted,
            "blockers": self.blockers,
            "shadow_intent_count": self.shadow_intent_count,
            "persistence_result": self.persistence_result,
            "replayed": self.replayed,
            "safety_facts_hash": self.safety_facts.record_hash,
            "data_provenance_hash": self.data_provenance.record_hash,
            "hypothetical": self.hypothetical,
        })
        if self.summary_hash is not None and self.summary_hash != expected:
            raise ValueError("shadow summary hash mismatch")
        object.__setattr__(self, "summary_hash", expected)

    @property
    def public_source_hashes(self) -> tuple[str, ...]:
        return self.data_provenance.response_source_hashes

    @property
    def public_provenance_hash(self) -> str | None:
        return self.data_provenance.provenance_hash

    def public_payload(self) -> Mapping[str, object]:
        return MappingProxyType({
            "operation": "phase8b_shadow_run",
            "status": "accepted" if self.accepted else "blocked",
            "shadow_run_id": str(self.shadow_run_id),
            "scenario_id": self.scenario_id,
            "input_hash": self.input_hash,
            "decision_hash": self.decision_hash,
            "manifest_hash": self.manifest_hash,
            "blockers": self.blockers,
            "shadow_intent_count": self.shadow_intent_count,
            "persistence_result": self.persistence_result,
            "replayed": self.replayed,
            "hypothetical": True,
            "network_read_count": self.safety_facts.network_read_count,
            "network_write_count": self.safety_facts.network_write_count,
            "production_transport_call_count": self.safety_facts.production_transport_call_count,
            "authenticated_endpoint_call_count": self.safety_facts.authenticated_endpoint_call_count,
            "credential_read_count": self.safety_facts.credential_read_count,
            "production_write_count": self.safety_facts.production_write_count,
            "production_submit_reachable": self.safety_facts.production_submit_reachable,
            "production_cancel_reachable": self.safety_facts.production_cancel_reachable,
            "real_account_data_used": self.safety_facts.real_account_data_used,
            "operator_database_accessed": self.safety_facts.operator_database_accessed,
            "authenticated_proof_executed": self.safety_facts.authenticated_proof_executed,
            "source_classification": self.data_provenance.classification,
            "endpoint_identities": self.data_provenance.endpoint_identities,
            "public_source_hashes": self.data_provenance.response_source_hashes,
            "public_provenance_hash": self.data_provenance.provenance_hash,
            "public_provenance_payload_hash": self.data_provenance.payload_hash,
            "public_source_instance_id": self.data_provenance.source_instance_id,
            "failure_kind": self.data_provenance.failure_kind,
            "data_provenance_hash": self.data_provenance.record_hash,
            "summary_hash": self.summary_hash,
        })


__all__ = [
    "SHADOW_RUNTIME_VERSION",
    "ShadowDataProvenance",
    "ShadowDecisionRecord",
    "ShadowDecisionRequest",
    "ShadowEvidenceClassification",
    "ShadowMarketSnapshot",
    "ShadowOrderIntent",
    "ShadowRunSummary",
    "ShadowSafetyFacts",
    "SyntheticAccountSnapshot",
    "SyntheticBalance",
    "SyntheticPendingOrder",
    "SyntheticPosition",
    "shadow_uuid",
]
