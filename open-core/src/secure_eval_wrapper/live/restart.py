"""Reconstruct dry-run live authority exclusively from PostgreSQL."""
from __future__ import annotations


def reconstruct_live_runtime(*, repository, live_run_id):
    state = repository.reconstruct(live_run_id)
    if state["run"]["production_write_enabled"] or state["manifest"]["production_write_enabled"]:
        raise PermissionError("restarted Phase 8A run unexpectedly enables writes")
    if not state["run"]["dry_run"] or not state["manifest"]["dry_run"]:
        raise PermissionError("restarted Phase 8A run is not dry-run")
    return state


__all__ = ["reconstruct_live_runtime"]
