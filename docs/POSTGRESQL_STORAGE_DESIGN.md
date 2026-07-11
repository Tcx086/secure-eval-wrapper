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
- `data_quality`: checks, reconciliation runs/results, validation reports, quarantine decisions, and rejected observations.
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
`open-core/db/migrations/0003_data_quality_quarantine.sql` adds the indexed data-quality
quarantine decision table used by Phase 2D offline persistence.
`open-core/db/migrations/0004_reconciliation_persistence.sql` adds auditable cross-source
reconciliation summaries and child check results for Phase 2H.


## Table Responsibilities

### `raw_source_observations`
Stores one raw provider response or provider-normalized observation per row. Key fields:
`observation_id`, `source_provider`, `source_exchange`, `source_endpoint`, `symbol_raw`,
`symbol_normalized`, `timeframe`, `observed_at_utc`, `ingested_at_utc`, `payload_jsonb`,
`source_sha256`, and `collection_run_id`.

### `validated_bars`
Stores accepted OHLCV bars after validation. Key fields: `bar_id`, `symbol`, `exchange`,
`timeframe`, `bar_open_time_utc`, `bar_close_time_utc`, `is_final`, `open`, `high`, `low`, `close`, `volume`,
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

Phase 2D adds concrete PostgreSQL implementations, parameterized SQL execution, transactions, and
row/domain mappings for offline validation persistence. Future implementations must preserve the
public/private boundary.

### `quarantine_decisions`

`data_quality.quarantine_decisions` records an offline rejection decision for each failed source
observation. It links `quarantine_id`, `validation_report_id`, `validation_run_id`, and
`observation_id`, and stores the stable `quarantine_reason`, symbol/exchange/timeframe provenance,
optional source hash, quality details, and creation time. Indexes support report, run, observation,
and reason lookups. The table stores quality metadata only and never duplicates raw payloads.

### Phase 2D persistence boundary

The Phase 2D repository implementations accept an injected PostgreSQL DB-API connection and use
parameterized SQL. The offline flow persists raw observations, reports, check results, accepted
bars, and quarantine decisions in one transaction when the unified repository is used.
Validated-bar range queries use the half-open interval [start_utc, end_utc), matching the
offline collection and validation window convention. Validation-report inserts use the logical
identity `(validation_run_id, dataset_ref)`: an idempotent retry returns the existing database ID
only when its stored `report_sha256` matches the incoming hash, while different content raises a
`ValidationReportConflictError`. Both insert and conflict lookup use parameterized SQL. The returned
database-selected report ID, rather than a caller-proposed ID, becomes the foreign key on accepted
bars and quarantine decisions and is returned in the persistence summary. No driver is imported or
connection opened at module import time; PostgreSQL remains the sole authoritative storage target.

## Phase 2H reconciliation storage

`data_quality.reconciliation_results` stores one deterministic cross-source outcome. Its explicit
columns preserve `reconciliation_id`, `validation_run_id`, data type, symbol/timeframe, provider
names, UTC window, status, `config_sha256`, `dataset_sha256`, `result_sha256`, metrics, and creation
time. The idempotency constraint covers validation run, logical dataset identity, configuration,
and dataset hash. Indexes support validation-run, symbol/timeframe/window, status, and JSONB
provider-membership queries.

`data_quality.reconciliation_check_results` stores every stable reconciliation check separately.
Rows retain the database-selected reconciliation ID, validation run, declared check ID/type,
status, severity, affected observation UUID array, complete finding details, and creation time. The
foreign key cascades only when its reconciliation summary is removed, and a unique constraint on
`(reconciliation_id, check_id)` makes retries idempotent. Indexes support reconciliation, validation
run, check type, and status queries.

`PostgresReconciliationRepository` accepts an injected DB-API PostgreSQL connection, uses `%s`
parameters for every value, performs no import-time connection, and returns `RETURNING` IDs after
both inserts and uniqueness conflicts. `PostgresOhlcvPipelineRepository` combines this contract with
the Phase 2D raw/validation/bar/quarantine repositories on one connection.

