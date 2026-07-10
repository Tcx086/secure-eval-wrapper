# Simulated Execution and Event-Driven Backtesting

## Scope

Phase 5 is a deterministic, public-safe, bar-level research simulator. It demonstrates professional
execution, risk, accounting, audit, and PostgreSQL persistence semantics. It is not connected to an
exchange and makes no claim that simulated fills predict real execution or profitability.

The implemented flow is:

```text
validated final bars -> public alpha values -> standardized signals -> target sizing
-> pre-submit risk -> order intents -> SimulatedBroker -> pre-fill risk -> fills
-> cash and position accounting -> fees/funding/marks -> equity curve and metrics
-> one PostgreSQL audit bundle
```

Signals are research decisions. A signal never changes cash, a position, equity, or PnL. Sizing
creates a target and delta; an accepted order intent still does not change the portfolio. Only a
`Fill` can change a position. Cash changes are represented by ledger entries for initial cash, Spot
notional, realized perpetual PnL, fees, and funding.

## Identity and deterministic audit records

Every series uses the Phase 3-4 `SeriesIdentity`: provider, exchange, provider instrument ID,
canonical symbol, instrument type, timeframe, and settlement asset. Symbol alone is never an
execution key. Two venues, two timeframes, and Spot versus perpetual remain distinct positions.

Economic records are frozen dataclasses containing the run ID, event/as-of time, parent IDs,
configuration SHA-256, stable record SHA-256, source-tree identity, full series identity where
applicable, and public-safe provenance. IDs are UUIDv5 values over stable economic content.
Collection-run IDs, source observation IDs, ingestion timestamps, database timestamps, connection
details, and local paths are excluded from economic hashes. Recollection with different source IDs
therefore preserves execution results.

Appending, changing, or deleting input strictly after a historical cutoff cannot change earlier
intent, risk, order, fill, position/account snapshot, equity-point, or event records. Run-level final
metrics naturally describe the requested run horizon; the same cutoff produces the same metrics.

## Event priority and anti-lookahead

Each validated final bar creates an open event and a completed-bar event. At one UTC timestamp the
engine always processes:

1. completed-bar execution for the bar ending now;
2. close mark update;
3. realized funding;
4. signal sizing, supersession, risk, and order submission; and
5. open execution for the bar beginning now.

A signal derived at a close cannot use that bar's open, high, low, or close as an execution price.
It may fill at the next actual bar open when that open timestamp equals the prior close, because the
signal step precedes the next-open step. Missing bars are not synthesized. Pending GTC orders wait
for the next real eligible event; a market order without one expires at run end.

## Sizing

Fixed-quantity sizing maps long to a positive target, short to a negative perpetual target, and flat
to zero. Spot short targets are rejected. Fixed-notional sizing divides configured notional by the
latest completed close available at the signal timestamp using `Decimal`. An optional quantity step
rounds absolute quantity down. A rounded-zero target and an already-reached target emit explicit
no-action audit events.

Before a new target is submitted, active orders for the same complete series are cancelled as
superseded. Delta quantity is target minus the current fill-derived position. Different series do
not cancel one another.

## Order simulation

- Market orders fill completely at the next eligible actual open, apply adverse taker slippage, and
  expire if no later open exists.
- A buy limit fills at an eligible open at or below its limit, otherwise at the limit when a later
  completed bar's low reaches it. A sell limit is symmetric. Limit prices never cross their bound,
  and resting fills are maker fills.
- A buy stop gaps at the eligible open when the open is above the stop; otherwise it triggers at the
  stop when high reaches it. A sell stop is symmetric. Stop fills apply adverse taker slippage, so a
  gap is never unrealistically improved.
- A stop-limit has explicit untriggered and triggered states. An open trigger may evaluate its limit
  at that open. An intrabar high/low trigger never assumes favorable unknown ordering; it activates
  the limit for the next bar. Trigger timestamp and activation reason are retained.
- GTC remains active until fill, cancellation, or run-end expiry. IOC evaluates the first eligible
  open and expires when not filled. Phase 5 never partially fills an order.

## Fees and slippage

The fee interface includes zero fees and fixed maker/taker basis points. The slippage interface
includes zero and fixed adverse basis points. Buys move up and sells move down. Market and stop
orders use taker slippage. Limit and stop-limit fills are never slipped beyond their limit. Every
fill persists base price, final price, slippage amount/basis points, maker/taker flag, fee amount,
and settlement currency. Fees reduce cash through ledger rows; metrics cannot invent a fee.

