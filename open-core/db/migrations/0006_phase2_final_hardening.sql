-- Phase 2 final hardening: legacy instrument types and new-record identity guards.

BEGIN;

ALTER TABLE market_data.instruments
    DROP CONSTRAINT IF EXISTS chk_instruments_phase2_types;

UPDATE market_data.instruments
SET instrument_type = CASE instrument_type
    WHEN 'perpetual' THEN 'perpetual_swap'
    WHEN 'future' THEN 'dated_future'
    ELSE instrument_type
END
WHERE instrument_type IN ('perpetual', 'future');

ALTER TABLE market_data.instruments
    ADD CONSTRAINT chk_instruments_phase2_types
    CHECK (instrument_type IN (
        'spot', 'perpetual_swap', 'dated_future', 'option', 'index'
    ));

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'chk_validated_trades_phase2_identity_required'
          AND conrelid = 'market_data.validated_trades'::regclass
    ) THEN
        ALTER TABLE market_data.validated_trades
            ADD CONSTRAINT chk_validated_trades_phase2_identity_required
            CHECK (
                provider_name IS NOT NULL
                AND provider_instrument_id IS NOT NULL
                AND provider_trade_id IS NOT NULL
                AND instrument_type IS NOT NULL
                AND record_sha256 IS NOT NULL
            ) NOT VALID;
    END IF;
END
$$;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'chk_funding_rates_phase2_identity_required'
          AND conrelid = 'market_data.funding_rates'::regclass
    ) THEN
        ALTER TABLE market_data.funding_rates
            ADD CONSTRAINT chk_funding_rates_phase2_identity_required
            CHECK (
                provider_name IS NOT NULL
                AND provider_instrument_id IS NOT NULL
                AND instrument_type IS NOT NULL
                AND settlement_asset IS NOT NULL
                AND record_sha256 IS NOT NULL
            ) NOT VALID;
    END IF;
END
$$;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'chk_instruments_phase2_identity_required'
          AND conrelid = 'market_data.instruments'::regclass
    ) THEN
        ALTER TABLE market_data.instruments
            ADD CONSTRAINT chk_instruments_phase2_identity_required
            CHECK (
                provider_name IS NOT NULL
                AND provider_instrument_id IS NOT NULL
                AND canonical_display_symbol IS NOT NULL
                AND instrument_type IS NOT NULL
                AND metadata_sha256 IS NOT NULL
                AND validation_status IS NOT NULL
            ) NOT VALID;
    END IF;
END
$$;

COMMENT ON CONSTRAINT chk_validated_trades_phase2_identity_required
    ON market_data.validated_trades IS
    'Enforces complete provider identity and logical content hashes for new Phase 2 trade rows.';
COMMENT ON CONSTRAINT chk_funding_rates_phase2_identity_required
    ON market_data.funding_rates IS
    'Enforces complete derivative identity and logical content hashes for new Phase 2 funding rows.';
COMMENT ON CONSTRAINT chk_instruments_phase2_identity_required
    ON market_data.instruments IS
    'Enforces complete immutable metadata identity for new Phase 2 instrument versions.';

COMMIT;
