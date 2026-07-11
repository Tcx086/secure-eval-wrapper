# Monitoring and Simulated FIX 4.4-Compatible API

## Scope and safety boundary

Phase 6 provides deterministic point-in-time health evaluation and a strictly simulated, in-process FIX 4.4-compatible profile. It observes public data, signal lineage, Phase 5 simulated execution, recorded risk state, explicit system evidence, and simulated session state. Monitoring writes audit records only. It never mutates research or execution state.

The FIX subsystem is educational and research-oriented. It is not FIX-certified, opens no TCP listener or outbound connection, has no exchange or real counterparty, performs no authenticated access, and cannot route paper or live orders. Acknowledgement never changes a portfolio. Only an explicit synthetic market event passed to `SimulatedBroker` may create a fill, and only a fill may update gateway/accounting position state.

## Point-in-time monitoring and incidents

`MonitoringEngine` requires an explicit UTC `as_of_utc`; no check uses the wall clock. IDs are UUIDv5 values over stable canonical inputs. Record hashes include persisted logical/economic content, while public-safe operational metadata is excluded from stable identity.

Categories are `data`, `signal`, `execution`, `risk`, `system`, and `fix_session`. Check outcomes are `passed`, `warning`, `failed`, and `unknown`; aggregate health is `healthy`, `degraded`, `unhealthy`, or `unknown`. Aggregation precedence is:

```text
critical unhealthy > unhealthy > degraded > unknown > healthy
```

A continuous degraded/unhealthy episode is keyed to its exact health check and reason. Repeated failures update the episode. An explicit healthy result for that check resolves it. Missing or `unknown` evidence preserves an open or acknowledged incident and does not create a false recovery. A later failure after resolution creates a new deterministic episode ID.

## Supported FIX 4.4-compatible profile

