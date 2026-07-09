# Data Collection and Validation

## Purpose
The target data layer will collect crypto market data from public exchange APIs and provider APIs,
preserve source provenance, validate quality, and only promote accepted datasets into research and
backtesting. Phase 2 now includes contracts and offline preparation utilities only; no real
provider API calls, validation algorithms, or dataset promotion behavior are implemented yet.

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
reports, and cross-source reconciliation results. `DataValidator`, `CrossSourceReconciler`, and
`DatasetPromoter` are abstract boundaries only. `ValidationCheckStatus` describes an individual
check outcome, while `ValidationStatus` describes the final dataset gate decision. Stable
`QuarantineReason` values describe why a future promoter may reject an observation without
promoting it to a validated table.

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

## Provider Strategy
The framework should support multiple providers or exchanges for the same logical instrument:
Binance, OKX, Bybit, Coinbase, and third-party aggregators where licensing permits.

The Phase 2A registry records the following planning metadata only:

| Provider | OHLCV | Trades | Funding rates | Instruments |
|---|---|---|---|---|
| Binance | planned | planned | planned | planned |
| OKX | planned | planned | planned | planned |
| Bybit | planned | planned | planned | planned |
| Coinbase | planned | planned | unknown | planned |

`planned` does not mean implemented or verified. `unknown` means the capability must be resolved
when a future provider adapter is designed. The registry deliberately contains no URLs, clients,
authentication configuration, or credentials.

The offline sample provider is a fixture reader, not a concrete exchange adapter. All Binance,
OKX, Bybit, and Coinbase adapters remain planned or unknown.

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

Phase 2A models these stages, and Phase 2B supplies offline parsing guards and provenance helpers.
The complete validation, reconciliation, promotion, and PostgreSQL-backed flow remains future work.

## Single-Source Checks
Required checks:
- Missing bars.
- Duplicated timestamps.
- Non-monotonic timestamps.
- Invalid OHLC relationships.
- Negative or impossible volume.
- Price outliers.
- Volume anomalies.
- Stale data.
- Partial candles where final candles are required.
- Symbol mapping inconsistencies.
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
Each validation report should include validation run ID, dataset reference, provider set, time
range, symbols, timeframes, check summary, accepted count, rejected count, warning count, tolerance
config hash, source hashes, report hash, and final status.

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