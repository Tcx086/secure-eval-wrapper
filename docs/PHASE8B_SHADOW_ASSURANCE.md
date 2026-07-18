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

Omitting the persisted run's database target fails closed before a socket is opened. Accepted scenarios create at most one hypothetical shadow intent; blocked scenarios preserve ordered blocker codes and create no executable intent.

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

This mode internally constructs the audited unauthenticated `OkxPublicProvider` and permits only OKX BTC-USDT Spot instrument metadata and recent public trades. It performs at most two bounded public reads, accepts no caller-supplied method/path/body/headers, and uses no authenticated account endpoint. Timeouts must be greater than zero and at most ten seconds. A connection or response failure becomes a blocked public-data decision; it cannot be promoted to operational proof.

## Scenario matrix

The 27 account cases cover clean and flat state, existing Spot/perpetual exposure, limits, drawdown and daily loss, kill state, permissions, mode, balances, reservations, pending orders, snapshots, duplicates, malformed values, unsupported currencies, and stale or inconsistent synthetic state.

The 27 market cases cover normal fixture/public/replay observations plus stale, missing, malformed, incomplete, contradictory, cached, unsupported, wrong-provider/instrument/type, bad metadata, timestamp, status, tick/lot/quantity bounds, crossed quotes, duplicate/conflicting source, timeout, rate-limit, provider error, empty response, and declaration-confusion failures.

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

The generated artifact is `docs/evidence/phase8b_shadow_assurance_public.json`. Its keys and key order are fixed by an allowlist, and `evidence_payload_sha256` binds every other field. Validation rejects extra/forbidden keys, local paths, secret-shaped values, unexpected high-entropy strings, nonzero transport/authentication/credential/write counts, and any claim of real-account, operator-database, authenticated-proof, submit, cancel, or migration-0027 authority.

The artifact reports `status=implemented_pending_independent_audit` and `independent_audit_status=pending`. Public-network smoke is classified truthfully; network unavailability is recorded as `PUBLIC_NETWORK_SMOKE_NOT_EXECUTED`, never converted into a fabricated success.

## Limitations

- Synthetic balances, positions, permissions, and pending orders are test inputs, not exchange observations.
- Fixture data proves deterministic application behavior only.
- Public market reads do not prove account state or authenticated connectivity.
- Generic audit-manifest reuse provides atomic PostgreSQL authority but not normalized shadow-specific query tables; adding migration `0027` is intentionally out of scope.
- Independent review is still required before this candidate can be accepted.
- No result authorizes operator bootstrap, authenticated proof, Phase 8C, Phase 9, or production submit/cancel.
