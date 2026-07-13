-- Phase 7 fifth independent audit repair. Migrations 0001 through 0019 remain immutable.
-- PostgreSQL remains the sole authority; no Phase 8 or live-execution capability is introduced.

ALTER TABLE execution.paper_market_data_evidence
    ADD COLUMN IF NOT EXISTS source_kind text NOT NULL DEFAULT 'fixture',
    ADD COLUMN IF NOT EXISTS exchange text,
    ADD COLUMN IF NOT EXISTS provider_instrument_id text,
    ADD COLUMN IF NOT EXISTS instrument_type text,
    ADD COLUMN IF NOT EXISTS source_table text,
    ADD COLUMN IF NOT EXISTS source_row_id text,
    ADD COLUMN IF NOT EXISTS validation_report_id uuid REFERENCES data_quality.validation_reports(validation_report_id),
    ADD COLUMN IF NOT EXISTS price numeric,
    ADD COLUMN IF NOT EXISTS price_type text,
    ADD COLUMN IF NOT EXISTS quote_currency text,
    ADD COLUMN IF NOT EXISTS normalized_record_sha256 text;
UPDATE execution.paper_market_data_evidence
SET exchange=COALESCE(exchange,series_identity_jsonb->>'exchange'),
    provider_instrument_id=COALESCE(provider_instrument_id,series_identity_jsonb->>'provider_instrument_id',instrument),
    instrument_type=COALESCE(instrument_type,series_identity_jsonb->>'instrument_type'),
    source_row_id=COALESCE(source_row_id,observation_id),
    price_type=COALESCE(price_type,'close'),
    quote_currency=COALESCE(quote_currency,series_identity_jsonb->>'settlement_asset'),
    normalized_record_sha256=COALESCE(normalized_record_sha256,observation_sha256);
ALTER TABLE execution.paper_market_data_evidence
    ALTER COLUMN exchange SET NOT NULL,
    ALTER COLUMN provider_instrument_id SET NOT NULL,
    ALTER COLUMN instrument_type SET NOT NULL,
    ALTER COLUMN source_row_id SET NOT NULL,
    ALTER COLUMN price_type SET NOT NULL,
    ALTER COLUMN quote_currency SET NOT NULL,
    ALTER COLUMN normalized_record_sha256 SET NOT NULL;
ALTER TABLE execution.paper_market_data_evidence
    DROP CONSTRAINT IF EXISTS paper_market_data_evidence_source_kind_check,
    DROP CONSTRAINT IF EXISTS paper_market_data_evidence_price_check,
    DROP CONSTRAINT IF EXISTS paper_market_data_evidence_price_type_check,
    DROP CONSTRAINT IF EXISTS paper_market_data_evidence_normalized_hash_check,
    ADD CONSTRAINT paper_market_data_evidence_source_kind_check CHECK (source_kind IN ('fixture','postgresql')),
    ADD CONSTRAINT paper_market_data_evidence_price_check CHECK (price IS NULL OR price > 0),
    ADD CONSTRAINT paper_market_data_evidence_price_type_check CHECK (price_type IN ('close','last','bid','ask','mark','index','open','high','low')),
    ADD CONSTRAINT paper_market_data_evidence_normalized_hash_check CHECK (normalized_record_sha256 ~ '^[0-9a-f]{64}$'),
    ADD CONSTRAINT paper_market_data_evidence_postgres_complete_check CHECK (
        source_kind <> 'postgresql' OR (source_table IS NOT NULL AND validation_report_id IS NOT NULL AND price IS NOT NULL)
    );

ALTER TABLE execution.paper_order_submissions
    ADD COLUMN IF NOT EXISTS market_evidence_price numeric,
    ADD COLUMN IF NOT EXISTS risk_reference_price numeric,
    ADD COLUMN IF NOT EXISTS worst_case_order_price numeric,
    ADD COLUMN IF NOT EXISTS risk_notional numeric,
    ADD COLUMN IF NOT EXISTS reservation_notional numeric,
    ADD COLUMN IF NOT EXISTS price_deviation_bps numeric,
    ADD COLUMN IF NOT EXISTS price_source_sha256 text,
    ADD COLUMN IF NOT EXISTS price_calculator_version text;
