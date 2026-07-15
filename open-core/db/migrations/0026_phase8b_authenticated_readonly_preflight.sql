-- Phase 8B explicit authenticated read-only OKX preflight proof.
-- Migrations 0001 through 0025 are immutable. PostgreSQL remains authoritative.
-- This migration is necessary because the 0022-0025 schema stores private response bundles but
-- has no standalone, replay-safe, database-derived public proof for an operator-requested read.
-- Production submission, cancellation, withdrawal, transfer, borrowing, leverage, and derivatives
-- remain disabled and unimplemented.

ALTER TABLE execution.live_okx_response_bundles
    ADD COLUMN IF NOT EXISTS transport_is_fake boolean NOT NULL DEFAULT true;

-- Advance the existing operational migration-catalog guard to immutable 0001-0025 plus applied 0026.
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
           OR NEW.collector_version<>'phase8a-0025-v1'
           OR NEW.source_payload_jsonb->>'catalog_clean'<>'true'
           OR NEW.source_payload_jsonb->>'immutable_0001_0025'<>'true'
           OR NEW.source_payload_jsonb->>'latest_migration'<>'0026'
           OR (SELECT count(*) FROM audit.schema_migrations WHERE migration_id<='0025_zzzz')<>25
           OR (CASE
                WHEN jsonb_typeof(NEW.source_payload_jsonb->'expected_hashes_0001_0025')='object'
                THEN (SELECT count(*) FROM jsonb_object_keys(NEW.source_payload_jsonb->'expected_hashes_0001_0025'))
                ELSE 0
              END)<>25
           OR (CASE
                WHEN jsonb_typeof(NEW.source_payload_jsonb->'observed_hashes')='object'
                THEN (SELECT count(*) FROM jsonb_object_keys(NEW.source_payload_jsonb->'observed_hashes'))
                ELSE 0
              END)<>(SELECT count(*) FROM audit.schema_migrations)
           OR EXISTS (
               SELECT 1 FROM audit.schema_migrations m
               WHERE m.migration_id<='0025_zzzz'
                 AND NEW.source_payload_jsonb->'expected_hashes_0001_0025'->>m.migration_id
                     IS DISTINCT FROM m.sha256::text
           )
           OR EXISTS (
               SELECT 1 FROM audit.schema_migrations m
               WHERE NEW.source_payload_jsonb->'observed_hashes'->>m.migration_id
                     IS DISTINCT FROM m.sha256::text
           ) THEN
            RAISE EXCEPTION 'migration source is not bound to exact immutable 0001-0025 catalog';
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
    IF NEW.source_kind='credential_permissions' AND NEW.operational AND (
        NEW.collector_kind<>'okx_account_config_permission_collector'
        OR NEW.collector_version<>'phase8a-0025-v1'
        OR NEW.parser_version<>'okx-v5-parser-v4'
        OR jsonb_typeof(NEW.source_payload_jsonb)<>'object'
        OR jsonb_typeof(NEW.source_payload_jsonb->'provider_permissions')<>'array'
        OR jsonb_typeof(NEW.source_payload_jsonb->'normalized_permissions')<>'array'
        OR jsonb_typeof(NEW.source_payload_jsonb->'expected_permissions')<>'array'
        OR NEW.source_payload_jsonb->>'policy_version'<>'phase8a-read-only-v1'
    ) THEN
        RAISE EXCEPTION 'credential permission source lacks exact Phase 8A collector provenance';
    END IF;
    RETURN NEW;
END;
$$;

