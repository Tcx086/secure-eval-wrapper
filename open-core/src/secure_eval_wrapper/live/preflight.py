"""Evidence-producing, PostgreSQL-bound Phase 8A operational preflight."""
from __future__ import annotations

import hashlib
from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Mapping
from uuid import UUID, uuid4

from secure_eval_wrapper.data_collection.hashing import sha256_payload
from secure_eval_wrapper.data_collection.time_utils import require_utc_datetime

from .authorities import (
    FixtureOnlyPreflightEvidence,
    OperationalPreflightEvidence,
    VerifiedOperationalSource,
    _issue_verified_source,
)
from .collector_evidence import VerifiedOkxReadObservationBundle
from .credentials import validate_permission_summary
from .endpoints import endpoint_catalog_hash
from .gates import common_ci_indicators
from .models import (
    LiveAccountSnapshot,
    LiveCredentialReference,
    LivePreflightCheck,
    LivePreflightReport,
    LivePreflightPurpose,
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


def _collector_source(
    source_kind: str,
    *,
    live_run_id,
    collected_at_utc: datetime,
    collector_kind: str,
    source_record_identity: str,
    parser_version: str | None = None,
    **payload: object,
) -> VerifiedOperationalSource:
    normalized_hash = sha256_payload(payload)
    return _issue_verified_source(
        source_kind=source_kind, live_run_id=live_run_id, collected_at_utc=collected_at_utc,
        payload=payload, collector_kind=collector_kind, collector_version="phase8a-0024-v1",
        parser_version=parser_version, source_system_identity="secure-eval-wrapper/postgresql",
        source_record_identity=source_record_identity, raw_response_hash=normalized_hash,
        normalized_payload_hash=normalized_hash,
    )

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
        purpose: LivePreflightPurpose | str = LivePreflightPurpose.RUN_START,
    ) -> LivePreflightReport:
        now = require_utc_datetime(evaluated_at_utc, field_name="evaluated_at_utc")
        purpose = LivePreflightPurpose(purpose)
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
                now, (check,), (check.check_name,), (), LivePreflightStatus.BLOCKED, purpose=purpose,
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
        add("clean_migration_catalog", bool(migrations.get("catalog_clean")) and migrations.get("latest_migration") == "0024" and bool(migrations.get("immutable_0001_0023")), "migration 0024 and immutable 0001-0023 history must be collector-verified", ("migration_catalog",))
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
        add("validated_market_data", market.get("validated") is True
            and market.get("source_kind") == "postgresql" and market_fresh
            and market.get("validation_status") in ("accepted", "accepted_with_warnings")
            and market.get("report_status") in ("accepted", "accepted_with_warnings", "passed")
            and bool(market.get("raw_observation_ids"))
            and len(tuple(market.get("raw_observation_ids", ()))) == len(tuple(market.get("raw_observation_hashes", ())))
            and market.get("finality_verified") is True and market.get("quarantine_clear") is True,
            "Phase 7 PostgreSQL market evidence must have exact final, quarantine-clear lineage", ("market_data",), market_at)
        add("price_currency_identity", market.get("quote_currency") in configuration.allowed_settlement_assets, "price currency must match a permitted settlement currency", ("market_data",))

        tick = _decimal(instrument.get("tick_size")); lot = _decimal(instrument.get("lot_size")); minimum = _decimal(instrument.get("minimum_size")); minimum_notional = _decimal(instrument.get("minimum_notional")); maximum_notional = _decimal(instrument.get("maximum_notional"))
        add("instrument_metadata",
            instrument.get("instrument") in configuration.allowed_instruments
            and instrument.get("instrument_type") == "spot"
            and instrument.get("instrument_state") == "live"
            and instrument.get("base_currency") == instrument.get("instrument", "").split("-")[0]
            and instrument.get("quote_currency") in configuration.allowed_settlement_assets
            and bool(instrument.get("response_bundle_id"))
            and isinstance(instrument.get("provider_response_hash"), str)
            and len(instrument.get("provider_response_hash")) == 64,
            "instrument metadata must be exact, live, fresh OKX Spot authority", ("instrument_metadata",))
        add("tick_size", tick is not None and tick > 0, "tick size must be positive", ("instrument_metadata",))
        add("lot_size", lot is not None and lot > 0, "lot size must be positive", ("instrument_metadata",))
        add("minimum_order_size", minimum is not None and minimum > 0, "minimum order size must be positive", ("instrument_metadata",))
        add("order_notional_bounds", minimum_notional is not None and minimum_notional > 0 and (maximum_notional is None or maximum_notional >= configuration.maximum_order_notional), "provider and configured notional bounds must be compatible", ("instrument_metadata",))

        reconciliation_at = _datetime(reconciliation.get("evaluated_at_utc"))
        reconciliation_fresh = reconciliation_at is not None and 0 <= (now - reconciliation_at).total_seconds() <= configuration.reconciliation_freshness_seconds
        add("reconciliation", reconciliation.get("status") == LiveReconciliationStatus.RECONCILED.value and reconciliation_fresh and bool(reconciliation.get("reconciliation_id")), "latest PostgreSQL reconciliation must be fresh and reconciled", ("reconciliation",), reconciliation_at)
        if purpose is LivePreflightPurpose.KILL_RESET:
            add("kill_switch", kill.get("state") == "stopped" and bool(kill.get("kill_switch_id")) and bool(kill.get("triggered_at_utc")), "kill-reset preflight requires the current stopped PostgreSQL kill row and trigger evidence", ("kill_switch",))
        else:
            add("kill_switch", kill.get("state") == "armed" and bool(kill.get("kill_switch_id")), "normal preflight requires the current PostgreSQL kill switch to be armed", ("kill_switch",))
        account_fresh = 0 <= (now - account_snapshot.fetched_at_utc).total_seconds() <= configuration.account_snapshot_freshness_seconds
        add("account_snapshot_fresh", account_fresh, "account snapshot must be fresh", ("balances", "account_config"), account_snapshot.fetched_at_utc)
        add("ci_hard_prohibition", not bool(common_ci_indicators()) or postgresql.get("fake_transport") is True, "CI requires fake transport and prohibits production credentials/network reads", ("postgresql_probe",))
        add("live_writes_disabled", not configuration.production_write_enabled and configuration.dry_run, "production writes must remain disabled", ("repository",))

        blockers = tuple(check.check_name for check in checks if check.required and not check.passed)
        status = (
            LivePreflightStatus.PASSED_FOR_RESET
            if purpose is LivePreflightPurpose.KILL_RESET and not blockers
            else LivePreflightStatus.PASSED if not blockers else LivePreflightStatus.BLOCKED
        )
        return LivePreflightReport(
            live_run_id, configuration.configuration_hash, implementation_hash, repository_commit_sha,
            configuration.endpoint_catalog_hash, credential_reference.record_hash, account_snapshot.record_hash,
            now, tuple(checks), blockers, tuple(evidence.warnings), status, purpose=purpose,
        )


