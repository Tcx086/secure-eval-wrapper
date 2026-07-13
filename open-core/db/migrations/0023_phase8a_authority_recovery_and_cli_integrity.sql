-- Phase 8A independent-audit repairs.
-- Migrations 0001 through 0022 are immutable. PostgreSQL remains the only authority.
-- Production order and cancellation transport remains unconditionally unreachable.

CREATE TABLE IF NOT EXISTS execution.live_preflight_sources (
    source_id uuid PRIMARY KEY,
    live_run_id uuid NOT NULL,
    source_kind text NOT NULL CHECK (source_kind IN (
        'repository','migration_catalog','postgresql_probe','audit_rollback_probe',
        'credential_reference','credential_permissions','account_config','account_fingerprint',
        'subaccount','account_mode','margin_borrowing','balances','positions','open_orders',
        'venue_time','market_data','instrument_metadata','reconciliation','kill_switch'
    )),
    collected_at_utc timestamptz NOT NULL,
    source_payload_jsonb jsonb NOT NULL,
    source_sha256 text NOT NULL CHECK (source_sha256 ~ '^[0-9a-f]{64}$'),
    operational boolean NOT NULL,
    record_sha256 text NOT NULL CHECK (record_sha256 ~ '^[0-9a-f]{64}$'),
    UNIQUE (source_id, live_run_id),
    UNIQUE (live_run_id, source_kind, source_sha256)
);

CREATE TABLE IF NOT EXISTS execution.live_preflight_check_sources (
    preflight_check_id uuid NOT NULL,
    source_ordinal integer NOT NULL CHECK (source_ordinal >= 0),
    source_id uuid NOT NULL,
    live_run_id uuid NOT NULL,
    source_sha256 text NOT NULL CHECK (source_sha256 ~ '^[0-9a-f]{64}$'),
    PRIMARY KEY (preflight_check_id, source_id)
);
ALTER TABLE execution.live_preflight_checks
    ADD COLUMN IF NOT EXISTS check_ordinal integer;
WITH ranked_checks AS (
    SELECT preflight_check_id,
           row_number() OVER (PARTITION BY preflight_report_id ORDER BY check_name, preflight_check_id) - 1 AS check_ordinal
    FROM execution.live_preflight_checks
)
UPDATE execution.live_preflight_checks c
SET check_ordinal = r.check_ordinal
FROM ranked_checks r
WHERE c.preflight_check_id = r.preflight_check_id AND c.check_ordinal IS NULL;
ALTER TABLE execution.live_preflight_checks ALTER COLUMN check_ordinal SET NOT NULL;
ALTER TABLE execution.live_preflight_checks ADD CONSTRAINT ck_live_preflight_check_ordinal CHECK (check_ordinal >= 0);
ALTER TABLE execution.live_preflight_checks ADD CONSTRAINT uq_live_preflight_check_ordinal UNIQUE (preflight_report_id, check_ordinal);


-- Add exact authority identities to the original Phase 8A report and manifest rows.
ALTER TABLE execution.live_preflight_reports
    ADD COLUMN IF NOT EXISTS credential_reference_id uuid,
    ADD COLUMN IF NOT EXISTS account_snapshot_id uuid;
UPDATE execution.live_preflight_reports r
SET credential_reference_id = c.credential_reference_id
FROM execution.live_credential_references c
WHERE r.credential_reference_id IS NULL
  AND c.record_sha256 = r.credential_reference_sha256;
UPDATE execution.live_preflight_reports r
SET account_snapshot_id = a.account_snapshot_id
FROM execution.live_account_snapshots a
WHERE r.account_snapshot_id IS NULL
  AND a.live_run_id = r.live_run_id
  AND a.record_sha256 = r.account_snapshot_sha256;

ALTER TABLE execution.live_run_manifests
    ADD COLUMN IF NOT EXISTS credential_reference_id uuid;
UPDATE execution.live_run_manifests m
SET credential_reference_id = c.credential_reference_id
FROM execution.live_credential_references c
WHERE m.credential_reference_id IS NULL
  AND c.record_sha256 = m.credential_reference_sha256;

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM execution.live_preflight_reports WHERE credential_reference_id IS NULL OR account_snapshot_id IS NULL) THEN
        RAISE EXCEPTION '0023 cannot bind legacy preflight authority rows';
    END IF;
    IF EXISTS (SELECT 1 FROM execution.live_run_manifests WHERE credential_reference_id IS NULL) THEN
        RAISE EXCEPTION '0023 cannot bind legacy manifest credential rows';
    END IF;
END;
$$;

ALTER TABLE execution.live_preflight_reports
    ALTER COLUMN credential_reference_id SET NOT NULL,
    ALTER COLUMN account_snapshot_id SET NOT NULL;
ALTER TABLE execution.live_run_manifests
    ALTER COLUMN credential_reference_id SET NOT NULL;

-- Every critical identity can now be referenced together with its run membership.
ALTER TABLE execution.live_account_snapshots ADD CONSTRAINT uq_live_account_snapshot_run UNIQUE (account_snapshot_id, live_run_id);
ALTER TABLE execution.live_preflight_reports ADD CONSTRAINT uq_live_preflight_report_run UNIQUE (preflight_report_id, live_run_id);
ALTER TABLE execution.live_approvals ADD CONSTRAINT uq_live_approval_run UNIQUE (approval_id, live_run_id);
ALTER TABLE execution.live_run_manifests ADD CONSTRAINT uq_live_manifest_run UNIQUE (manifest_id, live_run_id);
ALTER TABLE execution.live_order_intents ADD CONSTRAINT uq_live_order_intent_run UNIQUE (order_intent_id, live_run_id);
ALTER TABLE execution.live_reconciliations ADD CONSTRAINT uq_live_reconciliation_run UNIQUE (reconciliation_id, live_run_id);
ALTER TABLE execution.live_credential_references ADD CONSTRAINT uq_live_credential_record UNIQUE (record_sha256);

ALTER TABLE execution.live_preflight_checks ADD COLUMN IF NOT EXISTS live_run_id uuid;
UPDATE execution.live_preflight_checks c SET live_run_id=r.live_run_id
FROM execution.live_preflight_reports r WHERE c.preflight_report_id=r.preflight_report_id AND c.live_run_id IS NULL;
ALTER TABLE execution.live_preflight_checks ALTER COLUMN live_run_id SET NOT NULL;
ALTER TABLE execution.live_preflight_checks ADD CONSTRAINT uq_live_preflight_check_run UNIQUE (preflight_check_id, live_run_id);

