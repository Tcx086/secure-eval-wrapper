"""PostgreSQL 16 catalog and hard-prohibition verification for repaired Phase 8A."""
import importlib
import json
import os

TABLES = {
"live_configuration_snapshots":{"configuration_snapshot_id","configuration_sha256","dry_run","production_write_enabled","record_sha256"},
"live_credential_references":{"credential_reference_id","account_fingerprint","permission_summary_jsonb","record_sha256"},
"live_account_snapshots":{"account_snapshot_id","live_run_id","available_equity","reserved_equity","record_sha256"},
"live_okx_response_bundles":{"response_bundle_id","live_run_id","bundle_purpose","producer_classification","endpoint_matrix_sha256","record_sha256"},
"live_okx_response_envelopes":{"response_bundle_id","endpoint_kind","request_identity","completed","error_classification","raw_response_jsonb","canonical_response_sha256","database_payload_sha256","record_sha256"},
"live_market_source_bindings":{"source_id","live_run_id","bar_id","validation_report_id","raw_observation_ids","raw_observation_hashes_jsonb","finality_verified","quarantine_clear","record_sha256"},
"live_instrument_metadata_sources":{"source_id","live_run_id","response_bundle_id","instrument_id","instrument_state","tick_size","lot_size","minimum_size","minimum_notional","provider_response_sha256","record_sha256"},
"live_reconciliation_input_bundles":{"reconciliation_input_bundle_id","reconciliation_id","live_run_id","response_bundle_id","local_projection_jsonb","venue_projection_jsonb","record_sha256"},
"live_recovery_query_completions":{"recovery_record_id","response_bundle_id","endpoint_kind","completed","error_classification","response_sha256","record_sha256"},
"live_preflight_sources":{"source_id","live_run_id","source_kind","source_sha256","operational","record_sha256","producer_classification","collector_kind","collector_version","parser_version","source_system_identity","source_record_identity","raw_response_sha256","normalized_payload_sha256","source_schema_version"},
"live_preflight_reports":{"preflight_report_id","live_run_id","credential_reference_id","account_snapshot_id","status","purpose","authority_generation","record_sha256"},
"live_preflight_checks":{"preflight_check_id","preflight_report_id","live_run_id","required","evidence_sha256"},
"live_preflight_check_sources":{"preflight_check_id","source_id","live_run_id","source_sha256"},
"live_approvals":{"approval_id","live_run_id","manifest_sha256","confirmation_challenge_sha256","consumed_notional"},
"live_run_manifests":{"manifest_id","live_run_id","credential_reference_id","dry_run","production_write_enabled","manifest_sha256"},
"live_runs":{"live_run_id","manifest_id","state","dry_run","production_write_enabled","version"},
"live_kill_switches":{"kill_switch_id","live_run_id","state","triggered_at_utc","reset_preflight_report_id","reset_approval_id"},
"live_kill_events":{"kill_event_id","kill_switch_id","live_run_id","new_state","record_sha256"},
"live_run_risk_state":{"live_run_id","trading_day","current_equity","high_watermark_equity","daily_submitted_notional","daily_realized_pnl","gross_exposure","net_exposure","order_rate_window_jsonb","cancellation_rate_window_jsonb","open_order_count","latest_market_data_at_utc","latest_account_snapshot_at_utc","latest_reconciliation_at_utc","latest_reconciliation_status","latest_reconciliation_input_bundle_id","latest_local_sequence","latest_venue_sequence","clock_skew_seconds","run_started_at_utc","transport_failure_count","version"},
"live_order_intents":{"order_intent_id","live_run_id","manifest_id","client_order_id","state","economic_sha256","instrument_metadata_source_id","instrument_metadata_parser_version","metadata_authority_generation"},
"live_runtime_risk_decisions":{"risk_decision_id","live_run_id","order_intent_id","risk_notional","reservation_notional","price_source_sha256"},
"live_reservations":{"reservation_id","live_run_id","order_intent_id","currency","original_amount","remaining_amount","original_quantity","remaining_quantity","worst_case_price","maximum_fee_bps","maximum_fee_amount","fee_currency_policy","risk_notional","reservation_notional","calculator_version","source_hashes_jsonb","state","version"},
"live_dispatch_outbox":{"dispatch_outbox_id","live_run_id","order_intent_id","state","provider_request_sha256","request_jsonb","claim_token","recovery_generation","version","instrument_metadata_source_id","instrument_metadata_sha256","instrument_metadata_parser_version","metadata_authority_generation"},
"live_dispatch_events":{"dispatch_event_id","live_run_id","dispatch_outbox_id","event_type","record_sha256"},
"live_cancel_outbox":{"cancel_outbox_id","live_run_id","order_intent_id","state","provider_request_sha256","request_jsonb"},
"live_transport_attempts":{"transport_attempt_id","live_run_id","result","external_write_attempted","successful_write"},
"live_order_observations":{"order_observation_id","live_run_id","client_order_id","provider_response_sha256","response_bundle_id","evidence_classification","endpoint_matrix_sha256","query_started_at_utc","query_completed_at_utc"},
"live_order_projections":{"order_intent_id","live_run_id","state","filled_quantity","version"},
"live_fill_observations":{"fill_observation_id","live_run_id","provider_fill_id","quantity","fee"},
"live_reconciliations":{"reconciliation_id","live_run_id","status","input_bundle_sha256","exact_input_jsonb","local_projection_as_of_utc","venue_observation_as_of_utc","query_started_at_utc","query_completed_at_utc","response_bundle_id","producer_classification","local_sequence","venue_sequence"},
"live_reconciliation_differences":{"reconciliation_difference_id","reconciliation_id","live_run_id","material"},
"live_recovery_records":{"recovery_record_id","live_run_id","generation","claim_token","query_first","state","outcome","manual_intervention_required","response_bundle_id","evidence_classification","endpoint_matrix_sha256"},
"live_lifecycle_events":{"lifecycle_event_id","live_run_id","sequence","parent_evidence_ids"},
"live_pre_run_summaries":{"summary_id","live_run_id","public_summary_jsonb","evidence_ids"},
"live_post_run_summaries":{"summary_id","live_run_id","external_write_attempted","external_write_suppressed"},
}
INDEXES = {"idx_live_preflight_sources_run_kind","idx_live_risk_state_day","idx_live_reservation_balance","idx_live_dispatch_claimable","idx_live_recovery_claims","idx_live_okx_bundle_run_purpose","idx_live_metadata_run_instrument","idx_live_recovery_query_matrix"}
TRIGGERS = {"trg_guard_live_preflight_authority","trg_guard_live_manifest_chain","trg_live_approval_consumption","trg_live_intent_mutation","trg_live_dispatch_request_immutable","trg_live_cancel_request_immutable","trg_live_dispatch_monotonic","trg_live_reservation_monotonic","trg_live_projection_monotonic","trg_live_collector_source","trg_guard_live_okx_response_payload_hash","trg_validate_live_okx_bundle_matrix","trg_validate_live_okx_envelope_matrix","trg_validate_live_preflight_graph","trg_validate_live_0024_source_details","trg_guard_live_0024_reconciliation","trg_validate_live_0024_reconciliation_exact","trg_guard_live_0024_intent_metadata","trg_guard_live_0024_outbox_metadata","trg_validate_live_0024_recovery_outcome","trg_guard_live_0024_kill_reset"}