UPDATE execution.paper_order_submissions SET
    market_evidence_price=COALESCE(market_evidence_price,reference_price),
    risk_reference_price=COALESCE(risk_reference_price,reference_price),
    worst_case_order_price=COALESCE(worst_case_order_price,submitted_notional/quantity),
    risk_notional=COALESCE(risk_notional,submitted_notional),
    reservation_notional=COALESCE(reservation_notional,submitted_notional),
    price_deviation_bps=COALESCE(price_deviation_bps,0),
    price_source_sha256=COALESCE(price_source_sha256,repeat('0',64)),
    price_calculator_version=COALESCE(price_calculator_version,'legacy-phase7-reference-price');
ALTER TABLE execution.paper_order_submissions
    ALTER COLUMN market_evidence_price SET NOT NULL,
    ALTER COLUMN risk_reference_price SET NOT NULL,
    ALTER COLUMN worst_case_order_price SET NOT NULL,
    ALTER COLUMN risk_notional SET NOT NULL,
    ALTER COLUMN reservation_notional SET NOT NULL,
    ALTER COLUMN price_deviation_bps SET NOT NULL,
    ALTER COLUMN price_source_sha256 SET NOT NULL,
    ALTER COLUMN price_calculator_version SET NOT NULL,
    ADD CONSTRAINT paper_submission_price_authority_check CHECK (
        market_evidence_price > 0 AND risk_reference_price > 0 AND worst_case_order_price > 0
        AND risk_notional > 0 AND submitted_notional=risk_notional
        AND reservation_notional > 0 AND price_deviation_bps >= 0
        AND price_source_sha256 ~ '^[0-9a-f]{64}$'
    );

ALTER TABLE execution.paper_runtime_risk_decisions
    ADD COLUMN IF NOT EXISTS market_evidence_price numeric,
    ADD COLUMN IF NOT EXISTS risk_reference_price numeric,
    ADD COLUMN IF NOT EXISTS worst_case_order_price numeric,
    ADD COLUMN IF NOT EXISTS risk_notional numeric,
    ADD COLUMN IF NOT EXISTS reservation_notional numeric,
    ADD COLUMN IF NOT EXISTS price_deviation_bps numeric,
    ADD COLUMN IF NOT EXISTS price_source_sha256 text,
    ADD COLUMN IF NOT EXISTS price_calculator_version text;
UPDATE execution.paper_runtime_risk_decisions d SET
    market_evidence_price=s.market_evidence_price,
    risk_reference_price=s.risk_reference_price,
    worst_case_order_price=s.worst_case_order_price,
    risk_notional=s.risk_notional,
    reservation_notional=s.reservation_notional,
    price_deviation_bps=s.price_deviation_bps,
    price_source_sha256=s.price_source_sha256,
    price_calculator_version=s.price_calculator_version
FROM execution.paper_order_submissions s WHERE s.submission_id=d.submission_id AND d.risk_notional IS NULL;
ALTER TABLE execution.paper_runtime_risk_decisions
    ALTER COLUMN market_evidence_price SET NOT NULL,
    ALTER COLUMN risk_reference_price SET NOT NULL,
    ALTER COLUMN worst_case_order_price SET NOT NULL,
    ALTER COLUMN risk_notional SET NOT NULL,
    ALTER COLUMN reservation_notional SET NOT NULL,
    ALTER COLUMN price_deviation_bps SET NOT NULL,
    ALTER COLUMN price_source_sha256 SET NOT NULL,
    ALTER COLUMN price_calculator_version SET NOT NULL;

ALTER TABLE execution.paper_reservations
    ADD COLUMN IF NOT EXISTS risk_notional numeric,
    ADD COLUMN IF NOT EXISTS reservation_notional numeric,
    ADD COLUMN IF NOT EXISTS price_source_sha256 text,
    ADD COLUMN IF NOT EXISTS price_calculator_version text;
UPDATE execution.paper_reservations r SET
    risk_notional=s.risk_notional,
    reservation_notional=s.reservation_notional,
    price_source_sha256=s.price_source_sha256,
    price_calculator_version=s.price_calculator_version
FROM execution.paper_order_submissions s WHERE s.submission_id=r.submission_id AND r.risk_notional IS NULL;
ALTER TABLE execution.paper_reservations
    ALTER COLUMN risk_notional SET NOT NULL,
    ALTER COLUMN reservation_notional SET NOT NULL,
    ALTER COLUMN price_source_sha256 SET NOT NULL,
    ALTER COLUMN price_calculator_version SET NOT NULL,
    ADD CONSTRAINT paper_reservation_price_authority_check CHECK (
        risk_notional > 0 AND reservation_notional > 0 AND price_source_sha256 ~ '^[0-9a-f]{64}$'
    );

