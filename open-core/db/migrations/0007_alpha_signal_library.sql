BEGIN;

ALTER TABLE alpha.alpha_registry
    ADD COLUMN alpha_version TEXT NOT NULL DEFAULT '1.0.0',
    ADD COLUMN category TEXT NOT NULL DEFAULT 'formulaic',
    ADD COLUMN required_data_types TEXT[] NOT NULL DEFAULT ARRAY[]::TEXT[],
    ADD COLUMN required_fields TEXT[] NOT NULL DEFAULT ARRAY[]::TEXT[],
    ADD COLUMN parameter_schema_jsonb JSONB NOT NULL DEFAULT '{}'::JSONB,
    ADD COLUMN default_parameters_jsonb JSONB NOT NULL DEFAULT '{}'::JSONB,
    ADD COLUMN minimum_warmup INTEGER NOT NULL DEFAULT 0,
    ADD COLUMN output_semantics TEXT NOT NULL DEFAULT 'unspecified',
    ADD COLUMN horizon TEXT NOT NULL DEFAULT 'unspecified',
    ADD COLUMN implementation_sha256 CHAR(64) NOT NULL DEFAULT repeat('0', 64),
    ADD COLUMN content_sha256 CHAR(64) NOT NULL DEFAULT repeat('0', 64);

ALTER TABLE alpha.alpha_registry
    DROP CONSTRAINT IF EXISTS alpha_registry_alpha_name_key;
ALTER TABLE alpha.alpha_registry
    ADD CONSTRAINT uq_alpha_registry_name_version UNIQUE (alpha_name, alpha_version),
    ADD CONSTRAINT chk_alpha_registry_minimum_warmup CHECK (minimum_warmup >= 0),
    ADD CONSTRAINT chk_alpha_registry_implementation_sha256 CHECK (implementation_sha256 ~ '^[0-9a-f]{64}$'),
    ADD CONSTRAINT chk_alpha_registry_content_sha256 CHECK (content_sha256 ~ '^[0-9a-f]{64}$');

CREATE TABLE IF NOT EXISTS alpha.alpha_runs (
    alpha_run_id UUID PRIMARY KEY,
    alpha_id UUID NOT NULL REFERENCES alpha.alpha_registry (alpha_id),
    alpha_name TEXT NOT NULL,
    alpha_version TEXT NOT NULL,
    symbol_set TEXT[] NOT NULL,
    window_start_utc TIMESTAMPTZ NOT NULL,
    window_end_utc TIMESTAMPTZ NOT NULL,
    dataset_refs TEXT[] NOT NULL,
    input_data_sha256 CHAR(64) NOT NULL CHECK (input_data_sha256 ~ '^[0-9a-f]{64}$'),
    config_sha256 CHAR(64) NOT NULL CHECK (config_sha256 ~ '^[0-9a-f]{64}$'),
    implementation_sha256 CHAR(64) NOT NULL CHECK (implementation_sha256 ~ '^[0-9a-f]{64}$'),
    content_sha256 CHAR(64) NOT NULL CHECK (content_sha256 ~ '^[0-9a-f]{64}$'),
    started_at_utc TIMESTAMPTZ NOT NULL,
    completed_at_utc TIMESTAMPTZ,
    status TEXT NOT NULL CHECK (status IN ('pending', 'running', 'completed', 'partial', 'failed')),
    output_count INTEGER NOT NULL CHECK (output_count >= 0),
    rejected_count INTEGER NOT NULL CHECK (rejected_count >= 0),
    skipped_count INTEGER NOT NULL CHECK (skipped_count >= 0),
    metadata_jsonb JSONB NOT NULL DEFAULT '{}'::JSONB,
    created_at_utc TIMESTAMPTZ NOT NULL DEFAULT now(),
    CHECK (window_end_utc > window_start_utc),
    CHECK (completed_at_utc IS NULL OR completed_at_utc >= started_at_utc)
);

CREATE TABLE IF NOT EXISTS alpha.alpha_values (
    alpha_value_id UUID PRIMARY KEY,
    alpha_run_id UUID NOT NULL REFERENCES alpha.alpha_runs (alpha_run_id) ON DELETE CASCADE,
    alpha_id UUID NOT NULL REFERENCES alpha.alpha_registry (alpha_id),
    alpha_name TEXT NOT NULL,
    alpha_version TEXT NOT NULL,
    symbol TEXT NOT NULL,
    timestamp_utc TIMESTAMPTZ NOT NULL,
    raw_score NUMERIC(38, 18),
    warmup_complete BOOLEAN NOT NULL,
    valid BOOLEAN NOT NULL,
    horizon TEXT NOT NULL,
    source_observation_ids UUID[] NOT NULL DEFAULT ARRAY[]::UUID[],
    dataset_sha256 CHAR(64) NOT NULL CHECK (dataset_sha256 ~ '^[0-9a-f]{64}$'),
    config_sha256 CHAR(64) NOT NULL CHECK (config_sha256 ~ '^[0-9a-f]{64}$'),
    implementation_sha256 CHAR(64) NOT NULL CHECK (implementation_sha256 ~ '^[0-9a-f]{64}$'),
    content_sha256 CHAR(64) NOT NULL CHECK (content_sha256 ~ '^[0-9a-f]{64}$'),
    provenance_jsonb JSONB NOT NULL DEFAULT '{}'::JSONB,
    created_at_utc TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT uq_alpha_values_run_symbol_time UNIQUE (alpha_run_id, symbol, timestamp_utc),
    CONSTRAINT chk_alpha_values_valid_score CHECK (NOT valid OR (warmup_complete AND raw_score IS NOT NULL))
);

