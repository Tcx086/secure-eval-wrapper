"""Injected-connection PostgreSQL persistence for Phase 7 paper trading."""
from __future__ import annotations
from dataclasses import asdict
from uuid import UUID
from secure_eval_wrapper.data_collection.hashing import sha256_payload
from secure_eval_wrapper.storage.postgres.alpha_signal_base import _PostgresRepositoryBase,_json_param

class Phase7ConflictError(RuntimeError):pass
class _Phase7BaseRepository(_PostgresRepositoryBase):
    def _execute(self,sql,params=()):
        cursor=self.connection.cursor()
        try:cursor.execute(sql,tuple(params))
        finally:
            close=getattr(cursor,"close",None)
            if close:close()
        if self.commit_on_write and hasattr(self.connection,"commit"):self.connection.commit()
    def _strict(self,table,id_col,id_value,columns,values,record_hash,hash_column="record_sha256"):
        names=",".join((id_col,*columns,hash_column)); marks=",".join(["%s", *[("%s::jsonb" if c.endswith("_jsonb") else "%s") for c in columns], "%s"]); sql=f"INSERT INTO {table} ({names}) VALUES ({marks}) ON CONFLICT ({id_col}) DO NOTHING RETURNING {id_col},{hash_column}"; cursor=self.connection.cursor()
        try:
            cursor.execute(sql,(id_value,*values,record_hash)); row=cursor.fetchone()
            if row is None:
                cursor.execute(f"SELECT {id_col},{hash_column} FROM {table} WHERE {id_col}=%s",(id_value,)); row=cursor.fetchone()
                if row is None or str(row[1])!=record_hash:raise Phase7ConflictError(f"{table} deterministic identity conflict")
        finally:
            close=getattr(cursor,"close",None)
            if close:close()
    def record_credential_reference(self,value):
        digest=value.reference_sha256; record=sha256_payload({"reference":digest,"loaded":value.loaded,"verified_at_utc":value.verified_at_utc,"permissions":value.permissions_summary})
        self._strict("execution.paper_credential_references","credential_reference_sha256",digest,("provider","alias","source_type","public_key_fingerprint","loaded","verified_at_utc","permissions_summary_jsonb"),(value.provider.value,value.alias,value.source_type.value,value.public_key_fingerprint,value.loaded,value.verified_at_utc,_json_param(value.permissions_summary)),record)
    def record_run(self,value,provider,environment,account_reference,configuration_sha256):
        self._strict("execution.paper_runs","paper_run_id",value.paper_run_id,("provider","environment","account_reference","state","configuration_sha256","manifest_id","started_at_utc","updated_at_utc","ended_at_utc","summary_jsonb"),(provider.value,environment.value,account_reference,value.state.value,configuration_sha256,value.manifest_id,value.started_at_utc,value.updated_at_utc,value.ended_at_utc,_json_param(value.summary)),value.record_sha256)
    def record_snapshot(self,value):
        self._strict("execution.paper_account_snapshots","snapshot_id",value.snapshot_id,("paper_run_id","account_reference","status","fetched_at_utc","venue_as_of_utc","account_mode","venue_sequence","source"),(value.paper_run_id,value.account_reference,value.status.value,value.fetched_at_utc,value.venue_as_of_utc,value.account_mode,value.venue_sequence,value.source),value.record_sha256)
        for balance in value.balances:self._execute("INSERT INTO execution.paper_balance_snapshots (snapshot_id,currency,total,available,reserved,record_sha256) VALUES (%s,%s,%s,%s,%s,%s) ON CONFLICT (snapshot_id,currency) DO NOTHING",(value.snapshot_id,balance.currency,balance.total,balance.available,balance.reserved,sha256_payload(asdict(balance))))
        for position in value.positions:self._execute("INSERT INTO execution.paper_position_snapshots (snapshot_id,series_identity_sha256,instrument_id,accounting_mode,quantity,average_entry_price,realized_pnl,funding,record_sha256) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT (snapshot_id,series_identity_sha256) DO NOTHING",(value.snapshot_id,position.series_identity.series_identity_sha256,position.series_identity.provider_instrument_id,position.accounting_mode.value,position.quantity,position.average_entry_price,position.realized_pnl,position.funding,sha256_payload({"snapshot":value.snapshot_id,"position":asdict(position)})))
        for client in value.open_client_order_ids:self._execute("INSERT INTO execution.paper_open_order_snapshots (snapshot_id,client_order_id,order_state,record_sha256) VALUES (%s,%s,%s,%s) ON CONFLICT (snapshot_id,client_order_id) DO NOTHING",(value.snapshot_id,client,"observed_open",sha256_payload({"snapshot":value.snapshot_id,"client":client})))
    def record_preflight(self,value):
        self._strict("execution.paper_preflight_reports","report_id",value.report_id,("paper_run_id","evaluated_at_utc","status","configuration_sha256","account_snapshot_sha256","implementation_sha256","endpoint_catalog_sha256","credential_reference_sha256","blockers_jsonb","warnings_jsonb"),(value.paper_run_id,value.evaluated_at_utc,value.status.value,value.configuration_sha256,value.account_snapshot_sha256,value.implementation_sha256,value.endpoint_catalog_sha256,value.credential_reference_sha256,_json_param(value.blockers),_json_param(value.warnings)),value.record_sha256)
        for check in value.checks:self._strict("execution.paper_preflight_checks","check_id",check.check_id,("report_id","check_name","status","required","reason_code","explanation","checked_at_utc","evidence_sha256"),(value.report_id,check.check_name,check.status.value,check.required,check.reason_code,check.explanation,check.checked_at_utc,check.evidence_sha256),check.record_sha256)
    def record_approval(self,value):
        self._strict("execution.paper_approvals","approval_id",value.approval_id,("paper_run_id","preflight_report_id","configuration_sha256","account_snapshot_sha256","credential_reference_sha256","provider","environment","allowed_instruments_jsonb","maximum_approved_total_notional","created_at_utc","expires_at_utc","approving_actor","approval_nonce","state"),(value.paper_run_id,value.preflight_report_id,value.configuration_sha256,value.account_snapshot_sha256,value.credential_reference_sha256,value.provider.value,value.environment.value,_json_param(value.allowed_instruments),value.maximum_approved_total_notional,value.created_at_utc,value.expires_at_utc,value.approving_actor,value.nonce,value.state.value),value.record_sha256)
    def record_manifest(self,value):
        self._strict("execution.paper_run_manifests","manifest_id",value.manifest_id,("paper_run_id","provider","environment","account_reference","implementation_sha256","repository_commit_sha","configuration_sha256","endpoint_catalog_sha256","preflight_report_id","approval_id","initial_account_snapshot_id","initial_account_snapshot_sha256","credential_reference_sha256","strategy_run_reference","allowed_instruments_jsonb","risk_limits_jsonb","start_at_utc","expected_maximum_duration_seconds","persistence_required","kill_switch_configuration_jsonb","parent_ids"),(value.paper_run_id,value.provider.value,value.environment.value,value.account_reference,value.implementation_sha256,value.repository_commit_sha,value.configuration_sha256,value.endpoint_catalog_sha256,value.preflight_report_id,value.approval_id,value.initial_account_snapshot_id,value.initial_account_snapshot_sha256,value.credential_reference.reference_sha256,value.strategy_run_reference,_json_param(value.allowed_instruments),_json_param(value.risk_limits),value.start_at_utc,value.expected_maximum_duration_seconds,value.persistence_required,_json_param(value.kill_switch_configuration),list(value.parent_ids)),value.manifest_sha256,hash_column="manifest_sha256")
    def record_kill_switch(self,value):
        cursor=self.connection.cursor()
        try:
            cursor.execute("INSERT INTO execution.paper_kill_switches (kill_switch_id,paper_run_id,state,reason,updated_at_utc,triggered_at_utc,evidence_sha256,incident_id,record_sha256) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT (kill_switch_id) DO UPDATE SET state=EXCLUDED.state,reason=EXCLUDED.reason,updated_at_utc=EXCLUDED.updated_at_utc,triggered_at_utc=EXCLUDED.triggered_at_utc,evidence_sha256=EXCLUDED.evidence_sha256,incident_id=EXCLUDED.incident_id,record_sha256=EXCLUDED.record_sha256",(value.kill_switch_id,value.paper_run_id,value.state.value,None if value.reason is None else value.reason.value,value.updated_at_utc,value.triggered_at_utc,value.evidence_sha256,value.incident_id,value.record_sha256))
        finally:
            close=getattr(cursor,"close",None)
            if close:close()
    def persist_start_run(self,*,run,configuration,credential_reference,snapshot,report,approval,manifest,kill_switch,lifecycle_event=None,fail_at=None):
        existing=self.get_active_run(run.paper_run_id)
        if existing is not None:
            stored=self.get_manifest(run.paper_run_id)
            if stored is None or str(stored["manifest_sha256"])!=manifest.manifest_sha256:raise Phase7ConflictError("paper run replay changed manifest")
            return
        with self.transaction():
            self.record_credential_reference(credential_reference)
            if fail_at=="credential":raise RuntimeError("injected credential reference failure")
            self.record_run(run,configuration.provider,configuration.environment,configuration.account_reference,configuration.config_sha256)
            if fail_at=="paper_run":raise RuntimeError("injected paper run failure")
            self.record_snapshot(snapshot)
            if fail_at in {"snapshot","balance","position"}:raise RuntimeError("injected snapshot child failure")
            self.record_preflight(report)
            if fail_at in {"preflight","check"}:raise RuntimeError("injected preflight child failure")
            self.record_approval(approval)
            if fail_at=="approval":raise RuntimeError("injected approval failure")
            self.record_manifest(manifest)
            if fail_at=="manifest":raise RuntimeError("injected manifest failure")
            self.record_kill_switch(kill_switch)
            if fail_at=="kill_switch":raise RuntimeError("injected kill switch failure")
            if lifecycle_event is not None:self.record_lifecycle(lifecycle_event)
    def record_submission_intent(self,submission,pre_submit_risk_sha256):
        existing=self._fetchone("SELECT economics_sha256 FROM execution.paper_order_submissions WHERE submission_id=%s",(submission.submission_id,))
        if existing is not None:
            if str(existing["economics_sha256"])!=submission.economics_sha256:raise Phase7ConflictError("client order replay changed economics")
            return
        with self.transaction():
            self._strict("execution.paper_order_submissions","submission_id",submission.submission_id,("paper_run_id","manifest_id","approval_id","order_intent_id","client_order_id","idempotency_key","series_identity_sha256","instrument_id","side","order_type","time_in_force","accounting_mode","quantity","reference_price","submitted_notional","limit_price","stop_price","submitted_at_utc","state","economics_sha256","pre_submit_risk_sha256"),(submission.paper_run_id,submission.manifest_id,submission.approval_id,submission.order_intent_id,submission.client_order_id,submission.idempotency_key,submission.series_identity.series_identity_sha256,submission.series_identity.provider_instrument_id,submission.side.value,submission.order_type.value,submission.time_in_force.value,submission.accounting_mode.value,submission.quantity,submission.reference_price,submission.submitted_notional,submission.limit_price,submission.stop_price,submission.submitted_at_utc,submission.state.value,submission.economics_sha256,pre_submit_risk_sha256),submission.record_sha256)
    def record_order(self,value):
        from .models import deterministic_paper_uuid
        record_id=deterministic_paper_uuid("paper-order-record",{"submission":value.submission_id,"sequence":value.venue_sequence})
        self._strict("execution.paper_orders","paper_order_record_id",record_id,("paper_run_id","submission_id","client_order_id","venue_order_id","state","original_quantity","cumulative_filled_quantity","remaining_quantity","average_fill_price","venue_sequence","created_at_utc","updated_at_utc","economics_sha256","operational_request_id","reject_reason"),(value.paper_run_id,value.submission_id,value.client_order_id,value.venue_order_id,value.state.value,value.quantity,value.cumulative_filled_quantity,value.remaining_quantity,value.average_fill_price,value.venue_sequence,value.created_at_utc,value.updated_at_utc,value.economics_sha256,value.operational_request_id,value.reject_reason),value.record_sha256)
    def record_lifecycle(self,event):
        self._strict("execution.paper_lifecycle_events","event_id",event.event_id,("paper_run_id","event_type","occurred_at_utc","deterministic_sequence","details_jsonb","parent_ids"),(event.paper_run_id,event.event_type,event.occurred_at_utc,event.sequence,_json_param(event.details),list(event.parent_ids)),event.record_sha256)
    def record_rate_limit_event(self,paper_run_id,provider,event,consecutive_failures=0):
        from .models import deterministic_paper_uuid
        event_id=deterministic_paper_uuid("rate-limit-event",{"run":paper_run_id,"operation":event["operation"],"at":event["at_utc"],"count":event["count"]}); digest=sha256_payload({"event_id":event_id,"event":event,"failures":consecutive_failures})
        with self.transaction():self._strict("execution.paper_rate_limit_events","rate_limit_event_id",event_id,("paper_run_id","provider","operation","request_count","local_limit","consecutive_failures","occurred_at_utc"),(paper_run_id,provider.value,event["operation"],event["count"],event["local_limit"],consecutive_failures,event["at_utc"]),digest)
    def update_run(self,value):
        with self.transaction():self._execute("UPDATE execution.paper_runs SET state=%s,updated_at_utc=%s,ended_at_utc=%s,summary_jsonb=%s::jsonb,record_sha256=%s WHERE paper_run_id=%s",(value.state.value,value.updated_at_utc,value.ended_at_utc,_json_param(value.summary),value.record_sha256,value.paper_run_id))
    def persist_submission_outcome(self,*,submission,order=None,transport_attempt=None,lifecycle_event=None,fail_at=None):
        with self.transaction():
            self._execute("UPDATE execution.paper_order_submissions SET state=%s,record_sha256=%s WHERE submission_id=%s",(submission.state.value,submission.record_sha256,submission.submission_id))
            if fail_at=="submission":raise RuntimeError("injected submission failure")
            if transport_attempt is not None:
                t=transport_attempt; self._strict("execution.paper_transport_attempts","transport_attempt_id",t["transport_attempt_id"],("paper_run_id","submission_id","request_id","request_type","method","approved_origin","approved_path","idempotency_key","attempted_at_utc","result_type","status_code","response_sha256","retryable","retry_ordinal"),(submission.paper_run_id,submission.submission_id,t["request_id"],t["request_type"],t["method"],t["approved_origin"],t["approved_path"],t.get("idempotency_key"),t["attempted_at_utc"],t["result_type"],t.get("status_code"),t.get("response_sha256"),t["retryable"],t.get("retry_ordinal",0)),t["record_sha256"])
            if fail_at=="transport":raise RuntimeError("injected transport failure")
            if order is not None:self.record_order(order)
            from .models import deterministic_paper_uuid
            event_id=deterministic_paper_uuid("paper-order-event",{"submission":submission.submission_id,"state":submission.state,"sequence":None if order is None else order.venue_sequence})
            self._strict("execution.paper_order_events","paper_order_event_id",event_id,("paper_run_id","submission_id","client_order_id","event_type","event_at_utc","venue_sequence","details_jsonb","parent_ids"),(submission.paper_run_id,submission.submission_id,submission.client_order_id,submission.state.value,submission.submitted_at_utc,None if order is None else order.venue_sequence,_json_param({"venue_order_id":None if order is None else order.venue_order_id}),[submission.submission_id]),sha256_payload({"event_id":event_id,"state":submission.state}))
            if fail_at=="venue_order":raise RuntimeError("injected venue order failure")
            if lifecycle_event is not None:self.record_lifecycle(lifecycle_event)
            if fail_at in {"order_event","lifecycle"}:raise RuntimeError("injected order event failure")
    def record_fill(self,value):
        self._strict("execution.paper_fills","fill_id",value.fill_id,("paper_run_id","submission_id","client_order_id","venue_order_id","venue_fill_id","series_identity_sha256","side","accounting_mode","quantity","price","fee_amount","fee_currency","filled_at_utc","venue_sequence","environment","accounting_applied"),(value.paper_run_id,value.submission_id,value.client_order_id,value.venue_order_id,value.venue_fill_id,value.series_identity.series_identity_sha256,value.side.value,value.accounting_mode.value,value.quantity,value.price,value.fee_amount,value.fee_currency,value.filled_at_utc,value.venue_sequence,value.environment.value,True),value.record_sha256)
    def record_reconciliation(self,value,differences):
        self._strict("execution.paper_reconciliations","reconciliation_id",value.reconciliation_id,("paper_run_id","local_snapshot_id","venue_snapshot_id","reconciled_at_utc","status","local_sequence","venue_sequence","difference_count","material_difference_count"),(value.paper_run_id,value.local_snapshot_id,value.venue_snapshot_id,value.reconciled_at_utc,value.status.value,value.local_sequence,value.venue_sequence,value.difference_count,value.material_difference_count),value.record_sha256)
        for d in differences:self._strict("execution.paper_reconciliation_differences","difference_id",d.difference_id,("reconciliation_id","difference_type","material","identity","local_value_jsonb","venue_value_jsonb","explanation"),(d.reconciliation_id,d.difference_type.value,d.material,d.identity,_json_param(d.local_value),_json_param(d.venue_value),d.explanation),d.record_sha256)
    def persist_fill_bundle(self,*,fill,order,local_snapshot,venue_snapshot,reconciliation,differences,lifecycle_event,fail_at=None):
        from .models import deterministic_paper_uuid
        with self.transaction():
            self.record_fill(fill)
            if fail_at=="fill":raise RuntimeError("injected fill failure")
            fee_id=deterministic_paper_uuid("paper-fee",{"fill":fill.fill_id}); fee_hash=sha256_payload({"fill":fill.fill_id,"amount":fill.fee_amount,"currency":fill.fee_currency})
            self._strict("execution.paper_fee_entries","fee_entry_id",fee_id,("fill_id","paper_run_id","amount","currency","occurred_at_utc"),(fill.fill_id,fill.paper_run_id,fill.fee_amount,fill.fee_currency,fill.filled_at_utc),fee_hash)
            if fail_at=="fee":raise RuntimeError("injected fee failure")
            self.record_order(order)
            self.record_snapshot(local_snapshot); self.record_snapshot(venue_snapshot)
            if fail_at in {"balance","position","snapshot"}:raise RuntimeError("injected account state failure")
            self.record_reconciliation(reconciliation,differences)
            if fail_at in {"reconciliation","difference"}:raise RuntimeError("injected reconciliation failure")
            self.record_lifecycle(lifecycle_event)
            if fail_at=="lifecycle":raise RuntimeError("injected lifecycle failure")
    def persist_reconciliation_bundle(self,*,bundle=None,local_snapshot=None,venue_snapshot=None,reconciliation=None,differences=(),kill_switch=None,kill_event=None,lifecycle_event=None,fail_at=None):
        if bundle is not None:local_snapshot=bundle.local_snapshot;venue_snapshot=bundle.venue_snapshot;reconciliation=bundle.reconciliation;differences=bundle.differences
        with self.transaction():
            self.record_snapshot(local_snapshot);self.record_snapshot(venue_snapshot);self.record_reconciliation(reconciliation,differences)
            if fail_at=="difference":raise RuntimeError("injected reconciliation difference failure")
            if kill_switch is not None:self.record_kill_switch(kill_switch)
            if lifecycle_event is not None:self.record_lifecycle(lifecycle_event)
            if fail_at=="kill_switch":raise RuntimeError("injected kill switch failure")
        return bundle
    def record_recovery(self,value,fail_at=None):
        with self.transaction():
            self._strict("execution.paper_recovery_records","recovery_id",value.recovery_id,("paper_run_id","submission_id","started_at_utc","completed_at_utc","status","action","explanation","parent_ids"),(value.paper_run_id,value.submission_id,value.started_at_utc,value.completed_at_utc,value.status.value,value.action,value.explanation,list(value.parent_ids)),value.record_sha256)
            if fail_at=="recovery":raise RuntimeError("injected recovery record failure")
    def persist_kill_event(self,value,event,fail_at=None):
        from .models import deterministic_paper_uuid
        with self.transaction():
            prior=self.get_kill_switch(value.paper_run_id); self.record_kill_switch(value)
            if fail_at=="kill_switch":raise RuntimeError("injected kill-switch projection failure")
            event_id=deterministic_paper_uuid("kill-event",{"kill":value.kill_switch_id,"state":value.state,"at":value.updated_at_utc,"event":event}); self._strict("execution.paper_kill_switch_events","kill_switch_event_id",event_id,("kill_switch_id","paper_run_id","prior_state","next_state","reason","occurred_at_utc","details_jsonb"),(value.kill_switch_id,value.paper_run_id,None if prior is None else prior["state"],value.state.value,None if value.reason is None else value.reason.value,value.updated_at_utc,_json_param(event)),sha256_payload(event))
            if fail_at=="kill_event":raise RuntimeError("injected kill-switch event failure")
    def get_active_run(self,paper_run_id):return self._fetchone("SELECT * FROM execution.paper_runs WHERE paper_run_id=%s AND state IN ('approved','running','paused','killed')",(paper_run_id,))
    def get_manifest(self,paper_run_id):return self._fetchone("SELECT * FROM execution.paper_run_manifests WHERE paper_run_id=%s",(paper_run_id,))
    def get_kill_switch(self,paper_run_id):return self._fetchone("SELECT * FROM execution.paper_kill_switches WHERE paper_run_id=%s",(paper_run_id,))
    def list_unresolved_submissions(self,paper_run_id):return self._fetchall("SELECT * FROM execution.paper_order_submissions WHERE paper_run_id=%s AND state IN ('prepared','dispatch_claimed','submitted','pending_ack','submission_unknown','pending_recovery','cancel_requested','cancel_pending','cancel_unknown') ORDER BY submitted_at_utc,submission_id",(paper_run_id,))
    def list_lifecycle(self,paper_run_id,start_utc,end_utc):return self._fetchall("SELECT * FROM execution.paper_lifecycle_events WHERE paper_run_id=%s AND occurred_at_utc>=%s AND occurred_at_utc<%s ORDER BY occurred_at_utc,deterministic_sequence",(paper_run_id,start_utc,end_utc))

# Compatibility-only row repository; operational CLIs use DurablePostgresPaperRepository.
PostgresPaperRepository = _Phase7BaseRepository
