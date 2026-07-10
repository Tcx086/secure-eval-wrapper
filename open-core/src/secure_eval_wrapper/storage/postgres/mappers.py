"""Domain-to-row mappings for the offline Phase 2D persistence path.

The mapping layer is deliberately free of database imports and I/O.  It keeps domain objects
usable by in-memory tests while the PostgreSQL repositories serialize JSONB values at the DB-API
boundary.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from datetime import datetime
from decimal import Decimal
from enum import Enum
from uuid import NAMESPACE_URL, UUID, uuid5

from secure_eval_wrapper.data_collection.hashing import sha256_payload
from secure_eval_wrapper.data_collection.models import (
    FundingRate,
    InstrumentKey,
    InstrumentMetadata,
    InstrumentStatus,
    InstrumentType,
    NormalizedBar,
    NormalizedTrade,
    RawObservation,
)
from secure_eval_wrapper.data_validation.gating import accepted_ohlcv_bars
from secure_eval_wrapper.data_validation.models import (
    QuarantineReason,
    ValidationCheckStatus,
    ValidationReport,
    ValidationResult,
    ValidationStatus,
)
from secure_eval_wrapper.data_validation.quarantine import map_quarantine_reasons


def raw_observation_to_row(observation: RawObservation) -> dict[str, object]:
    """Map one raw observation to ``market_data.raw_source_observations`` columns."""

    if not isinstance(observation, RawObservation):
        raise TypeError("observation must be a RawObservation")
    return {
        "observation_id": observation.observation_id,
        "source_provider": observation.provider_name,
        "source_exchange": observation.exchange_name,
        "source_endpoint": observation.source_endpoint,
        "symbol_raw": observation.raw_symbol,
        "symbol_normalized": observation.normalized_symbol,
        "timeframe": observation.timeframe,
        "observed_at_utc": observation.observed_at_utc,
        "ingested_at_utc": observation.ingested_at_utc,
        "payload_jsonb": observation.payload,
        "source_sha256": observation.source_sha256,
        "collection_run_id": observation.collection_run_id,
        "data_type": observation.data_type.value,
        "provider_instrument_id": (
            observation.instrument_key.provider_instrument_id
            if observation.instrument_key is not None
            else observation.raw_symbol
        ),
        "instrument_type": (
            observation.instrument_key.instrument_type.value
            if observation.instrument_key is not None
            else None
        ),
    }


def normalized_bar_to_row(
    bar: NormalizedBar,
    *,
    validation_report_id: UUID,
    validation_status: ValidationStatus = ValidationStatus.ACCEPTED,
) -> dict[str, object]:
    """Map an accepted normalized bar to ``market_data.validated_bars`` columns."""

    if not isinstance(bar, NormalizedBar):
        raise TypeError("bar must be a NormalizedBar")
    if validation_status not in (
        ValidationStatus.ACCEPTED,
        ValidationStatus.ACCEPTED_WITH_WARNINGS,
    ):
        raise ValueError("validated bars require an accepted validation status")
    provenance = dict(bar.provenance)
    provenance.setdefault("bar_close_time_utc", bar.bar_close_time_utc)
    provenance.setdefault("is_final", bar.is_final)
    return {
        "bar_id": bar.bar_id,
        "symbol": bar.symbol,
        "exchange": bar.exchange,
        "timeframe": bar.timeframe,
        "bar_open_time_utc": bar.bar_open_time_utc,
        "open": bar.open,
        "high": bar.high,
        "low": bar.low,
        "close": bar.close,
        "volume": bar.volume,
        "validation_status": validation_status.value,
        "validation_report_id": validation_report_id,
        "source_observation_ids": list(bar.source_observation_ids),
        "provenance_jsonb": provenance,
    }


def _result_severity(result: ValidationResult) -> str:
    if result.status is ValidationCheckStatus.FAILED:
        return "error"
    if result.status is ValidationCheckStatus.WARNING:
        return "warning"
    return "info"


def validation_result_to_row(result: ValidationResult) -> dict[str, object]:
    """Map a validation result to one ``data_quality.data_quality_checks`` row.

    ``result_id`` is the storage primary key.  The declared ``check_id`` is retained in JSONB so
    multiple results for the same check definition can coexist across validation runs.
    """

    if not isinstance(result, ValidationResult):
        raise TypeError("result must be a ValidationResult")
    details = dict(result.details)
    details.update(
        {
            "declared_check_id": result.check_id,
            "message": result.message,
            "affected_observation_ids": list(result.affected_observation_ids),
        }
    )
    return {
        "check_id": result.result_id,
        "validation_run_id": result.validation_run_id,
        "check_type": str(result.details.get("check_type", result.check_id)),
        "severity": _result_severity(result),
        "symbol": result.symbol,
        "timeframe": result.timeframe,
        "window_start_utc": result.window_start_utc,
        "window_end_utc": result.window_end_utc,
        "status": result.status.value,
        "details_jsonb": details,
        "created_at_utc": result.created_at_utc,
    }


def validation_report_to_row(report: ValidationReport) -> dict[str, object]:
    """Map a validation report to ``data_quality.validation_reports`` columns."""

    if not isinstance(report, ValidationReport):
        raise TypeError("report must be a ValidationReport")
    report_status = report.status
    if report_status is ValidationStatus.QUARANTINED:
        report_status = ValidationStatus.REJECTED
    if report_status is ValidationStatus.FAILED:
        report_status = ValidationStatus.REJECTED
    report_jsonb = {
        "provider_names": list(report.provider_names),
        "data_types": [item.value for item in report.data_types],
        "symbols": list(report.symbols),
        "timeframes": list(report.timeframes),
        "window_start_utc": report.window_start_utc,
        "window_end_utc": report.window_end_utc,
        "tolerance_config_sha256": report.tolerance_config_sha256,
        "source_hashes": list(report.source_hashes),
        "results": [validation_result_to_row(item) for item in report.results],
    }
    return {
        "validation_report_id": report.validation_report_id,
        "validation_run_id": report.validation_run_id,
        "dataset_ref": report.dataset_ref,
        "accepted_count": report.accepted_count,
        "rejected_count": report.rejected_count,
        "warning_count": report.warning_count,
        "status": report_status.value,
        "report_sha256": report.report_sha256,
        "report_jsonb": report_jsonb,
        "created_at_utc": report.created_at_utc,
    }


def quarantine_decision_rows(
    report: ValidationReport,
    observations: Sequence[RawObservation] = (),
    bars: Sequence[NormalizedBar] = (),
    *,
    validation_report_id: UUID | None = None,
) -> tuple[dict[str, object], ...]:
    """Build deterministic quarantine rows for failed source observations.

    Only provenance and quality metadata are copied into ``details_jsonb``; raw payloads are never
    copied into quarantine decisions.
    """

    if not isinstance(report, ValidationReport):
        raise TypeError("report must be a ValidationReport")
    effective_validation_report_id = (
        report.validation_report_id
        if validation_report_id is None
        else validation_report_id
    )
    if not isinstance(effective_validation_report_id, UUID):
        raise TypeError("validation_report_id must be a UUID")
    observation_by_id = {item.observation_id: item for item in observations}
    bar_by_observation_id: dict[UUID, NormalizedBar] = {}
    for bar in bars:
        for observation_id in bar.source_observation_ids:
            bar_by_observation_id.setdefault(observation_id, bar)
    accepted_bar_ids = {bar.bar_id for bar in accepted_ohlcv_bars(bars, report)}
    rejected_source_observation_ids = tuple(
        observation_id
        for bar in bars
        if bar.bar_id not in accepted_bar_ids
        for observation_id in bar.source_observation_ids
    )
    reasons = map_quarantine_reasons(
        report,
        dataset_observation_ids=rejected_source_observation_ids,
    )
    result_details: dict[UUID, dict[str, object]] = {}
    for result in report.results:
        if result.status is not ValidationCheckStatus.FAILED:
            continue
        affected_ids = result.affected_observation_ids or rejected_source_observation_ids
        for observation_id in affected_ids:
            result_details.setdefault(observation_id, dict(result.details))

    rows: list[dict[str, object]] = []
    for observation_id, reason in sorted(reasons.items(), key=lambda item: str(item[0])):
        observation = observation_by_id.get(observation_id)
        bar = bar_by_observation_id.get(observation_id)
        symbol = bar.symbol if bar is not None else (observation.normalized_symbol if observation else None)
        exchange = bar.exchange if bar is not None else (observation.exchange_name if observation else None)
        timeframe = bar.timeframe if bar is not None else (observation.timeframe if observation else None)
        source_sha256 = observation.source_sha256 if observation is not None else None
        details = dict(result_details.get(observation_id, {}))
        details["source_observation_id"] = observation_id
        rows.append(
            {
                "quarantine_id": uuid5(
                    NAMESPACE_URL,
                    f"quarantine-decision:{effective_validation_report_id}:"
                    f"{observation_id}:{reason.value}",
                ),
                "validation_report_id": effective_validation_report_id,
                "validation_run_id": report.validation_run_id,
                "observation_id": observation_id,
                "quarantine_reason": reason.value,
                "symbol": symbol,
                "exchange": exchange,
                "timeframe": timeframe,
                "source_sha256": source_sha256,
                "details_jsonb": details,
                "created_at_utc": report.created_at_utc,
            }
        )
    return tuple(rows)



def normalized_trade_to_row(
    trade: NormalizedTrade,
    *,
    validation_report_id: UUID,
    validation_status: ValidationStatus = ValidationStatus.ACCEPTED,
) -> dict[str, object]:
    if not isinstance(trade, NormalizedTrade):
        raise TypeError("trade must be a NormalizedTrade")
    if validation_status not in (ValidationStatus.ACCEPTED, ValidationStatus.ACCEPTED_WITH_WARNINGS):
        raise ValueError("validated trades require an accepted validation status")
    key = trade.instrument_key
    if key is None or trade.provider_trade_id is None:
        raise ValueError("validated trades require provider instrument and trade identities")
    stable = {
        "provider_name": key.provider_name,
        "provider_instrument_id": key.provider_instrument_id,
        "provider_trade_id": trade.provider_trade_id,
        "symbol": trade.symbol,
        "exchange": trade.exchange,
        "instrument_type": key.instrument_type.value,
        "traded_at_utc": trade.traded_at_utc,
        "price": trade.price,
        "quantity": trade.quantity,
        "quote_quantity": trade.quote_quantity,
        "side": trade.side.value,
        "provider_sequence": trade.provider_sequence,
        "first_provider_trade_id": trade.first_provider_trade_id,
        "last_provider_trade_id": trade.last_provider_trade_id,
    }
    return {
        "trade_id": trade.trade_id,
        **stable,
        "record_sha256": sha256_payload(stable),
        "validation_status": validation_status.value,
        "validation_report_id": validation_report_id,
        "source_observation_ids": list(trade.source_observation_ids),
        "provenance_jsonb": dict(trade.provenance),
    }


def funding_rate_to_row(
    funding_rate: FundingRate,
    *,
    validation_report_id: UUID,
    validation_status: ValidationStatus = ValidationStatus.ACCEPTED,
) -> dict[str, object]:
    if not isinstance(funding_rate, FundingRate):
        raise TypeError("funding_rate must be a FundingRate")
    if validation_status not in (ValidationStatus.ACCEPTED, ValidationStatus.ACCEPTED_WITH_WARNINGS):
        raise ValueError("funding rates require an accepted validation status")
    key = funding_rate.instrument_key
    if key is None:
        raise ValueError("funding rates require an InstrumentKey")
    stable = {
        "provider_name": key.provider_name,
        "provider_instrument_id": key.provider_instrument_id,
        "instrument_type": key.instrument_type.value,
        "settlement_asset": key.settlement_asset,
        "symbol": funding_rate.symbol,
        "exchange": funding_rate.exchange,
        "funding_interval": funding_rate.funding_interval,
        "funding_time_utc": funding_rate.funding_time_utc,
        "rate": funding_rate.rate,
        "predicted_rate": funding_rate.predicted_rate,
        "mark_price": funding_rate.mark_price,
        "index_price": funding_rate.index_price,
    }
    return {
        "funding_rate_id": funding_rate.funding_rate_id,
        **stable,
        "record_sha256": sha256_payload(stable),
        "validation_status": validation_status.value,
        "validation_report_id": validation_report_id,
        "source_observation_ids": list(funding_rate.source_observation_ids),
        "provenance_jsonb": dict(funding_rate.provenance),
    }


def instrument_metadata_to_row(
    instrument: InstrumentMetadata,
    *,
    validation_report_id: UUID,
    validation_status: ValidationStatus = ValidationStatus.ACCEPTED,
) -> dict[str, object]:
    if not isinstance(instrument, InstrumentMetadata):
        raise TypeError("instrument must be InstrumentMetadata")
    if validation_status not in (ValidationStatus.ACCEPTED, ValidationStatus.ACCEPTED_WITH_WARNINGS):
        raise ValueError("instrument metadata requires an accepted validation status")
    key = instrument.instrument_key
    if key is None or instrument.metadata_sha256 is None:
        raise ValueError("instrument metadata requires identity and metadata hashes")
    provenance = instrument.metadata.get("provenance", {})
    return {
        "instrument_id": instrument.instrument_id,
        "provider_name": key.provider_name,
        "provider_instrument_id": key.provider_instrument_id,
        "symbol": instrument.symbol,
        "canonical_display_symbol": key.canonical_symbol,
        "exchange": instrument.exchange,
        "base_asset": instrument.base_asset,
        "quote_asset": instrument.quote_asset,
        "settlement_asset": instrument.settlement_asset,
        "instrument_type": instrument.instrument_type.value,
        "contract_type": key.contract_type,
        "margin_type": instrument.margin_type,
        "status": instrument.status.value,
        "price_precision": instrument.price_precision,
        "quantity_precision": instrument.quantity_precision,
        "tick_size": instrument.tick_size,
        "quantity_step": instrument.quantity_step,
        "minimum_quantity": instrument.minimum_quantity,
        "minimum_notional": instrument.minimum_notional,
        "contract_value": instrument.contract_value,
        "contract_multiplier": instrument.contract_multiplier,
        "margin_asset": instrument.margin_asset,
        "listing_at_utc": instrument.listing_at_utc,
        "expiry_at_utc": instrument.expiry_at_utc,
        "funding_interval": instrument.funding_interval,
        "metadata_sha256": instrument.metadata_sha256,
        "metadata_jsonb": dict(instrument.metadata),
        "validation_status": validation_status.value,
        "validation_report_id": validation_report_id,
        "source_observation_ids": list(instrument.source_observation_ids),
        "provenance_jsonb": dict(provenance) if isinstance(provenance, Mapping) else {},
        "first_seen_at_utc": instrument.first_seen_at_utc,
        "last_seen_at_utc": instrument.last_seen_at_utc,
    }


def _json_mapping(value: object, *, field_name: str) -> dict[str, object]:
    if value is None:
        return {}
    if isinstance(value, Mapping):
        return dict(value)
    if isinstance(value, str):
        parsed = json.loads(value)
        if isinstance(parsed, Mapping):
            return dict(parsed)
    raise ValueError(f"{field_name} must be a JSON object")


def _decimal_or_none(value: object) -> Decimal | None:
    return None if value is None else Decimal(str(value))


def instrument_metadata_from_row(row: Mapping[str, object]) -> InstrumentMetadata:
    """Rehydrate one immutable PostgreSQL instrument snapshot for drift comparison."""

    if not isinstance(row, Mapping):
        raise TypeError("instrument snapshot row must be a mapping")
    instrument_type = InstrumentType(str(row["instrument_type"]))
    key = InstrumentKey(
        provider_name=str(row["provider_name"]),
        exchange_name=str(row["exchange"]),
        provider_instrument_id=str(row["provider_instrument_id"]),
        base_asset=str(row["base_asset"]),
        quote_asset=str(row["quote_asset"]),
        settlement_asset=(
            None if row.get("settlement_asset") is None else str(row["settlement_asset"])
        ),
        instrument_type=instrument_type,
        canonical_symbol=str(row.get("canonical_display_symbol") or row["symbol"]),
        contract_type=(
            None if row.get("contract_type") is None else str(row["contract_type"])
        ),
        margin_type=None if row.get("margin_type") is None else str(row["margin_type"]),
    )
    metadata = _json_mapping(row.get("metadata_jsonb"), field_name="metadata_jsonb")
    provenance = _json_mapping(row.get("provenance_jsonb"), field_name="provenance_jsonb")
    if provenance and "provenance" not in metadata:
        metadata["provenance"] = provenance
    return InstrumentMetadata(
        instrument_id=UUID(str(row["instrument_id"])),
        symbol=str(row["symbol"]),
        exchange=str(row["exchange"]),
        base_asset=str(row["base_asset"]),
        quote_asset=str(row["quote_asset"]),
        instrument_type=instrument_type,
        status=InstrumentStatus(str(row["status"])),
        source_observation_ids=tuple(
            UUID(str(value)) for value in row.get("source_observation_ids", ())
        ),
        price_precision=(
            None if row.get("price_precision") is None else int(row["price_precision"])
        ),
        quantity_precision=(
            None if row.get("quantity_precision") is None else int(row["quantity_precision"])
        ),
        first_seen_at_utc=row.get("first_seen_at_utc"),
        last_seen_at_utc=row.get("last_seen_at_utc"),
        metadata=metadata,
        instrument_key=key,
        settlement_asset=key.settlement_asset,
        tick_size=_decimal_or_none(row.get("tick_size")),
        quantity_step=_decimal_or_none(row.get("quantity_step")),
        minimum_quantity=_decimal_or_none(row.get("minimum_quantity")),
        minimum_notional=_decimal_or_none(row.get("minimum_notional")),
        contract_value=_decimal_or_none(row.get("contract_value")),
        contract_multiplier=_decimal_or_none(row.get("contract_multiplier")),
        margin_asset=None if row.get("margin_asset") is None else str(row["margin_asset"]),
        margin_type=None if row.get("margin_type") is None else str(row["margin_type"]),
        listing_at_utc=row.get("listing_at_utc"),
        expiry_at_utc=row.get("expiry_at_utc"),
        funding_interval=(
            None if row.get("funding_interval") is None else str(row["funding_interval"])
        ),
        metadata_sha256=(
            None if row.get("metadata_sha256") is None else str(row["metadata_sha256"])
        ),
    )

def quarantine_decision_rows_for_records(
    report: ValidationReport,
    observations: Sequence[RawObservation],
    records: Sequence[NormalizedTrade | FundingRate | InstrumentMetadata],
    accepted_records: Sequence[NormalizedTrade | FundingRate | InstrumentMetadata],
    *,
    validation_report_id: UUID,
) -> tuple[dict[str, object], ...]:
    observation_by_id = {item.observation_id: item for item in observations}
    accepted_ids = {
        observation_id
        for record in accepted_records
        for observation_id in record.source_observation_ids
    }
    rejected_ids = tuple(
        observation_id
        for record in records
        for observation_id in record.source_observation_ids
        if observation_id not in accepted_ids
    )
    reasons = map_quarantine_reasons(report, dataset_observation_ids=rejected_ids)
    record_by_observation = {
        observation_id: record
        for record in records
        for observation_id in record.source_observation_ids
    }
    rows = []
    for observation_id, reason in sorted(reasons.items(), key=lambda item: str(item[0])):
        observation = observation_by_id.get(observation_id)
        record = record_by_observation.get(observation_id)
        symbol = getattr(record, "symbol", None)
        exchange = getattr(record, "exchange", None)
        timeframe = getattr(record, "funding_interval", None)
        rows.append({
            "quarantine_id": uuid5(
                NAMESPACE_URL,
                f"quarantine-decision:{validation_report_id}:{observation_id}:{reason.value}",
            ),
            "validation_report_id": validation_report_id,
            "validation_run_id": report.validation_run_id,
            "observation_id": observation_id,
            "quarantine_reason": reason.value,
            "symbol": symbol,
            "exchange": exchange,
            "timeframe": timeframe,
            "source_sha256": observation.source_sha256 if observation is not None else None,
            "details_jsonb": {
                "source_observation_id": observation_id,
                "data_type": observation.data_type.value if observation is not None else None,
            },
            "created_at_utc": report.created_at_utc,
        })
    return tuple(rows)
__all__ = [
    "funding_rate_to_row",
    "instrument_metadata_from_row",
    "instrument_metadata_to_row",
    "normalized_bar_to_row",
    "normalized_trade_to_row",
    "quarantine_decision_rows_for_records",
    "quarantine_decision_rows",
    "raw_observation_to_row",
    "validation_report_to_row",
    "validation_result_to_row",
]
