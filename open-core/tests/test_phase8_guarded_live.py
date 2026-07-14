from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
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
    _issue_verified_source,
    VerifiedOperationalSource,
    OperationalPreflightEvidence,
    SOURCE_KINDS,
)
from secure_eval_wrapper.live.configuration import GuardedLiveConfiguration, phase8a_dry_run_configuration
from secure_eval_wrapper.live.credentials import InjectedLocalCredentialProvider, LiveCredentialMaterial, redact, validate_permission_summary
from secure_eval_wrapper.live.collector_evidence import QueryDisposition, VerifiedOkxResponseEnvelope
from secure_eval_wrapper.live.durable_repository import DurablePostgresLiveRepository
from secure_eval_wrapper.live.endpoints import EndpointClass, LiveOperation, build_request_path, classify_exact, endpoint_catalog_hash
from secure_eval_wrapper.live.gates import evaluate_live_write_authority
from secure_eval_wrapper.live.identity import derive_okx_account_fingerprint, resolve_runtime_repository_identity
from secure_eval_wrapper.live.kill_switch import arm_kill_switch, reset_kill_switch, trigger_kill_switch
from secure_eval_wrapper.live.manifests import create_live_manifest, validate_live_manifest
from secure_eval_wrapper.live.models import LiveAccountSnapshot, LiveKillState, LiveObservationBundle, LiveOrderIntent, LivePreflightStatus, LiveRecoveryOutcome, LiveReconciliationStatus, live_uuid
from secure_eval_wrapper.live.preflight import LivePreflightEngine, OperationalPreflightError
from secure_eval_wrapper.live.reconciliation import reconcile_live
from secure_eval_wrapper.live.recovery import normalize_verified_recovery_observation, query_first_recovery
from secure_eval_wrapper.live.reservations import calculate_live_reservation
from secure_eval_wrapper.live.risk import evaluate_live_risk
from secure_eval_wrapper.live.venues.fake_live import FakeLiveVenue
from secure_eval_wrapper.live.venues.okx_live import OkxProductionSpotAdapter, signed_headers
from secure_eval_wrapper.paper.models import PaperMarketDataEvidence

T0 = datetime(2026, 7, 13, 3, 0, tzinfo=timezone.utc)
H = sha256_payload("phase8-repair-test")
OKX_UID = "redacted-account"
ACCOUNT_FINGERPRINT = derive_okx_account_fingerprint(OKX_UID)
RUNTIME_IDENTITY = resolve_runtime_repository_identity()
COMMIT = RUNTIME_IDENTITY.observed_commit_sha
_PREFLIGHT_BUNDLES = {}
_RECONCILIATION_DETAILS = {}
_METADATA_SOURCES = {}
_MARKET_SOURCES = {}


class ExactOkxTransport:
    is_fake = True

    def __init__(self, *, at=T0, uid=OKX_UID, main_uid=None, perm="read_only", overrides=None):
        self.at = at
        self.uid = uid
        self.main_uid = uid if main_uid is None else main_uid
        self.perm = perm
        self.overrides = dict(overrides or {})

    def execute(self, *, method, url, headers, body):
        if method != "GET":
            raise PermissionError("test transport is read-only")
        ts = str(int(self.at.timestamp() * 1000))
        responses = {
            "/api/v5/account/config": {"code": "0", "data": [{
                "uid": self.uid, "mainUid": self.main_uid, "perm": self.perm, "acctLv": "1", "posMode": "long_short_mode",
                "autoLoan": "false", "enableSpotBorrow": "false",
            }]},
            "/api/v5/account/balance": {"code": "0", "data": [{
                "totalEq": "10000", "uTime": ts, "details": [
                    {"ccy": "USDT", "eq": "10000", "availEq": "9000", "frozenBal": "1000"},
                    {"ccy": "BTC", "eq": "10", "availEq": "10", "frozenBal": "0"},
                ],
            }]},
            "/api/v5/account/positions": {"code": "0", "data": []},
            "/api/v5/trade/orders-pending": {"code": "0", "data": []},
            "/api/v5/trade/orders-history": {"code": "0", "data": []},
            "/api/v5/trade/fills-history": {"code": "0", "data": []},
            "/api/v5/public/time": {"code": "0", "data": [{"ts": ts}]},
            "/api/v5/public/instruments": {"code": "0", "data": [{
                "instType": "SPOT", "instId": "BTC-USDT", "baseCcy": "BTC",
                "quoteCcy": "USDT", "tickSz": "0.01", "lotSz": "0.001",
                "minSz": "0.001", "minNotional": "1", "state": "live",
            }]},
            "/api/v5/trade/order": {"code": "0", "data": []},
        }
        responses.update(self.overrides)
        path = url.removeprefix("https://www.okx.com").split("?", 1)[0]
        value = responses[path]
        if isinstance(value, BaseException):
            raise value
        return value


