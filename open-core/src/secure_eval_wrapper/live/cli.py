"""Operational PostgreSQL-backed Phase 8A CLIs; every command remains write-transport-free."""
from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from uuid import UUID

from secure_eval_wrapper.alpha.identity import SeriesIdentity
from secure_eval_wrapper.data_collection.models import InstrumentType
from secure_eval_wrapper.execution.models import OrderSide
from secure_eval_wrapper.live.authorities import LiveEvidenceSource, LiveVenueObservation, OperationalPreflightEvidence, RepositoryCommitEvidence
from secure_eval_wrapper.paper.models import PaperMarketDataEvidence
from secure_eval_wrapper.storage.postgres.config import load_postgres_config

from .durable_repository import DurablePostgresLiveRepository, _public_payload
from .models import LiveOrderIntent
from .preflight import LivePreflightEngine, collect_migration_catalog_source, collect_postgresql_probe_sources
from .reconciliation import build_and_reconcile
from .restart import reconstruct_live_runtime


ROOT = Path(__file__).resolve().parents[4]
MIGRATIONS = ROOT / "open-core" / "db" / "migrations"


def _print(payload) -> None:
    print(json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str))


def _connect():
    import psycopg
    from psycopg.rows import dict_row
    return psycopg.connect(**load_postgres_config().to_connection_kwargs(), row_factory=dict_row, autocommit=True)


def _parser(description: str, *, run_required: bool = True) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=description)
    parser.add_argument("--live-run-id", required=run_required)
    parser.add_argument("--configuration-hash")
    return parser


