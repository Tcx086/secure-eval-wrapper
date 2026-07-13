"""PostgreSQL 16 catalog verification for Phase 7 paper persistence."""
import importlib,json,os
TABLES={
"paper_runs":{"paper_run_id","environment","manifest_id","record_sha256"},"paper_run_manifests":{"manifest_id","preflight_report_id","approval_id","manifest_sha256"},"paper_preflight_reports":{"report_id","status","credential_reference_sha256"},"paper_preflight_checks":{"check_id","report_id","required"},"paper_approvals":{"approval_id","expires_at_utc","state"},"paper_order_submissions":{"submission_id","manifest_id","approval_id","client_order_id","state","counted_open","open_counted_at_utc","open_closed_at_utc","market_evidence_price","risk_reference_price","worst_case_order_price","risk_notional","reservation_notional","price_deviation_bps","price_source_sha256","price_calculator_version"},"paper_orders":{"paper_order_record_id","venue_sequence","cumulative_filled_quantity"},"paper_order_events":{"paper_order_event_id","submission_id"},"paper_fills":{"fill_id","venue_fill_id","accounting_applied"},"paper_fee_entries":{"fee_entry_id","fill_id"},"paper_account_snapshots":{"snapshot_id","venue_as_of_utc"},"paper_balance_snapshots":{"snapshot_id","currency"},"paper_position_snapshots":{"snapshot_id","series_identity_sha256"},"paper_open_order_snapshots":{"snapshot_id","client_order_id"},"paper_reconciliations":{"reconciliation_id","status"},"paper_reconciliation_differences":{"difference_id","difference_type"},"paper_recovery_records":{"recovery_id","status"},"paper_kill_switches":{"kill_switch_id","state"},"paper_kill_switch_events":{"kill_switch_event_id","next_state"},"paper_rate_limit_events":{"rate_limit_event_id","operation"},"paper_transport_attempts":{"transport_attempt_id","result_type"},"paper_credential_references":{"credential_reference_sha256","public_key_fingerprint"},"paper_lifecycle_events":{"event_id","deterministic_sequence"}}
INDEXES={"idx_phase7_paper_runs_state_time","idx_phase7_paper_manifest_account","idx_phase7_paper_submissions_run_time","idx_phase7_paper_submissions_unknown","idx_phase7_paper_orders_open","idx_phase7_paper_fills_run_time","idx_phase7_paper_snapshots_account_time","idx_phase7_paper_reconciliation_time","idx_phase7_paper_differences_type","idx_phase7_paper_recovery_time","idx_phase7_paper_kill_events_time","idx_phase7_paper_transport_time","idx_phase7_paper_lifecycle_half_open"}
TABLES.update({
"paper_configuration_snapshots":{"configuration_sha256","configuration_jsonb","record_sha256"},
"paper_approval_state_events":{"approval_event_id","approval_id","next_state"},
"paper_run_risk_state":{"paper_run_id","daily_submitted_notional","open_order_count","lifecycle_sequence","latest_market_evidence_id","latest_market_evidence_sha256"},
"paper_runtime_risk_decisions":{"runtime_risk_decision_id","submission_id","evaluated_limits_jsonb","evidence_jsonb","market_evidence_id","market_evidence_sha256","market_evidence_price","risk_reference_price","worst_case_order_price","risk_notional","reservation_notional","price_deviation_bps","price_source_sha256","price_calculator_version"},
"paper_reservations":{"reservation_id","remaining_amount","remaining_quantity","state","risk_notional","reservation_notional","price_source_sha256","price_calculator_version"},
"paper_reservation_events":{"reservation_event_id","reservation_id","event_type"},
"paper_dispatch_outbox":{"dispatch_id","submission_id","state","claim_token","claim_lease_expires_at_utc","recovery_claim_token","recovery_lease_expires_at_utc"},
"paper_dispatch_events":{"dispatch_event_id","dispatch_id","event_type"},
"paper_cancel_outbox":{"cancel_id","dispatch_id","state","claim_lease_expires_at_utc","recovery_claim_token","recovery_lease_expires_at_utc","terminal_evidence_sha256","terminal_order_observation_id","accounting_complete_at_confirmation"},
"paper_account_balance_projection":{"paper_run_id","currency","total","version"},
"paper_account_position_projection":{"paper_run_id","series_identity_sha256","series_identity_jsonb","quantity","version"},
"paper_order_budget_events":{"order_budget_event_id","submission_id","event_type","prior_counted_open","next_counted_open"},
"paper_market_data_evidence":{"market_evidence_id","series_identity_sha256","observation_id","evidence_sha256","source_kind","exchange","provider_instrument_id","instrument_type","source_table","source_row_id","validation_report_id","price","price_type","quote_currency","normalized_record_sha256"},
"paper_reconciliation_bundles":{"reconciliation_bundle_id","reconciliation_id","local_snapshot_id","venue_snapshot_id"},
"paper_internal_venue_sequences":{"paper_run_id","last_sequence"},
"paper_internal_venue_commands":{"command_id","command_type","idempotency_key","payload_jsonb"},
"paper_internal_venue_events":{"internal_venue_event_id","command_id","venue_sequence","event_type"},
"paper_venue_order_observations":{"venue_order_observation_id","submission_id","venue_sequence","state","evidence_sha256"},
"paper_order_projections":{"submission_id","latest_observation_id","authority_state","fill_application_complete","terminal_disposition","remaining_quantity","terminal_observation_sequence","latest_fill_sequence"},
"paper_recovery_observation_bundles":{"recovery_observation_bundle_id","query_id","fill_evidence_complete"},
"paper_fill_recovery_lineage":{"fill_recovery_lineage_id","fill_id","reservation_amount_before","reservation_amount_after"},
"paper_expiry_outbox":{"expiry_id","state","claim_token","recovery_claim_token"},
"paper_expiry_recovery_records":{"expiry_recovery_id","expiry_id","recovery_generation","recovery_claim_token","outcome","query_evidence_sha256"},
"paper_internal_venue_economics":{"paper_run_id","fee_bps","maximum_adverse_slippage_bps","reservation_calculator_version","fee_currency_policy","fill_price_policy","internal_venue_implementation_sha256"},
})
INDEXES |= {"idx_phase7_configuration_account","idx_phase7_approval_events_run_time","idx_phase7_runtime_risk_run_time","idx_phase7_reservations_open","idx_phase7_reservation_events_time","idx_phase7_dispatch_ready","idx_phase7_dispatch_unknown","idx_phase7_dispatch_events_time","idx_phase7_cancel_unresolved","idx_phase7_balance_projection_run","idx_phase7_position_projection_run","idx_phase7_dispatch_prepared_recovery","idx_phase7_dispatch_ambiguous_recovery","idx_phase7_cancel_requested_recovery","idx_phase7_cancel_ambiguous_recovery","idx_phase7_market_evidence_latest","idx_phase7_order_budget_events_run","idx_phase7_reconciliation_bundle_run","idx_phase7_internal_commands_pending","idx_phase7_internal_events_replay","idx_phase7_order_observations_latest","idx_phase7_order_projection_active","idx_phase7_recovery_bundles_run","idx_phase7_expiry_unresolved","idx_phase7_price_source_identity","idx_phase7_pending_fill_recovery","idx_phase7_expiry_recovery_lease"}
IMMUTABLE_TRIGGERS={"phase7_manifest_immutable","phase7_configuration_immutable","phase7_approval_delete_immutable","phase7_approval_update_guard","phase7_submission_delete_immutable","phase7_submission_update_guard","phase7_dispatch_update_guard","phase7_reservation_update_guard","phase7_cancel_update_guard","phase7_runtime_risk_immutable","phase7_approval_events_append_only","phase7_reservation_events_append_only","phase7_dispatch_events_append_only","phase7_orders_append_only","phase7_fills_append_only","phase7_run_terminal_guard","phase7_killed_switch_guard","phase7_order_budget_events_append_only","phase7_market_evidence_immutable","phase7_reconciliation_bundles_immutable","phase7_order_projection_guard","phase7_expiry_guard","phase7_paper_internal_venue_commands_append_only","phase7_paper_internal_venue_events_append_only","phase7_paper_venue_order_observations_append_only","phase7_paper_recovery_observation_bundles_append_only","phase7_paper_fill_recovery_lineage_append_only","phase7_internal_venue_economics_immutable","phase7_expiry_recovery_update_guard","phase7_expiry_recovery_delete_immutable"}
def main():
    psycopg=importlib.import_module("psycopg"); c=psycopg.connect(host=os.environ["POSTGRES_HOST"],port=int(os.environ["POSTGRES_PORT"]),dbname=os.environ["POSTGRES_DB"],user=os.environ["POSTGRES_USER"],password=os.environ["POSTGRES_PASSWORD"],sslmode=os.environ.get("POSTGRES_SSLMODE","disable")); counts={}
    try:
        with c.cursor() as cur:
            for table,required in TABLES.items():
                cur.execute("SELECT column_name FROM information_schema.columns WHERE table_schema='execution' AND table_name=%s",(table,)); missing=required-{r[0] for r in cur.fetchall()}
                if missing:raise RuntimeError(f"execution.{table} missing columns: {sorted(missing)}")
                cur.execute(f"SELECT count(*) FROM execution.{table}"); counts[table]=cur.fetchone()[0]
            cur.execute("SELECT indexname FROM pg_indexes WHERE schemaname='execution'"); missing=INDEXES-{r[0] for r in cur.fetchall()}
            if missing:raise RuntimeError("missing Phase 7 indexes: "+",".join(sorted(missing)))
            cur.execute("SELECT count(*) FROM execution.paper_runs WHERE environment='live'")
            if cur.fetchone()[0]:raise RuntimeError("live paper rows are forbidden")
            cur.execute("SELECT tgname FROM pg_trigger WHERE NOT tgisinternal AND tgrelid IN (SELECT oid FROM pg_class WHERE relnamespace='execution'::regnamespace)");missing=IMMUTABLE_TRIGGERS-{r[0] for r in cur.fetchall()}
            if missing:raise RuntimeError("missing Phase 7 immutable triggers: "+",".join(sorted(missing)))
            cur.execute("SELECT count(*) FROM execution.paper_fills f LEFT JOIN execution.paper_order_submissions s ON s.submission_id=f.submission_id WHERE s.submission_id IS NULL")
            if cur.fetchone()[0]:raise RuntimeError("orphan paper fills detected")
        print("OK: Phase 7 catalog "+json.dumps({"table_count":len(TABLES),"index_count":len(INDEXES),"row_counts":counts},sort_keys=True))
    finally:c.close()
if __name__=="__main__":main()
