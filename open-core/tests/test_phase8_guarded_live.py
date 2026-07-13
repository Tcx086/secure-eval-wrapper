from __future__ import annotations

import json
import os
import subprocess
import sys
import unittest
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from unittest.mock import patch
from uuid import UUID, uuid4

from secure_eval_wrapper.alpha.identity import SeriesIdentity
from secure_eval_wrapper.data_collection.hashing import sha256_payload
from secure_eval_wrapper.data_collection.models import InstrumentType
from secure_eval_wrapper.execution.models import OrderSide
from secure_eval_wrapper.live.approval import LiveApprovalController, LiveApprovalError, confirmation_challenge_hash, manifest_preview_hash
from secure_eval_wrapper.live.broker import GuardedLiveBroker
from secure_eval_wrapper.live.configuration import GuardedLiveConfiguration, phase8a_dry_run_configuration
from secure_eval_wrapper.live.credentials import InjectedLocalCredentialProvider, redact, validate_permission_summary
from secure_eval_wrapper.live.endpoints import EndpointClass, LiveOperation, build_request_path, classify_exact, endpoint_catalog_hash
from secure_eval_wrapper.live.gates import evaluate_live_write_authority
from secure_eval_wrapper.live.kill_switch import arm_kill_switch, reset_kill_switch, trigger_kill_switch
from secure_eval_wrapper.live.manifests import create_live_manifest, validate_live_manifest
from secure_eval_wrapper.live.models import LiveAccountSnapshot, LiveKillState, LiveOrderIntent, LivePreflightStatus, LiveReconciliationStatus
from secure_eval_wrapper.live.preflight import LivePreflightEngine, LivePreflightEvidence
from secure_eval_wrapper.live.reconciliation import reconcile_live
from secure_eval_wrapper.live.recovery import query_first_recovery
from secure_eval_wrapper.live.risk import LiveRiskState, evaluate_live_risk
from secure_eval_wrapper.live.venue import ProductionWriteSuppressed
from secure_eval_wrapper.live.venues.fake_live import FakeLiveVenue
from secure_eval_wrapper.live.venues.okx_live import OkxProductionSpotAdapter, signed_headers
from secure_eval_wrapper.paper.models import PaperMarketDataEvidence

T0 = datetime(2026, 7, 13, 3, 0, tzinfo=timezone.utc)
H = sha256_payload("phase8-test")


def identity():
    return SeriesIdentity("okx", "okx", "BTC-USDT", "BTC-USDT", InstrumentType.SPOT, "1m", "USDT")


def config():
    return phase8a_dry_run_configuration(endpoint_catalog_hash=endpoint_catalog_hash(), provider_implementation_hash=OkxProductionSpotAdapter.provider_implementation_hash)


def account(run, *, at=T0, fingerprint="0000000000000000"):
    return LiveAccountSnapshot(run, fingerprint, at, at, {"USDT": {"total": Decimal("10000"), "available": Decimal("9000"), "reserved": Decimal("1000")}}, {}, 0, Decimal("10000"), Decimal("9000"), Decimal("1000"), "spot_cash")


def credential(*, at=T0, permissions=("read", "spot_trade")):
    return InjectedLocalCredentialProvider("placeholder-key", "placeholder-secret", "placeholder-passphrase").reference(verified_at_utc=at, permissions=permissions)


def evidence(run, snapshot, *, at=T0, market_at=T0, reconcile_at=T0, overrides=None):
    values = dict(repository_commit_matches=True, implementation_hash_matches=True, clean_migration_catalog=True, postgresql_available=True, audit_tables_writable=True, credential_reference_present=True, credential_secret_absent_from_persistence=True, permissions_verified=True, permissions_safe=True, account_fingerprint_matches=True, subaccount_matches=True, account_exists=True, account_spot_cash=True, margin_disabled=True, borrowing_disabled=True, disallowed_positions_absent=True, existing_open_orders_identified=True, balances_complete=True, venue_time_at_utc=at, market_evidence_at_utc=market_at, validated_market_evidence=True, price_currency_matches=True, instrument_metadata_verified=True, tick_size_verified=True, lot_size_verified=True, minimum_order_size_verified=True, order_notional_bounds_verified=True, reconciliation_status=LiveReconciliationStatus.RECONCILED, reconciliation_at_utc=reconcile_at, kill_switch_armed=True, fake_transport=True, evidence_hashes={})
    if overrides: values.update(overrides)
    return LivePreflightEvidence(**values)


