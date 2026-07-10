# Implementation Status

This file is the human-readable source of implementation progress. The machine-readable status file
is `.project/implementation_status.json`.

Every future functional PR must update both files in the same change:
- `docs/IMPLEMENTATION_STATUS.md`
- `.project/implementation_status.json`

Completed work must be listed under `Completed`. Everything not done must remain under `Todo`.

## Non-Negotiable Constraints
- PostgreSQL is the only authoritative storage layer.
- SQLite is explicitly disallowed as authoritative storage.
- Live trading is disabled by default.
- No secrets, API keys, private strategies, real account data, or real trade logs may be added.
- Runtime features must not be implemented during documentation/control phases.

## Completed

### Phase 0: Architecture docs
- [x] Define full crypto trading system architecture.
- [x] Document layer-by-layer responsibilities.
- [x] Document proposed folder structure.
- [x] Document PostgreSQL-first storage design.
- [x] Document multi-source crypto data collection and validation.
- [x] Document shared execution contract and broker roadmap.
- [x] Document simulated FIX-style monitoring design.
- [x] Document live trading safety boundaries.
- [x] Document local data governance and cleanup rules.
- [x] Document public/private repository boundary.
- [x] Update README with architecture direction and documentation links.

### Phase 0: Project control files
- [x] Add repository-wide Codex engineering rules in `AGENTS.md`.
- [x] Establish `docs/IMPLEMENTATION_STATUS.md` as the human-readable status source.
- [x] Add `.project/implementation_status.json` as the machine-readable status source.
- [x] Add `.project/implementation_status.schema.json` for status-file validation.
- [x] Document the requirement that future functional PRs update both status files.
- [x] Document PostgreSQL as the only authoritative storage layer.
- [x] Explicitly disallow SQLite as authoritative storage.
- [x] Document that live trading is disabled by default.

### Phase 1A: PostgreSQL infrastructure and schema foundation
- [x] Add Dockerized local PostgreSQL infrastructure in `infra/docker-compose.postgres.yml`.
- [x] Add `open-core/db/` migration and schema documentation layout.
- [x] Create initial PostgreSQL schema migration in `open-core/db/migrations/0001_initial_schema.sql`.
- [x] Add metadata-only schema verification script in `open-core/scripts/verify_postgres_schema.py`.
- [x] Add local PostgreSQL setup documentation and PowerShell helper.
- [x] Replace `docs/FOLDER_STRUCTURE.md` mojibake tree characters with an ASCII tree.

### Phase 1B: Repository interfaces and migration discipline
- [x] Add `audit.schema_migrations` migration metadata table.
- [x] Track migration ID, filename, SHA256, applied timestamp, and description.
- [x] Strengthen schema verification for required schemas, tables, columns, indexes, unique constraints, and migration hashes.
- [x] Add `secure_eval_wrapper.storage` package skeleton.
- [x] Add PostgreSQL-only connection/config abstraction that does not connect during import.
- [x] Add repository interface definitions for market data, data quality, alpha, signal, execution, backtest, monitoring, audit, and artifacts.
- [x] Complete Phase 1 PostgreSQL schema, repository interface, and migration verification foundation.

### Pre-Phase 2: migration runner hardening
- [x] Harden `open-core/scripts/postgres_local.ps1` so migration metadata is recorded immediately after each successful migration, matching recorded migrations are skipped, hash mismatches fail clearly, and failed migrations stop without silently leaving untracked partial state.

### Phase 2A: data collection and validation contracts
- [x] Add importable data collection and data validation package skeletons.
- [x] Define provider, request, raw observation, normalized market data, instrument metadata, and collection run contracts.
- [x] Define the abstract crypto market data provider interface.
- [x] Register Binance, OKX, Bybit, and Coinbase as planned provider specifications without clients or credentials.
- [x] Define validation, reconciliation, quarantine, and dataset promotion contracts.
- [x] Add offline construction, registry, abstract-interface, and Python compile checks.

### Phase 2B: offline collection utilities
- [x] Add deterministic canonical JSON and SHA-256 source hashing utilities.
- [x] Add explicit UTC requirement and coercion guards that reject naive datetimes by default.
- [x] Add conservative simple-pair symbol normalization helpers.
- [x] Add an offline-only OHLCV sample provider restricted to public-safe fixtures under open-core/data/sample.
- [x] Add synthetic OHLCV fixture data and offline hashing, UTC, symbol, and provider tests.

### Phase 2C: offline normalization and single-source validation
- [x] Normalize sample-provider OHLCV observations into Decimal-based UTC `NormalizedBar` records with conservative symbols and complete provenance.
- [x] Add deterministic missing-bar, duplicate timestamp, non-monotonic timestamp, OHLC, volume, and partial-candle checks.
- [x] Build hashed in-memory validation reports and deterministic quarantine reason mappings without persistence.
- [x] Add offline normalization, validation, policy, quarantine, and report-hash tests guarded against network use.

### Phase 2D: PostgreSQL-backed offline validation persistence
- [x] Add the data-quality quarantine decisions migration and catalog verification coverage.
- [x] Add PostgreSQL repositories for raw observations, validation reports, checks, validated bars, and quarantine decisions.
- [x] Add domain-to-storage mappings that preserve source IDs, source hashes, report hashes, tolerance hashes, and provenance.
- [x] Add the offline accepted/rejected OHLCV persistence flow with transactional writes and deterministic quarantine decisions.
- [x] Add public-safe offline persistence tests and verification coverage.
- [x] Return existing validation report IDs on uniqueness conflicts and define validated-bar queries with end-exclusive time windows.

### Phase 2E: Binance public OHLCV provider adapter

