"""Mappings from Phase 3-4 domain records to PostgreSQL rows."""

from __future__ import annotations

from secure_eval_wrapper.alpha.models import AlphaDefinition, AlphaRun, AlphaValue
from secure_eval_wrapper.data_collection.hashing import sha256_payload
from secure_eval_wrapper.signals.models import SignalComponent, SignalRun, StandardizedSignal


def alpha_definition_to_row(definition: AlphaDefinition) -> dict[str, object]:
    return {
        "alpha_id": definition.alpha_id,
        "alpha_name": definition.name,
        "alpha_version": definition.version,
        "description": definition.description,
        "category": definition.category,
        "required_data_types": list(definition.required_data_types),
        "required_fields": list(definition.required_fields),
        "parameter_schema_jsonb": dict(definition.parameter_schema),
        "default_parameters_jsonb": dict(definition.default_parameters),
        "minimum_warmup": definition.minimum_warmup,
        "output_semantics": definition.output_semantics,
        "horizon": definition.horizon,
        "public_example": definition.public_example,
        "status": definition.status.value,
        "implementation_sha256": definition.implementation_code_sha256,
        "formula_sha256": definition.formula_sha256,
        "implementation_code_sha256": definition.implementation_code_sha256,
        "repository_commit_sha": definition.repository_commit_sha,
        "content_sha256": definition.content_sha256,
    }


def alpha_run_to_row(run: AlphaRun) -> dict[str, object]:
    stable = {
        "alpha_run_id": run.alpha_run_id,
        "alpha_id": run.alpha_id,
        "alpha_name": run.alpha_name,
        "alpha_version": run.alpha_version,
        "symbol_set": list(run.symbols),
        "series_identity_sha256_set": list(run.series_identity_sha256_set),
        "window_start_utc": run.window_start_utc,
        "window_end_utc": run.window_end_utc,
        "dataset_refs": list(run.dataset_refs),
        "input_data_sha256": run.input_data_sha256,
        "config_sha256": run.config_sha256,
        "implementation_sha256": run.implementation_code_sha256,
        "formula_sha256": run.formula_sha256,
        "implementation_code_sha256": run.implementation_code_sha256,
        "repository_commit_sha": run.repository_commit_sha,
        "status": run.status.value,
        "output_count": run.output_count,
        "rejected_count": run.rejected_count,
        "skipped_count": run.skipped_count,
        "metadata_jsonb": dict(run.metadata),
    }
    return {**stable, "content_sha256": sha256_payload(stable), "started_at_utc": run.started_at_utc, "completed_at_utc": run.completed_at_utc}


def _identity_columns(record: AlphaValue | StandardizedSignal) -> dict[str, object]:
    identity = record.series_identity
    return {
        "provider_name": identity.provider_name,
        "exchange": identity.exchange,
        "provider_instrument_id": identity.provider_instrument_id,
        "canonical_symbol": identity.canonical_symbol,
        "instrument_type": identity.instrument_type.value,
        "timeframe": identity.timeframe,
        "settlement_asset": identity.settlement_asset,
        "series_identity_sha256": identity.series_identity_sha256,
    }


def alpha_value_to_row(value: AlphaValue) -> dict[str, object]:
    return {
        "alpha_value_id": value.alpha_value_id,
        "alpha_run_id": value.alpha_run_id,
        "alpha_id": value.alpha_id,
        "alpha_name": value.alpha_name,
        "alpha_version": value.alpha_version,
        "symbol": value.symbol,
        **_identity_columns(value),
        "timestamp_utc": value.timestamp_utc,
        "as_of_utc": value.as_of_utc,
        "lookback_start_utc": value.lookback_start_utc,
        "lookback_end_utc": value.lookback_end_utc,
        "raw_score": value.raw_score,
        "warmup_complete": value.warmup_complete,
        "valid": value.valid,
        "evaluation_status": value.status.value,
        "reason_code": value.reason_code,
        "reason_message": value.reason_message,
        "horizon": value.horizon,
        "source_observation_ids": list(value.source_observation_ids),
        "dataset_sha256": value.dataset_sha256,
        "eligible_input_sha256": value.eligible_input_sha256,
        "config_sha256": value.config_sha256,
        "implementation_sha256": value.implementation_code_sha256,
        "formula_sha256": value.formula_sha256,
        "implementation_code_sha256": value.implementation_code_sha256,
        "repository_commit_sha": value.repository_commit_sha,
        "record_sha256": value.record_sha256,
        "content_sha256": value.record_sha256,
        "provenance_jsonb": dict(value.provenance),
    }