ALTER TABLE execution.live_runtime_risk_decisions ADD COLUMN IF NOT EXISTS live_run_id uuid;
UPDATE execution.live_runtime_risk_decisions d SET live_run_id=i.live_run_id
FROM execution.live_order_intents i WHERE d.order_intent_id=i.order_intent_id AND d.live_run_id IS NULL;
ALTER TABLE execution.live_runtime_risk_decisions ALTER COLUMN live_run_id SET NOT NULL;
ALTER TABLE execution.live_runtime_risk_decisions ADD CONSTRAINT uq_live_risk_decision_run UNIQUE (risk_decision_id, live_run_id);

ALTER TABLE execution.live_reservations ADD COLUMN IF NOT EXISTS live_run_id uuid;
UPDATE execution.live_reservations r SET live_run_id=i.live_run_id,version=r.version+1
FROM execution.live_order_intents i WHERE r.order_intent_id=i.order_intent_id AND r.live_run_id IS NULL;
ALTER TABLE execution.live_reservations ALTER COLUMN live_run_id SET NOT NULL;
ALTER TABLE execution.live_reservations ADD CONSTRAINT uq_live_reservation_run UNIQUE (reservation_id, live_run_id);

ALTER TABLE execution.live_dispatch_outbox ADD COLUMN IF NOT EXISTS live_run_id uuid;
UPDATE execution.live_dispatch_outbox d SET live_run_id=i.live_run_id,version=d.version+1
FROM execution.live_order_intents i WHERE d.order_intent_id=i.order_intent_id AND d.live_run_id IS NULL;
ALTER TABLE execution.live_dispatch_outbox ALTER COLUMN live_run_id SET NOT NULL;
ALTER TABLE execution.live_dispatch_outbox ADD CONSTRAINT uq_live_dispatch_run UNIQUE (dispatch_outbox_id, live_run_id);

ALTER TABLE execution.live_dispatch_events ADD COLUMN IF NOT EXISTS live_run_id uuid;
UPDATE execution.live_dispatch_events e SET live_run_id=d.live_run_id
FROM execution.live_dispatch_outbox d WHERE e.dispatch_outbox_id=d.dispatch_outbox_id AND e.live_run_id IS NULL;
ALTER TABLE execution.live_dispatch_events ALTER COLUMN live_run_id SET NOT NULL;

ALTER TABLE execution.live_reconciliation_differences ADD COLUMN IF NOT EXISTS live_run_id uuid;
UPDATE execution.live_reconciliation_differences d SET live_run_id=r.live_run_id
FROM execution.live_reconciliations r WHERE d.reconciliation_id=r.reconciliation_id AND d.live_run_id IS NULL;
ALTER TABLE execution.live_reconciliation_differences ALTER COLUMN live_run_id SET NOT NULL;

ALTER TABLE execution.live_kill_events ADD COLUMN IF NOT EXISTS live_run_id uuid;
UPDATE execution.live_kill_events e SET live_run_id=k.live_run_id
FROM execution.live_kill_switches k WHERE e.kill_switch_id=k.kill_switch_id AND e.live_run_id IS NULL;
ALTER TABLE execution.live_kill_events ALTER COLUMN live_run_id SET NOT NULL;

-- Composite foreign keys make cross-run construction fail even through direct SQL.
ALTER TABLE execution.live_preflight_reports
    ADD CONSTRAINT fk_live_report_account_run FOREIGN KEY (account_snapshot_id, live_run_id)
        REFERENCES execution.live_account_snapshots(account_snapshot_id, live_run_id) ON DELETE RESTRICT,
    ADD CONSTRAINT fk_live_report_credential FOREIGN KEY (credential_reference_id)
        REFERENCES execution.live_credential_references(credential_reference_id) ON DELETE RESTRICT,
    ADD CONSTRAINT fk_live_report_configuration FOREIGN KEY (configuration_sha256)
        REFERENCES execution.live_configuration_snapshots(configuration_sha256) ON DELETE RESTRICT;
ALTER TABLE execution.live_preflight_checks
    ADD CONSTRAINT fk_live_check_report_run FOREIGN KEY (preflight_report_id, live_run_id)
        REFERENCES execution.live_preflight_reports(preflight_report_id, live_run_id) ON DELETE RESTRICT;
ALTER TABLE execution.live_approvals
    ADD CONSTRAINT fk_live_approval_report_run FOREIGN KEY (preflight_report_id, live_run_id)
        REFERENCES execution.live_preflight_reports(preflight_report_id, live_run_id) ON DELETE RESTRICT;
ALTER TABLE execution.live_run_manifests
    ADD CONSTRAINT fk_live_manifest_report_run FOREIGN KEY (preflight_report_id, live_run_id)
        REFERENCES execution.live_preflight_reports(preflight_report_id, live_run_id) ON DELETE RESTRICT,
    ADD CONSTRAINT fk_live_manifest_approval_run FOREIGN KEY (approval_id, live_run_id)
        REFERENCES execution.live_approvals(approval_id, live_run_id) ON DELETE RESTRICT,
    ADD CONSTRAINT fk_live_manifest_account_run FOREIGN KEY (initial_account_snapshot_id, live_run_id)
        REFERENCES execution.live_account_snapshots(account_snapshot_id, live_run_id) ON DELETE RESTRICT,
    ADD CONSTRAINT fk_live_manifest_credential FOREIGN KEY (credential_reference_id)
        REFERENCES execution.live_credential_references(credential_reference_id) ON DELETE RESTRICT;
ALTER TABLE execution.live_runs
    ADD CONSTRAINT fk_live_run_manifest_run FOREIGN KEY (manifest_id, live_run_id)
        REFERENCES execution.live_run_manifests(manifest_id, live_run_id) ON DELETE RESTRICT;
ALTER TABLE execution.live_order_intents
    ADD CONSTRAINT fk_live_intent_manifest_run FOREIGN KEY (manifest_id, live_run_id)
        REFERENCES execution.live_run_manifests(manifest_id, live_run_id) ON DELETE RESTRICT;
ALTER TABLE execution.live_runtime_risk_decisions
    ADD CONSTRAINT fk_live_risk_intent_run FOREIGN KEY (order_intent_id, live_run_id)
        REFERENCES execution.live_order_intents(order_intent_id, live_run_id) ON DELETE RESTRICT;
ALTER TABLE execution.live_reservations
    ADD CONSTRAINT fk_live_reservation_intent_run FOREIGN KEY (order_intent_id, live_run_id)
        REFERENCES execution.live_order_intents(order_intent_id, live_run_id) ON DELETE RESTRICT;
ALTER TABLE execution.live_dispatch_outbox
    ADD CONSTRAINT fk_live_dispatch_intent_run FOREIGN KEY (order_intent_id, live_run_id)
        REFERENCES execution.live_order_intents(order_intent_id, live_run_id) ON DELETE RESTRICT;
