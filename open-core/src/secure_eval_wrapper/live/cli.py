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


def _migration_hashes_0001_0022() -> dict[str, str]:
    result = {}
    for path in sorted(MIGRATIONS.glob("[0-9][0-9][0-9][0-9]_*.sql")):
        if path.name[:4] > "0022": break
        result[path.stem] = hashlib.sha256(path.read_bytes().replace(b"\r\n", b"\n")).hexdigest()
    return result


def _persist_preflight_report(repository, *, configuration, credential, account, evidence, report, created_at_utc):
    with repository.transaction():
        repository._lock_run(report.live_run_id)
        for source in evidence.sources:
            repository._strict_insert(
                "execution.live_preflight_sources", "source_id", source.source_id,
                ("live_run_id", "source_kind", "collected_at_utc", "source_payload_jsonb", "source_sha256", "operational"),
                (source.live_run_id, source.source_kind, source.collected_at_utc, _public_payload(dict(source.payload)), source.source_hash, True), source.record_hash,
            )
        repository._strict_insert(
            "execution.live_preflight_reports", "preflight_report_id", report.report_id,
            ("live_run_id", "configuration_sha256", "implementation_sha256", "repository_commit_sha", "endpoint_catalog_sha256", "credential_reference_sha256", "account_snapshot_sha256", "evaluated_at_utc", "status", "blockers_jsonb", "warnings_jsonb", "credential_reference_id", "account_snapshot_id"),
            (report.live_run_id, report.configuration_hash, report.implementation_hash, report.repository_commit_sha, report.endpoint_catalog_hash, report.credential_reference_hash, report.account_snapshot_hash, report.evaluated_at_utc, report.status.value, _public_payload(report.blockers), _public_payload(report.warnings), credential.reference_id, account.snapshot_id), report.record_hash,
        )
        for check_ordinal, check in enumerate(report.checks):
            repository._strict_insert(
                "execution.live_preflight_checks", "preflight_check_id", check.check_id,
                ("preflight_report_id", "live_run_id", "check_ordinal", "check_name", "passed", "required", "evaluated_at_utc", "source_timestamp_utc", "explanation", "evidence_sha256"),
                (report.report_id, report.live_run_id, check_ordinal, check.check_name, check.passed, check.required, check.evaluated_at_utc, check.source_timestamp_utc, check.explanation, check.evidence_hash), check.record_hash,
            )
            for source_ordinal, (source_id, source_hash) in enumerate(zip(check.source_ids, check.source_hashes)):
                repository._execute("INSERT INTO execution.live_preflight_check_sources (preflight_check_id,source_ordinal,source_id,live_run_id,source_sha256) VALUES (%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING", (check.check_id, source_ordinal, source_id, report.live_run_id, source_hash))


