"""Evidence-producing, PostgreSQL-bound Phase 8A operational preflight."""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal, InvalidOperation
from uuid import uuid4

from secure_eval_wrapper.data_collection.hashing import sha256_payload
from secure_eval_wrapper.data_collection.time_utils import require_utc_datetime

from .authorities import (
    AuditRollbackProbeEvidence,
    FixtureOnlyPreflightEvidence,
    LiveEvidenceSource,
    MigrationCatalogEvidence,
    OperationalPreflightEvidence,
    PostgreSQLProbeEvidence,
)
from .credentials import validate_permission_summary
from .endpoints import endpoint_catalog_hash
from .gates import common_ci_indicators
from .models import (
    LiveAccountSnapshot,
    LiveCredentialReference,
    LivePreflightCheck,
    LivePreflightReport,
    LivePreflightStatus,
    LiveReconciliationStatus,
)


class OperationalPreflightError(PermissionError):
    pass


def _datetime(value: object) -> datetime | None:
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    return None


def _decimal(value: object) -> Decimal | None:
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None
    return parsed if parsed.is_finite() else None


class LivePreflightEngine:
    """Derive every operational verdict from exact typed sources."""

    def evaluate(
        self,
        *,
        live_run_id,
        configuration,
        account_snapshot: LiveAccountSnapshot,
        credential_reference: LiveCredentialReference,
        evidence: OperationalPreflightEvidence | FixtureOnlyPreflightEvidence,
        evaluated_at_utc: datetime,
        implementation_hash: str,
        repository_commit_sha: str,
        test_mode: bool = False,
    ) -> LivePreflightReport:
        now = require_utc_datetime(evaluated_at_utc, field_name="evaluated_at_utc")
        if isinstance(evidence, FixtureOnlyPreflightEvidence):
            if not test_mode or not evidence.fake_transport:
                raise OperationalPreflightError("fixture-only preflight evidence is rejected outside fake test mode")
            digest = sha256_payload({"fixture_only": dict(evidence.claims), "run": live_run_id})
            check = LivePreflightCheck(
                "fixture_only_evidence_rejected",
                False,
                True,
                now,
                "fixture-only Boolean evidence can never create an operational passed preflight",
                digest,
            )
            return LivePreflightReport(
                live_run_id, configuration.configuration_hash, implementation_hash, repository_commit_sha,
                configuration.endpoint_catalog_hash, credential_reference.record_hash, account_snapshot.record_hash,
                now, (check,), (check.check_name,), (), LivePreflightStatus.BLOCKED,
            )
        if not isinstance(evidence, OperationalPreflightEvidence):
            raise TypeError("operational preflight requires OperationalPreflightEvidence")
        if evidence.live_run_id != live_run_id:
            raise OperationalPreflightError("preflight evidence belongs to another run")
        if account_snapshot.live_run_id != live_run_id:
            raise OperationalPreflightError("account snapshot belongs to another run")

        source = {item.source_kind: item for item in evidence.sources}
        checks: list[LivePreflightCheck] = []

        def add(name: str, passed: bool, explanation: str, kinds: tuple[str, ...], source_time: datetime | None = None) -> None:
            exact = tuple(source[kind] for kind in kinds)
            ids = tuple(item.source_id for item in exact)
            hashes = tuple(item.source_hash for item in exact)
            digest = sha256_payload({"check": name, "sources": hashes, "derived_passed": bool(passed)})
            checks.append(LivePreflightCheck(name, bool(passed), True, now, explanation, digest, source_time, None, ids, hashes))

        repository = source["repository"].payload
        migrations = source["migration_catalog"].payload
        postgresql = source["postgresql_probe"].payload
        audit_probe = source["audit_rollback_probe"].payload
        credential_source = source["credential_reference"].payload
        permission_source = source["credential_permissions"].payload
        account_config = source["account_config"].payload
        account_fingerprint = source["account_fingerprint"].payload
        subaccount = source["subaccount"].payload
        account_mode = source["account_mode"].payload
        margin = source["margin_borrowing"].payload
        balances = source["balances"].payload
        positions = source["positions"].payload
        open_orders = source["open_orders"].payload
        venue_time = source["venue_time"].payload
        market = source["market_data"].payload
        instrument = source["instrument_metadata"].payload
        reconciliation = source["reconciliation"].payload
        kill = source["kill_switch"].payload

        actual_permissions: tuple[str, ...] = ()
        permission_error = None
        try:
            actual_permissions = validate_permission_summary(credential_reference.permission_summary)
        except PermissionError as exc:
            permission_error = str(exc)

        add("requested_mode_live_write_disabled", configuration.environment == "production" and configuration.dry_run and not configuration.production_write_enabled, "Phase 8A must be production-targeted dry-run with writes disabled", ("repository",))
        add("provider_environment_pair", (configuration.provider, configuration.environment) == ("okx", "production"), "only OKX production is catalogued", ("repository",))
        add("endpoint_catalog", configuration.endpoint_catalog_hash == endpoint_catalog_hash(), "endpoint catalog hash must match code", ("repository",))
        add("repository_commit_identity", repository.get("commit_sha") == repository_commit_sha == repository.get("expected_commit_sha"), "repository commit must match the requested authority", ("repository",))
        add("implementation_hash", repository.get("implementation_hash") == implementation_hash == configuration.provider_implementation_hash, "provider implementation hash must match", ("repository",))
        add("clean_migration_catalog", bool(migrations.get("catalog_clean")) and migrations.get("latest_migration") == "0023" and bool(migrations.get("immutable_0001_0022")), "migration 0023 and immutable history must be verified", ("migration_catalog",))
        add("postgresql_authority", bool(postgresql.get("available")) and bool(postgresql.get("transaction_probe")), "PostgreSQL availability and transaction probes must pass", ("postgresql_probe",))
        add("audit_tables_writable", bool(audit_probe.get("write_succeeded")) and bool(audit_probe.get("rollback_verified")), "audit-table write and rollback probes must pass", ("audit_rollback_probe",))
        add("credential_reference", str(credential_source.get("reference_id")) == str(credential_reference.reference_id) and credential_source.get("record_hash") == credential_reference.record_hash, "persisted credential identity and hash must match", ("credential_reference",))
        add("credential_material_not_persisted", credential_source.get("credential_material_present") is False, "credential material must not be persisted", ("credential_reference",))
        add("credential_permissions", permission_error is None and tuple(permission_source.get("permissions", ())) == actual_permissions and permission_source.get("credential_record_hash") == credential_reference.record_hash, "permissions are derived from the actual persisted credential reference and limited to read/Spot trade", ("credential_permissions", "credential_reference"))
        add("account_fingerprint", account_fingerprint.get("observed") == credential_reference.account_fingerprint == configuration.account_fingerprint == account_snapshot.account_fingerprint, "account fingerprints must match", ("account_fingerprint", "credential_reference"))
        add("subaccount_fingerprint", subaccount.get("observed") == configuration.subaccount_fingerprint, "configured subaccount must match", ("subaccount",))
        add("account_exists", account_config.get("account_exists") is True, "account config response must prove the account exists", ("account_config",))
        add("account_mode_spot_cash", account_mode.get("account_mode") == "spot_cash" == account_snapshot.account_mode, "account must be Spot/cash", ("account_mode", "account_config"))
        add("no_margin", margin.get("margin_enabled") is False and margin.get("leverage_enabled") is False, "margin and leverage must be disabled", ("margin_borrowing",))
        add("no_borrowing", margin.get("borrowing_enabled") is False, "borrowing must be disabled", ("margin_borrowing",))
        add("no_disallowed_positions", positions.get("derivative_count") == 0 and positions.get("short_count") == 0 and positions.get("margin_count") == 0, "derivative, short, and margin positions are forbidden", ("positions",))
        add("existing_open_orders", open_orders.get("enumerated") is True and int(open_orders.get("count", -1)) == account_snapshot.open_order_count, "existing open orders must be enumerated", ("open_orders",))
        add("balances_available_reserved", balances.get("complete") is True and balances.get("snapshot_hash") == account_snapshot.record_hash, "total, available, and reserved balances are required", ("balances",), account_snapshot.fetched_at_utc)

        venue_at = _datetime(venue_time.get("venue_time_at_utc"))
        clock_ok = venue_at is not None and abs((now - venue_at).total_seconds()) <= configuration.maximum_clock_skew_seconds
        add("venue_clock_skew", clock_ok, "venue clock skew must be bounded", ("venue_time",), venue_at)
        market_at = _datetime(market.get("validated_at_utc"))
        market_fresh = market_at is not None and 0 <= (now - market_at).total_seconds() <= configuration.market_data_freshness_seconds
        add("validated_market_data", market.get("validated") is True and market.get("source_kind") == "postgresql" and market_fresh, "Phase 7 PostgreSQL market evidence must be validated and fresh", ("market_data",), market_at)
        add("price_currency_identity", market.get("quote_currency") in configuration.allowed_settlement_assets, "price currency must match a permitted settlement currency", ("market_data",))

        tick = _decimal(instrument.get("tick_size")); lot = _decimal(instrument.get("lot_size")); minimum = _decimal(instrument.get("minimum_size")); minimum_notional = _decimal(instrument.get("minimum_notional")); maximum_notional = _decimal(instrument.get("maximum_notional"))
        add("instrument_metadata", instrument.get("instrument") in configuration.allowed_instruments and instrument.get("instrument_type") == "spot", "instrument metadata must match the configured Spot instrument", ("instrument_metadata",))
        add("tick_size", tick is not None and tick > 0, "tick size must be positive", ("instrument_metadata",))
        add("lot_size", lot is not None and lot > 0, "lot size must be positive", ("instrument_metadata",))
        add("minimum_order_size", minimum is not None and minimum > 0, "minimum order size must be positive", ("instrument_metadata",))
        add("order_notional_bounds", minimum_notional is not None and minimum_notional > 0 and (maximum_notional is None or maximum_notional >= configuration.maximum_order_notional), "provider and configured notional bounds must be compatible", ("instrument_metadata",))

        reconciliation_at = _datetime(reconciliation.get("evaluated_at_utc"))
        reconciliation_fresh = reconciliation_at is not None and 0 <= (now - reconciliation_at).total_seconds() <= configuration.reconciliation_freshness_seconds
        add("reconciliation", reconciliation.get("status") == LiveReconciliationStatus.RECONCILED.value and reconciliation_fresh and bool(reconciliation.get("reconciliation_id")), "latest PostgreSQL reconciliation must be fresh and reconciled", ("reconciliation",), reconciliation_at)
        add("kill_switch", kill.get("state") == "armed" and bool(kill.get("kill_switch_id")), "current PostgreSQL kill switch must be armed", ("kill_switch",))
        account_fresh = 0 <= (now - account_snapshot.fetched_at_utc).total_seconds() <= configuration.account_snapshot_freshness_seconds
        add("account_snapshot_fresh", account_fresh, "account snapshot must be fresh", ("balances", "account_config"), account_snapshot.fetched_at_utc)
        add("ci_hard_prohibition", not bool(common_ci_indicators()) or postgresql.get("fake_transport") is True, "CI requires fake transport and prohibits production credentials/network reads", ("postgresql_probe",))
        add("live_writes_disabled", not configuration.production_write_enabled and configuration.dry_run, "production writes must remain disabled", ("repository",))

        blockers = tuple(check.check_name for check in checks if check.required and not check.passed)
        status = LivePreflightStatus.PASSED if not blockers else LivePreflightStatus.BLOCKED
        return LivePreflightReport(
            live_run_id, configuration.configuration_hash, implementation_hash, repository_commit_sha,
            configuration.endpoint_catalog_hash, credential_reference.record_hash, account_snapshot.record_hash,
            now, tuple(checks), blockers, tuple(evidence.warnings), status,
        )