def exact_okx_bundle(
    run, purpose, *, at=T0, uid=OKX_UID, main_uid=None,
    expected_account_fingerprint=None, expected_subaccount_fingerprint=None,
    client_order_id=None, venue_sequence=1, perm="read_only", overrides=None,
):
    expected_account_fingerprint = (
        derive_okx_account_fingerprint(uid)
        if expected_account_fingerprint is None
        else expected_account_fingerprint
    )
    adapter = OkxProductionSpotAdapter(
        transport=ExactOkxTransport(at=at, uid=uid, main_uid=main_uid, perm=perm, overrides=overrides),
        credential_material=LiveCredentialMaterial("placeholder-key", "placeholder-secret", "placeholder-passphrase"),
        clock=lambda: at,
    )
    return adapter.collect_read_observation_bundle(
        live_run_id=run, purpose=purpose, instrument="BTC-USDT",
        expected_account_fingerprint=expected_account_fingerprint,
        expected_subaccount_fingerprint=expected_subaccount_fingerprint,
        client_order_id=client_order_id,
        venue_sequence=venue_sequence,
    )



def identity():
    return SeriesIdentity("okx", "okx", "BTC-USDT", "BTC-USDT", InstrumentType.SPOT, "1m", "USDT")


def config(*, fingerprint=ACCOUNT_FINGERPRINT):
    return phase8a_dry_run_configuration(account_fingerprint=fingerprint, endpoint_catalog_hash=endpoint_catalog_hash(), provider_implementation_hash=OkxProductionSpotAdapter.provider_implementation_hash)


def account(run, *, at=T0, fingerprint=ACCOUNT_FINGERPRINT):
    balances = {
        "USDT": {"total": Decimal("10000"), "available": Decimal("9000"), "reserved": Decimal("1000")},
        "BTC": {"total": Decimal("10"), "available": Decimal("10"), "reserved": Decimal("0")},
    }
    return LiveAccountSnapshot(run, fingerprint, at, at, balances, {}, 0, Decimal("10000"), Decimal("9000"), Decimal("1000"), "spot_cash")


def credential(*, at=T0, permissions=("read",), fingerprint=ACCOUNT_FINGERPRINT):
    return InjectedLocalCredentialProvider("placeholder-key", "placeholder-secret", "placeholder-passphrase", expected_account_fingerprint=fingerprint).reference(verified_at_utc=at, permissions=permissions)


def market_evidence(*, at=T0, price=Decimal("100")):
    ident = identity(); report_id = live_uuid("test-market-report", {"at": at, "price": price}); row_id = str(live_uuid("test-market-row", {"at": at, "price": price})); source_hash = sha256_payload({"source": row_id})
    return PaperMarketDataEvidence(ident, "okx", "BTC-USDT", "bar_close", row_id, at, at, True, "accepted", source_hash, source_hash, exchange="okx", provider_instrument_id="BTC-USDT", instrument_type="spot", source_table="market_data.validated_bars", source_row_id=row_id, validation_report_id=report_id, price=price, price_type="close", quote_currency="USDT", normalized_record_sha256=source_hash, source_kind="postgresql")


def typed_reconciliation(run, snap, *, at=T0):
    local = LiveLocalProjection(run, snap.account_fingerprint, (), (), dict(snap.balances), dict(snap.positions), 1, at, (snap.snapshot_id,))
    bundle = exact_okx_bundle(run, "reconciliation", at=at, expected_account_fingerprint=snap.account_fingerprint)
    reconciliation, exact_input = reconcile_live(
        local_projection=local, okx_bundle=bundle, evaluated_at_utc=at,
        freshness_seconds=30, maximum_clock_skew_seconds=5,
    )
    _RECONCILIATION_DETAILS[reconciliation.reconciliation_id] = (exact_input, bundle)
    return reconciliation