ALTER TABLE execution.live_dispatch_events
    ADD CONSTRAINT fk_live_dispatch_event_run FOREIGN KEY (dispatch_outbox_id, live_run_id)
        REFERENCES execution.live_dispatch_outbox(dispatch_outbox_id, live_run_id) ON DELETE RESTRICT;
ALTER TABLE execution.live_cancel_outbox
    ADD CONSTRAINT fk_live_cancel_intent_run FOREIGN KEY (order_intent_id, live_run_id)
        REFERENCES execution.live_order_intents(order_intent_id, live_run_id) ON DELETE RESTRICT;
ALTER TABLE execution.live_reconciliation_differences
    ADD CONSTRAINT fk_live_difference_reconciliation_run FOREIGN KEY (reconciliation_id, live_run_id)
        REFERENCES execution.live_reconciliations(reconciliation_id, live_run_id) ON DELETE RESTRICT;
ALTER TABLE execution.live_preflight_check_sources
    ADD CONSTRAINT fk_live_check_source_check_run FOREIGN KEY (preflight_check_id, live_run_id)
        REFERENCES execution.live_preflight_checks(preflight_check_id, live_run_id) ON DELETE RESTRICT,
    ADD CONSTRAINT fk_live_check_source_source_run FOREIGN KEY (source_id, live_run_id)
        REFERENCES execution.live_preflight_sources(source_id, live_run_id) ON DELETE RESTRICT;

-- PostgreSQL-owned runtime risk and account projection. One row is locked for every intent.
CREATE TABLE IF NOT EXISTS execution.live_run_risk_state (
    live_run_id uuid PRIMARY KEY REFERENCES execution.live_runs(live_run_id) ON DELETE RESTRICT,
    trading_day date NOT NULL,
    current_equity numeric NOT NULL CHECK (current_equity >= 0),
    high_watermark_equity numeric NOT NULL CHECK (high_watermark_equity >= current_equity),
    daily_submitted_notional numeric NOT NULL DEFAULT 0 CHECK (daily_submitted_notional >= 0),
    daily_realized_pnl numeric NOT NULL DEFAULT 0,
    gross_exposure numeric NOT NULL DEFAULT 0 CHECK (gross_exposure >= 0),
    net_exposure numeric NOT NULL DEFAULT 0,
    order_rate_window_jsonb jsonb NOT NULL DEFAULT '[]'::jsonb,
    cancellation_rate_window_jsonb jsonb NOT NULL DEFAULT '[]'::jsonb,
    open_order_count integer NOT NULL DEFAULT 0 CHECK (open_order_count >= 0),
    oldest_unknown_order_at_utc timestamptz,
    oldest_unacknowledged_order_at_utc timestamptz,
    latest_market_data_at_utc timestamptz NOT NULL,
    latest_account_snapshot_at_utc timestamptz NOT NULL,
    latest_reconciliation_at_utc timestamptz NOT NULL,
    latest_reconciliation_status text NOT NULL CHECK (latest_reconciliation_status IN ('reconciled','blocked','unknown')),
    clock_skew_seconds numeric NOT NULL CHECK (clock_skew_seconds >= 0),
    run_started_at_utc timestamptz NOT NULL,
    transport_failure_count integer NOT NULL DEFAULT 0 CHECK (transport_failure_count >= 0),
    balances_jsonb jsonb NOT NULL,
    positions_jsonb jsonb NOT NULL,
    latest_account_snapshot_id uuid NOT NULL,
    latest_reconciliation_id uuid,
    latest_market_evidence_id uuid NOT NULL,
    latest_market_evidence_sha256 text NOT NULL CHECK (latest_market_evidence_sha256 ~ '^[0-9a-f]{64}$'),
    updated_at_utc timestamptz NOT NULL,
    version integer NOT NULL DEFAULT 0 CHECK (version >= 0),
    record_sha256 text NOT NULL CHECK (record_sha256 ~ '^[0-9a-f]{64}$'),
    CONSTRAINT fk_live_risk_state_snapshot_run FOREIGN KEY (latest_account_snapshot_id, live_run_id)
        REFERENCES execution.live_account_snapshots(account_snapshot_id, live_run_id) ON DELETE RESTRICT
);

-- Typed reservation authority. Existing 0022 rows are upgraded conservatively.
ALTER TABLE execution.live_reservations
    ADD COLUMN IF NOT EXISTS original_amount numeric,
    ADD COLUMN IF NOT EXISTS remaining_amount numeric,
    ADD COLUMN IF NOT EXISTS original_quantity numeric,
    ADD COLUMN IF NOT EXISTS remaining_quantity numeric,
    ADD COLUMN IF NOT EXISTS worst_case_price numeric,
    ADD COLUMN IF NOT EXISTS maximum_fee_bps numeric,
    ADD COLUMN IF NOT EXISTS maximum_fee_amount numeric,
    ADD COLUMN IF NOT EXISTS fee_currency_policy text,
    ADD COLUMN IF NOT EXISTS reservation_notional numeric,
    ADD COLUMN IF NOT EXISTS calculator_version text,
    ADD COLUMN IF NOT EXISTS source_hashes_jsonb jsonb;
UPDATE execution.live_reservations r
SET original_amount=amount,
    remaining_amount=amount,
    original_quantity=i.quantity,
    remaining_quantity=i.quantity,
    worst_case_price=d.worst_case_order_price,
    maximum_fee_bps=0,
    maximum_fee_amount=0,
    fee_currency_policy='legacy_0022_no_fee',
    reservation_notional=d.reservation_notional,
    calculator_version=d.calculator_version,
    source_hashes_jsonb=jsonb_build_object('intent', i.record_sha256, 'risk', d.record_sha256),
    version=r.version+1
FROM execution.live_order_intents i
JOIN execution.live_runtime_risk_decisions d ON d.order_intent_id=i.order_intent_id
WHERE r.order_intent_id=i.order_intent_id AND r.original_amount IS NULL;
ALTER TABLE execution.live_reservations
    ALTER COLUMN original_amount SET NOT NULL,
    ALTER COLUMN remaining_amount SET NOT NULL,
    ALTER COLUMN original_quantity SET NOT NULL,
    ALTER COLUMN remaining_quantity SET NOT NULL,
    ALTER COLUMN worst_case_price SET NOT NULL,
    ALTER COLUMN maximum_fee_bps SET NOT NULL,
    ALTER COLUMN maximum_fee_amount SET NOT NULL,
    ALTER COLUMN fee_currency_policy SET NOT NULL,
    ALTER COLUMN reservation_notional SET NOT NULL,
    ALTER COLUMN calculator_version SET NOT NULL,
    ALTER COLUMN source_hashes_jsonb SET NOT NULL,
    ADD CONSTRAINT ck_live_reservation_amounts CHECK (
        original_amount > 0 AND remaining_amount >= 0 AND remaining_amount <= original_amount
        AND original_quantity > 0 AND remaining_quantity >= 0 AND remaining_quantity <= original_quantity
        AND worst_case_price > 0 AND maximum_fee_bps >= 0 AND maximum_fee_amount >= 0
        AND reservation_notional > 0
    );

