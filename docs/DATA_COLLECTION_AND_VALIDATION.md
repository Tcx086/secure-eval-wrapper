# Data Collection and Validation

## Purpose
The target data layer will collect crypto market data from public exchange APIs and provider APIs,
preserve source provenance, validate quality, and only promote accepted datasets into research and
backtesting. Phase 2 now includes contracts, offline preparation, sample, Binance Spot, and OKX V5
public OHLCV normalization; single-source validation/reporting; cross-source reconciliation;
auditable PostgreSQL persistence; and provider-neutral pipeline orchestration. Automated tests
remain offline. The combined CLI defaults to fixtures, while public-network access is explicitly
gated and bounded.

## Collection Scope
Initial crypto-only data types:
- OHLCV bars.
- Trades.
- Funding rates.
- Instrument metadata.

Future data types:
- Order book snapshots.
- Account snapshots for paper/live account monitoring.

## Phase 2A Contract Baseline
Phase 2 is in progress. The first increment adds importable, inert contracts under:

```text
open-core/src/secure_eval_wrapper/
|-- data_collection/
|   |-- models.py
|   |-- providers.py
|   `-- registry.py
`-- data_validation/
    |-- models.py
    `-- interfaces.py
```

The collection models cover provider specifications, provider-neutral requests, raw observations,
normalized OHLCV bars, normalized trades, funding rates, instrument metadata, and collection run
summaries. Decimal values are represented with `Decimal`, record and run identifiers with `UUID`,
and time fields with timezone-capable `datetime` values. These Phase 2A dataclasses remain inert.
Phase 2B adds explicit UTC guards and deterministic source hashing; future provider adapters must
call those guards while parsing payloads. Persistent writes must use the existing
PostgreSQL repository interfaces.

`MarketDataProvider` is an abstract interface with `fetch_ohlcv`, `fetch_trades`,
`fetch_funding_rates`, and `fetch_instruments` methods. The methods return raw observation
contracts so source payloads and request provenance can be preserved before normalization. Phase
2A includes no concrete subclass, HTTP library, endpoint URL, credential field, retry behavior, or
network test.

The validation package separates declarative checks, individual check results, dataset-level
reports, and cross-source reconciliation results. `OfflineOhlcvValidator` is the Phase 2C concrete,
network-free `DataValidator`; `OfflineOhlcvReconciler` is the Phase 2F concrete, network-free
`CrossSourceReconciler`; and `DatasetPromoter` remains an abstract future boundary.
`ValidationCheckStatus` describes an individual check outcome, while `ValidationStatus` describes
the final dataset gate decision. Stable `QuarantineReason` values
classify failed observations without promoting or persisting them.

## Phase 2B Offline Utilities
Phase 2B adds reusable, network-free preparation code:

- `hashing.py` emits compact canonical JSON and deterministic lowercase SHA-256 digests. Mapping
  key order does not affect a digest, non-finite floats are rejected, and observation source hashes
  cover both the payload and stable request metadata.
- `time_utils.py` requires UTC-aware values at strict boundaries and converts explicitly offset
  ISO-8601 values to UTC. Naive values fail unless the caller deliberately sets
  `assume_naive_utc=True`; local timezone assumptions are never made.
- `symbols.py` normalizes explicitly delimited simple pairs such as `btc/usdt` to `BTC-USDT`.
  Concatenated or multi-part symbols are rejected instead of guessed.
- `sample_provider.py` implements `MarketDataProvider` for synthetic OHLCV JSON fixtures under
  `open-core/data/sample/` only. Fixture filenames cannot include paths, and the provider contains
  no network client, URL, authentication, persistence, or exchange adapter code.

The included `crypto_ohlcv_sample.json` fixture is classified `synthetic_public_safe`. Each
returned `RawObservation` includes request provenance, explicit UTC timestamps, normalized symbol
metadata, and a deterministic source hash matching `^[0-9a-f]{64}$`. The sample provider supports
OHLCV only; trade, funding-rate, and instrument methods explicitly remain unimplemented.

## Phase 2C Offline Normalization and Single-Source Validation

Phase 2C adds deterministic, offline-only behavior under the existing contracts:

- `data_collection/normalization.py` converts sample-provider `RawObservation` values into
  `NormalizedBar` values. It parses OHLCV fields as exact `Decimal` values, requires explicit UTC
  timestamps, re-applies conservative `BASE-QUOTE` symbol normalization, creates deterministic bar
  IDs, and preserves source observation IDs plus request/source provenance. Optional
  `close_time_utc`, `is_final`, and `is_partial` fields are handled when present; contradictory
  final/partial flags fail normalization.
- `data_validation/ohlcv.py` implements missing-bar, duplicated-timestamp, non-monotonic-order,
  invalid-OHLC, invalid-volume, and partial-candle checks. Checks are grouped by symbol, exchange,
  and timeframe where appropriate. A missing interval is a warning by default and can be configured
  to reject the dataset. Explicitly partial candles reject by default; absent finality metadata is
  not guessed. An optional exact-`Decimal` maximum volume supplies a dataset-specific impossible
  volume ceiling.
- `data_validation/reporting.py` builds `ValidationResult` and `ValidationReport` objects. Accepted
  and rejected counts are bar counts; warning count is the number of warning results. Source hashes
  are unique and sorted, the tolerance configuration has its own SHA-256 digest, and the report
  digest excludes wall-clock creation timestamps and generated IDs so identical logical reports
  hash identically.
- `data_validation/quarantine.py` maps source observation IDs from failed results to stable
  `QuarantineReason` values in check order. It does not create or persist quarantine records.

All Phase 2C tests use the synthetic public-safe fixture and explicitly guard against socket use.
There are no HTTP clients, exchange adapters, credentials, database writes, or runtime trading
features in this increment.

## Provider Strategy
The framework should support multiple providers or exchanges for the same logical instrument:
Binance, OKX, Bybit, Coinbase, and third-party aggregators where licensing permits.

The Phase 2 registry records the following current capability metadata:

| Provider | OHLCV | Trades | Funding rates | Instruments |
|---|---|---|---|---|
| Binance | implemented | planned | planned | planned |
| OKX | implemented | planned | planned | planned |
| Bybit | planned | planned | planned | planned |
| Coinbase | planned | planned | unknown | planned |

`planned` does not mean implemented or verified. `unknown` means the capability must be resolved
when a future provider adapter is designed. The registry deliberately contains no URLs, clients,
authentication configuration, or credentials; fetch behavior stays in provider modules.

The offline sample provider remains a fixture reader rather than an exchange adapter. Binance Spot
OHLCV is implemented in Phase 2E and OKX V5 public historical OHLCV is implemented in Phase 2G.
Trades, funding rates, instrument metadata, and all Bybit/Coinbase adapters remain planned or unknown.

Provider adapters must normalize symbol naming, timestamp format, timezone to UTC, numeric
precision, timeframe names, funding interval representation, and instrument metadata fields.

## Source Provenance
Every raw observation must include:
- Provider name.
- Exchange name when applicable.
- Endpoint or API method.
- Request parameters.
- Request timestamp UTC.
- Ingest timestamp UTC.
- Raw symbol.
- Normalized symbol.
- Data type.
- Source payload hash.
- Collection run ID.
- Collection status.

The source hash should be computed from a deterministic representation of the raw payload and
request metadata.

Phase 2B implements this calculation with canonical JSON and SHA-256. Digests are always lowercase
64-character hexadecimal strings. Stable request parameters are hashed; collection run IDs and
request/ingest timestamps remain provenance fields and do not make an identical source unstable.

## Timestamp Rules
- Store all timestamps in UTC.
- Keep provider timestamps as source metadata when useful.
- Reject or quarantine records with ambiguous timestamps.
- Bars should use explicit open time and timeframe.
- Trades should use event time and ingest time.
- Funding rates should use funding interval time.

Phase 2B guards reject naive datetimes by default. A caller may mark a known-naive source value as
UTC only through an explicit opt-in at the normalization call site.

## Validation Gate
Raw data is not research-ready. Data must pass validation before alpha research, backtesting, or
public reports use it.

Validation stages:
1. Parse raw observations.
2. Normalize symbols and timestamps.
3. Run single-source quality checks.
4. Run cross-source reconciliation where possible.
5. Write validation report.
6. Promote accepted records to validated tables.
7. Quarantine rejected records with reasons.

Phase 2A models these stages, Phase 2B supplies offline parsing guards and provenance helpers, and
Phase 2C implements stages 1 through 3 plus in-memory report construction for sample OHLCV data.
Phase 2D persists the offline sample-provider flow through PostgreSQL, including raw observations,
reports, checks, accepted bars, and quarantine decisions. Phase 2E feeds Binance Spot public OHLCV
through the same normalization and validation path. Phase 2F implements stage 4 for normalized OHLCV. Phase 2H persists reconciliation summaries and
child checks in PostgreSQL, and Phase 2I orchestrates stages 1 through 7 for injected providers.

## Single-Source Checks
Implemented offline for normalized OHLCV in Phase 2C:
- Missing bars.
- Duplicated timestamps.
- Non-monotonic timestamps.
- Invalid OHLC relationships.
- Negative or configured-impossible volume.
- Partial candles when an explicit finality flag is available.

Still planned for later Phase 2 increments:
- Price outliers.
- Volume anomalies beyond an explicit maximum.
- Stale data.
- Symbol mapping consistency across provider metadata.
- Funding timestamp gaps.
- Instrument metadata drift.

## Cross-Source Reconciliation
Phase 2F compares two or more provider datasets for one canonical symbol and timeframe. Inputs are
indexed by provider and bar-open timestamp after enforcing UTC-aware timestamps, source observation
IDs, one exchange per provider dataset, and no duplicated provider timestamps. Mixed symbols or
timeframes fail instead of being reconciled implicitly.

The reconciler evaluates the union timestamp grid in deterministic order. A missing-coverage result
records every absent provider slot; an extra-bar result is reserved for a timestamp supplied by only
one provider. At timestamps with at least two bars, all provider pairs are compared. OHLC values
mismatch only when both the configured absolute tolerance and a symmetric relative-basis-point
tolerance are exceeded. Volume uses a configurable symmetric relative tolerance. Close times are
compared only when both bars provide one.

Each stable check type emits one aggregate `ValidationResult` containing timestamp-ordered findings,
compared values, provider names, tolerances, and affected source observation IDs. Warning/reject
policies control the result status. `ReconciliationResult` metrics count providers, union timestamps,
timestamps with pairwise comparisons, missing provider slots, mismatch timestamps, and provider-only
extra bars. Deterministic IDs hash stable datasets, config, and checks; `created_at_utc` is recorded but
does not affect those IDs.

## Tolerance Rules
Tolerance rules should be configurable and stored with the validation report.

Example design-level defaults:
- Major spot pairs close price deviation warning: greater than 10 basis points.
- Major spot pairs close price deviation reject: greater than 50 basis points unless justified by venue conditions.
- Timestamp mismatch warning: greater than one second for trades or greater than one bar interval for OHLCV alignment.
- Missing bars warning: any isolated missing bar.
- Missing bars reject: repeated missing bars or missing bars inside a required backtest window.
- Volume deviation warning: provider-specific because exchange volumes differ across venues.

Tolerances should vary by symbol liquidity, venue, data type, timeframe, and historical vs
near-real-time collection.

Phase 2F defaults are deliberately warning-oriented: price comparison allows an absolute tolerance
of `0.00000001` or a relative tolerance of 50 basis points, and volume comparison allows 5,000
basis points because venue volume can differ materially. A pair is accepted when it falls within
either price tolerance. Missing timestamps, mismatches, and extra bars warn by default; callers can
select reject policies explicitly. Config values are exact `Decimal` values and can be hashed by the
existing canonical SHA-256 utilities.

## Accepted vs Rejected Flow
```text
raw provider response
      |
      v
