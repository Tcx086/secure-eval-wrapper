"""End-to-end public OHLCV collection, validation, reconciliation, and persistence."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from uuid import UUID

from secure_eval_wrapper.data_collection.models import (
    CollectionStatus,
    DataRequest,
    MarketDataType,
    NormalizedBar,
    ProviderCapabilityStatus,
    RawObservation,
)
from secure_eval_wrapper.data_collection.normalization import normalize_ohlcv_observations
from secure_eval_wrapper.data_collection.providers import MarketDataProvider
from secure_eval_wrapper.data_collection.symbols import normalize_symbol
from secure_eval_wrapper.data_collection.time_utils import require_utc_datetime
from secure_eval_wrapper.data_validation.gating import accepted_ohlcv_bars
from secure_eval_wrapper.data_validation.models import (
    ReconciliationResult,
    ValidationReport,
    ValidationStatus,
)
from secure_eval_wrapper.data_validation.ohlcv import (
    OhlcvValidationConfig,
    validate_ohlcv_bars,
)
from secure_eval_wrapper.data_validation.persistence import (
    OfflinePersistenceSummary,
    persist_offline_ohlcv_validation_flow,
)
from secure_eval_wrapper.data_validation.reconciliation import (
    OhlcvReconciliationConfig,
    reconcile_ohlcv_sources,
)
from secure_eval_wrapper.data_validation.reconciliation_persistence import (
    ReconciliationPersistenceSummary,
    persist_reconciliation_result,
)


class PipelineStatus(str, Enum):
    """Overall pipeline outcome after collection and validation gates."""

    SUCCEEDED = "succeeded"
    PARTIAL = "partial"
    FAILED = "failed"


class PipelineStage(str, Enum):
    """Stable stage labels for explicit pipeline failures."""

    COLLECTION = "collection"
    NORMALIZATION = "normalization"
    VALIDATION = "validation"
    RECONCILIATION = "reconciliation"
    PERSISTENCE = "persistence"


@dataclass(frozen=True)
class OhlcvPipelineRequest:
    """One bounded provider-neutral OHLCV pipeline request."""

    collection_run_id: UUID
    validation_run_id: UUID
    provider_names: tuple[str, ...]
    symbol: str
    timeframe: str
    start_at_utc: datetime
    end_at_utc: datetime
    limit: int = 100
    max_pages: int = 20
    persistence_enabled: bool = False
    fail_fast: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.collection_run_id, UUID):
            raise TypeError("collection_run_id must be a UUID")
        if not isinstance(self.validation_run_id, UUID):
            raise TypeError("validation_run_id must be a UUID")
        if not self.provider_names:
            raise ValueError("provider_names must not be empty")
        normalized_names: list[str] = []
        for name in self.provider_names:
            if not isinstance(name, str) or not name.strip():
                raise ValueError("provider_names must contain non-empty strings")
            normalized_names.append(name.strip().lower())
        if len(set(normalized_names)) != len(normalized_names):
            raise ValueError("provider_names must be unique")
        object.__setattr__(self, "provider_names", tuple(sorted(normalized_names)))
        object.__setattr__(self, "symbol", normalize_symbol(self.symbol))
        if not isinstance(self.timeframe, str) or not self.timeframe.strip():
            raise ValueError("timeframe must be a non-empty string")
        object.__setattr__(self, "timeframe", self.timeframe.strip())
        start_at_utc = require_utc_datetime(
            self.start_at_utc,
            field_name="pipeline start_at_utc",
        )
        end_at_utc = require_utc_datetime(
            self.end_at_utc,
            field_name="pipeline end_at_utc",
        )
        if end_at_utc <= start_at_utc:
            raise ValueError("pipeline end_at_utc must be later than start_at_utc")
        if (
            isinstance(self.limit, bool)
            or not isinstance(self.limit, int)
            or self.limit <= 0
        ):
            raise ValueError("pipeline limit must be positive")
        maximum_limit = 300 if "okx" in self.provider_names else 1_000
        if self.limit > maximum_limit:
            raise ValueError(f"pipeline limit must not exceed {maximum_limit}")
        if (
            isinstance(self.max_pages, bool)
            or not isinstance(self.max_pages, int)
            or self.max_pages <= 0
            or self.max_pages > 1_000
        ):
            raise ValueError("pipeline max_pages must be between 1 and 1000")
        if not isinstance(self.persistence_enabled, bool):
            raise TypeError("persistence_enabled must be a boolean")
        if not isinstance(self.fail_fast, bool):
            raise TypeError("fail_fast must be a boolean")


@dataclass(frozen=True)
class OhlcvPipelineFailure:
    """Sanitized, explicit failure information for one stage."""

    provider_name: str | None
    stage: PipelineStage
    error_type: str
    message: str


@dataclass(frozen=True)
class ProviderCollectionOutcome:
    """Collection, normalization, and validation outcome for one provider."""

    provider_name: str
    status: CollectionStatus
    observations: tuple[RawObservation, ...] = ()
    bars: tuple[NormalizedBar, ...] = ()
    validation_report: ValidationReport | None = None
    validation_status: ValidationStatus | None = None
    accepted_bars: tuple[NormalizedBar, ...] = ()
    rejected_bar_count: int = 0
    eligible_for_reconciliation: bool = False
    error: OhlcvPipelineFailure | None = None


@dataclass(frozen=True)
class OhlcvPipelinePersistenceSummary:
    """Identifiers written inside the pipeline's one outer transaction."""

    provider_summaries: tuple[tuple[str, OfflinePersistenceSummary], ...]
    reconciliation_summary: ReconciliationPersistenceSummary | None


