# Implementation Status

This file is the human-readable source of implementation progress. The machine-readable status file
is `.project/implementation_status.json`.

Every future functional PR must update both files in the same change:
- `docs/IMPLEMENTATION_STATUS.md`
- `.project/implementation_status.json`

Completed work must be listed under `Completed`. Everything not done must remain under `Todo`.

Current phase: `phase_8_guarded_live_execution` (`in_progress`). Phase 8A guarded-live dry-run/read-only runtime and the Phase 8B authenticated read-only proof implementation are accepted; the dedicated local operator bootstrap is implemented pending independent audit, while the real local authenticated proof has not been executed. Production writes remain disabled and unreachable, Phase 8 is not complete, and Phase 9 remains todo.

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
- [x] Implement Binance USDÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â€šÂ¬Ã…â€œÃƒâ€¹Ã¢â‚¬Â -M and OKX SWAP public funding history.
- [x] Implement Binance Spot/USDÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â€šÂ¬Ã…â€œÃƒâ€¹Ã¢â‚¬Â -M and OKX SPOT/SWAP public instrument metadata.
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

### Phase 6: monitoring + simulated FIX API (second independent audit accepted)

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

#### Phase 6 second independent audit repairs completed

- [x] Prevent concurrent Spot sell orders from reserving or filling beyond fill-derived inventory.
- [x] Unify raw and typed FIX rejection handling with auditable rejected dispositions and no economic processing.
- [x] Enforce immutable session-event authority over every changed FIX session projection.
- [x] Persist the complete public FIX-to-simulated-execution lineage in one outer PostgreSQL transaction.
- [x] Persist repeated rejected-message occurrences separately from stable rejection observations.
- [x] Add migration `0015` without modifying migrations `0001` through `0014`.
- [x] Pass all required local, PostgreSQL 16, packaging, boundary, and migration validation, plus all six jobs in GitHub Actions checkpoint run `29143906785` on `e92d4484e7b4847a7a5b5ee49ae1dc2d573c0186`.

### Phase 7: safe paper trading (completed through the fifth independent audit)

