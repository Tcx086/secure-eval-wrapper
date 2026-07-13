from __future__ import annotations

import json
import os
import subprocess
import sys
import unittest
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from unittest.mock import patch
from uuid import uuid4

from secure_eval_wrapper.alpha.identity import SeriesIdentity
from secure_eval_wrapper.data_collection.hashing import sha256_payload
from secure_eval_wrapper.data_collection.models import InstrumentType
from secure_eval_wrapper.execution.models import OrderSide
from secure_eval_wrapper.live.approval import LiveApprovalController, confirmation_challenge_hash, manifest_preview_hash
from secure_eval_wrapper.live.authorities import (
    FixtureOnlyPreflightEvidence,
    LiveEvidenceSource,
    LiveLocalProjection,
    LiveRuntimeRiskState,
    LiveVenueObservation,
    OperationalPreflightEvidence,
    SOURCE_KINDS,
)
from secure_eval_wrapper.live.configuration import GuardedLiveConfiguration, phase8a_dry_run_configuration
from secure_eval_wrapper.live.credentials import InjectedLocalCredentialProvider, redact, validate_permission_summary
from secure_eval_wrapper.live.durable_repository import DurablePostgresLiveRepository
from secure_eval_wrapper.live.endpoints import EndpointClass, LiveOperation, build_request_path, classify_exact, endpoint_catalog_hash
from secure_eval_wrapper.live.gates import evaluate_live_write_authority
from secure_eval_wrapper.live.kill_switch import arm_kill_switch, reset_kill_switch, trigger_kill_switch
from secure_eval_wrapper.live.manifests import create_live_manifest, validate_live_manifest
from secure_eval_wrapper.live.models import LiveAccountSnapshot, LiveKillState, LiveOrderIntent, LivePreflightStatus, LiveRecoveryOutcome, LiveReconciliationStatus, live_uuid
from secure_eval_wrapper.live.preflight import LivePreflightEngine, OperationalPreflightError
from secure_eval_wrapper.live.reconciliation import reconcile_live
from secure_eval_wrapper.live.recovery import query_first_recovery
from secure_eval_wrapper.live.reservations import calculate_live_reservation
from secure_eval_wrapper.live.risk import evaluate_live_risk
from secure_eval_wrapper.live.venues.fake_live import FakeLiveVenue
from secure_eval_wrapper.live.venues.okx_live import OkxProductionSpotAdapter, signed_headers
from secure_eval_wrapper.paper.models import PaperMarketDataEvidence

T0 = datetime(2026, 7, 13, 3, 0, tzinfo=timezone.utc)
H = sha256_payload("phase8-repair-test")
COMMIT = "e60986de598f6b0a397473cc2ff4c1993c813c68"


def identity():
    return SeriesIdentity("okx", "okx", "BTC-USDT", "BTC-USDT", InstrumentType.SPOT, "1m", "USDT")


def config():
    return phase8a_dry_run_configuration(endpoint_catalog_hash=endpoint_catalog_hash(), provider_implementation_hash=OkxProductionSpotAdapter.provider_implementation_hash)


def account(run, *, at=T0, fingerprint="0000000000000000"):
    balances = {
        "USDT": {"total": Decimal("10000"), "available": Decimal("9000"), "reserved": Decimal("1000")},
        "BTC": {"total": Decimal("10"), "available": Decimal("10"), "reserved": Decimal("0")},
    }
    return LiveAccountSnapshot(run, fingerprint, at, at, balances, {}, 0, Decimal("10000"), Decimal("9000"), Decimal("1000"), "spot_cash")


def credential(*, at=T0, permissions=("read", "spot_trade")):
    return InjectedLocalCredentialProvider("placeholder-key", "placeholder-secret", "placeholder-passphrase").reference(verified_at_utc=at, permissions=permissions)


def market_evidence(*, at=T0, price=Decimal("100")):
    ident = identity(); report_id = uuid4(); row_id = str(uuid4()); source_hash = sha256_payload({"source": row_id})
    return PaperMarketDataEvidence(ident, "okx", "BTC-USDT", "bar_close", row_id, at, at, True, "accepted", source_hash, source_hash, exchange="okx", provider_instrument_id="BTC-USDT", instrument_type="spot", source_table="market_data.validated_bars", source_row_id=row_id, validation_report_id=report_id, price=price, price_type="close", quote_currency="USDT", normalized_record_sha256=source_hash, source_kind="postgresql")


