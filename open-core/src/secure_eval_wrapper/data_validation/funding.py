"""Deterministic offline validation for public funding-rate history."""

from __future__ import annotations

import re
from collections import defaultdict
from collections.abc import Callable, Mapping, Sequence
from datetime import datetime, timedelta, timezone
from uuid import NAMESPACE_URL, UUID, uuid5

from secure_eval_wrapper.data_collection.models import FundingRate, InstrumentType, MarketDataType
from secure_eval_wrapper.data_collection.time_utils import require_utc_datetime
from secure_eval_wrapper.data_validation.models import (
    QuarantineReason,
    ValidationCheckStatus,
    ValidationResult,
)
from secure_eval_wrapper.data_validation.reporting import build_validation_report


DUPLICATE_FUNDING_TIMESTAMP = "duplicate_funding_timestamp"
NON_FINITE_FUNDING_RATE = "non_finite_funding_rate"
MALFORMED_MARK_PRICE = "malformed_mark_price"
INVALID_FUNDING_TIMESTAMP = "invalid_funding_timestamp"
AMBIGUOUS_FUNDING_INSTRUMENT = "ambiguous_funding_instrument"
OUT_OF_WINDOW_FUNDING = "out_of_window_funding"
NON_MONOTONIC_FUNDING_TIMESTAMP = "non_monotonic_funding_timestamp"
FUNDING_TIMESTAMP_GAP = "funding_timestamp_gap"
FUNDING_INTERVAL_MISMATCH = "funding_interval_mismatch"
FUNDING_PROVIDER_INSTRUMENT_MISMATCH = "funding_provider_instrument_mismatch"

_CHECKS = (
    DUPLICATE_FUNDING_TIMESTAMP,
    NON_FINITE_FUNDING_RATE,
    MALFORMED_MARK_PRICE,
    INVALID_FUNDING_TIMESTAMP,
    AMBIGUOUS_FUNDING_INSTRUMENT,
    OUT_OF_WINDOW_FUNDING,
    NON_MONOTONIC_FUNDING_TIMESTAMP,
    FUNDING_TIMESTAMP_GAP,
    FUNDING_INTERVAL_MISMATCH,
    FUNDING_PROVIDER_INSTRUMENT_MISMATCH,
)
_REASONS = {
    DUPLICATE_FUNDING_TIMESTAMP: QuarantineReason.DUPLICATE_RECORD,
    NON_FINITE_FUNDING_RATE: QuarantineReason.UNSUPPORTED_PAYLOAD,
    MALFORMED_MARK_PRICE: QuarantineReason.INVALID_PRICE,
    INVALID_FUNDING_TIMESTAMP: QuarantineReason.AMBIGUOUS_TIMESTAMP,
    AMBIGUOUS_FUNDING_INSTRUMENT: QuarantineReason.MISSING_REQUIRED_DATA,
    OUT_OF_WINDOW_FUNDING: QuarantineReason.STALE_DATA,
    NON_MONOTONIC_FUNDING_TIMESTAMP: QuarantineReason.NON_MONOTONIC_TIMESTAMP,
    FUNDING_TIMESTAMP_GAP: QuarantineReason.FUNDING_TIMESTAMP_GAP,
    FUNDING_INTERVAL_MISMATCH: QuarantineReason.FUNDING_TIMESTAMP_GAP,
    FUNDING_PROVIDER_INSTRUMENT_MISMATCH: QuarantineReason.PROVIDER_INSTRUMENT_MISMATCH,
}
_INTERVAL = re.compile(r"^([1-9][0-9]*)([hm])$")


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _ids(records: Sequence[FundingRate]) -> tuple[UUID, ...]:
    return tuple(sorted({
        observation_id
        for record in records
        for observation_id in record.source_observation_ids
    }, key=str))


def _interval(value: str | None) -> timedelta | None:
    if value is None:
        return None
    match = _INTERVAL.fullmatch(value)
    if match is None:
        return None
    quantity = int(match.group(1))
    return timedelta(hours=quantity) if match.group(2) == "h" else timedelta(minutes=quantity)


def _validation_result(
    *,
    check_type: str,
    validation_run_id: UUID,
    dataset_ref: str,
    created: datetime,
    affected: tuple[UUID, ...],
    findings: Sequence[Mapping[str, object]],
    start: datetime,
    end: datetime,
    symbol: str | None,
    warning: bool,
) -> ValidationResult:
    if findings:
        status = ValidationCheckStatus.WARNING if warning else ValidationCheckStatus.FAILED
    else:
        status = ValidationCheckStatus.PASSED
    return ValidationResult(
        result_id=uuid5(NAMESPACE_URL, f"funding-validation:{validation_run_id}:{dataset_ref}:{check_type}"),
        validation_run_id=validation_run_id,
        check_id=uuid5(NAMESPACE_URL, f"funding-check:{check_type}"),
        status=status,
        created_at_utc=created,
        message=(
            f"Detected {len(findings)} {check_type} finding(s)."
            if findings else f"No {check_type} findings detected."
        ),
        symbol=symbol,
        window_start_utc=start,
        window_end_utc=end,
        affected_observation_ids=affected,
        details={
            "check_type": check_type,
            "quarantine_reason": _REASONS[check_type].value,
            "finding_count": len(findings),
            "findings": tuple(findings),
        },
    )


