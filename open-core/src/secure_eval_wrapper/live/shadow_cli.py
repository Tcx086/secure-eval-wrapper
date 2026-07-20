"""Command-line entry point for isolated Phase 8B shadow assurance."""
from __future__ import annotations

import argparse
import json
import os
from datetime import datetime, timezone
from typing import Mapping
from uuid import UUID

from .shadow_evidence import scenario_metrics
from .shadow_models import ShadowSafetyFacts
from .shadow_repository import (
    PostgresShadowRepository,
    validate_shadow_database_name,
    validate_shadow_postgres_host,
)
from .shadow_runtime import (
    FixtureShadowMarketSource,
    OkxPublicShadowMarketSource,
    ShadowAssuranceRuntime,
)


def _add_postgres_arguments(parser: argparse.ArgumentParser, *, database_required: bool) -> None:
    parser.add_argument("--postgres-database", required=database_required)
    parser.add_argument("--postgres-host", default=os.environ.get("POSTGRES_HOST", "127.0.0.1"))
    parser.add_argument(
        "--postgres-port",
        type=int,
        default=int(os.environ.get("POSTGRES_PORT", "5432")),
    )
    parser.add_argument("--postgres-user", default=os.environ.get("POSTGRES_USER"))
    parser.add_argument("--postgres-sslmode", default=os.environ.get("POSTGRES_SSLMODE", "disable"))


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="secure-eval-live-shadow")
    subparsers = parser.add_subparsers(dest="command", required=True)

    run = subparsers.add_parser("run", help="run one fixture or explicit public-data shadow")
    run.add_argument("--fixture", default="clean_flat_account")
    run.add_argument("--allow-public-network", action="store_true")
    run.add_argument("--provider", default="okx")
    run.add_argument("--instrument", default="BTC-USDT")
    _add_postgres_arguments(run, database_required=False)
    run.add_argument("--public-timeout-seconds", type=float, default=3.0)

    inspect = subparsers.add_parser("inspect", help="reload one persisted shadow bundle")
    inspect.add_argument("--run-id", required=True)
    _add_postgres_arguments(inspect, database_required=True)

    matrix = subparsers.add_parser("matrix", help="evaluate all socket-free fixtures")
    matrix.add_argument("--repository-sha", required=True)
    return parser


def _connect(args):
    # Validate the complete target identity before importing a driver or opening a socket.
    database = validate_shadow_database_name(args.postgres_database)
    host = validate_shadow_postgres_host(args.postgres_host)
    if isinstance(args.postgres_port, bool) or not 1 <= args.postgres_port <= 65535:
        raise PermissionError("shadow PostgreSQL port must be in [1, 65535]")
    try:
        import psycopg
    except ImportError as exc:
        raise RuntimeError("shadow PostgreSQL persistence requires the postgres extra") from exc
    connect_arguments: dict[str, object] = {
        "host": host,
        "port": args.postgres_port,
        "dbname": database,
        "sslmode": args.postgres_sslmode,
    }
    if args.postgres_user:
        connect_arguments["user"] = args.postgres_user
    # Authentication is deliberately delegated to libpq (.pgpass/PGPASSFILE/etc.).
    connection = psycopg.connect(**connect_arguments)
    return database, host, connection


def _zero_facts() -> ShadowSafetyFacts:
    return ShadowSafetyFacts(network_read_count=0)


def _fact_payload(facts: ShadowSafetyFacts) -> dict[str, int]:
    return {
        "network_read_count": facts.network_read_count,
        "network_write_count": facts.network_write_count,
        "production_transport_call_count": facts.production_transport_call_count,
        "authenticated_endpoint_call_count": facts.authenticated_endpoint_call_count,
        "credential_read_count": facts.credential_read_count,
        "production_write_count": facts.production_write_count,
    }


def _blocked_payload(
    blocker: str,
    facts: ShadowSafetyFacts | None = None,
    *,
    operation: str = "phase8b_shadow_run",
) -> dict[str, object]:
    return {
        "operation": operation,
        "status": "blocked",
        "blockers": [blocker],
        **_fact_payload(_zero_facts() if facts is None else facts),
        "production_submit_reachable": False,
        "production_cancel_reachable": False,
        "real_account_data_used": False,
        "operator_database_accessed": False,
        "authenticated_proof_executed": False,
    }