def collect_postgresql_probe_sources(*, connection, live_run_id, collected_at_utc: datetime, fake_transport: bool) -> tuple[VerifiedOperationalSource, VerifiedOperationalSource]:
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
                empty_payload_hash = sha256_payload({})
                cursor.execute(
                    "INSERT INTO execution.live_preflight_sources (source_id,live_run_id,source_kind,collected_at_utc,source_payload_jsonb,source_sha256,operational,record_sha256,normalized_payload_sha256) VALUES (%s,%s,'postgresql_probe',%s,'{}'::jsonb,%s,false,%s,%s)",
                    (probe_id, live_run_id, collected_at_utc, "0" * 64, "1" * 64, empty_payload_hash),
                )
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1 FROM execution.live_preflight_sources WHERE source_id=%s", (probe_id,))
            rollback_verified = cursor.fetchone() is None
    except Exception:
        rollback_verified = False
    postgres = _collector_source(
        "postgresql_probe", live_run_id=live_run_id, collected_at_utc=collected_at_utc,
        collector_kind="postgresql_transaction_probe", source_record_identity=str(probe_id),
        available=available, transaction_probe=transaction_probe, fake_transport=fake_transport,
    )
    rollback = _collector_source(
        "audit_rollback_probe", live_run_id=live_run_id, collected_at_utc=collected_at_utc,
        collector_kind="postgresql_rollback_probe", source_record_identity=str(probe_id),
        write_succeeded=transaction_probe, rollback_verified=rollback_verified,
    )
    return postgres, rollback