def passed_authority(*, run=None, at=T0):
    run = run or uuid4(); cfg = config(); snap = account(run, at=at); cred = credential(at=at)
    report = LivePreflightEngine().evaluate(live_run_id=run, configuration=cfg, account_snapshot=snap, credential_reference=cred, evidence=evidence(run, snap, at=at), evaluated_at_utc=at, implementation_hash=cfg.provider_implementation_hash, repository_commit_sha="e863f383")
    preview = manifest_preview_hash(live_run_id=run, configuration=cfg, credential_reference_hash=cred.record_hash, preflight_report_id=report.report_id, account_snapshot_hash=snap.record_hash, repository_commit_sha="e863f383")
    expires = at + timedelta(seconds=300)
    challenge = confirmation_challenge_hash(live_run_id=run, configuration=cfg, account_fingerprint=snap.account_fingerprint, manifest_hash=preview, repository_commit_sha="e863f383", nonce="nonce-123", approving_actor="local-operator", created_at_utc=at, expires_at_utc=expires, maximum_total_approved_notional=Decimal("5000"))
    approval = LiveApprovalController().create(report=report, configuration=cfg, account_snapshot=snap, manifest_hash=preview, repository_commit_sha="e863f383", created_at_utc=at, ttl_seconds=300, nonce="nonce-123", approving_actor="local-operator", maximum_total_approved_notional=Decimal("5000"), exact_confirmation_challenge_hash=challenge)
    manifest = create_live_manifest(configuration=cfg, report=report, approval=approval, account_snapshot=snap, credential_reference=cred, repository_commit_sha="e863f383", at_utc=at)
    return cfg, snap, cred, report, approval, manifest


def market_evidence(*, at=T0, price=Decimal("100")):
    ident = identity(); report_id = uuid4(); row_id = str(uuid4()); source_hash = sha256_payload({"source": row_id})
    return PaperMarketDataEvidence(ident, "okx", "BTC-USDT", "bar_close", row_id, at, at, True, "accepted", source_hash, source_hash, exchange="okx", provider_instrument_id="BTC-USDT", instrument_type="spot", source_table="market_data.validated_bars", source_row_id=row_id, validation_report_id=report_id, price=price, price_type="close", quote_currency="USDT", normalized_record_sha256=source_hash, source_kind="postgresql")


def live_intent(manifest, snap, reconciliation, market, *, reference=Decimal("100"), limit=Decimal("100"), quantity=Decimal("1")):
    return LiveOrderIntent(manifest.live_run_id, manifest.manifest_id, identity(), OrderSide.BUY, quantity, reference, limit, T0, market.evidence_id, market.evidence_sha256, H, snap.record_hash, reconciliation.record_hash)


