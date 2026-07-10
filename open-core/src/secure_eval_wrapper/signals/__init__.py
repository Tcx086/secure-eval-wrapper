"""Standardized deterministic research-signal generation."""

from secure_eval_wrapper.signals.combination import (
    CombinationConfig,
    InsufficientCoveragePolicy,
    WeightingMode,
    combine_thresholded_values,
)
from secure_eval_wrapper.signals.confidence import ConfidenceConfig, score_confidence
from secure_eval_wrapper.signals.models import (
    RankMethod,
    RankOrder,
    RankedAlphaValue,
    RankingConfig,
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
    apply_threshold_policy,
)

__all__ = [
    "AbsoluteThreshold",
    "CombinationConfig",
    "ConfidenceConfig",
    "InsufficientCoveragePolicy",
    "PercentileThreshold",
    "RankMethod",
    "RankOrder",
    "RankedAlphaValue",
    "RankingConfig",
    "SignalDirection",
    "SignalPipeline",
    "SignalPipelineError",
    "SignalPipelineRequest",
    "SignalRun",
    "SignalRunStatus",
    "StandardizedSignal",
    "TopBottomNThreshold",
    "WeightingMode",
    "apply_threshold_policy",
    "combine_thresholded_values",
    "rank_alpha_values",
    "score_confidence",
]
