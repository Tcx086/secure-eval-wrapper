# Implementation Status

This file is the project control file. Every future update must mark completed work under
`Completed`. Everything not done must remain under `Todo`.

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

## Todo

### Phase 1: PostgreSQL + schema + migrations
- [ ] Add Dockerized PostgreSQL infrastructure.
- [ ] Add `open-core/db/` migration layout.
- [ ] Create initial schema migrations.
- [ ] Add repository interfaces.
- [ ] Add local database setup documentation.
- [ ] Add migration verification workflow.

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