raw_source_observations
      |
      v
normalization
      |
      v
single-source checks
      |
      v
cross-source reconciliation
      |
      +--> rejected_observations + validation report
      |
      v
validated_bars / validated_trades / funding_rates
      |
      v
research, alpha library, backtesting
```

## Validation Reports
Each validation report includes validation run ID, dataset reference, provider set, time range,
symbols, timeframes, check results, accepted bar count, rejected bar count, warning-result count,
tolerance config hash, source hashes, report hash, and final status. Phase 2C constructs these
objects in memory only. Its stable report hash covers logical report content and deliberately omits
creation time and generated IDs.

Statuses:
- `accepted`
- `accepted_with_warnings`
- `rejected`
- `quarantined`

## Snapshot Rules
Research and backtesting should reference immutable dataset snapshots. Snapshot metadata includes
dataset ID, provider list, symbol list, time range, validation report ID, data hash, and created
timestamp. A backtest must record the dataset snapshot hash in its run manifest.

## Public Delivery Rules
Public artifacts may show aggregate data quality summaries, provider names where licensing permits,
validation status, hashes, and dataset references. Public artifacts must not show private raw
exports, account data, API keys, sensitive venue credentials, or private research signals.

## Phase 2D PostgreSQL-backed offline persistence

Phase 2D connects the offline sample-provider OHLCV path to the PostgreSQL storage contracts. The
`persist_offline_ohlcv_validation_flow` service writes raw source observations first, then the
hashed validation report and each check result. Bars whose source observations pass the report
are promoted to `market_data.validated_bars`; failed source observations produce deterministic
`data_quality.quarantine_decisions` rows linked to the report and validation run. The service is
offline-only, accepts an injected repository/DB-API connection, and makes no network or exchange
client calls.

Only public-safe offline fixtures are in scope for this Phase 2D service. Phase 2I reuses the same
row mappings for explicitly enabled Binance/OKX pipeline persistence, and Phase 2H adds the separate
reconciliation summary/check persistence service.

## Phase 2E Binance public OHLCV adapter

Phase 2E adds `BinanceSpotOhlcvProvider` for Binance Spot's public
`GET https://api.binance.com/api/v3/klines` endpoint only. It maps conservative, explicitly
delimited symbols such as `BTC-USDT` to `BTCUSDT`, converts UTC request windows to milliseconds,
caps `limit` at 1000, and converts the internal half-open end boundary to Binance's inclusive
millisecond precision without requesting the exact exclusive boundary. Returned klines are filtered
again to the requested half-open window.