ALTER TABLE execution.live_kill_switches
    ADD COLUMN IF NOT EXISTS triggered_at_utc timestamptz,
    ADD COLUMN IF NOT EXISTS reset_preflight_report_id uuid,
    ADD COLUMN IF NOT EXISTS reset_approval_id uuid;

ALTER TABLE execution.live_recovery_records
    ADD COLUMN IF NOT EXISTS outcome text,
    ADD COLUMN IF NOT EXISTS manual_intervention_required boolean NOT NULL DEFAULT false;
ALTER TABLE execution.live_recovery_records
    ADD CONSTRAINT ck_live_recovery_outcome CHECK (outcome IS NULL OR outcome IN (
        'confirmed_absent','observed_external_order','observed_external_fill','inconclusive','provider_rejected'
    ));

-- Incident states are explicit; observed venue effects can never be called suppressed.
ALTER TABLE execution.live_order_intents DROP CONSTRAINT IF EXISTS live_order_intents_state_check;
ALTER TABLE execution.live_order_intents ADD CONSTRAINT live_order_intents_state_check CHECK (state IN (
    'dry_run_prepared','dry_run_blocked','dry_run_suppressed','pending_recovery',
    'unexpected_external_side_effect','incident_blocked'
));
ALTER TABLE execution.live_dispatch_outbox DROP CONSTRAINT IF EXISTS live_dispatch_outbox_state_check;
ALTER TABLE execution.live_dispatch_outbox ADD CONSTRAINT live_dispatch_outbox_state_check CHECK (state IN (
    'dry_run_prepared','dry_run_suppressed','pending_recovery','unexpected_external_side_effect'
));
ALTER TABLE execution.live_order_projections DROP CONSTRAINT IF EXISTS live_order_projections_state_check;
ALTER TABLE execution.live_order_projections ADD CONSTRAINT live_order_projections_state_check CHECK (state IN (
    'dry_run_prepared','dry_run_blocked','dry_run_suppressed','pending_recovery','incident_blocked'
));
ALTER TABLE execution.live_dispatch_events DROP CONSTRAINT IF EXISTS live_dispatch_events_event_type_check;
ALTER TABLE execution.live_dispatch_events ADD CONSTRAINT live_dispatch_events_event_type_check CHECK (event_type IN (
    'prepared','claimed','write_suppressed','recovery_claimed','observation_persisted','unexpected_external_side_effect'
));

-- Passed operational preflight is only possible with a persisted, validated credential reference.
CREATE OR REPLACE FUNCTION execution.guard_live_preflight_authority()
RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
    credential execution.live_credential_references%ROWTYPE;
    unsafe_count integer;
BEGIN
    SELECT * INTO credential FROM execution.live_credential_references
    WHERE credential_reference_id=NEW.credential_reference_id;
    IF NOT FOUND OR credential.record_sha256 <> NEW.credential_reference_sha256 THEN
        RAISE EXCEPTION 'preflight credential reference/hash mismatch';
    END IF;
    IF NEW.status='passed' THEN
        IF credential.verified_at_utc IS NULL OR jsonb_typeof(credential.permission_summary_jsonb) <> 'array' THEN
            RAISE EXCEPTION 'passed preflight requires verified credential permissions';
        END IF;
        SELECT count(*) INTO unsafe_count
        FROM jsonb_array_elements_text(credential.permission_summary_jsonb) p(value)
        WHERE p.value NOT IN ('read','spot_trade');
        IF unsafe_count > 0
           OR NOT credential.permission_summary_jsonb ? 'read' THEN
            RAISE EXCEPTION 'unsafe credential permissions cannot produce a passed preflight';
        END IF;
    END IF;
    RETURN NEW;
END;
$$;
DROP TRIGGER IF EXISTS trg_guard_live_preflight_authority ON execution.live_preflight_reports;
CREATE TRIGGER trg_guard_live_preflight_authority BEFORE INSERT OR UPDATE ON execution.live_preflight_reports
FOR EACH ROW EXECUTE FUNCTION execution.guard_live_preflight_authority();

-- Validate the complete start chain even for direct constructors and direct SQL.
CREATE OR REPLACE FUNCTION execution.guard_live_manifest_chain()
RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
    r execution.live_preflight_reports%ROWTYPE;
    a execution.live_approvals%ROWTYPE;
    s execution.live_account_snapshots%ROWTYPE;
    c execution.live_credential_references%ROWTYPE;
BEGIN
    SELECT * INTO r FROM execution.live_preflight_reports WHERE preflight_report_id=NEW.preflight_report_id;
    SELECT * INTO a FROM execution.live_approvals WHERE approval_id=NEW.approval_id;
    SELECT * INTO s FROM execution.live_account_snapshots WHERE account_snapshot_id=NEW.initial_account_snapshot_id;
    SELECT * INTO c FROM execution.live_credential_references WHERE credential_reference_id=NEW.credential_reference_id;
    IF r.live_run_id<>NEW.live_run_id OR a.live_run_id<>NEW.live_run_id OR s.live_run_id<>NEW.live_run_id THEN
        RAISE EXCEPTION 'manifest authorities must belong to one live run';
    END IF;
    IF r.status<>'passed' OR a.preflight_report_id<>r.preflight_report_id
       OR r.configuration_sha256<>NEW.configuration_sha256 OR a.configuration_sha256<>NEW.configuration_sha256
       OR a.manifest_sha256<>NEW.manifest_sha256 OR r.repository_commit_sha<>NEW.repository_commit_sha
       OR r.endpoint_catalog_sha256<>NEW.endpoint_catalog_sha256
       OR r.account_snapshot_sha256<>s.record_sha256
       OR r.credential_reference_sha256<>c.record_sha256
       OR NEW.credential_reference_sha256<>c.record_sha256 THEN
        RAISE EXCEPTION 'manifest authority chain mismatch';
    END IF;
    RETURN NEW;
END;
$$;
DROP TRIGGER IF EXISTS trg_guard_live_manifest_chain ON execution.live_run_manifests;
CREATE TRIGGER trg_guard_live_manifest_chain BEFORE INSERT OR UPDATE ON execution.live_run_manifests
FOR EACH ROW EXECUTE FUNCTION execution.guard_live_manifest_chain();

-- Core evidence is append-only. Approval consumption is the one narrow authority transition.
CREATE OR REPLACE FUNCTION execution.prevent_live_authority_mutation()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'Phase 8A authority table % is append-only', TG_TABLE_NAME;
END;
$$;

