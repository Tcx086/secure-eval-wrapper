"""PostgreSQL configuration and lazy repository exports.

Importing this package never selects a driver, opens a connection, or eagerly imports the
validation layer. Repository implementations accept an injected DB-API PostgreSQL connection.
"""

from importlib import import_module

from secure_eval_wrapper.storage.postgres.config import (
    PostgresConfig,
    PostgresConfigError,
    load_postgres_config,
)
from secure_eval_wrapper.storage.postgres.connection import build_connection_kwargs

_LAZY_EXPORTS = {
    "PostgresDataQualityRepository": ("secure_eval_wrapper.storage.postgres.repositories", "PostgresDataQualityRepository"),
    "PostgresMarketDataRepository": ("secure_eval_wrapper.storage.postgres.repositories", "PostgresMarketDataRepository"),
    "PostgresOfflineValidationRepository": ("secure_eval_wrapper.storage.postgres.repositories", "PostgresOfflineValidationRepository"),
    "PostgresQuarantineRepository": ("secure_eval_wrapper.storage.postgres.repositories", "PostgresQuarantineRepository"),
    "ValidationReportConflictError": ("secure_eval_wrapper.storage.postgres.repositories", "ValidationReportConflictError"),
    "PostgresReconciliationRepository": ("secure_eval_wrapper.storage.postgres.reconciliation_repositories", "PostgresReconciliationRepository"),
    "PostgresOhlcvPipelineRepository": ("secure_eval_wrapper.storage.postgres.reconciliation_repositories", "PostgresOhlcvPipelineRepository"),
    "reconciliation_check_result_to_row": ("secure_eval_wrapper.storage.postgres.reconciliation_mappers", "reconciliation_check_result_to_row"),
    "reconciliation_result_to_row": ("secure_eval_wrapper.storage.postgres.reconciliation_mappers", "reconciliation_result_to_row"),
    "AlphaSignalConflictError": ("secure_eval_wrapper.storage.postgres.alpha_signal_repositories", "AlphaSignalConflictError"),
    "PostgresAlphaRepository": ("secure_eval_wrapper.storage.postgres.alpha_signal_repositories", "PostgresAlphaRepository"),
    "PostgresAlphaSignalRepository": ("secure_eval_wrapper.storage.postgres.alpha_signal_repositories", "PostgresAlphaSignalRepository"),
    "PostgresSignalRepository": ("secure_eval_wrapper.storage.postgres.alpha_signal_repositories", "PostgresSignalRepository"),
    "alpha_definition_to_row": ("secure_eval_wrapper.storage.postgres.alpha_signal_mappers", "alpha_definition_to_row"),
    "alpha_run_to_row": ("secure_eval_wrapper.storage.postgres.alpha_signal_mappers", "alpha_run_to_row"),
    "alpha_value_to_row": ("secure_eval_wrapper.storage.postgres.alpha_signal_mappers", "alpha_value_to_row"),
    "signal_component_to_row": ("secure_eval_wrapper.storage.postgres.alpha_signal_mappers", "signal_component_to_row"),
    "signal_run_to_row": ("secure_eval_wrapper.storage.postgres.alpha_signal_mappers", "signal_run_to_row"),
    "standardized_signal_to_row": ("secure_eval_wrapper.storage.postgres.alpha_signal_mappers", "standardized_signal_to_row"),
}

def __getattr__(name: str):
    try:
        module_name, attribute = _LAZY_EXPORTS[name]
    except KeyError as exc:
        raise AttributeError(name) from exc
    value = getattr(import_module(module_name), attribute)
    globals()[name] = value
    return value

__all__ = [
    "PostgresConfig",
    "PostgresConfigError",
    "build_connection_kwargs",
    "load_postgres_config",
    *_LAZY_EXPORTS,
]
