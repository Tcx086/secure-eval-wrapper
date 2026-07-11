# Safe Paper Trading

## Scope

Phase 7 implements provider-neutral paper trading without implementing guarded live execution. The supported environments are `paper_internal` and `paper_exchange_sandbox`; `simulated` remains the Phase 5 backtest mode. `live` is a validation-only forbidden future value. No configuration change can turn `PaperBroker` into a production broker, and there is no live broker class.

The enforced flow is:

```text
signal -> sizing -> OrderIntent -> pre-submit risk -> paper preflight
-> explicit approval -> immutable manifest -> PaperBroker -> venue acknowledgement
-> venue-confirmed paper fill -> paper accounting -> reconciliation -> monitoring
```

A signal is not an order, an intent is not an acknowledgement, and an acknowledgement is not a fill. Only a venue-confirmed paper fill changes paper balances or positions. Paper records are separate from Phase 5 simulation and never enter backtest metrics.

## Internal paper venue

`InternalPaperVenue` is a deterministic, in-process venue used for credential-free CI and recovery testing. It owns venue-side balances, positions, client and venue order IDs, states, fills, cancellation acknowledgements, request deduplication, sequence evidence, and an event stream. Explicit transitions cover pending acknowledgement, acknowledgement, partial/full fill, cancellation, rejection, expiry, and unknown recovery. Market, limit, stop, stop-limit, GTC, and IOC are supported.

Supplied events or configured schedules drive transitions; no uncontrolled wall-clock timing or random fill is used. Reproducible faults cover acknowledgement timeout, unknown submission, duplicate acknowledgement/fill, delayed fill, cancel timeout, lagged/stale snapshots, sequence gaps, and reconnect evidence. It is intentionally not an exchange emulator and does not model an order book, matching engine priority, or production liquidity.

Unlike Phase 5 bar simulation, internal paper supports cumulative partial fills and asynchronous acknowledgement. It does not alias or call `SimulatedBroker`.

## Official external demo boundary

The one Phase 7 external adapter is the official OKX V5 demo-trading REST subset, verified against the official API guide on 2026-07-11. The contract uses demo-created API keys, the exact REST origin `https://openapi.okx.com`, and mandatory `x-simulated-trading: 1`. Because OKX shares that REST hostname with production, the marker and exact route allowlist are inseparable proof: a missing or changed marker is treated as production access and rejected.

The allowlist covers account configuration, instruments, balances, positions, one order, one cancellation, one order query, pending/recent orders, and fills. The adapter's verified order subset is Spot market and limit orders. There are no withdrawal, transfer, deposit, subaccount, API-key-management, arbitrary-base-URL, production WebSocket, or FIX methods. Public WebSocket is not required for this subset and is not implemented.

The authenticated transport is injectable. Normal tests use fakes. The standard-library transport is created only after all external gates pass, disables redirects, bounds timeouts and response size, rejects malformed JSON, preserves only a response SHA-256, classifies retryability, and never returns headers or signatures in audit data.

## Credentials and redaction

Credentials are read lazily from `OKX_DEMO_API_KEY`, `OKX_DEMO_SECRET_KEY`, and `OKX_DEMO_PASSPHRASE`, or supplied by an injected test provider. Importing the package, running the internal venue, or invoking any default CLI does not read them. Required gates include explicit external CLI mode, `ENABLE_PAPER_TRADING=true`, exact provider/environment, validated endpoint catalog, valid bounded configuration, live false, inactive kill switch, and configured limits.

Only a public-safe credential reference is persisted: provider, local alias, source type, and a short one-way fingerprint of the local key alias. Secret keys, passphrases, authorization values, signatures, cookies, login payloads, and sensitive query parameters are centrally redacted and excluded from exceptions, hashes, manifests, monitoring, and PostgreSQL.

## Limits, preflight, approval, and manifest

Every configuration supplies finite positive limits for order/position/gross/net exposure, open orders, order/cancel rates, daily submitted notional, realized loss, drawdown, staleness, reconciliation age, unknown/unacknowledged duration, transport failures, clock skew, allowed instruments/assets/types/orders, derivative/short policy, persistence, approval, kill behavior, and run duration. Missing or unlimited values fail construction or preflight.

Preflight fails closed when required mode, endpoint, credential, market-data, account, limit, PostgreSQL, monitoring, kill-switch, or reconciliation evidence is unavailable. Its deterministic report binds configuration, account snapshot, implementation, endpoint catalog, and credential-reference hashes.