CREATE TABLE IF NOT EXISTS execution.live_authenticated_readonly_proofs (
    proof_id uuid PRIMARY KEY,
    proof_session_id uuid NOT NULL UNIQUE,
    response_bundle_id uuid NOT NULL,
    configuration_sha256 text NOT NULL REFERENCES execution.live_configuration_snapshots(configuration_sha256) ON DELETE RESTRICT,
    credential_reference_id uuid NOT NULL REFERENCES execution.live_credential_references(credential_reference_id) ON DELETE RESTRICT,
    expected_reviewed_sha text NOT NULL CHECK (expected_reviewed_sha ~ '^[0-9a-f]{40}$'),
    observed_repository_sha text NOT NULL CHECK (observed_repository_sha ~ '^[0-9a-f]{40}$'),
    repository_identity_source text NOT NULL CHECK (repository_identity_source IN ('git_checkout','build_metadata','verified_ci')),
    account_fingerprint text NOT NULL CHECK (account_fingerprint ~ '^[0-9a-f]{16}$'),
    credential_source text NOT NULL CHECK (credential_source IN ('environment','os_credential_store','injected_local')),
    provider_permissions_jsonb jsonb NOT NULL CHECK (provider_permissions_jsonb='["read_only"]'::jsonb),
    normalized_permissions_jsonb jsonb NOT NULL CHECK (normalized_permissions_jsonb='["read"]'::jsonb),
    instrument_id text NOT NULL,
    query_started_at_utc timestamptz NOT NULL,
    query_completed_at_utc timestamptz NOT NULL,
    venue_time_at_utc timestamptz NOT NULL,
    clock_skew_milliseconds bigint NOT NULL CHECK (clock_skew_milliseconds>=0),
    network_read_count integer NOT NULL CHECK (network_read_count=6),
    evidence_classification text NOT NULL CHECK (evidence_classification IN ('operational_collector','fixture')),
    status text NOT NULL CHECK (status IN ('passed','fixture_passed')),
    public_proof_jsonb jsonb NOT NULL,
    record_sha256 text NOT NULL CHECK (record_sha256 ~ '^[0-9a-f]{64}$'),
    created_at_utc timestamptz NOT NULL,
    CONSTRAINT fk_live_authenticated_readonly_bundle
        FOREIGN KEY (response_bundle_id,proof_session_id)
        REFERENCES execution.live_okx_response_bundles(response_bundle_id,live_run_id)
        ON DELETE RESTRICT,
    CHECK (query_completed_at_utc>=query_started_at_utc),
    CHECK (
        (status='passed' AND evidence_classification='operational_collector')
        OR (status='fixture_passed' AND evidence_classification='fixture')
    )
);

CREATE OR REPLACE FUNCTION execution.validate_live_authenticated_readonly_proof()
RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
    bundle execution.live_okx_response_bundles%ROWTYPE;
    configuration execution.live_configuration_snapshots%ROWTYPE;
    credential execution.live_credential_references%ROWTYPE;
    account_envelope execution.live_okx_response_envelopes%ROWTYPE;
    instrument_envelope execution.live_okx_response_envelopes%ROWTYPE;
    account_row jsonb;
    instrument_row jsonb;
    balance_details jsonb;
    observed_paths jsonb;
    observed_hashes jsonb;
    observed_currencies jsonb;
    observed_balance_count integer;
    observed_position_count integer;
    observed_open_order_count integer;
    observed_venue_time timestamptz;
    observed_fingerprint text;
    observed_classification text;
    calculated_record_sha256 text;
    public_key_count integer;
    completed_count integer;