- [x] Add provider-neutral PaperBroker contracts, bounded run configuration, preflight, explicit approval, immutable manifest, lifecycle, recovery, rate limiting, and safe CLI boundaries.
- [x] Add the deterministic asynchronous InternalPaperVenue with partial fills, idempotency, deterministic faults, recovery, accounting, reconciliation, monitoring, and persisted kill-switch behavior.
- [x] Verify and implement the credential-gated official OKX demo REST subset with an immutable route/marker catalog, lazy local credentials, bounded authenticated transport, and offline fake-transport tests.
- [x] Add PostgreSQL migration 0016, 23 separate paper audit tables, transaction repositories, clean/seeded migration validation, replay idempotency, rollback injection, and catalog verification.
- [x] Add Phase 7 documentation, 57 focused tests, package/CLI integration, CI jobs, public/private boundary scans, and explicit no-live/no-transfer/no-withdrawal constraints.
- [x] Pass all six jobs in GitHub Actions run `29162741634` on implementation main SHA `29222159e4ddac8ab3dd23c2334d3a2ab2236639`, including PostgreSQL 16 clean/seeded migration and transaction validation.
- [x] Pass all six jobs in GitHub Actions rollback-repair run `29163009702` on main SHA `3c0a7c2feb5be50baf9db5be4f89943c25bce1ec`, including the expanded recovery-record and kill-event rollback matrix.
- [x] Add append-only migration `0017`, PostgreSQL-authoritative prepare/claim/recovery and reservation state, complete restart reconstruction, atomic approval consumption, terminal kill semantics, full runtime risk enforcement, fill/accounting/reconciliation bundles, and truthful operational CLIs.
- [x] Add real PostgreSQL 16 crash-window, HTTP ambiguity, cancel, restart, late-fill, approval, concurrency, per-limit, immutability, clean/seeded/idempotency, rollback, conflict, and boundary regression coverage.
- [x] Pass all six jobs in GitHub Actions durable-audit checkpoint run `29181658332` on main SHA `99ff61c287052918598bf41518e9ed3ac5eac521` without adding Phase 8 runtime.
- [x] Complete prepared/cancel outbox restart recovery with separate leased recovery claims and strict token ownership.
- [x] Persist the exact reconciliation snapshot/order/fill bundle without refetching snapshots.
- [x] Make validated, identity-bound market-data evidence the only operational freshness authority.
- [x] Repair persisted created preflight approval reconstruction and atomic transition to running.
- [x] Make open-order terminal accounting transition-based and exactly once across late fills.
- [x] Make `InternalPaperVenue.fill()` candidate-validated and exception-atomic with fee/slippage reservation coverage.
- [x] Add migration `0018`, expanded offline/PostgreSQL regressions, clean/seeded validation, and six-job CI checkpoint run `29201729953` on `ae245f26ed21b13e9a32b723ff0112ae6bd3c82b`.
- [x] Persist asynchronous venue-state observations as PostgreSQL authority.
- [x] Apply terminal recovery fills and accounting atomically before closing reservations and order budgets.
- [x] Make persistent-mode `InternalPaperVenue` operational events crash-safe and PostgreSQL-rebuildable.
- [x] Unify durable runtime, paper accounting, and internal venue reservation authority and calculations.
- [x] Persist expiry and rejection command/state transitions without terminal-state bypasses.
- [x] Route expired dispatch and cancel claims automatically through generation-safe recovery claims.
- [x] Add append-only migration `0019`, real PostgreSQL fourth-audit regression coverage, clean/seeded/idempotent migration validation, rollback/conflict coverage, and six-job CI checkpoint run `29207907662` on `438fd3dd822c25d8f2309d72316f84671a7614e2`.
- [x] Add authoritative PostgreSQL-backed market price/source evidence with explicit internal-fixture isolation.
- [x] Persist one conservative risk-price and risk-notional calculation across order, position, exposure, daily, approval, reservation, and audit limits.
- [x] Make terminal fill completeness state-independent while preserving cancelled/expired/rejected dispositions across late fills.
- [x] Add explicit cancel supersession outcomes plus pending-fill and query-first expiry recovery with PostgreSQL leases.
- [x] Persist exact InternalPaperVenue economics and replay exact fee, reservation, balance, and position evidence after restart.
- [x] Enforce closed-order-budget monotonicity and terminal projection monotonicity in PostgreSQL.
- [x] Add append-only migration `0020`, fifth-audit regressions, clean 0001-to-0020 and seeded 0016-to-0020 validation, and migration immutability proof for `0001` through `0019`.
- [x] Make cancellation confirmation evidence-aware, preserve pending recovery until fill/fee/accounting completeness, and add append-only migration `0021` plus active-claim and immediate-adapter PostgreSQL regressions.
- [x] Complete Phase 7 at final implementation SHA `ce832e537c765f55f7435ae288e199fc8ffec3d6` after merged-main GitHub Actions run `29221216369` passed all six jobs: Ubuntu Python 3.11 job `86726532443`, Ubuntu Python 3.12 job `86726532445`, Ubuntu Python 3.13 job `86726532447`, Windows Python 3.12 job `86726532444`, PostgreSQL 16 integration job `86726532449`, and public/private and runtime boundary job `86726532440`.
- [x] Verify migrations `0001` through `0021`, including the active-cancel hidden-fill and immediate-CANCELLED adapter regressions, and close Phase 7 without starting any Phase 8 runtime.


### Phase 8A: guarded live execution foundation (independently audited and accepted checkpoint; Phase 8 remains in progress)

