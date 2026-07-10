# Monitoring and Simulated FIX 4.4-Compatible API

## Scope and safety boundary

Phase 6 adds deterministic point-in-time health evaluation and a strictly simulated, in-process FIX 4.4-compatible subset. It observes public data, signal lineage, Phase 5 simulated execution, recorded risk state, explicit system evidence, and simulated session state. Monitoring writes audit records only; it never mutates signals, orders, fills, positions, cash, equity, PnL, or backtest results.

The FIX subsystem is educational and research-oriented. It is not FIX-certified, opens no TCP listener or outbound connection, has no exchange or real counterparty, performs no authenticated access, and cannot route paper or live orders. Acknowledgement never changes a portfolio. Only an explicit synthetic market event passed to the existing `SimulatedBroker` may create a fill, and only existing accounting callbacks may apply that fill.

## Point-in-time identities

`MonitoringEngine` requires an explicit UTC `as_of_utc`, immutable inputs, configuration, monitored-run reference, implementation SHA-256, and source-tree identity. IDs are UUIDv5 values over stable canonical inputs. Record hashes include persisted logical/economic content. Operational metadata such as database creation time or host details is retained separately and excluded from stable identities. No check uses `datetime.now()`.

## Health categories and aggregation

Categories are `data`, `signal`, `execution`, `risk`, `system`, and `fix_session`. Check outcomes are `passed`, `warning`, `failed`, and `unknown`; aggregate health is `healthy`, `degraded`, `unhealthy`, or `unknown`. Severity is `info`, `warning`, `error`, or `critical`.

Aggregation does not average enums. Precedence is:

```text
critical unhealthy > unhealthy > degraded > unknown > healthy
```

Every snapshot retains the exact child check IDs that caused its state. Disabled checks are omitted. Missing or unchecked evidence is `unknown`, never an assumed pass. Threshold comparisons are explicit; rate, utilization, and drawdown warning/critical boundaries use inclusive `>=` semantics. Health is operational evidence and does not predict profit.

## Category checks

Data health evaluates economic-availability freshness, future timestamps, fixed-duration gaps, duplicate identities, economic conflicts, finality, OHLC relationships, negative volume, identity support, and perpetual funding realization/interval/settlement evidence. It never synthesizes a bar or guesses calendar durations.

Signal health evaluates freshness, alpha/signal run status, skipped/warmup output, component lineage, duplicate identity, market-series lineage, point-in-time availability, overlap handling, hash conflicts, and flat concentration. Signal confidence is not treated as profit probability, and PnL is not a signal-health input.

Execution health reads Phase 5 state for order age/status rates, fill latency evidence, risk lineage, blocked-order fills, fill/order/intent lineage, replay, stale/unmarked positions, accounting reconciliation, memberships/projections, complete reconstruction, equity curves, and fill-derived PnL. It cannot modify a backtest.

Risk health evaluates recorded block rates/reasons, limit and exposure utilization, drawdown/equity, Spot-short and cash attempts, invalid prices, decision lineage, and accepted-limit violations. It introduces no leverage, margin, collateral, or liquidation model.

System health accepts explicit results for migration/catalog state, schema objects, PostgreSQL availability/transactions, package/source identity, status synchronization, live-disabled state, PostgreSQL-only authority, path boundaries, configuration, engine exceptions, and simulated FIX health. An item that was not checked remains `unknown`.

## Incidents

A continuous degraded/unhealthy episode is keyed by category, component, reason code, and monitored identity. The first result opens one deterministic episode; repeated results update its count and latest timestamp without opening duplicates; recovery resolves it; a later failure begins a new episode using its new start boundary. `acknowledged` is represented in domain/storage. Phase 6 sends no email, SMS, PagerDuty, Slack, Telegram, or other external notification.

## Implemented FIX subset

