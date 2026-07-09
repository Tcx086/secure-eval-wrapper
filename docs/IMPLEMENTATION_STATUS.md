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

## Todo

### Phase 2: data collection + validation
- [ ] Implement crypto market data provider interfaces.
- [ ] Implement OHLCV collection.
- [ ] Implement trade collection.
- [ ] Implement funding rate collection.
- [ ] Implement instrument metadata collection.
- [ ] Implement provenance and source hashing.
- [ ] Implement single-source validation checks.
- [ ] Implement cross-source reconciliation.
- [ ] Implement validation reports.
- [ ] Implement accepted/rejected data flow.

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
