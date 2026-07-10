-- Phase 2J-2M: harden public trade, funding, and instrument identity/persistence.

BEGIN;

ALTER TABLE market_data.raw_source_observations
    ADD COLUMN IF NOT EXISTS data_type TEXT,
    ADD COLUMN IF NOT EXISTS provider_instrument_id TEXT,
    ADD COLUMN IF NOT EXISTS instrument_type TEXT;

ALTER TABLE market_data.validated_trades
    ADD COLUMN IF NOT EXISTS provider_name TEXT,
    ADD COLUMN IF NOT EXISTS provider_instrument_id TEXT,
    ADD COLUMN IF NOT EXISTS instrument_type TEXT,
    ADD COLUMN IF NOT EXISTS quote_quantity NUMERIC(38, 18),
    ADD COLUMN IF NOT EXISTS provider_sequence BIGINT,
    ADD COLUMN IF NOT EXISTS record_sha256 CHAR(64);

ALTER TABLE market_data.funding_rates
    ADD COLUMN IF NOT EXISTS provider_name TEXT,
    ADD COLUMN IF NOT EXISTS provider_instrument_id TEXT,
    ADD COLUMN IF NOT EXISTS instrument_type TEXT,
    ADD COLUMN IF NOT EXISTS settlement_asset TEXT,
    ADD COLUMN IF NOT EXISTS record_sha256 CHAR(64);

ALTER TABLE market_data.instruments
    ADD COLUMN IF NOT EXISTS provider_name TEXT,
    ADD COLUMN IF NOT EXISTS provider_instrument_id TEXT,
    ADD COLUMN IF NOT EXISTS canonical_display_symbol TEXT,
    ADD COLUMN IF NOT EXISTS settlement_asset TEXT,
    ADD COLUMN IF NOT EXISTS contract_type TEXT,
    ADD COLUMN IF NOT EXISTS margin_type TEXT,
    ADD COLUMN IF NOT EXISTS tick_size NUMERIC(38, 18),
    ADD COLUMN IF NOT EXISTS quantity_step NUMERIC(38, 18),
    ADD COLUMN IF NOT EXISTS minimum_quantity NUMERIC(38, 18),
    ADD COLUMN IF NOT EXISTS minimum_notional NUMERIC(38, 18),
    ADD COLUMN IF NOT EXISTS contract_value NUMERIC(38, 18),
    ADD COLUMN IF NOT EXISTS contract_multiplier NUMERIC(38, 18),
    ADD COLUMN IF NOT EXISTS margin_asset TEXT,
    ADD COLUMN IF NOT EXISTS listing_at_utc TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS expiry_at_utc TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS funding_interval TEXT,
    ADD COLUMN IF NOT EXISTS metadata_sha256 CHAR(64),
    ADD COLUMN IF NOT EXISTS validation_status TEXT,
    ADD COLUMN IF NOT EXISTS validation_report_id UUID,
    ADD COLUMN IF NOT EXISTS source_observation_ids UUID[] NOT NULL DEFAULT ARRAY[]::UUID[],
    ADD COLUMN IF NOT EXISTS provenance_jsonb JSONB NOT NULL DEFAULT '{}'::JSONB;

ALTER TABLE market_data.instruments
    DROP CONSTRAINT IF EXISTS instruments_symbol_exchange_key,
    DROP CONSTRAINT IF EXISTS instruments_instrument_type_check;

ALTER TABLE market_data.funding_rates
    DROP CONSTRAINT IF EXISTS funding_rates_symbol_exchange_funding_time_utc_key;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'chk_instruments_phase2_types'
          AND conrelid = 'market_data.instruments'::regclass
    ) THEN
        ALTER TABLE market_data.instruments
            ADD CONSTRAINT chk_instruments_phase2_types
            CHECK (instrument_type IN (
                'spot', 'perpetual_swap', 'dated_future', 'option', 'index'
            ));
    END IF;
