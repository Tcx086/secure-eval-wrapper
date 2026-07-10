"""Explicit policies that convert ranked research scores into long/short/flat directions."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Sequence

from secure_eval_wrapper.data_collection.hashing import sha256_payload
from secure_eval_wrapper.signals.models import (
    RankedAlphaValue,
    SignalDirection,
    ThresholdedAlphaValue,
)


@dataclass(frozen=True)
class AbsoluteThreshold:
    positive: Decimal
    negative: Decimal

    def __post_init__(self) -> None:
        if not self.positive.is_finite() or not self.negative.is_finite():
            raise ValueError("absolute thresholds must be finite")
        if self.positive <= 0 or self.negative >= 0 or self.positive <= self.negative:
            raise ValueError("absolute threshold requires positive > 0 and negative < 0")

    def as_dict(self):
        return {"policy": "absolute", "positive": self.positive, "negative": self.negative}


@dataclass(frozen=True)
class PercentileThreshold:
    upper: Decimal
    lower: Decimal

    def __post_init__(self) -> None:
        if not Decimal(0) <= self.lower < self.upper <= Decimal(1):
            raise ValueError("percentile thresholds require 0 <= lower < upper <= 1")

    def as_dict(self):
        return {"policy": "percentile", "upper": self.upper, "lower": self.lower}


@dataclass(frozen=True)
class TopBottomNThreshold:
    top_n: int
    bottom_n: int

    def __post_init__(self) -> None:
        if isinstance(self.top_n, bool) or isinstance(self.bottom_n, bool):
            raise ValueError("top_n and bottom_n must be integers")
        if self.top_n < 0 or self.bottom_n < 0 or self.top_n + self.bottom_n == 0:
            raise ValueError("top/bottom N requires at least one positive side")

    def as_dict(self):
        return {"policy": "top_bottom_n", "top_n": self.top_n, "bottom_n": self.bottom_n}


ThresholdPolicy = AbsoluteThreshold | PercentileThreshold | TopBottomNThreshold


def threshold_config_sha256(policy: ThresholdPolicy) -> str:
    return sha256_payload(policy.as_dict())


def apply_threshold_policy(
    values: Sequence[RankedAlphaValue],
    policy: ThresholdPolicy,
) -> tuple[ThresholdedAlphaValue, ...]:
    digest = threshold_config_sha256(policy)
    groups = {}
    for value in values:
        alpha = value.alpha_value
        groups.setdefault((alpha.timestamp_utc, alpha.alpha_id, alpha.alpha_version, alpha.horizon), []).append(value)
    outputs = []
    for key in sorted(groups, key=lambda item: (item[0], str(item[1]), item[2], item[3])):
        group = sorted(groups[key], key=lambda item: (item.rank, item.alpha_value.symbol))
        if isinstance(policy, TopBottomNThreshold):
            top_ids = {item.alpha_value.alpha_value_id for item in group[: policy.top_n]}
            bottom_ids = {item.alpha_value.alpha_value_id for item in group[len(group) - policy.bottom_n :]} if policy.bottom_n else set()
        else:
            top_ids = bottom_ids = set()
        for item in group:
            if isinstance(policy, AbsoluteThreshold):
                score = item.alpha_value.raw_score
                assert score is not None
                direction = SignalDirection.LONG if score >= policy.positive else SignalDirection.SHORT if score <= policy.negative else SignalDirection.FLAT
            elif isinstance(policy, PercentileThreshold):
                direction = SignalDirection.LONG if item.percentile >= policy.upper else SignalDirection.SHORT if item.percentile <= policy.lower else SignalDirection.FLAT
            else:
                identity = item.alpha_value.alpha_value_id
                in_top = identity in top_ids
                in_bottom = identity in bottom_ids
                direction = SignalDirection.FLAT if in_top and in_bottom else SignalDirection.LONG if in_top else SignalDirection.SHORT if in_bottom else SignalDirection.FLAT
            outputs.append(ThresholdedAlphaValue(item, direction, digest))
    return tuple(sorted(outputs, key=lambda item: (item.ranked.alpha_value.timestamp_utc, item.ranked.alpha_value.alpha_name, item.ranked.alpha_value.symbol)))


__all__ = [
    "AbsoluteThreshold",
    "PercentileThreshold",
    "ThresholdPolicy",
    "TopBottomNThreshold",
    "apply_threshold_policy",
    "threshold_config_sha256",
]
