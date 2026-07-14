-- Phase 8A collector provenance, reconciliation, metadata, and recovery integrity.
-- Migrations 0001 through 0023 are immutable. PostgreSQL remains authoritative.
-- Production submission and cancellation remain unconditionally unreachable.

ALTER TABLE execution.live_preflight_sources
    ADD COLUMN IF NOT EXISTS producer_classification text NOT NULL DEFAULT 'legacy_untrusted',
    ADD COLUMN IF NOT EXISTS collector_kind text,
    ADD COLUMN IF NOT EXISTS collector_version text,
    ADD COLUMN IF NOT EXISTS parser_version text,
    ADD COLUMN IF NOT EXISTS source_system_identity text,
    ADD COLUMN IF NOT EXISTS source_record_identity text,
    ADD COLUMN IF NOT EXISTS raw_response_sha256 text,
    ADD COLUMN IF NOT EXISTS normalized_payload_sha256 text,
    ADD COLUMN IF NOT EXISTS source_schema_version integer;

ALTER TABLE execution.live_preflight_sources
    ADD CONSTRAINT ck_live_source_producer_classification CHECK (
        producer_classification IN ('operational_collector','fixture','imported','legacy_untrusted')
    ),
    ADD CONSTRAINT ck_live_source_operational_provenance CHECK (
        NOT operational OR (
            producer_classification='operational_collector'
            AND collector_kind IS NOT NULL
            AND collector_version IS NOT NULL
            AND source_system_identity IS NOT NULL
            AND source_record_identity IS NOT NULL
            AND raw_response_sha256 ~ '^[0-9a-f]{64}$'
            AND normalized_payload_sha256 ~ '^[0-9a-f]{64}$'
            AND source_schema_version > 0
        )
    );

ALTER TABLE execution.live_preflight_reports
    ADD COLUMN IF NOT EXISTS purpose text NOT NULL DEFAULT 'run_start',
    ADD COLUMN IF NOT EXISTS authority_generation text NOT NULL DEFAULT 'legacy_untrusted';
ALTER TABLE execution.live_preflight_reports DROP CONSTRAINT IF EXISTS live_preflight_reports_status_check;
ALTER TABLE execution.live_preflight_reports
    ADD CONSTRAINT live_preflight_reports_status_check CHECK (status IN ('passed','passed_for_reset','blocked')),
    ADD CONSTRAINT ck_live_preflight_purpose CHECK (purpose IN ('run_start','run_continue','kill_reset')),
    ADD CONSTRAINT ck_live_preflight_status_purpose CHECK (
        (purpose='kill_reset' AND status IN ('passed_for_reset','blocked'))
        OR (purpose IN ('run_start','run_continue') AND status IN ('passed','blocked'))
    );

CREATE TABLE IF NOT EXISTS execution.live_okx_response_bundles (
    response_bundle_id uuid PRIMARY KEY,
    live_run_id uuid NOT NULL,
    bundle_purpose text NOT NULL CHECK (bundle_purpose IN ('preflight','reconciliation','recovery')),
    producer_classification text NOT NULL CHECK (producer_classification IN ('operational_collector','fixture','imported')),
    collector_kind text NOT NULL,
    collector_version text NOT NULL,
    parser_version text NOT NULL,
    account_fingerprint text NOT NULL,
    query_started_at_utc timestamptz NOT NULL,
    query_completed_at_utc timestamptz NOT NULL,
    venue_observed_at_utc timestamptz NOT NULL,
    endpoint_matrix_sha256 text NOT NULL CHECK (endpoint_matrix_sha256 ~ '^[0-9a-f]{64}$'),
    normalized_payload_sha256 text NOT NULL CHECK (normalized_payload_sha256 ~ '^[0-9a-f]{64}$'),
    record_sha256 text NOT NULL CHECK (record_sha256 ~ '^[0-9a-f]{64}$'),
    UNIQUE (response_bundle_id, live_run_id),
    CHECK (query_completed_at_utc >= query_started_at_utc)
);

CREATE TABLE IF NOT EXISTS execution.live_okx_response_envelopes (
    response_bundle_id uuid NOT NULL REFERENCES execution.live_okx_response_bundles(response_bundle_id) ON DELETE RESTRICT,
    endpoint_kind text NOT NULL CHECK (endpoint_kind IN (
        'account_config','balances','positions','pending_orders','order_history',
        'fills','venue_time','instrument_metadata','order_details'
    )),
    request_identity text NOT NULL,
    request_method text NOT NULL CHECK (request_method='GET'),
    request_path text NOT NULL CHECK (request_path LIKE '/api/v5/%'),
    top_level_provider_code text,
    query_started_at_utc timestamptz NOT NULL,
    query_completed_at_utc timestamptz NOT NULL,
    completed boolean NOT NULL,
    error_classification text,
    raw_response_jsonb jsonb,
    canonical_response_sha256 text,
    database_payload_sha256 text,
    parser_version text NOT NULL,
    record_sha256 text NOT NULL CHECK (record_sha256 ~ '^[0-9a-f]{64}$'),
    PRIMARY KEY (response_bundle_id, endpoint_kind),
    CHECK (query_completed_at_utc >= query_started_at_utc),
    CHECK (
        (completed AND error_classification IS NULL AND raw_response_jsonb IS NOT NULL
         AND top_level_provider_code='0'
         AND canonical_response_sha256 ~ '^[0-9a-f]{64}$')
        OR (NOT completed AND error_classification IS NOT NULL)
    ),
    CHECK (
        canonical_response_sha256 IS NOT DISTINCT FROM database_payload_sha256
    ),
    CHECK (
        error_classification IS NULL OR error_classification IN (
            'transport_ambiguous','parser_error','rate_limited','explicit_provider_rejection'
        )
    ),
    CHECK (
        error_classification<>'explicit_provider_rejection'
        OR (top_level_provider_code IS NOT NULL AND top_level_provider_code<>'0')
    ),
    CHECK (
        split_part(request_path,'?',1)=CASE endpoint_kind
            WHEN 'account_config' THEN '/api/v5/account/config'
            WHEN 'balances' THEN '/api/v5/account/balance'
            WHEN 'positions' THEN '/api/v5/account/positions'
            WHEN 'pending_orders' THEN '/api/v5/trade/orders-pending'
            WHEN 'order_history' THEN '/api/v5/trade/orders-history'
            WHEN 'fills' THEN '/api/v5/trade/fills-history'
            WHEN 'venue_time' THEN '/api/v5/public/time'
            WHEN 'instrument_metadata' THEN '/api/v5/public/instruments'
            WHEN 'order_details' THEN '/api/v5/trade/order'
        END
    )

);
CREATE OR REPLACE FUNCTION execution.live_canonical_jsonb_text(value jsonb)
RETURNS text LANGUAGE plpgsql IMMUTABLE STRICT PARALLEL SAFE AS $$
DECLARE
    kind text;
    rendered text;
