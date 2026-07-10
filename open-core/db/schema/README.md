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