ALTER TABLE execution.paper_order_projections
    ADD COLUMN IF NOT EXISTS terminal_disposition text,
    ADD COLUMN IF NOT EXISTS remaining_quantity numeric,
    ADD COLUMN IF NOT EXISTS terminal_observation_sequence bigint,
    ADD COLUMN IF NOT EXISTS latest_fill_sequence bigint;
UPDATE execution.paper_order_projections p SET
    terminal_disposition=COALESCE(terminal_disposition,CASE WHEN authority_state IN ('cancelled','expired','rejected','filled') THEN authority_state ELSE 'active' END),
    remaining_quantity=COALESCE(remaining_quantity,s.quantity-p.cumulative_filled_quantity),
    terminal_observation_sequence=COALESCE(terminal_observation_sequence,CASE WHEN p.terminal THEN p.venue_sequence END),
    latest_fill_sequence=COALESCE(latest_fill_sequence,(SELECT max(f.venue_sequence) FROM execution.paper_fills f WHERE f.submission_id=p.submission_id),0)
FROM execution.paper_order_submissions s WHERE s.submission_id=p.submission_id;
ALTER TABLE execution.paper_order_projections
    ALTER COLUMN terminal_disposition SET NOT NULL,
    ALTER COLUMN remaining_quantity SET NOT NULL,
    ALTER COLUMN latest_fill_sequence SET NOT NULL,
    ADD CONSTRAINT paper_projection_terminal_disposition_check CHECK (terminal_disposition IN ('active','cancelled','expired','rejected','filled')),
    ADD CONSTRAINT paper_projection_remaining_quantity_check CHECK (remaining_quantity >= 0),
    ADD CONSTRAINT paper_projection_terminal_sequence_check CHECK ((terminal_disposition='active')=(terminal_observation_sequence IS NULL));

ALTER TABLE execution.paper_cancel_outbox DROP CONSTRAINT IF EXISTS paper_cancel_outbox_state_check;
ALTER TABLE execution.paper_cancel_outbox ADD CONSTRAINT paper_cancel_outbox_state_check CHECK (state IN (
    'cancel_requested','cancel_claimed','cancel_confirmed','cancel_unknown',
    'cancel_superseded_by_fill','cancel_superseded_by_expiry','cancel_superseded_by_rejection'
));
ALTER TABLE execution.paper_dispatch_events DROP CONSTRAINT IF EXISTS paper_dispatch_events_event_type_check;
ALTER TABLE execution.paper_dispatch_events ADD CONSTRAINT paper_dispatch_events_event_type_check CHECK (event_type IN (
    'prepared','claimed','acknowledged','explicitly_rejected','unknown','recovered','cancel_requested','cancel_claimed','cancel_confirmed','cancel_unknown',
    'cancel_superseded_by_fill','cancel_superseded_by_expiry','cancel_superseded_by_rejection'
));

CREATE TABLE IF NOT EXISTS execution.paper_expiry_recovery_records (
    expiry_recovery_id uuid PRIMARY KEY,
    expiry_id uuid NOT NULL REFERENCES execution.paper_expiry_outbox(expiry_id) ON DELETE CASCADE,
    paper_run_id uuid NOT NULL REFERENCES execution.paper_runs(paper_run_id) ON DELETE CASCADE,
    submission_id uuid NOT NULL REFERENCES execution.paper_order_submissions(submission_id),
    recovery_generation integer NOT NULL CHECK (recovery_generation > 0),
    recovery_claim_token uuid NOT NULL,
    worker_id text NOT NULL,
    claimed_at_utc timestamptz NOT NULL,
    lease_expires_at_utc timestamptz NOT NULL,
    completed_at_utc timestamptz,
    outcome text CHECK (outcome IS NULL OR outcome IN ('expiry_confirmed','expiry_unknown','superseded_by_fill','superseded_by_cancel','superseded_by_rejection')),
    query_evidence_sha256 text CHECK (query_evidence_sha256 IS NULL OR query_evidence_sha256 ~ '^[0-9a-f]{64}$'),
    record_sha256 text NOT NULL CHECK (record_sha256 ~ '^[0-9a-f]{64}$'),
    UNIQUE (expiry_id,recovery_generation),
    UNIQUE (recovery_claim_token),
    CHECK (lease_expires_at_utc > claimed_at_utc),
    CHECK ((completed_at_utc IS NULL AND outcome IS NULL) OR (completed_at_utc IS NOT NULL AND outcome IS NOT NULL))
);

