"""PostgreSQL 16 catalog verification for Phase 7 paper persistence."""
import importlib,json,os
TABLES={
"paper_runs":{"paper_run_id","environment","manifest_id","record_sha256"},"paper_run_manifests":{"manifest_id","preflight_report_id","approval_id","manifest_sha256"},"paper_preflight_reports":{"report_id","status","credential_reference_sha256"},"paper_preflight_checks":{"check_id","report_id","required"},"paper_approvals":{"approval_id","expires_at_utc","state"},"paper_order_submissions":{"submission_id","manifest_id","approval_id","client_order_id","state"},"paper_orders":{"paper_order_record_id","venue_sequence","cumulative_filled_quantity"},"paper_order_events":{"paper_order_event_id","submission_id"},"paper_fills":{"fill_id","venue_fill_id","accounting_applied"},"paper_fee_entries":{"fee_entry_id","fill_id"},"paper_account_snapshots":{"snapshot_id","venue_as_of_utc"},"paper_balance_snapshots":{"snapshot_id","currency"},"paper_position_snapshots":{"snapshot_id","series_identity_sha256"},"paper_open_order_snapshots":{"snapshot_id","client_order_id"},"paper_reconciliations":{"reconciliation_id","status"},"paper_reconciliation_differences":{"difference_id","difference_type"},"paper_recovery_records":{"recovery_id","status"},"paper_kill_switches":{"kill_switch_id","state"},"paper_kill_switch_events":{"kill_switch_event_id","next_state"},"paper_rate_limit_events":{"rate_limit_event_id","operation"},"paper_transport_attempts":{"transport_attempt_id","result_type"},"paper_credential_references":{"credential_reference_sha256","public_key_fingerprint"},"paper_lifecycle_events":{"event_id","deterministic_sequence"}}
INDEXES={"idx_phase7_paper_runs_state_time","idx_phase7_paper_manifest_account","idx_phase7_paper_submissions_run_time","idx_phase7_paper_submissions_unknown","idx_phase7_paper_orders_open","idx_phase7_paper_fills_run_time","idx_phase7_paper_snapshots_account_time","idx_phase7_paper_reconciliation_time","idx_phase7_paper_differences_type","idx_phase7_paper_recovery_time","idx_phase7_paper_kill_events_time","idx_phase7_paper_transport_time","idx_phase7_paper_lifecycle_half_open"}
TABLES.update({
"paper_configuration_snapshots":{"configuration_sha256","configuration_jsonb","record_sha256"},
"paper_approval_state_events":{"approval_event_id","approval_id","next_state"},
"paper_run_risk_state":{"paper_run_id","daily_submitted_notional","open_order_count","lifecycle_sequence"},
"paper_runtime_risk_decisions":{"runtime_risk_decision_id","submission_id","evaluated_limits_jsonb","evidence_jsonb"},
"paper_reservations":{"reservation_id","remaining_amount","remaining_quantity","state"},
"paper_reservation_events":{"reservation_event_id","reservation_id","event_type"},
"paper_dispatch_outbox":{"dispatch_id","submission_id","state","claim_token"},
"paper_dispatch_events":{"dispatch_event_id","dispatch_id","event_type"},
"paper_cancel_outbox":{"cancel_id","dispatch_id","state"},
"paper_account_balance_projection":{"paper_run_id","currency","total","version"},
"paper_account_position_projection":{"paper_run_id","series_identity_sha256","series_identity_jsonb","quantity","version"},
})
INDEXES |= {"idx_phase7_configuration_account","idx_phase7_approval_events_run_time","idx_phase7_runtime_risk_run_time","idx_phase7_reservations_open","idx_phase7_reservation_events_time","idx_phase7_dispatch_ready","idx_phase7_dispatch_unknown","idx_phase7_dispatch_events_time","idx_phase7_cancel_unresolved","idx_phase7_balance_projection_run","idx_phase7_position_projection_run"}
IMMUTABLE_TRIGGERS={"phase7_manifest_immutable","phase7_configuration_immutable","phase7_approval_delete_immutable","phase7_approval_update_guard","phase7_submission_delete_immutable","phase7_submission_update_guard","phase7_dispatch_update_guard","phase7_reservation_update_guard","phase7_cancel_update_guard","phase7_runtime_risk_immutable","phase7_approval_events_append_only","phase7_reservation_events_append_only","phase7_dispatch_events_append_only","phase7_orders_append_only","phase7_fills_append_only","phase7_run_terminal_guard","phase7_killed_switch_guard"}
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
