"""Deterministic offline validation for normalized public trades."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable, Mapping, Sequence
from datetime import datetime, timezone
from uuid import NAMESPACE_URL, UUID, uuid5

from secure_eval_wrapper.data_collection.models import MarketDataType, NormalizedTrade, TradeSide
from secure_eval_wrapper.data_collection.time_utils import require_utc_datetime
from secure_eval_wrapper.data_validation.models import (
    QuarantineReason,
    ValidationCheckStatus,
    ValidationResult,
    ValidationSeverity,
)
from secure_eval_wrapper.data_validation.reporting import build_validation_report


DUPLICATE_PROVIDER_TRADE_ID = "duplicate_provider_trade_id"
DUPLICATE_NORMALIZED_TRADE_ID = "duplicate_normalized_trade_id"
INVALID_TRADE_PRICE = "invalid_trade_price"
INVALID_TRADE_QUANTITY = "invalid_trade_quantity"
INVALID_TRADE_TIMESTAMP = "invalid_trade_timestamp"
OUT_OF_WINDOW_TRADE = "out_of_window_trade"
INCONSISTENT_TRADE_INSTRUMENT = "inconsistent_trade_instrument"
IMPOSSIBLE_TRADE_SIDE = "impossible_trade_side"
NON_MONOTONIC_TRADE_SEQUENCE = "non_monotonic_trade_sequence"
MALFORMED_AGGREGATE_TRADE_RANGE = "malformed_aggregate_trade_range"

_CHECKS = (
    DUPLICATE_PROVIDER_TRADE_ID,
    DUPLICATE_NORMALIZED_TRADE_ID,
    INVALID_TRADE_PRICE,
    INVALID_TRADE_QUANTITY,
    INVALID_TRADE_TIMESTAMP,
    OUT_OF_WINDOW_TRADE,
    INCONSISTENT_TRADE_INSTRUMENT,
    IMPOSSIBLE_TRADE_SIDE,
    NON_MONOTONIC_TRADE_SEQUENCE,
    MALFORMED_AGGREGATE_TRADE_RANGE,
)
_REASONS = {
    DUPLICATE_PROVIDER_TRADE_ID: QuarantineReason.DUPLICATE_RECORD,
    DUPLICATE_NORMALIZED_TRADE_ID: QuarantineReason.DUPLICATE_RECORD,
    INVALID_TRADE_PRICE: QuarantineReason.INVALID_PRICE,
    INVALID_TRADE_QUANTITY: QuarantineReason.INVALID_QUANTITY,
    INVALID_TRADE_TIMESTAMP: QuarantineReason.AMBIGUOUS_TIMESTAMP,
    OUT_OF_WINDOW_TRADE: QuarantineReason.STALE_DATA,
    INCONSISTENT_TRADE_INSTRUMENT: QuarantineReason.SYMBOL_MAPPING_INCONSISTENCY,
    IMPOSSIBLE_TRADE_SIDE: QuarantineReason.INVALID_SIDE,
    NON_MONOTONIC_TRADE_SEQUENCE: QuarantineReason.NON_MONOTONIC_TIMESTAMP,
    MALFORMED_AGGREGATE_TRADE_RANGE: QuarantineReason.UNSUPPORTED_PAYLOAD,
}


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _ids(records: Sequence[NormalizedTrade]) -> tuple[UUID, ...]:
    return tuple(sorted({
        observation_id
        for record in records
        for observation_id in record.source_observation_ids
    }, key=str))


def _duplicate_groups(
    records: Sequence[NormalizedTrade],
    key: Callable[[NormalizedTrade], object],
) -> tuple[tuple[UUID, ...], tuple[Mapping[str, object], ...]]:
    groups: dict[object, list[NormalizedTrade]] = defaultdict(list)
    for record in records:
        groups[key(record)].append(record)
    findings = []
    affected: list[NormalizedTrade] = []
    for value, group in sorted(groups.items(), key=lambda item: str(item[0])):
        if value is None or len(group) < 2:
            continue
        affected.extend(group)
        findings.append({"identity": str(value), "count": len(group)})
    return _ids(affected), tuple(findings)


def _result(
    *,
    check_type: str,
    validation_run_id: UUID,
    dataset_ref: str,
    created_at_utc: datetime,
    affected: tuple[UUID, ...],
    findings: Sequence[Mapping[str, object]],
    window_start_utc: datetime,
    window_end_utc: datetime,
    symbol: str | None,
    warning: bool = False,
) -> ValidationResult:
    found = bool(findings)
    status = (
        ValidationCheckStatus.WARNING
        if found and warning
        else ValidationCheckStatus.FAILED
        if found
        else ValidationCheckStatus.PASSED
    )
    return ValidationResult(
        result_id=uuid5(NAMESPACE_URL, f"trade-validation:{validation_run_id}:{dataset_ref}:{check_type}"),
        validation_run_id=validation_run_id,
        check_id=uuid5(NAMESPACE_URL, f"trade-check:{check_type}"),
        status=status,
        created_at_utc=created_at_utc,
        message=(
            f"Detected {len(findings)} {check_type} finding(s)."
            if findings else f"No {check_type} findings detected."
        ),
        symbol=symbol,
        window_start_utc=window_start_utc,
        window_end_utc=window_end_utc,
        affected_observation_ids=affected,
        details={
            "check_type": check_type,
            "quarantine_reason": _REASONS[check_type].value,
            "finding_count": len(findings),
            "findings": tuple(findings),
        },
    )


def validate_trades(
    *,
    validation_run_id: UUID,
    dataset_ref: str,
    trades: Sequence[NormalizedTrade],
    window_start_utc: datetime,
    window_end_utc: datetime,
    clock: Callable[[], datetime] | None = None,
):
    """Validate one provider's normalized trades without I/O."""

    records = tuple(trades)
    if any(not isinstance(record, NormalizedTrade) for record in records):
        raise TypeError("trades must contain only NormalizedTrade records")
    start = require_utc_datetime(window_start_utc, field_name="trade window_start_utc")
    end = require_utc_datetime(window_end_utc, field_name="trade window_end_utc")
    if end <= start:
        raise ValueError("trade validation window must be half-open and non-empty")
    created = require_utc_datetime((clock or _utc_now)(), field_name="trade validator clock")
    symbols = sorted({record.symbol for record in records})
    symbol = symbols[0] if len(symbols) == 1 else None

    provider_ids, provider_findings = _duplicate_groups(records, lambda item: item.provider_trade_id)
    normalized_ids, normalized_findings = _duplicate_groups(records, lambda item: item.trade_id)

    price_invalid = [
        record for record in records if not record.price.is_finite() or record.price <= 0
    ]
    quantity_invalid = [
        record for record in records if not record.quantity.is_finite() or record.quantity <= 0
    ]
    invalid_timestamps: list[NormalizedTrade] = []
    timestamp_findings = []
    for record in records:
        try:
            require_utc_datetime(record.traded_at_utc, field_name="trade timestamp")
        except (TypeError, ValueError) as exc:
            invalid_timestamps.append(record)
            timestamp_findings.append({"trade_id": str(record.trade_id), "reason": str(exc)})
    out_of_window = [
        record for record in records
        if record not in invalid_timestamps
        and not (start <= record.traded_at_utc < end)
    ]
    inconsistent = []
    inconsistent_findings = []
    for record in records:
        key = record.instrument_key
        reasons = []
        if key is None:
            reasons.append("missing_instrument_key")
        else:
            if key.canonical_symbol != record.symbol:
                reasons.append("canonical_symbol_mismatch")
            if key.exchange_name != record.exchange:
                reasons.append("exchange_mismatch")
            if key.instrument_type.value != "spot":
                reasons.append("non_spot_trade")
            provider_id = record.provenance.get("provider_instrument_id")
            if provider_id not in (None, key.provider_instrument_id):
                reasons.append("provider_instrument_mismatch")
        if reasons:
            inconsistent.append(record)
            inconsistent_findings.append({"trade_id": str(record.trade_id), "reasons": tuple(reasons)})
    bad_sides = [record for record in records if record.side not in tuple(TradeSide)]
    sequence_bad: list[NormalizedTrade] = []
    sequence_findings = []
    previous = None
    for position, record in enumerate(records):
        if record.provider_sequence is None:
            continue
        if previous is not None and record.provider_sequence < previous.provider_sequence:
            sequence_bad.extend((previous, record))
            sequence_findings.append({
                "position": position,
                "previous_sequence": previous.provider_sequence,
                "current_sequence": record.provider_sequence,
            })
        previous = record
    malformed: list[NormalizedTrade] = []
    malformed_findings = []
    for record in records:
        first = record.first_provider_trade_id
        last = record.last_provider_trade_id
        if first is None and last is None:
            continue
        reasons = []
        if first is None or last is None or not first.isdigit() or not last.isdigit():
            reasons.append("non_numeric_or_incomplete_range")
        elif int(first) > int(last):
            reasons.append("first_trade_id_after_last_trade_id")
        if reasons:
            malformed.append(record)
            malformed_findings.append({"trade_id": str(record.trade_id), "reasons": tuple(reasons)})

    result_specs = (
        (DUPLICATE_PROVIDER_TRADE_ID, provider_ids, provider_findings),
        (DUPLICATE_NORMALIZED_TRADE_ID, normalized_ids, normalized_findings),
        (INVALID_TRADE_PRICE, _ids(price_invalid), tuple({"trade_id": str(item.trade_id), "price": item.price} for item in price_invalid)),
        (INVALID_TRADE_QUANTITY, _ids(quantity_invalid), tuple({"trade_id": str(item.trade_id), "quantity": item.quantity} for item in quantity_invalid)),
        (INVALID_TRADE_TIMESTAMP, _ids(invalid_timestamps), tuple(timestamp_findings)),
        (OUT_OF_WINDOW_TRADE, _ids(out_of_window), tuple({"trade_id": str(item.trade_id), "traded_at_utc": item.traded_at_utc} for item in out_of_window)),
        (INCONSISTENT_TRADE_INSTRUMENT, _ids(inconsistent), tuple(inconsistent_findings)),
        (IMPOSSIBLE_TRADE_SIDE, _ids(bad_sides), tuple({"trade_id": str(item.trade_id), "side": str(item.side)} for item in bad_sides)),
        (NON_MONOTONIC_TRADE_SEQUENCE, _ids(sequence_bad), tuple(sequence_findings)),
        (MALFORMED_AGGREGATE_TRADE_RANGE, _ids(malformed), tuple(malformed_findings)),
    )
    results = tuple(
        _result(
            check_type=check_type,
            validation_run_id=validation_run_id,
            dataset_ref=dataset_ref,
            created_at_utc=created,
            affected=affected,
            findings=findings,
            window_start_utc=start,
            window_end_utc=end,
            symbol=symbol,
        )
        for check_type, affected, findings in result_specs
    )
    return build_validation_report(
        validation_run_id=validation_run_id,
        dataset_ref=dataset_ref,
        provider_names=tuple(
            str(record.provenance.get("provider_name", record.exchange)) for record in records
        ),
        data_types=(MarketDataType.TRADES,),
        symbols=symbols,
        timeframes=(),
        window_start_utc=start,
        window_end_utc=end,
        results=results,
        record_observation_ids=tuple(record.source_observation_ids for record in records),
        source_hashes=tuple(
            value for record in records
            if isinstance((value := record.provenance.get("source_sha256")), str)
        ),
        tolerance_config={"checks": _CHECKS, "window_semantics": "[start,end)"},
        created_at_utc=created,
    )


__all__ = [
    "DUPLICATE_NORMALIZED_TRADE_ID",
    "DUPLICATE_PROVIDER_TRADE_ID",
    "INCONSISTENT_TRADE_INSTRUMENT",
    "INVALID_TRADE_PRICE",
    "INVALID_TRADE_QUANTITY",
    "INVALID_TRADE_TIMESTAMP",
    "MALFORMED_AGGREGATE_TRADE_RANGE",
    "NON_MONOTONIC_TRADE_SEQUENCE",
    "OUT_OF_WINDOW_TRADE",
    "validate_trades",
]