def collect_postgresql_probe_sources(*, connection, live_run_id, collected_at_utc: datetime, fake_transport: bool) -> tuple[LiveEvidenceSource, LiveEvidenceSource]:
    """Execute a real transaction probe and a write/rollback proof against PostgreSQL."""
    require_utc_datetime(collected_at_utc, field_name="collected_at_utc")
    available = False
    transaction_probe = False
    probe_id = uuid4()
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT current_setting('server_version_num')::integer >= 160000 AS ok")
            row = cursor.fetchone()
            available = bool(row["ok"] if isinstance(row, dict) else row[0])
        with connection.transaction(force_rollback=True):
            with connection.cursor() as cursor:
                cursor.execute("SELECT 1 AS ok")
                row = cursor.fetchone()
                transaction_probe = (row["ok"] if isinstance(row, dict) else row[0]) == 1
                cursor.execute(
                    "INSERT INTO execution.live_preflight_sources (source_id,live_run_id,source_kind,collected_at_utc,source_payload_jsonb,source_sha256,operational,record_sha256) VALUES (%s,%s,'postgresql_probe',%s,'{}'::jsonb,%s,true,%s)",
                    (probe_id, live_run_id, collected_at_utc, "0" * 64, "1" * 64),
                )
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1 FROM execution.live_preflight_sources WHERE source_id=%s", (probe_id,))
            rollback_verified = cursor.fetchone() is None
    except Exception:
        rollback_verified = False
    postgres = PostgreSQLProbeEvidence.source(
        live_run_id=live_run_id, collected_at_utc=collected_at_utc,
        available=available, transaction_probe=transaction_probe,
        fake_transport=fake_transport,
    )
    rollback = AuditRollbackProbeEvidence.source(
        live_run_id=live_run_id, collected_at_utc=collected_at_utc,
        write_succeeded=transaction_probe, rollback_verified=rollback_verified,
    )
    return postgres, rollback