BEGIN
    kind := jsonb_typeof(value);
    IF kind='object' THEN
        SELECT '{' || COALESCE(string_agg(
            to_jsonb(entry.key)::text || ':' || execution.live_canonical_jsonb_text(entry.value),
            ',' ORDER BY entry.key
        ), '') || '}' INTO rendered
        FROM jsonb_each(value) AS entry;
        RETURN rendered;
    ELSIF kind='array' THEN
        SELECT '[' || COALESCE(string_agg(
            execution.live_canonical_jsonb_text(entry.value),
            ',' ORDER BY entry.ordinality
        ), '') || ']' INTO rendered
        FROM jsonb_array_elements(value) WITH ORDINALITY AS entry(value, ordinality);
        RETURN rendered;
    END IF;
    RETURN value::text;
END;
$$;

CREATE OR REPLACE FUNCTION execution.guard_live_okx_response_payload_hash()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    NEW.database_payload_sha256 := CASE WHEN NEW.raw_response_jsonb IS NULL THEN NULL
        ELSE encode(sha256(convert_to(execution.live_canonical_jsonb_text(NEW.raw_response_jsonb),'UTF8')),'hex') END;
    IF NEW.canonical_response_sha256 IS DISTINCT FROM NEW.database_payload_sha256 THEN
        RAISE EXCEPTION 'OKX response payload hash mismatch';
    END IF;
    RETURN NEW;
END;
$$;
DROP TRIGGER IF EXISTS trg_guard_live_okx_response_payload_hash ON execution.live_okx_response_envelopes;
CREATE TRIGGER trg_guard_live_okx_response_payload_hash
BEFORE INSERT OR UPDATE ON execution.live_okx_response_envelopes
FOR EACH ROW EXECUTE FUNCTION execution.guard_live_okx_response_payload_hash();

ALTER TABLE execution.live_okx_response_envelopes
    ADD CONSTRAINT ck_live_okx_request_identity CHECK (
        request_identity=encode(sha256(convert_to(
            execution.live_canonical_jsonb_text(
                jsonb_build_object('method',request_method,'path',request_path)
            ),
            'UTF8'
        )),'hex')
    );

CREATE OR REPLACE FUNCTION execution.validate_live_okx_bundle_endpoint_matrix()
RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
    bundle execution.live_okx_response_bundles%ROWTYPE;
    expected_endpoints text[];
    observed_endpoints text[];
    observed_matrix jsonb;
    observed_matrix_sha256 text;
    first_query_at timestamptz;
    last_query_at timestamptz;
    parser_mismatch_count integer;
BEGIN
    SELECT * INTO bundle FROM execution.live_okx_response_bundles
    WHERE response_bundle_id=NEW.response_bundle_id;
    IF bundle.response_bundle_id IS NULL THEN
        RAISE EXCEPTION 'OKX response bundle is missing';
    END IF;
    expected_endpoints := CASE bundle.bundle_purpose
        WHEN 'preflight' THEN ARRAY[
            'account_config','balances','instrument_metadata',
            'pending_orders','positions','venue_time'
        ]
        WHEN 'reconciliation' THEN ARRAY[
            'account_config','balances','fills','order_history',
            'pending_orders','positions','venue_time'
        ]
        WHEN 'recovery' THEN ARRAY[
            'account_config','balances','fills','order_details',
            'order_history','pending_orders','positions'
        ]
    END;
    SELECT
        array_agg(endpoint_kind ORDER BY endpoint_kind),
        jsonb_object_agg(
            endpoint_kind,
            jsonb_build_object(
                'completed',completed,
                'disposition',CASE WHEN completed THEN 'completed' ELSE error_classification END,
                'response_hash',canonical_response_sha256
            )
        ),
        min(query_started_at_utc),
        max(query_completed_at_utc),
        count(*) FILTER (WHERE parser_version<>bundle.parser_version)
    INTO observed_endpoints,observed_matrix,first_query_at,last_query_at,parser_mismatch_count
    FROM execution.live_okx_response_envelopes
    WHERE response_bundle_id=bundle.response_bundle_id;

    IF observed_endpoints IS DISTINCT FROM expected_endpoints
       OR first_query_at IS DISTINCT FROM bundle.query_started_at_utc
       OR last_query_at IS DISTINCT FROM bundle.query_completed_at_utc
       OR parser_mismatch_count<>0 THEN
        RAISE EXCEPTION 'OKX response bundle endpoint matrix or query provenance is not exact';
    END IF;
    observed_matrix_sha256 := encode(sha256(convert_to(
        execution.live_canonical_jsonb_text(observed_matrix),'UTF8'
    )),'hex');
    IF observed_matrix_sha256<>bundle.endpoint_matrix_sha256 THEN
        RAISE EXCEPTION 'OKX response bundle endpoint matrix hash mismatch';
    END IF;
    RETURN NEW;
END;
$$;
DROP TRIGGER IF EXISTS trg_validate_live_okx_bundle_matrix ON execution.live_okx_response_bundles;
CREATE CONSTRAINT TRIGGER trg_validate_live_okx_bundle_matrix
AFTER INSERT ON execution.live_okx_response_bundles
DEFERRABLE INITIALLY DEFERRED FOR EACH ROW
EXECUTE FUNCTION execution.validate_live_okx_bundle_endpoint_matrix();
DROP TRIGGER IF EXISTS trg_validate_live_okx_envelope_matrix ON execution.live_okx_response_envelopes;
CREATE CONSTRAINT TRIGGER trg_validate_live_okx_envelope_matrix
AFTER INSERT ON execution.live_okx_response_envelopes
DEFERRABLE INITIALLY DEFERRED FOR EACH ROW
EXECUTE FUNCTION execution.validate_live_okx_bundle_endpoint_matrix();

CREATE TABLE IF NOT EXISTS execution.live_market_source_bindings (
    source_id uuid PRIMARY KEY,
    live_run_id uuid NOT NULL,
    bar_id uuid NOT NULL REFERENCES market_data.validated_bars(bar_id) ON DELETE RESTRICT,
    validation_report_id uuid NOT NULL REFERENCES data_quality.validation_reports(validation_report_id) ON DELETE RESTRICT,
    raw_observation_ids uuid[] NOT NULL,
    raw_observation_hashes_jsonb jsonb NOT NULL,
    validation_status text NOT NULL CHECK (validation_status IN ('accepted','accepted_with_warnings')),
    finality_verified boolean NOT NULL,
    quarantine_clear boolean NOT NULL,
    quote_currency text NOT NULL,
    observed_at_utc timestamptz NOT NULL,
    available_at_utc timestamptz NOT NULL,
    record_sha256 text NOT NULL CHECK (record_sha256 ~ '^[0-9a-f]{64}$'),
    CONSTRAINT fk_live_market_binding_source FOREIGN KEY (source_id,live_run_id)
        REFERENCES execution.live_preflight_sources(source_id,live_run_id) ON DELETE RESTRICT
);

