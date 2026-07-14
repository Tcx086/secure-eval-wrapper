"""Guarded-live broker that only prepares and suppresses Phase 8A writes."""
from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime

from secure_eval_wrapper.data_collection.hashing import sha256_payload

from .gates import evaluate_live_write_authority, require_fake_transport_in_ci
from .models import LiveOrderIntent, LiveOrderState, LiveTransportPlan
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
        self.configuration = configuration
        self.manifest = manifest
        self.approval = approval
        self.preflight_report = preflight_report
        self.repository = repository
        self.venue = venue
        self.worker_identity = worker_identity
        require_fake_transport_in_ci(venue)
        if getattr(repository, "authoritative_storage", None) != "PostgreSQL":
            raise TypeError("guarded live broker requires PostgreSQL authority")
        if manifest.production_write_enabled or not manifest.dry_run or configuration.production_write_enabled or not configuration.dry_run:
            raise PermissionError("Phase 8A broker requires persisted dry-run/write-disabled authority")
        if manifest.configuration_hash != configuration.configuration_hash or manifest.approval_id != approval.approval_id or manifest.preflight_report_id != preflight_report.report_id:
            raise PermissionError("broker authorities are not bound to one manifest chain")

    def prepare_and_suppress(
        self,
        *,
        intent: LiveOrderIntent,
        market_evidence,
        at_utc: datetime,
        risk_state=None,
        cli_enable_live_execution: bool = False,
        exact_confirmation_challenge_hash: str | None = None,
        tick_size=None,
        lot_size=None,
    ) -> DryRunResult:
        """PostgreSQL derives metadata normalization and the suppressed provider plan."""
        if tick_size is not None or lot_size is not None:
            raise ValueError("caller tick/lot values are not operational authority")
        persisted = self.repository.persisted_preflight(self.preflight_report.report_id)
        if (
            persisted is None
            or str(persisted["record_sha256"]) != self.preflight_report.record_hash
            or str(persisted["status"]) != "passed"
            or str(persisted["purpose"]) not in ("run_start", "run_continue")
            or str(persisted["authority_generation"]) != "collector_0024"
        ):
            raise PermissionError("caller-provided preflight cannot replace the persisted normal report")
        prepared = self.repository.prepare_operational_dry_run(
            intent=intent,
            configuration=self.configuration,
            approval=self.approval,
            market_evidence=market_evidence,
            created_at_utc=at_utc,
            caller_risk_state=risk_state,
        )
        normalized = prepared["intent"]
        risk = prepared["risk_decision"]
        accepted = risk.accepted if hasattr(risk, "accepted") else bool(risk["accepted"])
        if not accepted:
            return DryRunResult(normalized, risk, None, LiveOrderState.DRY_RUN_BLOCKED, False, False)
        plan = LiveTransportPlan(
            normalized.live_run_id,
            normalized.order_intent_id,
            "submit_limit_order",
            "POST",
            "/api/v5/trade/order",
            prepared["request_body"],
            prepared["provider_request_hash"],
            at_utc,
            True,
        )
        outbox_id = prepared["outbox_id"]
        if self.repository.dispatch_state(outbox_id) == "dry_run_suppressed":
            return DryRunResult(normalized, risk, plan, LiveOrderState.DRY_RUN_SUPPRESSED, False, True)
        claimed = self.repository.claim_dispatch(
            worker_identity=self.worker_identity, at_utc=at_utc, outbox_id=outbox_id,
        )
        if claimed is None or claimed[0] != outbox_id:
            raise RuntimeError("new dry-run outbox could not be claimed")
        authority = evaluate_live_write_authority(
            configuration=self.configuration,
            cli_enable_live_execution=cli_enable_live_execution,
            approval=self.approval,
            exact_confirmation_challenge_hash=exact_confirmation_challenge_hash,
            at_utc=at_utc,
        )
        if authority.allowed:
            raise AssertionError("Phase 8A live write authority can never be allowed")
        self.repository.suppress_claimed_dispatch(
            outbox_id=outbox_id, claim_token=claimed[1],
            worker_identity=self.worker_identity, at_utc=at_utc,
        )
        return DryRunResult(normalized, risk, plan, LiveOrderState.DRY_RUN_SUPPRESSED, False, True)
    def submit_order(self, *args, **kwargs):
        raise PermissionError("Phase 8A exposes no production submit operation")

    def cancel_order(self, *args, **kwargs):
        raise PermissionError("Phase 8A exposes no production cancel operation")


__all__ = ["DryRunResult", "GuardedLiveBroker"]
