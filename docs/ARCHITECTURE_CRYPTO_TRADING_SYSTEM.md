# Crypto Trading System Architecture

## Purpose
This repository is being rebuilt from a demo evaluation wrapper into a public crypto-focused
trading system framework. The public project should demonstrate professional infrastructure
design across data collection, validation, public alpha research, signal generation, simulated
execution, backtesting, monitoring, PostgreSQL storage, audit trails, local data governance, and
safe public delivery.

The public repository must not contain private strategy logic, API keys, real account data, raw
private exports, or sensitive trade-level logs. Public examples are intentionally educational and
replaceable.

## System Principles
- Crypto-only market focus.
- PostgreSQL is the authoritative storage layer.
- Public alpha examples emit signals, not direct orders.
- Backtest, paper trading, and future live trading share one execution contract.
- Live execution is disabled by default and requires explicit safety controls.
- Every run produces an auditable manifest with data, config, code, artifact, and storage hashes.
- Public delivery exposes aggregate evidence and redacted artifacts only.

## Layered Architecture

### 1. `data_collection`
Collects crypto market observations from public exchange APIs and third-party providers.

Planned data types:
- OHLCV bars.
- Trades.
- Funding rates.
- Instrument metadata.
- Order book snapshots in a later phase.
- Account snapshots in a later phase.

Responsibilities:
- Normalize timestamps to UTC.
- Preserve raw source observations before transformation.
- Record source provider, endpoint, request time, ingest time, symbol mapping, and source hash.
- Mark collection status and validation readiness.
- Avoid storing secrets or real private account payloads in public-safe paths.

### 2. `data_validation`
Verifies raw observations before research or backtesting can use them.

Responsibilities:
- Compare the same symbol/timeframe across more than one exchange or provider where possible.
- Detect missing bars, duplicated timestamps, price outliers, volume anomalies, stale data, and
  symbol mapping inconsistencies.
- Apply tolerance rules for cross-source price deviation.
- Produce validation reports and persist validation status.
- Route accepted records into validated datasets and rejected records into quarantine/audit views.

### 3. `alpha_library`
Contains public alpha examples only. These are infrastructure demonstrations, not private edge.

Allowed public examples:
- Momentum.
- Moving-average crossover.
- Breakout.
- Mean reversion.
- 101 Formulaic Alphas style examples.
- Funding-rate demo alpha.

Responsibilities:
- Register public alpha metadata.
- Use validated data only.
- Emit standardized signals with score, direction, confidence, horizon, and provenance.
- Never emit direct broker orders.

### 4. `signal_generation`
Turns alpha outputs into auditable strategy signals.

Responsibilities:
- Rank signals.
- Apply thresholds.
- Combine multiple alpha outputs.
- Resolve conflicts between long, short, and flat views.
- Compute confidence scores.
- Produce structured signal run records.
- Preserve enough provenance to reproduce the signal without exposing private logic.

### 5. `execution`
Defines one shared execution contract for backtesting, paper trading, and future live trading.

Core concepts:
- `Broker` interface.
- `SimulatedBroker`.
- `PaperBroker` in a future phase.
- `LiveBroker` in a future guarded phase.
- Order intent.
- Order result.
- Fill model.
- Fee model.
- Slippage model.
- Risk guard.
- Position manager.
- Reconciliation.

Backtesting and live-like flows must both submit order intents through the same broker contract.
Backtests must not directly mutate equity from signals.

Live trading safety:
- Disabled by default.
- Requires explicit environment flags.
- Requires API keys from local secrets only.
- Requires max notional limits.
- Requires dry-run support.
- Requires a kill switch.
- Requires pre-flight and post-run risk summaries.

### 6. `backtesting`
Runs historical simulations through the shared execution contract.

Responsibilities:
- Convert signal events into order intents.
- Pass order intents through `SimulatedBroker`.
- Receive fills.
- Update positions through the position manager.
- Compute metrics after simulated fills and portfolio state updates.

Crypto-specific concerns:
- 24/7 market calendar.
- Maker/taker fees.
- Slippage.
- Funding rates.
- Leverage in a future phase.
- Liquidation risk in a future phase.
- Missing candles.
- Exchange outage simulation in a future phase.

### 7. `monitoring`
Observes system health across data, signals, execution, risk, and infrastructure.

