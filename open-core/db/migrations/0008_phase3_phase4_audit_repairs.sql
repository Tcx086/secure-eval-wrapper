BEGIN;

ALTER TABLE market_data.validated_bars
    ADD COLUMN bar_close_time_utc TIMESTAMPTZ,
    ADD COLUMN is_final BOOLEAN,
    ADD CONSTRAINT chk_validated_bars_close_after_open
        CHECK (bar_close_time_utc IS NULL OR bar_close_time_utc > bar_open_time_utc);

ALTER TABLE alpha.alpha_registry
    ADD COLUMN formula_sha256 CHAR(64) NOT NULL DEFAULT repeat('0', 64),
    ADD COLUMN implementation_code_sha256 CHAR(64) NOT NULL DEFAULT repeat('0', 64),
    ADD COLUMN repository_commit_sha TEXT NOT NULL DEFAULT 'legacy:0007';
UPDATE alpha.alpha_registry
SET formula_sha256 = encode(sha256(convert_to('legacy-formula-unavailable|' || alpha_name || '@' || alpha_version, 'UTF8')), 'hex'),
    implementation_code_sha256 = encode(sha256(convert_to('legacy-code-unavailable|' || alpha_name || '@' || alpha_version, 'UTF8')), 'hex')
WHERE implementation_code_sha256 = repeat('0', 64);
ALTER TABLE alpha.alpha_registry
    ADD CONSTRAINT chk_alpha_registry_formula_sha256 CHECK (formula_sha256 ~ '^[0-9a-f]{64}$'),
    ADD CONSTRAINT chk_alpha_registry_code_sha256 CHECK (implementation_code_sha256 ~ '^[0-9a-f]{64}$');

ALTER TABLE alpha.alpha_runs
    ADD COLUMN series_identity_sha256_set TEXT[] NOT NULL DEFAULT ARRAY[]::TEXT[],
    ADD COLUMN formula_sha256 CHAR(64) NOT NULL DEFAULT repeat('0', 64),
    ADD COLUMN implementation_code_sha256 CHAR(64) NOT NULL DEFAULT repeat('0', 64),
    ADD COLUMN repository_commit_sha TEXT NOT NULL DEFAULT 'legacy:0007';
UPDATE alpha.alpha_runs
SET formula_sha256 = encode(sha256(convert_to('legacy-formula-unavailable|' || alpha_name || '@' || alpha_version, 'UTF8')), 'hex'),
    implementation_code_sha256 = encode(sha256(convert_to('legacy-code-unavailable|' || alpha_name || '@' || alpha_version, 'UTF8')), 'hex')
WHERE implementation_code_sha256 = repeat('0', 64);
ALTER TABLE alpha.alpha_runs
    ADD CONSTRAINT chk_alpha_runs_formula_sha256 CHECK (formula_sha256 ~ '^[0-9a-f]{64}$'),
    ADD CONSTRAINT chk_alpha_runs_code_sha256 CHECK (implementation_code_sha256 ~ '^[0-9a-f]{64}$'),
    ADD CONSTRAINT chk_alpha_runs_series_hashes CHECK (
        COALESCE(array_to_string(series_identity_sha256_set, ''), '') ~ '^([0-9a-f]{64})*$'
    );

ALTER TABLE alpha.alpha_values
    ADD COLUMN provider_name TEXT NOT NULL DEFAULT 'legacy',
    ADD COLUMN exchange TEXT NOT NULL DEFAULT 'legacy',
    ADD COLUMN provider_instrument_id TEXT NOT NULL DEFAULT 'legacy',
    ADD COLUMN canonical_symbol TEXT,
    ADD COLUMN instrument_type TEXT NOT NULL DEFAULT 'spot',
    ADD COLUMN timeframe TEXT NOT NULL DEFAULT 'legacy_unspecified',
    ADD COLUMN settlement_asset TEXT,
    ADD COLUMN series_identity_sha256 CHAR(64) NOT NULL DEFAULT repeat('0', 64),
    ADD COLUMN evaluation_status TEXT,
    ADD COLUMN reason_code TEXT,
    ADD COLUMN reason_message TEXT,
    ADD COLUMN as_of_utc TIMESTAMPTZ,
    ADD COLUMN lookback_start_utc TIMESTAMPTZ,
    ADD COLUMN lookback_end_utc TIMESTAMPTZ,
    ADD COLUMN eligible_input_sha256 CHAR(64),
    ADD COLUMN formula_sha256 CHAR(64) NOT NULL DEFAULT repeat('0', 64),
    ADD COLUMN implementation_code_sha256 CHAR(64) NOT NULL DEFAULT repeat('0', 64),
    ADD COLUMN repository_commit_sha TEXT NOT NULL DEFAULT 'legacy:0007',
    ADD COLUMN record_sha256 CHAR(64);