def typed_reconciliation(run, snap, *, at=T0):
    local = LiveLocalProjection(run, snap.account_fingerprint, (), (), dict(snap.balances), dict(snap.positions), 1, at, (snap.snapshot_id,))
    venue = LiveVenueObservation(run, snap.account_fingerprint, (), (), dict(snap.balances), dict(snap.positions), 1, at, (H,))
    return reconcile_live(local_projection=local, venue_observation=venue, evaluated_at_utc=at)


def operational_evidence(run, cfg, snap, cred, market, reconciliation, kill, *, at=T0, permission_override=None):
    permissions = tuple(cred.permission_summary if permission_override is None else permission_override)
    payloads = {
        "repository": {"commit_sha": COMMIT, "expected_commit_sha": COMMIT, "implementation_hash": cfg.provider_implementation_hash},
        "migration_catalog": {"catalog_clean": True, "latest_migration": "0023", "immutable_0001_0022": True},
        "postgresql_probe": {"available": True, "transaction_probe": True, "fake_transport": True},
        "audit_rollback_probe": {"write_succeeded": True, "rollback_verified": True},
        "credential_reference": {"reference_id": str(cred.reference_id), "record_hash": cred.record_hash, "credential_material_present": False},
        "credential_permissions": {"permissions": permissions, "credential_record_hash": cred.record_hash},
        "account_config": {"account_exists": True},
        "account_fingerprint": {"observed": snap.account_fingerprint},
        "subaccount": {"observed": cfg.subaccount_fingerprint},
        "account_mode": {"account_mode": "spot_cash"},
        "margin_borrowing": {"margin_enabled": False, "leverage_enabled": False, "borrowing_enabled": False},
        "balances": {"complete": True, "snapshot_hash": snap.record_hash},
        "positions": {"derivative_count": 0, "short_count": 0, "margin_count": 0},
        "open_orders": {"enumerated": True, "count": snap.open_order_count},
        "venue_time": {"venue_time_at_utc": at},
        "market_data": {
            "validated": True, "source_kind": "postgresql", "validated_at_utc": at, "quote_currency": "USDT",
            "market_evidence_sha256": market.evidence_sha256, "provider": "okx", "exchange": "okx", "provider_instrument_id": "BTC-USDT",
            "canonical_symbol": "BTC-USDT", "timeframe": "1m", "event_type": "bar_close", "source_row_id": market.source_row_id,
            "observed_at_utc": market.observed_at_utc, "available_at_utc": market.available_at_utc, "source_sha256": market.source_sha256,
            "normalized_record_sha256": market.normalized_record_sha256, "validation_report_id": str(market.validation_report_id), "price": market.price,
            "price_type": "close",
        },
        "instrument_metadata": {"instrument": "BTC-USDT", "instrument_type": "spot", "tick_size": "0.01", "lot_size": "0.001", "minimum_size": "0.001", "minimum_notional": "1", "maximum_notional": "100000"},
        "reconciliation": {"status": reconciliation.status.value, "evaluated_at_utc": reconciliation.evaluated_at_utc, "reconciliation_id": str(reconciliation.reconciliation_id), "record_hash": reconciliation.record_hash},
        "kill_switch": {"state": kill.state.value, "kill_switch_id": str(kill.kill_switch_id)},
    }
    return OperationalPreflightEvidence(run, tuple(LiveEvidenceSource(run, kind, at, payloads[kind], True) for kind in sorted(SOURCE_KINDS)))