def _source_facts(source: object | None) -> ShadowSafetyFacts:
    if type(source) is OkxPublicShadowMarketSource:
        return source.safety_facts
    return _zero_facts()


def _emit(payload: Mapping[str, object], *, serialization_facts: ShadowSafetyFacts) -> bool:
    try:
        encoded = json.dumps(dict(payload), sort_keys=True)
    except Exception:
        fallback = _blocked_payload(
            "shadow_result_serialization_failed",
            serialization_facts,
            operation=str(payload.get("operation", "phase8b_shadow_run")),
        )
        encoded = json.JSONEncoder(sort_keys=True).encode(fallback)
        print(encoded)
        return False
    print(encoded)
    return True


def run_main(args) -> int:
    if not args.postgres_database:
        _emit(
            _blocked_payload("explicit_shadow_postgresql_target_required"),
            serialization_facts=_zero_facts(),
        )
        return 2
    try:
        database, host, connection = _connect(args)
    except Exception:
        _emit(_blocked_payload("shadow_postgresql_unavailable"), serialization_facts=_zero_facts())
        return 2
    source: object | None = None
    try:
        repository = PostgresShadowRepository(
            connection,
            expected_database=database,
            expected_host=host,
        )
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
        emitted = _emit(summary.public_payload(), serialization_facts=summary.safety_facts)
        return 0 if emitted and summary.accepted else 2
    except Exception:
        facts = _source_facts(source)
        _emit(_blocked_payload("shadow_runtime_failed_closed", facts), serialization_facts=facts)
        return 2
    finally:
        connection.close()


def inspect_main(args) -> int:
    try:
        database, host, connection = _connect(args)
    except Exception:
        _emit(
            _blocked_payload(
                "shadow_postgresql_unavailable", operation="phase8b_shadow_inspect"
            ),
            serialization_facts=_zero_facts(),
        )
        return 2
    try:
        repository = PostgresShadowRepository(
            connection,
            expected_database=database,
            expected_host=host,
        )
        bundle = repository.load_bundle(UUID(args.run_id))
        if bundle is None or bundle.get("status") != "complete":
            _emit(
                _blocked_payload(
                    "shadow_run_not_found_or_incomplete",
                    operation="phase8b_shadow_inspect",
                ),
                serialization_facts=_zero_facts(),
            )
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
            **_fact_payload(_zero_facts()),
            "source_classification": decision["data_provenance"]["classification"],
            "endpoint_identities": decision["data_provenance"]["endpoint_identities"],
            "network_read_count": decision["data_provenance"]["network_read_count"],
            "public_source_hashes": decision["data_provenance"]["response_source_hashes"],
            "public_provenance_hash": decision["data_provenance"]["provenance_hash"],
            "public_provenance_payload_hash": decision["data_provenance"]["payload_hash"],
            "public_source_instance_id": decision["data_provenance"]["source_instance_id"],
            "failure_kind": decision["data_provenance"]["failure_kind"],
            "data_provenance_hash": decision["data_provenance_hash"],
            "summary_hash": bundle["summary"]["summary_hash"],
        }
        return 0 if _emit(result, serialization_facts=_zero_facts()) else 2
    except Exception:
        _emit(
            _blocked_payload(
                "shadow_runtime_failed_closed", operation="phase8b_shadow_inspect"
            ),
            serialization_facts=_zero_facts(),
        )
        return 2
    finally:
        connection.close()


def main(argv=None) -> int:
    args = _parser().parse_args(argv)
    if args.command == "run":
        return run_main(args)
    if args.command == "inspect":
        return inspect_main(args)
    if args.command == "matrix":
        payload = scenario_metrics(args.repository_sha)
        return 0 if _emit(payload, serialization_facts=_zero_facts()) else 2
    raise AssertionError("unreachable shadow command")


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ["main"]