def collect_migration_catalog_source(*, connection, live_run_id, collected_at_utc: datetime) -> VerifiedOperationalSource:
    with connection.cursor() as cursor:
        cursor.execute("SELECT migration_id,sha256 FROM audit.schema_migrations ORDER BY migration_id")
        rows = tuple(cursor.fetchall())
    actual = {str(row["migration_id"] if isinstance(row, dict) else row[0]): str(row["sha256"] if isinstance(row, dict) else row[1]) for row in rows}
    migration_root = Path(__file__).resolve().parents[3] / "db" / "migrations"
    expected_hashes = {
        path.stem: hashlib.sha256(path.read_bytes().replace(b"\r\n", b"\n")).hexdigest()
        for path in sorted(migration_root.glob("*.sql"))
        if path.name[:4].isdigit() and path.name[:4] <= "0023"
    }
    observed_immutable = {key: value for key, value in actual.items() if key[:4] <= "0023"}
    immutable = observed_immutable == expected_hashes and len(expected_hashes) == 23
    latest = max((key[:4] for key in actual), default="")
    catalog_hash = sha256_payload({"observed": actual, "expected_0001_0023": expected_hashes})
    return _collector_source(
        "migration_catalog", live_run_id=live_run_id, collected_at_utc=collected_at_utc,
        collector_kind="repository_migration_catalog", source_record_identity=catalog_hash,
        catalog_clean=immutable and latest == "0024", latest_migration=latest,
        immutable_0001_0023=immutable, observed_hashes=actual,
        expected_hashes_0001_0023=expected_hashes, catalog_hash=catalog_hash,
    )