def signal_run_to_row(run: SignalRun) -> dict[str, object]:
    stable = {
        "signal_run_id": run.signal_run_id,
        "alpha_run_ids": list(run.alpha_run_ids),
        "symbol_universe": list(run.symbol_universe),
        "series_identity_sha256_set": list(run.series_identity_sha256_set),
        "window_start_utc": run.window_start_utc,
        "window_end_utc": run.window_end_utc,
        "ranking_config_jsonb": dict(run.ranking_config),
        "threshold_config_jsonb": dict(run.threshold_config),
        "combination_config_jsonb": dict(run.combination_config),
        "config_sha256": run.config_sha256,
        "code_sha256": run.implementation_code_sha256,
        "formula_sha256": run.formula_sha256,
        "implementation_code_sha256": run.implementation_code_sha256,
        "repository_commit_sha": run.repository_commit_sha,
        "data_sha256": run.data_sha256,
        "overlap_policy": run.overlap_policy,
        "overlap_resolution_reason": run.overlap_resolution_reason,
        "status": run.status.value,
        "output_count": run.output_count,
        "long_count": run.long_count,
        "short_count": run.short_count,
        "flat_count": run.flat_count,
        "skipped_count": run.skipped_count,
        "failure_count": run.failure_count,
        "metadata_jsonb": dict(run.metadata),
    }
    return {
        **stable,
        "run_id": run.signal_run_id,
        "dataset_ref": f"sha256:{run.data_sha256}",
        "seed": None,
        "started_at_utc": run.started_at_utc,
        "completed_at_utc": run.completed_at_utc,
        "content_sha256": sha256_payload(stable),
    }


def standardized_signal_to_row(signal: StandardizedSignal) -> dict[str, object]:
    contributions = signal.provenance.get("contributions", [{}])
    return {
        "signal_id": signal.signal_id,
        "signal_run_id": signal.signal_run_id,
        "alpha_id": None if len(signal.alpha_ids_versions) != 1 else contributions[0].get("alpha_id"),
        "alpha_ids_versions": list(signal.alpha_ids_versions),
        "alpha_run_ids": list(signal.alpha_run_ids),
        "symbol": signal.symbol,
        **_identity_columns(signal),
        "timestamp_utc": signal.timestamp_utc,
        "direction": signal.direction.value,
        "score": signal.raw_score,
        "raw_score": signal.raw_score,
        "normalized_score": signal.normalized_score,
        "rank": signal.rank,
        "percentile": signal.percentile,
        "confidence": signal.confidence,
        "horizon": signal.horizon,
        "source_alpha_value_ids": list(signal.source_alpha_value_ids),
        "config_sha256": signal.config_sha256,
        "data_sha256": signal.data_sha256,
        "code_sha256": signal.implementation_code_sha256,
        "formula_sha256": signal.formula_sha256,
        "implementation_code_sha256": signal.implementation_code_sha256,
        "repository_commit_sha": signal.repository_commit_sha,
        "overlap_policy": signal.overlap_policy,
        "resolution_reason": signal.resolution_reason,
        "record_sha256": signal.record_sha256,
        "content_sha256": signal.record_sha256,
        "provenance_jsonb": dict(signal.provenance),
    }


def signal_component_to_row(component: SignalComponent) -> dict[str, object]:
    return {
        "signal_component_id": component.signal_component_id,
        "signal_id": component.signal_id,
        "alpha_value_id": component.alpha_value_id,
        "alpha_id": component.alpha_id,
        "raw_value": component.raw_value,
        "normalized_value": component.normalized_value,
        "configured_weight": component.configured_weight,
        "effective_weight": component.effective_weight,
        "signed_contribution": component.signed_contribution,
        "component_disposition": component.component_disposition.value,
        "resolution_reason": component.resolution_reason,
        "component_sha256": component.component_sha256,
        "public_metadata_jsonb": dict(component.public_metadata),
    }


__all__ = [
    "alpha_definition_to_row",
    "alpha_run_to_row",
    "alpha_value_to_row",
    "signal_component_to_row",
    "signal_run_to_row",
    "standardized_signal_to_row",
]
