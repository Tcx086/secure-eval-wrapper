# Secure Eval Wrapper

[![CI](https://github.com/Tcx086/secure-eval-wrapper/actions/workflows/ci.yml/badge.svg)](https://github.com/Tcx086/secure-eval-wrapper/actions/workflows/ci.yml)

A public, auditable, and reproducible framework for building crypto trading systems without exposing private alpha, credentials, account data, or sensitive trade records.

The project is developed in explicit, auditable phases. Architecture, PostgreSQL foundations, public market data, public alpha, and standardized signals are complete. Deterministic simulated execution and event-driven backtesting are complete after local PostgreSQL 16 and independent GitHub Actions validation.

> **Current status:** Phase 6 monitoring and strictly simulated FIX is complete. Phase 7 safe paper trading is in progress with an offline internal venue and gated official demo adapter; live execution and external production FIX remain unimplemented.

## Why this project exists

Most trading repositories focus on strategy returns while leaving data quality, reproducibility, execution semantics, and operational safety underspecified. This project takes the opposite approach.

The main objectives are:

- preserve a strict boundary between public infrastructure and private research;
- make data lineage, IDs, hashes, validation decisions, and database writes auditable;
- prevent lookahead, data contamination, silent fallback, and inconsistent backtests;
- use one shared execution contract for future backtest, paper, and guarded live modes;
- keep live trading disabled by default;
- avoid unverifiable claims, fabricated data, and fake execution results.

This repository is infrastructure-first. It is not presented as a profitable strategy, a signal service, or a ready-to-run trading bot.

## System direction

```text
Public market endpoints
        |
        v
Raw observations + source provenance
        |
        v
Normalization + UTC enforcement
        |
        v
Validation + accepted/rejected gate
        |                 |
        |                 +--> Quarantine + audit evidence
        v
Validated PostgreSQL datasets
        |
        +--> Cross-source reconciliation
        |
        v
Public alpha library                 [Phase 3]
        |
        v
Standardized signal generation       [Phase 4]
        |
        v
Simulated execution + backtesting    [Phase 5]
        |
        v
Monitoring + simulated FIX           [Phase 6]
        |
        v
Paper/live                           [Future; disabled]
```

Signals are not fills. Phase 5 backtests create order intents, pass them through `SimulatedBroker`, receive fills, update cash and positions only from fills/funding, and only then calculate portfolio metrics.

## Implementation status

| Phase | Scope | Status |
|---|---|---|
| 0 | Architecture, project controls, public/private boundary | Completed |
| 1 | PostgreSQL infrastructure, migrations, repository interfaces | Completed |
| 2 | Public market-data collection, validation, reconciliation, persistence | Completed |
| 3 | Public Alpha Library | Completed; audit repair accepted |
| 4 | Standardized Signal Generation | Completed; audit repair accepted |
| 5 | Simulated Execution and Event-Driven Backtesting | Completed; PostgreSQL and CI validated |
| 6 | Monitoring and strictly simulated FIX 4.4-compatible profile | Completed; first-independent-audit repairs accepted |
| 7 | Paper Trading | Future |
| 8 | Guarded Live Execution | Future; disabled by default |
| 9 | Reporting and Public Delivery | Future |

The authoritative progress records are:

- [Simulated execution and backtesting](docs/SIMULATED_EXECUTION_AND_BACKTESTING.md)
- [Monitoring and simulated FIX](docs/MONITORING_AND_SIMULATED_FIX.md)
- [`docs/IMPLEMENTATION_STATUS.md`](docs/IMPLEMENTATION_STATUS.md)
- [`.project/implementation_status.json`](.project/implementation_status.json)

Completed and planned work must remain synchronized between these two files.

## Phase 2: implemented public data layer

The current runtime framework supports the following public market data:

| Provider component | OHLCV | Trades | Funding rates | Instruments |
|---|---:|---:|---:|---:|
| Binance Spot (`binance`) | Implemented | Implemented | Not applicable | Implemented |
| Binance USD-M (`binance_usdm`) | Not implemented | Not implemented | Implemented | Implemented |
| OKX V5 Public (`okx`) | Implemented | Implemented | Implemented | Implemented |
| Bybit | Planned | Planned | Planned | Planned |
| Coinbase | Planned | Planned | Unknown | Planned |

Implemented controls include:

- exact UTC handling and half-open windows `[start, end)`;
- deterministic IDs and canonical SHA-256 hashes;
- raw-observation provenance before normalization;
- `Decimal`-based normalized records;
- accepted/rejected validation gates;
- deterministic quarantine decisions;
- Binance and OKX OHLCV reconciliation;
- typed pipelines for OHLCV, trades, funding rates, and instruments;
- PostgreSQL-only authoritative persistence;
- transaction boundaries with rollback on child-write failure;
- idempotent retries with content-conflict detection;
- immutable instrument metadata versions and drift detection;
- explicit Spot, perpetual-swap, and dated-future identities;
- grounded funding-interval evidence;
- explicit `SKIPPED` outcomes when interval evidence is unavailable;
- fixture-default command-line execution with network and persistence disabled by default.

The implementation does **not** silently assume an eight-hour funding interval when the provider does not supply sufficient evidence.

## Safety defaults

The project is deliberately restrictive.

- PostgreSQL is the only authoritative storage target.
- SQLite is not an authoritative database or fallback.
- Public-network collection is disabled unless explicitly enabled.
- PostgreSQL persistence is independently disabled unless explicitly enabled.
- Live trading is disabled and not implemented in the current phase.
- No exchange credentials are required for the completed public-data workflow.
- No private strategy code, private feature engineering, real account snapshots, or real trade logs belong in this repository.
- Generated artifacts must be classified and redacted before public delivery.

See [`AGENTS.md`](AGENTS.md) for repository-wide engineering rules.

## Quick start

### Requirements

- Python 3.10 or newer;
- Docker with Docker Compose for optional local PostgreSQL;
- PowerShell for the included PostgreSQL helper;
- `psycopg` or `psycopg2` only when Python-based PostgreSQL persistence is enabled.

### Clone the repository

```bash
git clone https://github.com/Tcx086/secure-eval-wrapper.git
cd secure-eval-wrapper
```

### Run the complete offline fixture pipeline

This is the safest default. It uses classified synthetic public-safe fixtures, opens no exchange connection, and performs no database writes.

```bash
python open-core/scripts/run_public_market_data_pipeline.py
```

The summary reports provider status, normalized and accepted counts, validation outcomes, persistence state, and hash validity. It does not print full provider payloads or connection secrets.

### Optional bounded public-network smoke mode

Network access requires an explicit environment flag.

PowerShell:

```powershell
$env:ENABLE_PUBLIC_NETWORK_SMOKE = "true"
python open-core\scripts\run_public_market_data_pipeline.py --mode public-network
```

Bash:

```bash
ENABLE_PUBLIC_NETWORK_SMOKE=true \
python open-core/scripts/run_public_market_data_pipeline.py --mode public-network
```

Public-network mode does not automatically enable persistence.

## Phase 6 offline demos

Both commands are socket-free and database-free by default:

```bash
secure-eval-monitor
secure-eval-fix-sim
```

PostgreSQL persistence requires both `--persist` and `ENABLE_POSTGRES_PERSISTENCE=true`, plus explicit `POSTGRES_*` configuration. No SQLite or file fallback exists. The simulated FIX demo performs a buy fill followed by a valid inventory-closing sell and never opens external FIX connectivity.
## Local PostgreSQL

Create a local environment file and replace the example password:

PowerShell:

```powershell
Copy-Item .env.example .env
```

Bash:

```bash
cp .env.example .env
```

Start PostgreSQL, apply migrations, and verify the catalog:

```powershell
.\open-core\scripts\postgres_local.ps1 start
.\open-core\scripts\postgres_local.ps1 apply
.\open-core\scripts\postgres_local.ps1 verify
```

The database binds to `127.0.0.1`, and local PostgreSQL state is stored under ignored runtime paths.

To persist the fixture pipeline, both the environment gate and CLI flag are required:

```powershell
$env:ENABLE_POSTGRES_PERSISTENCE = "true"
python open-core\scripts\run_public_market_data_pipeline.py --persist
```

There is no SQLite or file-database fallback.

## Tests and static compilation

PowerShell:

```powershell
$env:PYTHONPATH = "open-core\src"
python -m unittest discover -s open-core\tests -p "test_*.py"
python -m compileall open-core\src open-core\scripts open-core\tests
```

Bash:

```bash
PYTHONPATH=open-core/src python -m unittest discover -s open-core/tests -p "test_*.py"
python -m compileall open-core/src open-core/scripts open-core/tests
```

The test suite is designed to run offline and includes deterministic hashing, normalization, validation, reconciliation, persistence, transaction, migration, socket-isolation, and security-boundary coverage.

## Reproducibility and audit model

The framework separates logical identity from collection-time provenance.

Stable event hashes are based on provider identity and economic event content. Volatile fields such as collection run IDs, request timestamps, ingestion timestamps, endpoints, and request parameters remain available as provenance but do not alter the logical identity of the same market event.

Persistent workflows use:

- stable UUIDs and hashes;
- source and dataset provenance;
- validation-report identities;
- migration file hashes;
- PostgreSQL foreign keys and uniqueness constraints;
- database-selected IDs propagated to child records;
- explicit conflict errors when the same logical identity arrives with different content.

This design is intended to make a run explainable without publishing private trading logic.

## Public alpha-to-signal quick start

Phases 3 and 4 are complete: the framework now includes a transparent, lookahead-safe public alpha registry and standardized research-signal pipeline over accepted Phase 2 records. Run the fully offline synthetic fixture from the repository root:

```powershell
python open-core\scripts\run_public_alpha_signal_pipeline.py
```

The run evaluates eleven public examples, then produces single-alpha and combined long/short/flat research signals. It opens no sockets and does not persist by default. See [Public Alpha Library](docs/PUBLIC_ALPHA_LIBRARY.md) and [Signal Generation](docs/SIGNAL_GENERATION.md).

## Repository layout

```text
secure-eval-wrapper/
|-- AGENTS.md                         repository-wide engineering controls
|-- docs/                             architecture and implementation records
|-- infra/                            local PostgreSQL infrastructure
|-- open-core/
|   |-- data/sample/                  classified public-safe fixtures
|   |-- db/migrations/                ordered PostgreSQL migrations
|   |-- scripts/                      fixture, migration, and verification CLIs
|   |-- src/secure_eval_wrapper/
|   |   |-- data_collection/          public provider adapters and contracts
|   |   |-- data_validation/          validation, quarantine, reconciliation
|   |   |-- data_pipeline/            provider-neutral orchestration
|   |   |-- alpha/                    public point-in-time alpha research
|   |   |-- signals/                  standardized research signals
|   |   `-- storage/                  PostgreSQL repositories and mappings
|   `-- tests/                        offline and persistence-boundary tests
|-- security/                         baseline controls and threat model
|-- delivery/                         public-safe or redacted artifacts
`-- var/                              ignored local runtime state
```

Some earlier evaluation-wrapper components remain in the repository for compatibility and historical context. The current rebuild is governed by the phased architecture and status files above; old demo metrics must not be treated as current system validation.

## Documentation

- [Crypto Trading System Architecture](docs/ARCHITECTURE_CRYPTO_TRADING_SYSTEM.md)
- [Data Collection and Validation](docs/DATA_COLLECTION_AND_VALIDATION.md)
- [PostgreSQL Storage Design](docs/POSTGRESQL_STORAGE_DESIGN.md)
- [Public Alpha Library](docs/PUBLIC_ALPHA_LIBRARY.md)
- [Signal Generation](docs/SIGNAL_GENERATION.md)
- [Execution and FIX-Style Monitoring](docs/EXECUTION_AND_FIX_MONITORING.md)
- [Safe Paper Trading](docs/SAFE_PAPER_TRADING.md)
- [Live Trading Safety](docs/LIVE_TRADING_SAFETY.md)
- [Local Data Governance](docs/LOCAL_DATA_GOVERNANCE.md)
- [Folder Structure](docs/FOLDER_STRUCTURE.md)
- [Implementation Status](docs/IMPLEMENTATION_STATUS.md)

## Public/private boundary

Public repository content may include:

- framework contracts and orchestration;
- public endpoint adapters;
- transparent educational alpha examples;
- synthetic or public-safe fixture data;
- simulated execution and monitoring examples;
- aggregate metrics and redacted manifests.

The following must remain private and outside Git:

- proprietary alpha and private feature engineering;
- exchange credentials and signing material;
- real account or balance snapshots;
- real trade logs and raw private exports;
- local database contents;
- partner-specific confidential material.

The intended principle is simple: **make the infrastructure inspectable without publishing the edge.**

## Roadmap

Phases 3 and 4 are complete and auditable: public alphas produce continuous point-in-time `AlphaValue` records, and standardized signals apply deterministic ranking, thresholding, combination, conflict, and confidence rules with PostgreSQL lineage.

Phase 5 simulated execution and backtesting is complete through its fourth independent audit. Phase 6 monitoring and the strictly simulated FIX API are completed after first-independent-audit repair and GitHub Actions validation. Phase 7 safe paper trading is in progress; the internal venue and official OKX demo boundary are implemented, while live trading remains unimplemented and unreachable.

## Disclaimer

This repository is for software architecture, data engineering, testing, and research infrastructure. It does not provide investment advice, does not guarantee trading performance, and should not be connected to real capital without independent review and the future guarded execution controls described in the roadmap.

## Phase 6 monitoring and simulated FIX

The public framework now includes deterministic point-in-time monitoring and a strictly simulated, in-process FIX 4.4-compatible subset. Run `secure-eval-monitor` or `secure-eval-fix-sim` after installing `open-core`. Both demos are synthetic, socket-free, and persistence-free by default. See [Monitoring and Simulated FIX](docs/MONITORING_AND_SIMULATED_FIX.md). Phase 7 adds a separate safe PaperBroker and gated official demo adapter. No live broker or external production FIX connection is implemented.

## Phase 7 safe paper trading

`secure-eval-paper-internal` runs the complete deterministic, socket-free paper lifecycle with acknowledgement, partial fills, cancellation, timeout recovery, reconciliation, and kill handling. External OKX demo support is separately gated and uses only the immutable official-demo route/header contract. PostgreSQL remains the audit authority when persistence is requested. See [Safe Paper Trading](docs/SAFE_PAPER_TRADING.md). No production or live execution is implemented.