END
$$;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'chk_validated_trades_record_sha256'
          AND conrelid = 'market_data.validated_trades'::regclass
    ) THEN
        ALTER TABLE market_data.validated_trades
            ADD CONSTRAINT chk_validated_trades_record_sha256
            CHECK (
                record_sha256 IS NULL
                OR record_sha256 ~ '^[0-9a-f]{64}$'
            );
    END IF;
END
$$;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'chk_funding_rates_record_sha256'
          AND conrelid = 'market_data.funding_rates'::regclass
    ) THEN
        ALTER TABLE market_data.funding_rates
            ADD CONSTRAINT chk_funding_rates_record_sha256
            CHECK (
                record_sha256 IS NULL
                OR record_sha256 ~ '^[0-9a-f]{64}$'
            );
    END IF;
END
$$;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'chk_instruments_metadata_sha256'
          AND conrelid = 'market_data.instruments'::regclass
    ) THEN
        ALTER TABLE market_data.instruments
            ADD CONSTRAINT chk_instruments_metadata_sha256
            CHECK (
                metadata_sha256 IS NULL
                OR metadata_sha256 ~ '^[0-9a-f]{64}$'
            );
    END IF;
END
$$;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'uq_validated_trades_provider_identity'
          AND conrelid = 'market_data.validated_trades'::regclass
    ) THEN
        ALTER TABLE market_data.validated_trades
            ADD CONSTRAINT uq_validated_trades_provider_identity
            UNIQUE (provider_name, provider_instrument_id, provider_trade_id);
    END IF;
END
$$;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'uq_funding_rates_provider_identity'
          AND conrelid = 'market_data.funding_rates'::regclass
    ) THEN
        ALTER TABLE market_data.funding_rates
            ADD CONSTRAINT uq_funding_rates_provider_identity
            UNIQUE (
                provider_name,
                provider_instrument_id,
                instrument_type,
                funding_time_utc
            );
    END IF;
END
$$;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'uq_instruments_versioned_identity'
          AND conrelid = 'market_data.instruments'::regclass
    ) THEN
        ALTER TABLE market_data.instruments
            ADD CONSTRAINT uq_instruments_versioned_identity
            UNIQUE (
                provider_name,
                provider_instrument_id,
                instrument_type,
                metadata_sha256
            );
    END IF;
END
$$;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'fk_instruments_validation_report'
          AND conrelid = 'market_data.instruments'::regclass
    ) THEN
        ALTER TABLE market_data.instruments
            ADD CONSTRAINT fk_instruments_validation_report
            FOREIGN KEY (validation_report_id)
            REFERENCES data_quality.validation_reports (validation_report_id);
    END IF;
END
$$;

CREATE INDEX IF NOT EXISTS idx_validated_trades_provider_instrument_time
    ON market_data.validated_trades (
        provider_name,
        provider_instrument_id,
        traded_at_utc
    );

CREATE INDEX IF NOT EXISTS idx_funding_rates_provider_instrument_time
    ON market_data.funding_rates (
        provider_name,
        provider_instrument_id,
        instrument_type,
        funding_time_utc
    );

CREATE INDEX IF NOT EXISTS idx_instruments_provider_identity
    ON market_data.instruments (
        provider_name,
        provider_instrument_id,
        instrument_type
    );

CREATE INDEX IF NOT EXISTS idx_instruments_canonical_type
    ON market_data.instruments (
        canonical_display_symbol,
        instrument_type,
        exchange
    );

COMMENT ON COLUMN market_data.instruments.metadata_sha256 IS
    'Content hash for an immutable versioned instrument metadata snapshot.';
COMMENT ON COLUMN market_data.validated_trades.record_sha256 IS
    'Stable normalized trade content hash used for conflict protection.';
COMMENT ON COLUMN market_data.funding_rates.record_sha256 IS
    'Stable normalized funding-rate content hash used for conflict protection.';

COMMIT;
