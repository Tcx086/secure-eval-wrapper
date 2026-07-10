# Public Alpha Library

## Scope

Phase 3 provides deterministic public research examples over Phase 2 validation-gated data. An
alpha returns continuous `AlphaValue` scores. It cannot produce signal direction, orders, position
sizes, execution instructions, PnL, or portfolio state. The implementations use pure Python,
`Decimal`, frozen dataclasses, injected repositories, and no network or database activity during
import or calculation.

## Contracts

- `AlphaDefinition` records stable identity/version, category, inputs, parameter schema/defaults,
  warmup, output semantics, horizon, status, and implementation SHA-256.
- `AlphaEvaluationRequest` declares a half-open UTC window, symbols, parameters, dataset lineage,
  run identity, and hashes.
- `AlphaValue` records an explicit valid or invalid/warmup point with source observation IDs and
  dataset/config/implementation hashes.
- `AlphaRun` records operational status and valid, rejected, and warmup-skipped counts.
- `PublicAlpha` separates parameter validation and pure calculation from orchestration and storage.
- `AlphaEngine` resolves the registry, validates and prepares point-in-time data, validates outputs,
  creates deterministic IDs, and optionally persists one run in one outer PostgreSQL transaction.

The in-memory registry rejects duplicate name/version pairs and implementation-hash conflicts,
lists definitions in stable order, resolves explicit versions, and retains deprecated definitions
for historical lineage.

## Point-in-time controls

`AlphaDataSet` is the explicit boundary for accepted or accepted-with-warnings Phase 2 data and
requires validation-report lineage. `PointInTimeSeries` sorts deterministically and rejects naive
timestamps, duplicate logical timestamps, mixed symbols, mixed timeframes, non-final bars,
ambiguous funding instruments, and funding records without grounded interval evidence.

All window access is trailing. Prior windows can explicitly exclude the current observation. The
engine verifies that no source timestamp is later than its output timestamp. Missing warmup is an
invalid/warmup `AlphaValue`; it is never backfilled from future data. Zero denominators or other
undefined calculations follow each alpha's documented explicit policy.

## Public examples

| Name | Category | Exact score semantics |
|---|---|---|
| `momentum` | momentum | `close[t] / close[t-L] - 1` |
| `moving_average_crossover` | trend | `short trailing mean / long trailing mean - 1` |
| `prior_channel_breakout` | breakout | distance outside the prior-only high/low channel, divided by prior range; zero inside |
| `trailing_mean_reversion` | mean reversion | negative trailing population z-score of close; zero when variance is zero |
| `short_term_return_reversal` | formulaic | negative trailing return |
| `prior_range_close_position` | formulaic | `2 * (close - prior low) / prior range - 1`; current bar excluded |
| `volatility_adjusted_momentum` | formulaic | trailing return divided by trailing one-period return volatility |
| `price_volume_divergence` | formulaic | trailing price return minus trailing volume return |
| `rolling_range_expansion` | formulaic | current high-low range divided by prior mean range minus one |
| `signed_volume_pressure` | formulaic | `sign(close-open) * volume / trailing mean volume` |
| `funding_rate_contrarian` | funding | negative realized funding rate with preserved grounded interval evidence |

The formulaic examples are a small transparent educational set inspired by public formulaic
research conventions. They are not the complete “101 Formulaic Alphas” library, do not copy opaque
or proprietary formulas, use no private parameters, and make no profitability claim. The funding
score alone is not a complete strategy.

## PostgreSQL persistence

Migration `0007_alpha_signal_library.sql` extends `alpha.alpha_registry` and adds
`alpha.alpha_runs` and `alpha.alpha_values`. Logical retries return database-selected IDs when
content hashes match and fail on identity/content conflicts. Reads are deterministic and half-open.
PostgreSQL is the only authoritative target; there is no SQLite or file-database fallback.

## Offline demo

From the repository root:

```powershell
python open-core\scripts\run_public_alpha_signal_pipeline.py
```

The default fixture is `synthetic_public_safe`, opens no sockets, touches no database, and prints
only aggregate counts and hash validity. Persistence requires both `--persist` and
`ENABLE_POSTGRES_PERSISTENCE=true`.
