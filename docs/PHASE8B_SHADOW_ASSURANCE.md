# Phase 8B Shadow Assurance

## Status and authority

This is a public-data and synthetic-account implementation candidate pending independent audit. It is an assurance path, not live execution, not an authenticated account proof, and not evidence that a strategy is profitable or production-ready. The real authenticated proof remains unexecuted, its authorization remains `NO`, the local operator bootstrap remains unexecuted, Phase 8C is not started, and Phase 9 remains todo.

Every shadow decision is permanently hypothetical. The runtime exposes no submit or cancel operation, cannot accept a production broker or authenticated transport, never loads exchange credentials, and reports these facts as zero or false:

- production transport calls;
- authenticated endpoint calls;
- credential reads;
- production writes;
- production submit/cancel reachability;
- real account data use;
- operator-database access;
- authenticated-proof execution.

## Architecture and shared application path

`ShadowAssuranceRuntime` accepts only the exact fixture or OKX public source type and only the exact memory test repository or PostgreSQL shadow repository type. Construction rejects callables, arbitrary endpoint transports, production broker/adapter types, and objects exposing submit, cancel, withdrawal, transfer, borrowing, leverage, send, or generic endpoint methods.

The runtime reuses the production application logic that is safe to evaluate without transport:

- Phase 8A configuration and repository-identity contracts;
- standardized signals and guarded-live order-intent construction;
- account preflight evaluation and exact approval challenge;
- immutable manifest construction;
- unchanged live risk evaluation and reservation calculation;
- PostgreSQL transaction, identity, idempotency, and audit-manifest contracts.

The final `ShadowOrderIntent` is a separate non-routable record with `shadow_only=true`, `production_write_enabled=false`, `submit_reachable=false`, `cancel_reachable=false`, and `transport_called=false`. It has no submit or cancel methods.

## Modes

### Deterministic fixture mode

Fixture mode is the default, socket-free path. It evaluates a stable catalog of 27 synthetic-account scenarios and 27 replayable public-market scenarios. An explicit disposable PostgreSQL target is required for a persisted run:

```text
secure-eval-live-shadow run \
  --fixture clean_flat_account \
  --postgres-database secure_eval_phase8b_shadow_local
```

The complete catalog can be evaluated without a socket or database:

```text
secure-eval-live-shadow matrix --repository-sha <40-character-reviewed-sha>
```

Omitting the persisted run's database target fails closed before a socket is opened. Only the literal loopback hosts `127.0.0.1` and `::1` are accepted; `localhost`, remote hosts, the operator database, and unrelated database names are rejected before connecting. The CLI has no PostgreSQL password argument and delegates authentication to libpq configuration such as `.pgpass` or `PGPASSFILE`. Accepted scenarios create at most one hypothetical shadow intent; blocked scenarios preserve ordered blocker codes and create no executable intent.

### Explicit public-data mode

Public network access is optional and never a CI dependency:

```text
secure-eval-live-shadow run \
  --allow-public-network \
  --provider okx \
  --instrument BTC-USDT \
  --public-timeout-seconds 3 \
  --postgres-database secure_eval_phase8b_shadow_public
```

This mode internally constructs the audited unauthenticated `OkxPublicProvider` and permits only `GET /api/v5/public/instruments` followed by `GET /api/v5/market/history-trades`. The trade request is symbols-only (`BTC-USDT`), has an empty instrument tuple and parameter map, uses a fixed five-minute UTC window, `limit<=10`, and `max_pages=1`. It performs no more than those two reads, sends empty headers, accepts no caller-supplied method/path/body/headers, and uses no authenticated account endpoint. The counter increments immediately before each transport send, including a send that raises. Source-issued provenance binds the exact source type and instance, endpoint sequence, actual send count, response hashes, instrument, classification, full payload hash, and failure kind before persistence. Timeouts must be greater than zero and at most ten seconds. A connection or response failure becomes a blocked public-data decision; it cannot be promoted to operational proof.

## Scenario matrix

The exact 27 account scenario IDs are `clean_flat_account`, `insufficient_quote_balance`, `insufficient_base_balance`, `existing_long_spot_position`, `synthetic_short_position`, `synthetic_perpetual_position`, `synthetic_futures_position`, `synthetic_options_exposure`, `pending_buy_order`, `pending_sell_order`, `excessive_reserved_notional`, `near_limit_notional`, `breached_daily_loss_guard`, `kill_switch_active`, `permission_read_only`, `permission_trade_enabled_synthetic_profile`, `conflicting_account_classification`, `malformed_account_snapshot`, `duplicate_positions`, `negative_balance`, `nan_or_infinity_quantity`, `wrong_settlement_asset`, `non_btc_usdt_instrument`, `unsupported_order_type`, `zero_quantity`, `quantity_rounding_below_minimum`, and `quantity_rounding_above_maximum`.

