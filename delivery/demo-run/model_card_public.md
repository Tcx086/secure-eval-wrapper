# Public Model Card (No Edge Disclosure)

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