DO $$
DECLARE table_name text;
BEGIN
    FOREACH table_name IN ARRAY ARRAY[
        'live_configuration_snapshots','live_credential_references','live_account_snapshots',
        'live_preflight_sources','live_preflight_checks','live_preflight_check_sources',
        'live_preflight_reports','live_run_manifests','live_runtime_risk_decisions',
        'live_transport_attempts','live_order_observations','live_fill_observations',
        'live_reconciliations','live_reconciliation_differences','live_pre_run_summaries',
        'live_post_run_summaries','live_lifecycle_events','live_kill_events','live_dispatch_events'
    ] LOOP
        EXECUTE format('DROP TRIGGER IF EXISTS trg_%I_immutable ON execution.%I', table_name, table_name);
        EXECUTE format('CREATE TRIGGER trg_%I_immutable BEFORE UPDATE OR DELETE ON execution.%I FOR EACH ROW EXECUTE FUNCTION execution.prevent_live_authority_mutation()', table_name, table_name);
    END LOOP;
END;
$$;

CREATE OR REPLACE FUNCTION execution.guard_live_approval_consumption()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP='DELETE' THEN RAISE EXCEPTION 'live approval cannot be deleted'; END IF;
    IF (to_jsonb(NEW) - 'consumed_notional') <> (to_jsonb(OLD) - 'consumed_notional')
       OR NEW.consumed_notional < OLD.consumed_notional THEN
        RAISE EXCEPTION 'only monotonic approval consumption may change';
    END IF;
    RETURN NEW;
END;
$$;
DROP TRIGGER IF EXISTS trg_live_approval_consumption ON execution.live_approvals;
CREATE TRIGGER trg_live_approval_consumption BEFORE UPDATE OR DELETE ON execution.live_approvals
FOR EACH ROW EXECUTE FUNCTION execution.guard_live_approval_consumption();

CREATE OR REPLACE FUNCTION execution.guard_live_intent_mutation()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP='DELETE' THEN RAISE EXCEPTION 'live intent cannot be deleted'; END IF;
    IF (to_jsonb(NEW) - 'state') <> (to_jsonb(OLD) - 'state') THEN
        RAISE EXCEPTION 'live intent economics, authorities, and hashes are immutable';
    END IF;
    IF OLD.state IN ('dry_run_blocked','dry_run_suppressed','incident_blocked') AND NEW.state<>OLD.state THEN
        RAISE EXCEPTION 'terminal live intent cannot transition';
    END IF;
    RETURN NEW;
END;
$$;
DROP TRIGGER IF EXISTS trg_live_intent_mutation ON execution.live_order_intents;
CREATE TRIGGER trg_live_intent_mutation BEFORE UPDATE OR DELETE ON execution.live_order_intents
FOR EACH ROW EXECUTE FUNCTION execution.guard_live_intent_mutation();

CREATE OR REPLACE FUNCTION execution.guard_live_outbox_request()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP='DELETE' THEN RAISE EXCEPTION 'live outbox cannot be deleted'; END IF;
    IF NEW.provider_request_sha256<>OLD.provider_request_sha256 OR NEW.request_jsonb<>OLD.request_jsonb
       OR NEW.request_method<>OLD.request_method OR NEW.request_path<>OLD.request_path
       OR NEW.order_intent_id<>OLD.order_intent_id OR NEW.live_run_id<>OLD.live_run_id
       OR NEW.client_order_id<>OLD.client_order_id THEN
        RAISE EXCEPTION 'live outbox request identity is immutable';
    END IF;
    RETURN NEW;
END;
$$;
DROP TRIGGER IF EXISTS trg_live_dispatch_request_immutable ON execution.live_dispatch_outbox;
CREATE TRIGGER trg_live_dispatch_request_immutable BEFORE UPDATE OR DELETE ON execution.live_dispatch_outbox
FOR EACH ROW EXECUTE FUNCTION execution.guard_live_outbox_request();
DROP TRIGGER IF EXISTS trg_live_cancel_request_immutable ON execution.live_cancel_outbox;
CREATE TRIGGER trg_live_cancel_request_immutable BEFORE UPDATE OR DELETE ON execution.live_cancel_outbox
FOR EACH ROW EXECUTE FUNCTION execution.guard_live_outbox_request();

CREATE OR REPLACE FUNCTION execution.prevent_phase8a_state_regression()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF TG_TABLE_NAME='live_dispatch_outbox' THEN
        IF OLD.state IN ('dry_run_suppressed','unexpected_external_side_effect') AND NEW.state<>OLD.state THEN
            RAISE EXCEPTION 'terminal live outbox cannot transition';
        END IF;
    ELSIF TG_TABLE_NAME='live_reservations' THEN
        IF NEW.live_run_id<>OLD.live_run_id OR NEW.order_intent_id<>OLD.order_intent_id
           OR NEW.currency<>OLD.currency OR NEW.original_amount<>OLD.original_amount
           OR NEW.original_quantity<>OLD.original_quantity OR NEW.worst_case_price<>OLD.worst_case_price
           OR NEW.maximum_fee_bps<>OLD.maximum_fee_bps OR NEW.maximum_fee_amount<>OLD.maximum_fee_amount
           OR NEW.fee_currency_policy<>OLD.fee_currency_policy OR NEW.risk_notional<>OLD.risk_notional
           OR NEW.reservation_notional<>OLD.reservation_notional OR NEW.calculator_version<>OLD.calculator_version
           OR NEW.source_hashes_jsonb<>OLD.source_hashes_jsonb OR NEW.record_sha256<>OLD.record_sha256 THEN
            RAISE EXCEPTION 'live reservation authority is immutable';
        END IF;
        IF NEW.remaining_amount>OLD.remaining_amount OR NEW.remaining_quantity>OLD.remaining_quantity
           OR (OLD.state IN ('released','consumed') AND NEW.state<>OLD.state) THEN
            RAISE EXCEPTION 'live reservation cannot increase or reopen';
        END IF;
    ELSIF TG_TABLE_NAME='live_order_projections' THEN
        IF NEW.live_run_id<>OLD.live_run_id OR NEW.order_intent_id<>OLD.order_intent_id THEN
            RAISE EXCEPTION 'live projection membership is immutable';
        END IF;
        IF OLD.state IN ('dry_run_suppressed','incident_blocked') AND NEW.state<>OLD.state THEN
            RAISE EXCEPTION 'terminal live projection cannot transition';
        END IF;
    END IF;
    IF NEW.version<>OLD.version+1 THEN
        RAISE EXCEPTION 'live projection version must advance exactly once';
    END IF;
    RETURN NEW;
END;
$$;

