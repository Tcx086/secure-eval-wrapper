# PostgreSQL Storage Design

## Positioning
PostgreSQL is the authoritative storage layer for the target crypto trading framework. SQLite must
not be used as the main design target. Local development should assume Dockerized PostgreSQL with
runtime data stored under ignored local paths.

## Phase 1 Foundation
Phase 1 adds the PostgreSQL storage foundation only:
- Dockerized local PostgreSQL in `infra/docker-compose.postgres.yml`.
- Ordered migration assets under `open-core/db/migrations/`.
- Human-readable schema notes under `open-core/db/schema/`.
- Migration metadata in `audit.schema_migrations`.
- Metadata-only schema verification in `open-core/scripts/verify_postgres_schema.py`.
- Local migration helper in `open-core/scripts/postgres_local.ps1`.
- Repository interface definitions under `open-core/src/secure_eval_wrapper/storage/repositories/`.
- PostgreSQL-only config helpers under `open-core/src/secure_eval_wrapper/storage/postgres/`.

This phase does not implement data collection, alpha logic, signal generation, execution,
backtesting, monitoring runtime behavior, paper trading, or live trading.

## Storage Principles
- Raw source observations are preserved before transformation.
- Validated datasets are separate from raw observations.
- Every run has a stable `run_id`.
- Every storage record used by research, backtesting, or delivery is traceable to a source, config, and manifest.
- Public reports should reference storage IDs and hashes, not private payloads.
- Repository classes own persistence details; domain code should not embed SQL directly.
- PostgreSQL is the only supported authoritative storage target.

## Schema Groups
- `market_data`: raw observations, validated bars, validated trades, funding rates, instrument metadata, future order book snapshots.
- `data_quality`: checks, reconciliation runs/results, validation reports, rejected observations.
- `alpha`: public alpha registry, alpha versions, alpha parameters.
- `signals`: signal runs, signals, signal components, signal conflicts.
- `execution`: order intents, orders, fills, positions, account snapshots.
- `backtesting`: backtest runs, metrics, equity curves, stress results.
- `monitoring`: monitoring events, FIX session events, execution health, risk events, system health.
- `audit`: run manifests, artifacts, artifact hashes, redaction events, migration metadata.

## Migrations
`open-core/db/migrations/0001_initial_schema.sql` creates PostgreSQL schemas for the storage groups
above and defines the initial tables required by the public framework contract:
- `market_data.raw_source_observations`
- `market_data.validated_bars`
- `market_data.validated_trades`
- `market_data.funding_rates`
- `market_data.instruments`
- `data_quality.data_quality_checks`
- `data_quality.validation_reports`
- `alpha.alpha_registry`
- `signals.signal_runs`
- `signals.signals`
- `execution.order_intents`
- `execution.orders`
- `execution.fills`
- `execution.positions`
- `execution.account_snapshots`
- `backtesting.backtest_runs`
- `backtesting.backtest_metrics`
- `backtesting.equity_curves`
- `backtesting.stress_results`
- `monitoring.monitoring_events`
- `monitoring.fix_session_events`
- `monitoring.risk_events`
- `audit.run_manifests`
- `audit.artifacts`

`open-core/db/migrations/0002_schema_migrations.sql` creates `audit.schema_migrations`, which
tracks `migration_id`, `filename`, `sha256`, `applied_at_utc`, and `description` for applied
migration files.

## Table Responsibilities

### `raw_source_observations`
Stores one raw provider response or provider-normalized observation per row. Key fields:
`observation_id`, `source_provider`, `source_exchange`, `source_endpoint`, `symbol_raw`,
`symbol_normalized`, `timeframe`, `observed_at_utc`, `ingested_at_utc`, `payload_jsonb`,
`source_sha256`, and `collection_run_id`.

### `validated_bars`
Stores accepted OHLCV bars after validation. Key fields: `bar_id`, `symbol`, `exchange`,
`timeframe`, `bar_open_time_utc`, `open`, `high`, `low`, `close`, `volume`,
`validation_status`, `validation_report_id`, and `source_observation_ids`. A unique key covers
`symbol`, `exchange`, `timeframe`, and `bar_open_time_utc`.

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
heartbeats, session state transitions, acknowledgements, rejects, and execution reports. The initial
`fix_session_events` table records simulated events only.

### `run_manifests`, `artifacts`, and `schema_migrations`
`run_manifests` stores run-level reproducibility metadata. `artifacts` stores artifact type,
classification, path/URI, hash, and redaction status. `schema_migrations` records migration file
identity and hashes so local schema state can be audited without private data.

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
- Each migration must be ordered and deterministic.
- Applied migration metadata must be recorded in `audit.schema_migrations` immediately after each migration succeeds.
- The verifier must check migration file SHA256 values against stored metadata, and the local runner must fail on recorded/local hash mismatches.
- Schema changes should include repository updates and implementation status updates.
- Test data and local seed data must not include secrets or real account data.

## Local PostgreSQL Workflow
Local PostgreSQL is configured in `infra/docker-compose.postgres.yml`.

Expected defaults:
- PostgreSQL 16 or later.
- Local-only port binding to `127.0.0.1`.
- Database name: `secure_eval_wrapper`.
- User and password loaded from local `.env`.
- Data directory under `var/postgres/`, ignored by Git.

Start, apply, and verify locally with:

```powershell
.\open-core\scripts\postgres_local.ps1 start
.\open-core\scripts\postgres_local.ps1 apply
.\open-core\scripts\postgres_local.ps1 verify
```

The verifier checks migration contents and PostgreSQL catalog metadata. It does not require private
data and does not insert records.

Local database state is disposable unless explicitly backed up. Backups containing private data are
private-only. CI should use ephemeral PostgreSQL containers, not developer local data.

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

Phase 1 defines these repository interfaces only. Concrete implementations, SQL execution,
transactions, and row/domain mapping remain future work and must preserve the public/private
boundary.