def passed_authority(*, run=None, at=T0, permissions=("read", "spot_trade")):
    run = run or uuid4(); cfg = config(); snap = account(run, at=at); cred = credential(at=at, permissions=permissions); market = market_evidence(at=at)
    reconciliation = typed_reconciliation(run, snap, at=at); kill = arm_kill_switch(live_run_id=run, at_utc=at)
    evidence = operational_evidence(run, cfg, snap, cred, market, reconciliation, kill, at=at)
    report = LivePreflightEngine().evaluate(live_run_id=run, configuration=cfg, account_snapshot=snap, credential_reference=cred, evidence=evidence, evaluated_at_utc=at, implementation_hash=cfg.provider_implementation_hash, repository_commit_sha=COMMIT)
    preview = manifest_preview_hash(live_run_id=run, configuration=cfg, credential_reference_hash=cred.record_hash, preflight_report_id=report.report_id, account_snapshot_hash=snap.record_hash, repository_commit_sha=COMMIT)
    expires = at + timedelta(seconds=300)
    challenge = confirmation_challenge_hash(live_run_id=run, configuration=cfg, account_fingerprint=snap.account_fingerprint, manifest_hash=preview, repository_commit_sha=COMMIT, nonce="nonce-123", approving_actor="local-operator", created_at_utc=at, expires_at_utc=expires, maximum_total_approved_notional=Decimal("5000"))
    approval = LiveApprovalController().create(report=report, configuration=cfg, account_snapshot=snap, manifest_hash=preview, repository_commit_sha=COMMIT, created_at_utc=at, ttl_seconds=300, nonce="nonce-123", approving_actor="local-operator", maximum_total_approved_notional=Decimal("5000"), exact_confirmation_challenge_hash=challenge)
    manifest = create_live_manifest(configuration=cfg, report=report, approval=approval, account_snapshot=snap, credential_reference=cred, repository_commit_sha=COMMIT, at_utc=at)
    return cfg, snap, cred, report, approval, manifest, kill, evidence, market, reconciliation


def runtime_state(run, snap, reconciliation, market, *, at=T0, **overrides):
    values = dict(live_run_id=run, trading_day=at.date(), current_equity=Decimal("10000"), high_watermark_equity=Decimal("10000"), daily_submitted_notional=Decimal(0), daily_realized_pnl=Decimal(0), gross_exposure=Decimal(0), net_exposure=Decimal(0), order_timestamps_utc=(), cancellation_timestamps_utc=(), open_order_count=0, oldest_unknown_order_at_utc=None, oldest_unacknowledged_order_at_utc=None, latest_market_data_at_utc=market.observed_at_utc, latest_account_snapshot_at_utc=snap.fetched_at_utc, latest_reconciliation_at_utc=reconciliation.evaluated_at_utc, latest_reconciliation_status=LiveReconciliationStatus.RECONCILED, clock_skew_seconds=Decimal(0), run_started_at_utc=at, transport_failure_count=0, balances=snap.balances, positions=snap.positions, version=0)
    values.update(overrides); return LiveRuntimeRiskState(**values)


def live_intent(manifest, snap, reconciliation, market, *, side=OrderSide.BUY, reference=Decimal("100"), limit=Decimal("100"), quantity=Decimal("1"), at=T0):
    return LiveOrderIntent(manifest.live_run_id, manifest.manifest_id, identity(), side, quantity, reference, limit, at, market.evidence_id, market.evidence_sha256, H, snap.record_hash, reconciliation.record_hash)