class ConfigurationAndEndpointTests(unittest.TestCase):
    def test_live_is_disabled_by_default(self):
        cfg = config(); self.assertTrue(cfg.dry_run); self.assertTrue(cfg.read_only_preflight); self.assertFalse(cfg.production_write_enabled); self.assertFalse(cfg.automatic_flatten); self.assertFalse(cfg.allow_short); self.assertFalse(cfg.allow_perpetual)

    def test_missing_or_permissive_limits_fail(self):
        values = dict(config().__dict__)
        for field, bad in (("maximum_order_notional", Decimal(0)), ("maximum_open_order_count", 0), ("maximum_fee_bps", None)):
            with self.subTest(field=field):
                changed = dict(values); changed[field] = bad
                with self.assertRaises((ValueError, TypeError)): GuardedLiveConfiguration(**changed)

    def test_market_derivative_and_non_limit_orders_are_forbidden(self):
        values = dict(config().__dict__)
        for field, bad in (("allowed_order_types", ("market",)), ("allowed_instrument_types", ("swap",)), ("allow_perpetual", True), ("automatic_flatten", True)):
            with self.subTest(field=field):
                changed = dict(values); changed[field] = bad
                with self.assertRaises(ValueError): GuardedLiveConfiguration(**changed)

    def test_exact_endpoint_catalog_and_forbidden_mutations(self):
        self.assertEqual(classify_exact("GET", "/api/v5/account/balance"), EndpointClass.AUTHENTICATED_READ)
        self.assertEqual(classify_exact("POST", "/api/v5/trade/order"), EndpointClass.TRADING_WRITE)
        for path in ("/api/v5/asset/withdrawal", "/api/v5/asset/transfer", "/api/v5/account/set-leverage", "/api/v5/account/borrow-repay", "/api/v5/trade/order-algo", "/anything"):
            self.assertEqual(classify_exact("POST", path), EndpointClass.FORBIDDEN)
        with self.assertRaises(PermissionError): build_request_path(LiveOperation.SUBMIT_LIMIT_ORDER)

    def test_exact_order_body_is_spot_cash_limit(self):
        body = OkxProductionSpotAdapter.build_limit_order_body(instrument="BTC-USDT", side="buy", quantity=Decimal("1.234"), limit_price=Decimal("100.129"), client_order_id="sew123", tick_size=Decimal("0.01"), lot_size=Decimal("0.001"))
        self.assertEqual(body, {"instId": "BTC-USDT", "tdMode": "cash", "clOrdId": "sew123", "side": "buy", "ordType": "limit", "px": "100.12", "sz": "1.234"})
        self.assertNotIn("lever", body); self.assertNotIn("posSide", body)

    def test_authentication_signature_is_deterministic(self):
        material = InjectedLocalCredentialProvider("placeholder-key", "placeholder-secret", "placeholder-passphrase").load(gates={"read_only_preflight": True, "provider_selected": True, "production_environment": True, "endpoint_catalog_valid": True, "configuration_valid": True, "production_writes_disabled": True, "kill_switch_armed": True, "postgresql_available": True})
        headers = signed_headers(credential_material=material, method="GET", request_path="/api/v5/account/balance", timestamp="2026-07-13T03:00:00.000Z")
        self.assertEqual(set(headers), {"Content-Type", "OK-ACCESS-KEY", "OK-ACCESS-SIGN", "OK-ACCESS-TIMESTAMP", "OK-ACCESS-PASSPHRASE"})
        self.assertEqual(headers, signed_headers(credential_material=material, method="GET", request_path="/api/v5/account/balance", timestamp="2026-07-13T03:00:00.000Z"))


class CredentialAndGateTests(unittest.TestCase):
    def test_unknown_and_forbidden_permissions_fail_closed(self):
        for permissions in ((), ("read", "withdraw"), ("read", "account_admin"), ("read", "mystery")):
            with self.subTest(permissions=permissions):
                with self.assertRaises(PermissionError): validate_permission_summary(permissions)
        self.assertEqual(validate_permission_summary(("spot_trade", "read")), ("read", "spot_trade"))

    def test_secret_redaction_covers_headers_queries_and_nested_values(self):
        payload = redact({"OK-ACCESS-KEY": "do-not-log", "nested": {"passphrase": "do-not-log", "url": "https://example.invalid?signature=do-not-log"}})
        text = json.dumps(payload); self.assertNotIn("do-not-log", text); self.assertGreaterEqual(text.count("[REDACTED]"), 3)

    def test_missing_each_gate_blocks_and_phase8a_always_blocks(self):
        cfg, snap, cred, report, approval, manifest = passed_authority()
        full = {"SECURE_EVAL_ENABLE_LIVE_EXECUTION": "true"}
        authority = evaluate_live_write_authority(configuration=cfg, cli_enable_live_execution=True, approval=approval, exact_confirmation_challenge_hash=approval.confirmation_challenge_hash, at_utc=T0 + timedelta(seconds=1), environment=full)
        self.assertFalse(authority.allowed); self.assertIn("phase8a_production_writes_disabled", authority.blockers)
        for env, cli, challenge, reason in (({}, True, approval.confirmation_challenge_hash, "missing_process_environment_gate"), (full, False, approval.confirmation_challenge_hash, "missing_cli_gate"), (full, True, "0" * 64, "missing_or_invalid_exact_approval_challenge")):
            self.assertIn(reason, evaluate_live_write_authority(configuration=cfg, cli_enable_live_execution=cli, approval=approval, exact_confirmation_challenge_hash=challenge, at_utc=T0 + timedelta(seconds=1), environment=env).blockers)

    def test_ci_hard_prohibition_cannot_be_overridden(self):
        cfg, snap, cred, report, approval, manifest = passed_authority()
        authority = evaluate_live_write_authority(configuration=cfg, cli_enable_live_execution=True, approval=approval, exact_confirmation_challenge_hash=approval.confirmation_challenge_hash, at_utc=T0 + timedelta(seconds=1), environment={"CI": "true", "SECURE_EVAL_ENABLE_LIVE_EXECUTION": "true"})
        self.assertFalse(authority.allowed); self.assertTrue(authority.ci_prohibited); self.assertIn("ci_hard_prohibition", authority.blockers)

    def test_credentials_are_not_loaded_before_gates_or_in_ci(self):
        provider = InjectedLocalCredentialProvider("placeholder-key", "placeholder-secret", "placeholder-passphrase")
        with self.assertRaises(PermissionError): provider.load(gates={})
        self.assertEqual(provider.load_count, 0)
        gates = {"read_only_preflight": True, "provider_selected": True, "production_environment": True, "endpoint_catalog_valid": True, "configuration_valid": True, "production_writes_disabled": True, "kill_switch_armed": True, "postgresql_available": True}
        with patch.dict(os.environ, {"CI": "true"}):
            with self.assertRaises(PermissionError): provider.load(gates=gates)