def main():
    psycopg = importlib.import_module("psycopg")
    connection = psycopg.connect(host=os.environ["POSTGRES_HOST"], port=int(os.environ["POSTGRES_PORT"]), dbname=os.environ["POSTGRES_DB"], user=os.environ["POSTGRES_USER"], password=os.environ["POSTGRES_PASSWORD"], sslmode=os.environ.get("POSTGRES_SSLMODE", "disable"))
    try:
        with connection.cursor() as cursor:
            for table, required in TABLES.items():
                cursor.execute("SELECT column_name FROM information_schema.columns WHERE table_schema='execution' AND table_name=%s", (table,)); missing = required - {row[0] for row in cursor.fetchall()}
                if missing: raise RuntimeError(f"execution.{table} missing columns: {sorted(missing)}")
            cursor.execute("SELECT indexname FROM pg_indexes WHERE schemaname='execution'"); missing = INDEXES - {row[0] for row in cursor.fetchall()}
            if missing: raise RuntimeError("missing Phase 8A indexes: " + ",".join(sorted(missing)))
            cursor.execute("SELECT tgname FROM pg_trigger WHERE NOT tgisinternal AND tgrelid IN (SELECT oid FROM pg_class WHERE relnamespace='execution'::regnamespace)"); missing = TRIGGERS - {row[0] for row in cursor.fetchall()}
            if missing: raise RuntimeError("missing Phase 8A guards: " + ",".join(sorted(missing)))
            cursor.execute("SELECT count(*) FROM execution.live_configuration_snapshots WHERE production_write_enabled OR NOT dry_run"); unsafe_config = cursor.fetchone()[0]
            cursor.execute("SELECT count(*) FROM execution.live_run_manifests WHERE production_write_enabled OR NOT dry_run"); unsafe_manifest = cursor.fetchone()[0]
            cursor.execute("SELECT count(*) FROM execution.live_transport_attempts WHERE external_write_attempted OR successful_write"); unsafe_transport = cursor.fetchone()[0]
            cursor.execute("SELECT count(*) FROM execution.live_order_intents WHERE state NOT IN ('dry_run_prepared','dry_run_blocked','dry_run_suppressed','pending_recovery','unexpected_external_side_effect','incident_blocked')"); unsafe_state = cursor.fetchone()[0]
            if any((unsafe_config, unsafe_manifest, unsafe_transport, unsafe_state)): raise RuntimeError("Phase 8A hard-prohibition catalog contains unsafe rows")
        print("OK: Phase 8A repaired catalog " + json.dumps({"table_count": len(TABLES), "index_count": len(INDEXES), "trigger_count": len(TRIGGERS), "production_writes": 0}, sort_keys=True))
    finally:
        connection.close()


if __name__ == "__main__": main()