## Risk guard

The same deterministic `RiskGuard` runs before submission and immediately before a fill using the
actual simulated price and fee. It can block maximum order notional, per-series position notional,
gross exposure, absolute net exposure, open orders per series, gross-exposure/equity, and optional
drawdown. It also blocks invalid quantity/price, unsupported accounting, Spot shorts, and Spot buys
whose notional plus fee exceeds cash. Every accepted or blocked decision is persisted and emitted;
a blocked pre-fill decision rejects the order and creates no fill.

## Accounting

Spot buys reduce cash by notional plus fee; sells increase cash by notional minus fee. Inventory
uses weighted average cost, cannot be negative by default, and realizes PnL on sales. Spot equity is
cash plus marked inventory.

Linear perpetual opening notional does not transfer principal. Fees reduce cash. Reductions,
closures, and reversals realize signed PnL into cash. A reversal first realizes the closed quantity,
then resets the remaining opposite quantity at the new price. Unrealized PnL is signed quantity
times mark minus average entry, and equity is cash plus unrealized PnL. All long/short open,
increase, reduce, close, and reversal transitions are tested. There is no leverage, collateral,
margin, liquidation, or forced final close model.

Every fill produces a position snapshot. Zero quantity always means a null average entry. Fill IDs
are replay-protected, deterministic replay recreates state, and ledger balances reconcile to cash.

## Funding

Only realized public funding records with grounded interval evidence apply to linear perpetual
positions. The position and latest point-in-time mark immediately before the funding event are used:

```text
funding_cash_flow = -signed_quantity * mark_price * funding_rate
```

Positive rates make longs pay and shorts receive; negative rates reverse the direction. Interval
length is preserved rather than assumed. Spot never receives funding, predicted-only values are
ignored, and a position opened at the same timestamp after the funding priority does not receive
that payment. Zero payments are omitted unless explicitly configured.

## Missing candles, marks, and final positions

Crypto is treated as 24/7, but the engine never invents a candle, execution price, zero return, or
forward-filled fill. Marks may become stale; account and position snapshots carry stale counts or
age. Final open positions remain open and are valued at the latest available mark. No implicit
liquidation or end-of-run sale occurs.

## Metrics

Metrics are derived from fills, cash ledger, funding, positions, marks, and the actual equity curve:
cash/equity/PnL, fees, funding, return, drawdown, gross/net exposure, turnover, lifecycle counts,
open positions, completed/winning/losing round trips, win rate, gross profit/loss, profit factor, and
non-positive equity. Undefined win rate, profit factor, drawdown fraction, or return is null rather
than a convenient zero. Sharpe, CAGR, annualized return, alpha/beta, and probability statistics are
not fabricated.

## PostgreSQL and atomic persistence

Migration `0009_phase5_simulated_execution_backtesting.sql` preserves migrations `0001` through
`0008`, strengthens the original order-intent/order/fill/position/account and backtest tables, and
adds `execution.risk_decisions`, `execution.position_snapshots`,
`execution.funding_payments`, `execution.cash_ledger_entries`, and
`backtesting.backtest_events`.

Repositories accept an injected DB-API PostgreSQL connection, connect nowhere during import, use
parameterized SQL, return database-selected IDs, order half-open reads deterministically, and reject
same-logical-identity/different-hash conflicts. One outer transaction persists the complete run,
intents, decisions, final orders, fills, positions, ledgers, funding, snapshots, events, equity, and
metrics. Any child failure rolls the entire backtest bundle back. PostgreSQL is the only authority;
there is no SQLite or file-database fallback.

## Offline demo and validation

Install and run without network or database access:

```text
python -m pip install -e ./open-core
secure-eval-backtest
secure-eval-validate
```

The compatible source wrapper is `python open-core/scripts/run_public_backtest_pipeline.py`.
Persistence requires both `--persist` and `ENABLE_POSTGRES_PERSISTENCE=true`, plus the optional
`postgres` package extra and explicit `POSTGRES_*` settings. Output is a small public-safe summary;
credentials, connection strings, private files, and large trade logs are never printed.

## Explicit limitations

- Bar-level simulation only; no tick or order-book liquidity.
- No partial fills, latency model, exchange outages, or volume participation.
- No leverage engine, collateral optimization, portfolio/cross margin, or liquidation.
- No paper trading, live trading, authenticated exchange endpoint, FIX connection, or trading
  WebSocket.
- No optimizer, hyperparameter search, machine learning, dashboard, or web UI.
- No calibrated execution-quality or profitability claim.