def operational_evidence(run, cfg, snap, cred, market, reconciliation, kill, *, at=T0, permission_override=None):
    permissions = tuple(cred.permission_summary if permission_override is None else permission_override)
    bundle = exact_okx_bundle(run, "preflight", at=at, expected_account_fingerprint=snap.account_fingerprint)
    _PREFLIGHT_BUNDLES[run] = bundle
    metadata = dict(bundle.envelope("instrument_metadata").normalized_payload[0])
    raw_id = str(market.source_row_id)
    migration_root = Path(__file__).resolve().parents[1] / "db" / "migrations"
    observed_hashes = {
        path.stem: hashlib.sha256(path.read_bytes().replace(b"\r\n", b"\n")).hexdigest()
        for path in sorted(migration_root.glob("*.sql"))
        if path.name[:4].isdigit()
    }
    expected_hashes = {
        migration_id: digest
        for migration_id, digest in observed_hashes.items()
        if migration_id[:4] <= "0024"
    }
    account_config = dict(bundle.envelope("account_config").normalized_payload)
    observed_subaccount_fingerprint = bundle.account_fingerprint if account_config.get("is_subaccount") is True else None
    payloads = {
        "repository": {"observed_commit_sha": COMMIT, "expected_reviewed_sha": COMMIT, "identity_source": RUNTIME_IDENTITY.identity_source, "resolver_version": RUNTIME_IDENTITY.resolver_version, "implementation_hash": cfg.provider_implementation_hash},
        "migration_catalog": {"catalog_clean": True, "latest_migration": "0025", "immutable_0001_0024": True, "expected_hashes_0001_0024": expected_hashes, "observed_hashes": observed_hashes},
        "postgresql_probe": {"available": True, "transaction_probe": True, "fake_transport": True},
        "audit_rollback_probe": {"write_succeeded": True, "rollback_verified": True},
        "credential_reference": {"reference_id": str(cred.reference_id), "record_hash": cred.record_hash, "credential_material_present": False},
        "credential_permissions": {
            "provider_permissions": tuple(account_config["provider_permissions"]),
            "normalized_permissions": tuple(account_config["normalized_permissions"]),
            "expected_permissions": permissions,
            "credential_reference_id": str(cred.reference_id),
            "credential_record_hash": cred.record_hash,
            "response_bundle_id": str(bundle.bundle_id),
            "account_config_response_sha256": bundle.envelope("account_config").canonical_response_hash,
            "parser_version": bundle.parser_version,
            "verified_at_utc": bundle.envelope("account_config").query_completed_at_utc,
            "policy_version": "phase8a-read-only-v1",
        },
        "account_config": {"account_exists": True, "account_mode": account_config["account_mode"], "is_subaccount": account_config.get("is_subaccount") is True, "account_type": str(account_config.get("type", ""))},
        "account_fingerprint": {"observed": bundle.account_fingerprint, "derivation": "sha256(canonical_json({provider:okx,account_uid:exact_uid}))[:16]"},
        "subaccount": {"observed": observed_subaccount_fingerprint, "proven_by": "uid_ne_mainUid" if observed_subaccount_fingerprint is not None else "uid_eq_mainUid"},
        "account_mode": {"account_mode": "spot_cash"},
        "margin_borrowing": {"margin_enabled": False, "leverage_enabled": False, "borrowing_enabled": False},
        "balances": {"complete": True, "snapshot_hash": snap.record_hash},
        "positions": {"derivative_count": 0, "short_count": 0, "margin_count": 0},
        "open_orders": {"enumerated": True, "count": snap.open_order_count},
        "venue_time": {"venue_time_at_utc": at},
        "market_data": {
            "validated": True, "source_kind": "postgresql", "validated_at_utc": at, "quote_currency": "USDT",
            "validation_status": "accepted", "report_status": "accepted",
            "raw_observation_ids": (raw_id,), "raw_observation_hashes": {raw_id: market.source_sha256},
            "finality_verified": True, "quarantine_clear": True,
            "market_evidence_sha256": market.evidence_sha256, "provider": "okx", "exchange": "okx", "provider_instrument_id": "BTC-USDT",
            "canonical_symbol": "BTC-USDT", "timeframe": "1m", "event_type": "bar_close", "source_row_id": market.source_row_id,
            "observed_at_utc": market.observed_at_utc, "available_at_utc": market.available_at_utc, "source_sha256": market.source_sha256,
            "normalized_record_sha256": market.normalized_record_sha256, "validation_report_id": str(market.validation_report_id), "price": market.price,
            "price_type": "close",
        },
        "instrument_metadata": {**metadata, "response_bundle_id": str(bundle.bundle_id), "provider_response_hash": bundle.envelope("instrument_metadata").canonical_response_hash, "maximum_notional": "100000"},
        "reconciliation": {"status": reconciliation.status.value, "evaluated_at_utc": reconciliation.evaluated_at_utc, "reconciliation_id": str(reconciliation.reconciliation_id), "record_hash": reconciliation.record_hash, "input_bundle_id": str(reconciliation.response_bundle_id)},
        "kill_switch": {"state": kill.state.value, "kill_switch_id": str(kill.kill_switch_id), "version": 0, "evidence_hash": kill.evidence_hash},
    }
    endpoint_by_kind = {
        "credential_permissions": "account_config", "account_config": "account_config", "account_fingerprint": "account_config",
        "subaccount": "account_config", "account_mode": "account_config",
        "margin_borrowing": "account_config", "balances": "balances",
        "positions": "positions", "open_orders": "pending_orders",
        "venue_time": "venue_time", "instrument_metadata": "instrument_metadata",
    }
    sources = []
    for kind in sorted(SOURCE_KINDS):
        endpoint_kind = endpoint_by_kind.get(kind)
        envelope = None if endpoint_kind is None else bundle.envelope(endpoint_kind)
        raw_hash = envelope.canonical_response_hash if envelope is not None else sha256_payload({"kind": kind, "payload": payloads[kind]})
        collector_kind = {
            "repository": "runtime_repository_identity_resolver",
            "migration_catalog": "repository_migration_catalog",
            "postgresql_probe": "postgresql_transaction_probe",
            "audit_rollback_probe": "postgresql_rollback_probe",
            "credential_permissions": "okx_account_config_permission_collector",
        }.get(kind, "okx_read_only_adapter" if envelope is not None else "offline_exact_test_collector")
        source = _issue_verified_source(
            source_kind=kind, live_run_id=run, collected_at_utc=at, payload=payloads[kind],
            collector_kind=collector_kind, collector_version="phase8a-0025-v1",
            parser_version=None if envelope is None else bundle.parser_version,
            source_system_identity="okx-production-read" if envelope is not None else "test-postgresql-authority",
            source_record_identity=str(bundle.bundle_id) if envelope is not None else (COMMIT if kind == "repository" else f"{kind}:{raw_hash}"),
            raw_response_hash=raw_hash, normalized_payload_hash=sha256_payload(payloads[kind]),
        )
        sources.append(source)
        if kind == "instrument_metadata":
            _METADATA_SOURCES[run] = source
        elif kind == "market_data":
            _MARKET_SOURCES[run] = source
    return OperationalPreflightEvidence(run, tuple(sources))