- [x] Add a separate `secure_eval_wrapper.live` package with immutable guarded-live configuration and no changes to paper or simulated execution authorities.
- [x] Verify and catalog exact OKX production Spot public-read, authenticated-read, trading-write, and forbidden endpoints against official V5 documentation.
- [x] Enforce independent environment, CLI, and exact approval gates plus an unconditional Phase 8A and CI production-write prohibition.
- [x] Implement local-only credential loading, permission fail-closed behavior, redaction, short public fingerprints, and no secret persistence.
- [x] Implement PostgreSQL-bound preflight, exact-challenge approval, immutable manifest, account evidence, and public-safe lifecycle contracts.
- [x] Reuse Phase 7 validated market evidence and conservative price authority for live risk notional, reservations, approval consumption, and summaries.
- [x] Implement truthful dry-run intent, exact OKX request planning, durable outbox claim and suppression, query-first recovery, reconciliation, kill switch, restart, and pre/post summaries.
- [x] Add append-only migration `0022` with 26 live audit tables, leases, recovery generations, monotonic projections, hard write-disabled constraints, transactional application, idempotency, and injected rollback proof.
- [x] Add five safe socket-free CLIs, guarded-live threat model and operator runbooks, 23 offline tests, 9 PostgreSQL integration tests, and explicit CI coverage.
- [x] Repair Phase 8A authority integrity with typed evidence-producing preflight, same-run/configuration database bindings, transaction-locked PostgreSQL risk, operational kill freshness, incident-first recovery, and fee-aware Spot reservations.
- [x] Add append-only migration `0023`, real PostgreSQL-backed live CLIs, typed new-process reconstruction, exact OKX read/response validation, authoritative reconciliation, direct-SQL immutability guards, 21 dedicated offline regressions, 25 dedicated PostgreSQL regressions, and clean plus seeded migration verification while keeping production writes unreachable.
- [x] Add append-only migration `0024` with collector-sealed operational evidence, exact OKX response-envelope provenance, Phase 7 market lineage, PostgreSQL-authoritative instrument metadata, atomic reconciliation/risk sequencing, distinct kill-reset and run-continue authority, exact recovery classification, and 29 dedicated offline plus 29 dedicated PostgreSQL regressions, clean and seeded migration verification, and immutable 0001-0023 proof while keeping production writes unreachable.
- [x] Repair the final Phase 8A identity blockers with exact-UID-derived OKX account fingerprints, collector-derived runtime repository identity, fail-closed cross-account and forged-SHA rejection before authority persistence, and unchanged migrations `0001` through `0024` while production writes remain unreachable.
- [x] Add append-only migration `0025` with exact OKX `perm` parsing, response-authoritative Phase 8A read-only credential policy, bundle/envelope/credential permission provenance, direct-SQL and restart fail-closed enforcement, focused offline and PostgreSQL attacks, and immutable `0001`-`0024` proof while production writes remain unreachable.
- [x] Independently audit and accept Phase 8A at merge commit `18316f7462d4bbb9732308598f6aec743ebba0a3` after final-main GitHub Actions run `29373677324` passed all six jobs: Ubuntu Python 3.11 job `87222447626`, Ubuntu Python 3.12 job `87222447618`, Ubuntu Python 3.13 job `87222447670`, Windows Python 3.12 job `87222447634`, PostgreSQL 16 integration job `87222447656`, and public/private and runtime boundary job `87222447616`.
- [x] Verify immutable migrations `0001` through `0025` and confirm that Phase 8A used no real credentials and performed no production orders or production cancellations.
- [x] Merge the Phase 8A acceptance status as PR `#4` at `3fe4736832954b01a75906917af41a9bc55745d6`, establishing the sole Phase 8B baseline after main run `29377882425` passed all six jobs: Ubuntu Python 3.11 job `87235016240`, Ubuntu Python 3.12 job `87235016311`, Ubuntu Python 3.13 job `87235016296`, Windows Python 3.12 job `87235016261`, PostgreSQL 16 integration job `87235016225`, and public/private and runtime boundary job `87235016288`.

### Phase 8B: explicit authenticated read-only proof (proof accepted; operator bootstrap implemented pending independent audit)

