"""Explicit policies that convert ranked research scores into long/short/flat directions."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import Enum
from typing import Sequence

from secure_eval_wrapper.data_collection.hashing import sha256_payload
from secure_eval_wrapper.signals.models import ComponentDisposition, RankedAlphaValue, SignalDirection, ThresholdedAlphaValue


class TopBottomOverlapPolicy(str, Enum):
    FAIL = "fail"
    SKIP_GROUP = "skip_group"
    FORCE_FLAT = "force_flat"


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
    overlap_policy: TopBottomOverlapPolicy = TopBottomOverlapPolicy.FAIL

    def __post_init__(self) -> None:
        if isinstance(self.top_n, bool) or isinstance(self.bottom_n, bool):
            raise ValueError("top_n and bottom_n must be integers")
        if self.top_n < 0 or self.bottom_n < 0 or self.top_n + self.bottom_n == 0:
            raise ValueError("top/bottom N requires at least one positive side")
        object.__setattr__(self, "overlap_policy", TopBottomOverlapPolicy(self.overlap_policy))

    def as_dict(self):
        return {"policy": "top_bottom_n", "top_n": self.top_n, "bottom_n": self.bottom_n, "overlap_policy": self.overlap_policy.value}


ThresholdPolicy = AbsoluteThreshold | PercentileThreshold | TopBottomNThreshold


def threshold_config_sha256(policy: ThresholdPolicy) -> str:
    return sha256_payload(policy.as_dict())


def _top_bottom_ids(group: list[RankedAlphaValue], policy: TopBottomNThreshold):
    top_ids: set[object] = set()
    bottom_ids: set[object] = set()
    if policy.top_n:
        cutoff = group[min(policy.top_n, len(group)) - 1].rank
        top_ids = {item.alpha_value.alpha_value_id for item in group if item.rank <= cutoff}
    if policy.bottom_n:
        boundary = group[max(0, len(group) - policy.bottom_n)].rank
        bottom_ids = {item.alpha_value.alpha_value_id for item in group if item.rank >= boundary}
    return top_ids, bottom_ids


def apply_threshold_policy(values: Sequence[RankedAlphaValue], policy: ThresholdPolicy) -> tuple[ThresholdedAlphaValue, ...]:
    digest = threshold_config_sha256(policy)
    groups = {}
    for value in values:
        alpha = value.alpha_value
        groups.setdefault((alpha.timestamp_utc, alpha.alpha_id, alpha.alpha_version, alpha.horizon), []).append(value)
    outputs = []
    for key in sorted(groups, key=lambda item: (item[0], str(item[1]), item[2], item[3])):
        group = sorted(groups[key], key=lambda item: (item.rank, item.alpha_value.series_identity.series_identity_sha256))
        top_ids: set[object] = set()
        bottom_ids: set[object] = set()
        overlap: set[object] = set()
        if isinstance(policy, TopBottomNThreshold):
            top_ids, bottom_ids = _top_bottom_ids(group, policy)
            overlap = top_ids & bottom_ids
            oversized = policy.top_n + policy.bottom_n > len(group)
            if oversized or overlap:
                reason = f"top_bottom_overlap:top_n={policy.top_n}:bottom_n={policy.bottom_n}:eligible={len(group)}"
                if policy.overlap_policy is TopBottomOverlapPolicy.FAIL:
                    raise ValueError(reason)
                if policy.overlap_policy is TopBottomOverlapPolicy.SKIP_GROUP:
                    continue
        for item in group:
            disposition = ComponentDisposition.CONTRIBUTED
            resolution_reason = None
            if isinstance(policy, AbsoluteThreshold):
                score = item.alpha_value.raw_score
                assert score is not None
                direction = SignalDirection.LONG if score >= policy.positive else SignalDirection.SHORT if score <= policy.negative else SignalDirection.FLAT
            elif isinstance(policy, PercentileThreshold):
                direction = SignalDirection.LONG if item.percentile >= policy.upper else SignalDirection.SHORT if item.percentile <= policy.lower else SignalDirection.FLAT
            else:
                identity = item.alpha_value.alpha_value_id
                if identity in overlap:
                    direction = SignalDirection.FLAT
                    disposition = ComponentDisposition.OVERLAP_FORCED_FLAT
                    resolution_reason = f"top_bottom_overlap_force_flat:eligible={len(group)}"
                else:
                    direction = SignalDirection.LONG if identity in top_ids else SignalDirection.SHORT if identity in bottom_ids else SignalDirection.FLAT
            if direction is SignalDirection.FLAT and disposition is ComponentDisposition.CONTRIBUTED:
                disposition = ComponentDisposition.FLAT
            outputs.append(ThresholdedAlphaValue(item, direction, digest, disposition, resolution_reason))
    return tuple(sorted(outputs, key=lambda item: (
        item.ranked.alpha_value.timestamp_utc,
        item.ranked.alpha_value.alpha_name,
        item.ranked.alpha_value.series_identity.series_identity_sha256,
    )))


__all__ = [
    "AbsoluteThreshold",
    "PercentileThreshold",
    "ThresholdPolicy",
    "TopBottomNThreshold",
    "TopBottomOverlapPolicy",
    "apply_threshold_policy",
    "threshold_config_sha256",
]
