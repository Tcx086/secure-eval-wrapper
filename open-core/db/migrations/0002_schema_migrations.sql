BEGIN;

CREATE SCHEMA IF NOT EXISTS audit;

CREATE TABLE IF NOT EXISTS audit.schema_migrations (
    migration_id TEXT PRIMARY KEY,
    filename TEXT NOT NULL UNIQUE,
    sha256 CHAR(64) NOT NULL CHECK (sha256 ~ '^[0-9a-f]{64}$'),
    applied_at_utc TIMESTAMPTZ NOT NULL DEFAULT now(),
    description TEXT NOT NULL
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_schema_migrations_sha256
    ON audit.schema_migrations (sha256);

CREATE INDEX IF NOT EXISTS idx_schema_migrations_applied_at
    ON audit.schema_migrations (applied_at_utc);

COMMENT ON TABLE audit.schema_migrations IS 'Applied PostgreSQL migration metadata and file hashes.';
COMMENT ON COLUMN audit.schema_migrations.migration_id IS 'Stable migration identifier, usually the migration filename stem.';
COMMENT ON COLUMN audit.schema_migrations.filename IS 'Migration SQL filename.';
COMMENT ON COLUMN audit.schema_migrations.sha256 IS 'Lowercase SHA256 of the migration file content.';
COMMENT ON COLUMN audit.schema_migrations.applied_at_utc IS 'Timestamp when the migration was recorded as applied.';
COMMENT ON COLUMN audit.schema_migrations.description IS 'Short human-readable migration description.';

COMMIT;
