"""Deterministic alpha-to-signal research pipeline."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from decimal import Decimal
from typing import Callable, Mapping, Protocol, Sequence
from uuid import NAMESPACE_URL, UUID, uuid5

from secure_eval_wrapper.alpha.models import AlphaRun, AlphaRunStatus, AlphaValue
from secure_eval_wrapper.data_collection.hashing import sha256_payload
from secure_eval_wrapper.data_collection.time_utils import require_utc_datetime
from secure_eval_wrapper.signals.combination import (
    CombinationConfig,
    alpha_identity,
    combine_thresholded_values,
)
from secure_eval_wrapper.signals.confidence import ConfidenceConfig, score_confidence
from secure_eval_wrapper.signals.models import (
    CombinationOutcome,
    RankingConfig,
    SignalContribution,
    SignalDirection,
    SignalFailure,
    SignalPipelineError,
    SignalPipelineResult,
    SignalRun,
    SignalRunStatus,
    StandardizedSignal,
)
from secure_eval_wrapper.signals.ranking import rank_alpha_values
from secure_eval_wrapper.signals.thresholding import (
    ThresholdPolicy,
    apply_threshold_policy,
)


class SignalPersistenceRepository(Protocol):
    def transaction(self): ...
    def record_signal_run(self, run: SignalRun): ...
    def record_signal(self, signal: StandardizedSignal): ...


@dataclass(frozen=True)
class SignalPipelineRequest:
    signal_run_id: UUID
    alpha_run_ids: tuple[UUID, ...]
    symbol_universe: tuple[str, ...]
    window_start_utc: datetime
    window_end_utc: datetime
    ranking_config: RankingConfig
    threshold_policy: ThresholdPolicy
    combination_config: CombinationConfig | None = None
    confidence_config: ConfidenceConfig = ConfidenceConfig()
    fail_fast: bool = True
    persistence_enabled: bool = False

    def __post_init__(self) -> None:
        start = require_utc_datetime(self.window_start_utc, field_name="signal window_start_utc")
        end = require_utc_datetime(self.window_end_utc, field_name="signal window_end_utc")
        if start >= end:
            raise ValueError("signal window must be non-empty and half-open")
        if not self.alpha_run_ids or len(set(self.alpha_run_ids)) != len(self.alpha_run_ids):
            raise ValueError("alpha_run_ids must be non-empty and unique")
        if not self.symbol_universe or len(set(self.symbol_universe)) != len(self.symbol_universe):
            raise ValueError("symbol_universe must be non-empty and unique")
        if any(not symbol.strip() for symbol in self.symbol_universe):
            raise ValueError("symbol_universe identities must be non-empty")


def _contribution_dict(item: SignalContribution) -> dict[str, object]:
    return {
        "alpha_value_id": item.alpha_value_id,
        "alpha_id": item.alpha_id,
        "alpha_name": item.alpha_name,
        "alpha_version": item.alpha_version,
        "direction": item.direction.value,
        "raw_score": item.raw_score,
        "normalized_score": item.normalized_score,
        "configured_weight": item.configured_weight,
        "effective_weight": item.effective_weight,
        "signed_contribution": item.signed_contribution,
    }


class SignalPipeline:
    def __init__(
        self,
        *,
        repository: SignalPersistenceRepository | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._repository = repository
        self._clock = clock or (lambda: datetime.now(timezone.utc))

    def _now(self) -> datetime:
        return require_utc_datetime(self._clock(), field_name="SignalPipeline clock")

    def run(
        self,
        request: SignalPipelineRequest,
        alpha_runs: Sequence[AlphaRun],
        alpha_values: Sequence[AlphaValue],
    ) -> SignalPipelineResult:
        started_at = self._now()
        failures = []
        run_by_id = {item.alpha_run_id: item for item in alpha_runs}
        try:
            if set(run_by_id) != set(request.alpha_run_ids):
                raise ValueError("alpha_runs must exactly match request alpha_run_ids")
            identities = tuple(sorted({f"{item.alpha_name}@{item.alpha_version}" for item in alpha_runs}))
            if len(identities) != len(alpha_runs):
                raise ValueError("duplicate alpha name/version runs are not compatible")
            if any(item.status not in (AlphaRunStatus.COMPLETED, AlphaRunStatus.PARTIAL) for item in alpha_runs):
                raise ValueError("signal generation requires completed or explicit partial AlphaRun inputs")
            if request.combination_config is None and len(identities) != 1:
                raise ValueError("multi-alpha signal generation requires an explicit combination_config")
            effective_combination = request.combination_config
            if effective_combination is not None and not effective_combination.expected_alpha_ids:
                effective_combination = replace(effective_combination, expected_alpha_ids=identities)
            if effective_combination is not None and set(effective_combination.expected_alpha_ids) != set(identities):
                raise ValueError("combination expected_alpha_ids must match supplied alpha runs")
        except Exception as exc:
            failure = SignalFailure("validation", type(exc).__name__, str(exc))
            raise SignalPipelineError(failure) from exc

        eligible = []
        for value in alpha_values:
            try:
                run = run_by_id.get(value.alpha_run_id)
                if run is None:
                    raise ValueError("AlphaValue references an undeclared alpha run")
                if value.alpha_id != run.alpha_id or value.alpha_version != run.alpha_version:
                    raise ValueError("AlphaValue identity conflicts with AlphaRun lineage")
                if value.symbol not in request.symbol_universe:
                    raise ValueError("AlphaValue symbol is outside the signal universe")
                if not request.window_start_utc <= value.timestamp_utc < request.window_end_utc:
                    raise ValueError("AlphaValue timestamp is outside the signal window")
                eligible.append(value)
            except Exception as exc:
                failure = SignalFailure("input_validation", type(exc).__name__, str(exc), getattr(value, "alpha_value_id", None))
                if request.fail_fast:
                    raise SignalPipelineError(failure) from exc
                failures.append(failure)

        ranked = rank_alpha_values(eligible, request.ranking_config)
        thresholded = apply_threshold_policy(ranked, request.threshold_policy)
        data_sha256 = sha256_payload(tuple(sorted({item.input_data_sha256 for item in alpha_runs})))
        code_sha256 = sha256_payload(tuple(sorted({item.implementation_sha256 for item in alpha_runs})))
        ranking_map = request.ranking_config.as_dict()
        threshold_map = request.threshold_policy.as_dict()
        combination_map = effective_combination.as_dict() if effective_combination is not None else {"mode": "single_alpha"}
        confidence_map = request.confidence_config.as_dict()
        config_sha256 = sha256_payload(
            {
                "ranking": ranking_map,
                "threshold": threshold_map,
                "combination": combination_map,
                "confidence": confidence_map,
                "symbol_universe": tuple(sorted(request.symbol_universe)),
                "window_start_utc": request.window_start_utc,
                "window_end_utc": request.window_end_utc,
            }
        )

        grouped = defaultdict(list)
        for item in thresholded:
            value = item.ranked.alpha_value
            grouped[(value.timestamp_utc, value.symbol, value.horizon)].append(item)
        signals = []
        skipped_count = 0
        for group_key in sorted(grouped, key=lambda item: (item[0], item[1], item[2])):
            timestamp, symbol, horizon = group_key
            components = grouped[group_key]
            try:
                if effective_combination is None:
                    component = components[0]
                    value = component.ranked.alpha_value
                    direction = component.direction
                    signed_normalized = (
                        abs(component.ranked.normalized_score)
                        if direction is SignalDirection.LONG
                        else -abs(component.ranked.normalized_score)
                        if direction is SignalDirection.SHORT
                        else Decimal(0)
                    )
                    contribution = SignalContribution(
                        alpha_value_id=value.alpha_value_id,
                        alpha_id=value.alpha_id,
                        alpha_name=value.alpha_name,
                        alpha_version=value.alpha_version,
                        direction=direction,
                        raw_score=value.raw_score or Decimal(0),
                        normalized_score=component.ranked.normalized_score,
                        configured_weight=Decimal(1),
                        effective_weight=Decimal(1),
                        signed_contribution=signed_normalized,
                    )
                    outcome = CombinationOutcome(
                        direction=direction,
                        raw_score=value.raw_score or Decimal(0),
                        normalized_score=signed_normalized,
                        contributions=(contribution,),
                        contributor_count=1,
                        expected_contributor_count=1,
                        coverage_ratio=Decimal(1),
                        agreement_ratio=Decimal(1) if direction is not SignalDirection.FLAT else Decimal(0),
                        conflict=False,
                        insufficient_coverage=False,
                    )
                    rank = component.ranked.rank
                    percentile = component.ranked.percentile
                    decision_threshold = Decimal(0)
                else:
                    outcome = combine_thresholded_values(components, effective_combination)
                    if outcome.skipped:
                        skipped_count += 1
                        continue
                    rank = None
                    percentile = (
                        sum((item.ranked.percentile for item in components), Decimal(0)) / Decimal(len(components))
                        if components
                        else None
                    )
                    decision_threshold = effective_combination.decision_threshold
                confidence = score_confidence(outcome, request.confidence_config, decision_threshold=decision_threshold)
                alpha_refs = tuple(sorted(f"{item.alpha_id}@{item.alpha_version}" for item in outcome.contributions))
                alpha_value_ids = tuple(item.alpha_value_id for item in outcome.contributions)
                signal_id = uuid5(NAMESPACE_URL, f"standardized-signal:{request.signal_run_id}:{symbol}:{timestamp.isoformat()}:{horizon}")
                signals.append(
                    StandardizedSignal(
                        signal_id=signal_id,
                        signal_run_id=request.signal_run_id,
                        alpha_ids_versions=alpha_refs,
                        alpha_run_ids=tuple(sorted(request.alpha_run_ids, key=str)),
                        symbol=symbol,
                        timestamp_utc=timestamp,
                        direction=outcome.direction,
                        raw_score=outcome.raw_score,
                        normalized_score=outcome.normalized_score,
                        rank=rank,
                        percentile=percentile,
                        confidence=confidence,
                        horizon=horizon,
                        source_alpha_value_ids=alpha_value_ids,
                        config_sha256=config_sha256,
                        data_sha256=data_sha256,
                        code_sha256=code_sha256,
                        provenance={
                            "research_output_only": True,
                            "ranking": ranking_map,
                            "threshold": threshold_map,
                            "combination": combination_map,
                            "confidence_model": confidence_map,
                            "contributions": tuple(_contribution_dict(item) for item in outcome.contributions),
                            "coverage_ratio": outcome.coverage_ratio,
                            "agreement_ratio": outcome.agreement_ratio,
                            "conflict": outcome.conflict,
                            "insufficient_coverage": outcome.insufficient_coverage,
                        },
                    )
                )
            except Exception as exc:
                failure = SignalFailure("generation", type(exc).__name__, str(exc))
                if request.fail_fast:
                    raise SignalPipelineError(failure) from exc
                failures.append(failure)

        signals.sort(key=lambda item: (item.timestamp_utc, item.symbol, str(item.signal_id)))
        status = SignalRunStatus.PARTIAL if failures and signals else SignalRunStatus.FAILED if failures else SignalRunStatus.COMPLETED
        run = SignalRun(
            signal_run_id=request.signal_run_id,
            alpha_run_ids=tuple(sorted(request.alpha_run_ids, key=str)),
            symbol_universe=tuple(sorted(request.symbol_universe)),
            window_start_utc=request.window_start_utc,
            window_end_utc=request.window_end_utc,
            ranking_config=ranking_map,
            threshold_config=threshold_map,
            combination_config=combination_map,
            config_sha256=config_sha256,
            code_sha256=code_sha256,
            data_sha256=data_sha256,
            status=status,
            output_count=len(signals),
            long_count=sum(item.direction is SignalDirection.LONG for item in signals),
            short_count=sum(item.direction is SignalDirection.SHORT for item in signals),
            flat_count=sum(item.direction is SignalDirection.FLAT for item in signals),
            skipped_count=skipped_count,
            failure_count=len(failures),
            started_at_utc=started_at,
            completed_at_utc=self._now(),
            metadata={"persistence_enabled": request.persistence_enabled, "execution_output": False},
        )
        result = SignalPipelineResult(run=run, signals=tuple(signals), failures=tuple(failures))
        if request.persistence_enabled:
            repository = self._repository
            if repository is None or not hasattr(repository, "transaction"):
                failure = SignalFailure("persistence", "TypeError", "persistence requires an injected transactional PostgreSQL repository")
                raise SignalPipelineError(failure)
            try:
                with repository.transaction():
                    repository.record_signal_run(run)
                    for signal in signals:
                        repository.record_signal(signal)
            except Exception as exc:
                failure = SignalFailure("persistence", type(exc).__name__, str(exc))
                raise SignalPipelineError(failure) from exc
        return result


__all__ = ["SignalPersistenceRepository", "SignalPipeline", "SignalPipelineRequest"]
