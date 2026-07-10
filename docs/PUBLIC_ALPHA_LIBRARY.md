# Public Alpha Library

## Scope

Phase 3 provides deterministic public research examples over Phase 2 validation-gated data. An
alpha returns continuous `AlphaValue` scores. It cannot produce signal direction, orders, position
sizes, execution instructions, PnL, or portfolio state. Calculations use `Decimal`, frozen domain
records, injected repositories, and no network or database activity during import or evaluation.

## Stable series identity

Every alpha request, point-in-time series, `AlphaValue`, and downstream signal uses one immutable
`SeriesIdentity` containing:

- provider name and exchange;
- provider instrument ID and canonical symbol;
- instrument type and settlement asset where applicable;
- timeframe; and
- a deterministic SHA-256 over those fields.

Symbol alone is never the logical key. Binance and OKX `BTC-USDT`, one-minute and five-minute
`BTC-USDT`, and Spot and perpetual `BTC-USDT` are separate series. PostgreSQL uniqueness uses the
run, `series_identity_sha256`, as-of timestamp, and horizon.

## Point-in-time availability

OHLCV eligibility is based on `bar_available_at_utc <= as_of_utc`, never bar open time.
`bar_available_at_utc` is the persisted `bar_close_time_utc` when present. A legacy null close can
be derived only for explicit fixed-duration `s`, `m`, `h`, or `d` timeframes. Unsupported and
calendar-dependent values fail. Naive datetimes fail, a close at or before open fails, and
`is_final = false` is always ineligible.

For a declared `as_of_utc`, the emitted `AlphaValue.timestamp_utc` equals that as-of time. Thus a
bar opening at 12:00 and closing at 12:01 cannot be used at 12:00. Trailing windows are built only
from records available at the evaluation boundary.

## Contracts and hashes

- `AlphaDefinition` separates `formula_sha256`, actual `implementation_code_sha256`, and a
  repository commit or equivalent source-tree identity.
- `AlphaEvaluationRequest` may select complete series identities and may declare one `as_of_utc`.
- `AlphaValue` persists typed status (`emitted`, `warmup`, `skipped`, `invalid`, or `failed`), reason,
  as-of time, lookback bounds, complete series identity, eligible-input hash, and record hash.
- Source observation IDs, validation report IDs, dataset references, and other collection lineage
  remain available as audit provenance but are excluded from stable logical hashes.
- `eligible_input_sha256` covers only stable economic fields available at that point. Later append,
  mutation, or deletion and a legitimate re-collection with new source IDs cannot change an
  earlier value ID, score, eligible-input hash, or record hash.

`AlphaRun` retains run-level operational lineage and valid, rejected, and warmup/skipped counts.
`AlphaEngine` resolves registry versions, validates complete identities and availability, performs
pure calculation, validates output lineage, creates deterministic point IDs, and can persist one
run transactionally.

## Public examples

The repaired public implementations use version `1.1.0`; legacy `1.0.0` registry rows remain historical and cannot be mistaken for the repaired code.

| Name | Exact score semantics |
|---|---|
| `momentum` | `close[t] / close[t-L] - 1` |
| `moving_average_crossover` | short trailing mean divided by long trailing mean, minus one |
| `prior_channel_breakout` | distance outside a prior-only high/low channel divided by prior range |
| `trailing_mean_reversion` | negative z-score of current close against a prior-only close window using population standard deviation; zero for prior zero variance |
| `short_term_return_reversal` | negative trailing return |
| `prior_range_close_position` | prior-only range location scaled around zero |
| `volatility_adjusted_momentum` | trailing return divided by trailing one-period return volatility |
| `price_volume_divergence` | trailing price return minus trailing volume return |
| `rolling_range_expansion` | current range divided by prior-only mean range, minus one |
| `signed_volume_pressure` | signed current volume divided by trailing mean volume |
| `funding_rate_contrarian` | negative mean of realized perpetual funding rates over an explicit bounded lookback |

Funding evaluation requires a complete perpetual instrument identity and grounded interval evidence;
it does not assume an eight-hour interval. The funding score is an educational research input, not
a complete strategy. The formulaic examples are a small public set, not a complete 101-alpha
library, and contain no proprietary formulas or private parameters.

## PostgreSQL persistence

Migration `0008_phase3_phase4_audit_repairs.sql` adds persisted bar close/finality, complete alpha
series identity, explicit evaluation status and bounds, eligible-input and record hashes, separate
formula/code provenance, and series-based uniqueness. Migrations `0001` through `0007` remain
unchanged. PostgreSQL is the only authoritative target; there is no SQLite fallback.

The fixture-default CLI evaluates without writes, then—only when explicitly enabled—persists all
alpha definitions, runs, values, signal runs, signals, and signal components through one bundled
outer transaction.
