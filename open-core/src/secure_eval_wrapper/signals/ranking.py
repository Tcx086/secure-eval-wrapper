"""Deterministic cross-sectional ranking within one timestamp and alpha identity."""

from __future__ import annotations

from collections import defaultdict
from decimal import Decimal
from typing import Sequence

from secure_eval_wrapper.alpha.models import AlphaValue
from secure_eval_wrapper.signals.models import (
    RankMethod,
    RankOrder,
    RankedAlphaValue,
    RankingConfig,
)


def rank_alpha_values(
    values: Sequence[AlphaValue],
    config: RankingConfig,
) -> tuple[RankedAlphaValue, ...]:
    """Rank valid, warmup-complete values without crossing timestamps or horizons."""

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
        ordered = sorted(group, key=lambda item: item.symbol)
        ordered = sorted(ordered, key=lambda item: item.raw_score, reverse=reverse)
        max_abs = max((abs(item.raw_score or Decimal(0)) for item in ordered), default=Decimal(0))
        dense_rank = 0
        previous_score = None
        rank_rows = []
        for position, value in enumerate(ordered, 1):
            if previous_score is None or value.raw_score != previous_score:
                dense_rank += 1
                previous_score = value.raw_score
            rank = position if config.method is RankMethod.ORDINAL else dense_rank
            rank_rows.append((value, rank, position))
        max_rank = max((row[1] for row in rank_rows), default=1)
        size = len(rank_rows)
        for value, rank, position in rank_rows:
            if size == 1:
                percentile = Decimal("0.5")
            elif config.method is RankMethod.ORDINAL:
                percentile = Decimal(size - position) / Decimal(size - 1)
            elif max_rank == 1:
                percentile = Decimal("0.5")
            else:
                percentile = Decimal(max_rank - rank) / Decimal(max_rank - 1)
            normalized = Decimal(0) if max_abs == 0 else (value.raw_score or Decimal(0)) / max_abs
            ranked.append(RankedAlphaValue(value, rank, percentile, normalized))
    return tuple(sorted(ranked, key=lambda item: (item.alpha_value.timestamp_utc, item.alpha_value.alpha_name, item.alpha_value.symbol)))


__all__ = ["rank_alpha_values"]