`persist_reconciliation_result` owns one summary-plus-children transaction when called directly.
The Phase 2I pipeline instead opens one outer transaction for all successful provider persistence
and the reconciliation records, then calls both persistence services with their internal transaction
management disabled. Thus the documented pipeline boundary is full-run atomic for the unified
repository: raw observations, validation reports/checks, accepted bars or quarantine decisions, and
reconciliation summary/check rows commit or roll back together.

## Phase 2I persistence gates

Pipeline persistence is off by default. The safe CLI requires both `--persist` and
`ENABLE_POSTGRES_PERSISTENCE=true` before loading PostgreSQL configuration or importing a driver.
Only `POSTGRES_*` settings are read. No SQLite, file database, in-memory authoritative store, or
implicit fallback is available. Public-network collection is gated independently and does not imply
persistence. Before storage, the same canonical validation gate used by reconciliation selects the
accepted bars; rejected records remain auditable through raw observations and deterministic
quarantine decisions.

## Phase 2J-2M trade, funding, and instrument hardening

Migration `0005_trade_funding_instrument_hardening.sql` completes the Phase 2 public market-data
storage contract. Raw observations expose data type, provider instrument ID, and instrument type
alongside payload and source hash.

Validated trades store provider/instrument identity, instrument type, optional quote quantity and
provider sequence, and a normalized record hash. Their logical key is provider plus instrument plus
provider trade ID. Funding rates key provider plus instrument plus type plus funding timestamp,
preserving settlement and content hash. Both compare hashes on conflicts and return the
database-selected identifier.

Instrument rows are immutable metadata versions. Identity includes provider, provider instrument,
and type; `metadata_sha256` distinguishes versions. Spot and perpetual records can share base/quote
without sharing ambiguous storage identity. Direct columns retain display symbol, settlement,
contract/margin classification, increments, minimums, contract values, dates, funding interval,
validation identity, source observations, and provenance. Drift creates a new version with
structured old/new details instead of mutating history.

Indexes support provider/instrument/time queries and provider/canonical lookups. Uniqueness and
validation-report foreign keys are verifier-covered. Reads use end-exclusive windows and explicit
instrument types. Typed persistence writes raw observations, reports/checks, accepted rows, and
quarantine decisions inside one outer transaction. PostgreSQL remains the only authoritative
implementation.

## Phase 2 final identity and snapshot hardening

Logical trade and funding hashes deliberately exclude raw observation IDs and collection provenance.
Those values remain in `source_observation_ids` and `provenance_jsonb`, while `record_sha256` covers
only immutable provider identity and economic event content. Re-fetching the same event is therefore
idempotent; changing price, quantity, rate, timestamp, or grounded interval still conflicts.

The PostgreSQL repository exposes a read-only latest-snapshot boundary that rehydrates instrument
rows for pipeline drift comparison. Metadata versions remain keyed by provider identity, type, and
metadata hash; drift inserts a new version and never mutates the prior snapshot.

Migration `0006_phase2_final_hardening.sql` leaves the recorded `0005` file untouched, maps legacy
instrument type names, restores the canonical type check, and adds `NOT VALID` identity checks.
PostgreSQL enforces those checks for new/updated rows while allowing an existing legacy installation
to upgrade without an immediate historical backfill. Catalog verification covers names and expected
validation state for all four hardening checks.

## Phase 3-4 alpha and signal storage

Migration `0007_alpha_signal_library.sql` establishes the original alpha/signal tables. Migration `0008_phase3_phase4_audit_repairs.sql` adds close/finality bar availability, complete series identities, typed alpha evaluation status and lookback bounds, stable per-as-of eligible-input/record hashes, separate formula/code/source-tree provenance, numeric average ranks, explicit overlap policy and reasons, series-based uniqueness, and normalized `signals.signal_components` rows with signal/alpha-value/alpha foreign keys. Migrations `0001` through `0007` are unchanged.