UPDATE alpha.alpha_values
SET provider_instrument_id = symbol,
    canonical_symbol = symbol,
    series_identity_sha256 = encode(sha256(convert_to('legacy|provider=legacy|exchange=legacy|instrument=' || symbol || '|canonical=' || symbol || '|type=spot|timeframe=legacy_unspecified|settlement=', 'UTF8')), 'hex'),
    evaluation_status = CASE WHEN valid THEN 'emitted' WHEN NOT warmup_complete THEN 'warmup' ELSE 'invalid' END,
    as_of_utc = timestamp_utc,
    eligible_input_sha256 = dataset_sha256,
    formula_sha256 = encode(sha256(convert_to('legacy-formula-unavailable|' || alpha_name || '@' || alpha_version, 'UTF8')), 'hex'),
    implementation_code_sha256 = encode(sha256(convert_to('legacy-code-unavailable|' || alpha_name || '@' || alpha_version, 'UTF8')), 'hex'),
    record_sha256 = content_sha256
WHERE canonical_symbol IS NULL OR evaluation_status IS NULL OR as_of_utc IS NULL
   OR eligible_input_sha256 IS NULL OR record_sha256 IS NULL;
ALTER TABLE alpha.alpha_values
    ALTER COLUMN canonical_symbol SET NOT NULL,
    ALTER COLUMN evaluation_status SET NOT NULL,
    ALTER COLUMN as_of_utc SET NOT NULL,
    ALTER COLUMN eligible_input_sha256 SET NOT NULL,
    ALTER COLUMN record_sha256 SET NOT NULL,
    DROP CONSTRAINT uq_alpha_values_run_symbol_time,
    ADD CONSTRAINT uq_alpha_values_run_series_time_horizon
        UNIQUE (alpha_run_id, series_identity_sha256, timestamp_utc, horizon),
    ADD CONSTRAINT chk_alpha_values_series_sha256 CHECK (series_identity_sha256 ~ '^[0-9a-f]{64}$'),
    ADD CONSTRAINT chk_alpha_values_eligible_sha256 CHECK (eligible_input_sha256 ~ '^[0-9a-f]{64}$'),
    ADD CONSTRAINT chk_alpha_values_formula_sha256 CHECK (formula_sha256 ~ '^[0-9a-f]{64}$'),
    ADD CONSTRAINT chk_alpha_values_code_sha256 CHECK (implementation_code_sha256 ~ '^[0-9a-f]{64}$'),
    ADD CONSTRAINT chk_alpha_values_record_sha256 CHECK (record_sha256 ~ '^[0-9a-f]{64}$'),
    ADD CONSTRAINT chk_alpha_values_evaluation_status CHECK (evaluation_status IN ('emitted', 'warmup', 'skipped', 'invalid', 'failed')),
    ADD CONSTRAINT chk_alpha_values_as_of_timestamp CHECK (as_of_utc = timestamp_utc),
    ADD CONSTRAINT chk_alpha_values_lookback CHECK (
        (lookback_start_utc IS NULL OR lookback_end_utc IS NULL OR lookback_end_utc >= lookback_start_utc)
        AND (lookback_end_utc IS NULL OR lookback_end_utc <= as_of_utc)
    ),
    ADD CONSTRAINT chk_alpha_values_identity_nonempty CHECK (
        provider_name <> '' AND exchange <> '' AND provider_instrument_id <> ''
        AND canonical_symbol <> '' AND instrument_type <> '' AND timeframe <> ''
    );