CREATE TABLE IF NOT EXISTS execution.live_instrument_metadata_sources (
    source_id uuid PRIMARY KEY,
    live_run_id uuid NOT NULL,
    response_bundle_id uuid NOT NULL,
    instrument_id text NOT NULL,
    instrument_type text NOT NULL CHECK (instrument_type='spot'),
    instrument_state text NOT NULL,
    base_currency text NOT NULL,
    quote_currency text NOT NULL,
    tick_size numeric NOT NULL CHECK (tick_size > 0),
    lot_size numeric NOT NULL CHECK (lot_size > 0),
    minimum_size numeric NOT NULL CHECK (minimum_size > 0),
    minimum_notional numeric NOT NULL CHECK (minimum_notional > 0),
    collected_at_utc timestamptz NOT NULL,
    provider_response_sha256 text NOT NULL CHECK (provider_response_sha256 ~ '^[0-9a-f]{64}$'),
    parser_version text NOT NULL,
    record_sha256 text NOT NULL CHECK (record_sha256 ~ '^[0-9a-f]{64}$'),
    CONSTRAINT fk_live_metadata_source FOREIGN KEY (source_id,live_run_id)
        REFERENCES execution.live_preflight_sources(source_id,live_run_id) ON DELETE RESTRICT,
    CONSTRAINT fk_live_metadata_bundle FOREIGN KEY (response_bundle_id,live_run_id)
        REFERENCES execution.live_okx_response_bundles(response_bundle_id,live_run_id) ON DELETE RESTRICT,
    UNIQUE (source_id,live_run_id,instrument_id)
);

ALTER TABLE execution.live_reconciliations
    ADD COLUMN IF NOT EXISTS local_projection_as_of_utc timestamptz,
    ADD COLUMN IF NOT EXISTS venue_observation_as_of_utc timestamptz,
    ADD COLUMN IF NOT EXISTS query_started_at_utc timestamptz,
    ADD COLUMN IF NOT EXISTS query_completed_at_utc timestamptz,
    ADD COLUMN IF NOT EXISTS response_bundle_id uuid,
    ADD COLUMN IF NOT EXISTS producer_classification text NOT NULL DEFAULT 'legacy_untrusted',
    ADD COLUMN IF NOT EXISTS local_sequence bigint,
    ADD COLUMN IF NOT EXISTS venue_sequence bigint;
ALTER TABLE execution.live_reconciliations
    ADD CONSTRAINT fk_live_reconciliation_response_bundle FOREIGN KEY (response_bundle_id,live_run_id)
        REFERENCES execution.live_okx_response_bundles(response_bundle_id,live_run_id) ON DELETE RESTRICT,
    ADD CONSTRAINT ck_live_reconciliation_sequences CHECK (
        local_sequence IS NULL OR (local_sequence >= 0 AND venue_sequence >= 0)
    );
CREATE TABLE IF NOT EXISTS execution.live_reconciliation_input_bundles (
    reconciliation_input_bundle_id uuid PRIMARY KEY,
    reconciliation_id uuid NOT NULL,
    live_run_id uuid NOT NULL,
    response_bundle_id uuid NOT NULL,
    local_projection_jsonb jsonb NOT NULL,
    venue_projection_jsonb jsonb NOT NULL,
    local_projection_sha256 text NOT NULL CHECK (local_projection_sha256 ~ '^[0-9a-f]{64}$'),
    venue_projection_sha256 text NOT NULL CHECK (venue_projection_sha256 ~ '^[0-9a-f]{64}$'),
    record_sha256 text NOT NULL CHECK (record_sha256 ~ '^[0-9a-f]{64}$'),
    CONSTRAINT fk_live_reconciliation_input_run FOREIGN KEY (reconciliation_id,live_run_id)
        REFERENCES execution.live_reconciliations(reconciliation_id,live_run_id) ON DELETE RESTRICT,
    CONSTRAINT fk_live_reconciliation_input_response FOREIGN KEY (response_bundle_id,live_run_id)
        REFERENCES execution.live_okx_response_bundles(response_bundle_id,live_run_id) ON DELETE RESTRICT,
    UNIQUE (reconciliation_id,live_run_id)
);

ALTER TABLE execution.live_run_risk_state
    ADD COLUMN IF NOT EXISTS latest_reconciliation_input_bundle_id uuid,
    ADD COLUMN IF NOT EXISTS latest_local_sequence bigint NOT NULL DEFAULT 0 CHECK (latest_local_sequence >= 0),
    ADD COLUMN IF NOT EXISTS latest_venue_sequence bigint NOT NULL DEFAULT 0 CHECK (latest_venue_sequence >= 0);
ALTER TABLE execution.live_reconciliation_input_bundles
    ADD CONSTRAINT uq_live_reconciliation_input_run UNIQUE (reconciliation_input_bundle_id,live_run_id);
ALTER TABLE execution.live_run_risk_state
    ADD CONSTRAINT fk_live_risk_reconciliation_input FOREIGN KEY (
        latest_reconciliation_input_bundle_id,live_run_id
    ) REFERENCES execution.live_reconciliation_input_bundles(
        reconciliation_input_bundle_id,live_run_id
    ) ON DELETE RESTRICT;

ALTER TABLE execution.live_order_intents
    ADD COLUMN IF NOT EXISTS instrument_metadata_source_id uuid,
    ADD COLUMN IF NOT EXISTS instrument_metadata_parser_version text,
    ADD COLUMN IF NOT EXISTS metadata_authority_generation text NOT NULL DEFAULT 'legacy_untrusted';
ALTER TABLE execution.live_dispatch_outbox
    ADD COLUMN IF NOT EXISTS instrument_metadata_source_id uuid,
    ADD COLUMN IF NOT EXISTS instrument_metadata_sha256 text,
    ADD COLUMN IF NOT EXISTS instrument_metadata_parser_version text,
    ADD COLUMN IF NOT EXISTS metadata_authority_generation text NOT NULL DEFAULT 'legacy_untrusted';

ALTER TABLE execution.live_order_intents
    ADD CONSTRAINT fk_live_intent_metadata_run FOREIGN KEY (
        instrument_metadata_source_id,live_run_id,instrument_id
    ) REFERENCES execution.live_instrument_metadata_sources(source_id,live_run_id,instrument_id) ON DELETE RESTRICT;
ALTER TABLE execution.live_dispatch_outbox
    ADD CONSTRAINT fk_live_dispatch_metadata_run FOREIGN KEY (
        instrument_metadata_source_id,live_run_id
    ) REFERENCES execution.live_preflight_sources(source_id,live_run_id) ON DELETE RESTRICT;

ALTER TABLE execution.live_order_observations
    ADD COLUMN IF NOT EXISTS response_bundle_id uuid,
    ADD COLUMN IF NOT EXISTS evidence_classification text NOT NULL DEFAULT 'legacy_untrusted',
    ADD COLUMN IF NOT EXISTS endpoint_matrix_sha256 text,
    ADD COLUMN IF NOT EXISTS query_started_at_utc timestamptz,
    ADD COLUMN IF NOT EXISTS query_completed_at_utc timestamptz;