CREATE INDEX IF NOT EXISTS idx_live_preflight_sources_run_kind ON execution.live_preflight_sources(live_run_id, source_kind, collected_at_utc DESC);
CREATE INDEX IF NOT EXISTS idx_live_risk_state_day ON execution.live_run_risk_state(trading_day, updated_at_utc);
CREATE INDEX IF NOT EXISTS idx_live_reservation_balance ON execution.live_reservations(live_run_id, currency, state);


-- Exact transport identity is durable and immutable, including method and path.
ALTER TABLE execution.live_dispatch_outbox
    ADD COLUMN IF NOT EXISTS request_method text,
    ADD COLUMN IF NOT EXISTS request_path text;
UPDATE execution.live_dispatch_outbox
SET request_method='POST', request_path='/api/v5/trade/order', version=version+1
WHERE request_method IS NULL OR request_path IS NULL;
ALTER TABLE execution.live_dispatch_outbox
    ALTER COLUMN request_method SET NOT NULL,
    ALTER COLUMN request_path SET NOT NULL,
    ADD CONSTRAINT ck_live_dispatch_request_route CHECK (request_method='POST' AND request_path='/api/v5/trade/order');

ALTER TABLE execution.live_cancel_outbox
    ADD COLUMN IF NOT EXISTS request_method text,
    ADD COLUMN IF NOT EXISTS request_path text;
UPDATE execution.live_cancel_outbox
SET request_method='POST', request_path='/api/v5/trade/cancel-order'
WHERE request_method IS NULL OR request_path IS NULL;
ALTER TABLE execution.live_cancel_outbox
    ALTER COLUMN request_method SET NOT NULL,
    ALTER COLUMN request_path SET NOT NULL,
    ADD CONSTRAINT ck_live_cancel_request_route CHECK (request_method='POST' AND request_path='/api/v5/trade/cancel-order');

-- Complete run-scoped membership for all mutable and observational children.
ALTER TABLE execution.live_cancel_outbox
    ADD CONSTRAINT uq_live_cancel_run UNIQUE (cancel_outbox_id, live_run_id);
ALTER TABLE execution.live_kill_switches
    ADD CONSTRAINT uq_live_kill_switch_run UNIQUE (kill_switch_id, live_run_id),
    ADD CONSTRAINT fk_live_kill_switch_run FOREIGN KEY (live_run_id)
        REFERENCES execution.live_runs(live_run_id) ON DELETE RESTRICT;
ALTER TABLE execution.live_kill_events
    ADD CONSTRAINT fk_live_kill_event_run FOREIGN KEY (kill_switch_id, live_run_id)
        REFERENCES execution.live_kill_switches(kill_switch_id, live_run_id) ON DELETE RESTRICT;
ALTER TABLE execution.live_transport_attempts
    ADD CONSTRAINT fk_live_transport_intent_run FOREIGN KEY (order_intent_id, live_run_id)
        REFERENCES execution.live_order_intents(order_intent_id, live_run_id) ON DELETE RESTRICT;
ALTER TABLE execution.live_order_observations
    ADD CONSTRAINT fk_live_order_observation_intent_run FOREIGN KEY (order_intent_id, live_run_id)
        REFERENCES execution.live_order_intents(order_intent_id, live_run_id) ON DELETE RESTRICT;
ALTER TABLE execution.live_order_projections
    ADD CONSTRAINT fk_live_projection_intent_run FOREIGN KEY (order_intent_id, live_run_id)
        REFERENCES execution.live_order_intents(order_intent_id, live_run_id) ON DELETE RESTRICT;
ALTER TABLE execution.live_fill_observations
    ADD CONSTRAINT fk_live_fill_intent_run FOREIGN KEY (order_intent_id, live_run_id)
        REFERENCES execution.live_order_intents(order_intent_id, live_run_id) ON DELETE RESTRICT;
ALTER TABLE execution.live_recovery_records
    ADD CONSTRAINT fk_live_recovery_intent_run FOREIGN KEY (order_intent_id, live_run_id)
        REFERENCES execution.live_order_intents(order_intent_id, live_run_id) ON DELETE RESTRICT;
ALTER TABLE execution.live_run_risk_state
    ADD CONSTRAINT fk_live_risk_reconciliation_run FOREIGN KEY (latest_reconciliation_id, live_run_id)
        REFERENCES execution.live_reconciliations(reconciliation_id, live_run_id) ON DELETE RESTRICT,
    ADD CONSTRAINT fk_live_risk_market_source_run FOREIGN KEY (latest_market_evidence_id, live_run_id)
        REFERENCES execution.live_preflight_sources(source_id, live_run_id) ON DELETE RESTRICT;

-- Reinstall exact preflight validation against configuration, account, and credential rows.
CREATE OR REPLACE FUNCTION execution.guard_live_preflight_authority()
RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
    credential execution.live_credential_references%ROWTYPE;
    account execution.live_account_snapshots%ROWTYPE;
    configuration execution.live_configuration_snapshots%ROWTYPE;
    unsafe_count integer;
BEGIN
    SELECT * INTO credential FROM execution.live_credential_references
    WHERE credential_reference_id=NEW.credential_reference_id;
    SELECT * INTO account FROM execution.live_account_snapshots
    WHERE account_snapshot_id=NEW.account_snapshot_id;
    SELECT * INTO configuration FROM execution.live_configuration_snapshots
    WHERE configuration_sha256=NEW.configuration_sha256;
    IF credential.credential_reference_id IS NULL
       OR account.account_snapshot_id IS NULL
       OR configuration.configuration_snapshot_id IS NULL
       OR credential.record_sha256<>NEW.credential_reference_sha256
       OR account.record_sha256<>NEW.account_snapshot_sha256
       OR account.live_run_id<>NEW.live_run_id
       OR credential.provider<>configuration.provider
       OR credential.account_fingerprint<>configuration.account_fingerprint
       OR account.account_fingerprint<>configuration.account_fingerprint
       OR NEW.implementation_sha256<>configuration.configuration_jsonb->>'provider_implementation_hash'
       OR NEW.endpoint_catalog_sha256<>configuration.configuration_jsonb->>'endpoint_catalog_hash' THEN
        RAISE EXCEPTION 'preflight configuration, credential, or account authority mismatch';
    END IF;
    IF NEW.status='passed' THEN
        IF credential.verified_at_utc IS NULL
           OR jsonb_typeof(credential.permission_summary_jsonb)<>'array'
           OR jsonb_array_length(credential.permission_summary_jsonb)=0 THEN
            RAISE EXCEPTION 'passed preflight requires verified credential permissions';
        END IF;
        SELECT count(*) INTO unsafe_count
        FROM jsonb_array_elements_text(credential.permission_summary_jsonb) p(value)
        WHERE p.value NOT IN ('read','spot_trade');
        IF unsafe_count>0 OR NOT credential.permission_summary_jsonb ? 'read' THEN
            RAISE EXCEPTION 'unsafe credential permissions cannot produce a passed preflight';
        END IF;
    END IF;
    RETURN NEW;
