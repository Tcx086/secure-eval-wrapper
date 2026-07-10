"""Storage-neutral orchestration for deterministic public alpha evaluation."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Callable
from uuid import NAMESPACE_URL, uuid5

from secure_eval_wrapper.alpha.input_validation import (
    AlphaDataSet,
    prepare_point_in_time_series,
    record_timestamp,
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
from secure_eval_wrapper.data_collection.time_utils import require_utc_datetime


class AlphaEngine:
    """Resolve, validate, calculate, validate outputs, and optionally persist atomically."""

    def __init__(
        self,
        registry: PublicAlphaRegistry,
        *,
        repository: AlphaPersistenceRepository | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._registry = registry
        self._repository = repository
        self._clock = clock or (lambda: datetime.now(timezone.utc))

    def _now(self) -> datetime:
        return require_utc_datetime(self._clock(), field_name="AlphaEngine clock")

    def evaluate(
        self,
        request: AlphaEvaluationRequest,
        dataset: AlphaDataSet,
    ) -> AlphaEvaluationResult:
        if not isinstance(request, AlphaEvaluationRequest):
            raise TypeError("request must be an AlphaEvaluationRequest")
        if not isinstance(dataset, AlphaDataSet):
            raise TypeError("dataset must be an AlphaDataSet")
        started_at = self._now()
        try:
            implementation = self._registry.resolve(request.alpha_name, request.alpha_version)
            definition = implementation.definition
            if request.code_sha256 is not None and request.code_sha256 != definition.implementation_sha256:
                raise ValueError("request code_sha256 conflicts with the registered implementation")
            if dataset.dataset_sha256 != request.dataset_sha256:
                raise ValueError("request dataset_sha256 does not match validation-gated input")
            if dataset.dataset_ref not in request.dataset_refs:
                raise ValueError("dataset_ref is missing from request lineage")
            parameters = implementation.validate_parameters(request.parameters)
        except Exception as exc:
            failure = AlphaFailure(None, "validation", type(exc).__name__, str(exc))
            raise AlphaEvaluationError(failure) from exc

        values = []
        failures = []
        seen_identities = set()
        required_type = definition.required_data_types[0]
        for symbol in sorted(request.symbols):
            try:
                series = prepare_point_in_time_series(
                    dataset,
                    symbol=symbol,
                    required_data_type=required_type,
                )
                computed = implementation.evaluate(series, parameters)
                if len(computed) != len(series.records):
                    raise ValueError("alpha implementation must return one explicit point per input record")
                for record, point in zip(series.records, computed):
                    expected_timestamp = record_timestamp(record)
                    if point.timestamp_utc != expected_timestamp:
                        raise ValueError("alpha point timestamp does not match its input record")
                    if any(source_time > point.timestamp_utc for source_time in point.source_timestamps_utc):
                        raise ValueError("alpha output contains future source data")
                    if not request.window_start_utc <= point.timestamp_utc < request.window_end_utc:
                        continue
                    identity = (symbol, point.timestamp_utc)
                    if identity in seen_identities:
                        raise ValueError("duplicate alpha symbol/timestamp output identity")
                    seen_identities.add(identity)
                    value_id = uuid5(
                        NAMESPACE_URL,
                        f"alpha-value:{request.evaluation_run_id}:{definition.alpha_id}:"
                        f"{symbol}:{point.timestamp_utc.isoformat()}",
                    )
                    values.append(
                        AlphaValue(
                            alpha_value_id=value_id,
                            alpha_id=definition.alpha_id,
                            alpha_name=definition.name,
                            alpha_version=definition.version,
                            alpha_run_id=request.evaluation_run_id,
                            symbol=symbol,
                            timestamp_utc=point.timestamp_utc,
                            raw_score=point.raw_score,
                            warmup_complete=point.warmup_complete,
                            valid=point.valid,
                            horizon=definition.horizon,
                            source_observation_ids=point.source_observation_ids,
                            dataset_sha256=request.dataset_sha256,
                            config_sha256=request.config_sha256,
                            implementation_sha256=definition.implementation_sha256,
                            provenance={
                                "dataset_refs": tuple(sorted(request.dataset_refs)),
                                "validation_report_ids": tuple(sorted(dataset.validation_report_ids, key=str)),
                                "parameters": dict(parameters),
                                "calculation": dict(point.provenance),
                                "point_in_time_safe": True,
                            },
                        )
                    )
            except Exception as exc:
                failure = AlphaFailure(symbol, "evaluation", type(exc).__name__, str(exc))
                if request.fail_fast:
                    raise AlphaEvaluationError(failure) from exc
                failures.append(failure)

        values.sort(key=lambda item: (item.timestamp_utc, item.symbol, str(item.alpha_value_id)))
        valid_count = sum(item.valid for item in values)
        skipped_count = sum(not item.warmup_complete for item in values)
        rejected_count = sum(item.warmup_complete and not item.valid for item in values) + len(failures)
        status = (
            AlphaRunStatus.FAILED
            if not values and failures
            else AlphaRunStatus.PARTIAL
            if failures
            else AlphaRunStatus.COMPLETED
        )
        run = AlphaRun(
            alpha_run_id=request.evaluation_run_id,
            alpha_id=definition.alpha_id,
            alpha_name=definition.name,
            alpha_version=definition.version,
            symbols=tuple(sorted(request.symbols)),
            window_start_utc=request.window_start_utc,
            window_end_utc=request.window_end_utc,
            dataset_refs=tuple(sorted(request.dataset_refs)),
            input_data_sha256=request.dataset_sha256,
            config_sha256=request.config_sha256,
            implementation_sha256=definition.implementation_sha256,
            started_at_utc=started_at,
            completed_at_utc=self._now(),
            status=status,
            output_count=valid_count,
            rejected_count=rejected_count,
            skipped_count=skipped_count,
            metadata={
                "public_example": True,
                "parameters": dict(parameters),
                "failure_count": len(failures),
                "research_output_only": True,
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
