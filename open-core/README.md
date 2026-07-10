# Open Core (Public)

## What this is
Runtime core for a production-style evaluation system.

## Main entrypoint
Run full pipeline:

```powershell
powershell -ExecutionPolicy Bypass -File scripts/start.ps1 -Mode all
```

Run specific mode:

```powershell
powershell -ExecutionPolicy Bypass -File scripts/start.ps1 -Mode quant
powershell -ExecutionPolicy Bypass -File scripts/start.ps1 -Mode generic
```

## Included
- Strategy interface contract
- Demo strategy (non-edge)
- Reproducible quant evaluation pipeline
- Generic non-quant evaluator demo
- Artifact generation scripts

## Not Included
- real_test_v5 / real_test_v6 private logic
- private params or secrets

## Public OHLCV vertical slice (Phase 2G-2I)

The public data framework includes injectable Binance Spot and OKX V5 historical OHLCV adapters,
offline normalization and single-source validation, deterministic cross-source reconciliation,
auditable PostgreSQL reconciliation persistence, and a provider-neutral orchestration service. It
contains no alpha, signals, backtesting, orders, account access, credentials, or live trading.

Run the default offline fixture pipeline (no network and no persistence):

```powershell
python open-core\scripts\run_public_ohlcv_pipeline.py
```

Run the tiny bounded public-network check only after explicit enablement:

```powershell
$env:ENABLE_PUBLIC_NETWORK_SMOKE = "true"
python open-core\scripts\run_public_ohlcv_pipeline.py --mode public-network
```

Persistence remains independently disabled. It requires PostgreSQL-only configuration, a supported
PostgreSQL driver, the environment gate, and the CLI flag:

```powershell
$env:ENABLE_POSTGRES_PERSISTENCE = "true"
python open-core\scripts\run_public_ohlcv_pipeline.py --persist
```

Downloaded public-network responses are kept in memory, are not written to the repository, and are
not printed. The summary contains provider status, observation counts, validation/reconciliation
status, and hash validity only.

## Complete public market-data layer (Phase 2)

The public data framework now includes Binance Spot and OKX Spot OHLCV and trades, Binance USDⓈ-M
and OKX SWAP funding, and Binance/OKX Spot and derivative instrument metadata. All paths use
injectable transports, deterministic UTC normalization, validation reports, accepted/rejected
gates, quarantine, and PostgreSQL-only persistence. No credentials, account endpoints, order
behavior, alpha logic, or execution behavior are included.

Run the fully offline fixture-default vertical:

    python open-core\scripts\run_public_market_data_pipeline.py

The summary covers OHLCV, trades, funding rates, and instruments without printing payloads.
Public-network mode is disabled unless ENABLE_PUBLIC_NETWORK_SMOKE=true. Persistence is a separate
gate requiring both --persist and ENABLE_POSTGRES_PERSISTENCE=true. Downloaded public responses
remain in memory and are not written unless persistence is independently and explicitly enabled.