END;
$$;

-- Reinstall full approval/report/manifest binding for direct SQL as well as repository writes.
CREATE OR REPLACE FUNCTION execution.guard_live_manifest_chain()
RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
    report execution.live_preflight_reports%ROWTYPE;
    approval execution.live_approvals%ROWTYPE;
    account execution.live_account_snapshots%ROWTYPE;
    credential execution.live_credential_references%ROWTYPE;
    configuration execution.live_configuration_snapshots%ROWTYPE;
BEGIN
    SELECT * INTO report FROM execution.live_preflight_reports WHERE preflight_report_id=NEW.preflight_report_id;
    SELECT * INTO approval FROM execution.live_approvals WHERE approval_id=NEW.approval_id;
    SELECT * INTO account FROM execution.live_account_snapshots WHERE account_snapshot_id=NEW.initial_account_snapshot_id;
    SELECT * INTO credential FROM execution.live_credential_references WHERE credential_reference_id=NEW.credential_reference_id;
    SELECT * INTO configuration FROM execution.live_configuration_snapshots WHERE configuration_sha256=NEW.configuration_sha256;
    IF report.live_run_id<>NEW.live_run_id
       OR approval.live_run_id<>NEW.live_run_id
       OR account.live_run_id<>NEW.live_run_id
       OR report.status<>'passed'
       OR approval.preflight_report_id<>report.preflight_report_id
       OR report.configuration_sha256<>NEW.configuration_sha256
       OR approval.configuration_sha256<>NEW.configuration_sha256
       OR approval.manifest_sha256<>NEW.manifest_sha256
       OR report.repository_commit_sha<>NEW.repository_commit_sha
       OR approval.approval_jsonb->>'repository_commit_sha'<>NEW.repository_commit_sha
       OR report.endpoint_catalog_sha256<>NEW.endpoint_catalog_sha256
       OR report.account_snapshot_sha256<>account.record_sha256
       OR report.credential_reference_sha256<>credential.record_sha256
       OR NEW.credential_reference_sha256<>credential.record_sha256
       OR approval.account_fingerprint<>account.account_fingerprint
       OR approval.account_fingerprint<>configuration.account_fingerprint
       OR approval.provider<>configuration.provider
       OR approval.environment<>configuration.environment
       OR NEW.implementation_sha256<>configuration.configuration_jsonb->>'provider_implementation_hash'
       OR NEW.endpoint_catalog_sha256<>configuration.configuration_jsonb->>'endpoint_catalog_hash'
       OR approval.approval_jsonb->'allowed_instruments'<>NEW.manifest_jsonb->'allowed_instruments' THEN
        RAISE EXCEPTION 'manifest authority chain mismatch';
    END IF;
    RETURN NEW;
END;
$$;

CREATE OR REPLACE FUNCTION execution.guard_live_outbox_request()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP='DELETE' THEN RAISE EXCEPTION 'live outbox cannot be deleted'; END IF;
    IF NEW.provider_request_sha256<>OLD.provider_request_sha256
       OR NEW.request_jsonb<>OLD.request_jsonb
       OR NEW.request_method<>OLD.request_method
       OR NEW.request_path<>OLD.request_path
       OR NEW.order_intent_id<>OLD.order_intent_id
       OR NEW.live_run_id<>OLD.live_run_id
       OR NEW.client_order_id<>OLD.client_order_id THEN
        RAISE EXCEPTION 'live outbox request identity is immutable';
    END IF;
    RETURN NEW;
END;
$$;

CREATE OR REPLACE FUNCTION execution.prevent_phase8a_state_regression()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF TG_TABLE_NAME='live_dispatch_outbox' THEN
        IF NOT (
            NEW.state=OLD.state
            OR (OLD.state='dry_run_prepared' AND NEW.state IN ('dry_run_suppressed','pending_recovery'))
            OR (OLD.state='pending_recovery' AND NEW.state IN ('dry_run_suppressed','unexpected_external_side_effect'))
        ) THEN
            RAISE EXCEPTION 'illegal live dispatch transition';
        END IF;
    ELSIF TG_TABLE_NAME='live_reservations' THEN
        IF NEW.live_run_id<>OLD.live_run_id
           OR NEW.order_intent_id<>OLD.order_intent_id
           OR NEW.currency<>OLD.currency
           OR NEW.original_amount<>OLD.original_amount
           OR NEW.original_quantity<>OLD.original_quantity
           OR NEW.worst_case_price<>OLD.worst_case_price
           OR NEW.maximum_fee_bps<>OLD.maximum_fee_bps
           OR NEW.maximum_fee_amount<>OLD.maximum_fee_amount
           OR NEW.fee_currency_policy<>OLD.fee_currency_policy
           OR NEW.risk_notional<>OLD.risk_notional
           OR NEW.reservation_notional<>OLD.reservation_notional
           OR NEW.calculator_version<>OLD.calculator_version
           OR NEW.source_hashes_jsonb<>OLD.source_hashes_jsonb
           OR NEW.record_sha256<>OLD.record_sha256 THEN
            RAISE EXCEPTION 'live reservation authority is immutable';
        END IF;
        IF NEW.remaining_amount>OLD.remaining_amount
           OR NEW.remaining_quantity>OLD.remaining_quantity
           OR NOT (
               NEW.state=OLD.state
               OR (OLD.state='projected' AND NEW.state IN ('released','consumed'))
           ) THEN
            RAISE EXCEPTION 'live reservation cannot increase or reopen';
        END IF;
    ELSIF TG_TABLE_NAME='live_order_projections' THEN
        IF NEW.live_run_id<>OLD.live_run_id OR NEW.order_intent_id<>OLD.order_intent_id THEN
            RAISE EXCEPTION 'live projection membership is immutable';
        END IF;
        IF NOT (
            NEW.state=OLD.state
            OR (OLD.state='dry_run_prepared' AND NEW.state IN ('dry_run_suppressed','pending_recovery'))
            OR (OLD.state='pending_recovery' AND NEW.state IN ('dry_run_suppressed','incident_blocked'))
        ) THEN
            RAISE EXCEPTION 'illegal live projection transition';
        END IF;
    END IF;
    IF NEW.version<>OLD.version+1 THEN
        RAISE EXCEPTION 'live projection version must advance exactly once';
    END IF;
    RETURN NEW;
END;
$$;

