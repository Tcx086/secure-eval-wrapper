# Folder Structure

## Purpose
This document defines the target repository layout for the crypto trading framework rebuild. The
layout is documentation-first in Phase 0; folders do not need runtime implementations yet.

## Proposed Layout
```text
secure-eval-wrapper/
â”œâ”€ README.md
â”œâ”€ docs/
â”‚  â”œâ”€ ARCHITECTURE_CRYPTO_TRADING_SYSTEM.md
â”‚  â”œâ”€ FOLDER_STRUCTURE.md
â”‚  â”œâ”€ POSTGRESQL_STORAGE_DESIGN.md
â”‚  â”œâ”€ DATA_COLLECTION_AND_VALIDATION.md
â”‚  â”œâ”€ EXECUTION_AND_FIX_MONITORING.md
â”‚  â”œâ”€ LOCAL_DATA_GOVERNANCE.md
â”‚  â””â”€ IMPLEMENTATION_STATUS.md
â”œâ”€ security/
â”œâ”€ api-spec/
â”œâ”€ infra/
â”‚  â”œâ”€ docker-compose.postgres.yml
â”‚  â””â”€ postgres/
â”œâ”€ open-core/
â”‚  â”œâ”€ main.py
â”‚  â”œâ”€ db/
â”‚  â”‚  â”œâ”€ migrations/
â”‚  â”‚  â”œâ”€ schema/
â”‚  â”‚  â””â”€ README.md
â”‚  â”œâ”€ scripts/
â”‚  â”œâ”€ data/
â”‚  â”‚  â””â”€ sample/
â”‚  â””â”€ src/
â”‚     â””â”€ secure_eval_wrapper/
â”‚        â”œâ”€ data_collection/
â”‚        â”œâ”€ data_validation/
â”‚        â”œâ”€ alpha_library/
â”‚        â”œâ”€ signal_generation/
â”‚        â”œâ”€ execution/
â”‚        â”‚  â”œâ”€ brokers/
â”‚        â”‚  â”œâ”€ fix_simulator/
â”‚        â”‚  â””â”€ risk/
â”‚        â”œâ”€ backtesting/
â”‚        â”œâ”€ monitoring/
â”‚        â”œâ”€ storage/
â”‚        â”‚  â”œâ”€ postgres/
â”‚        â”‚  â””â”€ repositories/
â”‚        â”œâ”€ audit/
â”‚        â”œâ”€ reporting/
â”‚        â””â”€ cli/
â”œâ”€ runner/
â”œâ”€ system/
â”œâ”€ delivery/
â””â”€ var/
   â”œâ”€ cache/
   â”œâ”€ raw/
   â”œâ”€ tmp/
   â”œâ”€ logs/
   â””â”€ postgres/
```

## Top-Level Responsibilities
- `README.md`: project introduction, current status, runnable demo note, and architecture links.
- `docs/`: architecture and implementation-control documentation.
- `security/`: threat model, security baseline, redaction rules, and future live-execution safety notes.
- `api-spec/`: public API contracts and stubs.
- `infra/`: Dockerized PostgreSQL and future deployment/observability definitions.
- `open-core/`: public runtime package. Existing demo code remains until the new framework is implemented.
- `runner/`: top-level orchestration entrypoints.
- `system/`: current stage pipeline and orchestrator.
- `delivery/`: public-safe generated or curated delivery artifacts.
- `var/`: ignored local runtime data.

## `open-core/src/secure_eval_wrapper/`
Target Python package namespace for the rebuilt framework.

### `data_collection/`
Provider adapters and collection jobs. Expected modules include `providers/`, `normalizers/`,
`provenance.py`, `source_hashing.py`, and `jobs.py`.

### `data_validation/`
Quality checks, cross-source reconciliation, validation reports, tolerances, and quarantine flows.

### `alpha_library/`
Public alpha examples only: momentum, moving-average crossover, breakout, mean reversion,
formulaic examples, funding-rate demo, and registry metadata.

### `signal_generation/`
Signal schema, ranking, thresholding, combination, conflict resolution, and confidence scoring.

### `execution/`
Shared execution contract and execution-domain components:
- `broker.py`
- `order_intent.py`
- `order_result.py`
- `fills.py`
- `fees.py`
- `slippage.py`
- `positions.py`
- `reconciliation.py`

Subfolders:
- `brokers/`: `simulated.py`, future `paper.py`, future `live.py`.
- `fix_simulator/`: session, heartbeat, execution report, rejects, latency simulation.
- `risk/`: limits, risk guard, kill switch, exposure checks.

### `backtesting/`
Backtest engine, event loop, portfolio accounting, metrics, stress tests, and funding logic.

### `monitoring/`
Data, signal, execution, risk, system, and future account health checks.

### `storage/`
PostgreSQL access and repository abstractions.
- `postgres/`: connections, migration helpers, SQL loading.
- `repositories/`: typed access to market data, signals, orders, fills, metrics, manifests, and artifacts.

### `audit/`
Run manifest building, hashing, redaction, and audit trail helpers.

### `reporting/`
Public reports, private reports, model cards, and artifact packaging.

### `cli/`
Command-line entrypoints for collection, validation, backtesting, reporting, and local audits.

## `open-core/db/`
Database definitions and migration assets:
- `migrations/`: ordered SQL or migration-tool files.
- `schema/`: human-readable schema group definitions.
- `README.md`: database setup and migration notes.

## `open-core/scripts/`
Developer scripts and run scripts. Scripts should call package modules rather than embedding
business logic.

## `infra/`
Infrastructure-only configuration:
- Dockerized PostgreSQL.
- Future observability stack definitions.
- Future deployment templates.

## `delivery/`
Delivery artifacts are classified before sharing:
- Public-safe reports.
- Public-safe metrics.
- Redacted manifests.
- Model cards.
- Artifact hashes.

Private-only outputs must not be placed here unless ignored and explicitly classified.

## `var/`
Local-only runtime data:
- `var/cache/`: provider caches and derived temporary caches.
- `var/raw/`: raw downloads and raw private exports.
- `var/tmp/`: short-lived working files.
- `var/logs/`: local logs.
- `var/postgres/`: local PostgreSQL data directory.

All `var/` runtime paths should be ignored by Git.