ALTER TABLE signals.signal_runs
    ADD COLUMN series_identity_sha256_set TEXT[] NOT NULL DEFAULT ARRAY[]::TEXT[],
    ADD COLUMN formula_sha256 CHAR(64) NOT NULL DEFAULT repeat('0', 64),
    ADD COLUMN implementation_code_sha256 CHAR(64) NOT NULL DEFAULT repeat('0', 64),
    ADD COLUMN repository_commit_sha TEXT NOT NULL DEFAULT 'legacy:0007',
    ADD COLUMN overlap_policy TEXT,
    ADD COLUMN overlap_resolution_reason TEXT;
UPDATE signals.signal_runs
SET formula_sha256 = encode(sha256(convert_to('legacy-signal-formula-unavailable', 'UTF8')), 'hex'),
    implementation_code_sha256 = encode(sha256(convert_to('legacy-signal-code-unavailable', 'UTF8')), 'hex');
ALTER TABLE signals.signal_runs
    ADD CONSTRAINT chk_signal_runs_formula_sha256 CHECK (formula_sha256 ~ '^[0-9a-f]{64}$'),
    ADD CONSTRAINT chk_signal_runs_code_sha256 CHECK (implementation_code_sha256 ~ '^[0-9a-f]{64}$'),
    ADD CONSTRAINT chk_signal_runs_series_hashes CHECK (
        COALESCE(array_to_string(series_identity_sha256_set, ''), '') ~ '^([0-9a-f]{64})*$'
    ),
    ADD CONSTRAINT chk_signal_runs_overlap_policy CHECK (overlap_policy IS NULL OR overlap_policy IN ('fail', 'skip_group', 'force_flat'));

ALTER TABLE signals.signals
    ADD COLUMN provider_name TEXT NOT NULL DEFAULT 'legacy',
    ADD COLUMN exchange TEXT NOT NULL DEFAULT 'legacy',
    ADD COLUMN provider_instrument_id TEXT NOT NULL DEFAULT 'legacy',
    ADD COLUMN canonical_symbol TEXT,
    ADD COLUMN instrument_type TEXT NOT NULL DEFAULT 'spot',
    ADD COLUMN timeframe TEXT NOT NULL DEFAULT 'legacy_unspecified',
    ADD COLUMN settlement_asset TEXT,
    ADD COLUMN series_identity_sha256 CHAR(64) NOT NULL DEFAULT repeat('0', 64),
    ADD COLUMN formula_sha256 CHAR(64) NOT NULL DEFAULT repeat('0', 64),
    ADD COLUMN implementation_code_sha256 CHAR(64) NOT NULL DEFAULT repeat('0', 64),
    ADD COLUMN repository_commit_sha TEXT NOT NULL DEFAULT 'legacy:0007',
    ADD COLUMN overlap_policy TEXT,
    ADD COLUMN resolution_reason TEXT,
    ADD COLUMN record_sha256 CHAR(64);
UPDATE signals.signals
SET provider_instrument_id = symbol,
    canonical_symbol = symbol,
    series_identity_sha256 = encode(sha256(convert_to('legacy|provider=legacy|exchange=legacy|instrument=' || symbol || '|canonical=' || symbol || '|type=spot|timeframe=legacy_unspecified|settlement=', 'UTF8')), 'hex'),
    formula_sha256 = encode(sha256(convert_to('legacy-signal-formula-unavailable', 'UTF8')), 'hex'),
    implementation_code_sha256 = encode(sha256(convert_to('legacy-signal-code-unavailable', 'UTF8')), 'hex'),
    record_sha256 = content_sha256