def passed_authority(*, run=None, at=T0, permissions=("read",)):
    run = run or uuid4(); cfg = config(); snap = account(run, at=at); cred = credential(at=at, permissions=permissions); market = market_evidence(at=at)
    reconciliation = typed_reconciliation(run, snap, at=at); kill = arm_kill_switch(live_run_id=run, at_utc=at)
    evidence = operational_evidence(run, cfg, snap, cred, market, reconciliation, kill, at=at)
    report = LivePreflightEngine().evaluate(live_run_id=run, configuration=cfg, account_snapshot=snap, credential_reference=cred, evidence=evidence, evaluated_at_utc=at, implementation_hash=cfg.provider_implementation_hash)
    preview = manifest_preview_hash(live_run_id=run, configuration=cfg, credential_reference_hash=cred.record_hash, preflight_report_id=report.report_id, account_snapshot_hash=snap.record_hash, repository_commit_sha=report.repository_commit_sha)
    expires = at + timedelta(seconds=300)
    challenge = confirmation_challenge_hash(live_run_id=run, configuration=cfg, account_fingerprint=snap.account_fingerprint, manifest_hash=preview, repository_commit_sha=report.repository_commit_sha, nonce="nonce-123", approving_actor="local-operator", created_at_utc=at, expires_at_utc=expires, maximum_total_approved_notional=Decimal("5000"))
    approval = LiveApprovalController().create(report=report, configuration=cfg, account_snapshot=snap, manifest_hash=preview, created_at_utc=at, ttl_seconds=300, nonce="nonce-123", approving_actor="local-operator", maximum_total_approved_notional=Decimal("5000"), exact_confirmation_challenge_hash=challenge)
    manifest = create_live_manifest(configuration=cfg, report=report, approval=approval, account_snapshot=snap, credential_reference=cred, at_utc=at)
    return cfg, snap, cred, report, approval, manifest, kill, evidence, market, reconciliation


