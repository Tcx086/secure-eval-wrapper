"""Deterministic validation and drift detection for public instrument metadata."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable, Mapping, Sequence
from datetime import datetime, timezone
from uuid import NAMESPACE_URL, UUID, uuid5

from secure_eval_wrapper.data_collection.models import (
    InstrumentMetadata,
    InstrumentStatus,
    InstrumentType,
    MarketDataType,
)
from secure_eval_wrapper.data_collection.time_utils import require_utc_datetime
from secure_eval_wrapper.data_validation.models import (
    QuarantineReason,
    ValidationCheckStatus,
    ValidationResult,
)
from secure_eval_wrapper.data_validation.reporting import build_validation_report


MISSING_PROVIDER_INSTRUMENT_ID = "missing_provider_instrument_id"
AMBIGUOUS_CANONICAL_INSTRUMENT = "ambiguous_canonical_instrument"
MISSING_BASE_QUOTE = "missing_base_quote"
INVALID_TICK_SIZE = "invalid_tick_size"
INVALID_QUANTITY_STEP = "invalid_quantity_step"
INVALID_CONTRACT_MULTIPLIER = "invalid_contract_multiplier"
INVALID_INSTRUMENT_STATUS = "invalid_instrument_status"
DERIVATIVE_MISSING_SETTLEMENT = "derivative_missing_settlement"
DUPLICATE_INSTRUMENT_IDENTITY = "duplicate_instrument_identity"
INCONSISTENT_PROVIDER_SYMBOL_MAPPING = "inconsistent_provider_symbol_mapping"
INSTRUMENT_METADATA_DRIFT = "instrument_metadata_drift"

_CHECKS = (
    MISSING_PROVIDER_INSTRUMENT_ID,
    AMBIGUOUS_CANONICAL_INSTRUMENT,
    MISSING_BASE_QUOTE,
    INVALID_TICK_SIZE,
    INVALID_QUANTITY_STEP,
    INVALID_CONTRACT_MULTIPLIER,
    INVALID_INSTRUMENT_STATUS,
    DERIVATIVE_MISSING_SETTLEMENT,
    DUPLICATE_INSTRUMENT_IDENTITY,
    INCONSISTENT_PROVIDER_SYMBOL_MAPPING,
    INSTRUMENT_METADATA_DRIFT,
)
_REASONS = {
    MISSING_PROVIDER_INSTRUMENT_ID: QuarantineReason.MISSING_REQUIRED_DATA,
    AMBIGUOUS_CANONICAL_INSTRUMENT: QuarantineReason.SYMBOL_MAPPING_INCONSISTENCY,
    MISSING_BASE_QUOTE: QuarantineReason.MISSING_REQUIRED_DATA,
    INVALID_TICK_SIZE: QuarantineReason.INVALID_INSTRUMENT_METADATA,
    INVALID_QUANTITY_STEP: QuarantineReason.INVALID_INSTRUMENT_METADATA,
    INVALID_CONTRACT_MULTIPLIER: QuarantineReason.INVALID_INSTRUMENT_METADATA,
    INVALID_INSTRUMENT_STATUS: QuarantineReason.INVALID_INSTRUMENT_METADATA,
    DERIVATIVE_MISSING_SETTLEMENT: QuarantineReason.MISSING_REQUIRED_DATA,
    DUPLICATE_INSTRUMENT_IDENTITY: QuarantineReason.DUPLICATE_RECORD,
    INCONSISTENT_PROVIDER_SYMBOL_MAPPING: QuarantineReason.SYMBOL_MAPPING_INCONSISTENCY,
    INSTRUMENT_METADATA_DRIFT: QuarantineReason.INSTRUMENT_METADATA_DRIFT,
}
_DRIFT_FIELDS = (
    "symbol",
    "base_asset",
    "quote_asset",
    "settlement_asset",
    "instrument_type",
    "status",
    "tick_size",
    "quantity_step",
    "minimum_quantity",
    "minimum_notional",
    "contract_value",
    "contract_multiplier",
    "margin_asset",
    "margin_type",
    "listing_at_utc",
    "expiry_at_utc",
    "funding_interval",
)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _identity(record: InstrumentMetadata) -> str | None:
    return record.instrument_key.identity_sha256 if record.instrument_key is not None else None


def _ids(records: Sequence[InstrumentMetadata]) -> tuple[UUID, ...]:
    return tuple(sorted({
        observation_id
        for record in records
        for observation_id in record.source_observation_ids
    }, key=str))


def compare_instrument_metadata(
    old: InstrumentMetadata,
    new: InstrumentMetadata,
) -> Mapping[str, Mapping[str, object]]:
    """Return structured old/new fields without mutating either snapshot."""

    if not isinstance(old, InstrumentMetadata) or not isinstance(new, InstrumentMetadata):
        raise TypeError("metadata drift comparison requires InstrumentMetadata values")
    if _identity(old) != _identity(new):
        raise ValueError("metadata drift comparison requires the same instrument identity")
    changes = {}
    for field_name in _DRIFT_FIELDS:
        previous = getattr(old, field_name)
        current = getattr(new, field_name)
        if previous != current:
            changes[field_name] = {"old": previous, "new": current}
    if old.metadata_sha256 != new.metadata_sha256 and not changes:
        changes["metadata"] = {
            "old": dict(old.metadata),
            "new": dict(new.metadata),
        }
    return changes


def _result(
    *,
    check_type: str,
    validation_run_id: UUID,
    dataset_ref: str,
    created: datetime,
    affected: tuple[UUID, ...],
    findings: Sequence[Mapping[str, object]],
    symbol: str | None,
    warning: bool = False,
) -> ValidationResult:
    status = (
        ValidationCheckStatus.WARNING
        if findings and warning
        else ValidationCheckStatus.FAILED
        if findings
        else ValidationCheckStatus.PASSED
    )
    return ValidationResult(
        result_id=uuid5(NAMESPACE_URL, f"instrument-validation:{validation_run_id}:{dataset_ref}:{check_type}"),
        validation_run_id=validation_run_id,
        check_id=uuid5(NAMESPACE_URL, f"instrument-check:{check_type}"),
        status=status,
        created_at_utc=created,
        message=(
            f"Detected {len(findings)} {check_type} finding(s)."
            if findings else f"No {check_type} findings detected."
        ),
        symbol=symbol,
        affected_observation_ids=affected,
        details={
            "check_type": check_type,
            "quarantine_reason": _REASONS[check_type].value,
            "finding_count": len(findings),
            "findings": tuple(findings),
        },
    )


def validate_instruments(
    *,
    validation_run_id: UUID,
    dataset_ref: str,
    instruments: Sequence[InstrumentMetadata],
    previous_instruments: Sequence[InstrumentMetadata] = (),
    clock: Callable[[], datetime] | None = None,
):
    records = tuple(instruments)
    previous = tuple(previous_instruments)
    if any(not isinstance(record, InstrumentMetadata) for record in (*records, *previous)):
        raise TypeError("instrument validation requires InstrumentMetadata records")
    created = require_utc_datetime((clock or _utc_now)(), field_name="instrument validator clock")
    symbols = sorted({record.symbol for record in records})
    symbol = symbols[0] if len(symbols) == 1 else None

    missing_ids = [
        record for record in records
        if record.instrument_key is None
        or not record.instrument_key.provider_instrument_id.strip()
    ]
    ambiguous = []
    ambiguous_findings = []
    missing_assets = []
    derivative_missing = []
    mapping_groups: dict[tuple[str, str], set[tuple[str, str, str]]] = defaultdict(set)
    identity_groups: dict[str | None, list[InstrumentMetadata]] = defaultdict(list)
    for record in records:
        key = record.instrument_key
        identity_groups[_identity(record)].append(record)
        reasons = []
        if key is None:
            reasons.append("missing_instrument_key")
        else:
            if key.canonical_symbol != record.symbol:
                reasons.append("canonical_symbol_mismatch")
            if key.exchange_name != record.exchange:
                reasons.append("exchange_mismatch")
            if key.base_asset != record.base_asset or key.quote_asset != record.quote_asset:
                reasons.append("asset_mismatch")
            mapping_groups[(key.provider_name, key.provider_instrument_id)].add(
                (record.symbol, record.instrument_type.value, record.settlement_asset or "")
            )
        if reasons:
            ambiguous.append(record)
            ambiguous_findings.append({"instrument_id": str(record.instrument_id), "reasons": tuple(reasons)})
        if not record.base_asset.strip() or not record.quote_asset.strip():
            missing_assets.append(record)
        if record.instrument_type in (
            InstrumentType.PERPETUAL_SWAP,
            InstrumentType.DATED_FUTURE,
        ) and not record.settlement_asset:
            derivative_missing.append(record)

    duplicates = [
        record
        for identity, group in identity_groups.items()
        if identity is not None and len(group) > 1
        for record in group
    ]
    duplicate_findings = tuple(
        {"instrument_identity_sha256": identity, "count": len(group)}
        for identity, group in sorted(identity_groups.items(), key=lambda item: str(item[0]))
        if identity is not None and len(group) > 1
    )
    inconsistent = []
    inconsistent_findings = []
    by_provider_id = {
        (record.instrument_key.provider_name, record.instrument_key.provider_instrument_id): record
        for record in records if record.instrument_key is not None
    }
    for provider_identity, mappings in sorted(mapping_groups.items()):
        if len(mappings) > 1:
            record = by_provider_id[provider_identity]
            inconsistent.append(record)
            inconsistent_findings.append({
                "provider_name": provider_identity[0],
                "provider_instrument_id": provider_identity[1],
                "canonical_mappings": tuple(sorted(mappings)),
            })

    invalid_tick = [
        record for record in records
        if record.tick_size is not None
        and (not record.tick_size.is_finite() or record.tick_size <= 0)
    ]
    invalid_step = [
        record for record in records
        if record.quantity_step is not None
        and (not record.quantity_step.is_finite() or record.quantity_step <= 0)
    ]
    invalid_multiplier = [
        record for record in records
        if record.contract_multiplier is not None
        and (not record.contract_multiplier.is_finite() or record.contract_multiplier <= 0)
    ]
    invalid_status = [
        record for record in records
        if record.status not in (
            InstrumentStatus.ACTIVE,
            InstrumentStatus.INACTIVE,
            InstrumentStatus.DELISTED,
        )
    ]

    previous_by_identity = {
        identity: record
        for record in previous
        if (identity := _identity(record)) is not None
    }
    drift_records = []
    drift_findings = []
    for record in records:
        identity = _identity(record)
        old = previous_by_identity.get(identity)
        if old is None:
            continue
        changes = compare_instrument_metadata(old, record)
        if changes:
            drift_records.append(record)
            drift_findings.append({
                "instrument_identity_sha256": identity,
                "provider_instrument_id": record.instrument_key.provider_instrument_id if record.instrument_key else None,
                "changes": dict(changes),
                "old_instrument_id": str(old.instrument_id),
                "new_instrument_id": str(record.instrument_id),
            })

    specs = (
        (MISSING_PROVIDER_INSTRUMENT_ID, _ids(missing_ids), tuple({"instrument_id": str(item.instrument_id)} for item in missing_ids), False),
        (AMBIGUOUS_CANONICAL_INSTRUMENT, _ids(ambiguous), tuple(ambiguous_findings), False),
        (MISSING_BASE_QUOTE, _ids(missing_assets), tuple({"instrument_id": str(item.instrument_id)} for item in missing_assets), False),
        (INVALID_TICK_SIZE, _ids(invalid_tick), tuple({"instrument_id": str(item.instrument_id), "tick_size": item.tick_size} for item in invalid_tick), False),
        (INVALID_QUANTITY_STEP, _ids(invalid_step), tuple({"instrument_id": str(item.instrument_id), "quantity_step": item.quantity_step} for item in invalid_step), False),
        (INVALID_CONTRACT_MULTIPLIER, _ids(invalid_multiplier), tuple({"instrument_id": str(item.instrument_id), "contract_multiplier": item.contract_multiplier} for item in invalid_multiplier), False),
        (INVALID_INSTRUMENT_STATUS, _ids(invalid_status), tuple({"instrument_id": str(item.instrument_id), "status": item.status.value} for item in invalid_status), False),
        (DERIVATIVE_MISSING_SETTLEMENT, _ids(derivative_missing), tuple({"instrument_id": str(item.instrument_id)} for item in derivative_missing), False),
        (DUPLICATE_INSTRUMENT_IDENTITY, _ids(duplicates), duplicate_findings, False),
        (INCONSISTENT_PROVIDER_SYMBOL_MAPPING, _ids(inconsistent), tuple(inconsistent_findings), False),
        (INSTRUMENT_METADATA_DRIFT, _ids(drift_records), tuple(drift_findings), True),
    )
    results = tuple(
        _result(
            check_type=check_type,
            validation_run_id=validation_run_id,
            dataset_ref=dataset_ref,
            created=created,
            affected=affected,
            findings=findings,
            symbol=symbol,
            warning=warning,
        )
        for check_type, affected, findings, warning in specs
    )
    times = [
        value
        for record in records
        for value in (record.first_seen_at_utc, record.last_seen_at_utc)
        if value is not None
    ]
    for value in times:
        require_utc_datetime(value, field_name="instrument snapshot timestamp")
    return build_validation_report(
        validation_run_id=validation_run_id,
        dataset_ref=dataset_ref,
        provider_names=tuple(
            record.instrument_key.provider_name if record.instrument_key else record.exchange
            for record in records
        ),
        data_types=(MarketDataType.INSTRUMENTS,),
        symbols=symbols,
        timeframes=(),
        window_start_utc=min(times) if times else None,
        window_end_utc=max(times) if times else None,
        results=results,
        record_observation_ids=tuple(record.source_observation_ids for record in records),
        source_hashes=tuple(
            value for record in records
            if isinstance((value := record.metadata.get("provenance", {}).get("source_sha256") if isinstance(record.metadata.get("provenance"), Mapping) else None), str)
        ),
        tolerance_config={"checks": _CHECKS, "metadata_drift_policy": "warning_and_version"},
        created_at_utc=created,
    )


__all__ = [
    "AMBIGUOUS_CANONICAL_INSTRUMENT",
    "DERIVATIVE_MISSING_SETTLEMENT",
    "DUPLICATE_INSTRUMENT_IDENTITY",
    "INCONSISTENT_PROVIDER_SYMBOL_MAPPING",
    "INSTRUMENT_METADATA_DRIFT",
    "INVALID_CONTRACT_MULTIPLIER",
    "INVALID_INSTRUMENT_STATUS",
    "INVALID_QUANTITY_STEP",
    "INVALID_TICK_SIZE",
    "MISSING_BASE_QUOTE",
    "MISSING_PROVIDER_INSTRUMENT_ID",
    "compare_instrument_metadata",
    "validate_instruments",
]
