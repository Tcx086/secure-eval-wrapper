# Execution and FIX-Style Monitoring

## Purpose
The execution layer defines one shared contract for backtests, paper trading, and future guarded
live trading. The monitoring layer includes a simulated FIX-style interface to demonstrate
professional trading-system observability without connecting to a real broker or FIX venue.

## Execution Contract
Signals are not orders. Signals must pass through risk checks and become order intents before any
broker receives them.

```text
standardized signal
      |
      v
risk guard
      |
      v
order intent
      |
      v
Broker interface
      |
      v
order acknowledgement / reject
      |
      v
execution report / fill
      |
      v
position update / reconciliation
```

## Broker Interface Design
The shared `Broker` interface should support:
- `submit_order(intent)`
- `cancel_order(order_id)`
- `get_order_status(order_id)`
- `list_open_orders(account_ref)`
- `get_positions(account_ref)`
- `reconcile(account_ref)`
- `health_check()`

The interface should return structured acknowledgements, reject reasons, fills, cancel
acknowledgements, health status, and reconciliation summaries.

## Domain Objects

### Order Intent
Created by the strategy/execution planner before broker submission. Fields include
`order_intent_id`, `run_id`, `signal_id`, `mode`, `symbol`, `side`, `order_type`, `quantity`,
`limit_price`, `time_in_force`, `risk_context`, and `created_at_utc`.

### Order Result
Broker response to an order intent. Fields include `order_id`, `order_intent_id`, `broker_type`,
`status`, `broker_order_ref`, `acknowledged_at_utc`, and `reject_reason`.

### Fill
Execution fill record. Fields include `fill_id`, `order_id`, `symbol`, `side`, `quantity`, `price`,
`fee_amount`, `fee_currency`, `liquidity_flag`, and `filled_at_utc`.

## Broker Roadmap

### `SimulatedBroker`
Initial broker for backtesting. It simulates acknowledgements, applies fill/fee/slippage models,
emits simulated execution reports, supports cancel/reject simulation, and produces deterministic
results from seed and config.

### `PaperBroker`
Future broker for exchange sandbox or paper mode. It must use no real capital, require explicit
paper-mode credentials, reconcile against sandbox/paper account state, and use the same order
intent/result schema as simulated and live modes.

### `LiveBroker`
Future guarded broker for real exchange APIs. It is not implemented in Phase 0 and must be disabled
by default.

Required live controls:
- Explicit environment flag such as `ENABLE_LIVE_TRADING=true`.
- API keys loaded from local secrets only.
- Max notional per order.
- Max daily notional.
- Max open exposure.
- Symbol allowlist.
- Dry-run support.
- Kill switch.
- Risk summary before order submission.
- Risk summary after execution.
- Audit manifest for every live run.

## Execution Models

### Fill Model
Defines whether and how an order fills using bar/trade data, order type, side, quantity, limit
price, liquidity assumptions, partial fill rules, and deterministic seed.

### Fee Model
Crypto-specific fees should support maker fee, taker fee, fee currency, exchange-specific fee
schedules, and funding costs for perpetuals.

### Slippage Model
Slippage should support fixed basis points, volatility-adjusted slippage, volume participation, and
wider slippage during simulated exchange outages or stale data.

### Risk Guard
Checks max order notional, max position notional, symbol allowlist, mode restrictions, future
leverage/liquidation limits, and kill switch state.

### Position Manager
Updates positions from fills only, tracks realized/unrealized PnL, tracks average price, and
supports reconciliation against broker/account snapshots.

## Backtest Relationship
Backtests must use `SimulatedBroker`. The backtest engine should not directly modify equity from a
signal.

```text
signal -> order intent -> SimulatedBroker -> fill -> position manager -> portfolio metrics
```

## Simulated FIX-Style Monitoring
The project should include a simulated FIX-style monitoring layer. This is not a real FIX broker
connection. It demonstrates session monitoring, execution lifecycle events, latency, and failure
handling.

Concepts to simulate:
- Heartbeat with session ID, sequence number, send/receive time, latency, and status.
- Session states: `connecting`, `connected`, `degraded`, `disconnected`, `recovering`, `stopped`.
- Order acknowledgement.
- Execution report for fill, partial fill, reject, cancel, or status update.
- Cancel acknowledgement and cancel reject.
- Order reject, session-level reject, and risk reject.
- Latency for submit-to-ack, submit-to-fill, cancel-to-ack, and heartbeat round trip.
- Dropped connection, missed heartbeat, delayed report, duplicate report, and out-of-order sequence simulation.

## Monitoring Domains
Monitoring should emit events for data health, signal health, execution health, risk health, system
health, and future account health.

## Public Safety
Public demos may include simulated FIX events and synthetic execution reports. They must not include
real broker session IDs, real account IDs, API credentials, or private trade logs.
## Phase 5 implemented simulated execution

The shared contract is now implemented for backtesting by `SimulatedBroker`. It supports market,
limit, stop, and deterministic two-stage stop-limit orders; GTC and open-evaluated IOC; maker/taker
fees; adverse slippage; pre-submit and actual-price pre-fill risk; supersession cancellation; and
run-end expiry. Positions and cash change only from fills and realized funding. Equal-timestamp
priority is completed-bar execution, close mark, funding, signal/submission, then next-bar open, so
a signal never fills from its own completed bar.

The Phase 6 monitoring and simulated FIX profile below is implemented in-process only. No paper
adapter, live adapter, authenticated endpoint, external FIX connection, or trading WebSocket is implemented.
See `SIMULATED_EXECUTION_AND_BACKTESTING.md` for the normative Phase 5 rules.

## Phase 6 implemented monitoring and simulated FIX

The monitoring concepts are implemented as deterministic point-in-time checks with explicit `unknown` handling and PostgreSQL atomic bundles. Unknown evidence preserves open/acknowledged incidents; only explicit healthy evidence resolves them.

The audited simulated FIX profile now uses fill-derived positions, typed receive dispositions, canonical all-supported-field replay identity, rejected raw-message observations, independent heartbeat/grace/disconnect boundaries, deterministic in-process fault orchestration, and version/hash-protected PostgreSQL session projections backed by immutable session events. Spot inventory and perpetual reduce/flat/reverse behavior come only from fills; acknowledgements never mutate positions and duplicate fills cannot apply twice.

It remains in-process only and can call only `SimulatedBroker`; it has no external TCP, exchange, paper, or live route. Normative semantics and the FIX 4.4 compatibility table are in `MONITORING_AND_SIMULATED_FIX.md`.