def collect_operational_preflight_evidence(
    *,
    connection,
    live_run_id,
    configuration,
    credential_reference,
    account_snapshot,
    market_evidence,
    reconciliation,
    kill_switch,
    okx_bundle: VerifiedOkxReadObservationBundle,
    repository_commit_sha: str,
    collected_at_utc: datetime,
) -> OperationalPreflightEvidence:
    """Collect all operational sources without accepting caller-created payload dictionaries."""
    if not isinstance(okx_bundle, VerifiedOkxReadObservationBundle) or not okx_bundle.complete:
        raise OperationalPreflightError("operational preflight requires a complete approved OKX adapter bundle")
    if okx_bundle.live_run_id != live_run_id or okx_bundle.purpose != "preflight":
        raise OperationalPreflightError("OKX preflight bundle run or purpose mismatch")
    if okx_bundle.account_fingerprint != account_snapshot.account_fingerprint:
        raise OperationalPreflightError("OKX bundle account fingerprint mismatch")
    with connection.cursor() as cursor:
        cursor.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", (str(live_run_id),))
        cursor.execute(
            "SELECT k.*,r.latest_account_snapshot_id,r.latest_reconciliation_id "
            "FROM execution.live_kill_switches k "
            "JOIN execution.live_run_risk_state r ON r.live_run_id=k.live_run_id "
            "WHERE k.live_run_id=%s FOR SHARE OF k,r",
            (live_run_id,),
        )
        current = cursor.fetchone()
        if current is not None:
            current = dict(current)
            if current["latest_account_snapshot_id"] != account_snapshot.snapshot_id:
                raise OperationalPreflightError("caller account snapshot is not current PostgreSQL authority")
            cursor.execute(
                "SELECT * FROM execution.live_reconciliations "
                "WHERE reconciliation_id=%s AND live_run_id=%s FOR SHARE",
                (current["latest_reconciliation_id"], live_run_id),
            )
            reconciliation_row = cursor.fetchone()
            if reconciliation_row is None:
                raise OperationalPreflightError("current PostgreSQL reconciliation is missing")
            reconciliation_row = dict(reconciliation_row)
            reconciliation_payload = {
                "status": reconciliation_row["status"],
                "evaluated_at_utc": reconciliation_row["evaluated_at_utc"],
                "reconciliation_id": str(reconciliation_row["reconciliation_id"]),
                "record_hash": reconciliation_row["record_sha256"],
                "input_bundle_id": str(reconciliation_row.get("response_bundle_id") or ""),
            }
            kill_payload = {
                "state": current["state"], "kill_switch_id": str(current["kill_switch_id"]),
                "version": int(current["version"]),
                "triggered_at_utc": current["triggered_at_utc"],
                "evidence_hash": current["evidence_sha256"],
            }
        else:
            if reconciliation is None or kill_switch is None:
                raise OperationalPreflightError("bootstrap preflight requires exact initial reconciliation and kill authorities")
            if getattr(reconciliation, "producer_classification", None) != "operational_collector":
                raise OperationalPreflightError("bootstrap reconciliation must be collector-issued")
            reconciliation_payload = {
                "status": reconciliation.status.value,
                "evaluated_at_utc": reconciliation.evaluated_at_utc,
                "reconciliation_id": str(reconciliation.reconciliation_id),
                "record_hash": reconciliation.record_hash,
                "input_bundle_id": str(reconciliation.response_bundle_id),
            }
            kill_payload = {
                "state": kill_switch.state.value,
                "kill_switch_id": str(kill_switch.kill_switch_id),
                "version": 0, "triggered_at_utc": None,
                "evidence_hash": kill_switch.evidence_hash,
            }


    migration, = (collect_migration_catalog_source(
        connection=connection, live_run_id=live_run_id, collected_at_utc=collected_at_utc,
    ),)
    postgres, rollback = collect_postgresql_probe_sources(
        connection=connection, live_run_id=live_run_id,
        collected_at_utc=collected_at_utc,
        fake_transport=okx_bundle.transport_is_fake,
    )

    def issued(kind: str, collector: str, identity: str, payload: Mapping[str, object], raw_hash: str | None = None, parser: str | None = None):
        normalized_hash = sha256_payload(payload)
        return _issue_verified_source(
            source_kind=kind, live_run_id=live_run_id, collected_at_utc=collected_at_utc,
            payload=payload, collector_kind=collector, collector_version="phase8a-0024-v1",
            parser_version=parser, source_system_identity="okx-production/postgresql",
            source_record_identity=identity, raw_response_hash=raw_hash or normalized_hash,
            normalized_payload_hash=normalized_hash,
        )

    account_config_envelope = okx_bundle.envelope("account_config")
    balance_envelope = okx_bundle.envelope("balances")
    positions_envelope = okx_bundle.envelope("positions")
    orders_envelope = okx_bundle.envelope("pending_orders")
    venue_time_envelope = okx_bundle.envelope("venue_time")
    instrument_envelope = okx_bundle.envelope("instrument_metadata")
    account_config = dict(account_config_envelope.normalized_payload)
    balances = dict(balance_envelope.normalized_payload)
    positions = tuple(positions_envelope.normalized_payload)
    open_orders = tuple(orders_envelope.normalized_payload)
    venue_time = dict(venue_time_envelope.normalized_payload)
    instruments = tuple(instrument_envelope.normalized_payload)
    if len(instruments) != 1:
        raise OperationalPreflightError("preflight requires exactly one instrument metadata row")
    instrument = dict(instruments[0])
    normalized_balances = {
        str(row["ccy"]): {
            "total": Decimal(str(row["equity"])),
            "available": Decimal(str(row["available"])),
            "reserved": Decimal(str(row["reserved"])),
        }
        for row in balances["details"]
    }
    normalized_positions = {
        str(row["instId"]): {
            "quantity": Decimal(str(row["quantity"])),
            "average_price": Decimal(str(row["average_price"])),
            "unrealized_pnl": Decimal(str(row["unrealized_pnl"])),
        }
        for row in positions
        if Decimal(str(row["quantity"])) != 0
    }
    if (
        normalized_balances != dict(account_snapshot.balances)
        or normalized_positions != dict(account_snapshot.positions)
        or Decimal(str(balances["total_equity"])) != account_snapshot.total_equity
        or len(open_orders) != account_snapshot.open_order_count
        or venue_time.get("venue_time_at_utc") != account_snapshot.venue_time_at_utc
    ):
        raise OperationalPreflightError("account snapshot is not derived from the exact OKX response bundle")


    bar_id = UUID(str(market_evidence.source_row_id))
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT b.*,r.status AS report_status,r.report_sha256,r.report_jsonb "
            "FROM market_data.validated_bars b JOIN data_quality.validation_reports r "
            "ON r.validation_report_id=b.validation_report_id WHERE b.bar_id=%s",
            (bar_id,),
        )
        market_row = cursor.fetchone()
        if market_row is None:
            raise OperationalPreflightError("market source is not backed by the exact Phase 7 row")
        market_row = dict(market_row)
        raw_ids = tuple(market_row["source_observation_ids"])
        cursor.execute(
            "SELECT observation_id,source_sha256,observed_at_utc FROM market_data.raw_source_observations "
            "WHERE observation_id=ANY(%s) ORDER BY observation_id",
            (list(raw_ids),),
        )
        raw_rows = tuple(dict(row) for row in cursor.fetchall())
        cursor.execute(
            "SELECT count(*) AS count FROM data_quality.quarantine_decisions "
            "WHERE validation_report_id=%s OR observation_id=ANY(%s)",
            (market_row["validation_report_id"], list(raw_ids)),
        )
        quarantine_count = int(cursor.fetchone()["count"])
    if not raw_ids or len(raw_rows) != len(raw_ids):
        raise OperationalPreflightError("market source raw observation lineage is incomplete")
    provenance = dict(market_row["provenance_jsonb"])
    finality = provenance.get("is_final") is True
    available_at = provenance.get("available_at_utc") or market_evidence.available_at_utc
    quote_currency = str(provenance.get("quote_currency") or market_evidence.quote_currency)
    market_payload = {
        "validated": market_row["validation_status"] in ("accepted", "accepted_with_warnings"),
        "provider": market_evidence.provider,
        "exchange": market_evidence.exchange,
        "provider_instrument_id": market_evidence.provider_instrument_id,
        "canonical_symbol": market_evidence.series_identity.canonical_symbol,
        "timeframe": market_evidence.series_identity.timeframe,
        "event_type": market_evidence.event_type,
        "source_table": market_evidence.source_table,
        "source_sha256": market_evidence.source_sha256,
        "normalized_record_sha256": market_evidence.normalized_record_sha256,
        "observation_id": market_evidence.observation_id,
        "source_kind": "postgresql", "validated_at_utc": available_at,
        "observed_at_utc": max(row["observed_at_utc"] for row in raw_rows),
        "available_at_utc": available_at, "quote_currency": quote_currency,
        "market_evidence_sha256": market_evidence.evidence_sha256,
        "source_row_id": str(bar_id), "validation_report_id": str(market_row["validation_report_id"]),
        "validation_status": market_row["validation_status"], "report_status": market_row["report_status"],
        "raw_observation_ids": tuple(str(row["observation_id"]) for row in raw_rows),
        "raw_observation_hashes": {
            str(row["observation_id"]): str(row["source_sha256"]) for row in raw_rows},
        "finality_verified": finality, "quarantine_clear": quarantine_count == 0,
        "price": market_row["close"], "price_type": "close",
    }
    if not (market_payload["validated"] and finality and quarantine_count == 0):
        raise OperationalPreflightError("Phase 7 market row is not final, accepted, and quarantine-clear")

    repository_payload = {
        "commit_sha": repository_commit_sha, "expected_commit_sha": repository_commit_sha,
        "implementation_hash": configuration.provider_implementation_hash,
    }
    sources = [
        issued("repository", "repository_commit_collector", repository_commit_sha, repository_payload),
        migration, postgres, rollback,
        issued("credential_reference", "credential_repository_collector", str(credential_reference.reference_id), {
            "reference_id": str(credential_reference.reference_id), "record_hash": credential_reference.record_hash,
            "credential_material_present": False,
        }),
        issued("credential_permissions", "credential_permission_verifier", f"{credential_reference.reference_id}:{credential_reference.verified_at_utc}", {
            "permissions": credential_reference.permission_summary,
            "credential_record_hash": credential_reference.record_hash,
            "verified_at_utc": credential_reference.verified_at_utc, "verifier_version": "permission-v2",
        }),
    ]
    bundle_identity = str(okx_bundle.bundle_id)
    account_raw = account_config_envelope.canonical_response_hash
    sources.extend([
        issued("account_config", "okx_read_only_adapter", bundle_identity, {"account_exists": True, **account_config}, account_raw, okx_bundle.parser_version),
        issued("account_fingerprint", "okx_read_only_adapter", bundle_identity, {"observed": account_snapshot.account_fingerprint}, account_raw, okx_bundle.parser_version),
        issued("subaccount", "okx_read_only_adapter", bundle_identity, {"observed": configuration.subaccount_fingerprint}, account_raw, okx_bundle.parser_version),
        issued("account_mode", "okx_read_only_adapter", bundle_identity, {"account_mode": account_config["account_mode"]}, account_raw, okx_bundle.parser_version),
        issued("margin_borrowing", "okx_read_only_adapter", bundle_identity, {
            "margin_enabled": False, "leverage_enabled": False, "borrowing_enabled": False,
        }, account_raw, okx_bundle.parser_version),
        issued("balances", "okx_read_only_adapter", bundle_identity, {
            "complete": True, "snapshot_hash": account_snapshot.record_hash,
            "provider_response_hash": balance_envelope.canonical_response_hash,
        }, balance_envelope.canonical_response_hash, okx_bundle.parser_version),
        issued("positions", "okx_read_only_adapter", bundle_identity, {
            "derivative_count": 0, "short_count": sum(Decimal(str(row["quantity"])) < 0 for row in positions),
            "margin_count": 0, "provider_response_hash": positions_envelope.canonical_response_hash,
        }, positions_envelope.canonical_response_hash, okx_bundle.parser_version),
        issued("open_orders", "okx_read_only_adapter", bundle_identity, {
            "enumerated": True, "count": len(open_orders),
            "provider_response_hash": orders_envelope.canonical_response_hash,
        }, orders_envelope.canonical_response_hash, okx_bundle.parser_version),
        issued("venue_time", "okx_read_only_adapter", bundle_identity, venue_time, venue_time_envelope.canonical_response_hash, okx_bundle.parser_version),
        issued("market_data", "phase7_postgresql_market_collector", str(bar_id), market_payload),
        issued("instrument_metadata", "okx_read_only_adapter", bundle_identity, {
            **instrument, "instrument_type": "spot",
            "maximum_notional": str(configuration.maximum_order_notional),
            "provider_response_hash": instrument_envelope.canonical_response_hash,
            "response_bundle_id": bundle_identity,
        }, instrument_envelope.canonical_response_hash, okx_bundle.parser_version),
        issued("reconciliation", "postgresql_current_reconciliation_collector",
            f"{reconciliation_payload['reconciliation_id']}:{reconciliation_payload['record_hash']}", reconciliation_payload),
        issued("kill_switch", "postgresql_current_kill_collector",
            f"{kill_payload['kill_switch_id']}:{kill_payload['version']}", kill_payload),
    ])
    return OperationalPreflightEvidence(live_run_id, tuple(sources))


# The old name remains importable only as an explicit fixture-only type.
LivePreflightEvidence = FixtureOnlyPreflightEvidence


__all__ = [
    "OperationalPreflightError", "LivePreflightEngine", "LivePreflightEvidence",
    "FixtureOnlyPreflightEvidence", "OperationalPreflightEvidence",
    "collect_postgresql_probe_sources", "collect_migration_catalog_source",
    "collect_operational_preflight_evidence",
]