class EvidenceAndAuthorityTests(unittest.TestCase):
    def test_operational_preflight_passes_and_every_check_cites_sources(self):
        *_, report, approval, manifest, kill, evidence, market, reconciliation = passed_authority()
        self.assertIs(report.status, LivePreflightStatus.PASSED); self.assertTrue(report.checks)
        self.assertTrue(all(check.source_ids and len(check.source_ids) == len(check.source_hashes) for check in report.checks))

    def test_all_true_boolean_fixture_never_passes(self):
        run = uuid4(); cfg = config(); snap = account(run); cred = credential(); fixture = FixtureOnlyPreflightEvidence(run, {name: True for name in SOURCE_KINDS})
        with self.assertRaises(OperationalPreflightError): LivePreflightEngine().evaluate(live_run_id=run, configuration=cfg, account_snapshot=snap, credential_reference=cred, evidence=fixture, evaluated_at_utc=T0, implementation_hash=cfg.provider_implementation_hash, repository_commit_sha=COMMIT)
        report = LivePreflightEngine().evaluate(live_run_id=run, configuration=cfg, account_snapshot=snap, credential_reference=cred, evidence=fixture, evaluated_at_utc=T0, implementation_hash=cfg.provider_implementation_hash, repository_commit_sha=COMMIT, test_mode=True)
        self.assertIs(report.status, LivePreflightStatus.BLOCKED)

    def test_actual_unsafe_credential_blocks_even_safe_source_claim(self):
        run = uuid4(); cfg = config(); snap = account(run); cred = credential(permissions=("read", "withdraw")); market = market_evidence(); reconciliation = typed_reconciliation(run, snap); kill = arm_kill_switch(live_run_id=run, at_utc=T0)
        evidence = operational_evidence(run, cfg, snap, cred, market, reconciliation, kill, permission_override=("read", "spot_trade"))
        report = LivePreflightEngine().evaluate(live_run_id=run, configuration=cfg, account_snapshot=snap, credential_reference=cred, evidence=evidence, evaluated_at_utc=T0, implementation_hash=cfg.provider_implementation_hash, repository_commit_sha=COMMIT)
        self.assertIn("credential_permissions", report.blockers)

    def test_direct_start_bundle_binding_attack_is_rejected_before_sql(self):
        ctx_a = passed_authority(); ctx_b = passed_authority()
        with self.assertRaises(ValueError): DurablePostgresLiveRepository._validate_start_bundle(configuration=ctx_a[0], credential_reference=ctx_a[2], account_snapshot=ctx_a[1], report=ctx_a[3], approval=ctx_b[4], manifest=ctx_a[5], kill_switch=ctx_a[6], evidence=ctx_a[7])

    def test_configuration_and_manifest_remain_write_disabled(self):
        cfg, snap, cred, report, approval, manifest, *_ = passed_authority()
        self.assertTrue(cfg.dry_run); self.assertFalse(cfg.production_write_enabled); self.assertFalse(manifest.production_write_enabled)
        validate_live_manifest(manifest, configuration=cfg, report=report, approval=approval, account_snapshot=snap, credential_reference=cred)