class PreflightApprovalManifestTests(unittest.TestCase):
    def test_complete_preflight_passes_and_explicit_checks_exist(self):
        cfg, snap, cred, report, approval, manifest = passed_authority()
        self.assertIs(report.status, LivePreflightStatus.PASSED); self.assertEqual(report.blockers, ())
        names = {row.check_name for row in report.checks}
        self.assertTrue({"postgresql_authority", "credential_permissions", "account_mode_spot_cash", "no_margin", "no_borrowing", "validated_market_data", "reconciliation", "ci_hard_prohibition", "live_writes_disabled"}.issubset(names))

    def test_stale_market_account_and_reconciliation_block(self):
        run = uuid4(); cfg = config(); snap = account(run, at=T0 - timedelta(seconds=60)); cred = credential()
        report = LivePreflightEngine().evaluate(live_run_id=run, configuration=cfg, account_snapshot=snap, credential_reference=cred, evidence=evidence(run, snap, market_at=T0 - timedelta(seconds=60), reconcile_at=T0 - timedelta(seconds=60)), evaluated_at_utc=T0, implementation_hash=cfg.provider_implementation_hash, repository_commit_sha="e863f383")
        self.assertIs(report.status, LivePreflightStatus.BLOCKED); self.assertTrue({"validated_market_data", "reconciliation", "account_snapshot_fresh"}.issubset(report.blockers))

    def test_exact_challenge_expiry_manifest_and_account_mismatches(self):
        cfg, snap, cred, report, approval, manifest = passed_authority()
        with self.assertRaises(LiveApprovalError): LiveApprovalController().validate(approval, report=report, configuration=cfg, manifest_hash=approval.manifest_hash, account_snapshot=snap, at_utc=approval.expires_at_utc)
        with self.assertRaises(LiveApprovalError): LiveApprovalController().create(report=report, configuration=cfg, account_snapshot=snap, manifest_hash=approval.manifest_hash, repository_commit_sha="e863f383", created_at_utc=T0, ttl_seconds=300, nonce="new", approving_actor="operator", maximum_total_approved_notional=Decimal("100"), exact_confirmation_challenge_hash="0" * 64)
        changed = account(manifest.live_run_id, fingerprint="1111111111111111")
        with self.assertRaises(LiveApprovalError): LiveApprovalController().validate(approval, report=report, configuration=cfg, manifest_hash=approval.manifest_hash, account_snapshot=changed, at_utc=T0 + timedelta(seconds=1))
        validate_live_manifest(manifest, configuration=cfg, report=report, approval=approval, account_snapshot=snap, credential_reference=cred)
        with self.assertRaises(ValueError): validate_live_manifest(manifest, configuration=cfg, report=report, approval=approval, account_snapshot=changed, credential_reference=cred)