Monitoring domains:
- Data health.
- Signal health.
- Execution health.
- Risk health.
- System health.
- Account health in a future phase.

The public framework includes a simulated FIX-style monitoring interface, not a real broker/FIX
connection. It demonstrates professional trading-system monitoring concepts:
- Heartbeat.
- Session state.
- Order acknowledgement.
- Execution report.
- Cancel and reject simulation.
- Latency measurement.
- Dropped connection simulation.

### 8. `storage`
PostgreSQL is the fixed authoritative database. SQLite is not the main design target.

Storage responsibilities:
- Store raw source observations.
- Store validated market data.
- Store quality checks.
- Store alpha registry entries.
- Store signal runs and signals.
- Store order intents, orders, fills, positions, and account snapshots.
- Store backtest runs, metrics, equity curves, and stress results.
- Store monitoring and risk events.
- Store run manifests and artifact metadata.

Local development should assume Dockerized PostgreSQL.

### 9. `audit`
Every run produces a manifest.

Manifest fields:
- Data hash.
- Config hash.
- Code hash.
- Artifact hash.
- Seed.
- Run mode.
- Timestamp.
- Storage reference.

Public redaction rules:
- Do not expose private strategy logic.
- Do not expose secrets.
- Do not expose raw account data.
- Do not expose sensitive trade-level logs.
- Publish aggregate metrics, redacted reports, and reproducibility metadata only.

### 10. `local_data_governance`
Defines local runtime folders, cleanup rules, sensitive file scans, and public artifact
classification.

Ignored local runtime paths:
- `var/cache/`
- `var/raw/`
- `var/tmp/`
- `var/logs/`
- `var/postgres/`
- `.env`
- API key files.
- Raw private exports.

## Data Flow
```text
Public exchange/provider APIs
        |
        v
data_collection
  raw observations + provenance + source hash
        |
        v
data_validation
  missing/duplicate/outlier/stale/cross-source checks
        |
        +--> rejected/quarantined records + validation report
        |
        v
validated market datasets in PostgreSQL
        |
        v
alpha_library
  public alpha examples
        |
        v
signal_generation
  ranked/conflicted/thresholded signals
        |
        v
execution contract
  SimulatedBroker now, PaperBroker/LiveBroker later
        |
        v
backtesting / monitoring / audit / reporting
```

## Execution Flow
```text
validated data
    |
    v
public alpha emits signal
    |
    v
signal_generation creates auditable signal event
    |
    v
risk guard checks limits
    |
    v
order intent
    |
    v
Broker interface
    |
    +--> SimulatedBroker for backtests
    +--> PaperBroker for exchange sandbox/paper mode, future
    +--> LiveBroker for guarded live mode, future and disabled by default
    |
    v
order result + fills
    |
    v
position manager + reconciliation
    |
    v
metrics + monitoring events + run manifest
```

## Backtest, Paper, and Live Relationship
| Mode | Broker | Market Source | Account Risk | Status |
|---|---|---|---|---|
| Backtest | `SimulatedBroker` | Historical validated data | Simulated only | Planned implementation |
| Paper | `PaperBroker` | Exchange sandbox or paper adapter | No real capital | Future |
| Live | `LiveBroker` | Real exchange API | Real capital | Future, disabled by default |

This shared contract prevents a common research bug: treating a signal as if it were a fill.
Signals express intent; brokers produce order acknowledgements, fills, rejects, and state.

## Public and Private Boundary
Public repository may contain:
- Framework interfaces.
- Public demo alphas.
- Synthetic or public sample data.
- Documentation.
- Schema and migration design.
- Simulated execution and monitoring.
- Redacted aggregate reports.

Private/local-only material must stay outside Git:
- Proprietary strategies.
- Private feature engineering.
- API keys and exchange credentials.
- Real account snapshots.
- Raw private exports.
- Sensitive trade-level logs.
- Partner-specific delivery material.

## Future Extensibility
The design intentionally keeps extension points narrow:
- Add providers through `data_collection/providers`.
- Add validation checks through `data_validation/checks`.
- Add public alphas through `alpha_library`.
- Add broker adapters through `execution/brokers`.
- Add repositories without changing domain contracts.
- Add dashboards from monitoring events and artifact tables.

Runtime code should be implemented only after the Phase 0 documents are accepted.