class RiskReservationAndRecoveryTests(unittest.TestCase):
    def test_caller_state_type_is_rejected_by_operational_risk(self):
        cfg, snap, cred, report, approval, manifest, kill, evidence, market, reconciliation = passed_authority(); intent = live_intent(manifest, snap, reconciliation, market)
        with self.assertRaises(TypeError): evaluate_live_risk(intent=intent, market_evidence=market, configuration=cfg, state={}, approval=approval, approval_consumed_notional=Decimal(0), kill_switch_state=LiveKillState.ARMED, evaluated_at_utc=T0)

    def test_every_runtime_limit_has_a_derived_blocker(self):
        cfg, snap, cred, report, approval, manifest, kill, evidence, market, reconciliation = passed_authority(); intent = live_intent(manifest, snap, reconciliation, market)
        state = runtime_state(manifest.live_run_id, snap, reconciliation, market, daily_realized_pnl=Decimal("-501"), high_watermark_equity=Decimal("11000"), order_timestamps_utc=tuple(T0 - timedelta(seconds=1) for _ in range(10)), transport_failure_count=3)
        risk = evaluate_live_risk(intent=intent, market_evidence=market, configuration=cfg, state=state, approval=approval, approval_consumed_notional=Decimal(0), kill_switch_state=LiveKillState.STOPPED, evaluated_at_utc=T0)
        self.assertTrue({"maximum_daily_realized_loss", "maximum_drawdown", "maximum_orders_per_minute", "maximum_transport_failures", "kill_switch_not_armed"}.issubset(risk.reasons))

    def test_spot_buy_reserves_quote_plus_maximum_fee(self):
        cfg, snap, cred, report, approval, manifest, kill, evidence, market, reconciliation = passed_authority(); intent = live_intent(manifest, snap, reconciliation, market)
        risk = evaluate_live_risk(intent=intent, market_evidence=market, configuration=cfg, state=runtime_state(manifest.live_run_id, snap, reconciliation, market), approval=approval, approval_consumed_notional=Decimal(0), kill_switch_state=LiveKillState.ARMED, evaluated_at_utc=T0)
        reservation = calculate_live_reservation(intent=intent, risk_decision=risk, maximum_fee_bps=cfg.maximum_fee_bps)
        self.assertEqual(reservation.currency, "USDT"); self.assertEqual(reservation.original_amount, risk.worst_case_order_price + reservation.maximum_fee_amount)
        self.assertEqual(reservation.maximum_fee_amount, risk.risk_notional * cfg.maximum_fee_bps / Decimal(10000))

    def test_spot_sell_reserves_base_quantity_not_quote_notional(self):
        cfg, snap, cred, report, approval, manifest, kill, evidence, market, reconciliation = passed_authority(); intent = live_intent(manifest, snap, reconciliation, market, side=OrderSide.SELL, quantity=Decimal("2"))
        state = runtime_state(manifest.live_run_id, snap, reconciliation, market, positions={"BTC-USDT": {"notional": "500"}})
        risk = evaluate_live_risk(intent=intent, market_evidence=market, configuration=cfg, state=state, approval=approval, approval_consumed_notional=Decimal(0), kill_switch_state=LiveKillState.ARMED, evaluated_at_utc=T0)
        reservation = calculate_live_reservation(intent=intent, risk_decision=risk, maximum_fee_bps=cfg.maximum_fee_bps)
        self.assertEqual(reservation.currency, "BTC"); self.assertEqual(reservation.original_amount, Decimal("2")); self.assertNotEqual(reservation.original_amount, reservation.risk_notional)

    def test_observed_order_and_fill_are_incidents_not_suppression(self):
        expected = {"instrument": "BTC-USDT", "client_order_id": "client1", "side": "buy", "quantity": Decimal("1"), "limit_price": Decimal("100")}
        order = {"ordId": "o1", "clOrdId": "client1", "instId": "BTC-USDT", "state": "live", "side": "buy", "sz": "1", "px": "100"}
        bundle = query_first_recovery(live_run_id=uuid4(), venue=FakeLiveVenue(orders=[order]), instrument="BTC-USDT", client_order_id="client1", queried_at_utc=T0, expected_intent=expected, account_fingerprint="acct")
        self.assertIs(bundle.outcome, LiveRecoveryOutcome.OBSERVED_EXTERNAL_ORDER)
        fill = {"tradeId": "f1", "ordId": "o1", "clOrdId": "client1", "instId": "BTC-USDT", "side": "buy", "fillSz": "1", "fillPx": "100", "fee": "-0.1", "feeCcy": "USDT"}
        bundle = query_first_recovery(live_run_id=uuid4(), venue=FakeLiveVenue(orders=[order], fills=[fill]), instrument="BTC-USDT", client_order_id="client1", queried_at_utc=T0, expected_intent=expected, account_fingerprint="acct")
        self.assertIs(bundle.outcome, LiveRecoveryOutcome.OBSERVED_EXTERNAL_FILL)

    def test_absent_query_is_typed_confirmed_absent(self):
        expected = {"instrument": "BTC-USDT", "client_order_id": "client1", "side": "buy", "quantity": Decimal("1"), "limit_price": Decimal("100")}
        bundle = query_first_recovery(live_run_id=uuid4(), venue=FakeLiveVenue(), instrument="BTC-USDT", client_order_id="client1", queried_at_utc=T0, expected_intent=expected, account_fingerprint="acct")
        self.assertIs(bundle.outcome, LiveRecoveryOutcome.CONFIRMED_ABSENT)

    def test_old_preflight_and_approval_cannot_reset(self):
        cfg, snap, cred, report, approval, manifest, kill, evidence, market, reconciliation = passed_authority(); stopped = trigger_kill_switch(kill, reason="manual", at_utc=T0 + timedelta(seconds=1), evidence={})
        with self.assertRaises(PermissionError): reset_kill_switch(stopped, fresh_preflight=report, new_approval=approval, at_utc=T0 + timedelta(seconds=2))