WHERE canonical_symbol IS NULL OR record_sha256 IS NULL;
ALTER TABLE signals.signals
    ALTER COLUMN canonical_symbol SET NOT NULL,
    ALTER COLUMN record_sha256 SET NOT NULL,
    ALTER COLUMN rank TYPE NUMERIC(18, 8) USING rank::NUMERIC,
    DROP CONSTRAINT uq_signals_run_symbol_time_horizon,
    ADD CONSTRAINT uq_signals_run_series_time_horizon
        UNIQUE (signal_run_id, series_identity_sha256, timestamp_utc, horizon),
    ADD CONSTRAINT chk_signals_series_sha256 CHECK (series_identity_sha256 ~ '^[0-9a-f]{64}$'),
    ADD CONSTRAINT chk_signals_formula_sha256 CHECK (formula_sha256 ~ '^[0-9a-f]{64}$'),
    ADD CONSTRAINT chk_signals_code_sha256 CHECK (implementation_code_sha256 ~ '^[0-9a-f]{64}$'),
    ADD CONSTRAINT chk_signals_record_sha256 CHECK (record_sha256 ~ '^[0-9a-f]{64}$'),
    ADD CONSTRAINT chk_signals_overlap_policy CHECK (overlap_policy IS NULL OR overlap_policy IN ('fail', 'skip_group', 'force_flat')),
    ADD CONSTRAINT chk_signals_identity_nonempty CHECK (
        provider_name <> '' AND exchange <> '' AND provider_instrument_id <> ''
        AND canonical_symbol <> '' AND instrument_type <> '' AND timeframe <> ''
    );

CREATE TABLE IF NOT EXISTS signals.signal_components (
    signal_component_id UUID PRIMARY KEY,
    signal_id UUID NOT NULL REFERENCES signals.signals (signal_id) ON DELETE CASCADE,
    alpha_value_id UUID NOT NULL REFERENCES alpha.alpha_values (alpha_value_id),
    alpha_id UUID NOT NULL REFERENCES alpha.alpha_registry (alpha_id),
    raw_value NUMERIC(38, 18) NOT NULL,
    normalized_value NUMERIC(38, 18) NOT NULL,
    configured_weight NUMERIC(38, 18) NOT NULL,
    effective_weight NUMERIC(38, 18) NOT NULL,
    signed_contribution NUMERIC(38, 18) NOT NULL,
    component_disposition TEXT NOT NULL,
    resolution_reason TEXT,
    component_sha256 CHAR(64) NOT NULL,
    public_metadata_jsonb JSONB NOT NULL DEFAULT '{}'::JSONB,
    created_at_utc TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT uq_signal_components_signal_alpha_value UNIQUE (signal_id, alpha_value_id),
    CONSTRAINT chk_signal_components_sha256 CHECK (component_sha256 ~ '^[0-9a-f]{64}$'),
    CONSTRAINT chk_signal_components_disposition CHECK (
        component_disposition IN ('contributed', 'flat', 'overlap_forced_flat', 'insufficient_coverage_flat')
    )
);

CREATE INDEX idx_validated_bars_close_availability
    ON market_data.validated_bars (bar_close_time_utc, is_final);
CREATE INDEX idx_alpha_values_series_time
    ON alpha.alpha_values (series_identity_sha256, timestamp_utc, alpha_id);
CREATE INDEX idx_signals_series_time
    ON signals.signals (series_identity_sha256, timestamp_utc, horizon);
CREATE INDEX idx_signal_components_signal
    ON signals.signal_components (signal_id, alpha_id, alpha_value_id);
CREATE INDEX idx_signal_components_alpha_value
    ON signals.signal_components (alpha_value_id);

COMMENT ON COLUMN market_data.validated_bars.bar_close_time_utc IS
    'Persisted economic availability boundary. Legacy nulls may be derived only for explicitly supported fixed-duration timeframes.';
COMMENT ON COLUMN market_data.validated_bars.is_final IS
    'False bars are never eligible for alpha evaluation.';
COMMENT ON COLUMN alpha.alpha_values.eligible_input_sha256 IS
    'Stable hash of economic records available at as_of_utc; excludes collection provenance and future records.';
COMMENT ON TABLE signals.signal_components IS
    'Normalized immutable child rows preserving every alpha contribution to a research signal.';

COMMIT;
