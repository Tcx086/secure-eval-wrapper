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

### Phase 2I hardening: validation gates and persistence identity

- [x] Add one canonical record-level OHLCV validation gate shared by reconciliation and persistence.
- [x] Exclude rejected bars from reconciliation and expose provider accepted/rejected eligibility outcomes.
- [x] Add quality-aware pipeline status semantics for succeeded, partial, and failed runs.
- [x] Propagate database-selected validation report IDs to accepted bars, quarantine rows, and persistence summaries.
- [x] Reject validation report identity conflicts when stored and incoming report hashes differ.

### Phase 2J-2M: public trades, funding, instruments, and Phase 2 completion

- [x] Add unambiguous provider instrument identities that separate Spot, perpetual swaps, and dated futures.
- [x] Verify and document current official public endpoint, request, response, pagination, limit, and authentication contracts.
- [x] Implement Binance and OKX public Spot trade collection through injectable transports.
- [x] Implement Binance USDâ“ˆ-M and OKX SWAP public funding history.
- [x] Implement Binance Spot/USDâ“ˆ-M and OKX SPOT/SWAP public instrument metadata.
- [x] Add deterministic normalization, validation reports, accepted/rejected gates, and quarantine for all three data types.
- [x] Add PostgreSQL trade/funding persistence, immutable instrument metadata versions, conflict hashes, reads, indexes, constraints, and foreign-key verification.
- [x] Add provider-neutral typed trade, funding, and instrument pipelines with fail-fast, partial, warning, and one-transaction persistence semantics.
- [x] Add a classified fixture-default complete public-data CLI with independently gated public network and PostgreSQL modes.
- [x] Add comprehensive offline provider, pagination, normalization, validation, gating, persistence, pipeline, CLI, socket-isolation, and boundary tests.
- [x] Complete the Phase 2 exit review without starting Phase 3 alpha implementation.

### Phase 2 final hardening

- [x] Exclude volatile collection provenance from stable trade/funding event hashes while preserving full source audit columns.
- [x] Integrate prior instrument snapshots into the metadata pipeline with PostgreSQL and in-memory reader boundaries.
- [x] Separate concrete `binance`, `binance_usdm`, and `okx` component capabilities from exchange-level summaries.
- [x] Ground funding intervals in verified public metadata, preserve typed interval sources, and skip gap checks explicitly when unavailable.

### Phase 3: public alpha library

- [x] Define alpha metadata, evaluation request, run, value, failure, and public implementation contracts.
- [x] Add a deterministic versioned public registry with duplicate and implementation-hash conflict protection.
- [x] Add shared validation-gated point-in-time inputs, trailing-only windows, warmup enforcement, and leakage controls.
- [x] Implement momentum, moving-average crossover, prior-channel breakout, and trailing mean-reversion examples.
- [x] Implement six transparent low-dimensional formulaic-style OHLCV examples without claiming the complete 101 library.
- [x] Implement grounded-interval perpetual funding-rate contrarian demonstration alpha.
- [x] Add provider-neutral AlphaEngine output validation, deterministic IDs/hashes, typed failures, and explicit partial status.
- [x] Add PostgreSQL-only versioned alpha registry, run/value persistence, idempotency, half-open reads, and one outer transaction.
- [x] Add the classified synthetic fixture and fully offline alpha-to-signal CLI.
- [x] Add alpha documentation, exact calculation tests, parameter tests, persistence tests, and import/socket boundary tests.
- [x] Add future-append, future-mutation, breakout-current-bar, rolling-window, and funding leakage regression tests.
- [x] Complete the Phase 3 exit review without adding execution, backtesting, paper, or live trading.

### Phase 4: signal generation

- [x] Define standardized signal and signal-run contracts with research-only lineage and hashes.
- [x] Implement deterministic timestamp-scoped ascending/descending dense/ordinal ranking and percentiles.
- [x] Implement validated absolute, percentile, and top/bottom N threshold policies with first-class flat results.
- [x] Implement equal, static, and normalized-score weighting with contributor and coverage policies.
- [x] Implement deterministic conflict resolution that preserves every signed contribution in provenance.
- [x] Implement transparent bounded confidence from score magnitude, agreement, coverage, and threshold distance.
- [x] Add single-alpha and multi-alpha SignalPipeline modes with typed failures and partial/failed status.
- [x] Add PostgreSQL-only signal run/signal persistence, conflict hashes, half-open reads, and one outer transaction.
- [x] Extend ordered migrations and catalog verification for alpha/signal tables, columns, indexes, constraints, foreign keys, checks, and hashes.
- [x] Add ranking, threshold, combination, confidence, pipeline, persistence, CLI, and security-boundary tests.
- [x] Document the exact signal formulas and that confidence is not a probability of profit.
- [x] Complete the Phase 4 exit review and advance current work to Phase 5 while leaving Phase 5 todo.

## Todo

### Future provider enhancements

- [ ] Consider additional public OHLCV/trade/funding/instrument adapters after Phase 2; Bybit and Coinbase are not currently implemented.

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
