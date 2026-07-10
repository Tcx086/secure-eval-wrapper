"""Storage-neutral orchestration for deterministic public alpha evaluation."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Callable
from uuid import NAMESPACE_URL, uuid5

from secure_eval_wrapper.alpha.input_validation import (
    AlphaDataSet,
    prepare_point_in_time_series,
    record_timestamp,
    series_identities_for_dataset,
)
from secure_eval_wrapper.alpha.interfaces import AlphaPersistenceRepository
from secure_eval_wrapper.alpha.models import (
    AlphaEvaluationError,
    AlphaEvaluationRequest,
    AlphaEvaluationResult,
    AlphaFailure,
    AlphaRun,
    AlphaRunStatus,
    AlphaValue,
)
from secure_eval_wrapper.alpha.registry import PublicAlphaRegistry
from secure_eval_wrapper.data_collection.hashing import sha256_payload
from secure_eval_wrapper.data_collection.time_utils import require_utc_datetime


class AlphaEngine:
    """Resolve, validate, calculate, validate outputs, and optionally persist atomically."""

    def __init__(self, registry: PublicAlphaRegistry, *, repository: AlphaPersistenceRepository | None = None, clock: Callable[[], datetime] | None = None) -> None:
        self._registry = registry
        self._repository = repository
        self._clock = clock or (lambda: datetime.now(timezone.utc))

    def _now(self) -> datetime:
        return require_utc_datetime(self._clock(), field_name="AlphaEngine clock")

    def evaluate(self, request: AlphaEvaluationRequest, dataset: AlphaDataSet) -> AlphaEvaluationResult:
        if not isinstance(request, AlphaEvaluationRequest):
            raise TypeError("request must be an AlphaEvaluationRequest")
        if not isinstance(dataset, AlphaDataSet):
            raise TypeError("dataset must be an AlphaDataSet")
        started_at = self._now()
        try:
            implementation = self._registry.resolve(request.alpha_name, request.alpha_version)
            definition = implementation.definition
            if request.code_sha256 is not None and request.code_sha256 != definition.implementation_code_sha256:
                raise ValueError("request code_sha256 conflicts with the registered implementation code")
            if request.repository_commit_sha is not None and request.repository_commit_sha != definition.repository_commit_sha:
                raise ValueError("request repository_commit_sha conflicts with the registered implementation")
            if dataset.dataset_sha256 != request.dataset_sha256:
                raise ValueError("request dataset_sha256 does not match validation-gated input")
            if dataset.dataset_ref not in request.dataset_refs:
                raise ValueError("dataset_ref is missing from request lineage")
            parameters = implementation.validate_parameters(request.parameters)
        except Exception as exc:
            failure = AlphaFailure(None, "validation", type(exc).__name__, str(exc))
            raise AlphaEvaluationError(failure) from exc

        required_type = definition.required_data_types[0]
        identities = request.series_identities or series_identities_for_dataset(
            dataset, symbols=request.symbols, required_data_type=required_type
        )
        failures: list[AlphaFailure] = []
        resolved_symbols = {item.canonical_symbol for item in identities}
        for missing in sorted(set(request.symbols) - resolved_symbols):
            failure = AlphaFailure(missing, "evaluation", "ValueError", f"no {required_type} series available for {missing}")
            if request.fail_fast:
                raise AlphaEvaluationError(failure)
            failures.append(failure)

        values = []
        seen_identities = set()
        for identity in sorted(identities, key=lambda item: item.series_identity_sha256):
            try:
                full_series = prepare_point_in_time_series(
                    dataset, series_identity=identity, required_data_type=required_type
                )
                series = full_series.eligible_as_of(request.as_of_utc) if request.as_of_utc is not None else full_series
                computed = implementation.evaluate(series, parameters)
                if len(computed) != len(series.records):
                    raise ValueError("alpha implementation must return one explicit point per input record")
                indexed = [(len(series.records) - 1, series.records[-1], computed[-1])] if request.as_of_utc is not None else list(
                    (index, record, point) for index, (record, point) in enumerate(zip(series.records, computed))
                )
                for _, record, point in indexed:
                    record_time = record_timestamp(record)
                    if point.timestamp_utc != record_time:
                        raise ValueError("alpha point timestamp does not match input availability time")
                    as_of = request.as_of_utc or point.timestamp_utc
                    if any(source_time > as_of for source_time in point.source_timestamps_utc):
                        raise ValueError("alpha output contains data unavailable at as_of_utc")
                    if not request.window_start_utc <= as_of < request.window_end_utc:
                        continue
                    logical_identity = (identity.series_identity_sha256, as_of, definition.horizon)
                    if logical_identity in seen_identities:
                        raise ValueError("duplicate alpha series/as-of/horizon output identity")
                    seen_identities.add(logical_identity)
                    input_hash = full_series.eligible_input_sha256(as_of)
                    source_times = tuple(sorted(point.source_timestamps_utc))
                    value_id = uuid5(
                        NAMESPACE_URL,
                        f"alpha-value:{request.evaluation_run_id}:{definition.alpha_id}:{identity.series_identity_sha256}:"
                        f"{as_of.isoformat()}:{definition.horizon}:{input_hash}:"
                        f"{request.config_sha256}:{definition.formula_sha256}:"
                        f"{definition.implementation_code_sha256}",
                    )
                    values.append(AlphaValue(
                        alpha_value_id=value_id,
                        alpha_id=definition.alpha_id,
                        alpha_name=definition.name,
                        alpha_version=definition.version,
                        alpha_run_id=request.evaluation_run_id,
                        symbol=identity.canonical_symbol,
                        timestamp_utc=as_of,
                        raw_score=point.raw_score,
                        warmup_complete=point.warmup_complete,
                        valid=point.valid,
                        horizon=definition.horizon,
                        source_observation_ids=point.source_observation_ids,
                        dataset_sha256=request.dataset_sha256,
                        config_sha256=request.config_sha256,
                        implementation_sha256=definition.implementation_code_sha256,
                        series_identity=identity,
                        status=point.status,
                        reason_code=point.reason_code,
                        reason_message=point.reason_message,
                        as_of_utc=as_of,
                        lookback_start_utc=source_times[0] if source_times else None,
                        lookback_end_utc=source_times[-1] if source_times else None,
                        eligible_input_sha256=input_hash,
                        formula_sha256=definition.formula_sha256,
                        implementation_code_sha256=definition.implementation_code_sha256,
                        repository_commit_sha=definition.repository_commit_sha,
                        provenance={
                            "dataset_refs": tuple(sorted(request.dataset_refs)),
                            "validation_report_ids": tuple(sorted(dataset.validation_report_ids, key=str)),
                            "parameters": dict(parameters),
                            "calculation": dict(point.provenance),
                            "point_in_time_safe": True,
                            "bar_availability_semantics": "close_time_and_finality",
                        },
                    ))
            except Exception as exc:
                failure = AlphaFailure(identity.canonical_symbol, "evaluation", type(exc).__name__, str(exc), identity.series_identity_sha256)
                if request.fail_fast:
                    raise AlphaEvaluationError(failure) from exc
                failures.append(failure)

        values.sort(key=lambda item: (item.timestamp_utc, item.series_identity.series_identity_sha256, str(item.alpha_value_id)))
        valid_count = sum(item.valid for item in values)
        skipped_count = sum(item.status.value in ("warmup", "skipped") for item in values)
        rejected_count = sum(item.status.value in ("invalid", "failed") for item in values) + len(failures)
        status = (
            AlphaRunStatus.FAILED if not values and failures
            else AlphaRunStatus.PARTIAL if failures
            else AlphaRunStatus.COMPLETED
        )
        input_data_sha256 = sha256_payload(tuple(sorted({item.eligible_input_sha256 for item in values}))) if values else request.dataset_sha256
        run = AlphaRun(
            alpha_run_id=request.evaluation_run_id,
            alpha_id=definition.alpha_id,
            alpha_name=definition.name,
            alpha_version=definition.version,
            symbols=tuple(sorted({item.canonical_symbol for item in identities})),
            window_start_utc=request.window_start_utc,
            window_end_utc=request.window_end_utc,
            dataset_refs=tuple(sorted(request.dataset_refs)),
            input_data_sha256=input_data_sha256,
            config_sha256=request.config_sha256,
            implementation_sha256=definition.implementation_code_sha256,
            started_at_utc=started_at,
            completed_at_utc=self._now(),
            status=status,
            output_count=valid_count,
            rejected_count=rejected_count,
            skipped_count=skipped_count,
            series_identity_sha256_set=tuple(sorted(item.series_identity_sha256 for item in identities)),
            formula_sha256=definition.formula_sha256,
            implementation_code_sha256=definition.implementation_code_sha256,
            repository_commit_sha=definition.repository_commit_sha,
            metadata={
                "public_example": True,
                "parameters": dict(parameters),
                "failure_count": len(failures),
                "research_output_only": True,
                "as_of_utc": request.as_of_utc,
            },
        )
        result = AlphaEvaluationResult(run=run, values=tuple(values), failures=tuple(failures))
        if request.persistence_enabled:
            repository = self._repository
            if repository is None or not hasattr(repository, "transaction"):
                failure = AlphaFailure(None, "persistence", "TypeError", "persistence requires an injected transactional PostgreSQL repository")
                raise AlphaEvaluationError(failure)
            try:
                with repository.transaction():
                    repository.register_alpha(definition)
                    repository.record_alpha_run(run)
                    for value in values:
                        repository.record_alpha_value(value)
            except Exception as exc:
                failure = AlphaFailure(None, "persistence", type(exc).__name__, str(exc))
                raise AlphaEvaluationError(failure) from exc
        return result


__all__ = ["AlphaEngine"]
