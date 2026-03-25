from __future__ import annotations

import random
from typing import Dict, List

from src.eval.metrics import summarize_path


def build_base_returns(score: float, confidence: float, bars: int, seed: int) -> List[float]:
    rng = random.Random(seed)
    drift = score * 0.0012
    vol = 0.008 + (1.0 - max(0.0, min(1.0, confidence))) * 0.006
    return [rng.gauss(drift, vol) for _ in range(bars)]


def monte_carlo(
    base_returns: List[float],
    n_paths: int,
    path_len: int,
    seed: int,
    fee_bps: float,
    slippage_bps: float,
) -> Dict[str, object]:
    rng = random.Random(seed)
    fee = fee_bps / 10000.0
    slippage = slippage_bps / 10000.0
    costs = fee + slippage

    stats: List[Dict[str, float]] = []
    for _ in range(n_paths):
        sim = []
        for _ in range(path_len):
            r = base_returns[rng.randrange(0, len(base_returns))]
            sim.append(r - costs)
        stats.append(summarize_path(sim))

    total_returns = sorted(s["total_return"] for s in stats)
    drawdowns = sorted(s["max_drawdown"] for s in stats)
    sharpes = sorted(s["sharpe"] for s in stats)

    def pct(values: List[float], p: float) -> float:
        idx = int((len(values) - 1) * p)
        return round(values[idx], 6)

    return {
        "paths": n_paths,
        "path_len": path_len,
        "p05_total_return": pct(total_returns, 0.05),
        "p50_total_return": pct(total_returns, 0.50),
        "p95_total_return": pct(total_returns, 0.95),
        "p95_max_drawdown": pct(drawdowns, 0.95),
        "p50_sharpe": pct(sharpes, 0.50),
    }


def stress_test(
    base_returns: List[float],
    slippage_bps_grid: List[float],
    fee_bps: float,
    volatility_mult_grid: List[float],
) -> List[Dict[str, float]]:
    out: List[Dict[str, float]] = []
    for slip in slippage_bps_grid:
        for vm in volatility_mult_grid:
            adjusted = []
            for r in base_returns:
                scaled = r * vm
                costs = (fee_bps + slip) / 10000.0
                adjusted.append(scaled - costs)
            s = summarize_path(adjusted)
            out.append(
                {
                    "slippage_bps": slip,
                    "volatility_mult": vm,
                    "total_return": s["total_return"],
                    "max_drawdown": s["max_drawdown"],
                    "sharpe": s["sharpe"],
                }
            )
    return out


def intrabar_probe(
    action: str,
    base_returns: List[float],
    seed: int,
    stop_loss_pct: float = 0.004,
    take_profit_pct: float = 0.006,
    steps_per_bar: int = 12,
) -> Dict[str, float]:
    rng = random.Random(seed)
    if action not in {"LONG", "SHORT"}:
        return {
            "bars_simulated": len(base_returns),
            "stop_hit_rate": 0.0,
            "take_profit_hit_rate": 0.0,
            "avg_intrabar_pnl": 0.0,
        }

    stop_hits = 0
    tp_hits = 0
    pnl_sum = 0.0

    sign = 1.0 if action == "LONG" else -1.0
    for bar_r in base_returns:
        price = 1.0
        stop = 1.0 - stop_loss_pct * sign
        take = 1.0 + take_profit_pct * sign
        hit = False
        bar_pnl = 0.0

        step_drift = bar_r / max(1, steps_per_bar)
        step_noise = abs(bar_r) / max(1, steps_per_bar) + 0.0008
        for _ in range(steps_per_bar):
            step_r = rng.gauss(step_drift, step_noise)
            price *= 1.0 + step_r
            if not hit:
                if (sign > 0 and price <= stop) or (sign < 0 and price >= stop):
                    stop_hits += 1
                    bar_pnl = -stop_loss_pct
                    hit = True
                elif (sign > 0 and price >= take) or (sign < 0 and price <= take):
                    tp_hits += 1
                    bar_pnl = take_profit_pct
                    hit = True

        if not hit:
            bar_pnl = sign * (price - 1.0)
        pnl_sum += bar_pnl

    n = len(base_returns) if base_returns else 1
    return {
        "bars_simulated": len(base_returns),
        "stop_hit_rate": round(stop_hits / n, 6),
        "take_profit_hit_rate": round(tp_hits / n, 6),
        "avg_intrabar_pnl": round(pnl_sum / n, 6),
    }

