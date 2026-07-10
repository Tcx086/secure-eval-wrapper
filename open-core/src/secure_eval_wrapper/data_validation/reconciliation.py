"""Deterministic, persistence-free OHLCV cross-source reconciliation."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from itertools import combinations
from uuid import NAMESPACE_URL, UUID, uuid5

from secure_eval_wrapper.data_collection.hashing import sha256_payload
from secure_eval_wrapper.data_collection.models import MarketDataType, NormalizedBar
from secure_eval_wrapper.data_collection.symbols import normalize_symbol
from secure_eval_wrapper.data_collection.time_utils import require_utc_datetime
from secure_eval_wrapper.data_validation.interfaces import (
    CrossSourceReconciler,
    NormalizedRecord,
)
from secure_eval_wrapper.data_validation.models import (
    QuarantineReason,
    ReconciliationResult,
    ValidationCheck,
    ValidationCheckStatus,
    ValidationResult,
    ValidationSeverity,
)
from secure_eval_wrapper.data_validation.ohlcv import FindingPolicy


CROSS_SOURCE_MISSING_TIMESTAMP = "cross_source_missing_timestamp"
CROSS_SOURCE_PRICE_MISMATCH = "cross_source_price_mismatch"
CROSS_SOURCE_VOLUME_MISMATCH = "cross_source_volume_mismatch"
CROSS_SOURCE_EXTRA_BAR = "cross_source_extra_bar"
CROSS_SOURCE_CLOSE_TIME_MISMATCH = "cross_source_close_time_mismatch"

_CHECK_ORDER = {
    CROSS_SOURCE_MISSING_TIMESTAMP: 0,
    CROSS_SOURCE_PRICE_MISMATCH: 1,
    CROSS_SOURCE_VOLUME_MISMATCH: 2,
    CROSS_SOURCE_EXTRA_BAR: 3,
    CROSS_SOURCE_CLOSE_TIME_MISMATCH: 4,
}
_PRICE_FIELDS = ("open", "high", "low", "close")
_BASIS_POINTS = Decimal("10000")


@dataclass(frozen=True)
class OhlcvReconciliationConfig:
    """Stable tolerances and finding policies for offline OHLCV reconciliation."""

    price_absolute_tolerance: Decimal = Decimal("0.00000001")
    price_relative_tolerance_bps: Decimal = Decimal("50")
    volume_relative_tolerance_bps: Decimal = Decimal("5000")
    missing_timestamp_policy: FindingPolicy = FindingPolicy.WARNING
    mismatch_policy: FindingPolicy = FindingPolicy.WARNING
    extra_bar_policy: FindingPolicy = FindingPolicy.WARNING

    def __post_init__(self) -> None:
        for field_name in (
            "price_absolute_tolerance",
            "price_relative_tolerance_bps",
            "volume_relative_tolerance_bps",
        ):
            value = getattr(self, field_name)
            if not isinstance(value, Decimal):
                raise TypeError(f"{field_name} must be a Decimal")
            if not value.is_finite() or value < 0:
                raise ValueError(f"{field_name} must be finite and non-negative")
        for field_name in (
            "missing_timestamp_policy",
            "mismatch_policy",
            "extra_bar_policy",
        ):
            object.__setattr__(self, field_name, FindingPolicy(getattr(self, field_name)))

    def as_mapping(self) -> Mapping[str, object]:
        """Return canonical-hashing-compatible configuration data."""

        return {
            "price_absolute_tolerance": self.price_absolute_tolerance,
            "price_relative_tolerance_bps": self.price_relative_tolerance_bps,
            "volume_relative_tolerance_bps": self.volume_relative_tolerance_bps,
            "missing_timestamp_policy": self.missing_timestamp_policy.value,
            "mismatch_policy": self.mismatch_policy.value,
            "extra_bar_policy": self.extra_bar_policy.value,
        }


@dataclass(frozen=True)
class _PreparedDatasets:
    provider_names: tuple[str, ...]
    datasets_by_provider: Mapping[str, tuple[NormalizedBar, ...]]
    bars_by_timestamp: Mapping[datetime, Mapping[str, NormalizedBar]]
    exchanges_by_provider: Mapping[str, str | None]
    symbol: str
    timeframe: str
    timestamps: tuple[datetime, ...]
    dataset_sha256: str


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _check_id(check_type: str) -> UUID:
    return uuid5(
        NAMESPACE_URL,
        f"secure-eval-wrapper:offline-ohlcv-reconciliation:{check_type}",
    )


def _policy_severity(policy: FindingPolicy) -> ValidationSeverity:
    if policy is FindingPolicy.REJECT:
        return ValidationSeverity.ERROR
    return ValidationSeverity.WARNING


def default_ohlcv_reconciliation_checks(
    config: OhlcvReconciliationConfig | None = None,
) -> tuple[ValidationCheck, ...]:
    """Return all Phase 2F checks in stable evaluation order."""

    config = OhlcvReconciliationConfig() if config is None else config
    definitions = (
        (
            CROSS_SOURCE_MISSING_TIMESTAMP,
            "Detect timestamps without coverage from every provider.",
            _policy_severity(config.missing_timestamp_policy),
            {"policy": config.missing_timestamp_policy.value},
        ),
        (
            CROSS_SOURCE_PRICE_MISMATCH,
            "Compare pairwise open, high, low, and close values within tolerance.",
            _policy_severity(config.mismatch_policy),
            {
                "policy": config.mismatch_policy.value,
                "price_absolute_tolerance": config.price_absolute_tolerance,
                "price_relative_tolerance_bps": config.price_relative_tolerance_bps,
            },
        ),
        (
            CROSS_SOURCE_VOLUME_MISMATCH,
            "Compare pairwise volume values within relative tolerance.",
            _policy_severity(config.mismatch_policy),
            {
                "policy": config.mismatch_policy.value,
                "volume_relative_tolerance_bps": config.volume_relative_tolerance_bps,
            },
        ),
        (
            CROSS_SOURCE_EXTRA_BAR,
            "Detect timestamps supplied by only one provider.",
            _policy_severity(config.extra_bar_policy),
            {"policy": config.extra_bar_policy.value},
        ),
        (
            CROSS_SOURCE_CLOSE_TIME_MISMATCH,
            "Compare close timestamps when at least two providers supply them.",
            _policy_severity(config.mismatch_policy),
            {"policy": config.mismatch_policy.value},
        ),
    )
    return tuple(
        ValidationCheck(
            check_id=_check_id(check_type),
            check_type=check_type,
            description=description,
            severity=severity,
            data_types=(MarketDataType.OHLCV,),
            parameters=parameters,
        )
        for check_type, description, severity, parameters in definitions
    )


def _validate_decimal(value: object, *, field_name: str) -> Decimal:
    if not isinstance(value, Decimal):
        raise TypeError(f"{field_name} must be a Decimal")
    if not value.is_finite():
        raise ValueError(f"{field_name} must be finite")
    return value


def _bar_identity(bar: NormalizedBar) -> Mapping[str, object]:
    return {
        "symbol": bar.symbol,
        "exchange": bar.exchange,
        "timeframe": bar.timeframe,
        "bar_open_time_utc": bar.bar_open_time_utc,
        "bar_close_time_utc": bar.bar_close_time_utc,
        "open": bar.open,
        "high": bar.high,
        "low": bar.low,
        "close": bar.close,
        "volume": bar.volume,
        "source_observation_ids": tuple(sorted(bar.source_observation_ids, key=str)),
    }


def _prepare_datasets(
    datasets_by_provider: Mapping[str, Sequence[NormalizedRecord]],
) -> _PreparedDatasets:
    if not isinstance(datasets_by_provider, Mapping):
        raise TypeError("datasets_by_provider must be a mapping")
    if len(datasets_by_provider) < 2:
        raise ValueError("OHLCV reconciliation requires at least two provider datasets")

    normalized_datasets: dict[str, tuple[NormalizedBar, ...]] = {}
    provider_names_by_casefold: dict[str, str] = {}
    exchanges_by_provider: dict[str, str | None] = {}
    symbols: set[str] = set()
    timeframes: set[str] = set()

    for raw_provider_name, raw_records in datasets_by_provider.items():
        if not isinstance(raw_provider_name, str) or not raw_provider_name.strip():
            raise ValueError("provider names must be non-empty strings")
        provider_name = raw_provider_name.strip()
        folded_name = provider_name.casefold()
        if folded_name in provider_names_by_casefold:
            raise ValueError("provider names must be unique after trimming and case folding")
        provider_names_by_casefold[folded_name] = provider_name
        try:
            records = tuple(raw_records)
        except TypeError as exc:
            raise TypeError("each provider dataset must be a sequence") from exc

        bars: list[NormalizedBar] = []
        timestamps: set[datetime] = set()
        exchanges: set[str] = set()
        for position, record in enumerate(records):
            if not isinstance(record, NormalizedBar):
                raise TypeError(
                    f"provider '{provider_name}' record {position} must be a NormalizedBar"
                )
            open_time = require_utc_datetime(
                record.bar_open_time_utc,
                field_name=(
                    f"provider '{provider_name}' record {position} bar_open_time_utc"
                ),
            )
            if record.bar_close_time_utc is not None:
                require_utc_datetime(
                    record.bar_close_time_utc,
                    field_name=(
                        f"provider '{provider_name}' record {position} bar_close_time_utc"
                    ),
                )
            if normalize_symbol(record.symbol) != record.symbol:
                raise ValueError("reconciliation bars must use canonical BASE-QUOTE symbols")
            if not isinstance(record.timeframe, str) or not record.timeframe.strip():
                raise ValueError("reconciliation bars must have a non-empty timeframe")
            if not isinstance(record.exchange, str) or not record.exchange.strip():
                raise ValueError("reconciliation bars must have a non-empty exchange")
            if not record.source_observation_ids:
                raise ValueError(
                    "reconciliation bars must preserve source_observation_ids"
                )
            if any(
                not isinstance(observation_id, UUID)
                for observation_id in record.source_observation_ids
            ):
                raise TypeError("source_observation_ids must contain UUID values")
            for field_name in (*_PRICE_FIELDS, "volume"):
                _validate_decimal(
                    getattr(record, field_name),
                    field_name=f"NormalizedBar {field_name}",
                )
            if open_time in timestamps:
                raise ValueError(
                    f"provider '{provider_name}' has duplicate bar_open_time_utc values"
                )
            timestamps.add(open_time)
            symbols.add(record.symbol)
            timeframes.add(record.timeframe.strip())
            exchanges.add(record.exchange.strip())
            bars.append(record)

        if len(exchanges) > 1:
            raise ValueError(
                f"provider '{provider_name}' dataset contains mixed exchanges"
            )
        normalized_datasets[provider_name] = tuple(
            sorted(bars, key=lambda item: (item.bar_open_time_utc, str(item.bar_id)))
        )
        exchanges_by_provider[provider_name] = next(iter(exchanges), None)

    if not symbols:
        raise ValueError("OHLCV reconciliation requires at least one normalized bar")
    if len(symbols) != 1:
        raise ValueError("OHLCV reconciliation does not support mixed symbols")
    if len(timeframes) != 1:
        raise ValueError("OHLCV reconciliation does not support mixed timeframes")

    provider_names = tuple(sorted(normalized_datasets))
    bars_by_timestamp: dict[datetime, dict[str, NormalizedBar]] = {}
    for provider_name in provider_names:
        for bar in normalized_datasets[provider_name]:
            bars_by_timestamp.setdefault(bar.bar_open_time_utc, {})[provider_name] = bar
    ordered_timestamps = tuple(sorted(bars_by_timestamp))
    dataset_payload = {
        "datasets_by_provider": {
            provider_name: tuple(
                _bar_identity(bar) for bar in normalized_datasets[provider_name]
            )
            for provider_name in provider_names
        }
    }
    return _PreparedDatasets(
        provider_names=provider_names,
        datasets_by_provider=normalized_datasets,
        bars_by_timestamp={
            timestamp: dict(bars_by_timestamp[timestamp])
            for timestamp in ordered_timestamps
        },
        exchanges_by_provider={
            provider_name: exchanges_by_provider[provider_name]
            for provider_name in provider_names
        },
        symbol=next(iter(symbols)),
        timeframe=next(iter(timeframes)),
        timestamps=ordered_timestamps,
        dataset_sha256=sha256_payload(dataset_payload),
    )


def _relative_difference_bps(left: Decimal, right: Decimal) -> Decimal:
    denominator = max(abs(left), abs(right))
    if denominator == 0:
        return Decimal(0)
    return abs(left - right) / denominator * _BASIS_POINTS


def _observation_ids(bars: Sequence[NormalizedBar]) -> tuple[UUID, ...]:
    return tuple(
        sorted(
            {
                observation_id
                for bar in bars
                for observation_id in bar.source_observation_ids
            },
            key=str,
        )
    )


def _collect_findings(
    prepared: _PreparedDatasets,
    config: OhlcvReconciliationConfig,
) -> Mapping[str, tuple[Mapping[str, object], ...]]:
    findings: dict[str, list[Mapping[str, object]]] = {
        check_type: [] for check_type in _CHECK_ORDER
    }
    for timestamp in prepared.timestamps:
        by_provider = prepared.bars_by_timestamp[timestamp]
        present_providers = tuple(
            provider for provider in prepared.provider_names if provider in by_provider
        )
        missing_providers = tuple(
            provider for provider in prepared.provider_names if provider not in by_provider
        )
        present_bars = tuple(by_provider[provider] for provider in present_providers)
        present_observation_ids = _observation_ids(present_bars)

        if missing_providers:
            findings[CROSS_SOURCE_MISSING_TIMESTAMP].append(
                {
                    "bar_open_time_utc": timestamp,
                    "present_providers": present_providers,
                    "missing_providers": missing_providers,
                    "source_observation_ids": present_observation_ids,
                }
            )
        if len(present_providers) == 1:
            findings[CROSS_SOURCE_EXTRA_BAR].append(
                {
                    "bar_open_time_utc": timestamp,
                    "extra_provider": present_providers[0],
                    "absent_providers": missing_providers,
                    "source_observation_ids": present_observation_ids,
                }
            )

        price_comparisons: list[Mapping[str, object]] = []
        volume_comparisons: list[Mapping[str, object]] = []
        close_time_comparisons: list[Mapping[str, object]] = []
        for left_provider, right_provider in combinations(present_providers, 2):
            left_bar = by_provider[left_provider]
            right_bar = by_provider[right_provider]
            source_ids = _observation_ids((left_bar, right_bar))
            for field_name in _PRICE_FIELDS:
                left_value = getattr(left_bar, field_name)
                right_value = getattr(right_bar, field_name)
                absolute_difference = abs(left_value - right_value)
                relative_difference_bps = _relative_difference_bps(
                    left_value,
                    right_value,
                )
                if (
                    absolute_difference > config.price_absolute_tolerance
                    and relative_difference_bps
                    > config.price_relative_tolerance_bps
                ):
                    price_comparisons.append(
                        {
                            "field": field_name,
                            "left_provider": left_provider,
                            "right_provider": right_provider,
                            "left_value": left_value,
                            "right_value": right_value,
                            "absolute_difference": absolute_difference,
                            "relative_difference_bps": relative_difference_bps,
                            "source_observation_ids": source_ids,
                        }
                    )

            relative_volume_difference_bps = _relative_difference_bps(
                left_bar.volume,
                right_bar.volume,
            )
            if relative_volume_difference_bps > config.volume_relative_tolerance_bps:
                volume_comparisons.append(
                    {
                        "left_provider": left_provider,
                        "right_provider": right_provider,
                        "left_value": left_bar.volume,
                        "right_value": right_bar.volume,
                        "relative_difference_bps": relative_volume_difference_bps,
                        "source_observation_ids": source_ids,
                    }
                )

            if (
                left_bar.bar_close_time_utc is not None
                and right_bar.bar_close_time_utc is not None
                and left_bar.bar_close_time_utc != right_bar.bar_close_time_utc
            ):
                close_time_comparisons.append(
                    {
                        "left_provider": left_provider,
                        "right_provider": right_provider,
                        "left_close_time_utc": left_bar.bar_close_time_utc,
                        "right_close_time_utc": right_bar.bar_close_time_utc,
                        "source_observation_ids": source_ids,
                    }
                )

        if price_comparisons:
            findings[CROSS_SOURCE_PRICE_MISMATCH].append(
                {
                    "bar_open_time_utc": timestamp,
                    "comparisons": tuple(price_comparisons),
                    "source_observation_ids": _observation_ids(
                        tuple(
                            by_provider[provider]
                            for provider in present_providers
                            if any(
                                provider
                                in (item["left_provider"], item["right_provider"])
                                for item in price_comparisons
                            )
                        )
                    ),
                }
            )
        if volume_comparisons:
            findings[CROSS_SOURCE_VOLUME_MISMATCH].append(
                {
                    "bar_open_time_utc": timestamp,
                    "comparisons": tuple(volume_comparisons),
                    "source_observation_ids": _observation_ids(
                        tuple(
                            by_provider[provider]
                            for provider in present_providers
                            if any(
                                provider
                                in (item["left_provider"], item["right_provider"])
                                for item in volume_comparisons
                            )
                        )
                    ),
                }
            )
        if close_time_comparisons:
            findings[CROSS_SOURCE_CLOSE_TIME_MISMATCH].append(
                {
                    "bar_open_time_utc": timestamp,
                    "comparisons": tuple(close_time_comparisons),
                    "source_observation_ids": _observation_ids(
                        tuple(
                            by_provider[provider]
                            for provider in present_providers
                            if any(
                                provider
                                in (item["left_provider"], item["right_provider"])
                                for item in close_time_comparisons
                            )
                        )
                    ),
                }
            )

    return {
        check_type: tuple(findings[check_type])
        for check_type in sorted(_CHECK_ORDER, key=_CHECK_ORDER.get)
    }


def _selected_checks(
    checks: Sequence[ValidationCheck],
    config: OhlcvReconciliationConfig,
) -> tuple[ValidationCheck, ...]:
    selected = tuple(checks) or default_ohlcv_reconciliation_checks(config)
    if any(not isinstance(check, ValidationCheck) for check in selected):
        raise TypeError("reconciliation checks must be ValidationCheck values")
    if len({check.check_type for check in selected}) != len(selected):
        raise ValueError("reconciliation check types must be unique")
    unknown = {check.check_type for check in selected} - set(_CHECK_ORDER)
    if unknown:
        raise ValueError(f"unsupported OHLCV reconciliation checks: {sorted(unknown)}")
    if any(MarketDataType.OHLCV not in check.data_types for check in selected):
        raise ValueError("all reconciliation checks must declare the OHLCV data type")
    return tuple(
        sorted(
            selected,
            key=lambda check: (_CHECK_ORDER[check.check_type], str(check.check_id)),
        )
    )


def _finding_status(
    severity: ValidationSeverity,
    *,
    found: bool,
) -> ValidationCheckStatus:
    if not found:
        return ValidationCheckStatus.PASSED
    if severity in (ValidationSeverity.ERROR, ValidationSeverity.CRITICAL):
        return ValidationCheckStatus.FAILED
    return ValidationCheckStatus.WARNING


def _message(check_type: str, finding_count: int) -> str:
    descriptions = {
        CROSS_SOURCE_MISSING_TIMESTAMP: "missing provider-coverage timestamp",
        CROSS_SOURCE_PRICE_MISMATCH: "price-mismatch timestamp",
        CROSS_SOURCE_VOLUME_MISMATCH: "volume-mismatch timestamp",
        CROSS_SOURCE_EXTRA_BAR: "provider-specific extra bar",
        CROSS_SOURCE_CLOSE_TIME_MISMATCH: "close-time-mismatch timestamp",
    }
    if finding_count == 0:
        return f"No cross-source {descriptions[check_type]} findings detected."
    return f"Detected {finding_count} cross-source {descriptions[check_type]} finding(s)."


def _tolerance_details(
    check_type: str,
    config: OhlcvReconciliationConfig,
) -> Mapping[str, object]:
    details: dict[str, object] = {}
    if check_type == CROSS_SOURCE_PRICE_MISMATCH:
        details.update(
            {
                "price_absolute_tolerance": config.price_absolute_tolerance,
                "price_relative_tolerance_bps": config.price_relative_tolerance_bps,
            }
        )
    elif check_type == CROSS_SOURCE_VOLUME_MISMATCH:
        details.update(
            {
                "volume_relative_tolerance_bps": config.volume_relative_tolerance_bps,
            }
        )
    return details


def _affected_observation_ids(
    findings: Sequence[Mapping[str, object]],
) -> tuple[UUID, ...]:
    values: set[UUID] = set()
    for finding in findings:
        source_ids = finding.get("source_observation_ids", ())
        if isinstance(source_ids, Sequence):
            values.update(item for item in source_ids if isinstance(item, UUID))
    return tuple(sorted(values, key=str))


class OfflineOhlcvReconciler(CrossSourceReconciler):
    """Compare normalized OHLCV datasets without network access or persistence."""

    def __init__(
        self,
        *,
        config: OhlcvReconciliationConfig | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._config = OhlcvReconciliationConfig() if config is None else config
        if not isinstance(self._config, OhlcvReconciliationConfig):
            raise TypeError("config must be an OhlcvReconciliationConfig")
        self._clock = _utc_now if clock is None else clock

    def reconcile(
        self,
        *,
        validation_run_id: UUID,
        datasets_by_provider: Mapping[str, Sequence[NormalizedRecord]],
        checks: Sequence[ValidationCheck] = (),
    ) -> ReconciliationResult:
        """Reconcile one normalized symbol/timeframe window deterministically."""

        if not isinstance(validation_run_id, UUID):
            raise TypeError("validation_run_id must be a UUID")
        prepared = _prepare_datasets(datasets_by_provider)
        selected_checks = _selected_checks(checks, self._config)
        created_at_utc = require_utc_datetime(
            self._clock(),
            field_name="offline reconciler clock",
        )
        all_findings = _collect_findings(prepared, self._config)
        check_payload = tuple(
            {
                "check_id": check.check_id,
                "check_type": check.check_type,
                "severity": check.severity,
                "parameters": dict(check.parameters),
            }
            for check in selected_checks
        )
        config_sha256 = sha256_payload(
            {
                "config": dict(self._config.as_mapping()),
                "checks": check_payload,
            }
        )
        identity_sha256 = sha256_payload(
            {
                "validation_run_id": validation_run_id,
                "dataset_sha256": prepared.dataset_sha256,
                "config_sha256": config_sha256,
            }
        )
        window_start = prepared.timestamps[0]
        window_end = prepared.timestamps[-1]

        results: list[ValidationResult] = []
        for check in selected_checks:
            findings = all_findings[check.check_type]
            status = _finding_status(check.severity, found=bool(findings))
            details = {
                "check_type": check.check_type,
                "quarantine_reason": QuarantineReason.CROSS_SOURCE_MISMATCH.value,
                "provider_names": prepared.provider_names,
                "exchanges_by_provider": dict(prepared.exchanges_by_provider),
                "symbol": prepared.symbol,
                "timeframe": prepared.timeframe,
                "policy": (
                    FindingPolicy.REJECT.value
                    if check.severity in (ValidationSeverity.ERROR, ValidationSeverity.CRITICAL)
                    else FindingPolicy.WARNING.value
                ),
                **dict(_tolerance_details(check.check_type, self._config)),
                "finding_count": len(findings),
                "findings": findings,
            }
            results.append(
                ValidationResult(
                    result_id=uuid5(
                        NAMESPACE_URL,
                        "offline-ohlcv-reconciliation-result:"
                        f"{identity_sha256}:{check.check_id}",
                    ),
                    validation_run_id=validation_run_id,
                    check_id=check.check_id,
                    status=status,
                    created_at_utc=created_at_utc,
                    message=_message(check.check_type, len(findings)),
                    symbol=prepared.symbol,
                    timeframe=prepared.timeframe,
                    window_start_utc=window_start,
                    window_end_utc=window_end,
                    affected_observation_ids=_affected_observation_ids(findings),
                    details=details,
                )
            )

        statuses = {result.status for result in results}
        if ValidationCheckStatus.FAILED in statuses:
            reconciliation_status = ValidationCheckStatus.FAILED
        elif ValidationCheckStatus.WARNING in statuses:
            reconciliation_status = ValidationCheckStatus.WARNING
        else:
            reconciliation_status = ValidationCheckStatus.PASSED

        missing_findings = all_findings[CROSS_SOURCE_MISSING_TIMESTAMP]
        price_findings = all_findings[CROSS_SOURCE_PRICE_MISMATCH]
        volume_findings = all_findings[CROSS_SOURCE_VOLUME_MISMATCH]
        extra_findings = all_findings[CROSS_SOURCE_EXTRA_BAR]
        close_time_findings = all_findings[CROSS_SOURCE_CLOSE_TIME_MISMATCH]
        metrics = {
            "provider_count": len(prepared.provider_names),
            "timestamp_count": len(prepared.timestamps),
            "compared_bar_count": sum(
                len(prepared.bars_by_timestamp[timestamp]) >= 2
                for timestamp in prepared.timestamps
            ),
            "missing_count": sum(
                len(finding["missing_providers"])
                for finding in missing_findings
            ),
            "price_mismatch_count": len(price_findings),
            "volume_mismatch_count": len(volume_findings),
            "extra_bar_count": len(extra_findings),
            "close_time_mismatch_count": len(close_time_findings),
        }
        reconciliation_id = uuid5(
            NAMESPACE_URL,
            f"offline-ohlcv-reconciliation:{identity_sha256}",
        )
        result_sha256 = sha256_payload(
            {
                "reconciliation_id": reconciliation_id,
                "validation_run_id": validation_run_id,
                "data_type": MarketDataType.OHLCV,
                "symbol": prepared.symbol,
                "timeframe": prepared.timeframe,
                "provider_names": prepared.provider_names,
                "window_start_utc": window_start,
                "window_end_utc": window_end,
                "status": reconciliation_status,
                "results": tuple(
                    {
                        "result_id": result.result_id,
                        "check_id": result.check_id,
                        "status": result.status,
                        "message": result.message,
                        "symbol": result.symbol,
                        "timeframe": result.timeframe,
                        "window_start_utc": result.window_start_utc,
                        "window_end_utc": result.window_end_utc,
                        "affected_observation_ids": result.affected_observation_ids,
                        "details": dict(result.details),
                    }
                    for result in results
                ),
                "metrics": metrics,
                "config_sha256": config_sha256,
                "dataset_sha256": prepared.dataset_sha256,
            }
        )
        return ReconciliationResult(
            reconciliation_id=reconciliation_id,
            validation_run_id=validation_run_id,
            data_type=MarketDataType.OHLCV,
            symbol=prepared.symbol,
            timeframe=prepared.timeframe,
            provider_names=prepared.provider_names,
            window_start_utc=window_start,
            window_end_utc=window_end,
            status=reconciliation_status,
            results=tuple(results),
            metrics=metrics,
            config_sha256=config_sha256,
            dataset_sha256=prepared.dataset_sha256,
            result_sha256=result_sha256,
            created_at_utc=created_at_utc,
        )


def reconcile_ohlcv_sources(
    *,
    validation_run_id: UUID,
    datasets_by_provider: Mapping[str, Sequence[NormalizedBar]],
    config: OhlcvReconciliationConfig | None = None,
    checks: Sequence[ValidationCheck] = (),
    clock: Callable[[], datetime] | None = None,
) -> ReconciliationResult:
    """Convenience entry point for deterministic offline OHLCV reconciliation."""

    return OfflineOhlcvReconciler(config=config, clock=clock).reconcile(
        validation_run_id=validation_run_id,
        datasets_by_provider=datasets_by_provider,
        checks=checks,
    )


__all__ = [
    "CROSS_SOURCE_CLOSE_TIME_MISMATCH",
    "CROSS_SOURCE_EXTRA_BAR",
    "CROSS_SOURCE_MISSING_TIMESTAMP",
    "CROSS_SOURCE_PRICE_MISMATCH",
    "CROSS_SOURCE_VOLUME_MISMATCH",
    "OfflineOhlcvReconciler",
    "OhlcvReconciliationConfig",
    "default_ohlcv_reconciliation_checks",
    "reconcile_ohlcv_sources",
]
