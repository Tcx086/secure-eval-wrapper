"""Command-line entry point for audited local Phase 8B PostgreSQL bootstrap."""
from __future__ import annotations

import argparse
import json
import sys

from .bootstrap import (
    DEFAULT_DATABASE,
    BootstrapSafetyError,
    Phase8BOperatorBootstrap,
    PostgresAdminTarget,
)


def _connection_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--database", default=DEFAULT_DATABASE)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=5432)
    parser.add_argument("--admin-database", default="postgres")
    parser.add_argument("--admin-user", default="postgres")
    parser.add_argument(
        "--sslmode",
        choices=("disable", "require", "verify-ca", "verify-full"),
        default="disable",
    )


def _exact_plan_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--expected-reviewed-sha", required=True)
    parser.add_argument("--account-fingerprint", required=True)
    parser.add_argument("--instrument", required=True)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="secure-eval-live-bootstrap",
        description=(
            "Plan and initialize a dedicated local PostgreSQL Phase 8B read-only "
            "operator database without loading credentials or making OKX requests."
        ),
    )
    commands = parser.add_subparsers(dest="command", required=True)
    inspect_parser = commands.add_parser("inspect", help="read-only target inspection")
    _connection_arguments(inspect_parser)
    plan_parser = commands.add_parser("plan", help="produce a read-only hashed plan")
    _connection_arguments(plan_parser)
    _exact_plan_arguments(plan_parser)
    initialize_parser = commands.add_parser(
        "initialize", help="apply one exact previously reviewed plan"
    )
    _connection_arguments(initialize_parser)
    _exact_plan_arguments(initialize_parser)
    initialize_parser.add_argument("--plan-hash", required=True)
    initialize_parser.add_argument("--confirm-readonly-bootstrap", action="store_true")
    verify_parser = commands.add_parser("verify", help="read-only readiness verification")
    _connection_arguments(verify_parser)
    _exact_plan_arguments(verify_parser)
    return parser


def _target(args) -> PostgresAdminTarget:
    return PostgresAdminTarget(
        database=args.database,
        host=args.host,
        port=args.port,
        admin_database=args.admin_database,
        admin_user=args.admin_user,
        sslmode=args.sslmode,
    )


def _failure_payload(message: str) -> dict[str, object]:
    return {
        "status": "failed_closed",
        "error": message,
        "credentials_accessed": False,
        "network_reads_occurred": False,
        "network_writes_occurred": False,
        "real_proof_executed": False,
    }


def main(argv=None, *, bootstrap_factory=Phase8BOperatorBootstrap) -> int:
    args = build_parser().parse_args(argv)
    try:
        bootstrap = bootstrap_factory(_target(args))
        if args.command == "inspect":
            result = bootstrap.inspect_public()
        elif args.command == "plan":
            result = bootstrap.plan(
                expected_reviewed_sha=args.expected_reviewed_sha,
                account_fingerprint=args.account_fingerprint,
                instrument=args.instrument,
            )
        elif args.command == "initialize":
            result = bootstrap.initialize(
                expected_reviewed_sha=args.expected_reviewed_sha,
                account_fingerprint=args.account_fingerprint,
                instrument=args.instrument,
                previous_plan_hash=args.plan_hash,
                confirm_readonly_bootstrap=args.confirm_readonly_bootstrap,
            )
        else:
            result = bootstrap.verify(
                expected_reviewed_sha=args.expected_reviewed_sha,
                account_fingerprint=args.account_fingerprint,
                instrument=args.instrument,
            )
    except (BootstrapSafetyError, ValueError) as exc:
        print(json.dumps(_failure_payload(str(exc)), sort_keys=True, separators=(",", ":")))
        return 2
    except Exception:
        print(json.dumps(
            _failure_payload("local_postgresql_operation_failed"),
            sort_keys=True,
            separators=(",", ":"),
        ))
        return 3
    print(json.dumps(result, sort_keys=True, separators=(",", ":"), default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ["build_parser", "main"]