- [x] Add an injectable HTTP transport boundary with an optional standard-library implementation.
- [x] Implement Binance Spot public `/api/v3/klines` request building and 12-field response parsing.
- [x] Generate deterministic hashed `RawObservation` records that flow through existing normalization and validation.
- [x] Add fully offline fake-transport tests and an explicitly disabled public-network smoke script.

### Phase 2F: Offline OHLCV cross-source reconciliation

- [x] Implement configurable deterministic offline OHLCV reconciliation across provider datasets.
- [x] Emit stable missing-coverage, price, volume, extra-bar, and close-time validation results.
- [x] Build deterministic reconciliation IDs, status, metrics, and UTC provenance without persistence.
- [x] Add fully offline tests for policies, tolerances, ordering, input guards, and network isolation.

### Phase 2G: OKX V5 public OHLCV provider adapter

- [x] Verify the current OKX V5 public GET /api/v5/market/history-candles contract against official documentation.
- [x] Implement an injectable public-only OKX historical OHLCV adapter with conservative symbol and UTC timeframe mappings.
- [x] Add bounded backward pagination, half-open filtering, cursor-progress guards, deterministic provenance, and documented finality parsing.
- [x] Add fully offline OKX request, parsing, pagination, hashing, normalization, validation, and socket-isolation tests.

### Phase 2H: PostgreSQL reconciliation persistence

- [x] Add ordered reconciliation summary and child-check PostgreSQL tables with indexes and idempotency constraints.
- [x] Expose deterministic reconciliation configuration, dataset, and result hashes in the domain contract.
- [x] Add reconciliation row mappings and parameterized PostgreSQL repository methods that return database-selected conflict IDs.
- [x] Add atomic reconciliation persistence with child-write rollback and a unified OHLCV pipeline repository.
- [x] Extend schema verification and offline tests for reconciliation tables, columns, indexes, constraints, migration hashes, mappings, and transactions.

### Phase 2I: End-to-end public OHLCV data pipeline

- [x] Add typed provider-neutral orchestration for collection, normalization, single-source validation, and cross-source reconciliation.
- [x] Add explicit fail-fast and partial-provider policies without treating one provider as successful reconciliation.
- [x] Add one outer PostgreSQL transaction boundary for optional raw, validation, bar/quarantine, and reconciliation persistence.
- [x] Add a fixture-default CLI with separately gated bounded public-network and PostgreSQL persistence modes.
- [x] Add offline Binance plus OKX integration tests for success, failures, quarantine, mismatches, persistence, determinism, and socket isolation.

## Todo

### Phase 2: data collection + validation (in progress)
- [ ] Implement additional public OHLCV provider adapters beyond Binance and OKX without embedding credentials.
- [ ] Implement public trade collection.
- [ ] Implement public funding rate collection.
- [ ] Implement public instrument metadata collection.

### Phase 3: public alpha library
- [ ] Create public alpha registry.
- [ ] Implement momentum demo alpha.
- [ ] Implement moving-average crossover demo alpha.
- [ ] Implement breakout demo alpha.
- [ ] Implement mean reversion demo alpha.
- [ ] Implement 101 Formulaic Alphas style examples.
- [ ] Implement funding-rate demo alpha.
- [ ] Add alpha documentation and tests.

### Phase 4: signal generation
- [ ] Define standardized signal schema.
- [ ] Implement signal ranking.
- [ ] Implement thresholding.
- [ ] Implement signal combination.
- [ ] Implement conflict resolution.
- [ ] Implement confidence scoring.
- [ ] Persist signal runs and signals.

### Phase 5: simulated execution + backtesting
- [ ] Define broker interface.
- [ ] Implement order intent schema.
- [ ] Implement `SimulatedBroker`.
- [ ] Implement fill model.
- [ ] Implement fee model.
- [ ] Implement slippage model.
- [ ] Implement risk guard.
- [ ] Implement position manager.
- [ ] Implement backtest event loop.
- [ ] Generate metrics from fills and positions, not direct signal equity mutation.
- [ ] Add crypto-specific backtest handling for fees, funding, 24/7 markets, and missing candles.

### Phase 6: monitoring + simulated FIX API
- [ ] Implement monitoring event schema.
- [ ] Implement data health monitoring.
- [ ] Implement signal health monitoring.
- [ ] Implement execution health monitoring.
- [ ] Implement risk health monitoring.
- [ ] Implement system health monitoring.
- [ ] Implement simulated FIX heartbeat.
- [ ] Implement simulated session state.
- [ ] Implement simulated order acknowledgement.
- [ ] Implement simulated execution report.
- [ ] Implement simulated cancel/reject flow.
- [ ] Implement latency measurement.
- [ ] Implement dropped connection simulation.

### Phase 7: paper trading
- [ ] Design paper broker adapter.
- [ ] Add paper-mode configuration.
- [ ] Add sandbox/paper reconciliation.
- [ ] Add paper trading run manifests.
- [ ] Add paper trading safety checks.

### Phase 8: guarded live execution
- [ ] Design live broker adapter.
- [ ] Keep live trading disabled by default.
- [ ] Require explicit live enablement flag.
- [ ] Require local-only API keys.
- [ ] Require max notional limits.
- [ ] Require dry-run support.
- [ ] Require kill switch.
- [ ] Require pre-flight risk summary.
- [ ] Require post-run risk summary.
- [ ] Require live execution audit manifest.

### Phase 9: reporting + public delivery
- [ ] Build public report templates.
- [ ] Build private report templates.
- [ ] Implement artifact classification.
- [ ] Implement redaction workflow.
- [ ] Implement artifact hashing.
- [ ] Generate public-safe delivery bundles.
- [ ] Add release checklist.
- [ ] Add local sensitive file audit before delivery.