class RiskRecoveryKillTests(unittest.TestCase):
    def state(self):
        return LiveRiskState(Decimal(0), Decimal(0), Decimal(0), Decimal(0), Decimal(0), 0, LiveReconciliationStatus.RECONCILED, LiveKillState.ARMED)

    def test_low_reference_high_limit_attack_blocks_before_plan(self):
        cfg, snap, cred, report, approval, manifest = passed_authority(); reconciliation = reconcile_live(live_run_id=manifest.live_run_id, local_projection={"orders": [], "fills": [], "balances": {}, "positions": {}, "average_prices": {}, "realized_pnl": 0, "fees": 0, "sequence": 1, "timestamp_utc": T0}, venue_observation={"orders": [], "fills": [], "balances": {}, "positions": {}, "average_prices": {}, "realized_pnl": 0, "fees": 0, "sequence": 1, "timestamp_utc": T0}, evaluated_at_utc=T0)
        market = market_evidence(price=Decimal("100")); intent = live_intent(manifest, snap, reconciliation, market, reference=Decimal("100"), limit=Decimal("50000"))
        decision = evaluate_live_risk(intent=intent, market_evidence=market, configuration=cfg, state=self.state(), approval=approval, evaluated_at_utc=T0)
        self.assertFalse(decision.accepted); self.assertEqual(decision.risk_notional, Decimal("50000")); self.assertIn("maximum_order_notional", decision.reasons)

    def test_one_persisted_risk_notional_drives_all_limits(self):
        cfg, snap, cred, report, approval, manifest = passed_authority(); reconciliation = reconcile_live(live_run_id=manifest.live_run_id, local_projection={"orders": [], "fills": [], "balances": {}, "positions": {}, "average_prices": {}, "realized_pnl": 0, "fees": 0, "sequence": 1, "timestamp_utc": T0}, venue_observation={"orders": [], "fills": [], "balances": {}, "positions": {}, "average_prices": {}, "realized_pnl": 0, "fees": 0, "sequence": 1, "timestamp_utc": T0}, evaluated_at_utc=T0)
        market = market_evidence(price=Decimal("100")); intent = live_intent(manifest, snap, reconciliation, market, limit=Decimal("101"))
        decision = evaluate_live_risk(intent=intent, market_evidence=market, configuration=cfg, state=self.state(), approval=approval, evaluated_at_utc=T0)
        self.assertTrue(decision.accepted); self.assertEqual(decision.risk_notional, decision.reservation_notional); self.assertEqual(decision.worst_case_order_price, Decimal("101"))

    def test_query_first_recovery_order_and_no_blind_retry(self):
        venue = FakeLiveVenue(orders=[{"clOrdId": "client1", "state": "live"}])
        bundle = query_first_recovery(live_run_id=uuid4(), venue=venue, instrument="BTC-USDT", client_order_id="client1", queried_at_utc=T0)
        self.assertTrue(bundle.complete); self.assertEqual([row[0] for row in venue.calls], ["query_order", "recent_orders", "open_orders", "fills", "account_config", "balances", "positions"]); self.assertEqual(venue.write_attempt_count, 0)
        self.assertEqual(bundle.bundle_id, query_first_recovery(live_run_id=bundle.live_run_id, venue=FakeLiveVenue(orders=[{"clOrdId": "client1", "state": "live"}]), instrument="BTC-USDT", client_order_id="client1", queried_at_utc=T0).bundle_id)

    def test_kill_switch_rejects_unknown_reason_and_reset_requires_fresh_authority(self):
        cfg, snap, cred, report, approval, manifest = passed_authority(); armed = arm_kill_switch(live_run_id=manifest.live_run_id, at_utc=T0)
        with self.assertRaises(ValueError): trigger_kill_switch(armed, reason="flatten", at_utc=T0, evidence={})
        stopped = trigger_kill_switch(armed, reason="production_write_attempt_in_ci", at_utc=T0, evidence={"ci": True})
        self.assertIs(stopped.state, LiveKillState.STOPPED); self.assertTrue(stopped.requires_fresh_preflight); self.assertTrue(stopped.requires_new_approval)
        reset = reset_kill_switch(stopped, fresh_preflight=report, new_approval=approval, at_utc=T0 + timedelta(seconds=1)); self.assertIs(reset.state, LiveKillState.RESET)

    def test_reconciliation_material_difference_blocks(self):
        row = reconcile_live(live_run_id=uuid4(), local_projection={"orders": [], "fills": [], "balances": {"USDT": 10}, "positions": {}, "average_prices": {}, "realized_pnl": 0, "fees": 0, "sequence": 1, "timestamp_utc": T0}, venue_observation={"orders": [], "fills": [], "balances": {"USDT": 9}, "positions": {}, "average_prices": {}, "realized_pnl": 0, "fees": 0, "sequence": 1, "timestamp_utc": T0}, evaluated_at_utc=T0)
        self.assertIs(row.status, LiveReconciliationStatus.BLOCKED); self.assertEqual(row.differences[0]["field"], "balances")


