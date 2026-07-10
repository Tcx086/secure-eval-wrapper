# Standardized Signal Generation

## Scope and contract

Phase 4 converts valid `AlphaValue` research outputs into deterministic `StandardizedSignal`
records. A signal has long, short, or flat direction, raw and normalized scores, optional rank and
percentile, deterministic heuristic confidence, horizon, alpha lineage, hashes, and provenance.
It has no broker, order, quantity, leverage, account, position, exposure, execution, PnL, or
portfolio-allocation fields.

`SignalPipeline` supports one alpha or an explicit multi-alpha combination. It validates run,
symbol, UTC-window, horizon, and alpha identities; ranks; thresholds; combines; retains conflicts;
computes confidence; and optionally persists one `SignalRun` and its children in one PostgreSQL
transaction. Persistence is disabled by default.

## Ranking

Ranking groups by timestamp, alpha ID/version, and horizon. Invalid and warmup-incomplete values
are excluded, missing symbols are not imputed, and timestamps never mix. Ascending and descending
orders and dense or ordinal ranks are explicit. Symbol identity is the deterministic final
tie-break. The best cross-sectional percentile is 1 and the worst is 0; a one-symbol or all-tied
group receives 0.5. Normalized score preserves sign and is `raw_score / max(abs(raw_score))` within
the group, bounded to `[-1, 1]`.

## Threshold policies

- Absolute: long at or above a positive threshold, short at or below a negative threshold, flat
  between them.
- Percentile: long at or above the upper percentile, short at or below the lower percentile, flat
  between them.
- Top/bottom N: deterministic best and worst selections. If a small universe puts an observation in
  both sets, the explicit conflict result is flat.

Every policy is validated and hashed. There is no implicit threshold that creates directions.

## Combination and conflicts

Combination supports equal weights, explicit positive static weights, or effective weights scaled
by absolute normalized score. Each component contributes:

```text
signed_contribution = effective_weight * direction_sign * abs(normalized_score)
aggregate_score = sum(signed_contribution) / sum(effective_weight)
```

The aggregate is bounded to `[-1, 1]`. Positive and negative threshold crossings become long and
short; an exact tie or interior value is flat. Expected alpha identities, minimum contributor
count, minimum coverage ratio, and insufficient-coverage flat/skip policy are explicit. Provenance
retains every alpha value ID, alpha version, configured/effective weight, signed contribution,
coverage, agreement, conflict state, and aggregate score. Conflicting alphas are never discarded.

## Confidence

The default deterministic formula is a weighted mean:

```text
confidence = 0.4 * abs(aggregate_score)
           + 0.3 * agreement_ratio
           + 0.2 * coverage_ratio
           + 0.1 * distance_beyond_decision_threshold
```

Component weights are configurable, non-negative, and normalized by their sum. Confidence is
clamped to `[0, 1]`; insufficient coverage returns zero and flat signals default to zero. This is a
transparent heuristic consistency score, not a calibrated probability of profit.

## PostgreSQL persistence

Migration `0007_alpha_signal_library.sql` hardens `signals.signal_runs` and `signals.signals` with
alpha-run lineage, all exact configurations, data/config/code/content hashes, raw/normalized
scores, rank/percentile, source alpha value IDs, counts, conflict provenance, uniqueness, indexes,
foreign keys, and checks. Idempotent retries compare content hashes and return database-selected
IDs. Reads use half-open UTC ranges. PostgreSQL remains the only authoritative target.
