"""Generic typed orchestration shared by non-OHLCV public data pipelines."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from enum import Enum
from typing import Generic, TypeVar
from uuid import UUID

from secure_eval_wrapper.data_collection.models import (
    CollectionStatus,
    DataRequest,
    MarketDataType,
    ProviderCapabilityStatus,
    RawObservation,
)
from secure_eval_wrapper.data_collection.providers import MarketDataProvider
from secure_eval_wrapper.data_pipeline.ohlcv_pipeline import PipelineStage, PipelineStatus
from secure_eval_wrapper.data_validation.models import ValidationReport, ValidationStatus


RecordT = TypeVar("RecordT")
SummaryT = TypeVar("SummaryT")


@dataclass(frozen=True)
class MarketDataPipelineFailure:
    provider_name: str | None
    stage: PipelineStage
    error_type: str
    message: str


@dataclass(frozen=True)
class TypedProviderOutcome(Generic[RecordT]):
    provider_name: str
    status: CollectionStatus
    observations: tuple[RawObservation, ...] = ()
    records: tuple[RecordT, ...] = ()
    validation_report: ValidationReport | None = None
    accepted_records: tuple[RecordT, ...] = ()
    rejected_count: int = 0
    usable: bool = False
    error: MarketDataPipelineFailure | None = None


@dataclass(frozen=True)
class TypedPipelinePersistence(Generic[SummaryT]):
    provider_summaries: tuple[tuple[str, SummaryT], ...]


@dataclass(frozen=True)
class TypedPipelineResult(Generic[RecordT, SummaryT]):
    collection_run_id: UUID
    validation_run_id: UUID
    data_type: MarketDataType
    provider_names: tuple[str, ...]
    status: PipelineStatus
    outcomes: tuple[TypedProviderOutcome[RecordT], ...]
    persistence: TypedPipelinePersistence[SummaryT] | None

    @property
    def errors(self) -> tuple[MarketDataPipelineFailure, ...]:
        return tuple(
            outcome.error for outcome in self.outcomes if outcome.error is not None
        )

    @property
    def accepted_records(self) -> tuple[RecordT, ...]:
        return tuple(
            record
            for outcome in self.outcomes
            for record in outcome.accepted_records
        )


class TypedPipelineError(RuntimeError):
    def __init__(
        self,
        failure: MarketDataPipelineFailure,
        outcomes: Sequence[TypedProviderOutcome[object]] = (),
    ) -> None:
        super().__init__(
            f"{failure.stage.value} failed"
            + (f" for {failure.provider_name}: {failure.message}" if failure.provider_name else f": {failure.message}")
        )
        self.failure = failure
        self.outcomes = tuple(outcomes)


Normalizer = Callable[[Sequence[RawObservation]], tuple[RecordT, ...]]
Validator = Callable[[UUID, str, Sequence[RecordT], DataRequest], ValidationReport]
Gate = Callable[[Sequence[RecordT], ValidationReport], tuple[RecordT, ...]]
Persister = Callable[[Sequence[RawObservation], Sequence[RecordT], ValidationReport, object], SummaryT]


class TypedMarketDataPipeline(Generic[RecordT, SummaryT]):
    """Typed callback-driven pipeline without runtime record-type branching."""

    def __init__(
        self,
        providers: Sequence[MarketDataProvider],
        *,
        data_type: MarketDataType,
        fetch: Callable[[MarketDataProvider, DataRequest], Sequence[RawObservation]],
        normalize: Normalizer[RecordT],
        validate: Validator[RecordT],
        gate: Gate[RecordT],
        persist: Persister[RecordT, SummaryT],
        repository: object | None = None,
    ) -> None:
        by_name: dict[str, MarketDataProvider] = {}
        for provider in providers:
            if not isinstance(provider, MarketDataProvider):
                raise TypeError("providers must implement MarketDataProvider")
            if not provider.spec.public_market_data_only:
                raise ValueError("pipelines accept public-market-data-only providers")
            if provider.spec.capabilities.get(data_type) is not ProviderCapabilityStatus.IMPLEMENTED:
                raise ValueError(f"provider '{provider.spec.name}' does not implement {data_type.value}")
            if provider.spec.name in by_name:
                raise ValueError(f"duplicate provider component '{provider.spec.name}'")
            by_name[provider.spec.name] = provider
        if not by_name:
            raise ValueError("at least one provider component is required")
        self._providers = by_name
        self._data_type = data_type
        self._fetch = fetch
        self._normalize = normalize
        self._validate = validate
        self._gate = gate
        self._persist_one = persist
        self._repository = repository

    def run(
        self,
        *,
        collection_run_id: UUID,
        validation_run_id: UUID,
        requests_by_provider: Mapping[str, DataRequest],
        persistence_enabled: bool,
        fail_fast: bool,
    ) -> TypedPipelineResult[RecordT, SummaryT]:
        provider_names = tuple(sorted(requests_by_provider))
        if not provider_names:
            raise ValueError("requests_by_provider must not be empty")
        missing = sorted(set(provider_names) - set(self._providers))
        if missing:
            raise ValueError("missing injected providers: " + ", ".join(missing))
        if persistence_enabled and self._repository is None:
            raise ValueError("persistence requires an injected PostgreSQL repository")

        outcomes: list[TypedProviderOutcome[RecordT]] = []
        for provider_name in provider_names:
            request = requests_by_provider[provider_name]
            if request.collection_run_id != collection_run_id:
                raise ValueError("provider request collection_run_id mismatch")
            if request.provider_name != provider_name or request.data_type is not self._data_type:
                raise ValueError("provider request identity or data type mismatch")
            observations: tuple[RawObservation, ...] = ()
            records: tuple[RecordT, ...] = ()
            report = None
            stage = PipelineStage.COLLECTION
            try:
                observations = tuple(self._fetch(self._providers[provider_name], request))
                if not observations:
                    raise ValueError(f"provider returned no {self._data_type.value} observations")
                stage = PipelineStage.NORMALIZATION
                records = self._normalize(observations)
                stage = PipelineStage.VALIDATION
                report = self._validate(validation_run_id, provider_name, records, request)
            except Exception as exc:
                failure = MarketDataPipelineFailure(
                    provider_name=provider_name,
                    stage=stage,
                    error_type=type(exc).__name__,
                    message=str(exc),
                )
                outcome = TypedProviderOutcome(
                    provider_name=provider_name,
                    status=CollectionStatus.FAILED,
                    observations=observations,
                    records=records,
                    validation_report=report,
                    error=failure,
                )
                outcomes.append(outcome)
                if fail_fast:
                    raise TypedPipelineError(failure, outcomes) from exc
                continue

            accepted = self._gate(records, report)
            rejected_count = len(records) - len(accepted)
            usable = bool(accepted) and report.status not in (
                ValidationStatus.QUARANTINED,
                ValidationStatus.FAILED,
            )
            outcomes.append(
                TypedProviderOutcome(
                    provider_name=provider_name,
                    status=CollectionStatus.SUCCEEDED,
                    observations=observations,
                    records=records,
                    validation_report=report,
                    accepted_records=accepted,
                    rejected_count=rejected_count,
                    usable=usable,
                )
            )

        persistence = (
            self._persist(outcomes)
            if persistence_enabled
            else None
        )
        usable_count = sum(outcome.usable for outcome in outcomes)
        complete = all(
            outcome.error is None
            and outcome.usable
            and outcome.rejected_count == 0
            and outcome.validation_report is not None
            and outcome.validation_report.status in (
                ValidationStatus.ACCEPTED,
                ValidationStatus.ACCEPTED_WITH_WARNINGS,
            )
            for outcome in outcomes
        )
        status = (
            PipelineStatus.FAILED
            if usable_count == 0
            else PipelineStatus.SUCCEEDED
            if complete
            else PipelineStatus.PARTIAL
        )
        return TypedPipelineResult(
            collection_run_id=collection_run_id,
            validation_run_id=validation_run_id,
            data_type=self._data_type,
            provider_names=provider_names,
            status=status,
            outcomes=tuple(outcomes),
            persistence=persistence,
        )

    def _persist(
        self,
        outcomes: Sequence[TypedProviderOutcome[RecordT]],
    ) -> TypedPipelinePersistence[SummaryT]:
        repository = self._repository
        if repository is None or not hasattr(repository, "transaction"):
            raise TypeError("persistence requires a unified transactional PostgreSQL repository")
        summaries = []
        try:
            with repository.transaction():
                for outcome in outcomes:
                    if outcome.error is not None or outcome.validation_report is None:
                        continue
                    summaries.append((
                        outcome.provider_name,
                        self._persist_one(
                            outcome.observations,
                            outcome.records,
                            outcome.validation_report,
                            repository,
                        ),
                    ))
        except Exception as exc:
            failure = MarketDataPipelineFailure(
                provider_name=None,
                stage=PipelineStage.PERSISTENCE,
                error_type=type(exc).__name__,
                message=str(exc),
            )
            raise TypedPipelineError(failure, outcomes) from exc
        return TypedPipelinePersistence(provider_summaries=tuple(summaries))


__all__ = [
    "MarketDataPipelineFailure",
    "TypedMarketDataPipeline",
    "TypedPipelineError",
    "TypedPipelinePersistence",
    "TypedPipelineResult",
    "TypedProviderOutcome",
]
