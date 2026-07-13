"""PostgreSQL-authoritative durable live evidence and dry-run outbox."""
from __future__ import annotations

import json
import re
from contextlib import contextmanager
from datetime import timedelta
from decimal import Decimal
from typing import Mapping
from uuid import uuid4

from secure_eval_wrapper.data_collection.hashing import canonical_json_dumps, sha256_payload
from secure_eval_wrapper.storage.postgres.alpha_signal_base import _json_param

from .credentials import redact
from .models import LiveOrderState, live_uuid


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
            if row is None: return None
            if isinstance(row, Mapping): return dict(row)
            names = tuple(getattr(item, "name", item[0]) for item in cursor.description)
            return dict(zip(names, row))
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

    def persist_start_bundle(self, *, configuration, credential_reference, account_snapshot, report, approval, manifest, kill_switch, created_at_utc, fail_at=None):
        with self.transaction():
            self._lock_run(manifest.live_run_id)
            configuration_id = live_uuid("configuration", {"hash": configuration.configuration_hash})
            configuration_payload = {name: getattr(configuration, name) for name in configuration.__dataclass_fields__}
            self._strict_insert("execution.live_configuration_snapshots", "configuration_snapshot_id", configuration_id, ("configuration_sha256","provider","environment","account_fingerprint","dry_run","read_only_preflight","production_write_enabled","configuration_jsonb","created_at_utc"), (configuration.configuration_hash, configuration.provider, configuration.environment, configuration.account_fingerprint, configuration.dry_run, configuration.read_only_preflight, configuration.production_write_enabled, _public_payload(configuration_payload), created_at_utc), sha256_payload(configuration_payload))
            if fail_at == "configuration": raise RuntimeError("injected configuration failure")
            self._strict_insert("execution.live_credential_references", "credential_reference_id", credential_reference.reference_id, ("provider","alias","source_type","account_fingerprint","loaded","verified_at_utc","permission_summary_jsonb","created_at_utc"), (credential_reference.provider, credential_reference.alias, credential_reference.source_type, credential_reference.account_fingerprint, credential_reference.loaded, credential_reference.verified_at_utc, _public_payload(credential_reference.permission_summary), created_at_utc), credential_reference.record_hash)
            if fail_at == "credential": raise RuntimeError("injected credential failure")
            self._strict_insert("execution.live_account_snapshots", "account_snapshot_id", account_snapshot.snapshot_id, ("live_run_id","account_fingerprint","fetched_at_utc","venue_time_at_utc","total_equity","available_equity","reserved_equity","open_order_count","account_mode","snapshot_jsonb"), (account_snapshot.live_run_id, account_snapshot.account_fingerprint, account_snapshot.fetched_at_utc, account_snapshot.venue_time_at_utc, account_snapshot.total_equity, account_snapshot.available_equity, account_snapshot.reserved_equity, account_snapshot.open_order_count, account_snapshot.account_mode, _public_payload({"balances": dict(account_snapshot.balances), "positions": dict(account_snapshot.positions)})), account_snapshot.record_hash)
            if fail_at == "account": raise RuntimeError("injected account failure")
            self._strict_insert("execution.live_preflight_reports", "preflight_report_id", report.report_id, ("live_run_id","configuration_sha256","implementation_sha256","repository_commit_sha","endpoint_catalog_sha256","credential_reference_sha256","account_snapshot_sha256","evaluated_at_utc","status","blockers_jsonb","warnings_jsonb"), (report.live_run_id, report.configuration_hash, report.implementation_hash, report.repository_commit_sha, report.endpoint_catalog_hash, report.credential_reference_hash, report.account_snapshot_hash, report.evaluated_at_utc, report.status.value, _public_payload(report.blockers), _public_payload(report.warnings)), report.record_hash)
            for check in report.checks:
                self._strict_insert("execution.live_preflight_checks", "preflight_check_id", check.check_id, ("preflight_report_id","check_name","passed","required","evaluated_at_utc","source_timestamp_utc","explanation","evidence_sha256"), (report.report_id, check.check_name, check.passed, check.required, check.evaluated_at_utc, check.source_timestamp_utc, check.explanation, check.evidence_hash), check.record_hash)
            if fail_at == "preflight": raise RuntimeError("injected preflight failure")
            self._strict_insert("execution.live_approvals", "approval_id", approval.approval_id, ("live_run_id","preflight_report_id","configuration_sha256","account_fingerprint","provider","environment","manifest_sha256","confirmation_challenge_sha256","maximum_total_approved_notional","consumed_notional","created_at_utc","expires_at_utc","approving_actor","nonce","approval_jsonb"), (approval.live_run_id, approval.preflight_report_id, approval.configuration_hash, approval.account_fingerprint, approval.provider, approval.environment, approval.manifest_hash, approval.confirmation_challenge_hash, approval.maximum_total_approved_notional, Decimal(0), approval.created_at_utc, approval.expires_at_utc, approval.approving_actor, approval.nonce, _public_payload({"allowed_instruments": approval.allowed_instruments, "repository_commit_sha": approval.repository_commit_sha})), approval.record_hash)
            if fail_at == "approval": raise RuntimeError("injected approval failure")
            self._strict_insert("execution.live_run_manifests", "manifest_id", manifest.manifest_id, ("live_run_id","approval_id","preflight_report_id","initial_account_snapshot_id","configuration_sha256","implementation_sha256","repository_commit_sha","endpoint_catalog_sha256","credential_reference_sha256","manifest_sha256","dry_run","production_write_enabled","manifest_jsonb","created_at_utc"), (manifest.live_run_id, manifest.approval_id, manifest.preflight_report_id, manifest.initial_account_snapshot_id, manifest.configuration_hash, manifest.implementation_hash, manifest.repository_commit_sha, manifest.endpoint_catalog_hash, manifest.credential_reference_hash, manifest.manifest_hash, manifest.dry_run, manifest.production_write_enabled, _public_payload({"allowed_instruments": manifest.allowed_instruments, "risk_limits": dict(manifest.risk_limits), "kill_switch_policy": dict(manifest.kill_switch_policy), "parent_evidence_ids": manifest.parent_evidence_ids}), created_at_utc), manifest.record_hash)
            if fail_at == "manifest": raise RuntimeError("injected manifest failure")
            run_hash = sha256_payload({"run": manifest.live_run_id, "manifest": manifest.manifest_id, "state": "created"})
            self._strict_insert("execution.live_runs", "live_run_id", manifest.live_run_id, ("manifest_id","state","dry_run","production_write_enabled","started_at_utc","completed_at_utc","version"), (manifest.manifest_id, "created", True, False, None, None, 0), run_hash)
            self._strict_insert("execution.live_kill_switches", "kill_switch_id", kill_switch.kill_switch_id, ("live_run_id","state","reason","evidence_sha256","requires_fresh_preflight","requires_new_approval","updated_at_utc","version"), (kill_switch.live_run_id, kill_switch.state.value, kill_switch.reason, kill_switch.evidence_hash, kill_switch.requires_fresh_preflight, kill_switch.requires_new_approval, kill_switch.updated_at_utc, 0), kill_switch.record_hash)
            if fail_at == "kill_switch": raise RuntimeError("injected kill switch failure")
        return True

    def persisted_preflight(self, preflight_report_id):
        return self._fetchone("SELECT * FROM execution.live_preflight_reports WHERE preflight_report_id=%s", (preflight_report_id,))

    def prepare_dry_run_bundle(self, *, intent, risk_decision, request_body, provider_request_hash: str, created_at_utc, reservation_currency: str, fail_at=None):
        with self.transaction():
            self._lock_run(intent.live_run_id)
            manifest = self._fetchone("SELECT approval_id,production_write_enabled,dry_run FROM execution.live_run_manifests WHERE manifest_id=%s FOR SHARE", (intent.manifest_id,))
            if manifest is None or manifest["production_write_enabled"] or not manifest["dry_run"]:
                raise PermissionError("persisted Phase 8A manifest is absent or write-enabled")
            state = LiveOrderState.DRY_RUN_PREPARED if risk_decision.accepted else LiveOrderState.DRY_RUN_BLOCKED
            existing = self._fetchone("SELECT record_sha256,state FROM execution.live_order_intents WHERE order_intent_id=%s FOR UPDATE", (intent.order_intent_id,))
            if existing is not None:
                if str(existing["record_sha256"]) != intent.record_hash:
                    raise LiveConflictError("live intent replay changed immutable economics")
                existing_outbox = self._fetchone("SELECT dispatch_outbox_id FROM execution.live_dispatch_outbox WHERE order_intent_id=%s", (intent.order_intent_id,))
                if existing["state"] == "dry_run_blocked":
                    return None
                if existing_outbox is None:
                    raise LiveConflictError("accepted live intent exists without its durable outbox")
                return existing_outbox["dispatch_outbox_id"]
            self._strict_insert("execution.live_order_intents", "order_intent_id", intent.order_intent_id, ("live_run_id","manifest_id","client_order_id","instrument_id","side","order_type","accounting_mode","quantity","limit_price","reference_price","market_evidence_id","market_evidence_sha256","instrument_metadata_sha256","account_snapshot_sha256","reconciliation_sha256","economic_sha256","state","created_at_utc"), (intent.live_run_id, intent.manifest_id, intent.client_order_id, intent.series_identity.provider_instrument_id, intent.side.value, intent.order_type.value, intent.accounting_mode.value, intent.quantity, intent.limit_price, intent.reference_price, intent.market_evidence_id, intent.market_evidence_hash, intent.instrument_metadata_hash, intent.account_snapshot_hash, intent.reconciliation_hash, intent.economic_hash, state.value, intent.created_at_utc), intent.record_hash)
            if fail_at == "intent": raise RuntimeError("injected intent failure")
            self._strict_insert("execution.live_runtime_risk_decisions", "risk_decision_id", risk_decision.decision_id, ("order_intent_id","accepted","reasons_jsonb","market_evidence_price","risk_reference_price","worst_case_order_price","risk_notional","reservation_notional","price_deviation_bps","price_source_sha256","calculator_version","decided_at_utc"), (intent.order_intent_id, risk_decision.accepted, _public_payload(risk_decision.reasons), risk_decision.market_evidence_price, risk_decision.risk_reference_price, risk_decision.worst_case_order_price, risk_decision.risk_notional, risk_decision.reservation_notional, risk_decision.price_deviation_bps, risk_decision.price_source_hash, risk_decision.calculator_version, risk_decision.evaluated_at_utc), risk_decision.record_hash)
            if fail_at == "risk": raise RuntimeError("injected risk failure")
            if not risk_decision.accepted:
                return None
            approval = self._fetchone("SELECT * FROM execution.live_approvals WHERE approval_id=%s FOR UPDATE", (manifest["approval_id"],))
            if approval is None or approval["expires_at_utc"] <= created_at_utc:
                raise PermissionError("persisted live approval is absent or expired")
            consumed = Decimal(str(approval["consumed_notional"])) + risk_decision.risk_notional
            if consumed > Decimal(str(approval["maximum_total_approved_notional"])):
                raise PermissionError("persisted live approval notional is exhausted")
            self._execute("UPDATE execution.live_approvals SET consumed_notional=%s WHERE approval_id=%s", (consumed, manifest["approval_id"]))
            reservation_id = live_uuid("reservation", {"intent": intent.order_intent_id})
            reservation_hash = sha256_payload({"reservation": reservation_id, "risk": risk_decision.risk_notional, "currency": reservation_currency})
            self._strict_insert("execution.live_reservations", "reservation_id", reservation_id, ("order_intent_id","currency","amount","risk_notional","state","dry_run","created_at_utc","updated_at_utc","version"), (intent.order_intent_id, reservation_currency, risk_decision.reservation_notional, risk_decision.risk_notional, "projected", True, created_at_utc, created_at_utc, 0), reservation_hash)
            if fail_at == "reservation": raise RuntimeError("injected reservation failure")
            outbox_id = live_uuid("dispatch-outbox", {"intent": intent.order_intent_id})
            outbox_hash = sha256_payload({"outbox": outbox_id, "request": provider_request_hash, "body": request_body})
            self._strict_insert("execution.live_dispatch_outbox", "dispatch_outbox_id", outbox_id, ("order_intent_id","client_order_id","state","provider_request_sha256","request_jsonb","worker_identity","claim_token","lease_expires_at_utc","recovery_generation","recovery_claim_token","recovery_worker_identity","recovery_lease_expires_at_utc","created_at_utc","updated_at_utc","suppressed_at_utc","version"), (intent.order_intent_id, intent.client_order_id, "dry_run_prepared", provider_request_hash, _public_payload(request_body), None, None, None, 0, None, None, None, created_at_utc, created_at_utc, None, 0), outbox_hash)
            projection_hash = sha256_payload({"intent": intent.order_intent_id, "state": "dry_run_prepared"})
            self._strict_insert("execution.live_order_projections", "order_intent_id", intent.order_intent_id, ("live_run_id","state","filled_quantity","fees","latest_observation_id","updated_at_utc","version"), (intent.live_run_id, "dry_run_prepared", Decimal(0), Decimal(0), None, created_at_utc, 0), projection_hash)
            self._dispatch_event(outbox_id, "prepared", {"request_hash": provider_request_hash}, created_at_utc)
            if fail_at == "outbox": raise RuntimeError("injected outbox failure")
            return outbox_id

    def _dispatch_event(self, outbox_id, event_type, payload, at_utc):
        event_id = live_uuid("dispatch-event", {"outbox": outbox_id, "type": event_type, "payload": payload})
        record_hash = sha256_payload({"event": event_id, "at": at_utc, "payload": payload})
        self._strict_insert("execution.live_dispatch_events", "dispatch_event_id", event_id, ("dispatch_outbox_id","event_type","event_jsonb","occurred_at_utc"), (outbox_id, event_type, _public_payload(payload), at_utc), record_hash)

    def claim_dispatch(self, *, worker_identity: str, at_utc, lease_seconds: int = 30, outbox_id=None):
        if lease_seconds <= 0: raise ValueError("lease_seconds must be positive")
        with self.transaction():
            sql = "SELECT * FROM execution.live_dispatch_outbox WHERE state='dry_run_prepared' AND (lease_expires_at_utc IS NULL OR lease_expires_at_utc<=%s)" + (" AND dispatch_outbox_id=%s" if outbox_id is not None else "") + " ORDER BY created_at_utc FOR UPDATE SKIP LOCKED LIMIT 1"
            row = self._fetchone(sql, (at_utc, outbox_id) if outbox_id is not None else (at_utc,))
            if row is None: return None
            token = uuid4(); lease = at_utc + timedelta(seconds=lease_seconds)
            changed = self._execute("UPDATE execution.live_dispatch_outbox SET worker_identity=%s,claim_token=%s,lease_expires_at_utc=%s,updated_at_utc=%s,version=version+1 WHERE dispatch_outbox_id=%s AND version=%s", (worker_identity, token, lease, at_utc, row["dispatch_outbox_id"], row["version"]))
            if changed != 1: raise LiveClaimError("dispatch claim lost")
            self._dispatch_event(row["dispatch_outbox_id"], "claimed", {"worker": worker_identity, "claim_token": str(token)}, at_utc)
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
            self._dispatch_event(outbox_id, "write_suppressed", {"external_write_attempted": False}, at_utc)
            attempt_id = live_uuid("transport-attempt", {"outbox": outbox_id, "result": "write_suppressed"})
            self._strict_insert("execution.live_transport_attempts", "transport_attempt_id", attempt_id, ("live_run_id","order_intent_id","operation","provider_request_sha256","provider_response_sha256","result","external_write_attempted","successful_write","attempted_at_utc"), (self._fetchone("SELECT live_run_id FROM execution.live_order_intents WHERE order_intent_id=%s", (row["order_intent_id"],))["live_run_id"], row["order_intent_id"], "submit_limit_order", row["provider_request_sha256"], None, "write_suppressed", False, False, at_utc), sha256_payload({"attempt": attempt_id, "suppressed": True}))
            return True

    def mark_pending_recovery(self, *, outbox_id, claim_token, worker_identity: str, at_utc):
        """Record an ambiguous simulated outcome without retrying the write."""
        with self.transaction():
            row = self._fetchone(
                "SELECT * FROM execution.live_dispatch_outbox WHERE dispatch_outbox_id=%s FOR UPDATE",
                (outbox_id,),
            )
            if (
                row is None
                or row["state"] != "dry_run_prepared"
                or row["claim_token"] != claim_token
                or row["worker_identity"] != worker_identity
                or row["lease_expires_at_utc"] <= at_utc
            ):
                raise LiveClaimError("pending recovery requires the active dispatch lease owner")
            changed = self._execute(
                "UPDATE execution.live_dispatch_outbox "
                "SET state='pending_recovery',worker_identity=NULL,claim_token=NULL,"
                "lease_expires_at_utc=NULL,updated_at_utc=%s,version=version+1 "
                "WHERE dispatch_outbox_id=%s AND version=%s",
                (at_utc, outbox_id, row["version"]),
            )
            if changed != 1:
                raise LiveClaimError("pending recovery transition lost")
            projection = self._fetchone(
                "SELECT * FROM execution.live_order_projections WHERE order_intent_id=%s FOR UPDATE",
                (row["order_intent_id"],),
            )
            self._execute(
                "UPDATE execution.live_order_projections "
                "SET state='pending_recovery',updated_at_utc=%s,record_sha256=%s,version=version+1 "
                "WHERE order_intent_id=%s AND version=%s",
                (
                    at_utc,
                    sha256_payload({"intent": row["order_intent_id"], "state": "pending_recovery"}),
                    row["order_intent_id"],
                    projection["version"],
                ),
            )
            self._execute(
                "UPDATE execution.live_order_intents SET state='pending_recovery' WHERE order_intent_id=%s",
                (row["order_intent_id"],),
            )
            attempt_id = live_uuid(
                "transport-attempt",
                {"outbox": outbox_id, "result": "ambiguous"},
            )
            live_run_id = self._fetchone(
                "SELECT live_run_id FROM execution.live_order_intents WHERE order_intent_id=%s",
                (row["order_intent_id"],),
            )["live_run_id"]
            self._strict_insert(
                "execution.live_transport_attempts",
                "transport_attempt_id",
                attempt_id,
                (
                    "live_run_id",
                    "order_intent_id",
                    "operation",
                    "provider_request_sha256",
                    "provider_response_sha256",
                    "result",
                    "external_write_attempted",
                    "successful_write",
                    "attempted_at_utc",
                ),
                (
                    live_run_id,
                    row["order_intent_id"],
                    "simulated_submit_limit_order",
                    row["provider_request_sha256"],
                    None,
                    "ambiguous",
                    False,
                    False,
                    at_utc,
                ),
                sha256_payload({"attempt": attempt_id, "ambiguous": True}),
            )
        return True

    def claim_recovery(
        self,
        *,
        worker_identity: str,
        at_utc,
        lease_seconds: int = 30,
        outbox_id=None,
    ):
        if lease_seconds <= 0:
            raise ValueError("lease_seconds must be positive")
        with self.transaction():
            sql = (
                "SELECT d.*,i.live_run_id FROM execution.live_dispatch_outbox d "
                "JOIN execution.live_order_intents i ON i.order_intent_id=d.order_intent_id "
                "WHERE d.state='pending_recovery' "
                "AND (d.recovery_lease_expires_at_utc IS NULL OR d.recovery_lease_expires_at_utc<=%s)"
                + (" AND d.dispatch_outbox_id=%s" if outbox_id is not None else "")
                + " ORDER BY d.created_at_utc FOR UPDATE OF d SKIP LOCKED LIMIT 1"
            )
            row = self._fetchone(
                sql,
                (at_utc, outbox_id) if outbox_id is not None else (at_utc,),
            )
            if row is None:
                return None
            token = uuid4()
            lease = at_utc + timedelta(seconds=lease_seconds)
            generation = int(row["recovery_generation"]) + 1
            changed = self._execute(
                "UPDATE execution.live_dispatch_outbox "
                "SET recovery_generation=%s,recovery_claim_token=%s,"
                "recovery_worker_identity=%s,recovery_lease_expires_at_utc=%s,"
                "updated_at_utc=%s,version=version+1 "
                "WHERE dispatch_outbox_id=%s AND version=%s",
                (
                    generation,
                    token,
                    worker_identity,
                    lease,
                    at_utc,
                    row["dispatch_outbox_id"],
                    row["version"],
                ),
            )
            if changed != 1:
                raise LiveClaimError("recovery claim lost")
            recovery_id = live_uuid(
                "recovery-record",
                {"outbox": row["dispatch_outbox_id"], "generation": generation},
            )
            record_hash = sha256_payload(
                {
                    "recovery": recovery_id,
                    "worker": worker_identity,
                    "claim": token,
                    "lease": lease,
                    "state": "claimed",
                }
            )
            self._strict_insert(
                "execution.live_recovery_records",
                "recovery_record_id",
                recovery_id,
                (
                    "live_run_id",
                    "order_intent_id",
                    "client_order_id",
                    "generation",
                    "worker_identity",
                    "claim_token",
                    "lease_expires_at_utc",
                    "query_first",
                    "observation_bundle_sha256",
                    "state",
                    "created_at_utc",
                    "updated_at_utc",
                ),
                (
                    row["live_run_id"],
                    row["order_intent_id"],
                    row["client_order_id"],
                    generation,
                    worker_identity,
                    token,
                    lease,
                    True,
                    None,
                    "claimed",
                    at_utc,
                    at_utc,
                ),
                record_hash,
            )
            self._dispatch_event(
                row["dispatch_outbox_id"],
                "recovery_claimed",
                {
                    "generation": generation,
                    "worker": worker_identity,
                    "claim_token": str(token),
                },
                at_utc,
            )
            return row["dispatch_outbox_id"], token, generation

    def persist_recovery_observation(
        self,
        *,
        outbox_id,
        claim_token,
        worker_identity: str,
        observation_bundle,
        at_utc,
    ):
        observation_id = live_uuid(
            "order-observation",
            {
                "run": observation_bundle.live_run_id,
                "client": observation_bundle.client_order_id,
                "queried_at": observation_bundle.queried_at_utc,
            },
        )
        with self.transaction():
            existing = self._fetchone(
                "SELECT record_sha256 FROM execution.live_order_observations "
                "WHERE order_observation_id=%s",
                (observation_id,),
            )
            if existing is not None:
                if str(existing["record_sha256"]) != observation_bundle.record_hash:
                    raise LiveConflictError("conflicting recovery observation replay")
                return observation_id
            row = self._fetchone(
                "SELECT d.*,i.live_run_id FROM execution.live_dispatch_outbox d "
                "JOIN execution.live_order_intents i ON i.order_intent_id=d.order_intent_id "
                "WHERE d.dispatch_outbox_id=%s FOR UPDATE OF d",
                (outbox_id,),
            )
            if (
                row is None
                or row["state"] != "pending_recovery"
                or row["recovery_claim_token"] != claim_token
                or row["recovery_worker_identity"] != worker_identity
                or row["recovery_lease_expires_at_utc"] <= at_utc
                or row["live_run_id"] != observation_bundle.live_run_id
                or row["client_order_id"] != observation_bundle.client_order_id
            ):
                raise LiveClaimError("observation persistence requires the active recovery lease owner")
            payload = {
                "bundle_id": observation_bundle.bundle_id,
                "queried_order": observation_bundle.queried_order,
                "recent_orders": observation_bundle.recent_orders,
                "open_orders": observation_bundle.open_orders,
                "fills": observation_bundle.fills,
                "account_observation": dict(observation_bundle.account_observation),
                "complete": observation_bundle.complete,
            }
            queried = observation_bundle.queried_order or {}
            self._strict_insert(
                "execution.live_order_observations",
                "order_observation_id",
                observation_id,
                (
                    "live_run_id",
                    "order_intent_id",
                    "client_order_id",
                    "provider_order_id",
                    "provider_state",
                    "observed_at_utc",
                    "observation_jsonb",
                    "provider_response_sha256",
                ),
                (
                    row["live_run_id"],
                    row["order_intent_id"],
                    row["client_order_id"],
                    queried.get("ordId"),
                    queried.get("state"),
                    observation_bundle.queried_at_utc,
                    _public_payload(payload),
                    observation_bundle.record_hash,
                ),
                observation_bundle.record_hash,
            )
            for fill in observation_bundle.fills:
                provider_fill_id = str(fill.get("tradeId") or fill.get("fillId") or "")
                if not provider_fill_id:
                    raise ValueError("recovery fill lacks a stable provider fill identity")
                quantity = Decimal(str(fill.get("fillSz") or fill.get("sz") or fill.get("quantity")))
                price = Decimal(str(fill.get("fillPx") or fill.get("px") or fill.get("price")))
                fee = abs(Decimal(str(fill.get("fee") or "0")))
                fee_currency = str(fill.get("feeCcy") or fill.get("fee_currency") or "")
                if quantity <= 0 or price <= 0 or not fee_currency:
                    raise ValueError("recovery fill is incomplete")
                fill_id = live_uuid(
                    "fill-observation",
                    {"run": row["live_run_id"], "provider_fill_id": provider_fill_id},
                )
                fill_hash = sha256_payload(fill)
                self._strict_insert(
                    "execution.live_fill_observations",
                    "fill_observation_id",
                    fill_id,
                    (
                        "live_run_id",
                        "order_intent_id",
                        "provider_fill_id",
                        "provider_order_id",
                        "client_order_id",
                        "quantity",
                        "price",
                        "fee",
                        "fee_currency",
                        "observed_at_utc",
                        "provider_response_sha256",
                    ),
                    (
                        row["live_run_id"],
                        row["order_intent_id"],
                        provider_fill_id,
                        fill.get("ordId"),
                        fill.get("clOrdId") or row["client_order_id"],
                        quantity,
                        price,
                        fee,
                        fee_currency,
                        observation_bundle.queried_at_utc,
                        fill_hash,
                    ),
                    fill_hash,
                )
            generation = int(row["recovery_generation"])
            recovery_id = live_uuid(
                "recovery-record",
                {"outbox": outbox_id, "generation": generation},
            )
            recovery_state = "resolved" if observation_bundle.complete else "ambiguous"
            recovery_hash = sha256_payload(
                {
                    "recovery": recovery_id,
                    "bundle": observation_bundle.record_hash,
                    "state": recovery_state,
                }
            )
            changed = self._execute(
                "UPDATE execution.live_recovery_records "
                "SET observation_bundle_sha256=%s,state=%s,updated_at_utc=%s,record_sha256=%s "
                "WHERE recovery_record_id=%s AND claim_token=%s AND worker_identity=%s",
                (
                    observation_bundle.record_hash,
                    recovery_state,
                    at_utc,
                    recovery_hash,
                    recovery_id,
                    claim_token,
                    worker_identity,
                ),
            )
            if changed != 1:
                raise LiveClaimError("recovery record ownership lost")
            next_state = "dry_run_suppressed" if observation_bundle.complete else "pending_recovery"
            changed = self._execute(
                "UPDATE execution.live_dispatch_outbox "
                "SET state=%s,recovery_claim_token=NULL,recovery_worker_identity=NULL,"
                "recovery_lease_expires_at_utc=NULL,suppressed_at_utc=%s,"
                "updated_at_utc=%s,version=version+1 "
                "WHERE dispatch_outbox_id=%s AND version=%s",
                (
                    next_state,
                    at_utc if observation_bundle.complete else None,
                    at_utc,
                    outbox_id,
                    row["version"],
                ),
            )
            if changed != 1:
                raise LiveClaimError("recovery observation transition lost")
            projection = self._fetchone(
                "SELECT * FROM execution.live_order_projections WHERE order_intent_id=%s FOR UPDATE",
                (row["order_intent_id"],),
            )
            self._execute(
                "UPDATE execution.live_order_projections "
                "SET state=%s,latest_observation_id=%s,updated_at_utc=%s,"
                "record_sha256=%s,version=version+1 "
                "WHERE order_intent_id=%s AND version=%s",
                (
                    next_state,
                    observation_id,
                    at_utc,
                    sha256_payload(
                        {
                            "intent": row["order_intent_id"],
                            "state": next_state,
                            "observation": observation_bundle.record_hash,
                        }
                    ),
                    row["order_intent_id"],
                    projection["version"],
                ),
            )
            self._execute(
                "UPDATE execution.live_order_intents SET state=%s WHERE order_intent_id=%s",
                (next_state, row["order_intent_id"]),
            )
            self._dispatch_event(
                outbox_id,
                "observation_persisted",
                {
                    "generation": generation,
                    "observation_bundle_sha256": observation_bundle.record_hash,
                    "complete": observation_bundle.complete,
                },
                at_utc,
            )
            return observation_id

    def prepare_cancel_dry_run(
        self,
        *,
        live_run_id,
        order_intent_id,
        client_order_id: str,
        request_body,
        provider_request_hash: str,
        created_at_utc,
    ):
        """Persist and suppress a cancellation plan without invoking transport."""
        with self.transaction():
            row = self._fetchone(
                "SELECT i.live_run_id,m.dry_run,m.production_write_enabled "
                "FROM execution.live_order_intents i "
                "JOIN execution.live_run_manifests m ON m.manifest_id=i.manifest_id "
                "WHERE i.order_intent_id=%s FOR SHARE",
                (order_intent_id,),
            )
            if (
                row is None
                or row["live_run_id"] != live_run_id
                or not row["dry_run"]
                or row["production_write_enabled"]
            ):
                raise PermissionError("cancel outbox requires persisted dry-run authority")
            cancel_id = live_uuid(
                "cancel-outbox",
                {"intent": order_intent_id, "request": provider_request_hash},
            )
            cancel_hash = sha256_payload(
                {"cancel": cancel_id, "request": provider_request_hash, "body": request_body}
            )
            self._strict_insert(
                "execution.live_cancel_outbox",
                "cancel_outbox_id",
                cancel_id,
                (
                    "live_run_id",
                    "order_intent_id",
                    "client_order_id",
                    "state",
                    "provider_request_sha256",
                    "request_jsonb",
                    "worker_identity",
                    "claim_token",
                    "lease_expires_at_utc",
                    "recovery_generation",
                    "created_at_utc",
                    "updated_at_utc",
                ),
                (
                    live_run_id,
                    order_intent_id,
                    client_order_id,
                    "dry_run_prepared",
                    provider_request_hash,
                    _public_payload(request_body),
                    None,
                    None,
                    None,
                    0,
                    created_at_utc,
                    created_at_utc,
                ),
                cancel_hash,
            )
            self._execute(
                "UPDATE execution.live_cancel_outbox "
                "SET state='dry_run_suppressed',updated_at_utc=%s "
                "WHERE cancel_outbox_id=%s AND state='dry_run_prepared'",
                (created_at_utc, cancel_id),
            )
            attempt_id = live_uuid(
                "transport-attempt",
                {"cancel": cancel_id, "result": "write_suppressed"},
            )
            self._strict_insert(
                "execution.live_transport_attempts",
                "transport_attempt_id",
                attempt_id,
                (
                    "live_run_id",
                    "order_intent_id",
                    "operation",
                    "provider_request_sha256",
                    "provider_response_sha256",
                    "result",
                    "external_write_attempted",
                    "successful_write",
                    "attempted_at_utc",
                ),
                (
                    live_run_id,
                    order_intent_id,
                    "cancel_order",
                    provider_request_hash,
                    None,
                    "write_suppressed",
                    False,
                    False,
                    created_at_utc,
                ),
                sha256_payload({"attempt": attempt_id, "suppressed": True}),
            )
            return cancel_id
    def persist_reconciliation(self, reconciliation, *, exact_input):
        with self.transaction():
            self._strict_insert("execution.live_reconciliations", "reconciliation_id", reconciliation.reconciliation_id, ("live_run_id","status","input_bundle_sha256","exact_input_jsonb","evaluated_at_utc"), (reconciliation.live_run_id, reconciliation.status.value, reconciliation.input_bundle_hash, _public_payload(exact_input), reconciliation.evaluated_at_utc), reconciliation.record_hash)
            for difference in reconciliation.differences:
                difference_id = live_uuid("reconciliation-difference", {"reconciliation": reconciliation.reconciliation_id, "difference": difference})
                record_hash = sha256_payload({"difference": difference_id, "payload": difference})
                self._strict_insert("execution.live_reconciliation_differences", "reconciliation_difference_id", difference_id, ("reconciliation_id","field_name","material","local_value_jsonb","venue_value_jsonb"), (reconciliation.reconciliation_id, str(difference.get("field")), bool(difference.get("material", True)), _public_payload(difference.get("local")), _public_payload(difference.get("venue"))), record_hash)
        return reconciliation.reconciliation_id

    def persist_summary(self, summary):
        table = "execution.live_pre_run_summaries" if summary.summary_type == "pre_run" else "execution.live_post_run_summaries"
        columns = ("live_run_id","generated_at_utc","public_summary_jsonb","evidence_ids")
        values = (summary.live_run_id, summary.generated_at_utc, _public_payload(dict(summary.public_payload)), list(summary.evidence_ids))
        if summary.summary_type == "post_run":
            columns += ("external_write_attempted","external_write_suppressed")
            values += (False, bool(summary.public_payload.get("external_write_suppressed")))
        with self.transaction():
            return self._strict_insert(table, "summary_id", summary.summary_id, columns, values, summary.record_hash)

    def reconstruct(self, live_run_id):
        run = self._fetchone("SELECT * FROM execution.live_runs WHERE live_run_id=%s", (live_run_id,))
        manifest = self._fetchone("SELECT * FROM execution.live_run_manifests WHERE live_run_id=%s", (live_run_id,))
        kill = self._fetchone("SELECT * FROM execution.live_kill_switches WHERE live_run_id=%s", (live_run_id,))
        if run is None or manifest is None or kill is None:
            raise LookupError("live run cannot be reconstructed from PostgreSQL")
        if run["production_write_enabled"] or manifest["production_write_enabled"] or not run["dry_run"] or not manifest["dry_run"]:
            raise PermissionError("reconstructed Phase 8A authority is not dry-run/write-disabled")
        outbox = self._fetchone("SELECT count(*) AS count FROM execution.live_dispatch_outbox d JOIN execution.live_order_intents i ON i.order_intent_id=d.order_intent_id WHERE i.live_run_id=%s AND d.state='pending_recovery'", (live_run_id,))
        return {"run": run, "manifest": manifest, "kill_switch": kill, "pending_recovery_count": int(outbox["count"])}


__all__ = ["LiveConflictError", "LiveClaimError", "DurablePostgresLiveRepository"]
