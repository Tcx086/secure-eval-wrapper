from __future__ import annotations

from typing import Dict, List


def render_stress_table(rows: List[Dict[str, float]]) -> str:
    lines = [
        "| slippage_bps | volatility_mult | total_return | max_drawdown | sharpe |",
        "|---:|---:|---:|---:|---:|",
    ]
    for r in rows:
        lines.append(
            f"| {r['slippage_bps']:.0f} | {r['volatility_mult']:.2f} | "
            f"{r['total_return']:.4f} | {r['max_drawdown']:.4f} | {r['sharpe']:.3f} |"
        )
    return "\n".join(lines)


def render_evaluation_report(
    strategy: str,
    signal: Dict[str, object],
    monte_carlo_result: Dict[str, object],
    intrabar_result: Dict[str, float],
    stress_rows: List[Dict[str, float]],
    manifest_path: str,
) -> str:
    return f"""# Evaluation Report (Public Demo)

## 1) Decision Output
- Strategy: `{strategy}`
- Action: `{signal['action']}`
- Score: `{signal['score']}`
- Confidence: `{signal['confidence']}`
- Metadata: `{signal['meta']}`

## 2) Monte Carlo Summary
- Paths: {monte_carlo_result['paths']}
- Bars per Path: {monte_carlo_result['path_len']}
- P05 Total Return: {monte_carlo_result['p05_total_return']}
- P50 Total Return: {monte_carlo_result['p50_total_return']}
- P95 Total Return: {monte_carlo_result['p95_total_return']}
- P95 Max Drawdown: {monte_carlo_result['p95_max_drawdown']}
- P50 Sharpe: {monte_carlo_result['p50_sharpe']}

## 3) Stress Test Matrix
{render_stress_table(stress_rows)}

## 4) Intrabar Probe
- Bars Simulated: {intrabar_result['bars_simulated']}
- Stop Hit Rate: {intrabar_result['stop_hit_rate']}
- Take Profit Hit Rate: {intrabar_result['take_profit_hit_rate']}
- Avg Intrabar PnL: {intrabar_result['avg_intrabar_pnl']}

## 5) Reproducibility
- Manifest: `{manifest_path}`
- Method: fixed seed + input snapshot hash + config hash + code hashes.
- Interpretation: this demonstrates controlled iteration and reproducibility, not random trial-and-error.

## 6) Confidentiality Boundary
- Public: framework, evaluation methodology, risk/stability evidence.
- Private: real strategy logic, features, thresholds, weights, and training details.
"""


def render_model_card_public() -> str:
    return """# Public Model Card (No Edge Disclosure)

## Purpose
Demonstrate a reproducible decision pipeline and risk evaluation framework without exposing proprietary alpha logic.

## Architecture (High-level)
1. Signal Layer: produces action/score/confidence from normalized features.
2. Risk Layer: applies stress, drawdown, and intrabar stability probes.
3. Evaluation Layer: Monte Carlo and scenario testing with deterministic seeds.
4. Audit Layer: manifest with input/config/code hashes for reproducibility.

## What We Reveal
- Interface contracts
- Evaluation methodology
- Stability metrics under scenario shifts
- Reproducibility process and evidence

## What We Keep Private
- Real feature engineering pipeline
- True scoring/decision function internals
- Hyperparameters and threshold schedules
- Training datasets and selection criteria

## Why This Is Not Random Iteration
- Every run has explicit hypothesis and config.
- Same snapshot + same seed + same code hashes => same result.
- Robustness is tested across controlled perturbations, not cherry-picked samples.
"""

