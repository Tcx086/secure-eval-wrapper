# Data Collection and Validation

## Purpose
The target data layer will collect crypto market data from public exchange APIs and provider APIs,
preserve source provenance, validate quality, and only promote accepted datasets into research and
backtesting. Phase 2 now includes contracts, offline preparation, sample and Binance Spot OHLCV
normalization, single-source validation/reporting, and PostgreSQL-backed offline persistence.
Cross-source reconciliation is not implemented. Automated tests remain offline; public-network
access exists only through the explicitly enabled Binance smoke script.

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
network-free `DataValidator`; `CrossSourceReconciler` and `DatasetPromoter` remain abstract future
boundaries. `ValidationCheckStatus` describes an individual check outcome, while
`ValidationStatus` describes the final dataset gate decision. Stable `QuarantineReason` values
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
| OKX | planned | planned | planned | planned |
| Bybit | planned | planned | planned | planned |
| Coinbase | planned | planned | unknown | planned |

`planned` does not mean implemented or verified. `unknown` means the capability must be resolved
when a future provider adapter is designed. The registry deliberately contains no URLs, clients,
authentication configuration, or credentials; fetch behavior stays in provider modules.

The offline sample provider remains a fixture reader rather than an exchange adapter. Binance Spot
OHLCV is implemented in Phase 2E; Binance's other capabilities and all OKX, Bybit, and Coinbase
capabilities remain planned or unknown.

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
through the same normalization and validation path. Cross-source reconciliation remains future work.

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
When multiple providers cover the same symbol and timeframe, compare close price deviation,
high/low range deviation, volume deviation, missing intervals, timestamp alignment, and symbol or
instrument metadata consistency.

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

Only public-safe offline fixtures are in scope for this persistence path. Binance provider results
are not persisted by Phase 2E. Additional provider collection and cross-source reconciliation remain
future Phase 2 work.

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