The exact 27 market scenario IDs are `normal_public_snapshot`, `stale_data`, `future_timestamp`, `clock_skew`, `crossed_bid_ask`, `bid_zero`, `ask_zero`, `negative_price`, `nan_or_infinity_price`, `missing_instrument_metadata`, `delisted_instrument`, `instrument_not_live`, `wrong_instrument_type`, `perpetual_instead_of_spot`, `duplicate_response_rows`, `malformed_json`, `incomplete_response`, `provider_error_code`, `timeout`, `connection_failure`, `rate_limit`, `partial_page`, `conflicting_public_sources`, `public_response_replay`, `stale_cached_response`, `fixture_marked_operational`, and `operational_response_marked_fixture`.

Each case has a stable scenario ID, stable input hash, expected result, ordered blockers, expected intent count, read/write count, and persistence result. Tests require all 54 catalog outcomes to match these declarations.

## PostgreSQL, restart, replay, and recovery

PostgreSQL 16 is the only authoritative persistent target. The CLI and repository accept only disposable names matching `secure_eval_phase8b_shadow_<suffix>`; the operator database name `secure_eval_phase8b` and unrelated databases are rejected before repository use. Tests create isolated names in these families:

- `secure_eval_phase8b_shadow_primary_<suffix>`;
- `secure_eval_phase8b_shadow_restart_<suffix>`;
- `secure_eval_phase8b_shadow_concurrent_<suffix>`.

No migration is added. The repository requires the exact immutable `0001` through `0026` migration catalog, canonical migration `0026` SHA-256 `698772fb68c5c4981256682d064c3be641193ab10c8dbf55e1a5b390ca7c504a`, and no `0027`. Because the existing schema has no dedicated shadow table, a complete decision and summary are stored atomically in the generic `audit.run_manifests` structure with `run_mode=simulation` and `storage_ref=phase8b_shadow_assurance`.

An identical run-ID/payload replay is idempotent and hash-stable. A different payload under the same run ID is an explicit conflict. Modified inputs receive new evidence and may bind `parent_input_hash`; previous evidence remains queryable. Fresh-process inspection reloads input, decision, manifest, blockers, and zero-write facts from PostgreSQL.

The test matrix covers seven concurrency cases and nine crash points from market normalization through post-commit response loss. Pre-commit failures leave no shadow row. A post-commit response loss leaves exactly one complete recoverable bundle, and retry is an idempotent replay. Preparing or partially finalized evidence is never reported as complete.

## Public assurance evidence

The generated artifact is `docs/evidence/phase8b_shadow_assurance_public.json`. Its keys and key order are fixed by an allowlist, and `evidence_payload_sha256` binds every other field. Its results come from an executable deterministic verifier, not caller-supplied passed counts. The artifact binds the repository SHA, scenario-catalog hash, runtime-implementation hash, unique per-case results and result hashes for all 54 catalog cases, three restart cases, six replay cases, seven actual in-memory concurrency cases, and all nine crash points. Validation reruns the verifier and rejects count, self-hash, verifier-hash, missing-case, duplicate-case, fake-success, and repository-SHA tampering.

PostgreSQL evidence has an explicit classification. The checked artifact uses `POSTGRESQL_VERIFIER_NOT_EXECUTED` and therefore reports zero PostgreSQL passed-case counts. The public-network smoke is also `PUBLIC_NETWORK_SMOKE_NOT_EXECUTED`; no real public request was made to generate the checked artifact. Validation additionally rejects extra/forbidden keys, local paths, secret-shaped values, unexpected high-entropy strings, nonzero transport/authentication/credential/write counts, and any claim of real-account, operator-database, authenticated-proof, submit, cancel, or migration-0027 authority.

The artifact reports `status=implemented_pending_independent_audit` and `independent_audit_status=pending`. Public-network smoke was not run for the checked artifact and is recorded as `PUBLIC_NETWORK_SMOKE_NOT_EXECUTED`, never converted into a fabricated success.

## Limitations

- Synthetic balances, positions, permissions, and pending orders are test inputs, not exchange observations.
- Fixture data proves deterministic application behavior only.
- Public market reads do not prove account state or authenticated connectivity.
- Generic audit-manifest reuse provides atomic PostgreSQL authority but not normalized shadow-specific query tables; adding migration `0027` is intentionally out of scope.
- Independent review is still required before this candidate can be accepted.
- No result authorizes operator bootstrap, authenticated proof, Phase 8C, Phase 9, or production submit/cancel.
