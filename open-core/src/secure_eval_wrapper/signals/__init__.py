"""Standardized deterministic research-signal generation."""

from secure_eval_wrapper.signals.combination import (
    CombinationConfig,
    InsufficientCoveragePolicy,
    WeightingMode,
    combine_thresholded_values,
)
from secure_eval_wrapper.signals.confidence import ConfidenceConfig, score_confidence
from secure_eval_wrapper.signals.models import (
    ComponentDisposition,
    RankMethod,
    RankOrder,
    RankedAlphaValue,
    RankingConfig,
    SignalComponent,
    SignalDirection,
    SignalPipelineError,
    SignalRun,
    SignalRunStatus,
    StandardizedSignal,
)
from secure_eval_wrapper.signals.pipeline import SignalPipeline, SignalPipelineRequest
from secure_eval_wrapper.signals.ranking import rank_alpha_values
from secure_eval_wrapper.signals.thresholding import (
    AbsoluteThreshold,
    PercentileThreshold,
    TopBottomNThreshold,
    TopBottomOverlapPolicy,
    apply_threshold_policy,
)

__all__ = [
    "AbsoluteThreshold",
    "CombinationConfig",
    "ConfidenceConfig",
    "ComponentDisposition",
    "InsufficientCoveragePolicy",
    "PercentileThreshold",
    "RankMethod",
    "RankOrder",
    "RankedAlphaValue",
    "RankingConfig",
    "SignalComponent",
    "SignalDirection",
    "SignalPipeline",
    "SignalPipelineError",
    "SignalPipelineRequest",
    "SignalRun",
    "SignalRunStatus",
    "StandardizedSignal",
    "TopBottomNThreshold",
    "TopBottomOverlapPolicy",
    "WeightingMode",
    "apply_threshold_policy",
    "combine_thresholded_values",
    "rank_alpha_values",
    "score_confidence",
]
