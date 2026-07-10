"""Deterministic cross-sectional ranking with average-rank economic tie semantics."""

from __future__ import annotations

from collections import defaultdict
from decimal import Decimal
from typing import Sequence

from secure_eval_wrapper.alpha.models import AlphaValue
from secure_eval_wrapper.signals.models import RankOrder, RankedAlphaValue, RankingConfig


def rank_alpha_values(values: Sequence[AlphaValue], config: RankingConfig) -> tuple[RankedAlphaValue, ...]:
    """Rank valid values without assigning different economic ranks to equal scores."""

    groups = defaultdict(list)
    for value in values:
        if not isinstance(value, AlphaValue):
            raise TypeError("ranking accepts AlphaValue records only")
        if not value.valid or not value.warmup_complete:
            continue
        assert value.raw_score is not None
        groups[(value.timestamp_utc, value.alpha_id, value.alpha_version, value.horizon)].append(value)

    ranked = []
    for key in sorted(groups, key=lambda item: (item[0], str(item[1]), item[2], item[3])):
        group = groups[key]
        reverse = config.order is RankOrder.DESCENDING
        ordered = sorted(group, key=lambda item: item.series_identity.series_identity_sha256)
        ordered = sorted(ordered, key=lambda item: item.raw_score, reverse=reverse)
        size = len(ordered)
        max_abs = max((abs(item.raw_score or Decimal(0)) for item in ordered), default=Decimal(0))
        position = 0
        while position < size:
            end = position + 1
            while end < size and ordered[end].raw_score == ordered[position].raw_score:
                end += 1
            average_rank = (Decimal(position + 1) + Decimal(end)) / Decimal(2)
            percentile = Decimal("0.5") if size == 1 else (Decimal(size) - average_rank) / Decimal(size - 1)
            for value in ordered[position:end]:
                normalized = Decimal(0) if max_abs == 0 else (value.raw_score or Decimal(0)) / max_abs
                ranked.append(RankedAlphaValue(value, average_rank, percentile, normalized))
            position = end
    return tuple(sorted(ranked, key=lambda item: (
        item.alpha_value.timestamp_utc,
        item.alpha_value.alpha_name,
        item.alpha_value.symbol,
        item.alpha_value.series_identity.series_identity_sha256,
    )))


__all__ = ["rank_alpha_values"]