class OkxAndBoundaryTests(unittest.TestCase):
    def test_nonzero_per_order_scode_is_rejected(self):
        with self.assertRaises(ValueError): OkxProductionSpotAdapter.parse_order_response({"code": "0", "data": [{"ordId": "1", "clOrdId": "c", "sCode": "51008", "sMsg": "insufficient"}]})

    def test_read_parsers_validate_identity_and_numeric_fields(self):
        parsed = OkxProductionSpotAdapter.parse_venue_time({"code": "0", "data": [{"ts": "1783911600000"}]}); self.assertIn("venue_time_at_utc", parsed)
        ticker = OkxProductionSpotAdapter.parse_ticker({"code": "0", "data": [{"instId": "BTC-USDT", "last": "100", "ts": "1783911600000"}]}, expected_instrument="BTC-USDT"); self.assertEqual(ticker["last"], Decimal("100"))
        with self.assertRaises(ValueError): OkxProductionSpotAdapter.parse_ticker({"code": "0", "data": [{"instId": "ETH-USDT", "last": "100", "ts": "1783911600000"}]}, expected_instrument="BTC-USDT")

    def test_all_okx_read_parsers_accept_exact_spot_evidence(self):
        ts = "1783911600000"
        instrument_payload = {
            "code": "0",
            "data": [{
                "instType": "SPOT", "instId": "BTC-USDT", "baseCcy": "BTC",
                "quoteCcy": "USDT", "tickSz": "0.01", "lotSz": "0.001",
                "minSz": "0.001", "state": "live",
            }],
        }
        account_payload = {
            "code": "0",
            "data": [{
                "uid": "redacted-account", "acctLv": "1", "posMode": "long_short_mode",
                "autoLoan": "false", "enableSpotBorrow": "false",
            }],
        }
        balances_payload = {
            "code": "0",
            "data": [{
                "totalEq": "1000", "uTime": ts,
                "details": [{"ccy": "USDT", "eq": "1000", "availEq": "900", "frozenBal": "100"}],
            }],
        }
        positions_payload = {
            "code": "0",
            "data": [{
                "instId": "BTC-USDT", "instType": "SPOT", "pos": "1",
                "avgPx": "100", "upl": "2", "uTime": ts,
            }],
        }
        order = {
            "ordId": "o1", "clOrdId": "client1", "instId": "BTC-USDT",
            "side": "buy", "sz": "1", "px": "100", "state": "live",
            "accFillSz": "0", "cTime": ts, "uTime": ts,
        }
        order_payload = {"code": "0", "data": [order]}
        fill_payload = {
            "code": "0",
            "data": [{
                "tradeId": "f1", "ordId": "o1", "clOrdId": "client1",
                "instId": "BTC-USDT", "side": "buy", "fillSz": "1",
                "fillPx": "100", "fee": "-0.1", "feeCcy": "USDT", "ts": ts,
            }],
        }
        self.assertEqual(len(OkxProductionSpotAdapter.parse_instruments(instrument_payload, expected_instrument="BTC-USDT")), 1)
        self.assertEqual(OkxProductionSpotAdapter.parse_account_config(account_payload)["account_mode"], "spot_cash")
        self.assertEqual(OkxProductionSpotAdapter.parse_balances(balances_payload)["total_equity"], Decimal("1000"))
        self.assertEqual(len(OkxProductionSpotAdapter.parse_positions(positions_payload)), 1)
        self.assertEqual(OkxProductionSpotAdapter.parse_order_details(order_payload, expected_instrument="BTC-USDT", expected_client_order_id="client1")["ordId"], "o1")
        self.assertEqual(len(OkxProductionSpotAdapter.parse_pending_orders(order_payload, expected_instrument="BTC-USDT")), 1)
        self.assertEqual(len(OkxProductionSpotAdapter.parse_order_history(order_payload, expected_instrument="BTC-USDT")), 1)
        self.assertEqual(len(OkxProductionSpotAdapter.parse_fills_history(fill_payload, expected_instrument="BTC-USDT")), 1)
        accepted = OkxProductionSpotAdapter.parse_order_response(
            {"code": "0", "data": [{"ordId": "o1", "clOrdId": "client1", "sCode": "0", "sMsg": ""}]}
        )
        self.assertEqual(accepted["sCode"], "0")

    def test_okx_read_parsers_reject_borrowing_invalid_state_and_fill_side(self):
        account = {
            "code": "0",
            "data": [{
                "uid": "redacted-account", "acctLv": "1", "posMode": "long_short_mode",
                "autoLoan": "false", "enableSpotBorrow": "true",
            }],
        }
        with self.assertRaises(ValueError):
            OkxProductionSpotAdapter.parse_account_config(account)
        order = {
            "ordId": "o1", "clOrdId": "client1", "instId": "BTC-USDT",
            "side": "buy", "sz": "1", "px": "100", "state": "unknown",
            "accFillSz": "0", "cTime": "1783911600000", "uTime": "1783911600000",
        }
        with self.assertRaises(ValueError):
            OkxProductionSpotAdapter.parse_pending_orders(
                {"code": "0", "data": [order]},
                expected_instrument="BTC-USDT",
            )
        fill = {
            "tradeId": "f1", "ordId": "o1", "clOrdId": "client1",
            "instId": "BTC-USDT", "side": "invalid", "fillSz": "1",
            "fillPx": "100", "fee": "-0.1", "feeCcy": "USDT", "ts": "1783911600000",
        }
        with self.assertRaises(ValueError):
            OkxProductionSpotAdapter.parse_fills_history(
                {"code": "0", "data": [fill]},
                expected_instrument="BTC-USDT",
            )

    def test_endpoint_and_write_boundaries(self):
        self.assertEqual(classify_exact("GET", "/api/v5/account/balance"), EndpointClass.AUTHENTICATED_READ)
        self.assertEqual(classify_exact("POST", "/api/v5/asset/withdrawal"), EndpointClass.FORBIDDEN)
        with self.assertRaises(PermissionError): build_request_path(LiveOperation.SUBMIT_LIMIT_ORDER)
        adapter = OkxProductionSpotAdapter(transport=object())
        with self.assertRaises(PermissionError): adapter.submit_order({})
        with self.assertRaises(PermissionError): adapter.cancel_order({})
        self.assertEqual(adapter.network_writes, 0)

    def test_permissions_redaction_and_ci_gate(self):
        for permissions in ((), ("read", "withdraw"), ("read", "borrow"), ("read", "mystery")):
            with self.assertRaises(PermissionError): validate_permission_summary(permissions)
        payload = redact({"OK-ACCESS-KEY": "secret", "nested": {"passphrase": "secret"}}); self.assertNotIn("secret", json.dumps(payload))
        cfg, snap, cred, report, approval, manifest, *_ = passed_authority()
        authority = evaluate_live_write_authority(configuration=cfg, cli_enable_live_execution=True, approval=approval, exact_confirmation_challenge_hash=approval.confirmation_challenge_hash, at_utc=T0 + timedelta(seconds=1), environment={"CI": "true", "SECURE_EVAL_ENABLE_LIVE_EXECUTION": "true"})
        self.assertFalse(authority.allowed); self.assertTrue(authority.ci_prohibited)

    def test_signature_is_deterministic_without_exposing_material(self):
        provider = InjectedLocalCredentialProvider("placeholder-key", "placeholder-secret", "placeholder-passphrase")
        gates = {"read_only_preflight": True, "provider_selected": True, "production_environment": True, "endpoint_catalog_valid": True, "configuration_valid": True, "production_writes_disabled": True, "kill_switch_armed": True, "postgresql_available": True}
        with patch.dict(os.environ, {"CI": "false", "GITHUB_ACTIONS": "false"}, clear=False): material = provider.load(gates=gates)
        headers = signed_headers(credential_material=material, method="GET", request_path="/api/v5/account/balance", timestamp="2026-07-13T03:00:00.000Z")
        self.assertEqual(headers, signed_headers(credential_material=material, method="GET", request_path="/api/v5/account/balance", timestamp="2026-07-13T03:00:00.000Z"))

    def test_arbitrary_dicts_cannot_claim_reconciled(self):
        with self.assertRaises(TypeError): reconcile_live(local_projection={}, venue_observation={}, evaluated_at_utc=T0)

    def test_cli_without_explicit_run_is_nonzero_and_write_free(self):
        script = os.path.join(os.path.dirname(__file__), "..", "scripts", "run_live_status.py")
        completed = subprocess.run([sys.executable, script], capture_output=True, text=True)
        self.assertNotEqual(completed.returncode, 0); self.assertNotIn("network_writes_occurred\":true", completed.stdout.lower())


if __name__ == "__main__": unittest.main()
