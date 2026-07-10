"""Deterministic multi-alpha combination and explicit conflict preservation."""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from enum import Enum
from typing import Mapping, Sequence

from secure_eval_wrapper.signals.models import (
    CombinationOutcome,
    SignalContribution,
    SignalDirection,
    ThresholdedAlphaValue,
)


class WeightingMode(str, Enum):
    EQUAL = "equal"
    STATIC = "static"
    NORMALIZED_SCORE = "normalized_score"


class InsufficientCoveragePolicy(str, Enum):
    FLAT = "flat"
    SKIP = "skip"


@dataclass(frozen=True)
class CombinationConfig:
    weighting: WeightingMode = WeightingMode.EQUAL
    static_weights: Mapping[str, Decimal] = field(default_factory=dict)
    expected_alpha_ids: tuple[str, ...] = ()
    minimum_contributors: int = 1
    minimum_coverage_ratio: Decimal = Decimal(1)
    decision_threshold: Decimal = Decimal(0)
    insufficient_coverage_policy: InsufficientCoveragePolicy = InsufficientCoveragePolicy.FLAT

    def __post_init__(self) -> None:
        object.__setattr__(self, "weighting", WeightingMode(self.weighting))
        object.__setattr__(self, "insufficient_coverage_policy", InsufficientCoveragePolicy(self.insufficient_coverage_policy))
        if self.minimum_contributors < 1:
            raise ValueError("minimum_contributors must be positive")
        if not Decimal(0) <= self.minimum_coverage_ratio <= Decimal(1):
            raise ValueError("minimum_coverage_ratio must be in [0, 1]")
        if not Decimal(0) <= self.decision_threshold <= Decimal(1):
            raise ValueError("decision_threshold must be in [0, 1]")
        if len(set(self.expected_alpha_ids)) != len(self.expected_alpha_ids):
            raise ValueError("expected_alpha_ids must be unique")
        for key, weight in self.static_weights.items():
            if not key or not isinstance(weight, Decimal) or not weight.is_finite() or weight <= 0:
                raise ValueError("static weights require non-empty identities and positive finite Decimal values")
        if self.weighting is WeightingMode.STATIC and not self.static_weights:
            raise ValueError("static weighting requires explicit static_weights")

    def as_dict(self):
        return {
            "weighting": self.weighting.value,
            "static_weights": dict(sorted(self.static_weights.items())),
            "expected_alpha_ids": tuple(sorted(self.expected_alpha_ids)),
            "minimum_contributors": self.minimum_contributors,
            "minimum_coverage_ratio": self.minimum_coverage_ratio,
            "decision_threshold": self.decision_threshold,
            "insufficient_coverage_policy": self.insufficient_coverage_policy.value,
        }


def alpha_identity(item: ThresholdedAlphaValue) -> str:
    value = item.ranked.alpha_value
    return f"{value.alpha_name}@{value.alpha_version}"


def combine_thresholded_values(
    values: Sequence[ThresholdedAlphaValue],
    config: CombinationConfig,
) -> CombinationOutcome:
    ordered = tuple(sorted(values, key=lambda item: alpha_identity(item)))
    identities = tuple(alpha_identity(item) for item in ordered)
    if len(set(identities)) != len(identities):
        raise ValueError("combination received duplicate alpha identities")
    expected = config.expected_alpha_ids or identities
    if any(identity not in expected for identity in identities):
        raise ValueError("combination received an alpha outside expected_alpha_ids")
    expected_count = len(expected)
    contributor_count = len(ordered)
    coverage = Decimal(contributor_count) / Decimal(expected_count) if expected_count else Decimal(0)
    insufficient = contributor_count < config.minimum_contributors or coverage < config.minimum_coverage_ratio

    contributions = []
    weighted_raw = Decimal(0)
    weighted_normalized = Decimal(0)
    total_effective_weight = Decimal(0)
    directions = []
    for item in ordered:
        identity = alpha_identity(item)
        base_weight = config.static_weights.get(identity, Decimal(1))
        if config.weighting is WeightingMode.STATIC and identity not in config.static_weights:
            raise ValueError(f"missing static weight for {identity}")
        effective = base_weight
        if config.weighting is WeightingMode.NORMALIZED_SCORE:
            effective *= abs(item.ranked.normalized_score)
        sign = Decimal(1) if item.direction is SignalDirection.LONG else Decimal(-1) if item.direction is SignalDirection.SHORT else Decimal(0)
        raw = item.ranked.alpha_value.raw_score
        assert raw is not None
        signed_normalized = sign * abs(item.ranked.normalized_score)
        signed_raw = sign * abs(raw)
        signed_contribution = effective * signed_normalized
        weighted_raw += effective * signed_raw
        weighted_normalized += signed_contribution
        total_effective_weight += effective
        if item.direction is not SignalDirection.FLAT:
            directions.append(item.direction)
        contributions.append(
            SignalContribution(
                alpha_value_id=item.ranked.alpha_value.alpha_value_id,
                alpha_id=item.ranked.alpha_value.alpha_id,
                alpha_name=item.ranked.alpha_value.alpha_name,
                alpha_version=item.ranked.alpha_value.alpha_version,
                direction=item.direction,
                raw_score=raw,
                normalized_score=item.ranked.normalized_score,
                configured_weight=base_weight,
                effective_weight=effective,
                signed_contribution=signed_contribution,
            )
        )
    aggregate_raw = Decimal(0) if total_effective_weight == 0 else weighted_raw / total_effective_weight
    aggregate = Decimal(0) if total_effective_weight == 0 else weighted_normalized / total_effective_weight
    aggregate = max(Decimal(-1), min(Decimal(1), aggregate))
    if insufficient:
        direction = SignalDirection.FLAT
    elif aggregate >= config.decision_threshold and aggregate > 0:
        direction = SignalDirection.LONG
    elif aggregate <= -config.decision_threshold and aggregate < 0:
        direction = SignalDirection.SHORT
    else:
        direction = SignalDirection.FLAT
    agreeing = sum(item is direction for item in directions) if direction is not SignalDirection.FLAT else 0
    agreement = Decimal(agreeing) / Decimal(contributor_count) if contributor_count else Decimal(0)
    conflict = SignalDirection.LONG in directions and SignalDirection.SHORT in directions
    return CombinationOutcome(
        direction=direction,
        raw_score=aggregate_raw,
        normalized_score=aggregate,
        contributions=tuple(contributions),
        contributor_count=contributor_count,
        expected_contributor_count=expected_count,
        coverage_ratio=coverage,
        agreement_ratio=agreement,
        conflict=conflict,
        insufficient_coverage=insufficient,
        skipped=insufficient and config.insufficient_coverage_policy is InsufficientCoveragePolicy.SKIP,
    )


__all__ = [
    "CombinationConfig",
    "InsufficientCoveragePolicy",
    "WeightingMode",
    "alpha_identity",
    "combine_thresholded_values",
]