- [x] Implement an explicit CLI-only authenticated read-only OKX production Spot preflight with required configuration hash, environment credential source, expected account fingerprint, expected reviewed SHA, and instrument; the no-flag path is socket-free and CI is blocked before PostgreSQL, repository identity, credential, or transport access.
- [x] Restrict the proof operation to the exact ordered six GETs for account configuration, balances, one Spot instrument, pending orders, unparameterized account positions, and venue time; require the positions data array to be empty, reject `trade` or `withdraw` immediately after the first account-config response, and keep all write/account-power methods unreachable.
- [x] Persist raw response envelopes only inside the existing private PostgreSQL evidence boundary and publish only configuration, provider-implementation, endpoint-catalog, and response-derived hashes, short fingerprint, account classification, permission sets, endpoint paths, timestamps/skew, currency names and counts, position/order counts, instrument identity/state, blockers/warnings, and truthful network read/write facts.
- [x] Add append-only migration `0026` because migrations `0022`-`0025` had private bundles but no standalone replay-safe proof record; enforce configuration/credential/account/run bindings, the exact unparameterized positions path, the zero-position rule, response derivation, fake-versus-operational classification, append-only immutability, transaction rollback, idempotent replay, restart reload, and direct-SQL fixture-promotion rejection.
- [x] Complete focused fake-transport offline and PostgreSQL 16 validation without real credentials, account identifiers, raw private exports, production orders, or production cancellations; production writes remain disabled.
- [x] Repair the Phase 8B audit findings by removing the unsupported `instType=SPOT` positions parameter, requiring an empty completed positions response, normalizing documented non-Spot position types as disallowed exposure, removing literal kill-switch success from the standalone proof credential gate, and documenting the normal-role direct-SQL guarantee without claiming resistance to an unrestricted authoritative database writer.
- [x] Independently audit and accept the Phase 8B implementation delivered by PR `#5`, candidate head `c3d5dc4b7f4d91d752f2dc2c06aba2078a896180`, and merge commit `9b23c71a31a6e4183c23b7504618ebff158fee1e`; final-main Actions run `29441539059` passed all six jobs: Ubuntu Python 3.11 job `87441507235`, Ubuntu Python 3.12 job `87441507210`, Ubuntu Python 3.13 job `87441507245`, Windows Python 3.12 job `87441507224`, PostgreSQL 16 integration job `87441507309`, and public/private and runtime boundary job `87441507243`.
- [x] Verify migration `0026` SHA-256 `698772fb68c5c4981256682d064c3be641193ab10c8dbf55e1a5b390ca7c504a`, keep migrations `0001` through `0026` immutable, and accept only the exact ordered authenticated read-only GETs: `/api/v5/account/config`, `/api/v5/account/balance`, `/api/v5/public/instruments?instId=<INSTRUMENT>&instType=SPOT`, `/api/v5/trade/orders-pending?instId=<INSTRUMENT>&instType=SPOT`, `/api/v5/account/positions`, and `/api/v5/public/time`; a successful proof requires an empty positions response and `position_count=0`.
- [x] Confirm that implementation and audit used no real credentials and performed no authenticated OKX request, production order, or production cancellation; the real local authenticated proof remains unexecuted and production writes remain disabled and unreachable.
- [x] Implement the dedicated `secure-eval-live-bootstrap` inspect/plan/initialize/verify workflow with pinned immutable migration hashes, exact repository/database/plan identity, an explicit read-only confirmation, the fixed conservative BTC-USDT Spot configuration factory, typed atomic PostgreSQL persistence, public-safe verification, offline attack coverage, and isolated PostgreSQL tests; this operator bootstrap remains pending independent audit and has not accessed credentials, OKX, or the existing operator database.
- [x] Harden bootstrap failure provenance with the exact last completed stage, require the complete Phase 8 schema contract before configuration insertion, derive the bootstrap result hash independently, and add altered-catalog plus concurrent-conflict regressions without changing migrations `0001` through `0026`.
- [x] Repair the dedicated bootstrap audit boundary with literal loopback-only PostgreSQL targets, dedicated database naming, server current-user/cluster/version/OID-bound plans, true persistent-object emptiness inspection, one target-wide full-operation advisory lock, one global configuration singleton, independently derived verify hashes, and real two-fingerprint plus full-initialize concurrency regressions; keep migrations `0001` through `0026` immutable and production writes unreachable.

## Todo

### Future provider enhancements

- [ ] Consider additional public OHLCV/trade/funding/instrument adapters after Phase 2; Bybit and Coinbase are not currently implemented.


### Phase 8: guarded live execution (remaining)
- [ ] Independently audit and accept the dedicated Phase 8B local operator bootstrap before using it for operator authorization.
- [ ] Optionally execute exactly one controlled local authenticated read-only proof with operator-owned environment credentials that are never persisted, only after the separate exact operator authorization.
- [ ] Independently review the resulting redacted proof before accepting the operational Phase 8B checkpoint.
- [ ] Do not design Phase 8C until the operational Phase 8B checkpoint is separately accepted.
- [ ] Keep production orders and cancellations disabled until a later explicitly approved phase.

### Phase 9: reporting + public delivery
- [ ] Build public report templates.
- [ ] Build private report templates.
- [ ] Implement artifact classification.
- [ ] Implement redaction workflow.
- [ ] Implement artifact hashing.
- [ ] Generate public-safe delivery bundles.
- [ ] Add release checklist.
- [ ] Add local sensitive file audit before delivery.
