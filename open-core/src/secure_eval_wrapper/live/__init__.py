"""Phase 8A guarded live execution foundation.

This package is intentionally separate from paper and simulated execution.  It supports
PostgreSQL-authoritative dry-run planning and optional read-only provider preflight only.
"""
from .approval import LiveApprovalController, confirmation_challenge_hash, manifest_preview_hash
from .authorities import *
from .configuration import GuardedLiveConfiguration, phase8a_dry_run_configuration
from .endpoints import EndpointClass, LiveOperation, endpoint_catalog_hash
from .gates import evaluate_live_write_authority
from .identity import *
from .kill_switch import arm_kill_switch, reset_kill_switch, trigger_kill_switch
from .models import *
from .preflight import LivePreflightEngine, LivePreflightEvidence, OperationalPreflightEvidence

_LAZY_BROKER_EXPORTS = {"DryRunResult", "GuardedLiveBroker"}


def __getattr__(name):
    if name in _LAZY_BROKER_EXPORTS:
        from .broker import DryRunResult, GuardedLiveBroker

        return {"DryRunResult": DryRunResult, "GuardedLiveBroker": GuardedLiveBroker}[name]
    raise AttributeError(name)


__all__ = [name for name in globals() if not name.startswith("_")] + sorted(_LAZY_BROKER_EXPORTS)
