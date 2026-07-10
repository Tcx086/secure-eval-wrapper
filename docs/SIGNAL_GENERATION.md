# Standardized Signal Generation

## Scope and contract

Phase 4 converts emitted `AlphaValue` research outputs into deterministic `StandardizedSignal`
records. A signal has long, short, or flat direction, scores, optional rank and percentile,
heuristic confidence, horizon, full series identity, alpha lineage, stable hashes, and provenance.
It has no broker, order, quantity, leverage, account, position, execution, PnL, or allocation fields.

`SignalPipeline` validates run and complete series identities, ranks, thresholds, combines,
preserves structured contributions, and computes confidence. Persistence remains disabled by
default.

## Ranking

Ranking groups by evaluation timestamp, alpha ID/version, and horizon. Invalid and warmup values
are excluded and missing series are not imputed. Economic ties always receive the same average rank
and the same percentile; symbol or series ordering is used only to make output ordering stable, not
to break a tie. This applies to ascending and descending ranking and to legacy `dense` or `ordinal`
configuration values. New configurations default to `average`.

For a universe of size `n > 1`:

```text
average_rank = mean(one-based positions occupied by the tied value)
percentile = (n - average_rank) / (n - 1)
```

A one-member universe receives rank 1 and percentile 0.5. Normalized score preserves sign and is
`raw_score / max(abs(raw_score))` within the group, bounded to `[-1, 1]`.

## Threshold policies

- Absolute: long at or above a positive threshold, short at or below a negative threshold, and flat
  between them.
- Percentile: long at or above the upper percentile, short at or below the lower percentile, and
  flat between them.
- Top/bottom N: select best and worst economic ranks while including ties at a selection boundary.

Top/bottom N requires an explicit overlap policy whenever the selected sets can overlap:

- `fail`: reject the group;
- `skip_group`: emit no signal for the group; or
- `force_flat`: emit overlapping components flat and record the resolution reason.

The selected policy is included in configuration hashes and PostgreSQL run/signal columns. A
forced resolution is retained on both the signal and normalized component row.

## Combination and SignalComponent

Combination supports equal weights, explicit positive static weights, or weights scaled by
absolute normalized score:

```text
signed_contribution = effective_weight * direction_sign * abs(normalized_score)
aggregate_score = sum(signed_contribution) / sum(effective_weight)
```

The aggregate is bounded to `[-1, 1]`. Exact ties and interior values are flat. Expected alpha
identities, contributor count, coverage ratio, and insufficient-coverage flat/skip behavior are
explicit.

Every contribution also becomes an immutable `SignalComponent` child row with:

- signal, alpha-value, and alpha foreign keys;
- raw and normalized values;
- configured and effective weights;
- signed contribution;
- component disposition and resolution reason;
- deterministic component SHA-256; and
- public-safe metadata.

A duplicate JSON contribution summary may remain in signal provenance for readability, but it does
not replace normalized `signals.signal_components` persistence.

## Point-in-time hash invariance

Each signal uses the stable per-point eligible-input hashes of its source alpha values rather than a
whole-dataset run hash. Signal IDs and record hashes exclude mutable collection provenance and run
creation timestamps. A future append, mutation, or deletion after one signal timestamp therefore
cannot change that historical signal's ID, record hash, or score. Formula SHA, actual
implementation-code SHA, and repository/source-tree identity are retained separately.

## Confidence

The default deterministic formula is:

```text
confidence = 0.4 * abs(aggregate_score)
           + 0.3 * agreement_ratio
           + 0.2 * coverage_ratio
           + 0.1 * distance_beyond_decision_threshold
```

Weights are configurable and normalized by their sum. Confidence is clamped to `[0, 1]`;
insufficient coverage returns zero and flat signals default to zero. It is a transparent consistency
heuristic, not a calibrated probability of profit.

## PostgreSQL and bundled persistence

Migration `0008_phase3_phase4_audit_repairs.sql` adds complete series identity, average-rank storage,
overlap policy/reasons, separate formula/code provenance, stable signal record hashes, and
`signals.signal_components` with deterministic uniqueness and foreign keys.

`persist_alpha_signal_bundle` is the milestone-wide atomic boundary. One transaction writes all
required alpha definitions, alpha runs, alpha values, signal runs, standardized signals, and signal
components. A failure during a later alpha value, signal run, signal, or component rolls back every
write in the bundle. The CLI does not commit each alpha or signal independently.