CREATE TABLE IF NOT EXISTS execution.paper_internal_venue_economics (
    paper_run_id uuid PRIMARY KEY REFERENCES execution.paper_runs(paper_run_id) ON DELETE CASCADE,
    fee_bps numeric NOT NULL CHECK (fee_bps >= 0),
    maximum_adverse_slippage_bps numeric NOT NULL CHECK (maximum_adverse_slippage_bps >= 0),
    reservation_calculator_version text NOT NULL,
    fee_currency_policy text NOT NULL,
    fill_price_policy text NOT NULL,
    internal_venue_implementation_sha256 text NOT NULL CHECK (internal_venue_implementation_sha256 ~ '^[0-9a-f]{64}$'),
    created_at_utc timestamptz NOT NULL,
    record_sha256 text NOT NULL CHECK (record_sha256 ~ '^[0-9a-f]{64}$')
);

CREATE OR REPLACE FUNCTION execution.phase7_guard_submission_update()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF ROW(NEW.paper_run_id,NEW.manifest_id,NEW.approval_id,NEW.order_intent_id,NEW.client_order_id,
           NEW.idempotency_key,NEW.series_identity_sha256,NEW.instrument_id,NEW.side,NEW.order_type,
           NEW.time_in_force,NEW.accounting_mode,NEW.quantity,NEW.reference_price,NEW.submitted_notional,
           NEW.limit_price,NEW.stop_price,NEW.submitted_at_utc,NEW.economics_sha256,NEW.pre_submit_risk_sha256,
           NEW.market_evidence_price,NEW.risk_reference_price,NEW.worst_case_order_price,NEW.risk_notional,
           NEW.reservation_notional,NEW.price_deviation_bps,NEW.price_source_sha256,NEW.price_calculator_version)
       IS DISTINCT FROM
       ROW(OLD.paper_run_id,OLD.manifest_id,OLD.approval_id,OLD.order_intent_id,OLD.client_order_id,
           OLD.idempotency_key,OLD.series_identity_sha256,OLD.instrument_id,OLD.side,OLD.order_type,
           OLD.time_in_force,OLD.accounting_mode,OLD.quantity,OLD.reference_price,OLD.submitted_notional,
           OLD.limit_price,OLD.stop_price,OLD.submitted_at_utc,OLD.economics_sha256,OLD.pre_submit_risk_sha256,
           OLD.market_evidence_price,OLD.risk_reference_price,OLD.worst_case_order_price,OLD.risk_notional,
           OLD.reservation_notional,OLD.price_deviation_bps,OLD.price_source_sha256,OLD.price_calculator_version) THEN
        RAISE EXCEPTION 'paper submission economics, price authority, and control bindings are immutable';
    END IF;
    IF OLD.counted_open=false AND OLD.open_closed_at_utc IS NOT NULL
       AND (NEW.counted_open OR NEW.open_closed_at_utc IS NULL OR NEW.open_close_cause_id IS DISTINCT FROM OLD.open_close_cause_id) THEN
        RAISE EXCEPTION 'closed paper order budget cannot reopen';
    END IF;
    RETURN NEW;
END;
$$;

CREATE OR REPLACE FUNCTION execution.phase7_guard_order_projection_update()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF ROW(NEW.submission_id,NEW.paper_run_id,NEW.client_order_id,NEW.venue_order_id,NEW.economics_sha256)
       IS DISTINCT FROM ROW(OLD.submission_id,OLD.paper_run_id,OLD.client_order_id,OLD.venue_order_id,OLD.economics_sha256) THEN
        RAISE EXCEPTION 'paper order projection identity is immutable';
    END IF;
    IF NEW.venue_sequence < OLD.venue_sequence OR NEW.cumulative_filled_quantity < OLD.cumulative_filled_quantity
       OR NEW.latest_fill_sequence < OLD.latest_fill_sequence THEN
        RAISE EXCEPTION 'paper order projection cannot regress';
    END IF;
    IF OLD.terminal_disposition <> 'active' AND (
        NEW.terminal_disposition <> OLD.terminal_disposition OR NOT NEW.terminal
        OR NEW.terminal_observation_sequence IS DISTINCT FROM OLD.terminal_observation_sequence
    ) THEN RAISE EXCEPTION 'terminal paper order disposition cannot regress or change'; END IF;
    RETURN NEW;
