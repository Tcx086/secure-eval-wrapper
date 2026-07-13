"""PostgreSQL 16 catalog and hard-prohibition verification for Phase 8A."""
import importlib
import json
import os

TABLES = {
"live_configuration_snapshots":{"configuration_snapshot_id","configuration_sha256","dry_run","production_write_enabled","record_sha256"},
"live_credential_references":{"credential_reference_id","account_fingerprint","permission_summary_jsonb","record_sha256"},
"live_account_snapshots":{"account_snapshot_id","live_run_id","available_equity","reserved_equity","record_sha256"},
"live_preflight_reports":{"preflight_report_id","live_run_id","status","blockers_jsonb","record_sha256"},
"live_preflight_checks":{"preflight_check_id","preflight_report_id","required","evidence_sha256"},
"live_approvals":{"approval_id","manifest_sha256","confirmation_challenge_sha256","consumed_notional"},
"live_run_manifests":{"manifest_id","live_run_id","dry_run","production_write_enabled","manifest_sha256"},
"live_runs":{"live_run_id","manifest_id","state","dry_run","production_write_enabled","version"},
"live_kill_switches":{"kill_switch_id","live_run_id","state","requires_fresh_preflight","requires_new_approval"},
"live_kill_events":{"kill_event_id","kill_switch_id","new_state","record_sha256"},
"live_order_intents":{"order_intent_id","client_order_id","state","economic_sha256"},
"live_runtime_risk_decisions":{"risk_decision_id","order_intent_id","risk_notional","reservation_notional","price_source_sha256"},
"live_reservations":{"reservation_id","order_intent_id","risk_notional","state","version"},
"live_dispatch_outbox":{"dispatch_outbox_id","order_intent_id","state","claim_token","recovery_generation","version"},
"live_dispatch_events":{"dispatch_event_id","dispatch_outbox_id","event_type","record_sha256"},
"live_cancel_outbox":{"cancel_outbox_id","order_intent_id","state","claim_token","recovery_generation"},
"live_transport_attempts":{"transport_attempt_id","result","external_write_attempted","successful_write"},
"live_order_observations":{"order_observation_id","client_order_id","provider_response_sha256"},
"live_order_projections":{"order_intent_id","state","filled_quantity","version"},
"live_fill_observations":{"fill_observation_id","provider_fill_id","quantity","fee"},
"live_reconciliations":{"reconciliation_id","status","input_bundle_sha256","exact_input_jsonb"},
"live_reconciliation_differences":{"reconciliation_difference_id","reconciliation_id","material"},
"live_recovery_records":{"recovery_record_id","generation","claim_token","query_first","state"},
"live_lifecycle_events":{"lifecycle_event_id","live_run_id","sequence","parent_evidence_ids"},
"live_pre_run_summaries":{"summary_id","live_run_id","public_summary_jsonb","evidence_ids"},
"live_post_run_summaries":{"summary_id","live_run_id","external_write_attempted","external_write_suppressed"},
}
INDEXES = {"idx_live_preflight_reports_run_time","idx_live_account_snapshots_run_time","idx_live_intents_run_state","idx_live_dispatch_claimable","idx_live_cancel_claimable","idx_live_observations_client_time","idx_live_fills_run_time","idx_live_recovery_claims","idx_live_lifecycle_sequence"}
TRIGGERS = {"trg_live_dispatch_monotonic","trg_live_reservation_monotonic","trg_live_projection_monotonic"}


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
            if missing: raise RuntimeError("missing Phase 8A monotonic triggers: " + ",".join(sorted(missing)))
            cursor.execute("SELECT count(*) FROM execution.live_configuration_snapshots WHERE production_write_enabled OR NOT dry_run"); unsafe_config = cursor.fetchone()[0]
            cursor.execute("SELECT count(*) FROM execution.live_run_manifests WHERE production_write_enabled OR NOT dry_run"); unsafe_manifest = cursor.fetchone()[0]
            cursor.execute("SELECT count(*) FROM execution.live_transport_attempts WHERE external_write_attempted OR successful_write"); unsafe_transport = cursor.fetchone()[0]
            cursor.execute("SELECT count(*) FROM execution.live_order_intents WHERE state NOT IN ('dry_run_prepared','dry_run_blocked','dry_run_suppressed','pending_recovery')"); unsafe_state = cursor.fetchone()[0]
            if any((unsafe_config, unsafe_manifest, unsafe_transport, unsafe_state)): raise RuntimeError("Phase 8A hard-prohibition catalog contains unsafe rows")
        print("OK: Phase 8A catalog " + json.dumps({"table_count": len(TABLES), "index_count": len(INDEXES), "trigger_count": len(TRIGGERS), "production_writes": 0}, sort_keys=True))
    finally:
        connection.close()


if __name__ == "__main__": main()
