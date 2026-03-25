from __future__ import annotations

import math
from typing import Dict, List


def max_drawdown(equity_curve: List[float]) -> float:
    peak = equity_curve[0]
    worst = 0.0
    for v in equity_curve:
        if v > peak:
            peak = v
        dd = (v - peak) / peak
        if dd < worst:
            worst = dd
    return worst


def sharpe_ratio(returns: List[float], annualization: float = 252.0) -> float:
    if not returns:
        return 0.0
    mean_r = sum(returns) / len(returns)
    var = sum((r - mean_r) ** 2 for r in returns) / max(1, len(returns) - 1)
    std = math.sqrt(var)
    if std == 0:
        return 0.0
    return (mean_r / std) * math.sqrt(annualization)


def equity_from_returns(returns: List[float], start: float = 1.0) -> List[float]:
    eq = [start]
    for r in returns:
        eq.append(eq[-1] * (1.0 + r))
    return eq


def summarize_path(returns: List[float]) -> Dict[str, float]:
    eq = equity_from_returns(returns)
    total_return = eq[-1] - 1.0
    mdd = max_drawdown(eq)
    sr = sharpe_ratio(returns)
    return {
        "total_return": round(total_return, 6),
        "max_drawdown": round(mdd, 6),
        "sharpe": round(sr, 6),
    }