@dataclass(frozen=True)
class OhlcvPipelineResult:
    """Typed public OHLCV pipeline result."""

    collection_run_id: UUID
    validation_run_id: UUID
    provider_names: tuple[str, ...]
    symbol: str
    timeframe: str
    start_at_utc: datetime
    end_at_utc: datetime
    status: PipelineStatus
    outcomes: tuple[ProviderCollectionOutcome, ...]
    reconciliation: ReconciliationResult | None
    persistence: OhlcvPipelinePersistenceSummary | None

    @property
    def errors(self) -> tuple[OhlcvPipelineFailure, ...]:
        return tuple(
            outcome.error for outcome in self.outcomes if outcome.error is not None
        )


class OhlcvPipelineError(RuntimeError):
    """Fail-fast exception retaining the explicit failure and completed outcomes."""

    def __init__(
        self,
        failure: OhlcvPipelineFailure,
        outcomes: Sequence[ProviderCollectionOutcome] = (),
    ) -> None:
        super().__init__(
            f"OHLCV pipeline {failure.stage.value} failed"
            + (
                f" for {failure.provider_name}: {failure.message}"
                if failure.provider_name is not None
                else f": {failure.message}"
            )
        )
        self.failure = failure
        self.outcomes = tuple(outcomes)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _dataset_ref(request: OhlcvPipelineRequest, provider_name: str) -> str:
    return (
        f"public-ohlcv:{provider_name}:{request.symbol}:{request.timeframe}:"
        f"{request.start_at_utc.isoformat()}:{request.end_at_utc.isoformat()}"
    )