ALTER TABLE execution.live_recovery_records
    ADD COLUMN IF NOT EXISTS response_bundle_id uuid,
    ADD COLUMN IF NOT EXISTS evidence_classification text NOT NULL DEFAULT 'legacy_untrusted',
    ADD COLUMN IF NOT EXISTS endpoint_matrix_sha256 text;
ALTER TABLE execution.live_order_observations
    ADD CONSTRAINT fk_live_order_observation_response_bundle FOREIGN KEY (
        response_bundle_id,live_run_id
    ) REFERENCES execution.live_okx_response_bundles(response_bundle_id,live_run_id) ON DELETE RESTRICT,
    ADD CONSTRAINT ck_live_order_observation_classification CHECK (
        evidence_classification IN ('operational_collector','fixture','imported','legacy_untrusted')
    );
ALTER TABLE execution.live_recovery_records
    ADD CONSTRAINT fk_live_recovery_response_bundle FOREIGN KEY (
        response_bundle_id,live_run_id
    ) REFERENCES execution.live_okx_response_bundles(response_bundle_id,live_run_id) ON DELETE RESTRICT,
    ADD CONSTRAINT ck_live_recovery_classification CHECK (
        evidence_classification IN ('operational_collector','fixture','imported','legacy_untrusted')
    );


CREATE TABLE IF NOT EXISTS execution.live_recovery_query_completions (
    recovery_record_id uuid NOT NULL REFERENCES execution.live_recovery_records(recovery_record_id) ON DELETE RESTRICT,
    response_bundle_id uuid NOT NULL,
    endpoint_kind text NOT NULL CHECK (endpoint_kind IN (
        'account_config','balances','positions','pending_orders',
        'order_history','fills','order_details'
    )),
    completed boolean NOT NULL,
    error_classification text,
    response_sha256 text,
    query_started_at_utc timestamptz NOT NULL,
    query_completed_at_utc timestamptz NOT NULL,
    record_sha256 text NOT NULL CHECK (record_sha256 ~ '^[0-9a-f]{64}$'),
    PRIMARY KEY (recovery_record_id,endpoint_kind),
    FOREIGN KEY (response_bundle_id,endpoint_kind)
        REFERENCES execution.live_okx_response_envelopes(response_bundle_id,endpoint_kind)
        ON DELETE RESTRICT,
    CHECK (query_completed_at_utc >= query_started_at_utc),
    CHECK ((completed AND error_classification IS NULL AND response_sha256 ~ '^[0-9a-f]{64}$')
        OR (NOT completed AND error_classification IS NOT NULL))
);

CREATE OR REPLACE FUNCTION execution.guard_live_collector_source()
RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
    calculated_payload_sha256 text;
BEGIN
    calculated_payload_sha256 := encode(sha256(convert_to(
        execution.live_canonical_jsonb_text(NEW.source_payload_jsonb),'UTF8'
    )),'hex');
    IF NEW.normalized_payload_sha256 IS DISTINCT FROM calculated_payload_sha256 THEN
        RAISE EXCEPTION 'preflight source normalized payload hash mismatch';
    END IF;
    IF NEW.operational AND (
        NEW.producer_classification<>'operational_collector'
        OR NEW.collector_kind IS NULL OR NEW.collector_version IS NULL
        OR NEW.source_system_identity IS NULL OR NEW.source_record_identity IS NULL
        OR NEW.raw_response_sha256 !~ '^[0-9a-f]{64}$'
        OR NEW.normalized_payload_sha256 !~ '^[0-9a-f]{64}$'
        OR NEW.source_schema_version IS NULL OR NEW.source_schema_version<1
    ) THEN
        RAISE EXCEPTION 'operational preflight source lacks collector-issued provenance';
    END IF;
    IF NEW.source_kind='migration_catalog' AND NEW.operational THEN
        IF NEW.collector_kind<>'repository_migration_catalog'
           OR NEW.source_payload_jsonb->>'catalog_clean'<>'true'
           OR NEW.source_payload_jsonb->>'immutable_0001_0023'<>'true'
           OR NEW.source_payload_jsonb->>'latest_migration'<>'0024'
           OR (SELECT count(*) FROM audit.schema_migrations WHERE migration_id<='0023_zzzz')<>23
           OR (CASE
                WHEN jsonb_typeof(NEW.source_payload_jsonb->'expected_hashes_0001_0023')='object'
                THEN (SELECT count(*) FROM jsonb_object_keys(NEW.source_payload_jsonb->'expected_hashes_0001_0023'))
                ELSE 0
              END)<>23
           OR (CASE
                WHEN jsonb_typeof(NEW.source_payload_jsonb->'observed_hashes')='object'
                THEN (SELECT count(*) FROM jsonb_object_keys(NEW.source_payload_jsonb->'observed_hashes'))
                ELSE 0
              END)<>(SELECT count(*) FROM audit.schema_migrations)
           OR EXISTS (
               SELECT 1 FROM audit.schema_migrations m
               WHERE m.migration_id<='0023_zzzz'
                 AND NEW.source_payload_jsonb->'expected_hashes_0001_0023'->>m.migration_id
                     IS DISTINCT FROM m.sha256::text
           )
           OR EXISTS (
               SELECT 1 FROM audit.schema_migrations m
               WHERE NEW.source_payload_jsonb->'observed_hashes'->>m.migration_id
                     IS DISTINCT FROM m.sha256::text
           ) THEN
            RAISE EXCEPTION 'migration source is not bound to exact immutable 0001-0023 catalog';
        END IF;
    END IF;
    IF NEW.source_kind='postgresql_probe' AND NEW.operational AND (
        NEW.collector_kind<>'postgresql_transaction_probe'
        OR NEW.source_payload_jsonb->>'available'<>'true'
        OR NEW.source_payload_jsonb->>'transaction_probe'<>'true'
    ) THEN
        RAISE EXCEPTION 'PostgreSQL source is not collector-probed';
    END IF;
    IF NEW.source_kind='audit_rollback_probe' AND NEW.operational AND (
        NEW.collector_kind<>'postgresql_rollback_probe'
        OR NEW.source_payload_jsonb->>'write_succeeded'<>'true'
        OR NEW.source_payload_jsonb->>'rollback_verified'<>'true'
    ) THEN
        RAISE EXCEPTION 'rollback source is not collector-probed';
    END IF;
    RETURN NEW;
END;
$$;
DROP TRIGGER IF EXISTS trg_live_collector_source ON execution.live_preflight_sources;
CREATE TRIGGER trg_live_collector_source BEFORE INSERT ON execution.live_preflight_sources
FOR EACH ROW EXECUTE FUNCTION execution.guard_live_collector_source();

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
    IF NEW.status IN ('passed','passed_for_reset') THEN
        IF NEW.authority_generation<>'collector_0024' THEN
            RAISE EXCEPTION 'passed preflight requires collector_0024 authority';
        END IF;
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

