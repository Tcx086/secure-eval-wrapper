# PostgreSQL Database Assets

This directory contains PostgreSQL-only database definitions for the public crypto trading
framework rebuild. It does not contain private data, seeds, account snapshots, trade logs, or
runtime trading logic.

## Layout

- `migrations/`: ordered SQL migrations.
- `schema/`: human-readable notes about schema groups and responsibilities.

## Local PostgreSQL

Local PostgreSQL runs through `infra/docker-compose.postgres.yml` and stores disposable local state
under `var/postgres/`, which is ignored by Git.

1. Create a local `.env` from `.env.example`.
2. Replace the example local password with a local development value.
3. Start PostgreSQL:

```powershell
.\open-core\scripts\postgres_local.ps1 start
```

4. Apply the initial schema:

```powershell
.\open-core\scripts\postgres_local.ps1 apply
```

5. Verify the schema:

```powershell
.\open-core\scripts\postgres_local.ps1 verify
```

The verification script reads connection settings from the environment, loading `.env` first when
present. It inspects the migration file and checks PostgreSQL catalog metadata; it does not insert
sample data.

## Direct Docker Command

From the repository root:

```powershell
docker compose --env-file .env -f infra\docker-compose.postgres.yml up -d
```

The service binds PostgreSQL only to `127.0.0.1`.
