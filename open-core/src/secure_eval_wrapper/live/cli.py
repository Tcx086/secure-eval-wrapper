"""Safe command-line boundaries for Phase 8A guarded live operations."""
from __future__ import annotations

import argparse
import json

from .endpoints import endpoint_catalog_hash
from .venues.okx_live import OkxProductionSpotAdapter


def _base(command: str) -> dict[str, object]:
    return {"command": command, "mode": "DRY-RUN", "production_write_status": "disabled", "account_fingerprint": "not-configured", "provider": "okx", "environment": "production", "manifest_id": None, "configuration_hash": None, "risk_cap": None, "approval_expiry": None, "blockers": ["PostgreSQL authority, explicit immutable configuration, persisted preflight, and exact approval are required"], "network_reads_occurred": False, "network_writes_occurred": False, "endpoint_catalog_hash": endpoint_catalog_hash(), "provider_implementation_hash": OkxProductionSpotAdapter.provider_implementation_hash}


def _parser(description: str) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=description)
    parser.add_argument("--postgresql", action="store_true", help="request local PostgreSQL authority; connection configuration remains local")
    parser.add_argument("--read-only-network-preflight", action="store_true", help="request optional authenticated read-only preflight; never enabled in CI")
    parser.add_argument("--enable-live-execution", action="store_true", help="Gate B only; Phase 8A still refuses production writes")
    return parser


def _run(command: str, argv=None) -> int:
    args = _parser(f"Secure Eval {command} (Phase 8A dry-run/read-only)").parse_args(argv)
    payload = _base(command)
    if args.enable_live_execution:
        payload["blockers"].append("Phase 8A production writes remain unconditionally disabled")
    if args.read_only_network_preflight:
        payload["blockers"].append("Use a fully configured local operator integration; this public-safe CLI does not load credentials implicitly")
    if args.postgresql:
        payload["blockers"].append("A live run/configuration/approval identity must be supplied by the local operator integration")
    print(json.dumps(payload, sort_keys=True, separators=(",", ":")))
    return 0


def preflight_main(argv=None): return _run("live-preflight", argv)
def dry_run_main(argv=None): return _run("live-dry-run", argv)
def status_main(argv=None): return _run("live-status", argv)
def reconcile_main(argv=None): return _run("live-reconcile", argv)
def kill_main(argv=None): return _run("live-kill", argv)


def main(argv=None):
    parser = argparse.ArgumentParser(description="Phase 8A guarded live CLI")
    parser.add_argument("command", choices=("preflight", "dry-run", "status", "reconcile", "kill"))
    args, rest = parser.parse_known_args(argv)
    return {"preflight": preflight_main, "dry-run": dry_run_main, "status": status_main, "reconcile": reconcile_main, "kill": kill_main}[args.command](rest)


if __name__ == "__main__":
    raise SystemExit(main())