CREATE OR REPLACE FUNCTION execution.validate_live_0024_source_details()
RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
    kill execution.live_kill_switches%ROWTYPE;
    risk execution.live_run_risk_state%ROWTYPE;
    verified_okx_kinds integer;
BEGIN
    IF NEW.status NOT IN ('passed','passed_for_reset') THEN RETURN NEW; END IF;
    SELECT * INTO kill FROM execution.live_kill_switches WHERE live_run_id=NEW.live_run_id;
    SELECT * INTO risk FROM execution.live_run_risk_state WHERE live_run_id=NEW.live_run_id;

    IF NOT EXISTS (
        SELECT 1
        FROM execution.live_preflight_checks c
        JOIN execution.live_preflight_check_sources cs
          ON cs.preflight_check_id=c.preflight_check_id AND cs.live_run_id=c.live_run_id
        JOIN execution.live_preflight_sources s
          ON s.source_id=cs.source_id AND s.live_run_id=cs.live_run_id
        WHERE c.preflight_report_id=NEW.preflight_report_id
          AND s.source_kind='kill_switch' AND c.passed AND c.required
          AND s.source_payload_jsonb->>'kill_switch_id'=kill.kill_switch_id::text
          AND (s.source_payload_jsonb->>'version')::integer=kill.version
          AND s.source_payload_jsonb->>'state'=kill.state
          AND s.source_payload_jsonb->>'evidence_hash'=kill.evidence_sha256
    ) THEN RAISE EXCEPTION 'preflight kill evidence is not the current locked row'; END IF;

    IF NOT EXISTS (
        SELECT 1
        FROM execution.live_preflight_checks c
        JOIN execution.live_preflight_check_sources cs
          ON cs.preflight_check_id=c.preflight_check_id AND cs.live_run_id=c.live_run_id
        JOIN execution.live_preflight_sources s
          ON s.source_id=cs.source_id AND s.live_run_id=cs.live_run_id
        JOIN execution.live_reconciliations r
          ON r.reconciliation_id=risk.latest_reconciliation_id AND r.live_run_id=s.live_run_id
        WHERE c.preflight_report_id=NEW.preflight_report_id
          AND s.source_kind='reconciliation' AND c.passed AND c.required
          AND s.source_payload_jsonb->>'reconciliation_id'=r.reconciliation_id::text
          AND s.source_payload_jsonb->>'record_hash'=r.record_sha256
          AND s.source_payload_jsonb->>'status'=r.status
          AND r.producer_classification='operational_collector'
    ) THEN RAISE EXCEPTION 'preflight reconciliation evidence is not current operational authority'; END IF;

    IF NOT EXISTS (
        SELECT 1
        FROM execution.live_preflight_checks c
        JOIN execution.live_preflight_check_sources cs
          ON cs.preflight_check_id=c.preflight_check_id AND cs.live_run_id=c.live_run_id
        JOIN execution.live_market_source_bindings b
          ON b.source_id=cs.source_id AND b.live_run_id=cs.live_run_id
        JOIN market_data.validated_bars v ON v.bar_id=b.bar_id
        JOIN data_quality.validation_reports vr ON vr.validation_report_id=b.validation_report_id
        WHERE c.preflight_report_id=NEW.preflight_report_id
          AND b.source_id=risk.latest_market_evidence_id
          AND b.validation_report_id=v.validation_report_id
          AND b.validation_status=v.validation_status
          AND vr.status IN ('accepted','accepted_with_warnings')
          AND b.finality_verified AND b.quarantine_clear
          AND v.provenance_jsonb->>'is_final'='true'
          AND b.raw_observation_ids @> v.source_observation_ids
          AND v.source_observation_ids @> b.raw_observation_ids
          AND NOT EXISTS (
              SELECT 1 FROM unnest(b.raw_observation_ids) raw_id
              LEFT JOIN market_data.raw_source_observations raw ON raw.observation_id=raw_id
              WHERE raw.observation_id IS NULL
                 OR b.raw_observation_hashes_jsonb->>raw_id::text<>raw.source_sha256
          )
          AND NOT EXISTS (
              SELECT 1 FROM data_quality.quarantine_decisions q
              WHERE q.validation_report_id=b.validation_report_id
                 OR q.observation_id=ANY(b.raw_observation_ids)
          )
    ) THEN RAISE EXCEPTION 'preflight market evidence is not exact Phase 7 authority'; END IF;

    IF NOT EXISTS (
        SELECT 1
        FROM execution.live_preflight_checks c
        JOIN execution.live_preflight_check_sources cs
          ON cs.preflight_check_id=c.preflight_check_id AND cs.live_run_id=c.live_run_id
        JOIN execution.live_preflight_sources s
          ON s.source_id=cs.source_id AND s.live_run_id=cs.live_run_id
        JOIN execution.live_instrument_metadata_sources m
          ON m.source_id=s.source_id AND m.live_run_id=s.live_run_id
        JOIN execution.live_okx_response_bundles b
          ON b.response_bundle_id=m.response_bundle_id AND b.live_run_id=m.live_run_id
        JOIN execution.live_okx_response_envelopes e
          ON e.response_bundle_id=b.response_bundle_id AND e.endpoint_kind='instrument_metadata'
        WHERE c.preflight_report_id=NEW.preflight_report_id
          AND c.passed AND c.required AND s.source_kind='instrument_metadata'
          AND b.bundle_purpose='preflight' AND b.producer_classification='operational_collector'
          AND e.completed AND e.top_level_provider_code='0'
          AND e.canonical_response_sha256=m.provider_response_sha256
          AND s.raw_response_sha256=e.canonical_response_sha256
          AND m.instrument_state='live'
    ) THEN RAISE EXCEPTION 'preflight instrument metadata lacks exact OKX provenance'; END IF;

    SELECT count(DISTINCT s.source_kind) INTO verified_okx_kinds
    FROM execution.live_preflight_checks c
    JOIN execution.live_preflight_check_sources cs
      ON cs.preflight_check_id=c.preflight_check_id AND cs.live_run_id=c.live_run_id
    JOIN execution.live_preflight_sources s
      ON s.source_id=cs.source_id AND s.live_run_id=cs.live_run_id
    JOIN execution.live_okx_response_bundles b
      ON b.response_bundle_id=s.source_record_identity::uuid AND b.live_run_id=s.live_run_id
    JOIN execution.live_okx_response_envelopes e
      ON e.response_bundle_id=b.response_bundle_id
     AND e.endpoint_kind=CASE
        WHEN s.source_kind IN ('account_config','account_fingerprint','subaccount','account_mode','margin_borrowing') THEN 'account_config'
        WHEN s.source_kind='balances' THEN 'balances'
        WHEN s.source_kind='positions' THEN 'positions'
        WHEN s.source_kind='open_orders' THEN 'pending_orders'
        WHEN s.source_kind='venue_time' THEN 'venue_time'
        WHEN s.source_kind='instrument_metadata' THEN 'instrument_metadata'
     END
    WHERE c.preflight_report_id=NEW.preflight_report_id
      AND s.source_kind IN (
        'account_config','account_fingerprint','subaccount','account_mode','margin_borrowing',
        'balances','positions','open_orders','venue_time','instrument_metadata'
      )
      AND c.passed AND c.required
      AND b.bundle_purpose='preflight' AND b.producer_classification='operational_collector'
      AND e.completed AND e.top_level_provider_code='0'
      AND s.raw_response_sha256=e.canonical_response_sha256;
    IF verified_okx_kinds<>10 THEN
        RAISE EXCEPTION 'preflight OKX sources do not bind exact response envelopes';
    END IF;
    RETURN NEW;