The implementation was reviewed against FIX Trading Community’s [FIXimate FIX 4.4 message repository](https://fiximate.fixtrading.org/legacy/en/FIX.4.4/messages_sorted_by_name.html), including the authoritative pages for [NewOrderSingle](https://fiximate.fixtrading.org/legacy/en/FIX.4.4/body_495268.html), [OrderCancelRequest](https://fiximate.fixtrading.org/legacy/en/FIX.4.4/body_495470.html), [ExecutionReport](https://fiximate.fixtrading.org/legacy/en/FIX.4.4/body_5756.html), [OrderCancelReject](https://fiximate.fixtrading.org/legacy/en/FIX.4.4/body_494857.html), [BusinessMessageReject](https://fiximate.fixtrading.org/legacy/en/FIX.4.4/body_5251106.html), and [Logon](https://fiximate.fixtrading.org/legacy/en/FIX.4.4/body_494965.html). This is a deliberately narrow profile, not a claim of complete FIX 4.4 support or certification.

All messages require the exact standard header/trailer profile: `8`, `9`, `35`, `34`, `49`, `56`, `52`, optional replay tags `43`/`122`, and `10`, with `8=FIX.4.4`. The profile is ASCII with SOH delimiters. `BodyLength` counts bytes from tag 35 through the SOH before tag 10. `CheckSum` is the modulo-256 sum of every byte through that preceding SOH and is encoded as three digits.

| Message | MsgType | Profile-required body fields | Conditional/profile notes |
|---|---:|---|---|
| Heartbeat | `0` | none | `112` is emitted when answering TestRequest. |
| TestRequest | `1` | `112` | Starts an explicit pending response deadline. |
| ResendRequest | `2` | `7`, `16` | Generated for an inbound sequence gap. |
| Reject | `3` | `45` | `372` and `58` are emitted when available. |
| SequenceReset | `4` | `36` | `123` is supported; new sequence must move forward. |
| Logout | `5` | none | `58` is supported. |
| Logon | `A` | `98`, `108` | The profile supports no credentials or authentication fields. |
| ExecutionReport | `8` | `37`, `17`, `11`, `55`, `54`, `39`, `150`, `151`, `14`, `6` | Electronic simulated-order profile only. |
| OrderCancelReject | `9` | `37`, `11`, `41`, `39`, `434` | `102` and `58` are emitted. `434=1` identifies OrderCancelRequest. |
| NewOrderSingle | `D` | `11`, `55`, `54`, `60`, `38`, `40`, `59` | `44` required for limit; `99` required for stop; both for stop-limit. The profile requires TIF and supports only GTC/IOC. |
| OrderCancelRequest | `F` | `11`, `41`, `55`, `54`, `60`, `38` | Quantity must identify the remaining order quantity in this profile. |
| BusinessMessageReject | `j` | `372`, `380` | `45` and `58` are emitted; supported reason values are FIX 4.4 values `0` through `7`. |

Supported values are Side `1/2`, OrdType `1/2/3/4`, TimeInForce `1/3`, OrdStatus `0/2/4/8/C`, and ExecType `0/F/4/8/C`. An internal stop/stop-limit activation that remains working is represented with `ExecType=0` and `OrdStatus=0`; the implementation does not invent a non-FIX-4.4 “triggered” enum. Trade fills use `ExecType=F`, current filled status uses `OrdStatus=2`, cancel uses `4`, rejection uses `8`, and expiry uses `C`.

The codec enforces deterministic tag ordering, required/singleton tags, integer/finite Decimal/UTC values, enum values, BeginString, BodyLength, CheckSum, and non-empty text. Exact byte-hash and round-trip tests cover every supported message type.

## Replay and rejection semantics

A canonical replay identity includes MsgType, CompIDs, and every supported body/administrative field, including order, execution, quantity, price, stop, TIF, status, and reject fields. Only legitimate replay transport differences are excluded: `PossDupFlag`, current `SendingTime`, and `OrigSendingTime`.

Session receive returns a typed disposition: `accepted_new`, `accepted_replay`, `rejected`, or `sequence_gap`. An identical low-sequence PossDup is accepted as replay but never passed to economic gateway handling. Changed quantity, price, symbol, side, or any other supported field is rejected as a replay conflict. PostgreSQL uses the same replay identity, making identical replay persistence idempotent while rejecting changed content at the same session/direction/sequence.

Malformed bytes and constructed `FixMessage` values use the same typed rejected disposition. Codec validation failures, wrong CompIDs, invalid direct content, unsupported session state, and conflicting low-sequence content create a stable rejected observation, an immutable rejection event, and no inbound-sequence or economic processing. Both `SimulatedFixGateway.handle_raw()` and the tuple-compatible typed handler preserve the disposition and never throw an unaudited CompID exception.

A stable observation is keyed by session, direction, raw SHA-256, and rejection code. Timestamped delivery occurrences are persisted separately and can reference only a rejected parent message, so retrying the same logical occurrence is idempotent while the same malformed content received at two different times produces two ordered audit occurrences. Half-open history reads join each occurrence to its rejection code, reason, raw hash, sequence, type, and parsed header evidence. A different reason under the same stable identity conflicts; a different rejection code is explicitly separate.

## Session timing semantics

All time is injected; no session method reads a wall clock.

- Peer silence is measured only from the most recent inbound activity.
- At `heartbeat_interval_seconds`, the session emits TestRequest and records both send time and `pending_test_deadline_at_utc = sent + test_request_grace_seconds`.
- At the grace boundary, the pending request is marked expired and an immutable `test_request_grace_expired` event is emitted. A late matching Heartbeat cannot falsely restore the session.
- The connection remains in `test_request_pending` until `sent + disconnect_timeout_seconds`; at that independent boundary it disconnects.
- `disconnect_timeout_seconds` must be at least the grace period.
- When peer silence has not triggered TestRequest, outbound heartbeat scheduling uses the most recent outbound activity.

Thus heartbeat interval, response grace, and disconnect timeout each materially control a distinct boundary.

## Position/accounting lifecycle

Every NewOrderSingle reads the current fill-derived quantity through the gateway's deterministic position provider (or its internal fill-derived state). Spot available inventory is current filled inventory minus the remaining quantities of active Spot sell orders. Submission therefore reserves inventory deterministically; cancellation, rejection, expiry, and fill remove the active reservation. Pre-fill risk recalculates inventory at the actual fill boundary.

The existing `SimulatedBroker` invokes gateway accounting after each accepted fill and before evaluating the next eligible order. A fill is not returned or finalized if accounting raises, duplicate fills are replay-protected, and no Spot position may become negative. External accounting application requires paired snapshot and restore callbacks; mutation failures restore and verify the authoritative position before the exception escapes. Linear perpetual increase, reduce, flat, short, and reversal remain signed and do not use Spot reservations. Acknowledgements never change positions.

## Deterministic fault orchestration

`FaultOrchestrator` consumes `FaultSchedule` in-process. It implements drop before Logon processing, drop after acknowledgement, drop with an active order, heartbeat response loss, duplicate inbound delivery, inbound sequence gap, delayed outbound report, and reconnect delay. Every activation produces an activated `ConnectionFault`, an immutable session event, monitoring evidence, and a deterministic outcome. There is no randomness or networking.

## PostgreSQL persistence

Migrations `0013` and `0014` remain unchanged. Migration `0015_phase6_concurrency_and_audit_integrity.sql` adds rejected-occurrence history, complete per-event projection snapshots, and an authoritative session tail ordinal and hash. The upgrade repairs legacy hash links, appends a deterministic authoritative snapshot to every upgraded projection, including eventless sessions, and validates every existing chain before commit. Deferred PostgreSQL validation requires every changed session projection to identify a contiguous immutable event tail, match all state, sequence, last-message, pending-test, and grace-expiry fields, and carry the appropriate accepted-message, sent-message, transition, or timeout evidence. Exact unchanged replay from either the original or a freshly reconstructed object is idempotent without an unexplained projection version.

The public FIX demo persists one complete transaction containing the session projection, messages, rejected observations and occurrences, immutable session events, gateway-created `OrderIntent`, `RiskDecision`, `SimulatedOrder`, `Fill`, FIX order links, and execution reports. Any child failure rolls the entire session and execution lineage back. These records use only the existing `SimulatedBroker` and public simulation mode.

PostgreSQL is the only authority. There is no SQLite or file fallback.

## Double-gated offline CLIs

After `python -m pip install -e ./open-core`:

```text
secure-eval-monitor
secure-eval-fix-sim
```

Source wrappers are `python open-core/scripts/run_public_monitoring.py` and `python open-core/scripts/run_simulated_fix.py`. Defaults are synthetic, compact, database-free, socket-free, and do not import a PostgreSQL driver. Persistence requires both `--persist` and `ENABLE_POSTGRES_PERSISTENCE=true`, plus explicit `POSTGRES_HOST`, `POSTGRES_PORT`, `POSTGRES_DB`, `POSTGRES_USER`, `POSTGRES_PASSWORD`, and optional `POSTGRES_SSLMODE`. The driver is imported lazily, the connection is closed, and `persistence_status="postgresql"` plus non-zero execution-lineage counts are printed only after the combined transaction commits.

## Limitations

- Not FIX-certified and not a complete FIX engine.
- No external TCP session, exchange connectivity, real counterparty, credentials, or account monitoring.
- No paper/live routing, leverage, margin, collateral, or liquidation model.
- No production latency measurement or alert-delivery integration.
- No partial-fill/liquidity/order-book model beyond Phase 5.
- Operational thresholds are policies, not statistical guarantees.
- Health state and simulated execution do not predict profitability.
