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
- [x] Implement Binance USDÃ¢â€œË†-M and OKX SWAP public funding history.
- [x] Implement Binance Spot/USDÃ¢â€œË†-M and OKX SPOT/SWAP public instrument metadata.
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

### Phase 3: public alpha library (audit repair accepted)

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
- [x] Enforce close-time and finality-based point-in-time bar availability with explicit fixed-timeframe legacy derivation.
- [x] Introduce one complete immutable series identity across alpha requests, inputs, values, signals, hashes, and PostgreSQL persistence.
- [x] Make eligible-input, alpha-value, and signal identities invariant to future data and mutable collection provenance.
- [x] Correct mean reversion to use a prior-only comparison window with explicit warmup and zero-variance behavior.
- [x] Correct funding contrarian to use a bounded rolling mean of realized rates with grounded interval evidence.
- [x] Persist typed alpha evaluation status, reasons, as-of bounds, lookback bounds, stable input hashes, and complete lineage.
- [x] Separate formula identity, implementation code identity, and repository commit identity.
- [x] Add the required exact, point-in-time, identity, provenance, and PostgreSQL regression coverage.

### Phase 4: standardized signal generation (audit repair accepted)

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
- [x] Implement average-rank tie semantics for ascending and descending rankings.
- [x] Add and persist an explicit top/bottom N overlap policy and resolution reason.
- [x] Add the immutable SignalComponent domain contract and normalized PostgreSQL persistence.
- [x] Add one atomic bundled alpha-to-signal persistence boundary with rollback coverage at every child failure point.
- [x] Complete clean-install, 0007-to-0008 upgrade, schema, migration-hash, full-suite, offline, and security-boundary validation.
- [x] Re-accept Phase 3 and Phase 4 only after every mandatory audit repair and validation passes.

### Phase 5: simulated execution + backtesting (fourth independent audit accepted)

- [x] Add installable package metadata, console entry points, cross-platform validation, and least-privilege CI.
- [x] Define immutable deterministic execution, sizing, risk, order, fill, position, cash, funding, account, and event contracts.
- [x] Implement market, limit, stop, stop-limit, GTC/IOC, fee, slippage, cancellation, rejection, and expiry semantics in `SimulatedBroker`.
- [x] Implement exact Spot and linear-perpetual fill-driven accounting, reversals, replay protection, and ledger reconciliation.
- [x] Implement realized grounded-interval funding with same-timestamp priority and no Spot/predicted funding.
- [x] Implement the multi-series event-driven engine, missing-candle handling, stale marks, final open positions, and fill-derived metrics.
- [x] Add migration `0009` and injected-connection PostgreSQL repositories with one complete-bundle transaction.
- [x] Add the fixture-default socket-free offline backtest CLI and optional double-gated PostgreSQL persistence.
- [x] Add dedicated Phase 5 order, risk, accounting, funding, metrics, persistence, anti-lookahead, and future-invariance tests.
- [x] Pass local PostgreSQL 16 clean install, seeded `0008` to `0009` upgrade, catalog/hash checks, real writes/reads, conflicts, and rollback injection.
- [x] Document exact Phase 5 semantics, persistence, validation, and limitations.
- [x] Pass independent GitHub Actions validation on the checkpoint branch and `main` implementation SHA.
- [x] Mark portfolios at the actual bar open before pre-fill risk and fill evaluation, with explicit mark provenance.
- [x] Enforce one base, fee, Spot quote, perpetual settlement, fill, and fee-ledger currency without FX conversion.
- [x] Route prohibited Spot short targets through an auditable blocked risk decision without aborting the backtest.
- [x] Correct Spot unrealized PnL while preserving cash-plus-marked-inventory equity semantics.
- [x] Add immutable logical identities and different-hash conflict protection for position snapshots and cash-ledger entries.
- [x] Enforce canonical lowercase hexadecimal SHA-256 values in contracts and PostgreSQL.
- [x] Derive and enforce deterministic backtest run IDs from stable economic and implementation inputs.
- [x] Propagate the configured simulation account identity consistently through contracts, hashes, and persistence.
- [x] Add simulated-order lineage to pre-fill risk decisions.
- [x] Define fee- and funding-aware net economic round-trip metric semantics.
- [x] Strengthen the public/private CI boundary scan.
- [x] Add migration `0010`, dedicated regression coverage, PostgreSQL upgrade validation, and final CI validation.
- [x] Add migration `0011` with normalized complete-run membership for shared immutable economic records.
- [x] Make aggregate metric identity and persistence explicitly scoped to the complete deterministic `backtest_run_id`.
- [x] Add complete-run reads, deterministic ordering, deletion/reference safety, and membership cleanup.
- [x] Add overlapping short/extended-run coexistence, isolation, reconstruction, idempotency, conflict, and rollback regressions.
- [x] Classify immutable economic/event records separately from complete-run-scoped final projections.
- [x] Add migration `0012` with run-scoped final order and position projections without modifying migrations `0001` through `0011`.
- [x] Repair complete-bundle persistence, reconstruction, deletion, conflict, idempotency, and rollback semantics.
- [x] Add expired-A/filled-B, long-A/flat-B, and long-A/reversed-B real PostgreSQL regressions.
- [x] Pass clean install, seeded `0011` to `0012` upgrade, full-suite, packaging, boundary, and final CI validation.