END;
$$;
CREATE OR REPLACE FUNCTION execution.validate_live_preflight_graph()
RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
    source_count integer;
    kill execution.live_kill_switches%ROWTYPE;
    risk execution.live_run_risk_state%ROWTYPE;
BEGIN
    IF NEW.status NOT IN ('passed','passed_for_reset') THEN RETURN NEW; END IF;
    IF NEW.authority_generation<>'collector_0024' THEN
        RAISE EXCEPTION 'passed preflight requires collector_0024 authority';
    END IF;
    SELECT count(DISTINCT s.source_kind) INTO source_count
    FROM execution.live_preflight_checks c
    JOIN execution.live_preflight_check_sources cs
      ON cs.preflight_check_id=c.preflight_check_id AND cs.live_run_id=c.live_run_id
    JOIN execution.live_preflight_sources s
      ON s.source_id=cs.source_id AND s.live_run_id=cs.live_run_id
    WHERE c.preflight_report_id=NEW.preflight_report_id
      AND c.live_run_id=NEW.live_run_id AND c.passed AND c.required
      AND s.operational AND s.producer_classification='operational_collector';
    IF source_count<>19 THEN
        RAISE EXCEPTION 'passed preflight lacks all collector-issued operational source kinds';
    END IF;
    IF EXISTS (
        SELECT 1 FROM execution.live_preflight_check_sources cs
        JOIN execution.live_preflight_checks c ON c.preflight_check_id=cs.preflight_check_id
        JOIN execution.live_preflight_sources s ON s.source_id=cs.source_id
        WHERE c.preflight_report_id=NEW.preflight_report_id
          AND (cs.live_run_id<>NEW.live_run_id OR cs.source_sha256<>s.source_sha256)
    ) THEN RAISE EXCEPTION 'preflight check/source membership or hash mismatch'; END IF;
    SELECT * INTO kill FROM execution.live_kill_switches WHERE live_run_id=NEW.live_run_id;
    SELECT * INTO risk FROM execution.live_run_risk_state WHERE live_run_id=NEW.live_run_id;
    IF kill.kill_switch_id IS NULL OR risk.live_run_id IS NULL THEN
        RAISE EXCEPTION 'passed preflight requires current kill and risk rows';
    END IF;
    IF NEW.purpose='kill_reset' THEN
        IF NEW.status<>'passed_for_reset' OR kill.state<>'stopped' OR kill.triggered_at_utc IS NULL THEN
            RAISE EXCEPTION 'kill-reset preflight must bind the current stopped kill row';
        END IF;
    ELSIF NEW.status<>'passed' OR kill.state<>'armed' THEN
        RAISE EXCEPTION 'normal preflight must bind the current armed kill row';
    END IF;
    IF risk.latest_account_snapshot_id<>NEW.account_snapshot_id THEN
        RAISE EXCEPTION 'preflight account source is stale';
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM execution.live_market_source_bindings b
        WHERE b.source_id=risk.latest_market_evidence_id AND b.live_run_id=NEW.live_run_id
          AND b.finality_verified AND b.quarantine_clear
    ) THEN RAISE EXCEPTION 'preflight market source is not bound to Phase 7 authority'; END IF;
    IF NOT EXISTS (
        SELECT 1 FROM execution.live_instrument_metadata_sources m
        WHERE m.live_run_id=NEW.live_run_id AND m.instrument_state='live'
    ) THEN RAISE EXCEPTION 'preflight lacks live instrument metadata authority'; END IF;
    RETURN NEW;
END;
$$;
DROP TRIGGER IF EXISTS trg_validate_live_preflight_graph ON execution.live_preflight_reports;
CREATE CONSTRAINT TRIGGER trg_validate_live_preflight_graph
AFTER INSERT OR UPDATE ON execution.live_preflight_reports
DEFERRABLE INITIALLY DEFERRED FOR EACH ROW
EXECUTE FUNCTION execution.validate_live_preflight_graph();

DROP TRIGGER IF EXISTS trg_validate_live_0024_source_details ON execution.live_preflight_reports;
CREATE CONSTRAINT TRIGGER trg_validate_live_0024_source_details
AFTER INSERT OR UPDATE ON execution.live_preflight_reports
DEFERRABLE INITIALLY DEFERRED FOR EACH ROW
EXECUTE FUNCTION execution.validate_live_0024_source_details();

CREATE OR REPLACE FUNCTION execution.guard_live_0024_reconciliation()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.status='reconciled' AND (
        NEW.producer_classification<>'operational_collector'
        OR NEW.response_bundle_id IS NULL
        OR NEW.local_projection_as_of_utc IS NULL OR NEW.venue_observation_as_of_utc IS NULL
        OR NEW.query_started_at_utc IS NULL OR NEW.query_completed_at_utc IS NULL
        OR NEW.query_completed_at_utc<NEW.query_started_at_utc
    ) THEN RAISE EXCEPTION 'reconciled status requires an exact operational venue bundle'; END IF;
    RETURN NEW;
END;
$$;
DROP TRIGGER IF EXISTS trg_guard_live_0024_reconciliation ON execution.live_reconciliations;
CREATE TRIGGER trg_guard_live_0024_reconciliation BEFORE INSERT ON execution.live_reconciliations
FOR EACH ROW EXECUTE FUNCTION execution.guard_live_0024_reconciliation();

CREATE OR REPLACE FUNCTION execution.guard_live_0024_intent_metadata()
RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
    metadata execution.live_instrument_metadata_sources%ROWTYPE;
    source execution.live_preflight_sources%ROWTYPE;
BEGIN
    SELECT * INTO metadata FROM execution.live_instrument_metadata_sources
    WHERE source_id=NEW.instrument_metadata_source_id
      AND live_run_id=NEW.live_run_id AND instrument_id=NEW.instrument_id;
    SELECT * INTO source FROM execution.live_preflight_sources
    WHERE source_id=NEW.instrument_metadata_source_id AND live_run_id=NEW.live_run_id;
    IF metadata.source_id IS NULL OR source.source_id IS NULL
       OR NEW.metadata_authority_generation<>'collector_0024'
       OR NEW.instrument_metadata_sha256<>source.source_sha256
       OR NEW.instrument_metadata_parser_version<>metadata.parser_version
       OR metadata.instrument_state<>'live'
       OR NEW.quantity<metadata.minimum_size
       OR NEW.quantity*NEW.limit_price<metadata.minimum_notional
       OR mod(NEW.quantity,metadata.lot_size)<>0
       OR mod(NEW.limit_price,metadata.tick_size)<>0 THEN
        RAISE EXCEPTION 'live intent is not normalized by current verified instrument metadata';
    END IF;
    RETURN NEW;