class OhlcvPipeline:
    """Orchestrate injected public providers without adding any trading behavior."""

    def __init__(
        self,
        providers: Sequence[MarketDataProvider],
        *,
        validation_config: OhlcvValidationConfig | None = None,
        reconciliation_config: OhlcvReconciliationConfig | None = None,
        repository: object | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        providers_by_name: dict[str, MarketDataProvider] = {}
        for provider in providers:
            if not isinstance(provider, MarketDataProvider):
                raise TypeError("providers must implement MarketDataProvider")
            spec = provider.spec
            if not spec.public_market_data_only:
                raise ValueError("pipeline providers must be public-market-data-only")
            if (
                spec.capabilities.get(MarketDataType.OHLCV)
                is not ProviderCapabilityStatus.IMPLEMENTED
            ):
                raise ValueError(f"provider '{spec.name}' does not implement public OHLCV")
            if spec.name in providers_by_name:
                raise ValueError(f"duplicate provider '{spec.name}'")
            providers_by_name[spec.name] = provider
        if not providers_by_name:
            raise ValueError("at least one provider is required")
        self._providers_by_name = providers_by_name
        self._validation_config = validation_config
        self._reconciliation_config = reconciliation_config
        self._repository = repository
        self._clock = _utc_now if clock is None else clock

    def run(self, request: OhlcvPipelineRequest) -> OhlcvPipelineResult:
        """Run collection through optional atomic persistence."""

        if not isinstance(request, OhlcvPipelineRequest):
            raise TypeError("request must be an OhlcvPipelineRequest")
        missing = sorted(set(request.provider_names) - set(self._providers_by_name))
        if missing:
            raise ValueError("missing injected providers: " + ", ".join(missing))
        if request.persistence_enabled and self._repository is None:
            raise ValueError("persistence_enabled requires an injected PostgreSQL repository")

        outcomes: list[ProviderCollectionOutcome] = []
        successful_datasets: dict[str, tuple[NormalizedBar, ...]] = {}
        for provider_name in request.provider_names:
            provider = self._providers_by_name[provider_name]
            stage = PipelineStage.COLLECTION
            observations: tuple[RawObservation, ...] = ()
            bars: tuple[NormalizedBar, ...] = ()
            report: ValidationReport | None = None
            try:
                observations = tuple(
                    provider.fetch_ohlcv(
                        DataRequest(
                            collection_run_id=request.collection_run_id,
                            provider_name=provider_name,
                            data_type=MarketDataType.OHLCV,
                            symbols=(request.symbol,),
                            timeframe=request.timeframe,
                            start_at_utc=request.start_at_utc,
                            end_at_utc=request.end_at_utc,
                            limit=request.limit,
                            max_pages=request.max_pages,
                        )
                    )
                )
                if not observations:
                    raise ValueError("provider returned no OHLCV observations in the window")
                stage = PipelineStage.NORMALIZATION
                bars = normalize_ohlcv_observations(observations)
                stage = PipelineStage.VALIDATION
                report = validate_ohlcv_bars(
                    validation_run_id=request.validation_run_id,
                    dataset_ref=_dataset_ref(request, provider_name),
                    bars=bars,
                    config=self._validation_config,
                    clock=self._clock,
                )
            except Exception as exc:
                failure = OhlcvPipelineFailure(
                    provider_name=provider_name,
                    stage=stage,
                    error_type=type(exc).__name__,
                    message=str(exc),
                )
                outcome = ProviderCollectionOutcome(
                    provider_name=provider_name,
                    status=CollectionStatus.FAILED,
                    observations=observations,
                    bars=bars,
                    validation_report=report,
                    validation_status=report.status if report is not None else None,
                    error=failure,
                )
                outcomes.append(outcome)
                if request.fail_fast:
                    raise OhlcvPipelineError(failure, outcomes) from exc
                continue

            accepted_bars = accepted_ohlcv_bars(bars, report)
            rejected_bar_count = len(bars) - len(accepted_bars)
            eligible_for_reconciliation = bool(accepted_bars) and report.status not in (
                ValidationStatus.QUARANTINED,
                ValidationStatus.FAILED,
            )
            if eligible_for_reconciliation:
                successful_datasets[provider_name] = accepted_bars
            outcomes.append(
                ProviderCollectionOutcome(
                    provider_name=provider_name,
                    status=CollectionStatus.SUCCEEDED,
                    observations=observations,
                    bars=bars,
                    validation_report=report,
                    validation_status=report.status,
                    accepted_bars=accepted_bars,
                    rejected_bar_count=rejected_bar_count,
                    eligible_for_reconciliation=eligible_for_reconciliation,
                )
            )

        reconciliation: ReconciliationResult | None = None
        if len(successful_datasets) >= 2:
            try:
                reconciliation = reconcile_ohlcv_sources(
                    validation_run_id=request.validation_run_id,
                    datasets_by_provider=successful_datasets,
                    config=self._reconciliation_config,
                    clock=self._clock,
                )
            except Exception as exc:
                failure = OhlcvPipelineFailure(
                    provider_name=None,
                    stage=PipelineStage.RECONCILIATION,
                    error_type=type(exc).__name__,
                    message=str(exc),
                )
                raise OhlcvPipelineError(failure, outcomes) from exc

        persistence = (
            self._persist(request, outcomes, reconciliation)
            if request.persistence_enabled
            else None
        )
        usable_count = sum(outcome.eligible_for_reconciliation for outcome in outcomes)
        all_complete_and_usable = all(
            outcome.error is None
            and outcome.eligible_for_reconciliation
            and outcome.validation_status in (
                ValidationStatus.ACCEPTED,
                ValidationStatus.ACCEPTED_WITH_WARNINGS,
            )
            and outcome.rejected_bar_count == 0
            for outcome in outcomes
        )
        if usable_count == 0:
            status = PipelineStatus.FAILED
        elif all_complete_and_usable:
            status = PipelineStatus.SUCCEEDED
        else:
            status = PipelineStatus.PARTIAL
        return OhlcvPipelineResult(
            collection_run_id=request.collection_run_id,
            validation_run_id=request.validation_run_id,
            provider_names=request.provider_names,
            symbol=request.symbol,
            timeframe=request.timeframe,
            start_at_utc=request.start_at_utc,
            end_at_utc=request.end_at_utc,
            status=status,
            outcomes=tuple(outcomes),
            reconciliation=reconciliation,
            persistence=persistence,
        )

    def _persist(
        self,
        request: OhlcvPipelineRequest,
        outcomes: Sequence[ProviderCollectionOutcome],
        reconciliation: ReconciliationResult | None,
    ) -> OhlcvPipelinePersistenceSummary:
        repository = self._repository
        if repository is None or not hasattr(repository, "transaction"):
            raise TypeError(
                "pipeline persistence requires a unified transactional PostgreSQL repository"
            )
        provider_summaries: list[tuple[str, OfflinePersistenceSummary]] = []
        reconciliation_summary: ReconciliationPersistenceSummary | None = None
        try:
            with repository.transaction():
                for outcome in outcomes:
                    if outcome.error is not None or outcome.validation_report is None:
                        continue
                    summary = persist_offline_ohlcv_validation_flow(
                        outcome.observations,
                        outcome.bars,
                        outcome.validation_report,
                        repository=repository,
                        manage_transaction=False,
                    )
                    provider_summaries.append((outcome.provider_name, summary))
                if reconciliation is not None:
                    reconciliation_summary = persist_reconciliation_result(
                        reconciliation,
                        repository=repository,  # type: ignore[arg-type]
                        manage_transaction=False,
                    )
        except Exception as exc:
            failure = OhlcvPipelineFailure(
                provider_name=None,
                stage=PipelineStage.PERSISTENCE,
                error_type=type(exc).__name__,
                message=str(exc),
            )
            raise OhlcvPipelineError(failure, outcomes) from exc
        return OhlcvPipelinePersistenceSummary(
            provider_summaries=tuple(provider_summaries),
            reconciliation_summary=reconciliation_summary,
        )


def run_ohlcv_pipeline(
    request: OhlcvPipelineRequest,
    *,
    providers: Sequence[MarketDataProvider],
    validation_config: OhlcvValidationConfig | None = None,
    reconciliation_config: OhlcvReconciliationConfig | None = None,
    repository: object | None = None,
    clock: Callable[[], datetime] | None = None,
) -> OhlcvPipelineResult:
    """Convenience entry point for one injected public OHLCV pipeline run."""

    return OhlcvPipeline(
        providers,
        validation_config=validation_config,
        reconciliation_config=reconciliation_config,
        repository=repository,
        clock=clock,
    ).run(request)


__all__ = [
    "OhlcvPipeline",
    "OhlcvPipelineError",
    "OhlcvPipelineFailure",
    "OhlcvPipelinePersistenceSummary",
    "OhlcvPipelineRequest",
    "OhlcvPipelineResult",
    "PipelineStage",
    "PipelineStatus",
    "ProviderCollectionOutcome",
    "run_ohlcv_pipeline",
]
