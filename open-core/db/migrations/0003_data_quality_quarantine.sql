BEGIN;

CREATE TABLE IF NOT EXISTS data_quality.quarantine_decisions (
    quarantine_id UUID PRIMARY KEY,
    validation_report_id UUID NOT NULL
        REFERENCES data_quality.validation_reports (validation_report_id),
    validation_run_id UUID NOT NULL,
    observation_id UUID NOT NULL,
    quarantine_reason TEXT NOT NULL,
    symbol TEXT,
    exchange TEXT,
    timeframe TEXT,
    source_sha256 CHAR(64) CHECK (
        source_sha256 IS NULL OR source_sha256 ~ '^[0-9a-f]{64}$'
    ),
    details_jsonb JSONB NOT NULL DEFAULT '{}'::JSONB,
    created_at_utc TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_quarantine_decisions_validation_report
    ON data_quality.quarantine_decisions (validation_report_id);
CREATE INDEX IF NOT EXISTS idx_quarantine_decisions_validation_run
    ON data_quality.quarantine_decisions (validation_run_id);
CREATE INDEX IF NOT EXISTS idx_quarantine_decisions_observation
    ON data_quality.quarantine_decisions (observation_id);
CREATE INDEX IF NOT EXISTS idx_quarantine_decisions_reason
    ON data_quality.quarantine_decisions (quarantine_reason);

COMMENT ON TABLE data_quality.quarantine_decisions IS
    'Offline data-quality rejection decisions linked to source observations and validation reports.';

COMMIT;