END;
$$;
DROP TRIGGER IF EXISTS trg_guard_live_0024_intent_metadata ON execution.live_order_intents;
CREATE TRIGGER trg_guard_live_0024_intent_metadata
BEFORE INSERT ON execution.live_order_intents
FOR EACH ROW EXECUTE FUNCTION execution.guard_live_0024_intent_metadata();

CREATE OR REPLACE FUNCTION execution.guard_live_0024_outbox_metadata()
RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
    intent execution.live_order_intents%ROWTYPE;
BEGIN
    SELECT * INTO intent FROM execution.live_order_intents
    WHERE order_intent_id=NEW.order_intent_id AND live_run_id=NEW.live_run_id;
    IF intent.order_intent_id IS NULL
       OR NEW.metadata_authority_generation<>'collector_0024'
       OR NEW.instrument_metadata_source_id<>intent.instrument_metadata_source_id
       OR NEW.instrument_metadata_sha256<>intent.instrument_metadata_sha256
       OR NEW.instrument_metadata_parser_version<>intent.instrument_metadata_parser_version
       OR NEW.request_method<>'POST' OR NEW.request_path<>'/api/v5/trade/order'
       OR NEW.request_jsonb->>'instId'<>intent.instrument_id
       OR NEW.request_jsonb->>'clOrdId'<>intent.client_order_id
       OR (NEW.request_jsonb->>'sz')::numeric<>intent.quantity
       OR (NEW.request_jsonb->>'px')::numeric<>intent.limit_price THEN
        RAISE EXCEPTION 'live outbox request is not repository-derived metadata authority';
    END IF;
    RETURN NEW;
END;
$$;
DROP TRIGGER IF EXISTS trg_guard_live_0024_outbox_metadata ON execution.live_dispatch_outbox;
CREATE TRIGGER trg_guard_live_0024_outbox_metadata
BEFORE INSERT ON execution.live_dispatch_outbox
FOR EACH ROW EXECUTE FUNCTION execution.guard_live_0024_outbox_metadata();

CREATE OR REPLACE FUNCTION execution.validate_live_0024_reconciliation_exact()
RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
    completed_count integer;
BEGIN
    IF NEW.producer_classification<>'operational_collector' THEN
        IF NEW.status='reconciled' THEN
            RAISE EXCEPTION 'untrusted reconciliation cannot be reconciled';
        END IF;
        RETURN NEW;
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM execution.live_okx_response_bundles b
        WHERE b.response_bundle_id=NEW.response_bundle_id
          AND b.live_run_id=NEW.live_run_id
          AND (b.bundle_purpose='reconciliation' OR (NEW.status='blocked' AND b.bundle_purpose='recovery'))
          AND b.producer_classification='operational_collector'
          AND b.endpoint_matrix_sha256 ~ '^[0-9a-f]{64}$'
    ) THEN RAISE EXCEPTION 'reconciliation response bundle is not operational authority'; END IF;
    SELECT count(*) INTO completed_count
    FROM execution.live_okx_response_envelopes e
    WHERE e.response_bundle_id=NEW.response_bundle_id
      AND e.endpoint_kind IN (
        'account_config','balances','positions','pending_orders',
        'order_history','fills','venue_time'
      )
      AND e.completed AND e.top_level_provider_code='0';
    IF NEW.status='reconciled' AND completed_count<>7 THEN
        RAISE EXCEPTION 'reconciled status requires all seven exact read endpoints';
    END IF;
    IF NEW.local_projection_as_of_utc>NEW.evaluated_at_utc
       OR NEW.venue_observation_as_of_utc>NEW.evaluated_at_utc
       OR NEW.query_completed_at_utc>NEW.evaluated_at_utc
       OR NEW.query_completed_at_utc<NEW.query_started_at_utc THEN
        RAISE EXCEPTION 'reconciliation timestamps are invalid or future-dated';
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM execution.live_reconciliation_input_bundles i
        WHERE i.reconciliation_id=NEW.reconciliation_id
          AND i.live_run_id=NEW.live_run_id
          AND i.response_bundle_id=NEW.response_bundle_id
    ) THEN RAISE EXCEPTION 'reconciliation lacks an exact immutable input bundle'; END IF;
    RETURN NEW;
END;
$$;
DROP TRIGGER IF EXISTS trg_validate_live_0024_reconciliation_exact ON execution.live_reconciliations;
CREATE CONSTRAINT TRIGGER trg_validate_live_0024_reconciliation_exact
AFTER INSERT ON execution.live_reconciliations
DEFERRABLE INITIALLY DEFERRED FOR EACH ROW
EXECUTE FUNCTION execution.validate_live_0024_reconciliation_exact();

CREATE OR REPLACE FUNCTION execution.guard_live_risk_state_transition()
RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
    reconciliation execution.live_reconciliations%ROWTYPE;
BEGIN
    IF TG_OP='DELETE' OR NEW.live_run_id<>OLD.live_run_id THEN
        RAISE EXCEPTION 'live risk state identity cannot change';
    END IF;
    IF NEW.version<>OLD.version+1 THEN
        RAISE EXCEPTION 'live risk state version must advance exactly once';
    END IF;
    IF NEW.latest_local_sequence<OLD.latest_local_sequence
       OR NEW.latest_venue_sequence<OLD.latest_venue_sequence THEN
        RAISE EXCEPTION 'runtime reconciliation sequence cannot regress';
    END IF;
    IF NEW.latest_reconciliation_id IS DISTINCT FROM OLD.latest_reconciliation_id THEN
        SELECT * INTO reconciliation FROM execution.live_reconciliations
        WHERE reconciliation_id=NEW.latest_reconciliation_id AND live_run_id=NEW.live_run_id;
        IF reconciliation.reconciliation_id IS NULL
           OR reconciliation.status<>NEW.latest_reconciliation_status
           OR reconciliation.evaluated_at_utc<>NEW.latest_reconciliation_at_utc THEN
            RAISE EXCEPTION 'runtime risk state does not match exact reconciliation authority';
        END IF;
        IF NEW.latest_reconciliation_status='reconciled' AND (
            reconciliation.producer_classification<>'operational_collector'
            OR NEW.latest_reconciliation_input_bundle_id IS NULL
            OR NOT EXISTS (
                SELECT 1 FROM execution.live_reconciliation_input_bundles i
                WHERE i.reconciliation_input_bundle_id=NEW.latest_reconciliation_input_bundle_id
                  AND i.reconciliation_id=NEW.latest_reconciliation_id
                  AND i.live_run_id=NEW.live_run_id
            )
        ) THEN RAISE EXCEPTION 'reconciled runtime risk state lacks exact input authority'; END IF;
    END IF;
    RETURN NEW;
END;
$$;