`PostgresAlphaRepository` and `PostgresSignalRepository` accept an injected DB-API PostgreSQL connection, use `%s` parameters, connect nowhere during import, return database-selected IDs, and reject same-identity/different-content retries. Alpha-value and signal reads are deterministic and end-exclusive. `PostgresAlphaSignalRepository` shares one connection when a caller needs both domains. `persist_alpha_signal_bundle` owns one outer transaction for registry definitions, alpha runs/values, signal runs/signals, and signal components; any child failure rolls back the whole milestone. There is no SQLite or file-database fallback.

Persistence remains disabled in the offline alpha-to-signal CLI unless both `--persist` and `ENABLE_POSTGRES_PERSISTENCE=true` are present.
## Phase 5 execution and backtest persistence

Migration `0009_phase5_simulated_execution_backtesting.sql` leaves migrations `0001` through
`0008` unchanged. It strengthens order intents, orders, fills, positions, account snapshots,
backtest runs, metrics, and equity curves with complete series identity, deterministic hashes,
accounting/risk/fee/slippage fields, event timestamps, and logical conflict indexes. It adds
`execution.risk_decisions`, `execution.position_snapshots`, `execution.funding_payments`,
`execution.cash_ledger_entries`, and `backtesting.backtest_events`.

`PostgresPhase5Repository` accepts an injected DB-API connection, uses parameterized SQL, returns
database-selected IDs, rejects same-identity/different-hash retries, and provides deterministic
half-open reads. `persist_backtest_bundle` owns one outer transaction for the complete Phase 5
parent/child graph; injected failures at every child location leave zero run rows. Clean PostgreSQL
16 installation and a seeded `0008` to `0009` upgrade are independently validated. PostgreSQL
remains the only authority and persistence is disabled by default in the offline demo.

## Phase 6 monitoring and simulated FIX storage

Migration `0013_phase6_monitoring_simulated_fix.sql` adds normalized monitoring runs, check results, health snapshots, incident episodes/occurrences, simulated FIX sessions/messages/order links, fixed latency samples, and deterministic connection faults. It strengthens the original monitoring event skeleton and backfills Phase 5 final-position projections from the latest snapshot belonging to the same complete run and position lineage. PostgreSQL remains the only authority. Monitoring and FIX repositories accept injected DB-API connections, use parameterized SQL, reject deterministic identity/content conflicts, provide half-open ordered reads, and persist complete monitoring or session-transition bundles atomically.

### Phase 6 first-audit persistence repairs

Migration `0014_phase6_first_audit_repairs.sql` leaves `0001` through `0013` immutable and extends the Phase 6 model. Valid and rejected raw FIX observations share `monitoring.fix_messages`; rejected rows retain raw SHA-256, safe header fields, typed rejection code/reason, and deterministic identity. Canonical replay hashes support identical PossDup idempotency and changed-content conflict detection.

`monitoring.fix_session_events` remains the immutable authority. Its deterministic transition ordinal and previous-event hash form an append-only chain. `monitoring.fix_sessions` is a current-state projection guarded by expected `state_version` and prior record hash, non-decreasing inbound/outbound sequences, legal state transitions, and a deferred link to the last transition event. Messages, events, faults, links, and projection changes commit or roll back together. Clean `0001 -> 0014` and seeded `0013 -> 0014` upgrades are tested on PostgreSQL 16.

## Phase 7 paper persistence

Migration `0016_phase7_safe_paper_trading.sql` preserves migrations `0001` through `0015` and adds separate `execution.paper_*` objects. It stores public-safe credential references, preflight checks/reports, expiring approvals, immutable manifests, append-only order projections/events, confirmed fills/fees, account observations, reconciliation differences, recovery records, persisted kill state/events, rate-limit evidence, transport attempts, and lifecycle events.

`PostgresPaperRepository` accepts an injected connection and opens no database during import. Start-run, submission outcome, fill/account/reconciliation, reconciliation/kill, and kill-event bundles use outer transactions. A persistence-required configuration fails rather than using memory or a file database. Clean `0001 -> 0016`, seeded `0015 -> 0016`, migration hashes, catalog objects, replay idempotency, half-open reads, orphan checks, and injected rollback points are validated on PostgreSQL 16.
