"""Transparent deterministic confidence scoring for research signals."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from secure_eval_wrapper.signals.models import CombinationOutcome, SignalDirection


@dataclass(frozen=True)
class ConfidenceConfig:
    score_weight: Decimal = Decimal("0.4")
    agreement_weight: Decimal = Decimal("0.3")
    coverage_weight: Decimal = Decimal("0.2")
    distance_weight: Decimal = Decimal("0.1")
    flat_confidence: Decimal = Decimal(0)

    def __post_init__(self) -> None:
        weights = (self.score_weight, self.agreement_weight, self.coverage_weight, self.distance_weight)
        if any(not item.is_finite() or item < 0 for item in weights):
            raise ValueError("confidence weights must be finite and non-negative")
        if sum(weights, Decimal(0)) <= 0:
            raise ValueError("at least one confidence component weight must be positive")
        if not Decimal(0) <= self.flat_confidence <= Decimal(1):
            raise ValueError("flat_confidence must be in [0, 1]")

    def as_dict(self):
        return {
            "formula": "weighted_mean(abs_score, agreement_ratio, coverage_ratio, distance_beyond_threshold)",
            "score_weight": self.score_weight,
            "agreement_weight": self.agreement_weight,
            "coverage_weight": self.coverage_weight,
            "distance_weight": self.distance_weight,
            "flat_confidence": self.flat_confidence,
            "probability_of_profit": False,
        }


def score_confidence(
    outcome: CombinationOutcome,
    config: ConfidenceConfig,
    *,
    decision_threshold: Decimal,
) -> Decimal:
    """Return a bounded heuristic confidence score, never a profit probability."""

    if outcome.insufficient_coverage:
        return Decimal(0)
    if outcome.direction is SignalDirection.FLAT:
        return config.flat_confidence
    absolute_score = min(Decimal(1), abs(outcome.normalized_score))
    if decision_threshold >= 1:
        distance = Decimal(0)
    else:
        distance = max(Decimal(0), absolute_score - decision_threshold) / (Decimal(1) - decision_threshold)
    numerator = (
        config.score_weight * absolute_score
        + config.agreement_weight * outcome.agreement_ratio
        + config.coverage_weight * outcome.coverage_ratio
        + config.distance_weight * distance
    )
    denominator = config.score_weight + config.agreement_weight + config.coverage_weight + config.distance_weight
    return max(Decimal(0), min(Decimal(1), numerator / denominator))


__all__ = ["ConfidenceConfig", "score_confidence"]
