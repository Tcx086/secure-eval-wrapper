"""Storage contracts for the PostgreSQL-first framework.

Phase 1 exposes interfaces and configuration helpers only. Concrete repositories and runtime
database writes are intentionally deferred to later implementation phases.
"""

from secure_eval_wrapper.storage.alpha_signal_bundle import (
    AlphaSignalBundlePersistenceError,
    AlphaSignalBundleSummary,
    persist_alpha_signal_bundle,
)

from secure_eval_wrapper.storage.postgres.config import (
    PostgresConfig,
    PostgresConfigError,
    load_postgres_config,
)

__all__ = [
    "AlphaSignalBundlePersistenceError",
    "AlphaSignalBundleSummary",
    "persist_alpha_signal_bundle",
    "PostgresConfig",
    "PostgresConfigError",
    "load_postgres_config",
]