BEGIN
    SELECT * INTO bundle FROM execution.live_okx_response_bundles
    WHERE response_bundle_id=NEW.response_bundle_id AND live_run_id=NEW.proof_session_id;
    SELECT * INTO configuration FROM execution.live_configuration_snapshots
    WHERE configuration_sha256=NEW.configuration_sha256;
    SELECT * INTO credential FROM execution.live_credential_references
    WHERE credential_reference_id=NEW.credential_reference_id;

    IF bundle.response_bundle_id IS NULL
       OR configuration.configuration_snapshot_id IS NULL
       OR credential.credential_reference_id IS NULL THEN
        RAISE EXCEPTION 'authenticated read-only proof authority graph is incomplete';
    END IF;
    IF bundle.bundle_purpose<>'preflight'
       OR bundle.producer_classification<>'operational_collector'
       OR bundle.collector_kind<>'okx_production_spot_read_adapter'
       OR bundle.collector_version<>'phase8a-0025-v1'
       OR bundle.parser_version<>'okx-v5-parser-v4'
       OR bundle.account_fingerprint<>NEW.account_fingerprint
       OR bundle.query_started_at_utc<>NEW.query_started_at_utc
       OR bundle.query_completed_at_utc<>NEW.query_completed_at_utc
       OR bundle.venue_observed_at_utc<>NEW.venue_time_at_utc THEN
        RAISE EXCEPTION 'authenticated read-only proof bundle provenance mismatch';
    END IF;
    IF (NEW.status='passed' AND bundle.transport_is_fake)
       OR (NEW.status='fixture_passed' AND NOT bundle.transport_is_fake) THEN
        RAISE EXCEPTION 'authenticated read-only proof transport classification mismatch';
    END IF;
    IF configuration.provider<>'okx'
       OR configuration.environment<>'production'
       OR NOT configuration.dry_run
       OR NOT configuration.read_only_preflight
       OR configuration.production_write_enabled
       OR configuration.account_fingerprint<>NEW.account_fingerprint
       OR configuration.configuration_jsonb->>'endpoint_catalog_hash' IS NULL
       OR configuration.configuration_jsonb->>'provider_implementation_hash' IS NULL
       OR NOT (configuration.configuration_jsonb->'allowed_instruments' @> jsonb_build_array(NEW.instrument_id))
       OR NOT (configuration.configuration_jsonb->'credential_source_policy' @> jsonb_build_array(NEW.credential_source)) THEN
        RAISE EXCEPTION 'authenticated read-only proof configuration mismatch';
    END IF;
    IF credential.provider<>'okx'
       OR credential.source_type<>NEW.credential_source
       OR credential.account_fingerprint<>NEW.account_fingerprint
       OR NOT credential.loaded
       OR credential.verified_at_utc IS NULL
       OR credential.permission_summary_jsonb<>'["read"]'::jsonb THEN
        RAISE EXCEPTION 'authenticated read-only proof credential reference mismatch';
    END IF;

    SELECT count(*) FILTER (
               WHERE completed AND top_level_provider_code='0'
                 AND error_classification IS NULL AND request_method='GET'
           ),
           jsonb_agg(to_jsonb(request_path) ORDER BY endpoint_kind),
           jsonb_object_agg(endpoint_kind,canonical_response_sha256 ORDER BY endpoint_kind)
    INTO completed_count,observed_paths,observed_hashes
    FROM execution.live_okx_response_envelopes
    WHERE response_bundle_id=NEW.response_bundle_id
      AND endpoint_kind IN ('account_config','balances','instrument_metadata','pending_orders','positions','venue_time');
    IF completed_count<>6
       OR (SELECT count(*) FROM execution.live_okx_response_envelopes WHERE response_bundle_id=NEW.response_bundle_id)<>6
       OR observed_paths<>jsonb_build_array(
            '/api/v5/account/config',
            '/api/v5/account/balance',
            '/api/v5/public/instruments?instId='||NEW.instrument_id||'&instType=SPOT',
            '/api/v5/trade/orders-pending?instId='||NEW.instrument_id||'&instType=SPOT',
            '/api/v5/account/positions',
            '/api/v5/public/time'
       ) THEN
        RAISE EXCEPTION 'authenticated read-only proof endpoint matrix is not the exact six GETs';
    END IF;

    SELECT * INTO account_envelope FROM execution.live_okx_response_envelopes
    WHERE response_bundle_id=NEW.response_bundle_id AND endpoint_kind='account_config';
    IF jsonb_typeof(account_envelope.raw_response_jsonb->'data')<>'array'
       OR jsonb_array_length(account_envelope.raw_response_jsonb->'data')<>1 THEN
        RAISE EXCEPTION 'authenticated read-only account-config response shape is invalid';
    END IF;
    account_row:=account_envelope.raw_response_jsonb#>'{data,0}';
    IF NOT account_row ?& ARRAY['uid','mainUid','perm','acctLv','posMode','autoLoan','enableSpotBorrow']
       OR account_row->>'perm'<>'read_only'
       OR account_row->>'acctLv'<>'1'
       OR lower(account_row->>'autoLoan') NOT IN ('false','0')
       OR lower(account_row->>'enableSpotBorrow') NOT IN ('false','0') THEN
        RAISE EXCEPTION 'authenticated read-only account authority is not exact cash/read_only';
    END IF;
    observed_fingerprint:=substring(encode(sha256(convert_to(
        execution.live_canonical_jsonb_text(jsonb_build_object(
            'provider','okx','account_uid',account_row->>'uid'
        )),'UTF8')),'hex') from 1 for 16);
    observed_classification:=CASE WHEN account_row->>'uid'=account_row->>'mainUid'
        THEN 'main_account' ELSE 'subaccount' END;
    IF observed_fingerprint<>NEW.account_fingerprint
       OR credential.verified_at_utc<>account_envelope.query_completed_at_utc THEN
        RAISE EXCEPTION 'authenticated read-only account fingerprint or verification time mismatch';
    END IF;

    SELECT raw_response_jsonb#>'{data,0,details}' INTO balance_details
    FROM execution.live_okx_response_envelopes
    WHERE response_bundle_id=NEW.response_bundle_id AND endpoint_kind='balances';
    IF jsonb_typeof(balance_details)<>'array' THEN
        RAISE EXCEPTION 'authenticated read-only balance response shape is invalid';
    END IF;
    SELECT COALESCE(jsonb_agg(to_jsonb(currency) ORDER BY currency),'[]'::jsonb),count(*)
    INTO observed_currencies,observed_balance_count
    FROM (SELECT DISTINCT value->>'ccy' AS currency FROM jsonb_array_elements(balance_details) value) currencies;
    IF observed_balance_count<>jsonb_array_length(balance_details) THEN
        RAISE EXCEPTION 'authenticated read-only balance currencies are duplicated';
    END IF;
    SELECT jsonb_array_length(raw_response_jsonb->'data') INTO observed_position_count
    FROM execution.live_okx_response_envelopes
    WHERE response_bundle_id=NEW.response_bundle_id AND endpoint_kind='positions';
    IF observed_position_count IS DISTINCT FROM 0 THEN
        RAISE EXCEPTION 'authenticated read-only proof requires an empty positions response';
    END IF;

    SELECT jsonb_array_length(raw_response_jsonb->'data') INTO observed_open_order_count
    FROM execution.live_okx_response_envelopes
    WHERE response_bundle_id=NEW.response_bundle_id AND endpoint_kind='pending_orders';
    SELECT * INTO instrument_envelope FROM execution.live_okx_response_envelopes
    WHERE response_bundle_id=NEW.response_bundle_id AND endpoint_kind='instrument_metadata';
    IF jsonb_typeof(instrument_envelope.raw_response_jsonb->'data')<>'array'
       OR jsonb_array_length(instrument_envelope.raw_response_jsonb->'data')<>1 THEN
        RAISE EXCEPTION 'authenticated read-only instrument response shape is invalid';
    END IF;
    instrument_row:=instrument_envelope.raw_response_jsonb#>'{data,0}';
    IF instrument_row->>'instType'<>'SPOT'
       OR instrument_row->>'instId'<>NEW.instrument_id
       OR lower(instrument_row->>'state')<>'live' THEN
        RAISE EXCEPTION 'authenticated read-only instrument identity or state mismatch';
    END IF;
    SELECT to_timestamp(((raw_response_jsonb#>>'{data,0,ts}')::numeric/1000)::double precision)
    INTO observed_venue_time
    FROM execution.live_okx_response_envelopes
    WHERE response_bundle_id=NEW.response_bundle_id AND endpoint_kind='venue_time';
    IF observed_venue_time IS DISTINCT FROM NEW.venue_time_at_utc
       OR NEW.clock_skew_milliseconds<>
          floor(abs(extract(epoch FROM (NEW.venue_time_at_utc-NEW.query_completed_at_utc)))*1000)::bigint
       OR NEW.clock_skew_milliseconds>(configuration.configuration_jsonb->>'maximum_clock_skew_seconds')::bigint*1000 THEN
        RAISE EXCEPTION 'authenticated read-only venue time or clock skew mismatch';
    END IF;

    SELECT count(*) INTO public_key_count FROM jsonb_object_keys(NEW.public_proof_jsonb);
    IF public_key_count<>39
       OR NOT NEW.public_proof_jsonb ?& ARRAY[
          'proof_id','proof_session_id','response_bundle_id','configuration_hash',
          'provider_implementation_hash','endpoint_catalog_hash',
          'credential_reference_id','expected_reviewed_sha','observed_repository_sha',
          'repository_identity_source','account_fingerprint','account_classification',
          'credential_source','provider_permissions','normalized_permissions','queried_paths',
          'endpoint_response_hashes','query_started_at_utc','query_completed_at_utc',
          'venue_time_at_utc','clock_skew_milliseconds','balance_currencies',
          'balance_currency_count','position_count','open_order_count','instrument_id',
          'instrument_state','instrument_metadata_response_hash','network_read_count',
          'network_reads_occurred','network_writes_occurred','production_write_status',
          'preflight_mode','evidence_classification','status','blockers','warnings',
          'private_evidence_storage','record_hash'
       ] THEN
        RAISE EXCEPTION 'authenticated read-only public proof keys are not exact';
    END IF;
    calculated_record_sha256:=encode(sha256(convert_to(
        execution.live_canonical_jsonb_text(NEW.public_proof_jsonb-'record_hash'),'UTF8'
    )),'hex');
    IF calculated_record_sha256<>NEW.record_sha256
       OR NEW.public_proof_jsonb->>'record_hash'<>NEW.record_sha256
       OR NEW.public_proof_jsonb->>'proof_id'<>NEW.proof_id::text
       OR NEW.public_proof_jsonb->>'proof_session_id'<>NEW.proof_session_id::text
       OR NEW.public_proof_jsonb->>'response_bundle_id'<>NEW.response_bundle_id::text
       OR NEW.public_proof_jsonb->>'configuration_hash'<>NEW.configuration_sha256
       OR NEW.public_proof_jsonb->>'provider_implementation_hash'<>
          configuration.configuration_jsonb->>'provider_implementation_hash'
       OR NEW.public_proof_jsonb->>'endpoint_catalog_hash'<>
          configuration.configuration_jsonb->>'endpoint_catalog_hash'
       OR NEW.public_proof_jsonb->>'credential_reference_id'<>NEW.credential_reference_id::text
       OR NEW.public_proof_jsonb->>'expected_reviewed_sha'<>NEW.expected_reviewed_sha
       OR NEW.public_proof_jsonb->>'observed_repository_sha'<>NEW.observed_repository_sha
       OR NEW.expected_reviewed_sha<>NEW.observed_repository_sha
       OR NEW.public_proof_jsonb->>'repository_identity_source'<>NEW.repository_identity_source
       OR NEW.public_proof_jsonb->>'account_fingerprint'<>NEW.account_fingerprint
       OR NEW.public_proof_jsonb->>'account_classification'<>observed_classification
       OR NEW.public_proof_jsonb->>'credential_source'<>NEW.credential_source
       OR NEW.public_proof_jsonb->'provider_permissions'<>NEW.provider_permissions_jsonb
       OR NEW.public_proof_jsonb->'normalized_permissions'<>NEW.normalized_permissions_jsonb
       OR NEW.public_proof_jsonb->'queried_paths'<>observed_paths
       OR NEW.public_proof_jsonb->'endpoint_response_hashes'<>observed_hashes
       OR (NEW.public_proof_jsonb->>'query_started_at_utc')::timestamptz<>NEW.query_started_at_utc
       OR (NEW.public_proof_jsonb->>'query_completed_at_utc')::timestamptz<>NEW.query_completed_at_utc
       OR (NEW.public_proof_jsonb->>'venue_time_at_utc')::timestamptz<>NEW.venue_time_at_utc
       OR (NEW.public_proof_jsonb->>'clock_skew_milliseconds')::bigint<>NEW.clock_skew_milliseconds
       OR NEW.public_proof_jsonb->'balance_currencies'<>observed_currencies
       OR (NEW.public_proof_jsonb->>'balance_currency_count')::integer<>observed_balance_count
       OR (NEW.public_proof_jsonb->>'position_count')::integer<>observed_position_count
       OR (NEW.public_proof_jsonb->>'open_order_count')::integer<>observed_open_order_count
       OR NEW.public_proof_jsonb->>'instrument_id'<>NEW.instrument_id
       OR NEW.public_proof_jsonb->>'instrument_state'<>'live'
       OR NEW.public_proof_jsonb->>'instrument_metadata_response_hash'<>instrument_envelope.canonical_response_sha256
       OR (NEW.public_proof_jsonb->>'network_read_count')::integer<>NEW.network_read_count
       OR NEW.public_proof_jsonb->>'network_reads_occurred'<>'true'
       OR NEW.public_proof_jsonb->>'network_writes_occurred'<>'false'
       OR NEW.public_proof_jsonb->>'production_write_status'<>'disabled'
       OR NEW.public_proof_jsonb->>'preflight_mode'<>'AUTHENTICATED READ-ONLY'
       OR NEW.public_proof_jsonb->>'evidence_classification'<>NEW.evidence_classification
       OR NEW.public_proof_jsonb->>'status'<>NEW.status
       OR NEW.public_proof_jsonb->'blockers'<>'[]'::jsonb
       OR jsonb_typeof(NEW.public_proof_jsonb->'warnings')<>'array'
       OR NEW.public_proof_jsonb->>'private_evidence_storage'<>'postgresql' THEN
        RAISE EXCEPTION 'authenticated read-only public proof is forged or not response-derived';
    END IF;
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS trg_validate_live_authenticated_readonly_proof
ON execution.live_authenticated_readonly_proofs;
CREATE CONSTRAINT TRIGGER trg_validate_live_authenticated_readonly_proof
AFTER INSERT ON execution.live_authenticated_readonly_proofs
DEFERRABLE INITIALLY DEFERRED FOR EACH ROW
EXECUTE FUNCTION execution.validate_live_authenticated_readonly_proof();

DROP TRIGGER IF EXISTS trg_live_authenticated_readonly_proofs_immutable
ON execution.live_authenticated_readonly_proofs;
CREATE TRIGGER trg_live_authenticated_readonly_proofs_immutable
BEFORE UPDATE OR DELETE ON execution.live_authenticated_readonly_proofs
FOR EACH ROW EXECUTE FUNCTION execution.prevent_live_authority_mutation();

CREATE INDEX IF NOT EXISTS idx_live_authenticated_readonly_account_time
ON execution.live_authenticated_readonly_proofs(account_fingerprint,query_completed_at_utc DESC);