CREATE OR REPLACE FUNCTION execution.guard_live_kill_transition()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP='DELETE' THEN RAISE EXCEPTION 'live kill switch cannot be deleted'; END IF;
    IF NEW.kill_switch_id<>OLD.kill_switch_id OR NEW.live_run_id<>OLD.live_run_id THEN
        RAISE EXCEPTION 'live kill identity is immutable';
    END IF;
    IF NOT (
        NEW.state=OLD.state
        OR (OLD.state='armed' AND NEW.state IN ('triggered','stopped'))
        OR (OLD.state='triggered' AND NEW.state IN ('cancellation_in_progress','cancellation_ambiguous','stopped'))
        OR (OLD.state='cancellation_in_progress' AND NEW.state IN ('cancellation_ambiguous','stopped'))
        OR (OLD.state='cancellation_ambiguous' AND NEW.state='stopped')
        OR (OLD.state='stopped' AND NEW.state='reset_pending')
        OR (OLD.state='reset_pending' AND NEW.state='armed')
        OR (OLD.state='reset' AND NEW.state='armed')
    ) THEN
        RAISE EXCEPTION 'illegal live kill-switch transition';
    END IF;
    IF NEW.version<>OLD.version+1 THEN
        RAISE EXCEPTION 'live kill-switch version must advance exactly once';
    END IF;
    IF OLD.triggered_at_utc IS NOT NULL AND NEW.triggered_at_utc IS DISTINCT FROM OLD.triggered_at_utc THEN
        RAISE EXCEPTION 'kill trigger time is immutable';
    END IF;
    RETURN NEW;
END;
$$;
DROP TRIGGER IF EXISTS trg_live_kill_transition ON execution.live_kill_switches;
CREATE TRIGGER trg_live_kill_transition BEFORE UPDATE OR DELETE ON execution.live_kill_switches
FOR EACH ROW EXECUTE FUNCTION execution.guard_live_kill_transition();

CREATE OR REPLACE FUNCTION execution.guard_live_cancel_transition()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP='DELETE' THEN RAISE EXCEPTION 'live cancel outbox cannot be deleted'; END IF;
    IF NOT (
        NEW.state=OLD.state
        OR (OLD.state='dry_run_prepared' AND NEW.state IN ('dry_run_suppressed','pending_recovery'))
        OR (OLD.state='pending_recovery' AND NEW.state='dry_run_suppressed')
    ) THEN
        RAISE EXCEPTION 'illegal live cancel transition';
    END IF;
    RETURN NEW;
END;
$$;
DROP TRIGGER IF EXISTS trg_live_cancel_transition ON execution.live_cancel_outbox;
CREATE TRIGGER trg_live_cancel_transition BEFORE UPDATE OR DELETE ON execution.live_cancel_outbox
FOR EACH ROW EXECUTE FUNCTION execution.guard_live_cancel_transition();

CREATE OR REPLACE FUNCTION execution.guard_live_risk_state_transition()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP='DELETE' OR NEW.live_run_id<>OLD.live_run_id THEN
        RAISE EXCEPTION 'live risk state identity cannot change';
    END IF;
    IF NEW.version<>OLD.version+1 THEN
        RAISE EXCEPTION 'live risk state version must advance exactly once';
    END IF;
    RETURN NEW;
END;
$$;
DROP TRIGGER IF EXISTS trg_live_risk_state_transition ON execution.live_run_risk_state;
CREATE TRIGGER trg_live_risk_state_transition BEFORE UPDATE OR DELETE ON execution.live_run_risk_state
FOR EACH ROW EXECUTE FUNCTION execution.guard_live_risk_state_transition();

CREATE OR REPLACE FUNCTION execution.guard_live_recovery_identity()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP='DELETE' THEN RAISE EXCEPTION 'live recovery record cannot be deleted'; END IF;
    IF NEW.recovery_record_id<>OLD.recovery_record_id
       OR NEW.live_run_id<>OLD.live_run_id
       OR NEW.order_intent_id IS DISTINCT FROM OLD.order_intent_id
       OR NEW.client_order_id<>OLD.client_order_id
       OR NEW.generation<>OLD.generation
       OR NEW.worker_identity<>OLD.worker_identity
       OR NEW.claim_token<>OLD.claim_token
       OR NEW.query_first<>OLD.query_first
       OR NEW.created_at_utc<>OLD.created_at_utc THEN
        RAISE EXCEPTION 'live recovery identity is immutable';
    END IF;
    IF NOT (
        NEW.state=OLD.state
        OR (OLD.state='claimed' AND NEW.state IN ('observed','resolved','ambiguous'))
        OR (OLD.state='observed' AND NEW.state IN ('resolved','ambiguous'))
    ) THEN
        RAISE EXCEPTION 'illegal live recovery transition';
    END IF;
    RETURN NEW;
END;
$$;
DROP TRIGGER IF EXISTS trg_live_recovery_identity ON execution.live_recovery_records;
CREATE TRIGGER trg_live_recovery_identity BEFORE UPDATE OR DELETE ON execution.live_recovery_records
FOR EACH ROW EXECUTE FUNCTION execution.guard_live_recovery_identity();

CREATE OR REPLACE FUNCTION execution.guard_live_run_transition()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP='DELETE' THEN RAISE EXCEPTION 'live run cannot be deleted'; END IF;
    IF NEW.live_run_id<>OLD.live_run_id
       OR NEW.manifest_id<>OLD.manifest_id
       OR NEW.dry_run<>OLD.dry_run
       OR NEW.production_write_enabled<>OLD.production_write_enabled
       OR NEW.record_sha256<>OLD.record_sha256 THEN
        RAISE EXCEPTION 'live run authority is immutable';
    END IF;
    IF NEW.version<>OLD.version+1 THEN
        RAISE EXCEPTION 'live run version must advance exactly once';
    END IF;
    IF OLD.state IN ('dry_run_completed','blocked') AND NEW.state<>OLD.state THEN
        RAISE EXCEPTION 'terminal live run cannot transition';
    END IF;
    IF OLD.state='stopped' AND NEW.state<>OLD.state AND NOT (
        NEW.state='dry_run_running' AND EXISTS (
            SELECT 1 FROM execution.live_kill_switches k
            WHERE k.live_run_id=NEW.live_run_id AND k.state='armed'
              AND k.reset_preflight_report_id IS NOT NULL AND k.reset_approval_id IS NOT NULL
        )
    ) THEN
        RAISE EXCEPTION 'stopped live run lacks reset authority';
    END IF;
    RETURN NEW;
END;
$$;
DROP TRIGGER IF EXISTS trg_live_run_transition ON execution.live_runs;
CREATE TRIGGER trg_live_run_transition BEFORE UPDATE OR DELETE ON execution.live_runs
FOR EACH ROW EXECUTE FUNCTION execution.guard_live_run_transition();