def validate_funding_rates(
    *,
    validation_run_id: UUID,
    dataset_ref: str,
    funding_rates: Sequence[FundingRate],
    window_start_utc: datetime,
    window_end_utc: datetime,
    clock: Callable[[], datetime] | None = None,
):
    records = tuple(funding_rates)
    if any(not isinstance(record, FundingRate) for record in records):
        raise TypeError("funding_rates must contain only FundingRate records")
    start = require_utc_datetime(window_start_utc, field_name="funding window_start_utc")
    end = require_utc_datetime(window_end_utc, field_name="funding window_end_utc")
    if end <= start:
        raise ValueError("funding validation window must be non-empty")
    created = require_utc_datetime((clock or _utc_now)(), field_name="funding validator clock")
    symbols = sorted({record.symbol for record in records})
    symbol = symbols[0] if len(symbols) == 1 else None

    invalid_timestamps = []
    invalid_timestamp_findings = []
    for record in records:
        try:
            require_utc_datetime(record.funding_time_utc, field_name="funding timestamp")
        except (TypeError, ValueError) as exc:
            invalid_timestamps.append(record)
            invalid_timestamp_findings.append({"funding_rate_id": str(record.funding_rate_id), "reason": str(exc)})
    valid_timestamp_records = tuple(
        record for record in records if record not in invalid_timestamps
    )

    duplicate_groups: dict[tuple[object, datetime], list[FundingRate]] = defaultdict(list)
    for record in valid_timestamp_records:
        identity = record.instrument_key.identity_sha256 if record.instrument_key else record.symbol
        duplicate_groups[(identity, record.funding_time_utc)].append(record)
    duplicate_records = [
        item for group in duplicate_groups.values() if len(group) > 1 for item in group
    ]
    duplicate_findings = tuple(
        {"instrument": str(key[0]), "funding_time_utc": key[1], "count": len(group)}
        for key, group in sorted(duplicate_groups.items(), key=lambda item: str(item[0]))
        if len(group) > 1
    )
    bad_rates = [record for record in records if not record.rate.is_finite()]
    bad_marks = [
        record for record in records
        if record.mark_price is not None
        and (not record.mark_price.is_finite() or record.mark_price <= 0)
    ]
    ambiguous = []
    ambiguous_findings = []
    provider_mismatch = []
    provider_findings = []
    for record in records:
        key = record.instrument_key
        reasons = []
        if key is None:
            reasons.append("missing_instrument_key")
        else:
            if key.instrument_type is not InstrumentType.PERPETUAL_SWAP:
                reasons.append("instrument_not_perpetual_swap")
            if key.settlement_asset is None:
                reasons.append("missing_settlement_asset")
            if key.canonical_symbol != record.symbol or key.exchange_name != record.exchange:
                reasons.append("canonical_identity_mismatch")
        if reasons:
            ambiguous.append(record)
            ambiguous_findings.append({"funding_rate_id": str(record.funding_rate_id), "reasons": tuple(reasons)})
        if key is not None:
            provenance_id = record.provenance.get("provider_instrument_id")
            if record.provider_instrument_id != key.provider_instrument_id or provenance_id not in (None, key.provider_instrument_id):
                provider_mismatch.append(record)
                provider_findings.append({
                    "funding_rate_id": str(record.funding_rate_id),
                    "expected": key.provider_instrument_id,
                    "recorded": record.provider_instrument_id,
                    "provenance": provenance_id,
                })

    out_of_window = [
        record for record in valid_timestamp_records
        if not (start <= record.funding_time_utc < end)
    ]
    nonmonotonic = []
    nonmonotonic_findings = []
    previous = None
    for position, record in enumerate(valid_timestamp_records):
        if previous is not None and record.funding_time_utc < previous.funding_time_utc:
            nonmonotonic.extend((previous, record))
            nonmonotonic_findings.append({
                "position": position,
                "previous": previous.funding_time_utc,
                "current": record.funding_time_utc,
            })
        previous = record

    gap_records = []
    gap_findings = []
    mismatch_records = []
    mismatch_findings = []
    by_identity: dict[str, list[FundingRate]] = defaultdict(list)
    for record in valid_timestamp_records:
        identity = record.instrument_key.identity_sha256 if record.instrument_key else record.symbol
        by_identity[identity].append(record)
    for identity, group in sorted(by_identity.items()):
        ordered = sorted(group, key=lambda record: record.funding_time_utc)
        for left, right in zip(ordered, ordered[1:]):
            elapsed = right.funding_time_utc - left.funding_time_utc
            expected_left = _interval(left.funding_interval)
            expected_right = _interval(right.funding_interval)
            expected = expected_left or expected_right
            if expected is not None and elapsed > expected:
                gap_records.extend((left, right))
                gap_findings.append({
                    "instrument_identity_sha256": identity,
                    "previous": left.funding_time_utc,
                    "current": right.funding_time_utc,
                    "elapsed_seconds": int(elapsed.total_seconds()),
                    "expected_seconds": int(expected.total_seconds()),
                })
            if (
                expected_left is not None
                and expected_right is not None
                and expected_left != expected_right
            ) or (expected is not None and elapsed != expected):
                mismatch_records.extend((left, right))
                mismatch_findings.append({
                    "instrument_identity_sha256": identity,
                    "previous_interval": left.funding_interval,
                    "current_interval": right.funding_interval,
                    "elapsed_seconds": int(elapsed.total_seconds()),
                })

    specs = (
        (DUPLICATE_FUNDING_TIMESTAMP, _ids(duplicate_records), duplicate_findings, False),
        (NON_FINITE_FUNDING_RATE, _ids(bad_rates), tuple({"funding_rate_id": str(item.funding_rate_id), "rate": item.rate} for item in bad_rates), False),
        (MALFORMED_MARK_PRICE, _ids(bad_marks), tuple({"funding_rate_id": str(item.funding_rate_id), "mark_price": item.mark_price} for item in bad_marks), False),
        (INVALID_FUNDING_TIMESTAMP, _ids(invalid_timestamps), tuple(invalid_timestamp_findings), False),
        (AMBIGUOUS_FUNDING_INSTRUMENT, _ids(ambiguous), tuple(ambiguous_findings), False),
        (OUT_OF_WINDOW_FUNDING, _ids(out_of_window), tuple({"funding_rate_id": str(item.funding_rate_id), "funding_time_utc": item.funding_time_utc} for item in out_of_window), False),
        (NON_MONOTONIC_FUNDING_TIMESTAMP, _ids(nonmonotonic), tuple(nonmonotonic_findings), False),
        (FUNDING_TIMESTAMP_GAP, _ids(gap_records), tuple(gap_findings), True),
        (FUNDING_INTERVAL_MISMATCH, _ids(mismatch_records), tuple(mismatch_findings), True),
        (FUNDING_PROVIDER_INSTRUMENT_MISMATCH, _ids(provider_mismatch), tuple(provider_findings), False),
    )
    results = tuple(
        _validation_result(
            check_type=check_type,
            validation_run_id=validation_run_id,
            dataset_ref=dataset_ref,
            created=created,
            affected=affected,
            findings=findings,
            start=start,
            end=end,
            symbol=symbol,
            warning=warning,
        )
        for check_type, affected, findings, warning in specs
    )
    return build_validation_report(
        validation_run_id=validation_run_id,
        dataset_ref=dataset_ref,
        provider_names=tuple(str(record.provenance.get("provider_name", record.exchange)) for record in records),
        data_types=(MarketDataType.FUNDING_RATES,),
        symbols=symbols,
        timeframes=tuple(sorted({item.funding_interval for item in records if item.funding_interval})),
        window_start_utc=start,
        window_end_utc=end,
        results=results,
        record_observation_ids=tuple(record.source_observation_ids for record in records),
        source_hashes=tuple(
            value for record in records
            if isinstance((value := record.provenance.get("source_sha256")), str)
        ),
        tolerance_config={"checks": _CHECKS, "gap_policy": "warning", "interval_mismatch_policy": "warning"},
        created_at_utc=created,
    )


__all__ = [
    "AMBIGUOUS_FUNDING_INSTRUMENT",
    "DUPLICATE_FUNDING_TIMESTAMP",
    "FUNDING_INTERVAL_MISMATCH",
    "FUNDING_PROVIDER_INSTRUMENT_MISMATCH",
    "FUNDING_TIMESTAMP_GAP",
    "INVALID_FUNDING_TIMESTAMP",
    "MALFORMED_MARK_PRICE",
    "NON_FINITE_FUNDING_RATE",
    "NON_MONOTONIC_FUNDING_TIMESTAMP",
    "OUT_OF_WINDOW_FUNDING",
    "validate_funding_rates",
]
