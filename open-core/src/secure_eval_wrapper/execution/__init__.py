"""Deterministic simulated-execution contracts.

The package performs no network or database activity at import time.  It intentionally exposes
only simulated execution; paper and live adapters are outside Phase 5.
"""

from secure_eval_wrapper.execution.broker import Broker, BrokerResult
from secure_eval_wrapper.execution.models import *  # noqa: F403

__all__ = ["Broker", "BrokerResult"]