The adapter parses each 12-element kline into a `RawObservation`, preserves the original provider
payload, keeps OHLCV numeric values as strings, records UTC-aware timestamps and request
provenance, and computes the existing deterministic source SHA-256. Binance REST klines do not
carry a final-candle flag, so the adapter does not guess one. The observations pass through the
existing `normalize_ohlcv_observations` and `validate_ohlcv_bars` path.

Network I/O is isolated behind the injectable `HttpTransport` contract. The standard-library
`UrlLibHttpTransport` performs no work at import time and contains no credential or signing logic.
Unit tests inject a fake transport, block socket creation, and make no real network calls. The
adapter exposes no authenticated endpoints, API-key headers, account methods, order methods,
websocket behavior, or trading logic; its non-OHLCV provider methods remain unimplemented.

`open-core/scripts/binance_public_ohlcv_smoke.py` is the only optional public-network check. It is
disabled unless `ENABLE_PUBLIC_NETWORK_SMOKE=true`, requests at most two completed public candles,
writes no downloaded data, uses no credentials, and prints only a public-safe summary.

## Phase 2F Offline OHLCV cross-source reconciliation

`OfflineOhlcvReconciler` and `reconcile_ohlcv_sources` compare normalized OHLCV datasets from at
least two named providers. The implementation is deterministic across input mapping and bar order,
uses exact `Decimal` comparisons, validates all timestamps as UTC, and preserves provider and source
observation provenance in each `ValidationResult`.

