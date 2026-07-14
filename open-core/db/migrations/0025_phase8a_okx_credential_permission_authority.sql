-- Phase 8A exact OKX credential-permission authority.
-- Migrations 0001 through 0024 are immutable. PostgreSQL remains authoritative.
-- Production submission and cancellation remain unconditionally unreachable.

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
           OR NEW.source_payload_jsonb->>'immutable_0001_0024'<>'true'
           OR NEW.source_payload_jsonb->>'latest_migration'<>'0025'
           OR (SELECT count(*) FROM audit.schema_migrations WHERE migration_id<='0024_zzzz')<>24
           OR (CASE
                WHEN jsonb_typeof(NEW.source_payload_jsonb->'expected_hashes_0001_0024')='object'
                THEN (SELECT count(*) FROM jsonb_object_keys(NEW.source_payload_jsonb->'expected_hashes_0001_0024'))
                ELSE 0
              END)<>24
           OR (CASE
                WHEN jsonb_typeof(NEW.source_payload_jsonb->'observed_hashes')='object'
                THEN (SELECT count(*) FROM jsonb_object_keys(NEW.source_payload_jsonb->'observed_hashes'))
                ELSE 0
              END)<>(SELECT count(*) FROM audit.schema_migrations)
           OR EXISTS (
               SELECT 1 FROM audit.schema_migrations m
               WHERE m.migration_id<='0024_zzzz'
                 AND NEW.source_payload_jsonb->'expected_hashes_0001_0024'->>m.migration_id
                     IS DISTINCT FROM m.sha256::text
           )
           OR EXISTS (
               SELECT 1 FROM audit.schema_migrations m
               WHERE NEW.source_payload_jsonb->'observed_hashes'->>m.migration_id
                     IS DISTINCT FROM m.sha256::text
           ) THEN
            RAISE EXCEPTION 'migration source is not bound to exact immutable 0001-0024 catalog';
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

CREATE OR REPLACE FUNCTION execution.validate_live_0025_credential_permission_source()
RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
    bundle execution.live_okx_response_bundles%ROWTYPE;
    envelope execution.live_okx_response_envelopes%ROWTYPE;
    credential execution.live_credential_references%ROWTYPE;
    account_row jsonb;
    permission_text text;
    provider_permissions jsonb;
    normalized_permissions jsonb;
    expected_permissions jsonb;
    permission_count integer;
    distinct_permission_count integer;
    account_source_count integer;
    permission_source_count integer;
    payload_key_count integer;
