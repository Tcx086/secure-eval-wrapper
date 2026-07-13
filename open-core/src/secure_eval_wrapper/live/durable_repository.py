"""PostgreSQL-authoritative Phase 8A evidence, risk, reservation, recovery, and restart."""
from __future__ import annotations

import json
import re
from contextlib import contextmanager
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Mapping
from uuid import UUID, uuid4

from secure_eval_wrapper.data_collection.hashing import canonical_json_dumps, sha256_payload
from secure_eval_wrapper.storage.postgres.alpha_signal_base import _json_param

from .authorities import LiveRuntimeRiskState, OperationalPreflightEvidence
from .credentials import redact, validate_permission_summary
from .models import (
    LiveKillState,
    LiveOrderState,
    LivePreflightStatus,
    LiveRecoveryOutcome,
    LiveReconciliationStatus,
    LiveRiskDecision,
    live_uuid,
)
from .reservations import calculate_live_reservation
from .risk import evaluate_live_risk
from .recovery import normalize_recovery_observation


class LiveConflictError(RuntimeError):
    pass


class LiveClaimError(RuntimeError):
    pass


_SECRET_FIELD = re.compile(r"(?i)(api.?secret|passphrase|authorization|signing.?key|ok-access-(?:key|sign|passphrase)|secret.?store.?payload)")


def _public_payload(value):
    redacted = redact(value)
    serialized = canonical_json_dumps(redacted)
    if _SECRET_FIELD.search(serialized):
        raise ValueError("persisted live payload contains a forbidden secret field")
    return _json_param(json.loads(serialized))


def _json_value(value):
    if value is None:
        return None
    if isinstance(value, (dict, list, tuple)):
        return value
    if isinstance(value, str):
        return json.loads(value)
    return value


def _utc(value) -> datetime:
    if isinstance(value, datetime):
        return value
    return datetime.fromisoformat(str(value).replace("Z", "+00:00"))


