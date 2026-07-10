# PostgreSQL Database Assets

This directory contains PostgreSQL-only database definitions for the public crypto trading
framework rebuild. It does not contain private data, seeds, account snapshots, trade logs, or
runtime trading logic.

## Layout

- `migrations/`: ordered SQL migrations.
- `schema/`: human-readable notes about schema groups and responsibilities.

## Migrations

Current migrations:

- `0001_initial_schema.sql`: creates the initial schema groups, tables, indexes, and constraints.
- `0002_schema_migrations.sql`: creates `audit.schema_migrations` for migration metadata.
- `0003_data_quality_quarantine.sql`: adds indexed quarantine decisions for failed offline validation observations.
- `0004_reconciliation_persistence.sql`: adds auditable reconciliation summaries and child check results with idempotency constraints.
- `0005_trade_funding_instrument_hardening.sql`: adds explicit trade/funding identity, immutable
  instrument metadata versions, conflict hashes, indexes, uniqueness, and foreign-key verification.
- `0006_phase2_final_hardening.sql`: normalizes legacy instrument types and enforces complete identities/hashes for new Phase 2 rows with upgrade-safe check constraints.
- `0007_alpha_signal_library.sql`: adds the original Phase 3-4 alpha and signal research schema.
- `0008_phase3_phase4_audit_repairs.sql`: adds close/finality availability, complete series identity, stable per-point hashes, explicit evaluation status and code provenance, average-rank storage, overlap policy, and normalized signal components.

Migration metadata tracks:

- `migration_id`
- `filename`
- `sha256`
- `applied_at_utc`
- `description`

The local helper bootstraps the metadata table, applies `*.sql` files in lexical order, and records
each migration immediately after it succeeds. Already-recorded migrations are skipped only when the
stored SHA256 matches the local file; hash mismatches fail clearly. The helper defaults to `.env`;
pass `-EnvFilePath` for an explicit local env file. The verifier checks local migration SHA256
values against `audit.schema_migrations` when connected to PostgreSQL, using either a local Python
PostgreSQL driver or the helper's Docker psql backend.

## Local PostgreSQL

Local PostgreSQL runs through `infra/docker-compose.postgres.yml` and stores disposable local state
under `var/postgres/`, which is ignored by Git.

1. Create a local `.env` from `.env.example`.
2. Replace the example local password with a local development value.
3. Start PostgreSQL:

```powershell
.\open-core\scripts\postgres_local.ps1 start
```

4. Apply all migrations and record migration metadata:

```powershell
.\open-core\scripts\postgres_local.ps1 apply
```

5. Verify the schema:

```powershell
.\open-core\scripts\postgres_local.ps1 verify
```

The verification script reads connection settings from the environment, loading `.env` first when
present. It inspects migrations, computes migration hashes, and checks PostgreSQL catalog metadata
for required schemas, tables, columns, indexes, unique constraints, and migration metadata rows.
This includes both Phase 2H reconciliation tables, their provider/status/window query indexes, and
their idempotency constraints. It
does not insert sample data.

## Direct Docker Command

From the repository root:

```powershell
docker compose --env-file .env -f infra\docker-compose.postgres.yml up -d
```

The service binds PostgreSQL only to `127.0.0.1`.

## Phase 3-4 migration and repositories

- `0007_alpha_signal_library.sql`: versions the public alpha registry, adds alpha runs/values, and hardens standardized signal runs/signals.
- `0008_phase3_phase4_audit_repairs.sql`: repairs the audited Phase 3-4 contracts without changing migrations `0001` through `0007`. It adds bar close/finality, complete series identity and series-based uniqueness, typed alpha status and lookback bounds, eligible-input/record hashes, formula/code/source-tree provenance, average-rank-compatible numeric storage, explicit overlap policy, and `signals.signal_components`.

The Phase 3-4 repositories use injected PostgreSQL DB-API connections, parameterized SQL, conflict-hash protection, database-selected IDs, half-open reads, and caller-owned outer transactions. The CLI uses one milestone-wide transaction for definitions, alpha runs/values, signal runs/signals, and signal components. No SQLite fallback exists.