def runtime_state(run, snap, reconciliation, market, *, at=T0, **overrides):
    values = dict(live_run_id=run, trading_day=at.date(), current_equity=Decimal("10000"), high_watermark_equity=Decimal("10000"), daily_submitted_notional=Decimal(0), daily_realized_pnl=Decimal(0), gross_exposure=Decimal(0), net_exposure=Decimal(0), order_timestamps_utc=(), cancellation_timestamps_utc=(), open_order_count=0, oldest_unknown_order_at_utc=None, oldest_unacknowledged_order_at_utc=None, latest_market_data_at_utc=market.observed_at_utc, latest_account_snapshot_at_utc=snap.fetched_at_utc, latest_reconciliation_at_utc=reconciliation.evaluated_at_utc, latest_reconciliation_status=LiveReconciliationStatus.RECONCILED, clock_skew_seconds=Decimal(0), run_started_at_utc=at, transport_failure_count=0, balances=snap.balances, positions=snap.positions, version=0)
    values.update(overrides); return LiveRuntimeRiskState(**values)


def live_intent(manifest, snap, reconciliation, market, *, side=OrderSide.BUY, reference=Decimal("100"), limit=Decimal("100"), quantity=Decimal("1"), at=T0):
    metadata = _METADATA_SOURCES.get(manifest.live_run_id)
    market_source = _MARKET_SOURCES.get(manifest.live_run_id)
    return LiveOrderIntent(
        manifest.live_run_id, manifest.manifest_id, identity(), side, quantity, reference, limit, at,
        market.evidence_id if market_source is None else market_source.source_id,
        market.evidence_sha256 if market_source is None else market_source.source_hash,
        metadata.source_hash if metadata else H,
        snap.record_hash, reconciliation.record_hash,
        instrument_metadata_source_id=None if metadata is None else metadata.source_id,
    )


