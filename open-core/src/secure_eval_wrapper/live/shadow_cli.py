"""Command-line entry point for isolated Phase 8B shadow assurance."""
from __future__ import annotations

import argparse
import json
import os
from datetime import datetime, timezone
from uuid import UUID

from .shadow_evidence import scenario_metrics
from .shadow_repository import PostgresShadowRepository, validate_shadow_database_name
from .shadow_runtime import (
    FixtureShadowMarketSource,
    OkxPublicShadowMarketSource,
    ShadowAssuranceRuntime,
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="secure-eval-live-shadow")
    subparsers = parser.add_subparsers(dest="command", required=True)

    run = subparsers.add_parser("run", help="run one fixture or explicit public-data shadow")
    run.add_argument("--fixture", default="clean_flat_account")
    run.add_argument("--allow-public-network", action="store_true")
    run.add_argument("--provider", default="okx")
    run.add_argument("--instrument", default="BTC-USDT")
    run.add_argument("--postgres-database")
    run.add_argument("--postgres-host", default=os.environ.get("POSTGRES_HOST"))
    run.add_argument("--postgres-port", type=int, default=int(os.environ.get("POSTGRES_PORT", "5432")))
    run.add_argument("--postgres-user", default=os.environ.get("POSTGRES_USER"))
    run.add_argument("--postgres-password", default=os.environ.get("POSTGRES_PASSWORD"))
    run.add_argument("--postgres-sslmode", default=os.environ.get("POSTGRES_SSLMODE", "disable"))
    run.add_argument("--public-timeout-seconds", type=float, default=3.0)

    inspect = subparsers.add_parser("inspect", help="reload one persisted shadow bundle")
    inspect.add_argument("--run-id", required=True)
    inspect.add_argument("--postgres-database", required=True)
    inspect.add_argument("--postgres-host", default=os.environ.get("POSTGRES_HOST"))
    inspect.add_argument("--postgres-port", type=int, default=int(os.environ.get("POSTGRES_PORT", "5432")))
    inspect.add_argument("--postgres-user", default=os.environ.get("POSTGRES_USER"))
    inspect.add_argument("--postgres-password", default=os.environ.get("POSTGRES_PASSWORD"))
    inspect.add_argument("--postgres-sslmode", default=os.environ.get("POSTGRES_SSLMODE", "disable"))

    matrix = subparsers.add_parser("matrix", help="evaluate all socket-free fixtures")
    matrix.add_argument("--repository-sha", required=True)
    return parser


def _connect(args):
    database = validate_shadow_database_name(args.postgres_database)
    missing = [
        name
        for name, value in (
            ("host", args.postgres_host),
            ("user", args.postgres_user),
            ("password", args.postgres_password),
        )
        if not value
    ]
    if missing:
        raise RuntimeError("shadow PostgreSQL connection fields are incomplete")
    try:
        import psycopg
    except ImportError as exc:
        raise RuntimeError("shadow PostgreSQL persistence requires the postgres extra") from exc
    connection = psycopg.connect(
        host=args.postgres_host,
        port=args.postgres_port,
        dbname=database,
        user=args.postgres_user,
        password=args.postgres_password,
        sslmode=args.postgres_sslmode,
    )
    return database, connection


def _blocked_payload(blocker: str) -> dict[str, object]:
    return {
        "operation": "phase8b_shadow_run",
        "status": "blocked",
        "blockers": [blocker],
        "network_read_count": 0,
        "production_transport_call_count": 0,
        "authenticated_endpoint_call_count": 0,
        "credential_read_count": 0,
        "production_write_count": 0,
        "production_submit_reachable": False,
        "production_cancel_reachable": False,
        "real_account_data_used": False,
        "operator_database_accessed": False,
        "authenticated_proof_executed": False,
    }


def run_main(args) -> int:
    if not args.postgres_database:
        print(json.dumps(_blocked_payload("explicit_shadow_postgresql_target_required"), sort_keys=True))
        return 2
    try:
        database, connection = _connect(args)
    except Exception:
        print(json.dumps(_blocked_payload("shadow_postgresql_unavailable"), sort_keys=True))
        return 2
    try:
        repository = PostgresShadowRepository(connection, expected_database=database)
        if args.allow_public_network:
            source = OkxPublicShadowMarketSource(
                allow_public_network=True,
                timeout_seconds=args.public_timeout_seconds,
            )
            runtime = ShadowAssuranceRuntime(repository=repository, market_source=source)
            summary = runtime.run_public(
                provider=args.provider,
                instrument=args.instrument,
                at_utc=datetime.now(timezone.utc),
            )
        else:
            source = FixtureShadowMarketSource()
            runtime = ShadowAssuranceRuntime(repository=repository, market_source=source)
            summary = runtime.run_fixture(args.fixture)
        print(json.dumps(dict(summary.public_payload()), sort_keys=True))
        return 0 if summary.accepted else 2
    except Exception:
        print(json.dumps(_blocked_payload("shadow_runtime_failed_closed"), sort_keys=True))
        return 2
    finally:
        connection.close()


def inspect_main(args) -> int:
    try:
        database, connection = _connect(args)
    except Exception:
        print(json.dumps(_blocked_payload("shadow_postgresql_unavailable"), sort_keys=True))
        return 2
    try:
        repository = PostgresShadowRepository(connection, expected_database=database)
        bundle = repository.load_bundle(UUID(args.run_id))
        if bundle is None or bundle.get("status") != "complete":
            print(json.dumps(_blocked_payload("shadow_run_not_found_or_incomplete"), sort_keys=True))
            return 2
        decision = bundle["decision"]
        result = {
            "operation": "phase8b_shadow_inspect",
            "status": "loaded",
            "run_id": decision["shadow_run_id"],
            "scenario_id": decision["scenario_id"],
            "input_hash": decision["input_hash"],
            "decision_hash": decision["decision_hash"],
            "manifest_hash": decision["manifest_hash"],
            "configuration_hash": decision["configuration_hash"],
            "market_snapshot_hash": decision["market_snapshot_hash"],
            "synthetic_account_snapshot_hash": decision["synthetic_account_snapshot_hash"],
            "bundle_hash": bundle["bundle_hash"],
            "blockers": decision["blockers"],
            "shadow_intent_count": bundle["summary"]["shadow_intent_count"],
            "persistence_result": "loaded_complete",
            "production_transport_call_count": 0,
            "authenticated_endpoint_call_count": 0,
            "credential_read_count": 0,
            "production_write_count": 0,
        }
        print(json.dumps(result, sort_keys=True))
        return 0
    finally:
        connection.close()


def main(argv=None) -> int:
    args = _parser().parse_args(argv)
    if args.command == "run":
        return run_main(args)
    if args.command == "inspect":
        return inspect_main(args)
    if args.command == "matrix":
        print(json.dumps(dict(scenario_metrics(args.repository_sha)), sort_keys=True))
        return 0
    raise AssertionError("unreachable shadow command")


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ["main"]
