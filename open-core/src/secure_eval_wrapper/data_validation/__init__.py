"""Public validation contracts and offline-only OHLCV checks."""

from secure_eval_wrapper.data_validation.gating import (
    accepted_funding_rates,
    accepted_instruments,
    accepted_ohlcv_bars,
    accepted_trades,
)
from secure_eval_wrapper.data_validation.interfaces import (
    CrossSourceReconciler,
    DataValidator,
    DatasetPromoter,
    NormalizedRecord,
    ValidationInput,
)
from secure_eval_wrapper.data_validation.models import (
    QuarantineReason,
    ReconciliationResult,
    ValidationCheck,
    ValidationCheckStatus,
    ValidationReport,
    ValidationResult,
    ValidationSeverity,
    ValidationStatus,
)
from secure_eval_wrapper.data_validation.ohlcv import (
    DUPLICATED_TIMESTAMPS,
    INVALID_OHLC_RELATIONSHIP,
    INVALID_VOLUME,
    MISSING_BARS,
    NON_MONOTONIC_TIMESTAMPS,
    PARTIAL_CANDLE,
    FindingPolicy,
    OfflineOhlcvValidator,
    OhlcvValidationConfig,
    default_ohlcv_checks,
    validate_ohlcv_bars,
)
from secure_eval_wrapper.data_validation.persistence import (
    OfflinePersistenceSummary,
    persist_offline_ohlcv_validation_flow,
)
from secure_eval_wrapper.data_validation.quarantine import map_quarantine_reasons
from secure_eval_wrapper.data_validation.reconciliation_persistence import (
    ReconciliationPersistenceSummary,
    persist_reconciliation_result,
)
from secure_eval_wrapper.data_validation.reconciliation import (
    CROSS_SOURCE_CLOSE_TIME_MISMATCH,
    CROSS_SOURCE_EXTRA_BAR,
    CROSS_SOURCE_MISSING_TIMESTAMP,
    CROSS_SOURCE_PRICE_MISMATCH,
    CROSS_SOURCE_VOLUME_MISMATCH,
    OfflineOhlcvReconciler,
    OhlcvReconciliationConfig,
    default_ohlcv_reconciliation_checks,
    reconcile_ohlcv_sources,
)
from secure_eval_wrapper.data_validation.reporting import build_validation_report
from secure_eval_wrapper.data_validation.trades import validate_trades
from secure_eval_wrapper.data_validation.funding import validate_funding_rates
from secure_eval_wrapper.data_validation.instruments import (
    compare_instrument_metadata,
    validate_instruments,
)

__all__ = [
    "CROSS_SOURCE_CLOSE_TIME_MISMATCH",
    "CROSS_SOURCE_EXTRA_BAR",
    "CROSS_SOURCE_MISSING_TIMESTAMP",
    "CROSS_SOURCE_PRICE_MISMATCH",
    "CROSS_SOURCE_VOLUME_MISMATCH",
    "CrossSourceReconciler",
    "DataValidator",
    "DatasetPromoter",
    "DUPLICATED_TIMESTAMPS",
    "FindingPolicy",
    "INVALID_OHLC_RELATIONSHIP",
    "INVALID_VOLUME",
    "MISSING_BARS",
    "NON_MONOTONIC_TIMESTAMPS",
    "NormalizedRecord",
    "OfflineOhlcvReconciler",
    "OfflineOhlcvValidator",
    "OfflinePersistenceSummary",
    "OhlcvReconciliationConfig",
    "OhlcvValidationConfig",
    "PARTIAL_CANDLE",
    "QuarantineReason",
    "ReconciliationPersistenceSummary",
    "ReconciliationResult",
    "ValidationCheck",
    "ValidationCheckStatus",
    "ValidationInput",
    "ValidationReport",
    "ValidationResult",
    "ValidationSeverity",
    "ValidationStatus",
    "accepted_funding_rates",
    "accepted_instruments",
    "accepted_ohlcv_bars",
    "accepted_trades",
    "build_validation_report",
    "default_ohlcv_checks",
    "default_ohlcv_reconciliation_checks",
    "map_quarantine_reasons",
    "persist_offline_ohlcv_validation_flow",
    "persist_reconciliation_result",
    "reconcile_ohlcv_sources",
    "compare_instrument_metadata",
    "validate_funding_rates",
    "validate_instruments",
    "validate_ohlcv_bars",
    "validate_trades",
]


from secure_eval_wrapper.data_validation.market_persistence import (
    FundingPersistenceSummary,
    InstrumentPersistenceSummary,
    TradePersistenceSummary,
    persist_funding_validation_flow,
    persist_instrument_validation_flow,
    persist_trade_validation_flow,
)

__all__ += [
    "FundingPersistenceSummary",
    "InstrumentPersistenceSummary",
    "TradePersistenceSummary",
    "persist_funding_validation_flow",
    "persist_instrument_validation_flow",
    "persist_trade_validation_flow",
]
