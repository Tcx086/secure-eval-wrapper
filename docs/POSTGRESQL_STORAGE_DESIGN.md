# PostgreSQL Storage Design

## Positioning
PostgreSQL is the authoritative storage layer for the target crypto trading framework. SQLite must
not be used as the main design target. Local development should assume Dockerized PostgreSQL with
runtime data stored under ignored local paths.

## Storage Principles
- Raw source observations are preserved before transformation.
- Validated datasets are separate from raw observations.
- Every run has a stable `run_id`.
- Every storage record used by research, backtesting, or delivery is traceable to a source, config, and manifest.
- Public reports should reference storage IDs and hashes, not private payloads.
- Repository classes own persistence details; domain code should not embed SQL directly.

## Schema Groups
- `market_data`: raw observations, validated bars, validated trades, funding rates, instrument metadata, future order book snapshots.
- `data_quality`: checks, reconciliation runs/results, validation reports, rejected observations.
- `alpha`: public alpha registry, alpha versions, alpha parameters.
- `signals`: signal runs, signals, signal components, signal conflicts.
- `execution`: order intents, orders, fills, positions, account snapshots.
- `backtesting`: backtest runs, metrics, equity curves, stress results.
- `monitoring`: monitoring events, FIX session events, execution health, risk events, system health.
- `audit`: run manifests, artifacts, artifact hashes, redaction events.

## Table Responsibilities

### `raw_source_observations`
Stores one raw provider response or provider-normalized observation per row. Key fields:
`observation_id`, `source_provider`, `source_exchange`, `source_endpoint`, `symbol_raw`,
`symbol_normalized`, `timeframe`, `observed_at_utc`, `ingested_at_utc`, `payload_jsonb`,
`source_sha256`, and `collection_run_id`.

### `validated_bars`
Stores accepted OHLCV bars after validation. Key fields: `bar_id`, `symbol`, `exchange`,
`timeframe`, `bar_open_time_utc`, `open`, `high`, `low`, `close`, `volume`,
`validation_status`, `validation_report_id`, and `source_observation_ids`. A unique key should
cover `symbol`, `exchange`, `timeframe`, and `bar_open_time_utc`.

### `data_quality_checks`
Stores individual check results. Key fields: `check_id`, `validation_run_id`, `check_type`,
`severity`, `symbol`, `timeframe`, `window_start_utc`, `window_end_utc`, `status`, and
`details_jsonb`.

### `validation_reports`
Gatekeeping records that decide whether data can enter research/backtesting. Key fields:
`validation_report_id`, `validation_run_id`, `dataset_ref`, `accepted_count`, `rejected_count`,
`warning_count`, `status`, and `report_sha256`.

### `alpha_registry`
Public alpha catalog. Key fields: `alpha_id`, `alpha_name`, `description`, `public_example`,
`status`, and `created_at_utc`.

### `signal_runs` and `signals`
`signal_runs` stores job metadata: `signal_run_id`, `run_id`, `dataset_ref`, `config_sha256`,
`code_sha256`, `seed`, timestamps, and status. `signals` stores standardized outputs:
`signal_id`, `signal_run_id`, `alpha_id`, `symbol`, `timestamp_utc`, `direction`, `score`,
`confidence`, `horizon`, and `provenance_jsonb`.

### `order_intents`, `orders`, and `fills`
`order_intents` stores pre-broker intent records. `orders` stores broker acknowledgements,
statuses, and rejects. `fills` stores execution fills with price, quantity, fee, liquidity flag,
and fill timestamp.

### `positions` and `account_snapshots`
`positions` stores run/account/symbol state updated from fills. `account_snapshots` is future-facing
for paper/live account reconciliation and must be private-only when real accounts are involved.

### `backtest_runs`, `backtest_metrics`, `equity_curves`, and `stress_results`
These tables store simulation metadata, aggregate metrics, time-indexed portfolio state, and
scenario/stress outputs.

### `monitoring_events` and `fix_session_events`
Stores data/signal/execution/risk/system health events plus simulated FIX-style messages such as
heartbeats, session state transitions, acknowledgements, rejects, and execution reports.

### `run_manifests` and `artifacts`
`run_manifests` stores run-level reproducibility metadata: `run_id`, `run_mode`, `data_sha256`,
`config_sha256`, `code_sha256`, `artifact_sha256`, `seed`, `storage_ref`, and timestamp.
`artifacts` stores artifact type, classification, path/URI, hash, and redaction status.

## Design-Level Relationships
- `validation_reports` reference raw observations through validation run metadata.
- `validated_bars` reference validation reports.
- `signal_runs` reference validated dataset identifiers.
- `signals` reference `signal_runs` and `alpha_registry`.
- `order_intents` reference signals.
- `orders` reference order intents.
- `fills` reference orders.
- `positions` are derived from fills by run/account/symbol.
- `backtest_runs` reference signal runs and execution model metadata.
- Metrics, equity curves, and stress results reference backtest runs.
- Run manifests reference major run-level hashes and storage identifiers.
- Artifacts reference run manifests and redaction state.

## Migration Strategy
- Migrations live under `open-core/db/migrations/`.
- Schema documentation lives under `open-core/db/schema/`.
- Each migration must be ordered, deterministic, and reversible where practical.
- Schema changes should include repository updates and implementation status updates.
- Test data and local seed data must not include secrets or real account data.

## Repository Pattern
Runtime code should access PostgreSQL through repositories:
- `MarketDataRepository`
- `DataQualityRepository`
- `AlphaRepository`
- `SignalRepository`
- `ExecutionRepository`
- `BacktestRepository`
- `MonitoringRepository`
- `AuditRepository`
- `ArtifactRepository`

Repositories are responsible for SQL execution, transactions, row/domain mapping, and avoiding
accidental public exposure of private fields.

## Dockerized Local PostgreSQL Plan
The planned local service should live in `infra/docker-compose.postgres.yml`.

Expected defaults:
- PostgreSQL 16 or later.
- Local-only port binding.
- Database name: `secure_eval_wrapper`.
- User and password loaded from local `.env`.
- Data directory under `var/postgres/`, ignored by Git.

Local database state is disposable unless explicitly backed up. Backups containing private data are
private-only. CI should use ephemeral PostgreSQL containers, not developer local data.