END;
$$;

CREATE OR REPLACE FUNCTION execution.phase7_guard_cancel_recovery_update()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF ROW(NEW.dispatch_id,NEW.paper_run_id,NEW.submission_id,NEW.client_order_id,NEW.requested_at_utc)
       IS DISTINCT FROM ROW(OLD.dispatch_id,OLD.paper_run_id,OLD.submission_id,OLD.client_order_id,OLD.requested_at_utc) THEN
        RAISE EXCEPTION 'paper cancellation identity is immutable';
    END IF;
    IF NEW.recovery_generation < OLD.recovery_generation THEN RAISE EXCEPTION 'paper cancellation recovery generation cannot decrease'; END IF;
    IF OLD.state <> NEW.state AND NOT (
        (OLD.state='cancel_requested' AND NEW.state='cancel_claimed') OR
        (OLD.state='cancel_claimed' AND NEW.state IN ('cancel_confirmed','cancel_unknown','cancel_superseded_by_fill','cancel_superseded_by_expiry','cancel_superseded_by_rejection')) OR
        (OLD.state='cancel_unknown' AND NEW.state IN ('cancel_unknown','cancel_confirmed','cancel_superseded_by_fill','cancel_superseded_by_expiry','cancel_superseded_by_rejection'))
    ) THEN RAISE EXCEPTION 'invalid paper cancel transition % -> %',OLD.state,NEW.state; END IF;
    RETURN NEW;
END;
$$;
CREATE OR REPLACE FUNCTION execution.phase7_guard_expiry_update()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF ROW(NEW.expiry_id,NEW.paper_run_id,NEW.submission_id,NEW.client_order_id,NEW.requested_at_utc)
       IS DISTINCT FROM ROW(OLD.expiry_id,OLD.paper_run_id,OLD.submission_id,OLD.client_order_id,OLD.requested_at_utc) THEN
        RAISE EXCEPTION 'paper expiry identity is immutable';
    END IF;
    IF NEW.recovery_generation < OLD.recovery_generation THEN RAISE EXCEPTION 'paper expiry recovery generation cannot decrease'; END IF;
    IF OLD.state <> NEW.state AND NOT (
        (OLD.state='expiry_requested' AND NEW.state='expiry_claimed') OR
        (OLD.state='expiry_claimed' AND NEW.state IN ('expiry_confirmed','expiry_unknown')) OR
        (OLD.state='expiry_unknown' AND NEW.state IN ('expiry_unknown','expiry_confirmed'))
    ) THEN RAISE EXCEPTION 'invalid paper expiry transition % -> %',OLD.state,NEW.state; END IF;
    RETURN NEW;
END;
$$;

CREATE OR REPLACE FUNCTION execution.phase7_guard_reservation_update()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF ROW(NEW.paper_run_id,NEW.submission_id,NEW.client_order_id,NEW.currency,NEW.original_amount,NEW.original_quantity,NEW.created_at_utc,NEW.economics_sha256,NEW.reserve_price,NEW.maximum_fee_bps,NEW.maximum_adverse_slippage_bps,NEW.calculator_version,NEW.risk_notional,NEW.reservation_notional,NEW.price_source_sha256,NEW.price_calculator_version)
       IS DISTINCT FROM ROW(OLD.paper_run_id,OLD.submission_id,OLD.client_order_id,OLD.currency,OLD.original_amount,OLD.original_quantity,OLD.created_at_utc,OLD.economics_sha256,OLD.reserve_price,OLD.maximum_fee_bps,OLD.maximum_adverse_slippage_bps,OLD.calculator_version,OLD.risk_notional,OLD.reservation_notional,OLD.price_source_sha256,OLD.price_calculator_version) THEN
        RAISE EXCEPTION 'paper reservation economic and price authority is immutable';
    END IF;
    IF NEW.remaining_amount > OLD.remaining_amount OR NEW.remaining_quantity > OLD.remaining_quantity OR NEW.spent_amount < OLD.spent_amount THEN
        RAISE EXCEPTION 'paper reservation cannot regress';
    END IF;
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS phase7_internal_venue_economics_immutable ON execution.paper_internal_venue_economics;
CREATE TRIGGER phase7_internal_venue_economics_immutable BEFORE UPDATE OR DELETE ON execution.paper_internal_venue_economics FOR EACH ROW EXECUTE FUNCTION execution.phase7_reject_immutable_change();
CREATE OR REPLACE FUNCTION execution.phase7_guard_expiry_recovery_update()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF ROW(NEW.expiry_recovery_id,NEW.expiry_id,NEW.paper_run_id,NEW.submission_id,NEW.recovery_generation,NEW.recovery_claim_token,NEW.worker_id,NEW.claimed_at_utc,NEW.lease_expires_at_utc)
       IS DISTINCT FROM ROW(OLD.expiry_recovery_id,OLD.expiry_id,OLD.paper_run_id,OLD.submission_id,OLD.recovery_generation,OLD.recovery_claim_token,OLD.worker_id,OLD.claimed_at_utc,OLD.lease_expires_at_utc) THEN
        RAISE EXCEPTION 'paper expiry recovery claim identity is immutable';
    END IF;
    IF OLD.completed_at_utc IS NOT NULL OR NEW.completed_at_utc IS NULL OR NEW.outcome IS NULL THEN
        RAISE EXCEPTION 'paper expiry recovery may be completed exactly once';
    END IF;
    RETURN NEW;
