"""Deterministic single-source checks for offline normalized OHLCV bars."""

from __future__ import annotations

import re
from collections import defaultdict
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from enum import Enum
from uuid import NAMESPACE_URL, UUID, uuid5

from secure_eval_wrapper.data_collection.models import MarketDataType, NormalizedBar
from secure_eval_wrapper.data_collection.symbols import normalize_symbol
from secure_eval_wrapper.data_collection.time_utils import require_utc_datetime
from secure_eval_wrapper.data_validation.interfaces import DataValidator, ValidationInput
from secure_eval_wrapper.data_validation.models import (
    QuarantineReason,
    ValidationCheck,
    ValidationCheckStatus,
    ValidationReport,
    ValidationResult,
    ValidationSeverity,
)
from secure_eval_wrapper.data_validation.reporting import build_validation_report


MISSING_BARS = "missing_bars"
DUPLICATED_TIMESTAMPS = "duplicated_timestamps"
NON_MONOTONIC_TIMESTAMPS = "non_monotonic_timestamps"
INVALID_OHLC_RELATIONSHIP = "invalid_ohlc_relationship"
INVALID_VOLUME = "invalid_volume"
PARTIAL_CANDLE = "partial_candle"

_CHECK_ORDER = {
    MISSING_BARS: 0,
    DUPLICATED_TIMESTAMPS: 1,
    NON_MONOTONIC_TIMESTAMPS: 2,
    INVALID_OHLC_RELATIONSHIP: 3,
    INVALID_VOLUME: 4,
    PARTIAL_CANDLE: 5,
}
_CHECK_REASONS = {
    MISSING_BARS: QuarantineReason.MISSING_REQUIRED_DATA,
    DUPLICATED_TIMESTAMPS: QuarantineReason.DUPLICATE_RECORD,
    NON_MONOTONIC_TIMESTAMPS: QuarantineReason.NON_MONOTONIC_TIMESTAMP,
    INVALID_OHLC_RELATIONSHIP: QuarantineReason.INVALID_OHLC_RELATIONSHIP,
    INVALID_VOLUME: QuarantineReason.INVALID_VOLUME,
    PARTIAL_CANDLE: QuarantineReason.PARTIAL_CANDLE,
}
_TIMEFRAME_PATTERN = re.compile(r"^([1-9][0-9]*)([smhdw])$")
_TIMEFRAME_SECONDS = {"s": 1, "m": 60, "h": 3600, "d": 86400, "w": 604800}


class FindingPolicy(str, Enum):
    """Whether a detected tolerance finding warns or rejects records."""

    WARNING = "warning"
    REJECT = "reject"


@dataclass(frozen=True)
class OhlcvValidationConfig:
    """Stable tolerance configuration for the offline OHLCV checks."""

    missing_bar_policy: FindingPolicy = FindingPolicy.WARNING
    partial_candle_policy: FindingPolicy = FindingPolicy.REJECT
    maximum_volume: Decimal | None = None

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "missing_bar_policy",
            FindingPolicy(self.missing_bar_policy),
        )
        object.__setattr__(
            self,
            "partial_candle_policy",
            FindingPolicy(self.partial_candle_policy),
        )
        if self.maximum_volume is not None:
            if not isinstance(self.maximum_volume, Decimal):
                raise TypeError("maximum_volume must be a Decimal or None")
            if not self.maximum_volume.is_finite() or self.maximum_volume < 0:
                raise ValueError("maximum_volume must be finite and non-negative")

    def as_mapping(self) -> Mapping[str, object]:
        return {
            "missing_bar_policy": self.missing_bar_policy.value,
            "partial_candle_policy": self.partial_candle_policy.value,
            "maximum_volume": self.maximum_volume,
        }


def _check_id(check_type: str) -> UUID:
    return uuid5(NAMESPACE_URL, f"secure-eval-wrapper:offline-ohlcv:{check_type}")


def _policy_severity(policy: FindingPolicy) -> ValidationSeverity:
    if policy is FindingPolicy.WARNING:
        return ValidationSeverity.WARNING
    return ValidationSeverity.ERROR


