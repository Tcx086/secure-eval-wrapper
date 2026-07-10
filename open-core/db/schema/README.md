# Schema Groups

The initial migration creates separate PostgreSQL schemas for the major framework domains:

- `market_data`: raw source observations, validated bars, validated trades, funding rates, and
  instruments.
- `data_quality`: validation reports and individual quality checks.
- `alpha`: public alpha registry metadata.
- `signals`: signal runs and standardized signal outputs.
- `execution`: order intents, orders, fills, positions, and account snapshots.
- `backtesting`: backtest runs, aggregate metrics, equity curves, and stress results.
- `monitoring`: monitoring events, simulated FIX session events, and risk events.
- `audit`: run manifests and classified artifacts.

The schema is intentionally foundational. It defines storage contracts and metadata/provenance
anchors without implementing data collection, alpha logic, execution, backtesting, monitoring, or
live trading.

## Phase 2 completed market-data identity

Migration 0005 extends validated trades and funding rates with provider instrument identity and
conflict hashes. Instruments are stored as immutable metadata versions keyed by provider,
provider instrument ID, instrument type, and metadata hash. This prevents Spot and perpetual
contracts from sharing ambiguous identities while preserving historical metadata drift.
Migration 0006 maps legacy `perpetual`/`future` values to the canonical derivative names and adds
upgrade-safe checks that require complete provider identity and logical hashes on new trade,
funding, and instrument records.

## Phase 3-4 research schema

Migration 0007 establishes the original public research-storage boundary. Migration 0008 repairs its independent-audit findings: validated bars persist close time/finality; alpha values and signals retain complete immutable series identity, per-as-of stable hashes, separate formula/code/source-tree provenance, and series-based uniqueness; alpha values persist typed status/reasons and lookback bounds; signal ranks support averages; overlap policy/reasons are explicit; and `signals.signal_components` stores every normalized contribution with alpha/signal foreign keys and a deterministic hash. Signal rows remain execution-free research outputs.
## Phase 5 execution and backtesting schema

Migration 0009 makes complete provider/instrument/timeframe identity and stable content hashes
first-class throughout execution. New normalized tables retain both risk stages, every position
snapshot, realized perpetual funding lineage, and every cash change. `backtesting.backtest_events`
uses a deterministic sequence plus timestamp priority; metrics and equity remain children of the
backtest run. Partial bundle commits are prohibited by the repository transaction boundary.
Existing legacy rows can upgrade with nullable strengthened columns, while every new Phase 5 write
is complete and conflict-protected.

### Phase 5 complete-run membership repair

Migration 0011 normalizes complete-run membership without changing migrations 0001 through 0010.
Stable lineage-derived economic rows can be members of multiple complete deterministic backtest
runs through `backtesting.backtest_run_memberships`. Typed foreign keys protect every referenced
record and a run/type-local ordinal preserves deterministic reconstruction. Child
`backtest_run_id` values are non-authoritative owner hints with safe rehoming/nulling semantics;
queries use memberships exclusively. Aggregate metrics remain run-scoped and are keyed by the
complete `backtest_run_id`.