class EvidenceAndAuthorityTests(unittest.TestCase):
    def test_operational_preflight_passes_and_every_check_cites_sources(self):
        *_, report, approval, manifest, kill, evidence, market, reconciliation = passed_authority()
        self.assertIs(report.status, LivePreflightStatus.PASSED); self.assertTrue(report.checks)
        self.assertTrue(all(check.source_ids and len(check.source_ids) == len(check.source_hashes) for check in report.checks))

    def test_all_true_boolean_fixture_never_passes(self):
        run = uuid4(); cfg = config(); snap = account(run); cred = credential(); fixture = FixtureOnlyPreflightEvidence(run, {name: True for name in SOURCE_KINDS})
        with self.assertRaises(OperationalPreflightError): LivePreflightEngine().evaluate(live_run_id=run, configuration=cfg, account_snapshot=snap, credential_reference=cred, evidence=fixture, evaluated_at_utc=T0, implementation_hash=cfg.provider_implementation_hash)
        report = LivePreflightEngine().evaluate(live_run_id=run, configuration=cfg, account_snapshot=snap, credential_reference=cred, evidence=fixture, evaluated_at_utc=T0, implementation_hash=cfg.provider_implementation_hash, test_mode=True)
        self.assertIs(report.status, LivePreflightStatus.BLOCKED)

    def test_actual_unsafe_credential_blocks_even_safe_source_claim(self):
        run = uuid4(); cfg = config(); snap = account(run); cred = credential(permissions=("read", "withdraw")); market = market_evidence(); reconciliation = typed_reconciliation(run, snap); kill = arm_kill_switch(live_run_id=run, at_utc=T0)
        evidence = operational_evidence(run, cfg, snap, cred, market, reconciliation, kill, permission_override=("read", "spot_trade"))
        report = LivePreflightEngine().evaluate(live_run_id=run, configuration=cfg, account_snapshot=snap, credential_reference=cred, evidence=evidence, evaluated_at_utc=T0, implementation_hash=cfg.provider_implementation_hash)
        self.assertIn("credential_permissions", report.blockers)

    def test_direct_start_bundle_binding_attack_is_rejected_before_sql(self):
        ctx_a = passed_authority(); ctx_b = passed_authority()
        with self.assertRaises(ValueError): DurablePostgresLiveRepository._validate_start_bundle(configuration=ctx_a[0], credential_reference=ctx_a[2], account_snapshot=ctx_a[1], report=ctx_a[3], approval=ctx_b[4], manifest=ctx_a[5], kill_switch=ctx_a[6], evidence=ctx_a[7], okx_bundle=_PREFLIGHT_BUNDLES[ctx_a[5].live_run_id])

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
                "uid": OKX_UID, "mainUid": OKX_UID, "perm": "read_only", "acctLv": "1", "posMode": "long_short_mode",
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
        provider = InjectedLocalCredentialProvider("placeholder-key", "placeholder-secret", "placeholder-passphrase", expected_account_fingerprint=ACCOUNT_FINGERPRINT)
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