BEGIN
    IF NEW.source_kind<>'credential_permissions' OR NOT NEW.operational THEN
        RETURN NEW;
    END IF;

    SELECT * INTO bundle
    FROM execution.live_okx_response_bundles
    WHERE response_bundle_id::text=NEW.source_record_identity
      AND live_run_id=NEW.live_run_id;
    IF bundle.response_bundle_id IS NULL
       OR bundle.bundle_purpose<>'preflight'
       OR bundle.producer_classification<>'operational_collector'
       OR bundle.collector_kind<>'okx_production_spot_read_adapter'
       OR bundle.collector_version<>'phase8a-0025-v1'
       OR bundle.parser_version<>'okx-v5-parser-v4' THEN
        RAISE EXCEPTION 'credential permission source is not bound to the exact operational preflight bundle';
    END IF;

    SELECT * INTO envelope
    FROM execution.live_okx_response_envelopes
    WHERE response_bundle_id=bundle.response_bundle_id
      AND endpoint_kind='account_config';
    IF envelope.response_bundle_id IS NULL
       OR NOT envelope.completed
       OR envelope.top_level_provider_code<>'0'
       OR envelope.parser_version<>bundle.parser_version
       OR envelope.canonical_response_sha256 IS NULL
       OR NEW.raw_response_sha256<>envelope.canonical_response_sha256
       OR NEW.parser_version<>envelope.parser_version THEN
        RAISE EXCEPTION 'credential permission source is not bound to the exact account-config response';
    END IF;

    IF jsonb_typeof(envelope.raw_response_jsonb->'data')<>'array'
       OR jsonb_array_length(envelope.raw_response_jsonb->'data')<>1
       OR jsonb_typeof(envelope.raw_response_jsonb#>'{data,0}')<>'object' THEN
        RAISE EXCEPTION 'account-config permission response shape is invalid';
    END IF;
    account_row := envelope.raw_response_jsonb#>'{data,0}';
    IF NOT account_row ?& ARRAY[
        'uid','mainUid','perm','acctLv','posMode','autoLoan','enableSpotBorrow'
    ]
       OR jsonb_typeof(account_row->'perm')<>'string'
       OR COALESCE(account_row->>'uid','')=''
       OR COALESCE(account_row->>'mainUid','')=''
       OR COALESCE(account_row->>'acctLv','')=''
       OR COALESCE(account_row->>'posMode','')=''
       OR COALESCE(account_row->>'autoLoan','')=''
       OR COALESCE(account_row->>'enableSpotBorrow','')='' THEN
        RAISE EXCEPTION 'account-config permission response is missing required exact fields';
    END IF;

    permission_text := account_row->>'perm';
    IF permission_text IS NULL OR permission_text=''
       OR permission_text<>btrim(permission_text)
       OR permission_text~'[[:space:]]'
       OR permission_text LIKE ',%'
       OR permission_text LIKE '%,'
       OR permission_text LIKE '%,,%' THEN
        RAISE EXCEPTION 'account-config perm is empty, malformed, or whitespace-ambiguous';
    END IF;
    SELECT count(*),count(DISTINCT value)
    INTO permission_count,distinct_permission_count
    FROM unnest(string_to_array(permission_text,',')) AS permission(value);
    IF permission_count=0 OR permission_count<>distinct_permission_count
       OR EXISTS (
           SELECT 1 FROM unnest(string_to_array(permission_text,',')) AS permission(value)
           WHERE value NOT IN ('read_only','trade','withdraw')
       ) THEN
        RAISE EXCEPTION 'account-config perm contains duplicate or unknown permissions';
    END IF;
    SELECT jsonb_agg(value ORDER BY value),
           jsonb_agg(CASE value WHEN 'read_only' THEN 'read' ELSE value END ORDER BY CASE value WHEN 'read_only' THEN 'read' ELSE value END)
    INTO provider_permissions,normalized_permissions
    FROM unnest(string_to_array(permission_text,',')) AS permission(value);
    IF provider_permissions<>'["read_only"]'::jsonb
       OR normalized_permissions<>'["read"]'::jsonb THEN
        RAISE EXCEPTION 'Phase 8A requires the exact OKX permission set read_only';
    END IF;

    SELECT * INTO credential
    FROM execution.live_credential_references
    WHERE credential_reference_id::text=NEW.source_payload_jsonb->>'credential_reference_id';
    IF credential.credential_reference_id IS NULL
       OR credential.record_sha256<>NEW.source_payload_jsonb->>'credential_record_hash'
       OR credential.provider<>'okx' THEN
        RAISE EXCEPTION 'credential permission source credential identity mismatch';
    END IF;
    IF jsonb_typeof(credential.permission_summary_jsonb)<>'array'
       OR EXISTS (
           SELECT 1 FROM jsonb_array_elements_text(credential.permission_summary_jsonb) permission(value)
           WHERE value NOT IN ('read','read_only','trade','withdraw')
       ) THEN
        RAISE EXCEPTION 'credential permission expectation is malformed or unrecognized';
    END IF;
    SELECT COALESCE(jsonb_agg(value ORDER BY value),'[]'::jsonb)
    INTO expected_permissions
    FROM (
        SELECT DISTINCT CASE value WHEN 'read_only' THEN 'read' ELSE value END AS value
        FROM jsonb_array_elements_text(credential.permission_summary_jsonb) permission(value)
    ) normalized_expected;

    SELECT count(*) INTO payload_key_count
    FROM jsonb_object_keys(NEW.source_payload_jsonb);
    IF payload_key_count<>10
       OR NOT NEW.source_payload_jsonb ?& ARRAY[
           'provider_permissions','normalized_permissions','expected_permissions',
           'credential_reference_id','credential_record_hash','response_bundle_id',
           'account_config_response_sha256','parser_version','verified_at_utc','policy_version'
       ]
       OR NEW.source_payload_jsonb->'provider_permissions'<>provider_permissions
       OR NEW.source_payload_jsonb->'normalized_permissions'<>normalized_permissions
       OR NEW.source_payload_jsonb->'expected_permissions'<>expected_permissions
       OR (expected_permissions<>'[]'::jsonb AND expected_permissions<>normalized_permissions)
       OR NEW.source_payload_jsonb->>'response_bundle_id'<>bundle.response_bundle_id::text
       OR NEW.source_payload_jsonb->>'account_config_response_sha256'<>envelope.canonical_response_sha256
       OR NEW.source_payload_jsonb->>'parser_version'<>envelope.parser_version
       OR (NEW.source_payload_jsonb->>'verified_at_utc')::timestamptz<>envelope.query_completed_at_utc
       OR NEW.source_payload_jsonb->>'policy_version'<>'phase8a-read-only-v1' THEN
        RAISE EXCEPTION 'credential permission payload is not derived from exact account-config authority';
    END IF;

    SELECT count(*) INTO account_source_count
    FROM execution.live_preflight_sources source
    WHERE source.live_run_id=NEW.live_run_id
      AND source.source_kind='account_config'
      AND source.operational
      AND source.producer_classification='operational_collector'
      AND source.collector_kind='okx_read_only_adapter'
      AND source.collector_version='phase8a-0025-v1'
      AND source.source_record_identity=bundle.response_bundle_id::text
      AND source.raw_response_sha256=envelope.canonical_response_sha256
      AND source.parser_version=envelope.parser_version;
    SELECT count(*) INTO permission_source_count
    FROM execution.live_preflight_sources source
    WHERE source.live_run_id=NEW.live_run_id
      AND source.source_kind='credential_permissions'
      AND source.operational
      AND source.source_record_identity=bundle.response_bundle_id::text;
    IF account_source_count<>1 OR permission_source_count<>1 THEN
        RAISE EXCEPTION 'credential permission source is forged, duplicated, or lacks exact account-config source binding';
    END IF;
    RETURN NEW;
END;
$$;
DROP TRIGGER IF EXISTS trg_validate_live_0025_credential_permission_source ON execution.live_preflight_sources;
CREATE CONSTRAINT TRIGGER trg_validate_live_0025_credential_permission_source
AFTER INSERT OR UPDATE ON execution.live_preflight_sources
DEFERRABLE INITIALLY DEFERRED FOR EACH ROW
EXECUTE FUNCTION execution.validate_live_0025_credential_permission_source();

CREATE OR REPLACE FUNCTION execution.guard_live_preflight_authority()
RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
    credential execution.live_credential_references%ROWTYPE;
    account execution.live_account_snapshots%ROWTYPE;
    configuration execution.live_configuration_snapshots%ROWTYPE;
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
        IF NEW.authority_generation<>'collector_0025' THEN
            RAISE EXCEPTION 'passed preflight requires collector_0025 authority';
        END IF;
        IF NOT EXISTS (
            SELECT 1
            FROM execution.live_preflight_sources source
            JOIN execution.live_okx_response_bundles bundle
              ON bundle.response_bundle_id::text=source.source_record_identity
             AND bundle.live_run_id=source.live_run_id
            JOIN execution.live_okx_response_envelopes envelope
              ON envelope.response_bundle_id=bundle.response_bundle_id
             AND envelope.endpoint_kind='account_config'
            WHERE source.live_run_id=NEW.live_run_id
              AND source.source_kind='credential_permissions'
              AND source.operational
              AND source.producer_classification='operational_collector'
              AND source.collector_kind='okx_account_config_permission_collector'
              AND source.collector_version='phase8a-0025-v1'
              AND source.source_payload_jsonb->>'credential_reference_id'=NEW.credential_reference_id::text
              AND source.source_payload_jsonb->>'credential_record_hash'=NEW.credential_reference_sha256
              AND source.source_payload_jsonb->'provider_permissions'='["read_only"]'::jsonb
              AND source.source_payload_jsonb->'normalized_permissions'='["read"]'::jsonb
              AND source.raw_response_sha256=envelope.canonical_response_sha256
              AND bundle.bundle_purpose='preflight'
              AND bundle.producer_classification='operational_collector'
        ) THEN
            RAISE EXCEPTION 'passed preflight lacks exact OKX credential permission authority';
        END IF;
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
    IF NEW.authority_generation<>'collector_0025' THEN
        RAISE EXCEPTION 'passed preflight requires collector_0025 authority';
    END IF;
    SELECT count(DISTINCT source.source_kind) INTO source_count
    FROM execution.live_preflight_checks check_record
    JOIN execution.live_preflight_check_sources check_source
      ON check_source.preflight_check_id=check_record.preflight_check_id
     AND check_source.live_run_id=check_record.live_run_id
    JOIN execution.live_preflight_sources source
      ON source.source_id=check_source.source_id
     AND source.live_run_id=check_source.live_run_id
    WHERE check_record.preflight_report_id=NEW.preflight_report_id
      AND check_record.live_run_id=NEW.live_run_id
      AND check_record.passed AND check_record.required
      AND source.operational
      AND source.producer_classification='operational_collector';
    IF source_count<>19 THEN
        RAISE EXCEPTION 'passed preflight lacks all collector-issued operational source kinds';
    END IF;
    IF EXISTS (
        SELECT 1 FROM execution.live_preflight_check_sources check_source
        JOIN execution.live_preflight_checks check_record
          ON check_record.preflight_check_id=check_source.preflight_check_id
        JOIN execution.live_preflight_sources source
          ON source.source_id=check_source.source_id
        WHERE check_record.preflight_report_id=NEW.preflight_report_id
          AND (check_source.live_run_id<>NEW.live_run_id
               OR check_source.source_sha256<>source.source_sha256)
    ) THEN
        RAISE EXCEPTION 'preflight check/source membership or hash mismatch';
    END IF;
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
        SELECT 1 FROM execution.live_market_source_bindings binding
        WHERE binding.source_id=risk.latest_market_evidence_id
          AND binding.live_run_id=NEW.live_run_id
          AND binding.finality_verified AND binding.quarantine_clear
    ) THEN
        RAISE EXCEPTION 'preflight market source is not bound to Phase 7 authority';
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM execution.live_instrument_metadata_sources metadata
        WHERE metadata.live_run_id=NEW.live_run_id AND metadata.instrument_state='live'
    ) THEN
        RAISE EXCEPTION 'preflight lacks live instrument metadata authority';
    END IF;
    RETURN NEW;