def collect_migration_catalog_source(*, connection, live_run_id, collected_at_utc: datetime, expected_hashes: dict[str, str]) -> LiveEvidenceSource:
    with connection.cursor() as cursor:
        cursor.execute("SELECT migration_id,sha256 FROM audit.schema_migrations ORDER BY migration_id")
        rows = tuple(cursor.fetchall())
    actual = {str(row["migration_id"] if isinstance(row, dict) else row[0]): str(row["sha256"] if isinstance(row, dict) else row[1]) for row in rows}
    prefix = {key: value for key, value in actual.items() if key[:4] <= "0022"}
    immutable = all(prefix.get(key) == value for key, value in expected_hashes.items())
    latest = max((key[:4] for key in actual), default="")
    return MigrationCatalogEvidence.source(
        live_run_id=live_run_id, collected_at_utc=collected_at_utc,
        catalog_clean=bool(actual), latest_migration=latest, immutable_0001_0022=immutable,
        catalog_hash=sha256_payload(actual),
    )


# The old name remains importable only as an explicit fixture-only type.
LivePreflightEvidence = FixtureOnlyPreflightEvidence


__all__ = [
    "OperationalPreflightError", "LivePreflightEngine", "LivePreflightEvidence",
    "FixtureOnlyPreflightEvidence", "OperationalPreflightEvidence",
    "collect_postgresql_probe_sources", "collect_migration_catalog_source",
]
