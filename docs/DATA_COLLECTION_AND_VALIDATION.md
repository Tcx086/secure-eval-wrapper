# Data Collection and Validation

## Purpose
The data layer collects crypto market data from public exchange APIs and provider APIs, preserves
source provenance, validates quality, and only promotes accepted datasets into research and
backtesting.

## Collection Scope
Initial crypto-only data types:
- OHLCV bars.
- Trades.
- Funding rates.
- Instrument metadata.

Future data types:
- Order book snapshots.
- Account snapshots for paper/live account monitoring.

## Provider Strategy
The framework should support multiple providers or exchanges for the same logical instrument:
Binance, OKX, Bybit, Coinbase, and third-party aggregators where licensing permits.

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

## Timestamp Rules
- Store all timestamps in UTC.
- Keep provider timestamps as source metadata when useful.
- Reject or quarantine records with ambiguous timestamps.
- Bars should use explicit open time and timeframe.
- Trades should use event time and ingest time.
- Funding rates should use funding interval time.

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