END;
$$;

CREATE OR REPLACE FUNCTION execution.validate_live_0025_permission_report()
RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
    permission_detail_count integer;
    verified_okx_kinds integer;
BEGIN
    IF NEW.status NOT IN ('passed','passed_for_reset') THEN RETURN NEW; END IF;
    IF NEW.authority_generation<>'collector_0025' THEN
        RAISE EXCEPTION 'passed preflight requires collector_0025 permission authority';
    END IF;
    SELECT count(*) INTO permission_detail_count
    FROM execution.live_preflight_checks check_record
    JOIN execution.live_preflight_check_sources check_source
      ON check_source.preflight_check_id=check_record.preflight_check_id
     AND check_source.live_run_id=check_record.live_run_id
    JOIN execution.live_preflight_sources source
      ON source.source_id=check_source.source_id
     AND source.live_run_id=check_source.live_run_id
    JOIN execution.live_okx_response_bundles bundle
      ON bundle.response_bundle_id::text=source.source_record_identity
     AND bundle.live_run_id=source.live_run_id
    JOIN execution.live_okx_response_envelopes envelope
      ON envelope.response_bundle_id=bundle.response_bundle_id
     AND envelope.endpoint_kind='account_config'
    WHERE check_record.preflight_report_id=NEW.preflight_report_id
      AND check_record.live_run_id=NEW.live_run_id
      AND check_record.check_name='credential_permissions'
      AND check_record.passed AND check_record.required
      AND source.source_kind='credential_permissions'
      AND source.operational
      AND source.producer_classification='operational_collector'
      AND source.collector_kind='okx_account_config_permission_collector'
      AND source.collector_version='phase8a-0025-v1'
      AND source.parser_version='okx-v5-parser-v4'
      AND source.raw_response_sha256=envelope.canonical_response_sha256
      AND source.source_payload_jsonb->>'response_bundle_id'=bundle.response_bundle_id::text
      AND source.source_payload_jsonb->>'credential_reference_id'=NEW.credential_reference_id::text
      AND source.source_payload_jsonb->>'credential_record_hash'=NEW.credential_reference_sha256
      AND source.source_payload_jsonb->'provider_permissions'='["read_only"]'::jsonb
      AND source.source_payload_jsonb->'normalized_permissions'='["read"]'::jsonb
      AND source.source_payload_jsonb->>'account_config_response_sha256'=envelope.canonical_response_sha256
      AND source.source_payload_jsonb->>'parser_version'=envelope.parser_version
      AND (source.source_payload_jsonb->>'verified_at_utc')::timestamptz=envelope.query_completed_at_utc
      AND envelope.completed AND envelope.top_level_provider_code='0'
      AND envelope.raw_response_jsonb#>>'{data,0,perm}'='read_only'
      AND bundle.bundle_purpose='preflight'
      AND bundle.producer_classification='operational_collector';
    IF permission_detail_count<>1 THEN
        RAISE EXCEPTION 'passed preflight credential permission detail is missing, forged, or unbound';
    END IF;

    SELECT count(DISTINCT source.source_kind) INTO verified_okx_kinds
    FROM execution.live_preflight_checks check_record
    JOIN execution.live_preflight_check_sources check_source
      ON check_source.preflight_check_id=check_record.preflight_check_id
     AND check_source.live_run_id=check_record.live_run_id
    JOIN execution.live_preflight_sources source
      ON source.source_id=check_source.source_id
     AND source.live_run_id=check_source.live_run_id
    JOIN execution.live_okx_response_bundles bundle
      ON bundle.response_bundle_id::text=source.source_record_identity
     AND bundle.live_run_id=source.live_run_id
    JOIN execution.live_okx_response_envelopes envelope
      ON envelope.response_bundle_id=bundle.response_bundle_id
     AND envelope.endpoint_kind=CASE
        WHEN source.source_kind IN (
            'credential_permissions','account_config','account_fingerprint',
            'subaccount','account_mode','margin_borrowing'
        ) THEN 'account_config'
        WHEN source.source_kind='balances' THEN 'balances'
        WHEN source.source_kind='positions' THEN 'positions'
        WHEN source.source_kind='open_orders' THEN 'pending_orders'
        WHEN source.source_kind='venue_time' THEN 'venue_time'
        WHEN source.source_kind='instrument_metadata' THEN 'instrument_metadata'
     END
    WHERE check_record.preflight_report_id=NEW.preflight_report_id
      AND source.source_kind IN (
        'credential_permissions','account_config','account_fingerprint','subaccount',
        'account_mode','margin_borrowing','balances','positions','open_orders',
        'venue_time','instrument_metadata'
      )
      AND check_record.passed AND check_record.required
      AND bundle.bundle_purpose='preflight'
      AND bundle.producer_classification='operational_collector'
      AND envelope.completed AND envelope.top_level_provider_code='0'
      AND source.raw_response_sha256=envelope.canonical_response_sha256;
    IF verified_okx_kinds<>11 THEN
        RAISE EXCEPTION 'preflight OKX sources do not include exact credential permission authority';
    END IF;
    RETURN NEW;
END;
$$;
DROP TRIGGER IF EXISTS trg_validate_live_0025_permission_report ON execution.live_preflight_reports;
CREATE CONSTRAINT TRIGGER trg_validate_live_0025_permission_report
AFTER INSERT OR UPDATE ON execution.live_preflight_reports
DEFERRABLE INITIALLY DEFERRED FOR EACH ROW
EXECUTE FUNCTION execution.validate_live_0025_permission_report();
-- Phase 8A rebinds kill-reset authority to the exact collector_0025 preflight graph.
-- The trigger name remains from 0024; only its function body is replaced append-only here.
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
           OR report.authority_generation<>'collector_0025'
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
           ) THEN RAISE EXCEPTION 'kill reset lacks exact stopped-row, collector_0025 report, or approval authority'; END IF;
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
