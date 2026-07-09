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

Migration metadata tracks:

- `migration_id`
- `filename`
- `sha256`
- `applied_at_utc`
- `description`

The local helper applies all `*.sql` files in lexical order and records metadata after the metadata
table exists. It defaults to `.env`; pass `-EnvFilePath` for an explicit local env file. The
verifier checks local migration SHA256 values against `audit.schema_migrations` when connected to
PostgreSQL, using either a local Python PostgreSQL driver or the helper's Docker psql backend.

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
for required schemas, tables, columns, indexes, unique constraints, and migration metadata rows. It
does not insert sample data.

## Direct Docker Command

From the repository root:

```powershell
docker compose --env-file .env -f infra\docker-compose.postgres.yml up -d
```

The service binds PostgreSQL only to `127.0.0.1`.