Stable checks cover missing provider timestamps, OHLC mismatches, volume mismatches, provider-only
extra bars, and optional close-time mismatches. `OhlcvReconciliationConfig` supplies exact absolute
and relative tolerances plus warning/reject policies. Reconciliation and result IDs exclude the
injected creation clock, while the returned `created_at_utc` remains explicit UTC provenance.

Phase 2F remains fully offline and contains no exchange, credential, or HTTP behavior. Phase 2H adds
an explicit PostgreSQL persistence boundary for these deterministic results; it does not add network
or trading behavior.

## Phase 2G OKX V5 public historical OHLCV adapter

The current contract was verified on 2026-07-09 against the official
[OKX V5 API guide](https://www.okx.com/docs-v5/en/). The adapter uses the unauthenticated market-data
route `GET /api/v5/market/history-candles` (API version V5). Its documented request parameters are
`instId`, `after`, `before`, `bar`, and `limit`; the maximum page size is 300. The response envelope
must contain `code="0"`, a string `msg`, and a list `data`. Each candle must have the documented
nine-field layout `[ts,o,h,l,c,vol,volCcy,volCcyQuote,confirm]`, where `confirm` is `0` for incomplete
and `1` for complete.

`OkxPublicOhlcvProvider` accepts conservative spot symbols such as `BTC-USDT`, explicitly maps
internal timeframes to OKX bar values, and selects UTC-opening variants for 6-hour and longer bars.
It requires explicit UTC-aware half-open bounds. The first request uses the exclusive end timestamp
as the `after` cursor; later requests move backward using the oldest timestamp. Page count is
bounded, cursor progress is checked, duplicate timestamps across pages fail, and results are
filtered to `[start_at_utc, end_at_utc)` before being returned in chronological order. The adapter
preserves the original nine-field provider payload, exact numeric strings, the documented finality
flag, derived close time, full request provenance, and deterministic source SHA-256.

The default API base is `https://openapi.okx.com`. A small allowlist supports OKX's documented regional
domain requirement (`openapi.okx.com`, `us.okx.com`, and `eea.okx.com`) without
allowing an arbitrary host. The adapter sends no authentication headers and exposes no private,
account, order, execution, signing, or credential behavior. Trades, funding rates, and instruments
remain explicitly unimplemented.

## Phase 2H reconciliation persistence

Migration `0004_reconciliation_persistence.sql` adds
`data_quality.reconciliation_results` and
`data_quality.reconciliation_check_results`. The summary row exposes reconciliation, validation,
provider, window, status, configuration hash, dataset hash, deterministic result hash, metrics, and
creation-time fields. Child rows preserve the declared check, status, severity, affected source
observation IDs, and structured findings. Unique constraints make summary/check writes idempotent;
repositories use parameterized SQL and return the database-selected identifier on conflict.

`persist_reconciliation_result` writes a summary and all child checks in one transaction and rolls
back on failure. It accepts an injected DB-API repository and never connects at module import time.
The schema verifier covers both tables, every required column, indexes, unique constraints, and the
existing per-migration SHA metadata verification.

## Phase 2I provider-neutral OHLCV pipeline and safe CLI

`OhlcvPipeline` validates a typed request, invokes injected public providers in deterministic name
order, normalizes observations, runs one validation report per provider, and returns explicit
provider failures. One canonical accepted-bar gate unions observation IDs from failed validation
results, rejects any bar containing one of those source IDs, and treats a failed result without
record IDs as a dataset-wide rejection. Warning-only results remain usable. Both persistence and
reconciliation call this same gate, so rejected bars cannot enter cross-source comparison.

Each provider outcome retains the raw observations and full normalized bar set for auditability,
while separately exposing accepted bars, rejected-bar count, validation status, and reconciliation
eligibility. Reconciliation runs only when at least two providers have non-empty eligible accepted
sets. Overall `PipelineStatus` is `succeeded` only when every requested provider completes with
usable data and no rejected bars, `partial` when some failure or rejection occurs but usable data
remains, and `failed` when no provider has usable accepted data. `accepted_with_warnings` alone does
not force a partial result. `fail_fast=True` still raises an `OhlcvPipelineError` retaining the
failure and completed outcomes.

Persistence is disabled by default. When enabled with a unified PostgreSQL repository, raw
observations, validation reports/checks, accepted bars or quarantine decisions, and reconciliation
summary/check rows are written inside one outer transaction. The validation-report repository's
returned database-selected ID is propagated to every accepted-bar and quarantine foreign key and
to the persistence summary. The inner persistence helpers suppress their own transaction boundaries
in this path, so a failure rolls back the full persisted pipeline run. PostgreSQL is the only
persistence implementation; there is no fallback storage.

`open-core/scripts/run_public_ohlcv_pipeline.py` defaults to public-safe in-memory Binance and OKX
fixtures and prints only statuses, counts, and hash validity. `--mode public-network` additionally
requires `ENABLE_PUBLIC_NETWORK_SMOKE=true`, uses a two-candle one-page completed window, and writes
no downloaded output. Persistence requires both `--persist` and
`ENABLE_POSTGRES_PERSISTENCE=true`; PostgreSQL settings and a driver are loaded only after both
explicit gates pass. The CLI never prints raw payloads or connection settings.