def default_ohlcv_checks(
    config: OhlcvValidationConfig | None = None,
) -> tuple[ValidationCheck, ...]:
    """Return the complete Phase 2C check set in canonical evaluation order."""

    config = OhlcvValidationConfig() if config is None else config
    definitions = (
        (
            MISSING_BARS,
            "Detect gaps larger than the normalized bar timeframe.",
            _policy_severity(config.missing_bar_policy),
            {"policy": config.missing_bar_policy.value},
        ),
        (
            DUPLICATED_TIMESTAMPS,
            "Reject repeated open timestamps within a symbol, exchange, and timeframe.",
            ValidationSeverity.ERROR,
            {},
        ),
        (
            NON_MONOTONIC_TIMESTAMPS,
            "Reject input sequences whose open timestamps move backward.",
            ValidationSeverity.ERROR,
            {},
        ),
        (
            INVALID_OHLC_RELATIONSHIP,
            "Reject non-finite, negative, or inconsistent OHLC prices.",
            ValidationSeverity.ERROR,
            {},
        ),
        (
            INVALID_VOLUME,
            "Reject non-finite, negative, or configured-impossible volume.",
            ValidationSeverity.ERROR,
            {"maximum_volume": config.maximum_volume},
        ),
        (
            PARTIAL_CANDLE,
            "Handle explicitly non-final candles according to policy.",
            _policy_severity(config.partial_candle_policy),
            {"policy": config.partial_candle_policy.value},
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


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _timeframe_delta(timeframe: str) -> timedelta:
    match = _TIMEFRAME_PATTERN.fullmatch(timeframe)
    if match is None:
        raise ValueError(
            f"unsupported OHLCV timeframe '{timeframe}'; expected forms such as 1m or 4h"
        )
    quantity = int(match.group(1))
    return timedelta(seconds=quantity * _TIMEFRAME_SECONDS[match.group(2)])


def _group_key(bar: NormalizedBar) -> tuple[str, str, str]:
    return bar.symbol, bar.exchange, bar.timeframe


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


def _finding_status(severity: ValidationSeverity, found: bool) -> ValidationCheckStatus:
    if not found:
        return ValidationCheckStatus.PASSED
    if severity in (ValidationSeverity.ERROR, ValidationSeverity.CRITICAL):
        return ValidationCheckStatus.FAILED
    return ValidationCheckStatus.WARNING


def _finding_details(
    *,
    check_type: str,
    details: Mapping[str, object],
) -> dict[str, object]:
    return {
        "check_type": check_type,
        "quarantine_reason": _CHECK_REASONS[check_type].value,
        **details,
    }


def _missing_bars(
    bars: Sequence[NormalizedBar],
) -> tuple[tuple[UUID, ...], Mapping[str, object], str]:
    groups: dict[tuple[str, str, str], list[NormalizedBar]] = defaultdict(list)
    for bar in bars:
        groups[_group_key(bar)].append(bar)

    gaps: list[Mapping[str, object]] = []
    adjacent: list[NormalizedBar] = []
    missing_count = 0
    for (symbol, exchange, timeframe), group in sorted(groups.items()):
        interval = _timeframe_delta(timeframe)
        unique_by_time: dict[datetime, NormalizedBar] = {}
        for bar in group:
            unique_by_time.setdefault(bar.bar_open_time_utc, bar)
        ordered = sorted(unique_by_time.values(), key=lambda item: item.bar_open_time_utc)
        interval_microseconds = interval // timedelta(microseconds=1)
        for previous, current in zip(ordered, ordered[1:]):
            elapsed = current.bar_open_time_utc - previous.bar_open_time_utc
            if elapsed <= interval:
                continue
            elapsed_microseconds = elapsed // timedelta(microseconds=1)
            gap_count = (elapsed_microseconds - 1) // interval_microseconds
            missing_count += gap_count
            adjacent.extend((previous, current))
            gaps.append(
                {
                    "symbol": symbol,
                    "exchange": exchange,
                    "timeframe": timeframe,
                    "previous_open_time_utc": previous.bar_open_time_utc,
                    "next_open_time_utc": current.bar_open_time_utc,
                    "missing_count": gap_count,
                }
            )
    affected = _observation_ids(adjacent)
    message = (
        "No missing OHLCV intervals detected."
        if not gaps
        else f"Detected {missing_count} missing OHLCV interval(s) across {len(gaps)} gap(s)."
    )
    return affected, {"missing_count": missing_count, "gaps": tuple(gaps)}, message


def _duplicated_timestamps(
    bars: Sequence[NormalizedBar],
) -> tuple[tuple[UUID, ...], Mapping[str, object], str]:
    groups: dict[tuple[str, str, str, datetime], list[NormalizedBar]] = defaultdict(list)
    for bar in bars:
        groups[(*_group_key(bar), bar.bar_open_time_utc)].append(bar)
    duplicates: list[Mapping[str, object]] = []
    affected_bars: list[NormalizedBar] = []
    for (symbol, exchange, timeframe, timestamp), group in sorted(groups.items()):
        if len(group) < 2:
            continue
        affected_bars.extend(group)
        duplicates.append(
            {
                "symbol": symbol,
                "exchange": exchange,
                "timeframe": timeframe,
                "bar_open_time_utc": timestamp,
                "record_count": len(group),
            }
        )
    affected = _observation_ids(affected_bars)
    message = (
        "No duplicated OHLCV timestamps detected."
        if not duplicates
        else f"Detected {len(duplicates)} duplicated OHLCV timestamp group(s)."
    )
    return affected, {"duplicate_groups": tuple(duplicates)}, message


def _non_monotonic_timestamps(
    bars: Sequence[NormalizedBar],
) -> tuple[tuple[UUID, ...], Mapping[str, object], str]:
    previous_by_group: dict[tuple[str, str, str], NormalizedBar] = {}
    violations: list[Mapping[str, object]] = []
    affected_bars: list[NormalizedBar] = []
    for position, bar in enumerate(bars):
        key = _group_key(bar)
        previous = previous_by_group.get(key)
        if previous is not None and bar.bar_open_time_utc < previous.bar_open_time_utc:
            affected_bars.extend((previous, bar))
            violations.append(
                {
                    "symbol": bar.symbol,
                    "exchange": bar.exchange,
                    "timeframe": bar.timeframe,
                    "previous_open_time_utc": previous.bar_open_time_utc,
                    "current_open_time_utc": bar.bar_open_time_utc,
                    "input_position": position,
                }
            )
        previous_by_group[key] = bar
    affected = _observation_ids(affected_bars)
    message = (
        "OHLCV timestamps are monotonic in provider order."
        if not violations
        else f"Detected {len(violations)} backward OHLCV timestamp transition(s)."
    )
    return affected, {"violations": tuple(violations)}, message


def _invalid_ohlc(
    bars: Sequence[NormalizedBar],
) -> tuple[tuple[UUID, ...], Mapping[str, object], str]:
    invalid: list[Mapping[str, object]] = []
    affected_bars: list[NormalizedBar] = []
    for bar in bars:
        prices = (bar.open, bar.high, bar.low, bar.close)
        reasons: list[str] = []
        if any(not price.is_finite() for price in prices):
            reasons.append("non_finite_price")
        elif any(price < 0 for price in prices):
            reasons.append("negative_price")
        else:
            if bar.high < max(bar.open, bar.close, bar.low):
                reasons.append("high_below_ohlc_value")
            if bar.low > min(bar.open, bar.close, bar.high):
                reasons.append("low_above_ohlc_value")
        if reasons:
            affected_bars.append(bar)
            invalid.append(
                {
                    "bar_open_time_utc": bar.bar_open_time_utc,
                    "symbol": bar.symbol,
                    "reasons": tuple(reasons),
                }
            )
    affected = _observation_ids(affected_bars)
    message = (
        "All OHLCV price relationships are valid."
        if not invalid
        else f"Detected {len(invalid)} bar(s) with invalid OHLC values."
    )
    return affected, {"invalid_bars": tuple(invalid)}, message


def _invalid_volume(
    bars: Sequence[NormalizedBar],
    maximum_volume: Decimal | None,
) -> tuple[tuple[UUID, ...], Mapping[str, object], str]:
    invalid: list[Mapping[str, object]] = []
    affected_bars: list[NormalizedBar] = []
    for bar in bars:
        reason: str | None = None
        if not bar.volume.is_finite():
            reason = "non_finite_volume"
        elif bar.volume < 0:
            reason = "negative_volume"
        elif maximum_volume is not None and bar.volume > maximum_volume:
            reason = "volume_above_configured_maximum"
        if reason is not None:
            affected_bars.append(bar)
            invalid.append(
                {
                    "bar_open_time_utc": bar.bar_open_time_utc,
                    "symbol": bar.symbol,
                    "volume": bar.volume,
                    "reason": reason,
                }
            )
    affected = _observation_ids(affected_bars)
    message = (
        "All OHLCV volumes are valid."
        if not invalid
        else f"Detected {len(invalid)} bar(s) with invalid volume."
    )
    return affected, {"invalid_bars": tuple(invalid)}, message


def _partial_candles(
    bars: Sequence[NormalizedBar],
) -> tuple[tuple[UUID, ...], Mapping[str, object], str]:
    partial = tuple(bar for bar in bars if bar.is_final is False)
    flagged_count = sum(bar.is_final is not None for bar in bars)
    affected = _observation_ids(partial)
    message = (
        "No explicitly partial OHLCV candles detected."
        if not partial
        else f"Detected {len(partial)} explicitly partial OHLCV candle(s)."
    )
    return affected, {
        "flagged_record_count": flagged_count,
        "partial_record_count": len(partial),
    }, message


class OfflineOhlcvValidator(DataValidator):
    """Run the Phase 2C checks without network access or persistence."""

    def __init__(
        self,
        *,
        config: OhlcvValidationConfig | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._config = OhlcvValidationConfig() if config is None else config
        self._clock = _utc_now if clock is None else clock

    def validate(
        self,
        *,
        validation_run_id: UUID,
        dataset_ref: str,
        records: Sequence[ValidationInput],
        checks: Sequence[ValidationCheck] = (),
    ) -> ValidationReport:
        """Validate normalized bars and return a fully hashed report."""

        bars: tuple[NormalizedBar, ...] = tuple(records)  # type: ignore[assignment]
        if any(not isinstance(record, NormalizedBar) for record in bars):
            raise TypeError("OfflineOhlcvValidator accepts NormalizedBar records only")
        for bar in bars:
            require_utc_datetime(
                bar.bar_open_time_utc,
                field_name="NormalizedBar bar_open_time_utc",
            )
            if bar.bar_close_time_utc is not None:
                require_utc_datetime(
                    bar.bar_close_time_utc,
                    field_name="NormalizedBar bar_close_time_utc",
                )
            if normalize_symbol(bar.symbol) != bar.symbol:
                raise ValueError("NormalizedBar symbol must use canonical BASE-QUOTE form")
            _timeframe_delta(bar.timeframe)
            if not bar.source_observation_ids:
                raise ValueError("NormalizedBar must preserve at least one source observation ID")

        selected_checks = tuple(checks) or default_ohlcv_checks(self._config)
        if len({check.check_type for check in selected_checks}) != len(selected_checks):
            raise ValueError("validation check types must be unique")
        unknown = set(check.check_type for check in selected_checks) - set(_CHECK_ORDER)
        if unknown:
            raise ValueError(f"unsupported OHLCV validation checks: {sorted(unknown)}")
        if any(MarketDataType.OHLCV not in check.data_types for check in selected_checks):
            raise ValueError("all OHLCV validation checks must declare the OHLCV data type")
        selected_checks = tuple(
            sorted(
                selected_checks,
                key=lambda item: (_CHECK_ORDER[item.check_type], str(item.check_id)),
            )
        )

        created_at_utc = require_utc_datetime(
            self._clock(),
            field_name="offline validator clock",
        )
        window_start = min((bar.bar_open_time_utc for bar in bars), default=None)
        window_end = max((bar.bar_open_time_utc for bar in bars), default=None)
        unique_symbols = sorted({bar.symbol for bar in bars})
        unique_timeframes = sorted({bar.timeframe for bar in bars})
        result_symbol = unique_symbols[0] if len(unique_symbols) == 1 else None
        result_timeframe = unique_timeframes[0] if len(unique_timeframes) == 1 else None

        validation_results: list[ValidationResult] = []
        for check in selected_checks:
            if check.check_type == MISSING_BARS:
                affected, details, message = _missing_bars(bars)
            elif check.check_type == DUPLICATED_TIMESTAMPS:
                affected, details, message = _duplicated_timestamps(bars)
            elif check.check_type == NON_MONOTONIC_TIMESTAMPS:
                affected, details, message = _non_monotonic_timestamps(bars)
            elif check.check_type == INVALID_OHLC_RELATIONSHIP:
                affected, details, message = _invalid_ohlc(bars)
            elif check.check_type == INVALID_VOLUME:
                affected, details, message = _invalid_volume(
                    bars,
                    self._config.maximum_volume,
                )
            else:
                affected, details, message = _partial_candles(bars)

            status = _finding_status(check.severity, bool(affected))
            if check.check_type == MISSING_BARS and details["missing_count"]:
                status = _finding_status(check.severity, True)
                if status is ValidationCheckStatus.FAILED:
                    affected = _observation_ids(bars)
            result_details = _finding_details(
                check_type=check.check_type,
                details=details,
            )
            validation_results.append(
                ValidationResult(
                    result_id=uuid5(
                        NAMESPACE_URL,
                        f"validation-result:{validation_run_id}:{dataset_ref}:{check.check_id}",
                    ),
                    validation_run_id=validation_run_id,
                    check_id=check.check_id,
                    status=status,
                    created_at_utc=created_at_utc,
                    message=message,
                    symbol=result_symbol,
                    timeframe=result_timeframe,
                    window_start_utc=window_start,
                    window_end_utc=window_end,
                    affected_observation_ids=affected,
                    details=result_details,
                )
            )

        provider_names: list[str] = []
        source_hashes: list[str] = []
        for bar in bars:
            provider_name = bar.provenance.get("provider_name")
            provider_names.append(
                provider_name if isinstance(provider_name, str) else bar.exchange
            )
            source_hash = bar.provenance.get("source_sha256")
            if isinstance(source_hash, str):
                source_hashes.append(source_hash)

        tolerance_payload = {
            "ohlcv_config": dict(self._config.as_mapping()),
            "checks": tuple(
                {
                    "check_id": check.check_id,
                    "check_type": check.check_type,
                    "severity": check.severity,
                    "parameters": dict(check.parameters),
                }
                for check in selected_checks
            ),
        }
        return build_validation_report(
            validation_run_id=validation_run_id,
            dataset_ref=dataset_ref,
            provider_names=provider_names,
            data_types=(MarketDataType.OHLCV,),
            symbols=unique_symbols,
            timeframes=unique_timeframes,
            window_start_utc=window_start,
            window_end_utc=window_end,
            results=validation_results,
            record_observation_ids=tuple(
                bar.source_observation_ids for bar in bars
            ),
            source_hashes=source_hashes,
            tolerance_config=tolerance_payload,
            created_at_utc=created_at_utc,
        )


def validate_ohlcv_bars(
    *,
    validation_run_id: UUID,
    dataset_ref: str,
    bars: Sequence[NormalizedBar],
    config: OhlcvValidationConfig | None = None,
    checks: Sequence[ValidationCheck] = (),
    clock: Callable[[], datetime] | None = None,
) -> ValidationReport:
    """Convenience entry point for one offline OHLCV validation run."""

    return OfflineOhlcvValidator(config=config, clock=clock).validate(
        validation_run_id=validation_run_id,
        dataset_ref=dataset_ref,
        records=bars,
        checks=checks,
    )