ALTER TABLE signals.signal_runs
    ADD COLUMN alpha_run_ids UUID[] NOT NULL DEFAULT ARRAY[]::UUID[],
    ADD COLUMN symbol_universe TEXT[] NOT NULL DEFAULT ARRAY[]::TEXT[],
    ADD COLUMN window_start_utc TIMESTAMPTZ,
    ADD COLUMN window_end_utc TIMESTAMPTZ,
    ADD COLUMN ranking_config_jsonb JSONB NOT NULL DEFAULT '{}'::JSONB,
    ADD COLUMN threshold_config_jsonb JSONB NOT NULL DEFAULT '{}'::JSONB,
    ADD COLUMN combination_config_jsonb JSONB NOT NULL DEFAULT '{}'::JSONB,
    ADD COLUMN data_sha256 CHAR(64) CHECK (data_sha256 IS NULL OR data_sha256 ~ '^[0-9a-f]{64}$'),
    ADD COLUMN output_count INTEGER NOT NULL DEFAULT 0 CHECK (output_count >= 0),
    ADD COLUMN long_count INTEGER NOT NULL DEFAULT 0 CHECK (long_count >= 0),
    ADD COLUMN short_count INTEGER NOT NULL DEFAULT 0 CHECK (short_count >= 0),
    ADD COLUMN flat_count INTEGER NOT NULL DEFAULT 0 CHECK (flat_count >= 0),
    ADD COLUMN skipped_count INTEGER NOT NULL DEFAULT 0 CHECK (skipped_count >= 0),
    ADD COLUMN failure_count INTEGER NOT NULL DEFAULT 0 CHECK (failure_count >= 0),
    ADD COLUMN content_sha256 CHAR(64) NOT NULL DEFAULT repeat('0', 64) CHECK (content_sha256 ~ '^[0-9a-f]{64}$');

ALTER TABLE signals.signal_runs
    DROP CONSTRAINT IF EXISTS signal_runs_status_check;
ALTER TABLE signals.signal_runs
    ADD CONSTRAINT chk_signal_runs_phase4_status CHECK (status IN ('pending', 'running', 'completed', 'partial', 'failed')),
    ADD CONSTRAINT chk_signal_runs_phase4_window CHECK (window_end_utc IS NULL OR window_start_utc IS NULL OR window_end_utc > window_start_utc);

ALTER TABLE signals.signals
    ADD COLUMN alpha_ids_versions TEXT[] NOT NULL DEFAULT ARRAY[]::TEXT[],
    ADD COLUMN alpha_run_ids UUID[] NOT NULL DEFAULT ARRAY[]::UUID[],
    ADD COLUMN raw_score NUMERIC(38, 18) NOT NULL DEFAULT 0,
    ADD COLUMN normalized_score NUMERIC(38, 18) NOT NULL DEFAULT 0,
    ADD COLUMN rank INTEGER,
    ADD COLUMN percentile NUMERIC(10, 8),
    ADD COLUMN source_alpha_value_ids UUID[] NOT NULL DEFAULT ARRAY[]::UUID[],
    ADD COLUMN config_sha256 CHAR(64) CHECK (config_sha256 IS NULL OR config_sha256 ~ '^[0-9a-f]{64}$'),
    ADD COLUMN data_sha256 CHAR(64) CHECK (data_sha256 IS NULL OR data_sha256 ~ '^[0-9a-f]{64}$'),
    ADD COLUMN code_sha256 CHAR(64) CHECK (code_sha256 IS NULL OR code_sha256 ~ '^[0-9a-f]{64}$'),
    ADD COLUMN content_sha256 CHAR(64) NOT NULL DEFAULT repeat('0', 64) CHECK (content_sha256 ~ '^[0-9a-f]{64}$');

ALTER TABLE signals.signals
    ADD CONSTRAINT uq_signals_run_symbol_time_horizon UNIQUE (signal_run_id, symbol, timestamp_utc, horizon),
    ADD CONSTRAINT chk_signals_normalized_score CHECK (normalized_score >= -1 AND normalized_score <= 1),
    ADD CONSTRAINT chk_signals_percentile CHECK (percentile IS NULL OR (percentile >= 0 AND percentile <= 1)),
    ADD CONSTRAINT chk_signals_rank CHECK (rank IS NULL OR rank >= 1);

CREATE INDEX idx_alpha_registry_category_status
    ON alpha.alpha_registry (category, status, alpha_name, alpha_version);
CREATE INDEX idx_alpha_runs_alpha_window
    ON alpha.alpha_runs (alpha_id, window_start_utc, window_end_utc);
CREATE INDEX idx_alpha_values_run_symbol_time
    ON alpha.alpha_values (alpha_run_id, symbol, timestamp_utc);
CREATE INDEX idx_alpha_values_symbol_time
    ON alpha.alpha_values (symbol, timestamp_utc, alpha_id);
CREATE INDEX idx_signal_runs_window
    ON signals.signal_runs (window_start_utc, window_end_utc);
CREATE INDEX idx_signals_timestamp_alpha
    ON signals.signals (timestamp_utc, alpha_id, horizon);

COMMENT ON TABLE alpha.alpha_runs IS 'Auditable public alpha evaluation runs over validation-gated data.';
COMMENT ON TABLE alpha.alpha_values IS 'Point-in-time continuous public alpha values, including explicit warmup and invalid points.';
COMMENT ON COLUMN signals.signals.confidence IS 'Deterministic heuristic confidence in [0,1], not a probability of profit.';
COMMENT ON COLUMN signals.signals.source_alpha_value_ids IS 'Lineage to research AlphaValue records; signals contain no order or execution instructions.';

COMMIT;
