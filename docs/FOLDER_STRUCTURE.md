# Folder Structure

## Purpose
This document defines the target repository layout for the crypto trading framework rebuild. The
layout is documentation-first in Phase 0 and is being implemented incrementally in later phases.

## Proposed Layout
```text
secure-eval-wrapper/
|-- README.md
|-- docs/
|   |-- ARCHITECTURE_CRYPTO_TRADING_SYSTEM.md
|   |-- FOLDER_STRUCTURE.md
|   |-- POSTGRESQL_STORAGE_DESIGN.md
|   |-- DATA_COLLECTION_AND_VALIDATION.md
|   |-- EXECUTION_AND_FIX_MONITORING.md
|   |-- LOCAL_DATA_GOVERNANCE.md
|   `-- IMPLEMENTATION_STATUS.md
|-- security/
|-- api-spec/
|-- infra/
|   |-- docker-compose.postgres.yml
|   `-- postgres/
|-- open-core/
|   |-- main.py
|   |-- db/
|   |   |-- migrations/
|   |   |   `-- 0001_initial_schema.sql
|   |   |-- schema/
|   |   |   `-- README.md
|   |   `-- README.md
|   |-- scripts/
|   |   |-- postgres_local.ps1
|   |   `-- verify_postgres_schema.py
|   |-- data/
|   |   `-- sample/
|   `-- src/
|       `-- secure_eval_wrapper/
|           |-- data_collection/
|           |-- data_validation/
|           |-- alpha_library/
|           |-- signal_generation/
|           |-- execution/
|           |   |-- brokers/
|           |   |-- fix_simulator/
|           |   `-- risk/
|           |-- backtesting/
|           |-- monitoring/
|           |-- storage/
|           |   |-- postgres/
|           |   `-- repositories/
|           |-- audit/
|           |-- reporting/
|           `-- cli/
|-- runner/
|-- system/
|-- delivery/
`-- var/
    |-- cache/
    |-- raw/
    |-- tmp/
    |-- logs/
    `-- postgres/
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
## Phase 5 implemented package delta

The implemented layout adds `open-core/pyproject.toml`, the `secure_eval_wrapper.execution` and
`secure_eval_wrapper.backtesting` packages, PostgreSQL Phase 5 row/repository modules,
`storage/backtest_bundle.py`, migration `0009_phase5_simulated_execution_backtesting.sql`, the
fixture wrapper `scripts/run_public_backtest_pipeline.py`, cross-platform migration/catalog
validation scripts, dedicated Phase 5 tests, and `.github/workflows/ci.yml`. `execution/brokers/`
contains only `simulated.py`; future paper/live brokers remain absent. The separate Phase 6 `fix/` package is strictly simulated and routes only to `SimulatedBroker`.

### Phase 6 additions

- `open-core/src/secure_eval_wrapper/monitoring/`: immutable contracts, configuration, category health evaluators, aggregation, incidents, engine, CLI, and atomic persistence.
- `open-core/src/secure_eval_wrapper/fix/`: simulated FIX tags, message contracts/factories, codec, validation, session, gateway, latency, faults, CLI, and persistence exports.
- `open-core/db/migrations/0013_phase6_monitoring_simulated_fix.sql`: projection valuation repair and initial Phase 6 PostgreSQL schema.
- `open-core/db/migrations/0014_phase6_first_audit_repairs.sql`: rejected observations, replay hashes, event-chain integrity, and guarded session projection.
- `open-core/scripts/run_public_monitoring.py` and `run_simulated_fix.py`: offline source-checkout demos.

## Phase 7 additions

- `open-core/src/secure_eval_wrapper/paper/`: safe paper contracts, internal venue, official demo adapter, lifecycle, accounting, reconciliation, recovery, kill switch, transport, credentials, persistence, and CLIs.
- `open-core/db/migrations/0016_phase7_safe_paper_trading.sql`: separate paper audit schema objects.
- `open-core/scripts/run_paper_*.py` and `run_internal_paper.py`: safe source-checkout commands.
- `open-core/tests/test_phase7_*.py`: offline, adapter, boundary, and PostgreSQL coverage.

Paper state is not stored under the Phase 5 simulated execution projections. Local PostgreSQL runtime state remains under ignored `var/postgres/` only.