CREATE OR REPLACE FUNCTION execution.validate_live_0024_recovery_outcome()
RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
    total_count integer;
    matched_count integer;
    completed_count integer;
    explicit_rejections integer;
BEGIN
    IF NEW.outcome IS NULL THEN RETURN NEW; END IF;
    IF NEW.evidence_classification<>'operational_collector'
       OR NEW.response_bundle_id IS NULL
       OR NOT EXISTS (
           SELECT 1 FROM execution.live_okx_response_bundles b
           WHERE b.response_bundle_id=NEW.response_bundle_id
             AND b.live_run_id=NEW.live_run_id
             AND b.bundle_purpose='recovery'
             AND b.producer_classification='operational_collector'
       ) THEN RAISE EXCEPTION 'operational recovery outcome requires exact adapter authority'; END IF;
    SELECT
        count(*),
        count(*) FILTER (WHERE
            q.completed IS NOT DISTINCT FROM e.completed
            AND q.error_classification IS NOT DISTINCT FROM e.error_classification
            AND q.response_sha256 IS NOT DISTINCT FROM e.canonical_response_sha256
            AND q.query_started_at_utc=e.query_started_at_utc
            AND q.query_completed_at_utc=e.query_completed_at_utc
        ),
        count(*) FILTER (WHERE q.completed),
        count(*) FILTER (WHERE q.error_classification='explicit_provider_rejection')
    INTO total_count,matched_count,completed_count,explicit_rejections
    FROM execution.live_recovery_query_completions q
    JOIN execution.live_okx_response_envelopes e
      ON e.response_bundle_id=NEW.response_bundle_id
     AND e.endpoint_kind=q.endpoint_kind
    WHERE q.recovery_record_id=NEW.recovery_record_id
      AND q.response_bundle_id=NEW.response_bundle_id;
    IF total_count<>7 OR matched_count<>7 THEN
        RAISE EXCEPTION 'recovery completion matrix does not exactly match its response envelopes';
    END IF;
    IF NEW.outcome='confirmed_absent' AND completed_count<>7 THEN
        RAISE EXCEPTION 'confirmed_absent requires all seven recovery queries';
    END IF;
    IF NEW.outcome='provider_rejected' AND explicit_rejections=0 THEN
        RAISE EXCEPTION 'provider_rejected requires an explicit parsed provider response';
    END IF;
    RETURN NEW;
END;
$$;
DROP TRIGGER IF EXISTS trg_validate_live_0024_recovery_outcome ON execution.live_recovery_records;
CREATE CONSTRAINT TRIGGER trg_validate_live_0024_recovery_outcome
AFTER INSERT OR UPDATE ON execution.live_recovery_records
DEFERRABLE INITIALLY DEFERRED FOR EACH ROW
EXECUTE FUNCTION execution.validate_live_0024_recovery_outcome();

CREATE OR REPLACE FUNCTION execution.guard_live_0024_kill_reset()
RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
    report execution.live_preflight_reports%ROWTYPE;
    approval execution.live_approvals%ROWTYPE;
BEGIN
    IF OLD.state='stopped' AND NEW.state='reset_pending' THEN
        SELECT * INTO report FROM execution.live_preflight_reports
        WHERE preflight_report_id=NEW.reset_preflight_report_id AND live_run_id=NEW.live_run_id;
        SELECT * INTO approval FROM execution.live_approvals
        WHERE approval_id=NEW.reset_approval_id AND live_run_id=NEW.live_run_id;
        IF OLD.triggered_at_utc IS NULL
           OR report.preflight_report_id IS NULL
           OR report.status<>'passed_for_reset' OR report.purpose<>'kill_reset'
           OR report.authority_generation<>'collector_0024'
           OR report.evaluated_at_utc<=OLD.triggered_at_utc
           OR approval.approval_id IS NULL
           OR approval.preflight_report_id<>report.preflight_report_id
           OR approval.created_at_utc<report.evaluated_at_utc
           OR approval.consumed_notional<>0
           OR NOT EXISTS (
               SELECT 1
               FROM execution.live_preflight_checks c
               JOIN execution.live_preflight_check_sources cs
                 ON cs.preflight_check_id=c.preflight_check_id AND cs.live_run_id=c.live_run_id
               JOIN execution.live_preflight_sources s
                 ON s.source_id=cs.source_id AND s.live_run_id=cs.live_run_id
               WHERE c.preflight_report_id=report.preflight_report_id
                 AND c.check_name='kill_switch' AND c.passed AND c.required
                 AND s.source_kind='kill_switch'
                 AND s.source_payload_jsonb->>'state'='stopped'
                 AND s.source_payload_jsonb->>'kill_switch_id'=OLD.kill_switch_id::text
                 AND (s.source_payload_jsonb->>'version')::integer=OLD.version
           ) THEN RAISE EXCEPTION 'kill reset lacks exact stopped-row, reset report, or approval authority'; END IF;
    ELSIF OLD.state='reset_pending' AND NEW.state='armed' THEN
        IF OLD.reset_preflight_report_id IS NULL OR OLD.reset_approval_id IS NULL
           OR NEW.reset_preflight_report_id<>OLD.reset_preflight_report_id
           OR NEW.reset_approval_id<>OLD.reset_approval_id THEN
            RAISE EXCEPTION 'kill reset_pending to armed lost its reset authority';
        END IF;
    END IF;
    RETURN NEW;
END;
$$;
DROP TRIGGER IF EXISTS trg_guard_live_0024_kill_reset ON execution.live_kill_switches;
CREATE TRIGGER trg_guard_live_0024_kill_reset
BEFORE UPDATE ON execution.live_kill_switches
FOR EACH ROW EXECUTE FUNCTION execution.guard_live_0024_kill_reset();

DO $$
DECLARE table_name text;
BEGIN
    FOREACH table_name IN ARRAY ARRAY[
        'live_okx_response_bundles','live_okx_response_envelopes',
        'live_market_source_bindings','live_instrument_metadata_sources',
        'live_reconciliation_input_bundles','live_recovery_query_completions'
    ] LOOP
        EXECUTE format('DROP TRIGGER IF EXISTS trg_%I_immutable ON execution.%I',table_name,table_name);
        EXECUTE format('CREATE TRIGGER trg_%I_immutable BEFORE UPDATE OR DELETE ON execution.%I FOR EACH ROW EXECUTE FUNCTION execution.prevent_live_authority_mutation()',table_name,table_name);
    END LOOP;
END;
$$;

CREATE INDEX IF NOT EXISTS idx_live_okx_bundle_run_purpose
    ON execution.live_okx_response_bundles(live_run_id,bundle_purpose,query_completed_at_utc DESC);
CREATE INDEX IF NOT EXISTS idx_live_metadata_run_instrument
    ON execution.live_instrument_metadata_sources(live_run_id,instrument_id,collected_at_utc DESC);
CREATE INDEX IF NOT EXISTS idx_live_recovery_query_matrix
    ON execution.live_recovery_query_completions(recovery_record_id,completed);
