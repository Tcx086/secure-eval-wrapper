-- Phase 2G-2I: auditable PostgreSQL persistence for cross-source reconciliation.

CREATE TABLE IF NOT EXISTS data_quality.reconciliation_results (
    reconciliation_id UUID PRIMARY KEY,
    validation_run_id UUID NOT NULL,
    data_type TEXT NOT NULL,
    symbol TEXT NOT NULL,
    timeframe TEXT,
    provider_names JSONB NOT NULL,
    window_start_utc TIMESTAMPTZ,
    window_end_utc TIMESTAMPTZ,
    status TEXT NOT NULL,
    config_sha256 CHAR(64) NOT NULL,
    dataset_sha256 CHAR(64) NOT NULL,
    result_sha256 CHAR(64) NOT NULL,
    metrics_jsonb JSONB NOT NULL,
    created_at_utc TIMESTAMPTZ NOT NULL,
    CONSTRAINT chk_reconciliation_results_provider_names_array
        CHECK (jsonb_typeof(provider_names) = 'array'),
    CONSTRAINT chk_reconciliation_results_window
        CHECK (
            window_start_utc IS NULL
            OR window_end_utc IS NULL
            OR window_end_utc >= window_start_utc
        ),
    CONSTRAINT chk_reconciliation_results_hashes
        CHECK (
            config_sha256 ~ '^[0-9a-f]{64}$'
            AND dataset_sha256 ~ '^[0-9a-f]{64}$'
            AND result_sha256 ~ '^[0-9a-f]{64}$'
        ),
    CONSTRAINT uq_reconciliation_results_identity
        UNIQUE NULLS NOT DISTINCT (
            validation_run_id,
            data_type,
            symbol,
            timeframe,
            config_sha256,
            dataset_sha256
        )
);

CREATE INDEX IF NOT EXISTS idx_reconciliation_results_validation_run
    ON data_quality.reconciliation_results (validation_run_id);

CREATE INDEX IF NOT EXISTS idx_reconciliation_results_symbol_window
    ON data_quality.reconciliation_results (
        symbol,
        timeframe,
        window_start_utc,
        window_end_utc
    );

CREATE INDEX IF NOT EXISTS idx_reconciliation_results_status
    ON data_quality.reconciliation_results (status);

CREATE INDEX IF NOT EXISTS idx_reconciliation_results_providers
    ON data_quality.reconciliation_results USING GIN (provider_names);

CREATE TABLE IF NOT EXISTS data_quality.reconciliation_check_results (
    result_id UUID PRIMARY KEY,
    reconciliation_id UUID NOT NULL
        REFERENCES data_quality.reconciliation_results (reconciliation_id)
        ON DELETE CASCADE,
    validation_run_id UUID NOT NULL,
    check_id UUID NOT NULL,
    check_type TEXT NOT NULL,
    status TEXT NOT NULL,
    severity TEXT NOT NULL,
    affected_observation_ids UUID[] NOT NULL,
    details_jsonb JSONB NOT NULL,
    created_at_utc TIMESTAMPTZ NOT NULL,
    CONSTRAINT uq_reconciliation_check_results_check
        UNIQUE (reconciliation_id, check_id)
);

CREATE INDEX IF NOT EXISTS idx_reconciliation_check_results_reconciliation
    ON data_quality.reconciliation_check_results (reconciliation_id);

CREATE INDEX IF NOT EXISTS idx_reconciliation_check_results_validation_run
    ON data_quality.reconciliation_check_results (validation_run_id);

CREATE INDEX IF NOT EXISTS idx_reconciliation_check_results_check_type
    ON data_quality.reconciliation_check_results (check_type);

CREATE INDEX IF NOT EXISTS idx_reconciliation_check_results_status
    ON data_quality.reconciliation_check_results (status);
