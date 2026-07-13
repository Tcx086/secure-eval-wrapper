"""Guarded-live broker that can only prepare and suppress Phase 8A writes."""
from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime

from secure_eval_wrapper.data_collection.hashing import sha256_payload

from .gates import evaluate_live_write_authority, require_fake_transport_in_ci
from .models import LiveOrderIntent, LiveOrderState, LiveTransportPlan
from .risk import evaluate_live_risk
from .venues.okx_live import OkxProductionSpotAdapter


@dataclass(frozen=True)
class DryRunResult:
    intent: LiveOrderIntent
    risk_decision: object
    transport_plan: LiveTransportPlan | None
    state: LiveOrderState
    external_write_attempted: bool
    external_write_suppressed: bool


class GuardedLiveBroker:
    def __init__(self, *, configuration, manifest, approval, preflight_report, repository, venue, worker_identity: str = "phase8a-dry-run") -> None:
        self.configuration = configuration; self.manifest = manifest; self.approval = approval; self.preflight_report = preflight_report; self.repository = repository; self.venue = venue; self.worker_identity = worker_identity
        require_fake_transport_in_ci(venue)
        if getattr(repository, "authoritative_storage", None) != "PostgreSQL":
            raise TypeError("guarded live broker requires PostgreSQL authority")
        if manifest.production_write_enabled or not manifest.dry_run or configuration.production_write_enabled or not configuration.dry_run:
            raise PermissionError("Phase 8A broker requires persisted dry-run/write-disabled authority")

    def prepare_and_suppress(self, *, intent: LiveOrderIntent, market_evidence, risk_state, tick_size, lot_size, at_utc: datetime, cli_enable_live_execution: bool = False, exact_confirmation_challenge_hash: str | None = None) -> DryRunResult:
        persisted = self.repository.persisted_preflight(self.preflight_report.report_id)
        if persisted is None or str(persisted["record_sha256"]) != self.preflight_report.record_hash or str(persisted["status"]) != "passed":
            raise PermissionError("caller-provided preflight cannot replace the persisted passed report")
        quantity = OkxProductionSpotAdapter.normalize_decimal(intent.quantity, lot_size)
        price = OkxProductionSpotAdapter.normalize_decimal(intent.limit_price, tick_size)
        normalized = replace(intent, quantity=quantity, limit_price=price, order_intent_id=None, client_order_id=None)
        risk = evaluate_live_risk(intent=normalized, market_evidence=market_evidence, configuration=self.configuration, state=risk_state, approval=self.approval, evaluated_at_utc=at_utc)
        authority = evaluate_live_write_authority(configuration=self.configuration, cli_enable_live_execution=cli_enable_live_execution, approval=self.approval, exact_confirmation_challenge_hash=exact_confirmation_challenge_hash, at_utc=at_utc)
        if not risk.accepted:
            self.repository.prepare_dry_run_bundle(intent=normalized, risk_decision=risk, request_body={}, provider_request_hash=sha256_payload({"blocked": risk.record_hash}), created_at_utc=at_utc, reservation_currency=normalized.series_identity.settlement_asset)
            return DryRunResult(normalized, risk, None, LiveOrderState.DRY_RUN_BLOCKED, False, False)
        body = OkxProductionSpotAdapter.build_limit_order_body(instrument=normalized.series_identity.provider_instrument_id, side=normalized.side.value, quantity=normalized.quantity, limit_price=normalized.limit_price, client_order_id=normalized.client_order_id, tick_size=tick_size, lot_size=lot_size)
        request_hash = sha256_payload({"method": "POST", "path": "/api/v5/trade/order", "body": body})
        plan = LiveTransportPlan(normalized.live_run_id, normalized.order_intent_id, "submit_limit_order", "POST", "/api/v5/trade/order", body, request_hash, at_utc, True)
        outbox_id = self.repository.prepare_dry_run_bundle(intent=normalized, risk_decision=risk, request_body=body, provider_request_hash=request_hash, created_at_utc=at_utc, reservation_currency=normalized.series_identity.settlement_asset)
        claimed = self.repository.claim_dispatch(worker_identity=self.worker_identity, at_utc=at_utc, outbox_id=outbox_id)
        if self.repository.dispatch_state(outbox_id) == "dry_run_suppressed":
            return DryRunResult(normalized, risk, plan, LiveOrderState.DRY_RUN_SUPPRESSED, False, True)
        if claimed is None or claimed[0] != outbox_id:
            raise RuntimeError("new dry-run outbox could not be claimed")
        self.repository.suppress_claimed_dispatch(outbox_id=outbox_id, claim_token=claimed[1], worker_identity=self.worker_identity, at_utc=at_utc)
        if authority.allowed:
            raise AssertionError("Phase 8A live write authority can never be allowed")
        return DryRunResult(normalized, risk, plan, LiveOrderState.DRY_RUN_SUPPRESSED, False, True)

    def submit_order(self, *args, **kwargs):
        raise PermissionError("Phase 8A exposes no production submit operation")

    def cancel_order(self, *args, **kwargs):
        raise PermissionError("Phase 8A exposes no production cancel operation")


__all__ = ["DryRunResult", "GuardedLiveBroker"]
