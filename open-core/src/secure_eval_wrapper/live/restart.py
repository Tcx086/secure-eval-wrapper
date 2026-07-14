"""Fail-closed typed reconstruction of an operational Phase 8A dry-run runtime."""
from __future__ import annotations

import json
from dataclasses import dataclass
from decimal import Decimal
from types import MappingProxyType
from typing import Mapping
from uuid import UUID

from .authorities import LiveReservationAuthority, LiveRuntimeRiskState
from .broker import GuardedLiveBroker
from .configuration import GuardedLiveConfiguration
from .models import (
    LiveAccountSnapshot,
    LiveApproval,
    LiveCredentialReference,
    LiveKillState,
    LiveKillSwitch,
    LivePreflightCheck,
    LivePreflightReport,
    LivePreflightStatus,
    LiveRunManifest,
)
from .venues.fake_live import FakeLiveVenue


def _json(value):
    return value if isinstance(value, (dict, list, tuple)) else json.loads(value)


@dataclass(frozen=True)
class ReconstructedLiveRun:
    live_run_id: UUID
    manifest_id: UUID
    state: str
    dry_run: bool
    production_write_enabled: bool
    started_at_utc: object
    completed_at_utc: object
    version: int
    record_hash: str

    def __post_init__(self) -> None:
        if not self.dry_run or self.production_write_enabled:
            raise PermissionError("reconstructed live run is not write-disabled dry-run authority")


@dataclass(frozen=True)
class ReconstructedReservation:
    authority: LiveReservationAuthority
    state: str
    version: int


@dataclass(frozen=True)
class ReconstructedLiveRecord:
    record_type: str
    record_id: UUID
    live_run_id: UUID
    state: str | None
    record_hash: str
    payload: Mapping[str, object]

    def __post_init__(self) -> None:
        if not self.record_type or len(self.record_hash) != 64:
            raise ValueError("reconstructed persisted record is malformed")
        object.__setattr__(self, "payload", MappingProxyType(dict(self.payload)))


@dataclass(frozen=True)
class ReconstructedLiveRuntime:
    configuration: GuardedLiveConfiguration
    credential_reference: LiveCredentialReference
    account_snapshot: LiveAccountSnapshot
    preflight_report: LivePreflightReport
    approval: LiveApproval
    manifest: LiveRunManifest
    live_run: ReconstructedLiveRun
    kill_switch: LiveKillSwitch
    risk_state: LiveRuntimeRiskState
    reservations: tuple[ReconstructedReservation, ...]
    dispatch_outboxes: tuple[ReconstructedLiveRecord, ...]
    cancel_outboxes: tuple[ReconstructedLiveRecord, ...]
    recovery_claims: tuple[ReconstructedLiveRecord, ...]
    reconciliations: tuple[ReconstructedLiveRecord, ...]
    summaries: tuple[ReconstructedLiveRecord, ...]
    broker: GuardedLiveBroker
    venue: FakeLiveVenue


def _typed_record(kind: str, identity_column: str, row, live_run_id: UUID, *, state_column: str | None = "state") -> ReconstructedLiveRecord:
    if row["live_run_id"] != live_run_id:
        raise PermissionError(f"reconstructed {kind} belongs to another run")
    return ReconstructedLiveRecord(
        kind,
        row[identity_column],
        row["live_run_id"],
        None if state_column is None else str(row[state_column]),
        str(row["record_sha256"]),
        dict(row),
    )