class DurablePostgresLiveRepository:
    authoritative_storage = "PostgreSQL"

    def __init__(self, connection) -> None:
        self.connection = connection
        self._transaction_depth = 0

    @contextmanager
    def transaction(self):
        if self._transaction_depth:
            self._transaction_depth += 1
            try:
                yield self
            finally:
                self._transaction_depth -= 1
            return
        self._transaction_depth = 1
        try:
            with self.connection.transaction():
                yield self
        finally:
            self._transaction_depth = 0

    def _fetchone(self, sql, params=()):
        cursor = self.connection.cursor()
        try:
            cursor.execute(sql, params)
            row = cursor.fetchone()
            if row is None:
                return None
            if isinstance(row, Mapping):
                return dict(row)
            names = tuple(getattr(item, "name", item[0]) for item in cursor.description)
            return dict(zip(names, row))
        finally:
            cursor.close()

    def _fetchall(self, sql, params=()):
        cursor = self.connection.cursor()
        try:
            cursor.execute(sql, params)
            rows = cursor.fetchall()
            names = tuple(getattr(item, "name", item[0]) for item in cursor.description)
            return [dict(row) if isinstance(row, Mapping) else dict(zip(names, row)) for row in rows]
        finally:
            cursor.close()

    def _execute(self, sql, params=()):
        cursor = self.connection.cursor()
        try:
            cursor.execute(sql, params)
            return cursor.rowcount
        finally:
            cursor.close()

    def _lock_run(self, live_run_id):
        self._execute("SELECT pg_advisory_xact_lock(hashtextextended(%s,0))", (str(live_run_id),))

    def _strict_insert(self, table, identity_column, identity, columns, values, record_hash):
        names = (identity_column,) + tuple(columns) + ("record_sha256",)
        placeholders = ",".join(["%s"] * len(names))
        sql = f"INSERT INTO {table} ({','.join(names)}) VALUES ({placeholders}) ON CONFLICT ({identity_column}) DO NOTHING RETURNING {identity_column}"
        row = self._fetchone(sql, (identity,) + tuple(values) + (record_hash,))
        if row is not None:
            return identity
        existing = self._fetchone(f"SELECT record_sha256 FROM {table} WHERE {identity_column}=%s", (identity,))
        if existing is None or str(existing["record_sha256"]) != record_hash:
            raise LiveConflictError(f"immutable conflict in {table}")
        return identity

    @staticmethod
    def _validate_start_bundle(*, configuration, credential_reference, account_snapshot, report, approval, manifest, kill_switch, evidence):
        run = report.live_run_id
        if not isinstance(evidence, OperationalPreflightEvidence):
            raise TypeError("start bundle requires exact OperationalPreflightEvidence")
        if any(value != run for value in (
            account_snapshot.live_run_id, approval.live_run_id, manifest.live_run_id,
            kill_switch.live_run_id, evidence.live_run_id,
        )):
            raise ValueError("all start-bundle authorities must belong to the same live run")
        if report.status is not LivePreflightStatus.PASSED:
            raise PermissionError("only a passed operational preflight can start a live dry-run")
        validate_permission_summary(credential_reference.permission_summary)
        required = (
            report.configuration_hash == configuration.configuration_hash,
            report.credential_reference_hash == credential_reference.record_hash,
            report.account_snapshot_hash == account_snapshot.record_hash,
            report.repository_commit_sha == manifest.repository_commit_sha,
            report.endpoint_catalog_hash == manifest.endpoint_catalog_hash,
            approval.preflight_report_id == report.report_id,
            approval.configuration_hash == configuration.configuration_hash,
            approval.account_fingerprint == account_snapshot.account_fingerprint,
            approval.manifest_hash == manifest.manifest_hash,
            approval.repository_commit_sha == manifest.repository_commit_sha,
            approval.provider == configuration.provider == manifest.provider == credential_reference.provider,
            approval.environment == configuration.environment == manifest.environment,
            approval.account_fingerprint == configuration.account_fingerprint == manifest.account_fingerprint,
            approval.allowed_instruments == configuration.allowed_instruments == manifest.allowed_instruments,
            manifest.endpoint_catalog_hash == configuration.endpoint_catalog_hash,
            manifest.risk_limits == configuration.risk_limits,
            manifest.expected_maximum_duration_seconds == configuration.maximum_run_duration_seconds,
            account_snapshot.account_fingerprint == credential_reference.account_fingerprint,
            manifest.configuration_hash == configuration.configuration_hash,
            manifest.approval_id == approval.approval_id,
            manifest.preflight_report_id == report.report_id,
            manifest.initial_account_snapshot_id == account_snapshot.snapshot_id,
            manifest.initial_account_snapshot_hash == account_snapshot.record_hash,
            manifest.credential_reference_hash == credential_reference.record_hash,
            manifest.implementation_hash == report.implementation_hash == configuration.provider_implementation_hash,
            kill_switch.state is LiveKillState.ARMED,
            manifest.dry_run and not manifest.production_write_enabled,
        )
        if not all(required):
            raise ValueError("start-bundle authority binding mismatch")
        sources = {item.source_id: item for item in evidence.sources}
        for check in report.checks:
            if not check.source_ids or len(check.source_ids) != len(check.source_hashes):
                raise ValueError("every operational preflight check must cite exact source IDs and hashes")
            for source_id, source_hash in zip(check.source_ids, check.source_hashes):
                if source_id not in sources or sources[source_id].source_hash != source_hash:
                    raise ValueError("preflight check source binding mismatch")

    def persist_start_bundle(self, *, configuration, credential_reference, account_snapshot, report, approval, manifest, kill_switch, evidence, created_at_utc, fail_at=None):
        self._validate_start_bundle(
            configuration=configuration, credential_reference=credential_reference,
            account_snapshot=account_snapshot, report=report, approval=approval,
            manifest=manifest, kill_switch=kill_switch, evidence=evidence,
        )
        source_by_kind = {source.source_kind: source for source in evidence.sources}
        with self.transaction():
            self._lock_run(manifest.live_run_id)
            configuration_id = live_uuid("configuration", {"hash": configuration.configuration_hash})
            configuration_payload = {name: getattr(configuration, name) for name in configuration.__dataclass_fields__}
            self._strict_insert(
                "execution.live_configuration_snapshots", "configuration_snapshot_id", configuration_id,
                ("configuration_sha256", "provider", "environment", "account_fingerprint", "dry_run", "read_only_preflight", "production_write_enabled", "configuration_jsonb", "created_at_utc"),
                (configuration.configuration_hash, configuration.provider, configuration.environment, configuration.account_fingerprint, configuration.dry_run, configuration.read_only_preflight, configuration.production_write_enabled, _public_payload(configuration_payload), created_at_utc),
                sha256_payload(configuration_payload),
            )
            if fail_at == "configuration": raise RuntimeError("injected configuration failure")
            self._strict_insert(
                "execution.live_credential_references", "credential_reference_id", credential_reference.reference_id,
                ("provider", "alias", "source_type", "account_fingerprint", "loaded", "verified_at_utc", "permission_summary_jsonb", "created_at_utc"),
                (credential_reference.provider, credential_reference.alias, credential_reference.source_type, credential_reference.account_fingerprint, credential_reference.loaded, credential_reference.verified_at_utc, _public_payload(credential_reference.permission_summary), created_at_utc),
                credential_reference.record_hash,
            )
            if fail_at == "credential": raise RuntimeError("injected credential failure")
            self._strict_insert(
                "execution.live_account_snapshots", "account_snapshot_id", account_snapshot.snapshot_id,
                ("live_run_id", "account_fingerprint", "fetched_at_utc", "venue_time_at_utc", "total_equity", "available_equity", "reserved_equity", "open_order_count", "account_mode", "snapshot_jsonb"),
                (account_snapshot.live_run_id, account_snapshot.account_fingerprint, account_snapshot.fetched_at_utc, account_snapshot.venue_time_at_utc, account_snapshot.total_equity, account_snapshot.available_equity, account_snapshot.reserved_equity, account_snapshot.open_order_count, account_snapshot.account_mode, _public_payload({"balances": dict(account_snapshot.balances), "positions": dict(account_snapshot.positions)})),
                account_snapshot.record_hash,
            )
            if fail_at == "account": raise RuntimeError("injected account failure")
            for source in evidence.sources:
                self._strict_insert(
                    "execution.live_preflight_sources", "source_id", source.source_id,
                    ("live_run_id", "source_kind", "collected_at_utc", "source_payload_jsonb", "source_sha256", "operational"),
                    (source.live_run_id, source.source_kind, source.collected_at_utc, _public_payload(dict(source.payload)), source.source_hash, source.operational),
                    source.record_hash,
                )
            self._strict_insert(
                "execution.live_preflight_reports", "preflight_report_id", report.report_id,
                ("live_run_id", "configuration_sha256", "implementation_sha256", "repository_commit_sha", "endpoint_catalog_sha256", "credential_reference_sha256", "account_snapshot_sha256", "evaluated_at_utc", "status", "blockers_jsonb", "warnings_jsonb", "credential_reference_id", "account_snapshot_id"),
                (report.live_run_id, report.configuration_hash, report.implementation_hash, report.repository_commit_sha, report.endpoint_catalog_hash, report.credential_reference_hash, report.account_snapshot_hash, report.evaluated_at_utc, report.status.value, _public_payload(report.blockers), _public_payload(report.warnings), credential_reference.reference_id, account_snapshot.snapshot_id),
                report.record_hash,
            )
            for check_ordinal, check in enumerate(report.checks):
                self._strict_insert(
                    "execution.live_preflight_checks", "preflight_check_id", check.check_id,
                    ("preflight_report_id", "live_run_id", "check_ordinal", "check_name", "passed", "required", "evaluated_at_utc", "source_timestamp_utc", "explanation", "evidence_sha256"),
                    (report.report_id, report.live_run_id, check_ordinal, check.check_name, check.passed, check.required, check.evaluated_at_utc, check.source_timestamp_utc, check.explanation, check.evidence_hash),
                    check.record_hash,
                )
                for source_ordinal, (source_id, source_hash) in enumerate(zip(check.source_ids, check.source_hashes)):
                    self._execute(
                        "INSERT INTO execution.live_preflight_check_sources (preflight_check_id,source_ordinal,source_id,live_run_id,source_sha256) VALUES (%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING",
                        (check.check_id, source_ordinal, source_id, report.live_run_id, source_hash),
                    )
            if fail_at == "preflight": raise RuntimeError("injected preflight failure")
            self._strict_insert(
                "execution.live_approvals", "approval_id", approval.approval_id,
                ("live_run_id", "preflight_report_id", "configuration_sha256", "account_fingerprint", "provider", "environment", "manifest_sha256", "confirmation_challenge_sha256", "maximum_total_approved_notional", "consumed_notional", "created_at_utc", "expires_at_utc", "approving_actor", "nonce", "approval_jsonb"),
                (approval.live_run_id, approval.preflight_report_id, approval.configuration_hash, approval.account_fingerprint, approval.provider, approval.environment, approval.manifest_hash, approval.confirmation_challenge_hash, approval.maximum_total_approved_notional, Decimal(0), approval.created_at_utc, approval.expires_at_utc, approval.approving_actor, approval.nonce, _public_payload({"allowed_instruments": approval.allowed_instruments, "repository_commit_sha": approval.repository_commit_sha})),
                approval.record_hash,
            )
            if fail_at == "approval": raise RuntimeError("injected approval failure")
            self._strict_insert(
                "execution.live_run_manifests", "manifest_id", manifest.manifest_id,
                ("live_run_id", "approval_id", "preflight_report_id", "initial_account_snapshot_id", "configuration_sha256", "implementation_sha256", "repository_commit_sha", "endpoint_catalog_sha256", "credential_reference_sha256", "manifest_sha256", "dry_run", "production_write_enabled", "manifest_jsonb", "created_at_utc", "credential_reference_id"),
                (manifest.live_run_id, manifest.approval_id, manifest.preflight_report_id, manifest.initial_account_snapshot_id, manifest.configuration_hash, manifest.implementation_hash, manifest.repository_commit_sha, manifest.endpoint_catalog_hash, manifest.credential_reference_hash, manifest.manifest_hash, manifest.dry_run, manifest.production_write_enabled, _public_payload({"allowed_instruments": manifest.allowed_instruments, "risk_limits": dict(manifest.risk_limits), "kill_switch_policy": dict(manifest.kill_switch_policy), "parent_evidence_ids": manifest.parent_evidence_ids}), created_at_utc, credential_reference.reference_id),
                manifest.record_hash,
            )
            if fail_at == "manifest": raise RuntimeError("injected manifest failure")
            run_hash = sha256_payload({"run": manifest.live_run_id, "manifest": manifest.manifest_id, "state": "dry_run_running"})
            self._strict_insert(
                "execution.live_runs", "live_run_id", manifest.live_run_id,
                ("manifest_id", "state", "dry_run", "production_write_enabled", "started_at_utc", "completed_at_utc", "version"),
                (manifest.manifest_id, "dry_run_running", True, False, created_at_utc, None, 0), run_hash,
            )
            self._strict_insert(
                "execution.live_kill_switches", "kill_switch_id", kill_switch.kill_switch_id,
                ("live_run_id", "state", "reason", "evidence_sha256", "requires_fresh_preflight", "requires_new_approval", "updated_at_utc", "version", "triggered_at_utc", "reset_preflight_report_id", "reset_approval_id"),
                (kill_switch.live_run_id, kill_switch.state.value, kill_switch.reason, kill_switch.evidence_hash, kill_switch.requires_fresh_preflight, kill_switch.requires_new_approval, kill_switch.updated_at_utc, 0, None, None, None), kill_switch.record_hash,
            )
            if fail_at == "kill_switch": raise RuntimeError("injected kill switch failure")

            market = source_by_kind["market_data"]
            reconciliation = source_by_kind["reconciliation"]
            venue_time = source_by_kind["venue_time"].payload
            market_at = _utc(market.payload["validated_at_utc"])
            reconciliation_at = _utc(reconciliation.payload["evaluated_at_utc"])
            market_evidence_hash = str(market.payload.get("market_evidence_sha256") or market.source_hash)
            reconciliation_id = UUID(str(reconciliation.payload["reconciliation_id"]))
            clock_skew = abs(Decimal(str((created_at_utc - _utc(venue_time["venue_time_at_utc"])).total_seconds())))
            risk_hash = sha256_payload({"run": manifest.live_run_id, "account": account_snapshot.record_hash, "market": market.source_hash, "reconciliation": reconciliation.source_hash})
            self._strict_insert(
                "execution.live_run_risk_state", "live_run_id", manifest.live_run_id,
                ("trading_day", "current_equity", "high_watermark_equity", "daily_submitted_notional", "daily_realized_pnl", "gross_exposure", "net_exposure", "order_rate_window_jsonb", "cancellation_rate_window_jsonb", "open_order_count", "oldest_unknown_order_at_utc", "oldest_unacknowledged_order_at_utc", "latest_market_data_at_utc", "latest_account_snapshot_at_utc", "latest_reconciliation_at_utc", "latest_reconciliation_status", "clock_skew_seconds", "run_started_at_utc", "transport_failure_count", "balances_jsonb", "positions_jsonb", "latest_account_snapshot_id", "latest_reconciliation_id", "latest_market_evidence_id", "latest_market_evidence_sha256", "updated_at_utc", "version"),
                (created_at_utc.date(), account_snapshot.total_equity, account_snapshot.total_equity, Decimal(0), Decimal(0), Decimal(0), Decimal(0), _public_payload(()), _public_payload(()), account_snapshot.open_order_count, None, None, market_at, account_snapshot.fetched_at_utc, reconciliation_at, str(reconciliation.payload["status"]), clock_skew, created_at_utc, 0, _public_payload(dict(account_snapshot.balances)), _public_payload(dict(account_snapshot.positions)), account_snapshot.snapshot_id, reconciliation_id, market.source_id, market_evidence_hash, created_at_utc, 0),
                risk_hash,
            )
            if fail_at == "risk_state": raise RuntimeError("injected risk-state failure")
        return True

    def persisted_preflight(self, preflight_report_id):
        return self._fetchone("SELECT * FROM execution.live_preflight_reports WHERE preflight_report_id=%s", (preflight_report_id,))

    @staticmethod
    def _risk_state(row) -> LiveRuntimeRiskState:
        return LiveRuntimeRiskState(
            row["live_run_id"], row["trading_day"], Decimal(str(row["current_equity"])), Decimal(str(row["high_watermark_equity"])),
            Decimal(str(row["daily_submitted_notional"])), Decimal(str(row["daily_realized_pnl"])), Decimal(str(row["gross_exposure"])), Decimal(str(row["net_exposure"])),
            tuple(_utc(item) for item in _json_value(row["order_rate_window_jsonb"])), tuple(_utc(item) for item in _json_value(row["cancellation_rate_window_jsonb"])),
            int(row["open_order_count"]), row["oldest_unknown_order_at_utc"], row["oldest_unacknowledged_order_at_utc"], row["latest_market_data_at_utc"], row["latest_account_snapshot_at_utc"], row["latest_reconciliation_at_utc"], LiveReconciliationStatus(row["latest_reconciliation_status"]), Decimal(str(row["clock_skew_seconds"])), row["run_started_at_utc"], int(row["transport_failure_count"]), _json_value(row["balances_jsonb"]), _json_value(row["positions_jsonb"]), int(row["version"]),
        )

    def load_risk_state(self, live_run_id):
        row = self._fetchone("SELECT * FROM execution.live_run_risk_state WHERE live_run_id=%s", (live_run_id,))
        if row is None: raise LookupError("live risk state is missing")
        return self._risk_state(row)

    def prepare_operational_dry_run(self, *, intent, configuration, approval, market_evidence, request_body, provider_request_hash: str, created_at_utc, fail_at=None, caller_risk_state=None):
        """Lock and derive all risk/reservation authority inside one PostgreSQL transaction."""
        del caller_risk_state
        body = dict(request_body)
        expected_text = {
            "instId": intent.series_identity.provider_instrument_id,
            "tdMode": "cash",
            "clOrdId": intent.client_order_id,
            "side": intent.side.value,
            "ordType": "limit",
        }
        if (
            set(body) != set(expected_text).union({"px", "sz"})
            or any(body.get(name) != value for name, value in expected_text.items())
            or Decimal(str(body.get("px"))) != intent.limit_price
            or Decimal(str(body.get("sz"))) != intent.quantity
            or provider_request_hash != sha256_payload({"method": "POST", "path": "/api/v5/trade/order", "body": body})
        ):
            raise ValueError("dispatch request method, path, body, or hash does not match the intent")
        with self.transaction():
            self._lock_run(intent.live_run_id)
            run = self._fetchone("SELECT * FROM execution.live_runs WHERE live_run_id=%s FOR UPDATE", (intent.live_run_id,))
            manifest = self._fetchone("SELECT * FROM execution.live_run_manifests WHERE manifest_id=%s FOR SHARE", (intent.manifest_id,))
            risk_row = self._fetchone("SELECT * FROM execution.live_run_risk_state WHERE live_run_id=%s FOR UPDATE", (intent.live_run_id,))
            approval_row = self._fetchone("SELECT * FROM execution.live_approvals WHERE approval_id=%s FOR UPDATE", (manifest["approval_id"],)) if manifest else None
            kill = self._fetchone("SELECT * FROM execution.live_kill_switches WHERE live_run_id=%s FOR UPDATE", (intent.live_run_id,))
            snapshot = self._fetchone("SELECT * FROM execution.live_account_snapshots WHERE account_snapshot_id=%s FOR SHARE", (risk_row["latest_account_snapshot_id"],)) if risk_row else None
            reconciliation = self._fetchone("SELECT * FROM execution.live_reconciliations WHERE reconciliation_id=%s FOR SHARE", (risk_row["latest_reconciliation_id"],)) if risk_row and risk_row["latest_reconciliation_id"] else None
            market_source = self._fetchone("SELECT * FROM execution.live_preflight_sources WHERE source_id=%s FOR SHARE", (risk_row["latest_market_evidence_id"],)) if risk_row else None
            report = self._fetchone("SELECT * FROM execution.live_preflight_reports WHERE preflight_report_id=%s FOR SHARE", (manifest["preflight_report_id"],)) if manifest else None
            if any(value is None for value in (run, manifest, risk_row, approval_row, kill, snapshot, reconciliation, market_source, report)):
                raise PermissionError("PostgreSQL operational authority is incomplete")
            if run["state"] != "dry_run_running" or run["production_write_enabled"] or manifest["production_write_enabled"] or not run["dry_run"] or not manifest["dry_run"]:
                raise PermissionError("persisted Phase 8A authority is not write-disabled dry-run")
            if manifest["live_run_id"] != intent.live_run_id or manifest["configuration_sha256"] != configuration.configuration_hash:
                raise PermissionError("intent configuration or manifest authority mismatch")
            if approval_row["approval_id"] != approval.approval_id or approval_row["record_sha256"] != approval.record_hash or approval_row["preflight_report_id"] != report["preflight_report_id"]:
                raise PermissionError("broker approval does not match PostgreSQL authority")
            if report["status"] != "passed" or approval_row["expires_at_utc"] <= created_at_utc:
                raise PermissionError("passed preflight and unexpired approval are required")
            if kill["state"] != LiveKillState.ARMED.value:
                raise PermissionError("persisted kill switch rejects new intent")
            if intent.account_snapshot_hash != snapshot["record_sha256"] or intent.reconciliation_hash != reconciliation["record_sha256"]:
                raise PermissionError("intent account or reconciliation authority is stale or mismatched")
            market_payload = _json_value(market_source["source_payload_jsonb"])
            if intent.market_evidence_hash != risk_row["latest_market_evidence_sha256"] or str(market_payload.get("market_evidence_sha256")) != risk_row["latest_market_evidence_sha256"]:
                raise PermissionError("intent market evidence is not current PostgreSQL authority")

            existing = self._fetchone("SELECT record_sha256,state FROM execution.live_order_intents WHERE order_intent_id=%s FOR UPDATE", (intent.order_intent_id,))
            if existing is not None:
                if str(existing["record_sha256"]) != intent.record_hash: raise LiveConflictError("live intent replay changed immutable economics")
                outbox = self._fetchone("SELECT dispatch_outbox_id FROM execution.live_dispatch_outbox WHERE order_intent_id=%s", (intent.order_intent_id,))
                return {"outbox_id": None if outbox is None else outbox["dispatch_outbox_id"], "risk_decision": self._fetchone("SELECT * FROM execution.live_runtime_risk_decisions WHERE order_intent_id=%s", (intent.order_intent_id,)), "replayed": True}

            state = self._risk_state(risk_row)
            risk = evaluate_live_risk(
                intent=intent, market_evidence=market_evidence, configuration=configuration, state=state,
                approval=approval, approval_consumed_notional=Decimal(str(approval_row["consumed_notional"])),
                kill_switch_state=LiveKillState(kill["state"]), evaluated_at_utc=created_at_utc,
            )
            reservation = calculate_live_reservation(intent=intent, risk_decision=risk, maximum_fee_bps=configuration.maximum_fee_bps)
            balances = state.balances
            available = Decimal(str(balances.get(reservation.currency, {}).get("available", "0")))
            already_reserved_row = self._fetchone("SELECT COALESCE(sum(remaining_amount),0) AS amount FROM execution.live_reservations WHERE live_run_id=%s AND currency=%s AND state='projected'", (intent.live_run_id, reservation.currency))
            already_reserved = Decimal(str(already_reserved_row["amount"]))
            if risk.accepted and reservation.original_amount > available - already_reserved:
                reason = f"insufficient_{reservation.currency.lower()}_balance"
                risk = LiveRiskDecision(risk.order_intent_id, False, (reason,), risk.market_evidence_price, risk.risk_reference_price, risk.worst_case_order_price, risk.risk_notional, risk.reservation_notional, risk.price_deviation_bps, risk.price_source_hash, risk.calculator_version, risk.evaluated_at_utc)
            state_value = LiveOrderState.DRY_RUN_PREPARED if risk.accepted else LiveOrderState.DRY_RUN_BLOCKED
            self._strict_insert(
                "execution.live_order_intents", "order_intent_id", intent.order_intent_id,
                ("live_run_id", "manifest_id", "client_order_id", "instrument_id", "side", "order_type", "accounting_mode", "quantity", "limit_price", "reference_price", "market_evidence_id", "market_evidence_sha256", "instrument_metadata_sha256", "account_snapshot_sha256", "reconciliation_sha256", "economic_sha256", "state", "created_at_utc"),
                (intent.live_run_id, intent.manifest_id, intent.client_order_id, intent.series_identity.provider_instrument_id, intent.side.value, intent.order_type.value, intent.accounting_mode.value, intent.quantity, intent.limit_price, intent.reference_price, intent.market_evidence_id, intent.market_evidence_hash, intent.instrument_metadata_hash, intent.account_snapshot_hash, intent.reconciliation_hash, intent.economic_hash, state_value.value, intent.created_at_utc), intent.record_hash,
            )
            if fail_at == "intent": raise RuntimeError("injected intent failure")
            self._strict_insert(
                "execution.live_runtime_risk_decisions", "risk_decision_id", risk.decision_id,
                ("order_intent_id", "accepted", "reasons_jsonb", "market_evidence_price", "risk_reference_price", "worst_case_order_price", "risk_notional", "reservation_notional", "price_deviation_bps", "price_source_sha256", "calculator_version", "decided_at_utc", "live_run_id"),
                (intent.order_intent_id, risk.accepted, _public_payload(risk.reasons), risk.market_evidence_price, risk.risk_reference_price, risk.worst_case_order_price, risk.risk_notional, risk.reservation_notional, risk.price_deviation_bps, risk.price_source_hash, risk.calculator_version, risk.evaluated_at_utc, intent.live_run_id), risk.record_hash,
            )
            if fail_at == "risk": raise RuntimeError("injected risk failure")
            if not risk.accepted:
                return {"outbox_id": None, "risk_decision": risk, "replayed": False}

            consumed = Decimal(str(approval_row["consumed_notional"])) + risk.risk_notional
            if consumed > Decimal(str(approval_row["maximum_total_approved_notional"])):
                raise PermissionError("persisted approval notional is exhausted")
            self._execute("UPDATE execution.live_approvals SET consumed_notional=%s WHERE approval_id=%s", (consumed, approval.approval_id))
            self._strict_insert(
                "execution.live_reservations", "reservation_id", reservation.reservation_id,
                ("order_intent_id", "currency", "amount", "risk_notional", "state", "dry_run", "created_at_utc", "updated_at_utc", "version", "live_run_id", "original_amount", "remaining_amount", "original_quantity", "remaining_quantity", "worst_case_price", "maximum_fee_bps", "maximum_fee_amount", "fee_currency_policy", "reservation_notional", "calculator_version", "source_hashes_jsonb"),
                (intent.order_intent_id, reservation.currency, reservation.original_amount, reservation.risk_notional, "projected", True, created_at_utc, created_at_utc, 0, intent.live_run_id, reservation.original_amount, reservation.remaining_amount, reservation.original_quantity, reservation.remaining_quantity, reservation.worst_case_price, reservation.maximum_fee_bps, reservation.maximum_fee_amount, reservation.fee_currency_policy, reservation.reservation_notional, reservation.calculator_version, _public_payload(dict(reservation.source_hashes))), reservation.record_hash,
            )
            if fail_at == "reservation": raise RuntimeError("injected reservation failure")
            outbox_id = live_uuid("dispatch-outbox", {"intent": intent.order_intent_id})
            outbox_hash = sha256_payload({"outbox": outbox_id, "request": provider_request_hash, "body": request_body})
            self._strict_insert(
                "execution.live_dispatch_outbox", "dispatch_outbox_id", outbox_id,
                ("order_intent_id", "client_order_id", "state", "provider_request_sha256", "request_jsonb", "request_method", "request_path", "worker_identity", "claim_token", "lease_expires_at_utc", "recovery_generation", "recovery_claim_token", "recovery_worker_identity", "recovery_lease_expires_at_utc", "created_at_utc", "updated_at_utc", "suppressed_at_utc", "version", "live_run_id"),
                (intent.order_intent_id, intent.client_order_id, "dry_run_prepared", provider_request_hash, _public_payload(request_body), "POST", "/api/v5/trade/order", None, None, None, 0, None, None, None, created_at_utc, created_at_utc, None, 0, intent.live_run_id), outbox_hash,
            )
            self._strict_insert(
                "execution.live_order_projections", "order_intent_id", intent.order_intent_id,
                ("live_run_id", "state", "filled_quantity", "fees", "latest_observation_id", "updated_at_utc", "version"),
                (intent.live_run_id, "dry_run_prepared", Decimal(0), Decimal(0), None, created_at_utc, 0),
                sha256_payload({"intent": intent.order_intent_id, "state": "dry_run_prepared"}),
            )
            self._dispatch_event(outbox_id, intent.live_run_id, "prepared", {"request_hash": provider_request_hash}, created_at_utc)
            timestamps = tuple(state.order_timestamps_utc) + (created_at_utc,)
            signed = risk.risk_notional if intent.side.value == "buy" else -risk.risk_notional
            risk_hash = sha256_payload({"prior": risk_row["record_sha256"], "intent": intent.record_hash, "decision": risk.record_hash})
            self._execute(
                "UPDATE execution.live_run_risk_state SET daily_submitted_notional=daily_submitted_notional+%s,gross_exposure=gross_exposure+%s,net_exposure=net_exposure+%s,order_rate_window_jsonb=%s,open_order_count=open_order_count+1,updated_at_utc=%s,record_sha256=%s,version=version+1 WHERE live_run_id=%s AND version=%s",
                (risk.risk_notional, risk.risk_notional, signed, _public_payload(timestamps), created_at_utc, risk_hash, intent.live_run_id, risk_row["version"]),
            )
            if fail_at == "outbox": raise RuntimeError("injected outbox failure")
            return {"outbox_id": outbox_id, "risk_decision": risk, "reservation": reservation, "replayed": False}

    def prepare_dry_run_bundle(self, **kwargs):
        if "risk_decision" in kwargs:
            raise TypeError("caller-provided LiveRiskDecision is not operational authority")
        return self.prepare_operational_dry_run(**kwargs)

    def _dispatch_event(self, outbox_id, live_run_id, event_type, payload, at_utc):
        event_id = live_uuid("dispatch-event", {"outbox": outbox_id, "type": event_type, "payload": payload})
        self._strict_insert(
            "execution.live_dispatch_events", "dispatch_event_id", event_id,
            ("dispatch_outbox_id", "event_type", "event_jsonb", "occurred_at_utc", "live_run_id"),
            (outbox_id, event_type, _public_payload(payload), at_utc, live_run_id),
            sha256_payload({"event": event_id, "at": at_utc, "payload": payload}),
        )

    def claim_dispatch(self, *, worker_identity: str, at_utc, lease_seconds: int = 30, outbox_id=None):
        if lease_seconds <= 0: raise ValueError("lease_seconds must be positive")
        with self.transaction():
            sql = "SELECT * FROM execution.live_dispatch_outbox WHERE state='dry_run_prepared' AND (lease_expires_at_utc IS NULL OR lease_expires_at_utc<=%s)" + (" AND dispatch_outbox_id=%s" if outbox_id is not None else "") + " ORDER BY created_at_utc FOR UPDATE SKIP LOCKED LIMIT 1"
            row = self._fetchone(sql, (at_utc, outbox_id) if outbox_id is not None else (at_utc,))
            if row is None: return None
            token = uuid4(); lease = at_utc + timedelta(seconds=lease_seconds)
            changed = self._execute("UPDATE execution.live_dispatch_outbox SET worker_identity=%s,claim_token=%s,lease_expires_at_utc=%s,updated_at_utc=%s,version=version+1 WHERE dispatch_outbox_id=%s AND version=%s", (worker_identity, token, lease, at_utc, row["dispatch_outbox_id"], row["version"]))
            if changed != 1: raise LiveClaimError("dispatch claim lost")
            self._dispatch_event(row["dispatch_outbox_id"], row["live_run_id"], "claimed", {"worker": worker_identity, "claim_token": str(token)}, at_utc)
            return row["dispatch_outbox_id"], token

    def dispatch_state(self, outbox_id):
        row = self._fetchone("SELECT state FROM execution.live_dispatch_outbox WHERE dispatch_outbox_id=%s", (outbox_id,))
        return None if row is None else str(row["state"])

    def suppress_claimed_dispatch(self, *, outbox_id, claim_token, worker_identity: str, at_utc):
        with self.transaction():
            row = self._fetchone("SELECT * FROM execution.live_dispatch_outbox WHERE dispatch_outbox_id=%s FOR UPDATE", (outbox_id,))
            if row is None or row["claim_token"] != claim_token or row["worker_identity"] != worker_identity or row["lease_expires_at_utc"] <= at_utc:
                raise LiveClaimError("dispatch suppression requires the active lease owner")
            changed = self._execute("UPDATE execution.live_dispatch_outbox SET state='dry_run_suppressed',worker_identity=NULL,claim_token=NULL,lease_expires_at_utc=NULL,suppressed_at_utc=%s,updated_at_utc=%s,version=version+1 WHERE dispatch_outbox_id=%s AND version=%s", (at_utc, at_utc, outbox_id, row["version"]))
            if changed != 1: raise LiveClaimError("dispatch suppression lost")
            projection = self._fetchone("SELECT * FROM execution.live_order_projections WHERE order_intent_id=%s FOR UPDATE", (row["order_intent_id"],))
            self._execute("UPDATE execution.live_order_projections SET state='dry_run_suppressed',updated_at_utc=%s,record_sha256=%s,version=version+1 WHERE order_intent_id=%s AND version=%s", (at_utc, sha256_payload({"intent": row["order_intent_id"], "state": "dry_run_suppressed"}), row["order_intent_id"], projection["version"]))
            self._execute("UPDATE execution.live_order_intents SET state='dry_run_suppressed' WHERE order_intent_id=%s", (row["order_intent_id"],))
            self._dispatch_event(outbox_id, row["live_run_id"], "write_suppressed", {"external_write_attempted": False}, at_utc)
            attempt_id = live_uuid("transport-attempt", {"outbox": outbox_id, "result": "write_suppressed"})
            self._strict_insert(
                "execution.live_transport_attempts", "transport_attempt_id", attempt_id,
                ("live_run_id", "order_intent_id", "operation", "provider_request_sha256", "provider_response_sha256", "result", "external_write_attempted", "successful_write", "attempted_at_utc"),
                (row["live_run_id"], row["order_intent_id"], "submit_limit_order", row["provider_request_sha256"], None, "write_suppressed", False, False, at_utc),
                sha256_payload({"attempt": attempt_id, "suppressed": True}),
            )
            return True

    def mark_pending_recovery(self, *, outbox_id, claim_token, worker_identity: str, at_utc):
        with self.transaction():
            row = self._fetchone("SELECT * FROM execution.live_dispatch_outbox WHERE dispatch_outbox_id=%s FOR UPDATE", (outbox_id,))
            if row is None or row["state"] != "dry_run_prepared" or row["claim_token"] != claim_token or row["worker_identity"] != worker_identity or row["lease_expires_at_utc"] <= at_utc:
                raise LiveClaimError("pending recovery requires the active dispatch lease owner")
            self._execute("UPDATE execution.live_dispatch_outbox SET state='pending_recovery',worker_identity=NULL,claim_token=NULL,lease_expires_at_utc=NULL,updated_at_utc=%s,version=version+1 WHERE dispatch_outbox_id=%s AND version=%s", (at_utc, outbox_id, row["version"]))
            projection = self._fetchone("SELECT * FROM execution.live_order_projections WHERE order_intent_id=%s FOR UPDATE", (row["order_intent_id"],))
            self._execute("UPDATE execution.live_order_projections SET state='pending_recovery',updated_at_utc=%s,record_sha256=%s,version=version+1 WHERE order_intent_id=%s AND version=%s", (at_utc, sha256_payload({"intent": row["order_intent_id"], "state": "pending_recovery"}), row["order_intent_id"], projection["version"]))
            self._execute("UPDATE execution.live_order_intents SET state='pending_recovery' WHERE order_intent_id=%s", (row["order_intent_id"],))
        return True

    def claim_recovery(self, *, worker_identity: str, at_utc, lease_seconds: int = 30, outbox_id=None):
        if lease_seconds <= 0: raise ValueError("lease_seconds must be positive")
        with self.transaction():
            sql = "SELECT * FROM execution.live_dispatch_outbox WHERE state='pending_recovery' AND (recovery_lease_expires_at_utc IS NULL OR recovery_lease_expires_at_utc<=%s)" + (" AND dispatch_outbox_id=%s" if outbox_id is not None else "") + " ORDER BY created_at_utc FOR UPDATE SKIP LOCKED LIMIT 1"
            row = self._fetchone(sql, (at_utc, outbox_id) if outbox_id is not None else (at_utc,))
            if row is None: return None
            token = uuid4(); lease = at_utc + timedelta(seconds=lease_seconds); generation = int(row["recovery_generation"]) + 1
            self._execute("UPDATE execution.live_dispatch_outbox SET recovery_generation=%s,recovery_claim_token=%s,recovery_worker_identity=%s,recovery_lease_expires_at_utc=%s,updated_at_utc=%s,version=version+1 WHERE dispatch_outbox_id=%s AND version=%s", (generation, token, worker_identity, lease, at_utc, row["dispatch_outbox_id"], row["version"]))
            recovery_id = live_uuid("recovery-record", {"outbox": row["dispatch_outbox_id"], "generation": generation})
            self._strict_insert(
                "execution.live_recovery_records", "recovery_record_id", recovery_id,
                ("live_run_id", "order_intent_id", "client_order_id", "generation", "worker_identity", "claim_token", "lease_expires_at_utc", "query_first", "observation_bundle_sha256", "state", "created_at_utc", "updated_at_utc", "outcome", "manual_intervention_required"),
                (row["live_run_id"], row["order_intent_id"], row["client_order_id"], generation, worker_identity, token, lease, True, None, "claimed", at_utc, at_utc, None, False),
                sha256_payload({"recovery": recovery_id, "worker": worker_identity, "claim": token, "lease": lease, "state": "claimed"}),
            )
            self._dispatch_event(row["dispatch_outbox_id"], row["live_run_id"], "recovery_claimed", {"generation": generation, "worker": worker_identity, "claim_token": str(token)}, at_utc)
            return row["dispatch_outbox_id"], token, generation

    def _append_kill_event(self, *, kill, prior_state, new_state, reason, evidence, at_utc):
        event_id = live_uuid("kill-event", {"kill": kill["kill_switch_id"], "prior": prior_state, "new": new_state, "at": at_utc, "evidence": evidence})
        self._strict_insert(
            "execution.live_kill_events", "kill_event_id", event_id,
            ("kill_switch_id", "prior_state", "new_state", "reason", "evidence_jsonb", "occurred_at_utc", "live_run_id"),
            (kill["kill_switch_id"], prior_state, new_state, reason, _public_payload(evidence), at_utc, kill["live_run_id"]),
            sha256_payload({"event": event_id, "evidence": evidence}),
        )

    def trigger_kill(self, *, live_run_id, reason: str, evidence, at_utc):
        with self.transaction():
            self._lock_run(live_run_id)
            kill = self._fetchone("SELECT * FROM execution.live_kill_switches WHERE live_run_id=%s FOR UPDATE", (live_run_id,))
            if kill is None: raise LookupError("kill switch is missing")
            if kill["state"] == "stopped": return "stopped"
            if kill["state"] == "triggered":
                return self.stop_kill(live_run_id=live_run_id, reason=reason, evidence=evidence, at_utc=at_utc)
            if kill["state"] != "armed":
                raise PermissionError("kill trigger requires armed authority")
            digest = sha256_payload({"reason": reason, "evidence": evidence})
            self._execute("UPDATE execution.live_kill_switches SET state='triggered',reason=%s,evidence_sha256=%s,requires_fresh_preflight=true,requires_new_approval=true,updated_at_utc=%s,triggered_at_utc=%s,record_sha256=%s,version=version+1 WHERE kill_switch_id=%s", (reason, digest, at_utc, at_utc, digest, kill["kill_switch_id"]))
            self._append_kill_event(kill=kill, prior_state="armed", new_state="triggered", reason=reason, evidence=evidence, at_utc=at_utc)
            return self.stop_kill(live_run_id=live_run_id, reason=reason, evidence=evidence, at_utc=at_utc)

    def stop_kill(self, *, live_run_id, reason: str, evidence, at_utc):
        with self.transaction():
            self._lock_run(live_run_id)
            kill = self._fetchone("SELECT * FROM execution.live_kill_switches WHERE live_run_id=%s FOR UPDATE", (live_run_id,))
            if kill is None: raise LookupError("kill switch is missing")
            if kill["state"] == "stopped": return "stopped"
            if kill["state"] not in ("triggered", "cancellation_in_progress", "cancellation_ambiguous"):
                raise PermissionError("kill stop requires a triggered authority")
            digest = sha256_payload({"reason": reason, "evidence": evidence, "stop": True})
            self._execute("UPDATE execution.live_kill_switches SET state='stopped',reason=%s,evidence_sha256=%s,requires_fresh_preflight=true,requires_new_approval=true,updated_at_utc=%s,record_sha256=%s,version=version+1 WHERE kill_switch_id=%s", (reason, digest, at_utc, digest, kill["kill_switch_id"]))
            self._append_kill_event(kill=kill, prior_state=kill["state"], new_state="stopped", reason=reason, evidence=evidence, at_utc=at_utc)
            self._execute("UPDATE execution.live_runs SET state='stopped',version=version+1 WHERE live_run_id=%s AND state<>'stopped'", (live_run_id,))
            return "stopped"

    def reset_kill(self, *, live_run_id, fresh_preflight_report_id, new_approval_id, at_utc):
        with self.transaction():
            self._lock_run(live_run_id)
            kill = self._fetchone("SELECT * FROM execution.live_kill_switches WHERE live_run_id=%s FOR UPDATE", (live_run_id,))
            report = self._fetchone("SELECT * FROM execution.live_preflight_reports WHERE preflight_report_id=%s AND live_run_id=%s FOR SHARE", (fresh_preflight_report_id, live_run_id))
            approval = self._fetchone("SELECT * FROM execution.live_approvals WHERE approval_id=%s AND live_run_id=%s FOR UPDATE", (new_approval_id, live_run_id))
            if kill is None or kill["state"] != "stopped" or kill["triggered_at_utc"] is None:
                raise PermissionError("only a durably stopped kill switch can reset")
            triggered = kill["triggered_at_utc"]
            if report is None or report["status"] != "passed" or report["evaluated_at_utc"] <= triggered:
                raise PermissionError("kill reset requires a post-trigger passed preflight")
            if approval is None or approval["created_at_utc"] <= triggered or approval["preflight_report_id"] != report["preflight_report_id"] or Decimal(str(approval["consumed_notional"])) != 0:
                raise PermissionError("kill reset requires a new unconsumed approval bound to fresh preflight")
            evidence = {"preflight_report_id": str(fresh_preflight_report_id), "approval_id": str(new_approval_id)}
            digest = sha256_payload(evidence)
            self._execute("UPDATE execution.live_kill_switches SET state='reset_pending',reason='manual',evidence_sha256=%s,updated_at_utc=%s,reset_preflight_report_id=%s,reset_approval_id=%s,record_sha256=%s,version=version+1 WHERE kill_switch_id=%s", (digest, at_utc, fresh_preflight_report_id, new_approval_id, digest, kill["kill_switch_id"]))
            pending = dict(kill); pending["state"] = "reset_pending"; pending["version"] = int(kill["version"]) + 1
            self._append_kill_event(kill=kill, prior_state="stopped", new_state="reset_pending", reason="manual", evidence=evidence, at_utc=at_utc)
            self._execute("UPDATE execution.live_kill_switches SET state='armed',updated_at_utc=%s,record_sha256=%s,version=version+1 WHERE kill_switch_id=%s", (at_utc, sha256_payload({"armed": evidence}), kill["kill_switch_id"]))
            self._execute("UPDATE execution.live_runs SET state='dry_run_running',version=version+1 WHERE live_run_id=%s AND state='stopped'", (live_run_id,))
            self._append_kill_event(kill=pending, prior_state="reset_pending", new_state="armed", reason="manual", evidence=evidence, at_utc=at_utc + timedelta(microseconds=1))
            return "armed"

    def persist_recovery_observation(self, *, outbox_id, claim_token, worker_identity: str, observation_bundle, at_utc):
        with self.transaction():
            row = self._fetchone("SELECT * FROM execution.live_dispatch_outbox WHERE dispatch_outbox_id=%s FOR UPDATE", (outbox_id,))
            if row is None or row["live_run_id"] != observation_bundle.live_run_id or row["client_order_id"] != observation_bundle.client_order_id:
                raise LiveClaimError("recovery observation does not match the durable outbox")
            expected = self._fetchone(
                "SELECT i.instrument_id AS instrument,i.client_order_id,i.side,i.quantity,i.limit_price,c.account_fingerprint FROM execution.live_order_intents i JOIN execution.live_run_manifests m ON m.manifest_id=i.manifest_id JOIN execution.live_configuration_snapshots c ON c.configuration_sha256=m.configuration_sha256 WHERE i.order_intent_id=%s AND i.live_run_id=%s FOR SHARE",
                (row["order_intent_id"], row["live_run_id"]),
            )
            if expected is None:
                raise LiveClaimError("recovery intent authority is missing")
            observation_bundle = normalize_recovery_observation(
                observation_bundle, expected_intent=expected, account_fingerprint=expected["account_fingerprint"]
            )
            observation_id = live_uuid("order-observation", {"run": observation_bundle.live_run_id, "client": observation_bundle.client_order_id, "queried_at": observation_bundle.queried_at_utc})
            existing = self._fetchone("SELECT record_sha256 FROM execution.live_order_observations WHERE order_observation_id=%s", (observation_id,))
            if existing is not None:
                if str(existing["record_sha256"]) != observation_bundle.record_hash: raise LiveConflictError("conflicting recovery observation replay")
                return observation_id
            if row["state"] != "pending_recovery" or row["recovery_claim_token"] != claim_token or row["recovery_worker_identity"] != worker_identity or row["recovery_lease_expires_at_utc"] <= at_utc:
                raise LiveClaimError("observation persistence requires the active recovery lease owner")
            payload = {"bundle_id": observation_bundle.bundle_id, "queried_order": observation_bundle.queried_order, "recent_orders": observation_bundle.recent_orders, "open_orders": observation_bundle.open_orders, "fills": observation_bundle.fills, "account_observation": dict(observation_bundle.account_observation), "outcome": observation_bundle.outcome.value}
            queried = observation_bundle.queried_order or {}
            self._strict_insert(
                "execution.live_order_observations", "order_observation_id", observation_id,
                ("live_run_id", "order_intent_id", "client_order_id", "provider_order_id", "provider_state", "observed_at_utc", "observation_jsonb", "provider_response_sha256"),
                (row["live_run_id"], row["order_intent_id"], row["client_order_id"], queried.get("ordId"), queried.get("state"), observation_bundle.queried_at_utc, _public_payload(payload), observation_bundle.record_hash), observation_bundle.record_hash,
            )
            for fill in observation_bundle.fills:
                provider_fill_id = str(fill.get("tradeId") or fill.get("fillId") or "")
                if not provider_fill_id: raise ValueError("recovery fill lacks a stable provider fill identity")
                quantity = Decimal(str(fill.get("fillSz") or fill.get("sz") or fill.get("quantity")))
                price = Decimal(str(fill.get("fillPx") or fill.get("px") or fill.get("price")))
                fee = abs(Decimal(str(fill.get("fee") or "0"))); fee_currency = str(fill.get("feeCcy") or fill.get("fee_currency") or "")
                if quantity <= 0 or price <= 0 or not fee_currency: raise ValueError("recovery fill is incomplete")
                fill_id = live_uuid("fill-observation", {"run": row["live_run_id"], "provider_fill_id": provider_fill_id})
                fill_hash = sha256_payload(dict(fill))
                self._strict_insert(
                    "execution.live_fill_observations", "fill_observation_id", fill_id,
                    ("live_run_id", "order_intent_id", "provider_fill_id", "provider_order_id", "client_order_id", "quantity", "price", "fee", "fee_currency", "observed_at_utc", "provider_response_sha256"),
                    (row["live_run_id"], row["order_intent_id"], provider_fill_id, fill.get("ordId"), fill.get("clOrdId") or row["client_order_id"], quantity, price, fee, fee_currency, observation_bundle.queried_at_utc, fill_hash), fill_hash,
                )
            generation = int(row["recovery_generation"]); recovery_id = live_uuid("recovery-record", {"outbox": outbox_id, "generation": generation})
            incident = observation_bundle.outcome in (LiveRecoveryOutcome.OBSERVED_EXTERNAL_ORDER, LiveRecoveryOutcome.OBSERVED_EXTERNAL_FILL)
            confirmed_absent = observation_bundle.outcome is LiveRecoveryOutcome.CONFIRMED_ABSENT
            recovery_state = "resolved" if incident or confirmed_absent else "ambiguous"
            recovery_hash = sha256_payload({"recovery": recovery_id, "bundle": observation_bundle.record_hash, "outcome": observation_bundle.outcome.value})
            changed = self._execute("UPDATE execution.live_recovery_records SET observation_bundle_sha256=%s,state=%s,updated_at_utc=%s,record_sha256=%s,outcome=%s,manual_intervention_required=%s WHERE recovery_record_id=%s AND claim_token=%s AND worker_identity=%s", (observation_bundle.record_hash, recovery_state, at_utc, recovery_hash, observation_bundle.outcome.value, incident, recovery_id, claim_token, worker_identity))
            if changed != 1: raise LiveClaimError("recovery record ownership lost")
            next_outbox = "unexpected_external_side_effect" if incident else ("dry_run_suppressed" if confirmed_absent else "pending_recovery")
            next_projection = "incident_blocked" if incident else next_outbox
            self._execute("UPDATE execution.live_dispatch_outbox SET state=%s,recovery_claim_token=NULL,recovery_worker_identity=NULL,recovery_lease_expires_at_utc=NULL,suppressed_at_utc=%s,updated_at_utc=%s,version=version+1 WHERE dispatch_outbox_id=%s AND version=%s", (next_outbox, at_utc if confirmed_absent else None, at_utc, outbox_id, row["version"]))
            projection = self._fetchone("SELECT * FROM execution.live_order_projections WHERE order_intent_id=%s FOR UPDATE", (row["order_intent_id"],))
            self._execute("UPDATE execution.live_order_projections SET state=%s,latest_observation_id=%s,updated_at_utc=%s,record_sha256=%s,version=version+1 WHERE order_intent_id=%s AND version=%s", (next_projection, observation_id, at_utc, sha256_payload({"intent": row["order_intent_id"], "state": next_projection, "observation": observation_bundle.record_hash}), row["order_intent_id"], projection["version"]))
            intent_state = "unexpected_external_side_effect" if incident else next_outbox
            self._execute("UPDATE execution.live_order_intents SET state=%s WHERE order_intent_id=%s", (intent_state, row["order_intent_id"]))
            event_type = "unexpected_external_side_effect" if incident else "observation_persisted"
            self._dispatch_event(outbox_id, row["live_run_id"], event_type, {"generation": generation, "observation_bundle_sha256": observation_bundle.record_hash, "outcome": observation_bundle.outcome.value}, at_utc)
            if incident:
                self.trigger_kill(live_run_id=row["live_run_id"], reason="unexpected_fill" if observation_bundle.outcome is LiveRecoveryOutcome.OBSERVED_EXTERNAL_FILL else "unexpected_venue_order", evidence={"observation_id": str(observation_id), "bundle_hash": observation_bundle.record_hash}, at_utc=at_utc)
                reconciliation_id = live_uuid("reconciliation", {"run": row["live_run_id"], "at": at_utc, "input": observation_bundle.record_hash})
                self._strict_insert("execution.live_reconciliations", "reconciliation_id", reconciliation_id, ("live_run_id", "status", "input_bundle_sha256", "exact_input_jsonb", "evaluated_at_utc"), (row["live_run_id"], "blocked", observation_bundle.record_hash, _public_payload({"unexpected_external_side_effect": payload}), at_utc), sha256_payload({"reconciliation": reconciliation_id, "blocked": True}))
            return observation_id

    def consume_reservation(self, *, reservation_id, amount: Decimal, quantity: Decimal, at_utc):
        if amount < 0 or quantity < 0: raise ValueError("reservation consumption cannot be negative")
        with self.transaction():
            row = self._fetchone("SELECT * FROM execution.live_reservations WHERE reservation_id=%s FOR UPDATE", (reservation_id,))
            if row is None: raise LookupError("reservation is missing")
            remaining_amount = Decimal(str(row["remaining_amount"])) - amount
            remaining_quantity = Decimal(str(row["remaining_quantity"])) - quantity
            if remaining_amount < 0 or remaining_quantity < 0: raise PermissionError("reservation consumption exceeds remaining authority")
            state = "consumed" if remaining_amount == 0 or remaining_quantity == 0 else "projected"
            self._execute("UPDATE execution.live_reservations SET remaining_amount=%s,remaining_quantity=%s,state=%s,updated_at_utc=%s,version=version+1 WHERE reservation_id=%s AND version=%s", (remaining_amount, remaining_quantity, state, at_utc, reservation_id, row["version"]))
            return remaining_amount, remaining_quantity

    def prepare_cancel_dry_run(self, *, live_run_id, order_intent_id, client_order_id: str, request_body, provider_request_hash: str, created_at_utc):
        with self.transaction():
            self._lock_run(live_run_id)
            kill = self._fetchone("SELECT * FROM execution.live_kill_switches WHERE live_run_id=%s FOR UPDATE", (live_run_id,))
            row = self._fetchone("SELECT i.live_run_id,i.instrument_id,i.client_order_id,m.dry_run,m.production_write_enabled FROM execution.live_order_intents i JOIN execution.live_run_manifests m ON m.manifest_id=i.manifest_id WHERE i.order_intent_id=%s FOR SHARE", (order_intent_id,))
            if row is None or row["live_run_id"] != live_run_id or not row["dry_run"] or row["production_write_enabled"] or kill is None:
                raise PermissionError("cancel outbox requires persisted dry-run authority")
            expected_body = {"instId": row["instrument_id"], "clOrdId": row["client_order_id"]}
            expected_hash = sha256_payload({"method": "POST", "path": "/api/v5/trade/cancel-order", "body": expected_body})
            if client_order_id != row["client_order_id"] or dict(request_body) != expected_body or provider_request_hash != expected_hash:
                raise ValueError("cancel request method, path, body, or hash does not match the intent")

            cancel_id = live_uuid("cancel-outbox", {"intent": order_intent_id, "request": provider_request_hash})
            cancel_hash = sha256_payload({"cancel": cancel_id, "request": provider_request_hash, "body": request_body})
            self._strict_insert("execution.live_cancel_outbox", "cancel_outbox_id", cancel_id, ("live_run_id", "order_intent_id", "client_order_id", "state", "provider_request_sha256", "request_jsonb", "request_method", "request_path", "worker_identity", "claim_token", "lease_expires_at_utc", "recovery_generation", "created_at_utc", "updated_at_utc"), (live_run_id, order_intent_id, client_order_id, "dry_run_prepared", provider_request_hash, _public_payload(request_body), "POST", "/api/v5/trade/cancel-order", None, None, None, 0, created_at_utc, created_at_utc), cancel_hash)
            self._execute("UPDATE execution.live_cancel_outbox SET state='dry_run_suppressed',updated_at_utc=%s WHERE cancel_outbox_id=%s AND state='dry_run_prepared'", (created_at_utc, cancel_id))
            attempt_id = live_uuid("transport-attempt", {"cancel": cancel_id, "result": "write_suppressed"})
            self._strict_insert("execution.live_transport_attempts", "transport_attempt_id", attempt_id, ("live_run_id", "order_intent_id", "operation", "provider_request_sha256", "provider_response_sha256", "result", "external_write_attempted", "successful_write", "attempted_at_utc"), (live_run_id, order_intent_id, "cancel_order", provider_request_hash, None, "write_suppressed", False, False, created_at_utc), sha256_payload({"attempt": attempt_id, "suppressed": True}))
            return cancel_id

    def build_local_projection(self, live_run_id, *, observed_at_utc):
        run = self._fetchone("SELECT r.*,m.manifest_jsonb FROM execution.live_runs r JOIN execution.live_run_manifests m ON m.manifest_id=r.manifest_id WHERE r.live_run_id=%s", (live_run_id,))
        risk = self._fetchone("SELECT * FROM execution.live_run_risk_state WHERE live_run_id=%s", (live_run_id,))
        if run is None or risk is None: raise LookupError("local projection authority is missing")
        orders = self._fetchall("SELECT order_intent_id,client_order_id,instrument_id,side,quantity,limit_price,state FROM execution.live_order_intents WHERE live_run_id=%s ORDER BY created_at_utc,order_intent_id", (live_run_id,))
        fills = self._fetchall("SELECT provider_fill_id,provider_order_id,client_order_id,quantity,price,fee,fee_currency FROM execution.live_fill_observations WHERE live_run_id=%s ORDER BY observed_at_utc,provider_fill_id", (live_run_id,))
        manifest = self._fetchone("SELECT account_fingerprint FROM execution.live_run_manifests m JOIN execution.live_configuration_snapshots c ON c.configuration_sha256=m.configuration_sha256 WHERE m.live_run_id=%s", (live_run_id,))
        source_ids = tuple(value for value in (risk["latest_account_snapshot_id"], risk["latest_reconciliation_id"], risk["latest_market_evidence_id"]) if value is not None) + tuple(row["order_intent_id"] for row in orders)
        return {"live_run_id": live_run_id, "account_fingerprint": manifest["account_fingerprint"], "orders": orders, "fills": fills, "balances": _json_value(risk["balances_jsonb"]), "positions": _json_value(risk["positions_jsonb"]), "sequence": int(run["version"]), "timestamp_utc": observed_at_utc, "source_ids": source_ids}

    def persist_reconciliation(self, reconciliation, *, exact_input):
        with self.transaction():
            self._strict_insert("execution.live_reconciliations", "reconciliation_id", reconciliation.reconciliation_id, ("live_run_id", "status", "input_bundle_sha256", "exact_input_jsonb", "evaluated_at_utc"), (reconciliation.live_run_id, reconciliation.status.value, reconciliation.input_bundle_hash, _public_payload(exact_input), reconciliation.evaluated_at_utc), reconciliation.record_hash)
            for difference in reconciliation.differences:
                difference_id = live_uuid("reconciliation-difference", {"reconciliation": reconciliation.reconciliation_id, "difference": difference})
                self._strict_insert("execution.live_reconciliation_differences", "reconciliation_difference_id", difference_id, ("reconciliation_id", "field_name", "material", "local_value_jsonb", "venue_value_jsonb", "live_run_id"), (reconciliation.reconciliation_id, str(difference.get("field")), bool(difference.get("material", True)), _public_payload(difference.get("local")), _public_payload(difference.get("venue")), reconciliation.live_run_id), sha256_payload({"difference": difference_id, "payload": difference}))
        return reconciliation.reconciliation_id

    def persist_summary(self, summary):
        table = "execution.live_pre_run_summaries" if summary.summary_type == "pre_run" else "execution.live_post_run_summaries"
        columns = ("live_run_id", "generated_at_utc", "public_summary_jsonb", "evidence_ids")
        values = (summary.live_run_id, summary.generated_at_utc, _public_payload(dict(summary.public_payload)), list(summary.evidence_ids))
        if summary.summary_type == "post_run":
            columns += ("external_write_attempted", "external_write_suppressed")
            values += (False, bool(summary.public_payload.get("external_write_suppressed")))
        with self.transaction():
            return self._strict_insert(table, "summary_id", summary.summary_id, columns, values, summary.record_hash)

    def status(self, live_run_id):
        return self._fetchone("SELECT r.live_run_id,r.state,r.dry_run,r.production_write_enabled,r.started_at_utc,r.completed_at_utc,k.state AS kill_state,k.reason AS kill_reason,m.manifest_id,m.configuration_sha256,m.approval_id,m.preflight_report_id FROM execution.live_runs r JOIN execution.live_run_manifests m ON m.manifest_id=r.manifest_id JOIN execution.live_kill_switches k ON k.live_run_id=r.live_run_id WHERE r.live_run_id=%s", (live_run_id,))

    def reconstruct(self, live_run_id):
        run = self._fetchone("SELECT * FROM execution.live_runs WHERE live_run_id=%s", (live_run_id,))
        manifest = self._fetchone("SELECT * FROM execution.live_run_manifests WHERE live_run_id=%s", (live_run_id,))
        if run is None or manifest is None: raise LookupError("live run cannot be reconstructed")
        if run["production_write_enabled"] or manifest["production_write_enabled"] or not run["dry_run"] or not manifest["dry_run"]: raise PermissionError("reconstructed authority is not dry-run/write-disabled")
        configuration = self._fetchone("SELECT * FROM execution.live_configuration_snapshots WHERE configuration_sha256=%s", (manifest["configuration_sha256"],))
        credential = self._fetchone("SELECT * FROM execution.live_credential_references WHERE credential_reference_id=%s", (manifest["credential_reference_id"],))
        account = self._fetchone("SELECT * FROM execution.live_account_snapshots WHERE account_snapshot_id=%s AND live_run_id=%s", (manifest["initial_account_snapshot_id"], live_run_id))
        preflight = self._fetchone("SELECT * FROM execution.live_preflight_reports WHERE preflight_report_id=%s AND live_run_id=%s", (manifest["preflight_report_id"], live_run_id))
        approval = self._fetchone("SELECT * FROM execution.live_approvals WHERE approval_id=%s AND live_run_id=%s", (manifest["approval_id"], live_run_id))
        kill = self._fetchone("SELECT * FROM execution.live_kill_switches WHERE live_run_id=%s", (live_run_id,))
        risk = self._fetchone("SELECT * FROM execution.live_run_risk_state WHERE live_run_id=%s", (live_run_id,))
        required = (configuration, credential, account, preflight, approval, kill, risk)
        if any(row is None for row in required): raise LookupError("live runtime authority is incomplete")
        if preflight["status"] != "passed" or manifest["configuration_sha256"] != approval["configuration_sha256"] or manifest["preflight_report_id"] != approval["preflight_report_id"]: raise PermissionError("live runtime authority chain is inconsistent")
        return {
            "configuration": configuration, "credential_reference": credential, "account_snapshot": account,
            "preflight_report": preflight, "preflight_checks": self._fetchall("SELECT * FROM execution.live_preflight_checks WHERE preflight_report_id=%s AND live_run_id=%s ORDER BY check_ordinal", (preflight["preflight_report_id"], live_run_id)),
            "approval": approval, "manifest": manifest, "run": run, "kill_switch": kill, "risk_state": risk,
            "reservations": self._fetchall("SELECT * FROM execution.live_reservations WHERE live_run_id=%s ORDER BY created_at_utc", (live_run_id,)),
            "dispatch_outboxes": self._fetchall("SELECT * FROM execution.live_dispatch_outbox WHERE live_run_id=%s ORDER BY created_at_utc", (live_run_id,)),
            "cancel_outboxes": self._fetchall("SELECT * FROM execution.live_cancel_outbox WHERE live_run_id=%s ORDER BY created_at_utc", (live_run_id,)),
            "recovery_claims": self._fetchall("SELECT * FROM execution.live_recovery_records WHERE live_run_id=%s ORDER BY created_at_utc", (live_run_id,)),
            "reconciliations": self._fetchall("SELECT * FROM execution.live_reconciliations WHERE live_run_id=%s ORDER BY evaluated_at_utc", (live_run_id,)),
            "pre_run_summary": self._fetchone("SELECT * FROM execution.live_pre_run_summaries WHERE live_run_id=%s", (live_run_id,)),
            "post_run_summary": self._fetchone("SELECT * FROM execution.live_post_run_summaries WHERE live_run_id=%s", (live_run_id,)),
            "pending_recovery_count": int(self._fetchone("SELECT count(*) AS count FROM execution.live_dispatch_outbox WHERE live_run_id=%s AND state='pending_recovery'", (live_run_id,))["count"]),
        }


__all__ = ["LiveConflictError", "LiveClaimError", "DurablePostgresLiveRepository"]