def preflight_main(argv=None):
    parser = _parser("Secure Eval live preflight (offline PostgreSQL authority by default)")
    parser.add_argument("--read-only-network-preflight", action="store_true")
    args = parser.parse_args(argv)
    network_reads = False
    try:
        run_id = _uuid(args.live_run_id)
        with _connect() as connection:
            repository = DurablePostgresLiveRepository(connection)
            runtime = reconstruct_live_runtime(repository=repository, live_run_id=run_id)
            if args.configuration_hash and args.configuration_hash != runtime.configuration.configuration_hash:
                raise PermissionError("requested configuration hash does not match PostgreSQL")
            if args.read_only_network_preflight:
                raise PermissionError("authenticated reads require an explicit local credential-provider integration; no credential is loaded implicitly")
            now = datetime.now(timezone.utc)
            rows = repository._fetchall("SELECT * FROM execution.live_preflight_sources WHERE live_run_id=%s ORDER BY collected_at_utc DESC", (run_id,))
            latest = {}
            for row in rows:
                latest.setdefault(row["source_kind"], LiveEvidenceSource(run_id, row["source_kind"], row["collected_at_utc"], row["source_payload_jsonb"], bool(row["operational"]), row["source_id"], row["source_sha256"]))
            postgres, rollback = collect_postgresql_probe_sources(connection=connection, live_run_id=run_id, collected_at_utc=now, fake_transport=True)
            migration = collect_migration_catalog_source(connection=connection, live_run_id=run_id, collected_at_utc=now, expected_hashes=_migration_hashes_0001_0022())
            repository_source = RepositoryCommitEvidence.source(live_run_id=run_id, collected_at_utc=now, commit_sha=runtime.manifest.repository_commit_sha, expected_commit_sha=runtime.manifest.repository_commit_sha, implementation_hash=runtime.configuration.provider_implementation_hash)
            latest.update(repository=repository_source, migration_catalog=migration, postgresql_probe=postgres, audit_rollback_probe=rollback)
            evidence = OperationalPreflightEvidence(run_id, tuple(latest[kind] for kind in sorted(latest)))
            report = LivePreflightEngine().evaluate(live_run_id=run_id, configuration=runtime.configuration, account_snapshot=runtime.account_snapshot, credential_reference=runtime.credential_reference, evidence=evidence, evaluated_at_utc=now, implementation_hash=runtime.configuration.provider_implementation_hash, repository_commit_sha=runtime.manifest.repository_commit_sha)
            _persist_preflight_report(repository, configuration=runtime.configuration, credential=runtime.credential_reference, account=runtime.account_snapshot, evidence=evidence, report=report, created_at_utc=now)
            _print({"command": "live-preflight", "live_run_id": run_id, "configuration_hash": runtime.configuration.configuration_hash, "preflight_report_id": report.report_id, "status": report.status.value, "blockers": report.blockers, "network_reads_occurred": network_reads, "network_writes_occurred": False, "production_write_status": "disabled"})
            return 0 if not report.blockers else 1
    except Exception as exc:
        _print({"command": "live-preflight", "status": "blocked", "blockers": [str(exc)], "network_reads_occurred": network_reads, "network_writes_occurred": False, "production_write_status": "disabled"})
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
    parser.add_argument("--tick-size", type=Decimal, required=True)
    parser.add_argument("--lot-size", type=Decimal, required=True)
    args = parser.parse_args(argv)
    try:
        run_id = _uuid(args.live_run_id)
        with _connect() as connection:
            repository = DurablePostgresLiveRepository(connection)
            runtime = reconstruct_live_runtime(repository=repository, live_run_id=run_id)
            if args.configuration_hash and args.configuration_hash != runtime.configuration.configuration_hash: raise PermissionError("configuration mismatch")
            risk_row = repository._fetchone("SELECT * FROM execution.live_run_risk_state WHERE live_run_id=%s", (run_id,))
            market = _market_evidence(repository, risk_row, runtime.configuration)
            reconciliation = repository._fetchone("SELECT * FROM execution.live_reconciliations WHERE reconciliation_id=%s AND live_run_id=%s", (risk_row["latest_reconciliation_id"], run_id))
            metadata = repository._fetchone("SELECT source_sha256 FROM execution.live_preflight_sources WHERE live_run_id=%s AND source_kind='instrument_metadata' ORDER BY collected_at_utc DESC LIMIT 1", (run_id,))
            now = datetime.now(timezone.utc)
            intent = LiveOrderIntent(run_id, runtime.manifest.manifest_id, market.series_identity, OrderSide(args.side), args.quantity, args.reference_price or args.limit_price, args.limit_price, now, market.evidence_id, market.evidence_sha256, metadata["source_sha256"], risk_row["record_sha256"] and runtime.account_snapshot.record_hash, reconciliation["record_sha256"])
            result = runtime.broker.prepare_and_suppress(intent=intent, market_evidence=market, tick_size=args.tick_size, lot_size=args.lot_size, at_utc=now)
            _print({"command": "live-dry-run", "live_run_id": run_id, "order_intent_id": result.intent.order_intent_id, "state": result.state.value, "risk_accepted": bool(result.risk_decision.accepted), "risk_reasons": result.risk_decision.reasons, "network_reads_occurred": False, "network_writes_occurred": False, "external_write_suppressed": result.external_write_suppressed, "production_write_status": "disabled"})
            return 0 if result.risk_decision.accepted else 1
    except Exception as exc:
        _print({"command": "live-dry-run", "status": "blocked", "blockers": [str(exc)], "network_reads_occurred": False, "network_writes_occurred": False, "production_write_status": "disabled"})
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
    parser = _parser("Secure Eval exact live reconciliation")
    parser.add_argument("--venue-observation-json", required=True)
    args = parser.parse_args(argv)
    try:
        run_id = _uuid(args.live_run_id); payload = json.loads(Path(args.venue_observation_json).read_text(encoding="utf-8")); now = datetime.now(timezone.utc)
        observed_at = datetime.fromisoformat(str(payload["observed_at_utc"]).replace("Z", "+00:00"))
        venue = LiveVenueObservation(run_id, payload["account_fingerprint"], tuple(payload["orders"]), tuple(payload["fills"]), payload["balances"], payload["positions"], int(payload["sequence"]), observed_at, tuple(payload["response_hashes"]))
        with _connect() as connection:
            repository = DurablePostgresLiveRepository(connection); reconciliation, _ = build_and_reconcile(repository=repository, live_run_id=run_id, venue_observation=venue, evaluated_at_utc=now)
            _print({"command": "live-reconcile", "live_run_id": run_id, "reconciliation_id": reconciliation.reconciliation_id, "status": reconciliation.status.value, "differences": reconciliation.differences, "network_reads_occurred": False, "network_writes_occurred": False, "production_write_status": "disabled"}); return 0 if reconciliation.status.value == "reconciled" else 1
    except Exception as exc:
        _print({"command": "live-reconcile", "status": "blocked", "blockers": [str(exc)], "network_reads_occurred": False, "network_writes_occurred": False, "production_write_status": "disabled"}); return 2


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