def _uuid(value: str) -> UUID:
    try:
        return UUID(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("--live-run-id must be a UUID") from exc


def preflight_main(argv=None):
    parser = _parser("Secure Eval live preflight collector gate")
    parser.add_argument("--read-only-network-preflight", action="store_true")
    args = parser.parse_args(argv)
    try:
        run_id = _uuid(args.live_run_id)
        with _connect() as connection:
            repository = DurablePostgresLiveRepository(connection)
            runtime = reconstruct_live_runtime(repository=repository, live_run_id=run_id)
            if args.configuration_hash and args.configuration_hash != runtime.configuration.configuration_hash:
                raise PermissionError("requested configuration hash does not match PostgreSQL")
            if not args.read_only_network_preflight:
                raise PermissionError(
                    "fresh account, position, open-order, venue-time, and instrument responses "
                    "must be recollected by the approved read-only adapter; historical source rows "
                    "are never reused"
                )
            raise PermissionError(
                "no credential material is loaded implicitly; provide an explicit local-only "
                "credential integration to the approved read-only collector"
            )
    except Exception as exc:
        _print({
            "command": "live-preflight", "status": "blocked", "blockers": [str(exc)],
            "evidence_classification": "no_stale_reuse",
            "network_reads_occurred": False, "network_writes_occurred": False,
            "production_write_status": "disabled",
        })
        return 2
def _market_evidence(repository, risk_row, configuration):
    source = repository._fetchone("SELECT source_payload_jsonb FROM execution.live_preflight_sources WHERE source_id=%s", (risk_row["latest_market_evidence_id"],))
    payload = source["source_payload_jsonb"]
    instrument = payload["provider_instrument_id"]
    identity = SeriesIdentity(payload.get("provider", "okx"), payload.get("exchange", "okx"), instrument, payload.get("canonical_symbol", instrument), InstrumentType.SPOT, payload.get("timeframe", "1m"), payload.get("quote_currency", instrument.split("-")[-1]))
    evidence = PaperMarketDataEvidence(
        identity, payload.get("provider", "okx"), instrument, payload.get("event_type", "bar_close"), str(payload["source_row_id"]),
        datetime.fromisoformat(str(payload["observed_at_utc"]).replace("Z", "+00:00")), datetime.fromisoformat(str(payload.get("available_at_utc", payload["observed_at_utc"])).replace("Z", "+00:00")),
        True, "accepted", payload["source_sha256"], payload["normalized_record_sha256"],
        exchange=payload.get("exchange", "okx"), provider_instrument_id=instrument, instrument_type="spot", source_table=payload.get("source_table", "market_data.validated_bars"),
        source_row_id=str(payload["source_row_id"]), validation_report_id=UUID(str(payload["validation_report_id"])), price=Decimal(str(payload["price"])), price_type=payload.get("price_type", "close"),
        quote_currency=payload.get("quote_currency", instrument.split("-")[-1]), normalized_record_sha256=payload["normalized_record_sha256"], source_kind="postgresql",
    )
    if evidence.evidence_sha256 != risk_row["latest_market_evidence_sha256"]:
        raise PermissionError("persisted market evidence cannot be reconstructed exactly")
    return evidence


def dry_run_main(argv=None):
    parser = _parser("Secure Eval operational live dry-run")
    parser.add_argument("--side", choices=("buy", "sell"), required=True)
    parser.add_argument("--quantity", type=Decimal, required=True)
    parser.add_argument("--limit-price", type=Decimal, required=True)
    parser.add_argument("--reference-price", type=Decimal)
    args = parser.parse_args(argv)
    try:
        run_id = _uuid(args.live_run_id)
        with _connect() as connection:
            repository = DurablePostgresLiveRepository(connection)
            runtime = reconstruct_live_runtime(repository=repository, live_run_id=run_id)
            if args.configuration_hash and args.configuration_hash != runtime.configuration.configuration_hash:
                raise PermissionError("configuration mismatch")
            risk_row = repository._fetchone(
                "SELECT * FROM execution.live_run_risk_state WHERE live_run_id=%s", (run_id,),
            )
            market = _market_evidence(repository, risk_row, runtime.configuration)
            reconciliation = repository._fetchone(
                "SELECT * FROM execution.live_reconciliations "
                "WHERE reconciliation_id=%s AND live_run_id=%s",
                (risk_row["latest_reconciliation_id"], run_id),
            )
            metadata = repository._fetchone(
                "SELECT m.source_id,s.source_sha256 FROM execution.live_instrument_metadata_sources m "
                "JOIN execution.live_preflight_sources s ON s.source_id=m.source_id "
                "AND s.live_run_id=m.live_run_id WHERE m.live_run_id=%s "
                "AND m.instrument_id=%s ORDER BY m.collected_at_utc DESC LIMIT 1",
                (run_id, market.series_identity.provider_instrument_id),
            )
            if metadata is None:
                raise PermissionError("verified PostgreSQL instrument metadata is missing")
            now = datetime.now(timezone.utc)
            intent = LiveOrderIntent(
                run_id, runtime.manifest.manifest_id, market.series_identity, OrderSide(args.side),
                args.quantity, args.reference_price or args.limit_price, args.limit_price, now,
                market.evidence_id, market.evidence_sha256, metadata["source_sha256"],
                runtime.account_snapshot.record_hash, reconciliation["record_sha256"],
                instrument_metadata_source_id=metadata["source_id"],
            )
            result = runtime.broker.prepare_and_suppress(
                intent=intent, market_evidence=market, at_utc=now,
            )
            _print({
                "command": "live-dry-run", "live_run_id": run_id,
                "order_intent_id": result.intent.order_intent_id, "state": result.state.value,
                "risk_accepted": bool(result.risk_decision.accepted),
                "risk_reasons": result.risk_decision.reasons,
                "network_reads_occurred": False, "network_writes_occurred": False,
                "external_write_suppressed": result.external_write_suppressed,
                "production_write_status": "disabled",
            })
            return 0 if result.risk_decision.accepted else 1
    except Exception as exc:
        _print({
            "command": "live-dry-run", "status": "blocked", "blockers": [str(exc)],
            "network_reads_occurred": False, "network_writes_occurred": False,
            "production_write_status": "disabled",
        })
        return 2
def status_main(argv=None):
    parser = _parser("Secure Eval live PostgreSQL status")
    args = parser.parse_args(argv)
    try:
        run_id = _uuid(args.live_run_id)
        with _connect() as connection:
            repository = DurablePostgresLiveRepository(connection); row = repository.status(run_id)
            if row is None: raise LookupError("live run not found")
            if args.configuration_hash and args.configuration_hash != row["configuration_sha256"]: raise PermissionError("configuration mismatch")
            row.update(command="live-status", network_reads_occurred=False, network_writes_occurred=False, production_write_status="disabled")
            _print(row); return 0
    except Exception as exc:
        _print({"command": "live-status", "status": "blocked", "blockers": [str(exc)], "network_reads_occurred": False, "network_writes_occurred": False, "production_write_status": "disabled"}); return 2


def reconcile_main(argv=None):
    parser = _parser("Secure Eval imported reconciliation inspection (never operational)")
    parser.add_argument("--venue-observation-json", required=True)
    args = parser.parse_args(argv)
    try:
        run_id = _uuid(args.live_run_id)
        payload = json.loads(Path(args.venue_observation_json).read_text(encoding="utf-8"))
        with _connect() as connection:
            repository = DurablePostgresLiveRepository(connection)
            local = repository.build_local_projection(
                run_id, observed_at_utc=datetime.now(timezone.utc),
            )
        comparable = ("account_fingerprint", "orders", "fills", "balances", "positions")
        differences = tuple(
            {"field": field, "local": local[field], "imported": payload.get(field)}
            for field in comparable if local[field] != payload.get(field)
        )
        _print({
            "command": "live-reconcile", "live_run_id": run_id,
            "status": "untrusted_fixture", "evidence_classification": "imported",
            "differences": differences, "operational_authority_updated": False,
            "network_reads_occurred": False, "network_writes_occurred": False,
            "production_write_status": "disabled",
        })
        return 1
    except Exception as exc:
        _print({
            "command": "live-reconcile", "status": "blocked",
            "evidence_classification": "imported", "blockers": [str(exc)],
            "operational_authority_updated": False,
            "network_reads_occurred": False, "network_writes_occurred": False,
            "production_write_status": "disabled",
        })
        return 2
def kill_main(argv=None):
    parser = _parser("Secure Eval durable live kill switch")
    parser.add_argument("--reason", default="manual")
    args = parser.parse_args(argv)
    try:
        run_id = _uuid(args.live_run_id); now = datetime.now(timezone.utc)
        with _connect() as connection:
            repository = DurablePostgresLiveRepository(connection); state = repository.trigger_kill(live_run_id=run_id, reason=args.reason, evidence={"source": "secure-eval-live-kill"}, at_utc=now)
            _print({"command": "live-kill", "live_run_id": run_id, "kill_state": state, "network_reads_occurred": False, "network_writes_occurred": False, "production_write_status": "disabled"}); return 0
    except Exception as exc:
        _print({"command": "live-kill", "status": "blocked", "blockers": [str(exc)], "network_reads_occurred": False, "network_writes_occurred": False, "production_write_status": "disabled"}); return 2


def main(argv=None):
    parser = argparse.ArgumentParser(description="Phase 8A guarded live CLI")
    parser.add_argument("command", choices=("preflight", "dry-run", "status", "reconcile", "kill"))
    args, rest = parser.parse_known_args(argv)
    return {"preflight": preflight_main, "dry-run": dry_run_main, "status": status_main, "reconcile": reconcile_main, "kill": kill_main}[args.command](rest)


if __name__ == "__main__": raise SystemExit(main())