class FakeRepo:
    authoritative_storage = "PostgreSQL"
    def __init__(self, report): self.report = report; self.prepared = []; self.suppressed = False; self.outbox = uuid4(); self.token = uuid4()
    def persisted_preflight(self, report_id): return {"record_sha256": self.report.record_hash, "status": "passed"} if report_id == self.report.report_id else None
    def prepare_dry_run_bundle(self, **kwargs): self.prepared.append(kwargs); return None if not kwargs["risk_decision"].accepted else self.outbox
    def claim_dispatch(self, **kwargs): return (self.outbox, self.token)
    def suppress_claimed_dispatch(self, **kwargs): self.suppressed = True; return True

    def dispatch_state(self, outbox_id): return "dry_run_suppressed" if self.suppressed else "dry_run_prepared"

class DryRunAndCliTests(unittest.TestCase):
    def _context(self, *, limit=Decimal("100")):
        cfg, snap, cred, report, approval, manifest = passed_authority(); projection = {"orders": [], "fills": [], "balances": {}, "positions": {}, "average_prices": {}, "realized_pnl": 0, "fees": 0, "sequence": 1, "timestamp_utc": T0}; reconciliation = reconcile_live(live_run_id=manifest.live_run_id, local_projection=projection, venue_observation=projection, evaluated_at_utc=T0); market = market_evidence(); intent = live_intent(manifest, snap, reconciliation, market, limit=limit); return cfg, snap, report, approval, manifest, reconciliation, market, intent

    def test_truthful_dry_run_persists_then_suppresses_without_venue_write(self):
        cfg, snap, report, approval, manifest, reconciliation, market, intent = self._context(); repo = FakeRepo(report); venue = FakeLiveVenue(); broker = GuardedLiveBroker(configuration=cfg, manifest=manifest, approval=approval, preflight_report=report, repository=repo, venue=venue)
        state = LiveRiskState(Decimal(0), Decimal(0), Decimal(0), Decimal(0), Decimal(0), 0, LiveReconciliationStatus.RECONCILED, LiveKillState.ARMED)
        result = broker.prepare_and_suppress(intent=intent, market_evidence=market, risk_state=state, tick_size=Decimal("0.01"), lot_size=Decimal("0.001"), at_utc=T0)
        self.assertEqual(result.state.value, "dry_run_suppressed"); self.assertTrue(result.external_write_suppressed); self.assertFalse(result.external_write_attempted); self.assertTrue(repo.suppressed); self.assertEqual(venue.write_attempt_count, 0); self.assertEqual(result.transport_plan.request_body["tdMode"], "cash")
        with self.assertRaises(PermissionError): broker.submit_order()
        with self.assertRaises(PermissionError): broker.cancel_order()

    def test_attack_has_no_transport_plan_or_outbox(self):
        cfg, snap, report, approval, manifest, reconciliation, market, intent = self._context(limit=Decimal("50000")); repo = FakeRepo(report); broker = GuardedLiveBroker(configuration=cfg, manifest=manifest, approval=approval, preflight_report=report, repository=repo, venue=FakeLiveVenue()); state = LiveRiskState(Decimal(0), Decimal(0), Decimal(0), Decimal(0), Decimal(0), 0, LiveReconciliationStatus.RECONCILED, LiveKillState.ARMED)
        result = broker.prepare_and_suppress(intent=intent, market_evidence=market, risk_state=state, tick_size=Decimal("0.01"), lot_size=Decimal("0.001"), at_utc=T0)
        self.assertEqual(result.state.value, "dry_run_blocked"); self.assertIsNone(result.transport_plan); self.assertEqual(repo.prepared[0]["request_body"], {}); self.assertFalse(repo.suppressed)

    def test_non_fake_transport_is_rejected_in_ci(self):
        cfg, snap, report, approval, manifest, reconciliation, market, intent = self._context(); transport = type("RealTransport", (), {"is_fake": False})()
        with patch.dict(os.environ, {"CI": "true"}):
            with self.assertRaises(PermissionError): GuardedLiveBroker(configuration=cfg, manifest=manifest, approval=approval, preflight_report=report, repository=FakeRepo(report), venue=transport)

    def test_cli_is_cross_platform_truthful_and_socket_free(self):
        script = os.path.join(os.path.dirname(__file__), "..", "scripts", "run_live_dry_run.py")
        completed = subprocess.run([sys.executable, script], check=True, capture_output=True, text=True)
        payload = json.loads(completed.stdout); self.assertEqual(payload["mode"], "DRY-RUN"); self.assertEqual(payload["production_write_status"], "disabled"); self.assertFalse(payload["network_reads_occurred"]); self.assertFalse(payload["network_writes_occurred"])


if __name__ == "__main__": unittest.main()