def reconstruct_live_runtime(*, repository, live_run_id) -> ReconstructedLiveRuntime:
    live_run_id = UUID(str(live_run_id))
    state = repository.reconstruct(live_run_id)
    config_payload = dict(_json(state["configuration"]["configuration_jsonb"]))
    decimal_fields = (
        "maximum_order_notional", "maximum_position_notional", "maximum_gross_exposure", "maximum_net_exposure",
        "maximum_daily_submitted_notional", "maximum_daily_realized_loss", "maximum_drawdown", "maximum_fee_bps",
        "maximum_adverse_slippage_bps", "maximum_reference_price_deviation_bps",
    )
    tuple_fields = ("allowed_instruments", "allowed_instrument_types", "allowed_settlement_assets", "allowed_order_types", "credential_source_policy")
    for name in decimal_fields:
        config_payload[name] = Decimal(str(config_payload[name]))
    for name in tuple_fields:
        config_payload[name] = tuple(config_payload[name])
    config_payload.setdefault("maximum_transport_failures", 3)
    configuration = GuardedLiveConfiguration(**config_payload)

    credential_row = state["credential_reference"]
    credential = LiveCredentialReference(
        credential_row["provider"], credential_row["alias"], credential_row["source_type"], credential_row["account_fingerprint"],
        bool(credential_row["loaded"]), credential_row["verified_at_utc"], tuple(_json(credential_row["permission_summary_jsonb"])), credential_row["credential_reference_id"],
    )
    account_row = state["account_snapshot"]
    account_payload = _json(account_row["snapshot_jsonb"])
    account = LiveAccountSnapshot(
        account_row["live_run_id"], account_row["account_fingerprint"], account_row["fetched_at_utc"], account_row["venue_time_at_utc"],
        account_payload["balances"], account_payload["positions"], int(account_row["open_order_count"]), Decimal(str(account_row["total_equity"])),
        Decimal(str(account_row["available_equity"])), Decimal(str(account_row["reserved_equity"])), account_row["account_mode"], account_row["account_snapshot_id"],
    )
    checks = []
    for row in state["preflight_checks"]:
        links = repository._fetchall("SELECT source_id,source_sha256 FROM execution.live_preflight_check_sources WHERE preflight_check_id=%s ORDER BY source_ordinal", (row["preflight_check_id"],))
        checks.append(LivePreflightCheck(
            row["check_name"], bool(row["passed"]), bool(row["required"]), row["evaluated_at_utc"], row["explanation"], row["evidence_sha256"],
            row["source_timestamp_utc"], row["preflight_check_id"], tuple(link["source_id"] for link in links), tuple(link["source_sha256"] for link in links),
        ))
    report_row = state["preflight_report"]
    report = LivePreflightReport(
        report_row["live_run_id"], report_row["configuration_sha256"], report_row["implementation_sha256"], report_row["repository_commit_sha"],
        report_row["endpoint_catalog_sha256"], report_row["credential_reference_sha256"], report_row["account_snapshot_sha256"], report_row["evaluated_at_utc"],
        tuple(checks), tuple(_json(report_row["blockers_jsonb"])), tuple(_json(report_row["warnings_jsonb"])), LivePreflightStatus(report_row["status"]), report_row["preflight_report_id"],
    )
    approval_row = state["approval"]
    approval_payload = _json(approval_row["approval_jsonb"])
    approval = LiveApproval(
        approval_row["live_run_id"], approval_row["configuration_sha256"], approval_row["account_fingerprint"], approval_row["provider"], approval_row["environment"],
        tuple(approval_payload["allowed_instruments"]), Decimal(str(approval_row["maximum_total_approved_notional"])), approval_row["created_at_utc"], approval_row["expires_at_utc"],
        approval_row["manifest_sha256"], approval_payload["repository_commit_sha"], approval_row["nonce"], approval_row["approving_actor"], approval_row["confirmation_challenge_sha256"], approval_row["preflight_report_id"], approval_row["approval_id"],
    )
    manifest_row = state["manifest"]
    manifest_payload = _json(manifest_row["manifest_jsonb"])
    manifest = LiveRunManifest(
        manifest_row["live_run_id"], configuration.provider, configuration.environment, configuration.account_fingerprint, manifest_row["configuration_sha256"], manifest_row["implementation_sha256"],
        manifest_row["repository_commit_sha"], manifest_row["endpoint_catalog_sha256"], manifest_row["credential_reference_sha256"], manifest_row["preflight_report_id"], manifest_row["approval_id"],
        manifest_row["initial_account_snapshot_id"], account.record_hash, tuple(manifest_payload["allowed_instruments"]), manifest_payload["risk_limits"], bool(manifest_row["dry_run"]), bool(manifest_row["production_write_enabled"]),
        configuration.maximum_run_duration_seconds, manifest_payload["kill_switch_policy"], tuple(UUID(str(value)) for value in manifest_payload["parent_evidence_ids"]), manifest_row["manifest_sha256"], manifest_row["manifest_id"],
    )
    run_row = state["run"]
    run = ReconstructedLiveRun(
        run_row["live_run_id"], run_row["manifest_id"], run_row["state"], bool(run_row["dry_run"]), bool(run_row["production_write_enabled"]),
        run_row["started_at_utc"], run_row["completed_at_utc"], int(run_row["version"]), run_row["record_sha256"],
    )
    kill_row = state["kill_switch"]
    kill = LiveKillSwitch(kill_row["live_run_id"], LiveKillState(kill_row["state"]), kill_row["reason"], kill_row["updated_at_utc"], kill_row["evidence_sha256"], bool(kill_row["requires_fresh_preflight"]), bool(kill_row["requires_new_approval"]), kill_row["kill_switch_id"])
    risk_state = repository.load_risk_state(live_run_id)

    reservations = []
    for row in state["reservations"]:
        if row["live_run_id"] != live_run_id:
            raise PermissionError("reconstructed reservation belongs to another run")
        authority = LiveReservationAuthority(
            row["live_run_id"], row["order_intent_id"], row["currency"], Decimal(str(row["original_amount"])), Decimal(str(row["remaining_amount"])),
            Decimal(str(row["original_quantity"])), Decimal(str(row["remaining_quantity"])), Decimal(str(row["worst_case_price"])), Decimal(str(row["maximum_fee_bps"])),
            Decimal(str(row["maximum_fee_amount"])), row["fee_currency_policy"], Decimal(str(row["risk_notional"])), Decimal(str(row["reservation_notional"])),
            row["calculator_version"], _json(row["source_hashes_jsonb"]), row["reservation_id"],
        )
        if authority.record_hash != row["record_sha256"]:
            raise PermissionError("reconstructed reservation hash mismatch")
        reservations.append(ReconstructedReservation(authority, row["state"], int(row["version"])))

    dispatch = tuple(_typed_record("dispatch_outbox", "dispatch_outbox_id", row, live_run_id) for row in state["dispatch_outboxes"])
    cancel = tuple(_typed_record("cancel_outbox", "cancel_outbox_id", row, live_run_id) for row in state["cancel_outboxes"])
    recovery = tuple(_typed_record("recovery_claim", "recovery_record_id", row, live_run_id) for row in state["recovery_claims"])
    reconciliations = tuple(_typed_record("reconciliation", "reconciliation_id", row, live_run_id, state_column="status") for row in state["reconciliations"])
    summary_rows = tuple(row for row in (state["pre_run_summary"], state["post_run_summary"]) if row is not None)
    summaries = tuple(_typed_record("summary", "summary_id", row, live_run_id, state_column=None) for row in summary_rows)

    venue = FakeLiveVenue()
    broker = GuardedLiveBroker(configuration=configuration, manifest=manifest, approval=approval, preflight_report=report, repository=repository, venue=venue, worker_identity="phase8a-restarted")
    return ReconstructedLiveRuntime(
        configuration, credential, account, report, approval, manifest, run, kill, risk_state,
        tuple(reservations), dispatch, cancel, recovery, reconciliations, summaries, broker, venue,
    )


__all__ = [
    "ReconstructedLiveRun", "ReconstructedReservation", "ReconstructedLiveRecord",
    "ReconstructedLiveRuntime", "reconstruct_live_runtime",
]