A separate short-lived approval binds one run, one preflight report, configuration, snapshot, credential reference, provider/environment, instrument allowlist, total notional, actor, nonce, and expiry. It is explicit, one-run, non-secret, and cannot be reused. Configuration, account, credential, run, expiry, or notional changes invalidate it.

The immutable manifest is created before submission and binds the run, environment, account alias, source identity, preflight, approval, initial snapshot, strategy reference, limits, duration, kill configuration, public-safe credential reference, and parent hashes. A broker cannot be constructed without the matching manifest and approval.

## Orders, unknown outcomes, and recovery

Stable client order IDs and idempotency keys derive from the paper run and economic intent. Reusing the same ID with identical economics returns existing venue state; changed economics conflicts. Duplicate fills apply once. A submission timeout becomes `submission_unknown`, never rejection. Recovery queries the original client ID, fetches fills/open orders, and reconciles; it never resubmits with a new ID. Exceeding the configured unknown duration triggers the kill switch.

Restart recovery loads the active run, manifest, unresolved submissions, and persisted kill switch from PostgreSQL. It queries by original client IDs, fetches fills/account state, reconciles, and resumes only when consistent. Restart never resets a kill switch or blindly resubmits pending economics.

## Accounting and reconciliation

Paper accounting is distinct from simulation. Spot uses cash/inventory, weighted average cost, realized PnL, venue fees, reserved buy cash, and reserved sell inventory. Cash and inventory cannot become negative and concurrent orders cannot over-reserve. Linear perpetual observations are supported only when explicitly enabled; no local leverage, margin, liquidation, or invented funding model exists.

Venue fills are authoritative execution evidence, PostgreSQL is the local audit authority, and venue balances/positions are external observations. Reconciliation compares order IDs/status/quantities, fills, fees/currencies, balances/reservations, positions/average entry, account mode, timestamps, and sequence evidence. Material differences are recorded, monitored, and block new orders; they are never silently overwritten. Any repair requires a recovery record.

## Monitoring, rate limits, and kill switch

Phase 6 monitoring accepts explicit `paper_internal` and `paper_exchange_sandbox` references and emits paper transport, order, fill, account, reconciliation, and kill evidence. Monitoring never submits or cancels orders directly.

Per-operation rate limits use an injected clock and explicit bounded retry schedules. Idempotency keys remain stable. Authentication and validation errors are not blindly retried. Unknown submission enters recovery, and cancellation retries remain bounded and audited.

The persisted kill switch states are armed, triggered, cancelling, killed, reset-pending, and reset. Triggers stop new submissions immediately, persist evidence, record cancellation intent before external attempts, reconcile outcomes, and continue applying late confirmed fills. Cancellation success is never assumed. Default behavior cancels open orders and monitors positions; it does not flatten. Automatic flattening is not implemented. Reset needs a fresh preflight, explicit approval, fresh snapshot, resolved reconciliation, and a new persisted reset record.

## PostgreSQL and CLIs

Migration `0016_phase7_safe_paper_trading.sql` adds separate run, manifest, preflight, approval, order/fill, account, reconciliation, recovery, kill, rate-limit, transport, credential-reference, and lifecycle tables. Start-run, submission outcome, fill, reconciliation, and kill bundles use explicit transactions. There is no SQLite or file authority and no silent in-memory fallback when persistence is requested.

Safe commands are:

```text
secure-eval-paper-preflight
secure-eval-paper-internal
secure-eval-paper-run
secure-eval-paper-status
secure-eval-paper-kill
secure-eval-paper-reconcile
```

Defaults are internal, credential-free, socket-free, and PostgreSQL-free. External demo mode requires exact provider/environment, manifest, approval, `--persist`, `ENABLE_PAPER_TRADING=true`, `ENABLE_POSTGRES_PERSISTENCE=true`, local demo credentials, and the immutable catalog. Preflight and run are separate; no interactive yes prompt is used. A real sandbox smoke is manual-only and additionally requires `ENABLE_REAL_SANDBOX_SMOKE=true`; public CI never uses credentials.

## Limitations and risk statement

Paper trading can lose sandbox funds or create unintended sandbox positions. Sandbox behavior may differ from production, and paper results do not prove live profitability. Credentials remain local and public CI is credential-free. No production/live execution, withdrawal, transfer, external production FIX, leverage engine, liquidation engine, or automatic flattening is implemented. Phase 8 requires a new independent design, implementation, review, and approval.