END;
$$;
DROP TRIGGER IF EXISTS phase7_expiry_recovery_update_guard ON execution.paper_expiry_recovery_records;
CREATE TRIGGER phase7_expiry_recovery_update_guard BEFORE UPDATE ON execution.paper_expiry_recovery_records FOR EACH ROW EXECUTE FUNCTION execution.phase7_guard_expiry_recovery_update();
DROP TRIGGER IF EXISTS phase7_expiry_recovery_delete_immutable ON execution.paper_expiry_recovery_records;
CREATE TRIGGER phase7_expiry_recovery_delete_immutable BEFORE DELETE ON execution.paper_expiry_recovery_records FOR EACH ROW EXECUTE FUNCTION execution.phase7_reject_immutable_change();
DROP TRIGGER IF EXISTS phase7_submission_update_guard ON execution.paper_order_submissions;
CREATE TRIGGER phase7_submission_update_guard BEFORE UPDATE ON execution.paper_order_submissions FOR EACH ROW EXECUTE FUNCTION execution.phase7_guard_submission_update();
DROP TRIGGER IF EXISTS phase7_order_projection_guard ON execution.paper_order_projections;
CREATE TRIGGER phase7_order_projection_guard BEFORE UPDATE ON execution.paper_order_projections FOR EACH ROW EXECUTE FUNCTION execution.phase7_guard_order_projection_update();
DROP TRIGGER IF EXISTS phase7_reservation_update_guard ON execution.paper_reservations;
CREATE TRIGGER phase7_reservation_update_guard BEFORE UPDATE ON execution.paper_reservations FOR EACH ROW EXECUTE FUNCTION execution.phase7_guard_reservation_update();
DROP TRIGGER IF EXISTS phase7_expiry_guard ON execution.paper_expiry_outbox;
CREATE TRIGGER phase7_expiry_guard BEFORE UPDATE ON execution.paper_expiry_outbox FOR EACH ROW EXECUTE FUNCTION execution.phase7_guard_expiry_update();

CREATE INDEX IF NOT EXISTS idx_phase7_price_source_identity ON execution.paper_market_data_evidence(source_table,source_row_id,validation_report_id);
CREATE INDEX IF NOT EXISTS idx_phase7_pending_fill_recovery ON execution.paper_order_projections(paper_run_id,updated_at_utc,submission_id) WHERE NOT fill_application_complete;
CREATE INDEX IF NOT EXISTS idx_phase7_expiry_recovery_lease ON execution.paper_expiry_outbox(paper_run_id,recovery_lease_expires_at_utc,expiry_id) WHERE state IN ('expiry_claimed','expiry_unknown');

COMMENT ON TABLE execution.paper_internal_venue_economics IS 'Immutable exact InternalPaperVenue economics used for restart and deterministic replay.';
COMMENT ON TABLE execution.paper_expiry_recovery_records IS 'Generation- and token-owned query-first expiry recovery evidence.';
COMMENT ON COLUMN execution.paper_order_submissions.risk_notional IS 'Single persisted authoritative notional used by all Phase 7 risk limits and audit reporting.';