### Phase 6: monitoring + simulated FIX API (in progress: second-independent-audit repair)

- [x] Repair Phase 5 complete-run final-position valuation from the latest run-owned immutable snapshot.
- [x] Add migration `0013` with valuation backfill and normalized monitoring/FIX persistence without modifying migrations `0001` through `0012`.
- [x] Implement immutable monitoring contracts, deterministic IDs/hashes, explicit UTC inputs, and public-safe provenance separation.
- [x] Implement data, signal, execution, risk, system, and simulated FIX session health evaluation.
- [x] Aggregate health with explicit critical-unhealthy, unhealthy, degraded, unknown, healthy precedence and causal child lineage.
- [x] Implement deterministic continuous incident open, update, acknowledge-domain, occurrence, recovery, and new-episode semantics.
- [x] Implement exact ASCII-SOH FIX 4.4-compatible encoding/decoding with BodyLength, CheckSum, required-tag, duplicate-tag, value, and enum validation.
- [x] Implement deterministic simulated session state, sequence gaps/recovery, PossDup replay handling, heartbeat/TestRequest timeout, reconnect, and logout.
- [x] Connect NewOrderSingle and cancel/reject flows only to the existing `SimulatedBroker`, with fills gated by explicit synthetic market events.
- [x] Implement fixed deterministic simulated latency and preconfigured recorded connection-fault schedules.
- [x] Add injected-connection PostgreSQL repositories, idempotency/conflict protection, half-open ordered reads, and atomic monitoring/FIX transactions.
- [x] Add fixture-default socket-free `secure-eval-monitor` and `secure-eval-fix-sim` console demos with double-gated PostgreSQL persistence.
- [x] Add monitoring, FIX codec/session/gateway, projection valuation, PostgreSQL, rollback, documentation, schema-verifier, and boundary coverage.
- [x] Pass 364-test local suite, dedicated Phase 5 audits, 60-test Phase 6 suite, 31-test FIX suite, editable install, console, compile, JSON, YAML, migration-hash, and boundary validation.
- [x] Pass GitHub Actions run `29122849335` on `ea81231c6c6a2fd1be6305022c187ba989a66ecd`, including PostgreSQL 16 clean `0001` to `0013`, seeded `0012` to `0013`, catalog, Phase 5/6 persistence, and rollback gates.
- [x] Confirm Phase 7 remains entirely todo and no paper/live broker, external FIX connection, authenticated exchange access, leverage, margin, collateral, liquidation, or machine learning was added.
#### Phase 6 first independent audit repairs completed

- [x] Implement real double-gated PostgreSQL persistence for both Phase 6 CLIs and source wrappers.
- [x] Make the simulated FIX gateway position/accounting aware with fill-only mutation and replay protection.
- [x] Correct heartbeat, TestRequest grace, and disconnect-timeout semantics with independent boundaries.
- [x] Repair PossDup replay identity and introduce typed receive dispositions.
- [x] Persist typed rejected raw FIX observations without advancing session/economic state.
- [x] Protect the FIX session projection with chained immutable authoritative events and optimistic version/hash checks.
- [x] Preserve active and acknowledged incidents when evidence is unknown.
- [x] Integrate all eight deterministic fault-schedule behaviors with session events and monitoring evidence.
- [x] Review and test the exact supported FIX 4.4-compatible profile against FIX Trading Community FIXimate.
- [x] Add migration `0014` without modifying migrations `0001` through `0013`.
- [x] Pass 386-test real-PostgreSQL-enabled full suite, Phase 5 audit/PostgreSQL suites, Phase 6 focused suites, 12 real Phase 6 PostgreSQL tests, clean `0001` to `0014`, seeded `0013` to `0014`, editable install, entry points, compile, JSON, YAML, catalog, migration-hash, socket-boundary, and public/private boundary validation.
- [x] Pass all six jobs in GitHub Actions repair run `29132859728` on `1d2de870f6653c3ae74d775856d55ef6a744dd92`.

## Todo

### Phase 6 second independent audit repairs

- [ ] Prevent concurrent Spot sell orders from reserving or filling beyond fill-derived inventory.
- [ ] Unify raw and typed FIX rejection handling with auditable rejected dispositions and no economic processing.
- [ ] Enforce immutable session-event authority over every changed FIX session projection.
- [ ] Persist the complete public FIX-to-simulated-execution lineage in one outer PostgreSQL transaction.
- [ ] Persist repeated rejected-message occurrences separately from stable rejection observations.
- [ ] Add migration `0015` without modifying migrations `0001` through `0014`.
- [ ] Pass all required local, PostgreSQL 16, packaging, boundary, migration, and final main-SHA CI validation.

### Future provider enhancements

- [ ] Consider additional public OHLCV/trade/funding/instrument adapters after Phase 2; Bybit and Coinbase are not currently implemented.

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
