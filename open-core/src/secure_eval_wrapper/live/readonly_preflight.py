"""Explicit authenticated read-only OKX preflight with public-safe PostgreSQL proof."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from types import MappingProxyType
from typing import Mapping
from uuid import UUID

from secure_eval_wrapper.data_collection.hashing import sha256_payload
from secure_eval_wrapper.data_collection.time_utils import require_utc_datetime

from .collector_evidence import VerifiedOkxReadObservationBundle, expected_preflight_request_paths
from .configuration import GuardedLiveConfiguration
from .credentials import LiveCredentialProvider
from .endpoints import endpoint_catalog_hash
from .gates import common_ci_indicators
from .identity import (
    RuntimeRepositoryIdentity,
    resolve_runtime_repository_identity,
    validate_git_commit_sha,
    validate_okx_account_fingerprint,
)
from .models import LiveCredentialReference, live_uuid
from .venues.okx_live import OkxProductionSpotAdapter, UrllibReadOnlyTransport

_REQUIRED_ENDPOINTS = (
    "account_config",
    "balances",
    "instrument_metadata",
    "pending_orders",
    "positions",
    "venue_time",
)



def _boolean(value: object, name: str) -> bool:
    if type(value) is not bool:
        raise TypeError(f"{name} must be a JSON boolean")
    return value


def _hash(value: str, name: str) -> str:
    if not isinstance(value, str) or len(value) != 64 or any(ch not in "0123456789abcdef" for ch in value):
        raise ValueError(f"{name} must be lowercase SHA-256")
    return value


def _iso(value: datetime) -> str:
    return require_utc_datetime(value, field_name="proof timestamp").isoformat().replace("+00:00", "Z")


def _at(value: object) -> datetime:
    if isinstance(value, datetime):
        return require_utc_datetime(value, field_name="proof timestamp")
    return require_utc_datetime(
        datetime.fromisoformat(str(value).replace("Z", "+00:00")),
        field_name="proof timestamp",
    )


def guarded_configuration_from_json(payload: Mapping[str, object]) -> GuardedLiveConfiguration:
    values = dict(payload)
    for name in (
        "maximum_order_notional",
        "maximum_position_notional",
        "maximum_gross_exposure",
        "maximum_net_exposure",
        "maximum_daily_submitted_notional",
        "maximum_daily_realized_loss",
        "maximum_drawdown",
        "maximum_fee_bps",
        "maximum_adverse_slippage_bps",
        "maximum_reference_price_deviation_bps",
    ):
        values[name] = Decimal(str(values[name]))
    for name in (
        "allowed_instruments",
        "allowed_instrument_types",
        "allowed_settlement_assets",
        "allowed_order_types",
        "credential_source_policy",
    ):
        values[name] = tuple(values[name])
    values.setdefault("maximum_transport_failures", 3)
    return GuardedLiveConfiguration(**values)


@dataclass(frozen=True)
class AuthenticatedReadOnlyProof:
    proof_id: UUID
    proof_session_id: UUID
    response_bundle_id: UUID
    configuration_hash: str
    provider_implementation_hash: str
    endpoint_catalog_hash: str
    credential_reference_id: UUID
    expected_reviewed_sha: str
    observed_repository_sha: str
    repository_identity_source: str
    account_fingerprint: str
    account_classification: str
    credential_source: str
    provider_permissions: tuple[str, ...]
    normalized_permissions: tuple[str, ...]
    queried_paths: tuple[str, ...]
    endpoint_response_hashes: Mapping[str, str]
    query_started_at_utc: datetime
    query_completed_at_utc: datetime
    venue_time_at_utc: datetime
    clock_skew_milliseconds: int
    balance_currencies: tuple[str, ...]
    balance_currency_count: int
    position_count: int
    open_order_count: int
    instrument_id: str
    instrument_state: str
    instrument_metadata_response_hash: str
    network_read_count: int
    network_reads_occurred: bool
    network_writes_occurred: bool
    production_write_status: str
    preflight_mode: str
    evidence_classification: str
    status: str
    blockers: tuple[str, ...]
    warnings: tuple[str, ...]
    private_evidence_storage: str
    record_hash: str | None = None

    def __post_init__(self) -> None:
        for name in (
            "configuration_hash",
            "provider_implementation_hash",
            "endpoint_catalog_hash",
            "instrument_metadata_response_hash",
        ):
            _hash(getattr(self, name), name)
        validate_git_commit_sha(self.expected_reviewed_sha, field_name="expected_reviewed_sha")
        validate_git_commit_sha(self.observed_repository_sha, field_name="observed_repository_sha")
        validate_okx_account_fingerprint(self.account_fingerprint)
        if self.observed_repository_sha != self.expected_reviewed_sha:
            raise PermissionError("observed repository SHA does not match the expected reviewed SHA")
        if self.provider_permissions != ("read_only",) or self.normalized_permissions != ("read",):
            raise PermissionError("proof requires the exact OKX permission set read_only")
        if self.account_classification not in {"main_account", "subaccount"}:
            raise ValueError("account classification is invalid")
        if self.credential_source not in {"environment", "injected_local", "os_credential_store"}:
            raise ValueError("credential source is invalid")
        if not self.repository_identity_source:
            raise ValueError("repository identity source is required")
        paths = tuple(self.queried_paths)
        if paths != expected_preflight_request_paths(self.instrument_id):
            raise ValueError("proof must contain the exact six queried OKX paths")
        hashes = dict(self.endpoint_response_hashes)
        if tuple(sorted(hashes)) != _REQUIRED_ENDPOINTS:
            raise ValueError("proof response-hash endpoint matrix is not exact")
        for value in hashes.values():
            _hash(value, "endpoint response hash")
        object.__setattr__(self, "queried_paths", paths)
        object.__setattr__(self, "endpoint_response_hashes", MappingProxyType(hashes))
        for name in ("query_started_at_utc", "query_completed_at_utc", "venue_time_at_utc"):
            require_utc_datetime(getattr(self, name), field_name=name)
        if self.query_completed_at_utc < self.query_started_at_utc:
            raise ValueError("proof query completion precedes its start")
        expected_skew = int(abs((self.venue_time_at_utc - self.query_completed_at_utc).total_seconds()) * 1000)
        if self.clock_skew_milliseconds != expected_skew:
            raise ValueError("proof clock skew is not derived from its timestamps")
        if self.balance_currencies != tuple(sorted(set(self.balance_currencies))):
            raise ValueError("balance currencies must be unique and sorted")
        if self.balance_currency_count != len(self.balance_currencies):
            raise ValueError("balance currency count does not match its names")
        if min(self.balance_currency_count, self.position_count, self.open_order_count) < 0:
            raise ValueError("proof aggregate counts cannot be negative")
        if not self.instrument_id or self.instrument_state != "live":
            raise PermissionError("proof requires a live Spot instrument")
        if self.network_read_count != 6 or not self.network_reads_occurred:
            raise ValueError("proof requires exactly six network reads")
        if self.network_writes_occurred or self.production_write_status != "disabled":
            raise PermissionError("proof cannot include production writes")
        if self.preflight_mode != "AUTHENTICATED READ-ONLY":
            raise ValueError("authorization mode is not exact")
        expected_status = "fixture_passed" if self.evidence_classification == "fixture" else "passed"
        if self.evidence_classification not in {"operational_collector", "fixture"} or self.status != expected_status:
            raise ValueError("proof status and evidence classification disagree")
        if self.blockers or self.private_evidence_storage != "postgresql":
            raise ValueError("a completed proof cannot contain blockers or non-PostgreSQL evidence")
        object.__setattr__(self, "blockers", tuple(self.blockers))
        object.__setattr__(self, "warnings", tuple(self.warnings))
        expected_id = live_uuid(
            "authenticated-readonly-proof",
            {"session": self.proof_session_id, "bundle": self.response_bundle_id, "configuration": self.configuration_hash},
        )
        if self.proof_id != expected_id:
            raise ValueError("authenticated read-only proof identity mismatch")
        calculated = sha256_payload(self._core_payload())
        if self.record_hash is not None and self.record_hash != calculated:
            raise ValueError("authenticated read-only proof record hash mismatch")
        object.__setattr__(self, "record_hash", calculated)

    def _core_payload(self) -> dict[str, object]:
        return {
            "proof_id": str(self.proof_id),
            "proof_session_id": str(self.proof_session_id),
            "response_bundle_id": str(self.response_bundle_id),
            "configuration_hash": self.configuration_hash,
            "provider_implementation_hash": self.provider_implementation_hash,
            "endpoint_catalog_hash": self.endpoint_catalog_hash,
            "credential_reference_id": str(self.credential_reference_id),
            "expected_reviewed_sha": self.expected_reviewed_sha,
            "observed_repository_sha": self.observed_repository_sha,
            "repository_identity_source": self.repository_identity_source,
            "account_fingerprint": self.account_fingerprint,
            "account_classification": self.account_classification,
            "credential_source": self.credential_source,
            "provider_permissions": self.provider_permissions,
            "normalized_permissions": self.normalized_permissions,
            "queried_paths": self.queried_paths,
            "endpoint_response_hashes": dict(self.endpoint_response_hashes),
            "query_started_at_utc": _iso(self.query_started_at_utc),
            "query_completed_at_utc": _iso(self.query_completed_at_utc),
            "venue_time_at_utc": _iso(self.venue_time_at_utc),
            "clock_skew_milliseconds": self.clock_skew_milliseconds,
            "balance_currencies": self.balance_currencies,
            "balance_currency_count": self.balance_currency_count,
            "position_count": self.position_count,
            "open_order_count": self.open_order_count,
            "instrument_id": self.instrument_id,
            "instrument_state": self.instrument_state,
            "instrument_metadata_response_hash": self.instrument_metadata_response_hash,
            "network_read_count": self.network_read_count,
            "network_reads_occurred": self.network_reads_occurred,
            "network_writes_occurred": self.network_writes_occurred,
            "production_write_status": self.production_write_status,
            "preflight_mode": self.preflight_mode,
            "evidence_classification": self.evidence_classification,
            "status": self.status,
            "blockers": self.blockers,
            "warnings": self.warnings,
            "private_evidence_storage": self.private_evidence_storage,
        }

    def public_payload(self) -> dict[str, object]:
        return {**self._core_payload(), "record_hash": self.record_hash}

    @classmethod
    def from_public_payload(cls, payload: Mapping[str, object]) -> "AuthenticatedReadOnlyProof":
        value = dict(payload)
        return cls(
            proof_id=UUID(str(value["proof_id"])),
            proof_session_id=UUID(str(value["proof_session_id"])),
            response_bundle_id=UUID(str(value["response_bundle_id"])),
            configuration_hash=str(value["configuration_hash"]),
            provider_implementation_hash=str(value["provider_implementation_hash"]),
            endpoint_catalog_hash=str(value["endpoint_catalog_hash"]),
            credential_reference_id=UUID(str(value["credential_reference_id"])),
            expected_reviewed_sha=str(value["expected_reviewed_sha"]),
            observed_repository_sha=str(value["observed_repository_sha"]),
            repository_identity_source=str(value["repository_identity_source"]),
            account_fingerprint=str(value["account_fingerprint"]),
            account_classification=str(value["account_classification"]),
            credential_source=str(value["credential_source"]),
            provider_permissions=tuple(value["provider_permissions"]),
            normalized_permissions=tuple(value["normalized_permissions"]),
            queried_paths=tuple(value["queried_paths"]),
            endpoint_response_hashes=dict(value["endpoint_response_hashes"]),
            query_started_at_utc=_at(value["query_started_at_utc"]),
            query_completed_at_utc=_at(value["query_completed_at_utc"]),
            venue_time_at_utc=_at(value["venue_time_at_utc"]),
            clock_skew_milliseconds=int(value["clock_skew_milliseconds"]),
            balance_currencies=tuple(value["balance_currencies"]),
            balance_currency_count=int(value["balance_currency_count"]),
            position_count=int(value["position_count"]),
            open_order_count=int(value["open_order_count"]),
            instrument_id=str(value["instrument_id"]),
            instrument_state=str(value["instrument_state"]),
            instrument_metadata_response_hash=str(value["instrument_metadata_response_hash"]),
            network_read_count=int(value["network_read_count"]),
            network_reads_occurred=_boolean(value["network_reads_occurred"], "network_reads_occurred"),
            network_writes_occurred=_boolean(value["network_writes_occurred"], "network_writes_occurred"),
            production_write_status=str(value["production_write_status"]),
            preflight_mode=str(value["preflight_mode"]),
            evidence_classification=str(value["evidence_classification"]),
            status=str(value["status"]),
            blockers=tuple(value["blockers"]),
            warnings=tuple(value["warnings"]),
            private_evidence_storage=str(value["private_evidence_storage"]),
            record_hash=str(value["record_hash"]),
        )


def build_authenticated_readonly_proof(
    *,
    proof_session_id: UUID,
    bundle: VerifiedOkxReadObservationBundle,
    configuration: GuardedLiveConfiguration,
    credential_reference: LiveCredentialReference,
    expected_reviewed_sha: str,
    repository_identity: RuntimeRepositoryIdentity,
    instrument: str,
    network_read_count: int,
    network_write_count: int,
) -> AuthenticatedReadOnlyProof:
    if not bundle.complete or bundle.purpose != "preflight" or bundle.live_run_id != proof_session_id:
        raise PermissionError("authenticated read-only proof requires a complete exact preflight bundle")
    if bundle.account_fingerprint != configuration.account_fingerprint:
        raise PermissionError("OKX response account does not match the configured fingerprint")
    by_kind = {item.endpoint_kind: item for item in bundle.envelopes}
    account = by_kind["account_config"].normalized_payload
    balances = by_kind["balances"].normalized_payload
    positions = by_kind["positions"].normalized_payload
    orders = by_kind["pending_orders"].normalized_payload
    instruments = by_kind["instrument_metadata"].normalized_payload
    venue = by_kind["venue_time"].normalized_payload
    if not isinstance(account, Mapping) or tuple(account.get("provider_permissions", ())) != ("read_only",):
        raise PermissionError("OKX account-config response does not prove read-only permission")
    if tuple(account.get("normalized_permissions", ())) != ("read",):
        raise PermissionError("OKX account-config permission normalization is not exact")
    if not isinstance(balances, Mapping) or not isinstance(instruments, tuple) or len(instruments) != 1:
        raise ValueError("OKX balance or instrument response shape is not exact")
    instrument_row = instruments[0]
    if instrument_row.get("instrument") != instrument or instrument_row.get("instrument_state") != "live":
        raise PermissionError("requested Spot instrument is not live in the exact response")
    details = tuple(balances.get("details", ()))
    currencies = tuple(sorted({str(row["ccy"]) for row in details}))
    if len(currencies) != len(details):
        raise ValueError("OKX balance response contains duplicate currencies")
    query_started = min(item.query_started_at_utc for item in bundle.envelopes)
    query_completed = max(item.query_completed_at_utc for item in bundle.envelopes)
    venue_time = venue["venue_time_at_utc"]
    skew = int(abs((venue_time - query_completed).total_seconds()) * 1000)
    if skew > configuration.maximum_clock_skew_seconds * 1000:
        raise PermissionError("venue clock skew exceeds the configured maximum")
    classification = "fixture" if bundle.transport_is_fake else "operational_collector"
    status = "fixture_passed" if bundle.transport_is_fake else "passed"
    proof_id = live_uuid(
        "authenticated-readonly-proof",
        {"session": proof_session_id, "bundle": bundle.bundle_id, "configuration": configuration.configuration_hash},
    )
    return AuthenticatedReadOnlyProof(
        proof_id=proof_id,
        proof_session_id=proof_session_id,
        response_bundle_id=bundle.bundle_id,
        configuration_hash=configuration.configuration_hash,
        provider_implementation_hash=configuration.provider_implementation_hash,
        endpoint_catalog_hash=configuration.endpoint_catalog_hash,
        credential_reference_id=credential_reference.reference_id,
        expected_reviewed_sha=expected_reviewed_sha,
        observed_repository_sha=repository_identity.observed_commit_sha,
        repository_identity_source=repository_identity.identity_source,
        account_fingerprint=bundle.account_fingerprint,
        account_classification="subaccount" if account.get("is_subaccount") else "main_account",
        credential_source=credential_reference.source_type,
        provider_permissions=tuple(account["provider_permissions"]),
        normalized_permissions=tuple(account["normalized_permissions"]),
        queried_paths=tuple(by_kind[kind].request_path for kind in _REQUIRED_ENDPOINTS),
        endpoint_response_hashes={kind: by_kind[kind].canonical_response_hash for kind in _REQUIRED_ENDPOINTS},
        query_started_at_utc=query_started,
        query_completed_at_utc=query_completed,
        venue_time_at_utc=venue_time,
        clock_skew_milliseconds=skew,
        balance_currencies=currencies,
        balance_currency_count=len(currencies),
        position_count=len(positions),
        open_order_count=len(orders),
        instrument_id=instrument,
        instrument_state=str(instrument_row["instrument_state"]),
        instrument_metadata_response_hash=by_kind["instrument_metadata"].canonical_response_hash,
        network_read_count=network_read_count,
        network_reads_occurred=network_read_count > 0,
        network_writes_occurred=network_write_count > 0,
        production_write_status="disabled",
        preflight_mode="AUTHENTICATED READ-ONLY",
        evidence_classification=classification,
        status=status,
        blockers=(),
        warnings=(),
        private_evidence_storage="postgresql",
    )


def run_authenticated_readonly_preflight(
    *,
    repository,
    proof_session_id: UUID,
    configuration_hash: str,
    expected_account_fingerprint: str,
    expected_reviewed_sha: str,
    instrument: str,
    credential_provider: LiveCredentialProvider,
    adapter_factory=None,
    identity_resolver=resolve_runtime_repository_identity,
) -> AuthenticatedReadOnlyProof:
    """Run only after every non-secret gate passes; persist raw evidence privately and proof publicly."""
    if common_ci_indicators():
        raise PermissionError("authenticated production network preflight is prohibited in CI")
    proof_session_id = UUID(str(proof_session_id))
    _hash(configuration_hash, "configuration_hash")
    expected_account_fingerprint = validate_okx_account_fingerprint(
        expected_account_fingerprint, field_name="expected_account_fingerprint"
    )
    expected_reviewed_sha = validate_git_commit_sha(expected_reviewed_sha, field_name="expected_reviewed_sha")
    if not isinstance(instrument, str) or not instrument or instrument != instrument.upper():
        raise ValueError("instrument must be an explicit uppercase OKX Spot instrument")
    if not repository.authenticated_readonly_storage_available():
        raise PermissionError("PostgreSQL Phase 8B authenticated read-only storage is unavailable")
    configuration = repository.load_guarded_live_configuration(configuration_hash)
    if configuration.account_fingerprint != expected_account_fingerprint:
        raise PermissionError("expected account fingerprint does not match PostgreSQL configuration")
    if instrument not in configuration.allowed_instruments:
        raise PermissionError("requested instrument is absent from the guarded configuration")
    if configuration.endpoint_catalog_hash != endpoint_catalog_hash():
        raise PermissionError("configured endpoint catalog hash is stale")
    if configuration.provider_implementation_hash != OkxProductionSpotAdapter.provider_implementation_hash:
        raise PermissionError("configured OKX adapter implementation hash is stale")
    if configuration.production_write_enabled or not configuration.read_only_preflight:
        raise PermissionError("configuration is not production-write-disabled read-only authority")
    reference = credential_provider.reference()
    if reference.account_fingerprint != expected_account_fingerprint:
        raise PermissionError("credential provider fingerprint does not match the explicit expectation")
    if reference.source_type not in configuration.credential_source_policy:
        raise PermissionError("credential source is absent from the guarded configuration policy")
    identity = identity_resolver()
    if identity.observed_commit_sha != expected_reviewed_sha:
        raise PermissionError("runtime repository SHA does not match the expected reviewed SHA")
    material = credential_provider.load(gates={
        "authenticated_read_only_preflight_requested": True,
        "read_only_preflight": True,
        "provider_selected": configuration.provider == "okx",
        "production_environment": configuration.environment == "production",
        "endpoint_catalog_valid": True,
        "configuration_valid": True,
        "production_writes_disabled": not configuration.production_write_enabled,
        "kill_switch_armed": True,
        "postgresql_available": True,
        "repository_identity_verified": True,
        "expected_account_fingerprint_present": True,
    })
    if adapter_factory is None:
        adapter = OkxProductionSpotAdapter(
            transport=UrllibReadOnlyTransport(), credential_material=material
        )
    else:
        adapter = adapter_factory(material)
    bundle = adapter.collect_read_observation_bundle(
        live_run_id=proof_session_id,
        purpose="preflight",
        instrument=instrument,
        expected_account_fingerprint=expected_account_fingerprint,
        expected_subaccount_fingerprint=configuration.subaccount_fingerprint,
    )
    if adapter.network_writes != 0:
        raise PermissionError("authenticated read-only adapter recorded a network write")
    verified_at = bundle.envelope("account_config").query_completed_at_utc
    credential_reference = LiveCredentialReference(
        reference.provider,
        f"phase8b-{reference.alias}-{proof_session_id}",
        reference.source_type,
        reference.account_fingerprint,
        True,
        verified_at,
        ("read",),
    )
    proof = build_authenticated_readonly_proof(
        proof_session_id=proof_session_id,
        bundle=bundle,
        configuration=configuration,
        credential_reference=credential_reference,
        expected_reviewed_sha=expected_reviewed_sha,
        repository_identity=identity,
        instrument=instrument,
        network_read_count=adapter.network_reads,
        network_write_count=adapter.network_writes,
    )
    repository.persist_authenticated_readonly_proof(
        proof=proof,
        bundle=bundle,
        credential_reference=credential_reference,
        configuration=configuration,
        created_at_utc=datetime.now(timezone.utc),
    )
    return proof


__all__ = [
    "AuthenticatedReadOnlyProof",
    "build_authenticated_readonly_proof",
    "guarded_configuration_from_json",
    "run_authenticated_readonly_preflight",
]