class ExactOperationalEvidenceRegressionTests(unittest.TestCase):
    @staticmethod
    def expected(client_order_id="client1"):
        return {
            "instrument": "BTC-USDT", "client_order_id": client_order_id,
            "side": "buy", "quantity": Decimal("1"), "limit_price": Decimal("100"),
        }

    def test_public_models_cannot_self_declare_operational_authority(self):
        run = uuid4()
        for kind, payload in (
            ("repository", {}),
            ("migration_catalog", {"catalog_clean": True, "immutable_0001_0023": True}),
            ("postgresql_probe", {"available": True, "transaction_probe": True}),
        ):
            with self.subTest(kind=kind), self.assertRaises(PermissionError):
                LiveEvidenceSource(run, kind, T0, payload, True)
        with self.assertRaises(PermissionError):
            VerifiedOperationalSource(
                live_run_id=uuid4(), source_kind="repository", collected_at_utc=T0,
                payload={}, collector_kind="caller", collector_version="caller",
                parser_version=None, source_system_identity="caller",
                source_record_identity="caller", raw_response_hash=H,
                normalized_payload_hash=H,
            )
        with self.assertRaises(PermissionError):
            VerifiedOkxResponseEnvelope(
                endpoint_kind="balances", request_identity=H,
                request_path="/api/v5/account/balance",
                query_started_at_utc=T0, query_completed_at_utc=T0,
                disposition=QueryDisposition.COMPLETED,
                raw_response={"code": "0", "data": []},
                normalized_payload={}, parser_version="caller",
            )

    def test_manual_recovery_bundle_is_not_operational_authority(self):
        manual = LiveObservationBundle(
            uuid4(), "client1", None, (), (), (),
            {"account_fingerprint": "acct", "query_timestamp_utc": T0},
            T0, LiveRecoveryOutcome.CONFIRMED_ABSENT,
        )
        with self.assertRaises(TypeError):
            normalize_verified_recovery_observation(
                manual, expected_intent=self.expected(), account_fingerprint="acct",
            )

    def test_recovery_transport_ambiguity_is_inconclusive(self):
        run = uuid4()
        bundle = exact_okx_bundle(
            run, "recovery", client_order_id="client1",
            expected_account_fingerprint=ACCOUNT_FINGERPRINT,
            overrides={"/api/v5/trade/order": TimeoutError("timeout")},
        )
        observed = normalize_verified_recovery_observation(
            bundle, expected_intent=self.expected(), account_fingerprint=ACCOUNT_FINGERPRINT,
        )
        self.assertIs(observed.outcome, LiveRecoveryOutcome.INCONCLUSIVE)
        self.assertFalse(bundle.complete)

    def test_only_explicit_nonzero_provider_code_is_provider_rejected(self):
        run = uuid4()
        bundle = exact_okx_bundle(
            run, "recovery", client_order_id="client1",
            expected_account_fingerprint=ACCOUNT_FINGERPRINT,
            overrides={"/api/v5/trade/order": {"code": "51001", "msg": "not found", "data": []}},
        )
        observed = normalize_verified_recovery_observation(
            bundle, expected_intent=self.expected(), account_fingerprint=ACCOUNT_FINGERPRINT,
        )
        self.assertIs(observed.outcome, LiveRecoveryOutcome.PROVIDER_REJECTED)
        self.assertEqual(bundle.envelope("order_details").disposition, QueryDisposition.EXPLICIT_PROVIDER_REJECTION)

    def test_complete_empty_recovery_is_confirmed_absent(self):
        bundle = exact_okx_bundle(
            uuid4(), "recovery", client_order_id="client1", expected_account_fingerprint=ACCOUNT_FINGERPRINT,
        )
        observed = normalize_verified_recovery_observation(
            bundle, expected_intent=self.expected(), account_fingerprint=ACCOUNT_FINGERPRINT,
        )
        self.assertTrue(bundle.complete)
        self.assertIs(observed.outcome, LiveRecoveryOutcome.CONFIRMED_ABSENT)

    def test_response_payloads_are_defensively_frozen(self):
        response = {"code": "0", "data": [{
            "uid": OKX_UID, "mainUid": OKX_UID, "perm": "read_only", "acctLv": "1", "posMode": "long_short_mode",
            "autoLoan": "false", "enableSpotBorrow": "false",
        }]}
        bundle = exact_okx_bundle(
            uuid4(), "preflight", overrides={"/api/v5/account/config": response},
        )
        envelope = bundle.envelope("account_config")
        response["data"][0]["acctLv"] = "9"
        self.assertEqual(envelope.raw_response["data"][0]["acctLv"], "1")
        with self.assertRaises(TypeError):
            envelope.raw_response["data"][0]["acctLv"] = "9"
        operational = passed_authority()[7]
        market_source = next(
            source for source in operational.sources if source.source_kind == "market_data"
        )
        nested_hashes = market_source.payload["raw_observation_hashes"]
        with self.assertRaises(TypeError):
            nested_hashes[next(iter(nested_hashes))] = H

    def test_reconciliation_rejects_future_stale_and_untrusted_evidence(self):
        run = uuid4()
        snap = account(run)
        local = LiveLocalProjection(
            run, snap.account_fingerprint, (), (), dict(snap.balances),
            dict(snap.positions), 1, T0, (snap.snapshot_id,),
        )
        with self.assertRaises(ValueError):
            reconcile_live(
                local_projection=local,
                okx_bundle=exact_okx_bundle(run, "reconciliation", at=T0 + timedelta(seconds=1)),
                evaluated_at_utc=T0, freshness_seconds=30, maximum_clock_skew_seconds=5,
            )
        with self.assertRaises(ValueError):
            reconcile_live(
                local_projection=local,
                okx_bundle=exact_okx_bundle(run, "reconciliation", at=T0),
                evaluated_at_utc=T0 + timedelta(seconds=31),
                freshness_seconds=30, maximum_clock_skew_seconds=5,
            )
        with self.assertRaises(TypeError):
            reconcile_live(
                local_projection=local, venue_observation={"status": "reconciled"},
                evaluated_at_utc=T0, freshness_seconds=30, maximum_clock_skew_seconds=5,
            )

    def test_dry_run_cli_exposes_no_authoritative_tick_or_lot_flags(self):
        script = Path(__file__).resolve().parents[1] / "scripts" / "run_live_dry_run.py"
        completed = subprocess.run([sys.executable, str(script), "--help"], capture_output=True, text=True)
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertNotIn("--tick-size", completed.stdout)
        self.assertNotIn("--lot-size", completed.stdout)



if __name__ == "__main__": unittest.main()