Header/trailer tags are `8`, `9`, `35`, `34`, `49`, `56`, `52`, optional `43`/`122`, and `10`, with `8=FIX.4.4`. Messages are ASCII with SOH delimiters. `BodyLength` counts bytes beginning at tag 35 through the SOH immediately before tag 10. `CheckSum` is the modulo-256 sum of every byte through that preceding SOH and is encoded as three digits.

Administrative messages: Logon `A`, Heartbeat `0`, TestRequest `1`, ResendRequest `2`, Reject `3`, SequenceReset `4`, and Logout `5`.

Application messages: NewOrderSingle `D`, OrderCancelRequest `F`, ExecutionReport `8`, OrderCancelReject `9`, and BusinessMessageReject `j`. Supported order types are market, limit, stop, and stop-limit; time in force is GTC or IOC. Implemented fields include `11`, `41`, `37`, `17`, `55`, `54`, `60`, `38`, `40`, `44`, `99`, `59`, `39`, `150`, `151`, `14`, `6`, and `58`.

The codec enforces deterministic ordering, required/singleton tags, integer/finite Decimal/UTC values, enum values, BeginString, BodyLength, CheckSum, and non-empty required text. Unknown optional tags are rejected unless public-safe extension preservation is explicitly enabled. A failed message never advances session or economic processing.

## Session, sequence, heartbeat, and faults

States are `disconnected`, `logon_pending`, `established`, `test_request_pending`, `logout_pending`, `recovering`, and `terminated`. Lower inbound sequence is rejected unless a matching valid `PossDup` replay is recognized. A higher sequence enters recovery and emits ResendRequest. SequenceReset cannot decrease expected sequence. Each outbound message increments exactly once.

All time comes from explicit timestamps. Peer silence at the heartbeat threshold emits TestRequest; a matching Heartbeat restores the session; the configured timeout causes a recorded simulated disconnect. Reconnect requires a new Logon.

The latency model records fixed decode, validation, risk, acknowledgement, broker, fill-report, and encode durations and thresholds. These are simulated processing durations, not network measurements. The preconfigured fault schedule supports drops around logon/acknowledgement/active orders, heartbeat loss, duplicate/gapped inbound messages, delayed reports, and reconnect delay. Fault activation is deterministic, public-safe, and recorded.

## PostgreSQL persistence

Migration `0013_phase6_monitoring_simulated_fix.sql` adds normalized monitoring runs, check results, health snapshots, incidents/occurrences, simulated FIX sessions/messages/order links, latency samples, and connection faults. It also repairs Phase 5 final-position projections by backfilling the latest snapshot belonging to the same complete run and position lineage, including mark, unrealized PnL, valuation time/source/staleness/status, source snapshot, and final hash.

Repositories use an injected DB-API PostgreSQL connection, parameterized SQL, deterministic ordering, half-open reads, database-selected IDs, idempotent same-hash retries, explicit different-hash conflicts, and no import-time I/O. One monitoring bundle transaction covers the run and every child. Simulated FIX message acceptance and resulting session/sequence state are persisted together. PostgreSQL is the only authority; there is no SQLite or file fallback.

## Offline demos

After `python -m pip install -e ./open-core`:

```text
secure-eval-monitor
secure-eval-fix-sim
```

Source wrappers are `python open-core/scripts/run_public_monitoring.py` and `python open-core/scripts/run_simulated_fix.py`. Defaults are synthetic, compact, database-free, and socket-free. Persistence requires both `--persist` and `ENABLE_POSTGRES_PERSISTENCE=true`; no fallback database is selected.

## Limitations

- Not FIX-certified and not a complete FIX engine.
- No external TCP session, exchange connectivity, or real counterparty.
- No paper/live routing, authenticated endpoint, credentials, or real account monitoring.
- No production latency measurement or alert-delivery integration.
- No partial-fill/liquidity/order-book model beyond Phase 5.
- Operational thresholds are policies, not statistical guarantees.
- Health state and simulated execution do not predict profitability.