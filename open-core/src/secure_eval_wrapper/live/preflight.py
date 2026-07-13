"""PostgreSQL-bound, fail-closed Phase 8A preflight evaluation."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Mapping

from secure_eval_wrapper.data_collection.hashing import sha256_payload
from secure_eval_wrapper.data_collection.time_utils import require_utc_datetime

from .endpoints import endpoint_catalog_hash
from .gates import common_ci_indicators
from .models import LiveAccountSnapshot, LiveCredentialReference, LivePreflightCheck, LivePreflightReport, LivePreflightStatus, LiveReconciliationStatus


@dataclass(frozen=True)
class LivePreflightEvidence:
    repository_commit_matches: bool
    implementation_hash_matches: bool
    clean_migration_catalog: bool
    postgresql_available: bool
    audit_tables_writable: bool
    credential_reference_present: bool
    credential_secret_absent_from_persistence: bool
    permissions_verified: bool
    permissions_safe: bool
    account_fingerprint_matches: bool
    subaccount_matches: bool
    account_exists: bool
    account_spot_cash: bool
    margin_disabled: bool
    borrowing_disabled: bool
    disallowed_positions_absent: bool
    existing_open_orders_identified: bool
    balances_complete: bool
    venue_time_at_utc: datetime | None
    market_evidence_at_utc: datetime | None
    validated_market_evidence: bool
    price_currency_matches: bool
    instrument_metadata_verified: bool
    tick_size_verified: bool
    lot_size_verified: bool
    minimum_order_size_verified: bool
    order_notional_bounds_verified: bool
    reconciliation_status: LiveReconciliationStatus
    reconciliation_at_utc: datetime | None
    kill_switch_armed: bool
    fake_transport: bool
    evidence_hashes: Mapping[str, str]
    warnings: tuple[str, ...] = ()


class LivePreflightEngine:
    def evaluate(self, *, live_run_id, configuration, account_snapshot: LiveAccountSnapshot, credential_reference: LiveCredentialReference, evidence: LivePreflightEvidence, evaluated_at_utc: datetime, implementation_hash: str, repository_commit_sha: str) -> LivePreflightReport:
        now = require_utc_datetime(evaluated_at_utc, field_name="evaluated_at_utc")
        checks: list[LivePreflightCheck] = []

        def add(name: str, passed: bool, explanation: str, *, source_time: datetime | None = None, required: bool = True) -> None:
            supplied = evidence.evidence_hashes.get(name)
            digest = supplied if isinstance(supplied, str) and len(supplied) == 64 else sha256_payload({"check": name, "passed": passed, "source": source_time})
            checks.append(LivePreflightCheck(name, bool(passed), required, now, explanation, digest, source_time))

        add("requested_mode_live_write_disabled", configuration.environment == "production" and configuration.dry_run and not configuration.production_write_enabled, "live target is explicit while Phase 8A writes remain disabled")
        add("provider_environment_pair", (configuration.provider, configuration.environment) == ("okx", "production"), "only OKX production is catalogued")
        add("endpoint_catalog", configuration.endpoint_catalog_hash == endpoint_catalog_hash(), "endpoint catalog hash must match code")
        add("repository_commit_identity", evidence.repository_commit_matches, "repository commit must match manifest input")
        add("configuration_hash", bool(configuration.configuration_hash), "configuration hash must be present")
        add("implementation_hash", evidence.implementation_hash_matches and implementation_hash == configuration.provider_implementation_hash, "provider implementation hash must match")
        add("clean_migration_catalog", evidence.clean_migration_catalog, "migration 0022 and immutable history must be verified")
        add("postgresql_authority", evidence.postgresql_available, "PostgreSQL authority must be available")
        add("audit_tables_writable", evidence.audit_tables_writable, "live audit tables must be writable")
        add("credential_reference", evidence.credential_reference_present, "public-safe credential reference must exist")
        add("credential_secret_not_persisted", evidence.credential_secret_absent_from_persistence, "secret material must not be persisted")
        add("credential_permissions", evidence.permissions_verified and evidence.permissions_safe, "permissions must be verified and limited to read/Spot trade")
        add("account_fingerprint", evidence.account_fingerprint_matches and credential_reference.account_fingerprint == configuration.account_fingerprint == account_snapshot.account_fingerprint, "account fingerprints must match")
        add("subaccount_fingerprint", evidence.subaccount_matches, "configured subaccount must match")
        add("account_exists", evidence.account_exists, "account must exist")
        add("account_mode_spot_cash", evidence.account_spot_cash and account_snapshot.account_mode == "spot_cash", "account must be Spot/cash")
        add("no_margin", evidence.margin_disabled, "margin must be disabled")
        add("no_borrowing", evidence.borrowing_disabled, "borrowing must be disabled")
        add("no_disallowed_positions", evidence.disallowed_positions_absent, "derivative, short, and margin positions are forbidden")
        add("existing_open_orders", evidence.existing_open_orders_identified, "existing open orders must be enumerated")
        add("balances_available_reserved", evidence.balances_complete, "total, available, and reserved balances are required", source_time=account_snapshot.fetched_at_utc)
        clock_ok = evidence.venue_time_at_utc is not None and abs((now - evidence.venue_time_at_utc).total_seconds()) <= configuration.maximum_clock_skew_seconds
        add("venue_clock_skew", clock_ok, "venue clock skew must be bounded", source_time=evidence.venue_time_at_utc)
        market_fresh = evidence.market_evidence_at_utc is not None and 0 <= (now - evidence.market_evidence_at_utc).total_seconds() <= configuration.market_data_freshness_seconds
        add("validated_market_data", evidence.validated_market_evidence and market_fresh, "validated authoritative market evidence must be fresh", source_time=evidence.market_evidence_at_utc)
        add("price_currency_identity", evidence.price_currency_matches, "price currency must match settlement currency")
        add("instrument_metadata", evidence.instrument_metadata_verified, "instrument metadata must be verified")
        add("tick_size", evidence.tick_size_verified, "tick size must be verified")
        add("lot_size", evidence.lot_size_verified, "lot size must be verified")
        add("minimum_order_size", evidence.minimum_order_size_verified, "minimum order size must be verified")
        add("order_notional_bounds", evidence.order_notional_bounds_verified, "provider and configured notional bounds must be verified")
        reconciliation_fresh = evidence.reconciliation_at_utc is not None and 0 <= (now - evidence.reconciliation_at_utc).total_seconds() <= configuration.reconciliation_freshness_seconds
        add("reconciliation", evidence.reconciliation_status is LiveReconciliationStatus.RECONCILED and reconciliation_fresh, "reconciliation must be recent and successful", source_time=evidence.reconciliation_at_utc)
        add("kill_switch", evidence.kill_switch_armed, "PostgreSQL kill switch must be armed")
        account_fresh = 0 <= (now - account_snapshot.fetched_at_utc).total_seconds() <= configuration.account_snapshot_freshness_seconds
        add("account_snapshot_fresh", account_fresh, "account snapshot must be fresh", source_time=account_snapshot.fetched_at_utc)
        add("dry_run", configuration.dry_run and configuration.read_only_preflight, "Phase 8A must remain dry-run/read-only")
        ci = bool(common_ci_indicators())
        add("ci_hard_prohibition", (not ci) or evidence.fake_transport, "CI requires fake transport and prohibits credential/network mutation")
        add("live_writes_disabled", not configuration.production_write_enabled, "production writes must remain disabled")
        blockers = tuple(check.check_name for check in checks if check.required and not check.passed)
        status = LivePreflightStatus.PASSED if not blockers else LivePreflightStatus.BLOCKED
        return LivePreflightReport(live_run_id, configuration.configuration_hash, implementation_hash, repository_commit_sha, configuration.endpoint_catalog_hash, credential_reference.record_hash, account_snapshot.record_hash, now, tuple(checks), blockers, tuple(evidence.warnings), status)


__all__ = ["LivePreflightEvidence", "LivePreflightEngine"]
