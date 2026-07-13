"""PostgreSQL-authoritative Phase 7 durable dispatch and restart state."""
from __future__ import annotations
import json
from contextlib import contextmanager
from datetime import datetime,timedelta
from dataclasses import asdict,replace
from decimal import Decimal
from typing import Mapping
from secure_eval_wrapper.alpha.identity import SeriesIdentity
from secure_eval_wrapper.data_collection.hashing import sha256_payload
from secure_eval_wrapper.data_collection.models import InstrumentType
from secure_eval_wrapper.execution.models import AccountingMode,OrderSide,RiskDecisionStatus
from secure_eval_wrapper.storage.postgres.alpha_signal_base import _json_param
from .accounting import PaperAccounting,Reservation
from .enums import ApprovalState,KillSwitchState,PaperOrderState,PaperRunState,ReconciliationStatus,RecoveryStatus,VenueOrderState
from .models import PaperMarketDataEvidence,PaperOrderSubmission,PaperRecoveryObservationBundle,PaperRecoveryRecord,PaperReconciliationBundle,VenueFill,VenueOrder,VenuePosition,deterministic_paper_uuid
from .persistence import Phase7ConflictError,_Phase7BaseRepository
from .reservations import calculate_reservation,reduce_reservation,select_risk_price

class RuntimeRiskBlocked(PermissionError):
    def __init__(self,reasons):self.reasons=tuple(reasons);super().__init__("paper runtime risk blocked: "+", ".join(self.reasons))
class DispatchNotClaimable(RuntimeError):pass
class IncompleteRecoveryEvidence(RuntimeError):pass

class DurablePostgresPaperRepository(_Phase7BaseRepository):
    @contextmanager
    def transaction(self):
        depth=getattr(self,"_transaction_depth",0)
        if depth:
            self._transaction_depth=depth+1
            try:yield self
            finally:self._transaction_depth=depth
            return
        self._transaction_depth=1
        try:
            with super().transaction():yield self
        finally:self._transaction_depth=0
    @staticmethod
    def _map(value):
        if value is None:return {}
        if isinstance(value,Mapping):return dict(value)
        if isinstance(value,str):return dict(json.loads(value))
        return dict(value)
    @staticmethod
    def _config(configuration):return {n:getattr(configuration,n) for n in configuration.__dataclass_fields__}
    def _lock(self,run):self._execute("SELECT pg_advisory_xact_lock(hashtextextended(%s,0))",(str(run),))
    def _configuration(self,c,at):
        payload=self._config(c); self._strict("execution.paper_configuration_snapshots","configuration_sha256",c.config_sha256,("provider","environment","account_reference","configuration_jsonb","created_at_utc"),(c.provider.value,c.environment.value,c.account_reference,_json_param(payload),at),sha256_payload({"id":c.config_sha256,"configuration":payload}))
    def _approval_event(self,a,prior,next_state,at,reason):
        binding=sha256_payload({"run":a.paper_run_id,"report":a.preflight_report_id,"config":a.configuration_sha256,"snapshot":a.account_snapshot_sha256,"credential":a.credential_reference_sha256,"provider":a.provider,"environment":a.environment})
        event_id=deterministic_paper_uuid("approval-event",{"approval":a.approval_id,"next":next_state,"at":at,"reason":reason})
        self._strict("execution.paper_approval_state_events","approval_event_id",event_id,("approval_id","paper_run_id","prior_state","next_state","occurred_at_utc","reason_code","binding_sha256"),(a.approval_id,a.paper_run_id,prior,next_state,at,reason,binding),sha256_payload({"event":event_id,"binding":binding}))
    def persist_start_run(self,*,run,configuration,credential_reference,snapshot,report,approval,manifest,kill_switch,lifecycle_event=None,fail_at=None):
        if approval.state is not ApprovalState.VALID:raise PermissionError("run start requires valid approval")
        with self.transaction():
            self._lock(run.paper_run_id)
            existing=self._fetchone("SELECT * FROM execution.paper_runs WHERE paper_run_id=%s FOR UPDATE",(run.paper_run_id,))
            stored_manifest=self.get_manifest(run.paper_run_id)
            if stored_manifest is not None:
                if str(stored_manifest["manifest_sha256"])!=manifest.manifest_sha256:raise Phase7ConflictError("paper run replay changed manifest")
                if existing and existing["state"]=="running":return False
                raise Phase7ConflictError("manifest exists without a running run projection")
            if existing is not None and existing["state"] not in ("created","approved"):raise Phase7ConflictError("paper run cannot start from "+str(existing["state"]))
            now=run.started_at_utc
            if now>=approval.expires_at_utc:raise PermissionError("paper approval expired")
            bindings=(approval.paper_run_id==run.paper_run_id and approval.preflight_report_id==report.report_id and approval.configuration_sha256==configuration.config_sha256 and approval.account_snapshot_sha256==snapshot.record_sha256 and approval.credential_reference_sha256==credential_reference.reference_sha256 and approval.provider==configuration.provider and approval.environment==configuration.environment and manifest.approval_id==approval.approval_id and manifest.preflight_report_id==report.report_id and manifest.initial_account_snapshot_id==snapshot.snapshot_id)
            if not bindings:raise PermissionError("approval/run/manifest binding mismatch")
            self.record_credential_reference(credential_reference);self._configuration(configuration,now)
            if fail_at=="credential":raise RuntimeError("injected credential/configuration failure")
            if existing is None:self.record_run(run,configuration.provider,configuration.environment,configuration.account_reference,configuration.config_sha256)
            else:
                if str(existing["configuration_sha256"])!=configuration.config_sha256 or existing["provider"]!=configuration.provider.value or existing["environment"]!=configuration.environment.value:raise Phase7ConflictError("created run authority changed before start")
            if fail_at=="paper_run":raise RuntimeError("injected paper run failure")
            self.record_snapshot(snapshot)
            if fail_at in {"snapshot","balance","position"}:raise RuntimeError("injected snapshot failure")
            self.record_preflight(report)
            if fail_at in {"preflight","check"}:raise RuntimeError("injected preflight failure")
            self.record_approval(approval)
            stored_approval=self._fetchone("SELECT * FROM execution.paper_approvals WHERE approval_id=%s FOR UPDATE",(approval.approval_id,))
            if stored_approval is None or stored_approval["state"]!="valid" or stored_approval["expires_at_utc"]<=now:raise PermissionError("approval concurrently consumed or expired")
            if fail_at=="approval":raise RuntimeError("injected approval failure")
            consumed=replace(approval,state=ApprovalState.CONSUMED);cur=self.connection.cursor()
            try:
                cur.execute("UPDATE execution.paper_approvals SET state='consumed',record_sha256=%s WHERE approval_id=%s AND state='valid' AND expires_at_utc>%s RETURNING approval_id",(consumed.record_sha256,approval.approval_id,now))
                if cur.fetchone() is None:raise PermissionError("approval concurrently consumed or expired")
            finally:cur.close()
            self._approval_event(approval,"valid","consumed",now,"run_started");self.record_manifest(manifest)
            if fail_at=="manifest":raise RuntimeError("injected manifest failure")
            self.record_kill_switch(kill_switch)
            if fail_at=="kill_switch":raise RuntimeError("injected kill switch failure")
            for b in snapshot.balances:self._execute("INSERT INTO execution.paper_account_balance_projection (paper_run_id,currency,total,version,updated_at_utc,record_sha256) VALUES (%s,%s,%s,0,%s,%s) ON CONFLICT (paper_run_id,currency) DO NOTHING",(run.paper_run_id,b.currency,b.total,now,sha256_payload({"run":run.paper_run_id,"balance":asdict(b)})))
            for p in snapshot.positions:self._execute("INSERT INTO execution.paper_account_position_projection (paper_run_id,series_identity_sha256,instrument_id,series_identity_jsonb,accounting_mode,quantity,average_entry_price,realized_pnl,funding,version,updated_at_utc,record_sha256) VALUES (%s,%s,%s,%s::jsonb,%s,%s,%s,%s,%s,0,%s,%s) ON CONFLICT (paper_run_id,series_identity_sha256) DO NOTHING",(run.paper_run_id,p.series_identity.series_identity_sha256,p.series_identity.provider_instrument_id,_json_param(p.series_identity.as_dict()),p.accounting_mode.value,p.quantity,p.average_entry_price,p.realized_pnl,p.funding,now,sha256_payload({"run":run.paper_run_id,"position":asdict(p)})))
            equity=next((b.total for b in snapshot.balances if b.currency==configuration.base_currency),Decimal(0));digest=sha256_payload({"run":run.paper_run_id,"version":0,"equity":equity})
            self._execute("INSERT INTO execution.paper_run_risk_state (paper_run_id,version,trading_day,rate_window_started_at_utc,initial_equity,current_equity,high_watermark_equity,latest_market_data_at_utc,latest_account_snapshot_at_utc,latest_reconciliation_at_utc,latest_reconciliation_status,venue_clock_skew_seconds,updated_at_utc,record_sha256) VALUES (%s,0,%s,%s,%s,%s,%s,NULL,%s,%s,'reconciled',0,%s,%s) ON CONFLICT (paper_run_id) DO NOTHING",(run.paper_run_id,now.date(),now,equity,equity,equity,snapshot.venue_as_of_utc,report.evaluated_at_utc,now,digest))
            self._execute("UPDATE execution.paper_runs SET state='running',manifest_id=%s,started_at_utc=%s,updated_at_utc=%s,record_sha256=%s WHERE paper_run_id=%s AND state IN ('created','approved','running')",(manifest.manifest_id,run.started_at_utc,run.updated_at_utc,run.record_sha256,run.paper_run_id))
            if lifecycle_event is not None:
                self.record_lifecycle(lifecycle_event);self._execute("UPDATE execution.paper_run_risk_state SET lifecycle_sequence=GREATEST(lifecycle_sequence,%s),updated_at_utc=%s,record_sha256=%s WHERE paper_run_id=%s",(lifecycle_event.sequence,lifecycle_event.occurred_at_utc,lifecycle_event.record_sha256,lifecycle_event.paper_run_id))
            return True
    def _submission(self,s,risk_hash):
        cols=("paper_run_id","manifest_id","approval_id","order_intent_id","client_order_id","idempotency_key","series_identity_sha256","instrument_id","side","order_type","time_in_force","accounting_mode","quantity","reference_price","submitted_notional","limit_price","stop_price","submitted_at_utc","state","economics_sha256","pre_submit_risk_sha256","market_evidence_price","risk_reference_price","worst_case_order_price","risk_notional","reservation_notional","price_deviation_bps","price_source_sha256","price_calculator_version")
        vals=(s.paper_run_id,s.manifest_id,s.approval_id,s.order_intent_id,s.client_order_id,s.idempotency_key,s.series_identity.series_identity_sha256,s.series_identity.provider_instrument_id,s.side.value,s.order_type.value,s.time_in_force.value,s.accounting_mode.value,s.quantity,s.reference_price,s.submitted_notional,s.limit_price,s.stop_price,s.submitted_at_utc,s.state.value,s.economics_sha256,risk_hash,s.market_evidence_price,s.risk_reference_price,s.worst_case_order_price,s.risk_notional,s.reservation_notional,s.price_deviation_bps,s.price_source_sha256,s.price_calculator_version)
        self._strict("execution.paper_order_submissions","submission_id",s.submission_id,cols,vals,s.record_sha256)
    @staticmethod
    def _assets(identity):
        p=identity.canonical_symbol.replace("/","-").split("-")
        if len(p)<2:raise ValueError("paper Spot symbol must identify base and quote")
        return p[0].upper(),p[1].upper()
    def _age(self,run,states,now):
        row=self._fetchone("SELECT min(submitted_at_utc) oldest FROM execution.paper_order_submissions WHERE paper_run_id=%s AND state=ANY(%s)",(run,list(states)))
        return Decimal(0) if not row or row["oldest"] is None else Decimal(str((now-row["oldest"]).total_seconds()))
    def _risk(self,c,a,intent,s,state,positions,balances,reservations,evidence,now):
        reasons=[];checks={}
        def check(name,ok,observed=None,limit=None):
            checks[name]={"passed":bool(ok),"observed":observed,"limit":limit}
            if not ok:reasons.append(name)
        ident=intent.series_identity;itype=ident.instrument_type.value;notional=Decimal(s.risk_notional);risk_price=Decimal(s.worst_case_order_price)
        check("allowed_instrument",ident.canonical_symbol in c.allowed_instruments,ident.canonical_symbol,c.allowed_instruments)
        check("allowed_instrument_type",itype in c.allowed_instrument_types,itype,c.allowed_instrument_types)
        check("allowed_settlement_asset",ident.settlement_asset in c.allowed_settlement_assets,ident.settlement_asset,c.allowed_settlement_assets)
        check("allowed_order_type",intent.order_type in c.allowed_order_types,intent.order_type.value,[x.value for x in c.allowed_order_types])
        check("perpetual_policy",itype!="perpetual_swap" or c.allow_perpetual,itype,c.allow_perpetual)
        check("maximum_reference_price_deviation",s.price_deviation_bps<=c.maximum_reference_price_deviation_bps,s.price_deviation_bps,c.maximum_reference_price_deviation_bps)
        current=next((Decimal(str(p["quantity"])) for p in positions if str(p["series_identity_sha256"])==ident.series_identity_sha256),Decimal(0));projected=current+intent.quantity*intent.side.sign
        check("spot_short_prohibition",intent.accounting_mode is not AccountingMode.SPOT or c.allow_short or projected>=0,projected,0)
        check("intent_position_matches_persisted",intent.current_quantity==current,intent.current_quantity,current)
        check("maximum_order_notional",notional<=c.maximum_order_notional,notional,c.maximum_order_notional)
        projected_notional=abs(projected)*risk_price
        check("maximum_position_notional_per_instrument",projected_notional<=c.maximum_position_notional_per_instrument,projected_notional,c.maximum_position_notional_per_instrument)
        marks=self._map(evidence.get("marks"));gross=Decimal(0);net=Decimal(0);missing=[];seen=False
        for p in positions:
            key=str(p["series_identity_sha256"]);qty=Decimal(str(p["quantity"]))
            if key==ident.series_identity_sha256:qty=projected;mark=risk_price;seen=True
            else:
                raw=marks.get(key) or marks.get(str(p["instrument_id"]))
                if raw is None and qty!=0:missing.append(key);continue
                mark=Decimal(str(raw or 0))
            gross+=abs(qty*mark);net+=qty*mark
        if not seen:gross+=abs(projected*risk_price);net+=projected*risk_price
        check("exposure_mark_evidence",not missing,missing,"required")
        check("maximum_gross_exposure",gross<=c.maximum_gross_exposure,gross,c.maximum_gross_exposure);check("maximum_net_exposure",abs(net)<=c.maximum_net_exposure,abs(net),c.maximum_net_exposure)
        check("maximum_open_order_count",int(state["open_order_count"])+1<=c.maximum_open_order_count,int(state["open_order_count"])+1,c.maximum_open_order_count)
        check("maximum_orders_per_minute",int(state["orders_in_current_minute"])+1<=c.maximum_orders_per_minute,int(state["orders_in_current_minute"])+1,c.maximum_orders_per_minute)
        check("maximum_daily_submitted_notional",Decimal(str(state["daily_submitted_notional"]))+notional<=c.maximum_daily_submitted_notional,Decimal(str(state["daily_submitted_notional"]))+notional,c.maximum_daily_submitted_notional)
        check("approval_maximum_total_notional",Decimal(str(state["approval_submitted_notional"]))+notional<=a.maximum_approved_total_notional,Decimal(str(state["approval_submitted_notional"]))+notional,a.maximum_approved_total_notional)
        loss=max(Decimal(0),-Decimal(str(state["daily_realized_pnl"])));draw=Decimal(str(state["high_watermark_equity"]))-Decimal(str(state["current_equity"]))
        check("maximum_daily_realized_loss",loss<=c.maximum_daily_realized_loss,loss,c.maximum_daily_realized_loss);check("maximum_current_drawdown",draw<=c.maximum_current_drawdown,draw,c.maximum_current_drawdown)
        def age(name,value,limit):
            seconds=None if value is None else Decimal(str((now-value).total_seconds()));check(name+"_evidence",seconds is not None,seconds,"required");check(name+"_staleness",seconds is not None and 0<=seconds<=limit,seconds,limit)
        age("market_data",evidence.get("market_data_at_utc"),c.stale_market_data_threshold_seconds);age("account_snapshot",evidence.get("account_snapshot_at_utc"),c.stale_account_snapshot_threshold_seconds)
        rat=evidence.get("reconciliation_at_utc");rage=None if rat is None else Decimal(str((now-rat).total_seconds()));rstatus=evidence.get("reconciliation_status")
        check("reconciliation_evidence",rage is not None and rstatus is not None,{"age":rage,"status":rstatus},"required");check("reconciliation_status",rstatus==ReconciliationStatus.RECONCILED.value,rstatus,"reconciled");check("reconciliation_age",rage is not None and 0<=rage<=c.maximum_reconciliation_age_seconds,rage,c.maximum_reconciliation_age_seconds)
        ua=evidence.get("oldest_unknown_age_seconds");aa=evidence.get("oldest_unacknowledged_age_seconds")
        check("maximum_unknown_order_age",ua is not None and Decimal(str(ua))<=c.maximum_unknown_order_duration_seconds,ua,c.maximum_unknown_order_duration_seconds);check("maximum_unacknowledged_order_age",aa is not None and Decimal(str(aa))<=c.maximum_unacknowledged_order_duration_seconds,aa,c.maximum_unacknowledged_order_duration_seconds)
        check("maximum_consecutive_transport_failures",int(state["consecutive_transport_failures"])<=c.maximum_consecutive_transport_failures,state["consecutive_transport_failures"],c.maximum_consecutive_transport_failures)
        skew=evidence.get("clock_skew_seconds");check("clock_skew_evidence",skew is not None,skew,"required");check("maximum_clock_skew",skew is not None and abs(Decimal(str(skew)))<=c.maximum_clock_skew_seconds,skew,c.maximum_clock_skew_seconds)
        run_age=Decimal(str((now-state["run_started_at_utc"]).total_seconds()));check("maximum_run_duration",0<=run_age<=c.maximum_run_duration_seconds,run_age,c.maximum_run_duration_seconds)
        requirement=calculate_reservation(s,maximum_fee_bps=Decimal(str(evidence.get("maximum_fee_bps",10))),maximum_adverse_slippage_bps=c.maximum_adverse_slippage_bps)
        total=next((Decimal(str(x["total"])) for x in balances if str(x["currency"])==requirement.currency),None);reserved=sum((Decimal(str(x["remaining_amount"])) for x in reservations if str(x["currency"])==requirement.currency and x["state"]=="open"),Decimal(0));available=None if total is None else total-reserved
        check("reservation_risk_notional_consistency",requirement.risk_notional==notional,requirement.risk_notional,notional)
        check("reservation_balance_evidence",total is not None,available,"required");check("durable_reservation_available",available is not None and available>=requirement.amount,available,requirement.amount)
        return tuple(dict.fromkeys(reasons)),checks,requirement
    def _verify_market_data_source(self,evidence,configuration=None):
        reasons=[]
        if evidence.source_kind=="fixture":
            if configuration is not None and (configuration.provider.value!="internal" or configuration.environment.value!="paper_internal"):reasons.append("fixture_market_data_forbidden")
            return tuple(reasons)
        specs={
            "market_data.validated_bars":("bar_id","bar_close_time_utc",{"close":"close","open":"open","high":"high","low":"low"}),
            "market_data.validated_trades":("trade_id","traded_at_utc",{"last":"price"}),
            "market_data.funding_rates":("funding_rate_id","funding_time_utc",{"mark":"mark_price","index":"index_price"}),
        }
        spec=specs.get(evidence.source_table)
        if spec is None:return ("market_data_source_table_forbidden",)
        id_column,time_column,price_columns=spec;price_column=price_columns.get(evidence.price_type)
        if price_column is None:return ("market_data_price_type_mismatch",)
        row=self._fetchone(f"SELECT to_jsonb(s) source_row FROM {evidence.source_table} s WHERE {id_column}=%s",(evidence.source_row_id,))
        if row is None:return ("market_data_source_row_missing",)
        source=self._map(row["source_row"]);provenance=self._map(source.get("provenance_jsonb"))
        report=self._fetchone("SELECT validation_report_id,status,report_sha256,report_jsonb FROM data_quality.validation_reports WHERE validation_report_id=%s",(evidence.validation_report_id,))
        if report is None:reasons.append("market_data_validation_report_missing")
        elif str(report["validation_report_id"])!=str(source.get("validation_report_id")) or report["status"] not in ("accepted","accepted_with_warnings"):reasons.append("market_data_validation_report_mismatch")
        if source.get("validation_status") not in ("accepted","accepted_with_warnings") or evidence.validation_status!="accepted":reasons.append("market_data_source_not_accepted")
        raw_source_time=source.get(time_column);source_time=raw_source_time if isinstance(raw_source_time,datetime) else None if raw_source_time is None else datetime.fromisoformat(str(raw_source_time).replace("Z","+00:00"))
        if source_time!=evidence.observed_at_utc:reasons.append("market_data_timestamp_mismatch")
        raw_price=source.get(price_column)
        if raw_price is None or Decimal(str(raw_price))!=evidence.price:reasons.append("market_data_price_mismatch")
        source_exchange=str(source.get("exchange") or provenance.get("exchange") or "")
        source_provider=str(source.get("provider_name") or provenance.get("provider_name") or provenance.get("provider") or "")
        source_instrument=str(source.get("provider_instrument_id") or provenance.get("provider_instrument_id") or source.get("symbol") or "")
        source_type=str(source.get("instrument_type") or provenance.get("instrument_type") or "")
        if source_exchange and source_exchange!=evidence.exchange:reasons.append("market_data_exchange_mismatch")
        if source_provider and source_provider not in (evidence.provider,evidence.series_identity.provider_name):reasons.append("market_data_provider_mismatch")
        if source_instrument and source_instrument not in (evidence.provider_instrument_id,evidence.instrument,evidence.series_identity.canonical_symbol):reasons.append("market_data_instrument_mismatch")
        if source_type and source_type!=evidence.instrument_type:reasons.append("market_data_instrument_type_mismatch")
        if evidence.source_table=="market_data.validated_bars":
            if not bool(source.get("is_final")):reasons.append("market_data_non_final")
            raw_close=source.get("bar_close_time_utc");source_close=None if raw_close is None else datetime.fromisoformat(str(raw_close).replace("Z","+00:00"))
            if source_close is None or evidence.available_at_utc<source_close:reasons.append("market_data_availability_mismatch")
            if source.get("timeframe")!=evidence.series_identity.timeframe:reasons.append("market_data_timeframe_mismatch")
        source_ids=tuple(str(value) for value in (source.get("source_observation_ids") or ()))
        if not source_ids:reasons.append("market_data_raw_source_missing")
        else:
            raw_rows=self._fetchall("SELECT observation_id,source_sha256 FROM market_data.raw_source_observations WHERE observation_id=ANY(%s::uuid[])",(list(source_ids),))
            hashes={str(value["source_sha256"]) for value in raw_rows}
            if len(raw_rows)!=len(source_ids):reasons.append("market_data_raw_source_missing")
            if evidence.source_sha256 not in hashes:reasons.append("market_data_source_hash_mismatch")
        expected_normalized=source.get("record_sha256") or provenance.get("normalized_record_sha256") or provenance.get("record_sha256")
        if expected_normalized is None or str(expected_normalized)!=evidence.normalized_record_sha256:reasons.append("market_data_normalized_hash_mismatch")
        quarantined=self._fetchone("SELECT EXISTS(SELECT 1 FROM data_quality.quarantine_decisions WHERE validation_report_id=%s AND observation_id=ANY(%s::uuid[])) value",(evidence.validation_report_id,list(source_ids)))
        if quarantined and bool(quarantined["value"]):reasons.append("market_data_quarantined")
        return tuple(dict.fromkeys(reasons))

    def record_market_data_evidence(self,paper_run_id,evidence:PaperMarketDataEvidence,*,recorded_at_utc,configuration=None):
        with self.transaction():
            self._lock(paper_run_id)
            source_reasons=self._verify_market_data_source(evidence,configuration)
            if source_reasons:raise RuntimeRiskBlocked(source_reasons)
            self._strict("execution.paper_market_data_evidence","market_evidence_id",evidence.evidence_id,("paper_run_id","series_identity_sha256","series_identity_jsonb","provider","instrument","event_type","observation_id","observed_at_utc","available_at_utc","is_final","validation_status","source_sha256","observation_sha256","recorded_at_utc","source_kind","exchange","provider_instrument_id","instrument_type","source_table","source_row_id","validation_report_id","price","price_type","quote_currency","normalized_record_sha256"),(paper_run_id,evidence.series_identity.series_identity_sha256,_json_param(evidence.series_identity.as_dict()),evidence.provider,evidence.instrument,evidence.event_type,evidence.observation_id,evidence.observed_at_utc,evidence.available_at_utc,evidence.is_final,evidence.validation_status,evidence.source_sha256,evidence.record_sha256,recorded_at_utc,evidence.source_kind,evidence.exchange,evidence.provider_instrument_id,evidence.instrument_type,evidence.source_table,evidence.source_row_id,evidence.validation_report_id,evidence.price,evidence.price_type,evidence.quote_currency,evidence.normalized_record_sha256),evidence.evidence_sha256,hash_column="evidence_sha256")
            if evidence.is_final and evidence.validation_status=="accepted":
                self._execute("UPDATE execution.paper_run_risk_state SET latest_market_data_at_utc=%s,latest_market_evidence_id=%s,latest_market_evidence_sha256=%s,updated_at_utc=%s,record_sha256=%s WHERE paper_run_id=%s AND (latest_market_data_at_utc IS NULL OR latest_market_data_at_utc<=%s)",(evidence.observed_at_utc,evidence.evidence_id,evidence.evidence_sha256,recorded_at_utc,sha256_payload({"run":paper_run_id,"market_evidence":evidence.evidence_id}),paper_run_id,evidence.observed_at_utc))
        return evidence
    def latest_market_data_evidence(self,paper_run_id,series_identity):
        row=self._fetchone("SELECT * FROM execution.paper_market_data_evidence WHERE paper_run_id=%s AND series_identity_sha256=%s ORDER BY observed_at_utc DESC,market_evidence_id DESC LIMIT 1",(paper_run_id,series_identity.series_identity_sha256))
        if row is None:return None
        return PaperMarketDataEvidence(self._identity(row["series_identity_jsonb"]),str(row["provider"]),str(row["instrument"]),str(row["event_type"]),str(row["observation_id"]),row["observed_at_utc"],row["available_at_utc"],bool(row["is_final"]),str(row["validation_status"]),str(row["source_sha256"]),str(row["observation_sha256"]),evidence_id=row["market_evidence_id"],exchange=str(row["exchange"]),provider_instrument_id=str(row["provider_instrument_id"]),instrument_type=str(row["instrument_type"]),source_table=row.get("source_table"),source_row_id=str(row["source_row_id"]),validation_report_id=row.get("validation_report_id"),price=None if row.get("price") is None else Decimal(str(row["price"])),price_type=str(row["price_type"]),quote_currency=str(row["quote_currency"]),normalized_record_sha256=str(row["normalized_record_sha256"]),source_kind=str(row["source_kind"]))
    def prepare_submission(self,*,configuration,approval,manifest,intent,risk_decision,now,market_evidence=None,evidence=None):
        if risk_decision.status is not RiskDecisionStatus.ACCEPTED:raise PermissionError("accepted pre-submit risk required")
        client="sew"+deterministic_paper_uuid("client-order",{"run":manifest.paper_run_id,"intent":intent.order_intent_id}).hex[:29];econ=sha256_payload({"series_identity":intent.series_identity.as_dict(),"side":intent.side,"order_type":intent.order_type,"time_in_force":intent.time_in_force,"accounting_mode":intent.accounting_mode,"quantity":intent.quantity,"limit_price":intent.limit_price,"stop_price":intent.stop_price})
        prepared=PaperOrderSubmission(manifest.paper_run_id,manifest.manifest_id,approval.approval_id,intent.order_intent_id,client,client,intent.series_identity,intent.side,intent.order_type,intent.time_in_force,intent.accounting_mode,intent.quantity,intent.reference_price,intent.quantity*intent.reference_price,now,econ,state=PaperOrderState.PREPARED,limit_price=intent.limit_price,stop_price=intent.stop_price,price_source_sha256="0"*64,price_calculator_version="missing-market-evidence");blocked=()
        with self.transaction():
            self._lock(manifest.paper_run_id);old=self._fetchone("SELECT * FROM execution.paper_order_submissions WHERE submission_id=%s",(prepared.submission_id,))
            if old:
                if str(old["economics_sha256"])!=econ:raise Phase7ConflictError("stable submission identity changed economics")
                return self._submission_from_row(old),True
            state=self._fetchone("SELECT r.state run_state,r.started_at_utc run_started_at_utc,r.configuration_sha256 run_configuration_sha256,m.manifest_sha256,m.approval_id manifest_approval_id,a.state approval_state,a.expires_at_utc,a.configuration_sha256 approval_configuration_sha256,a.account_snapshot_sha256 approval_account_snapshot_sha256,a.credential_reference_sha256 approval_credential_reference_sha256,a.provider approval_provider,a.environment approval_environment,a.allowed_instruments_jsonb approval_allowed_instruments,a.maximum_approved_total_notional approval_maximum_total_notional,k.state kill_state,rs.* FROM execution.paper_runs r JOIN execution.paper_run_manifests m ON m.paper_run_id=r.paper_run_id JOIN execution.paper_approvals a ON a.approval_id=m.approval_id JOIN execution.paper_kill_switches k ON k.paper_run_id=r.paper_run_id JOIN execution.paper_run_risk_state rs ON rs.paper_run_id=r.paper_run_id WHERE r.paper_run_id=%s FOR UPDATE OF r,a,k,rs",(manifest.paper_run_id,))
            lineage=(intent.run_id==manifest.paper_run_id and risk_decision.run_id==manifest.paper_run_id and risk_decision.order_intent_id==intent.order_intent_id and risk_decision.series_identity.series_identity_sha256==intent.series_identity.series_identity_sha256 and risk_decision.stage.value=="pre_submit")
            if not lineage:raise PermissionError("intent and accepted risk lineage do not match the durable run")
            if not state:raise PermissionError("paper run authority is missing")
            if state["run_state"]!="running" or state["kill_state"]!="armed":raise PermissionError("run is not active")
            authority=(configuration.config_sha256==str(state["run_configuration_sha256"])==manifest.configuration_sha256==str(state["approval_configuration_sha256"]) and approval.approval_id==state["manifest_approval_id"]==manifest.approval_id and approval.configuration_sha256==str(state["approval_configuration_sha256"]) and approval.account_snapshot_sha256==str(state["approval_account_snapshot_sha256"]) and approval.credential_reference_sha256==str(state["approval_credential_reference_sha256"]) and approval.provider.value==str(state["approval_provider"]) and approval.environment.value==str(state["approval_environment"]) and tuple(approval.allowed_instruments)==tuple(state["approval_allowed_instruments"]) and approval.maximum_approved_total_notional==Decimal(str(state["approval_maximum_total_notional"])))
            if not authority:raise PermissionError("configuration or approval does not match PostgreSQL authority")
            if state["approval_state"]!="consumed" or now>=state["expires_at_utc"]:raise PermissionError("approval is not current and consumed")
            if str(state["manifest_sha256"])!=manifest.manifest_sha256:raise PermissionError("manifest authority mismatch")
            if state["trading_day"]!=now.date():state["daily_submitted_notional"]=Decimal(0);state["daily_realized_pnl"]=Decimal(0)
            if (now-state["rate_window_started_at_utc"]).total_seconds()>=60:state["orders_in_current_minute"]=0;state["cancellations_in_current_minute"]=0;state["rate_window_started_at_utc"]=now
            positions=self._fetchall("SELECT * FROM execution.paper_account_position_projection WHERE paper_run_id=%s FOR UPDATE",(manifest.paper_run_id,));balances=self._fetchall("SELECT * FROM execution.paper_account_balance_projection WHERE paper_run_id=%s FOR UPDATE",(manifest.paper_run_id,));reservations=self._fetchall("SELECT * FROM execution.paper_reservations WHERE paper_run_id=%s AND state='open' FOR UPDATE",(manifest.paper_run_id,))
            source_reasons=() if market_evidence is None else self._verify_market_data_source(market_evidence,configuration)
            if market_evidence is not None and not source_reasons:self.record_market_data_evidence(manifest.paper_run_id,market_evidence,recorded_at_utc=now,configuration=configuration)
            market_evidence=market_evidence or self.latest_market_data_evidence(manifest.paper_run_id,intent.series_identity)
            expected_currency=intent.series_identity.settlement_asset or self._assets(intent.series_identity)[1]
            market_reasons=("market_data_missing",) if market_evidence is None else tuple((*source_reasons,*market_evidence.rejection_reasons(series_identity=intent.series_identity,at_utc=now,maximum_age_seconds=configuration.stale_market_data_threshold_seconds,expected_currency=expected_currency,allow_fixture=configuration.provider.value=="internal" and configuration.environment.value=="paper_internal")))
            if market_evidence is not None:
                selection=select_risk_price(intent,market_evidence,maximum_adverse_slippage_bps=configuration.maximum_adverse_slippage_bps)
                prepared=replace(prepared,submitted_notional=selection.risk_notional,market_evidence_price=selection.market_evidence_price,risk_reference_price=selection.risk_reference_price,worst_case_order_price=selection.worst_case_order_price,risk_notional=selection.risk_notional,reservation_notional=selection.reservation_notional,price_deviation_bps=selection.price_deviation_bps,price_source_sha256=selection.price_source_sha256,price_calculator_version=selection.price_calculator_version)
            ev=dict(evidence or {});ev["market_data_at_utc"]=None if market_evidence is None else market_evidence.observed_at_utc;ev["market_evidence_id"]=None if market_evidence is None else str(market_evidence.evidence_id);ev["market_evidence_sha256"]=None if market_evidence is None else market_evidence.evidence_sha256;ev["market_evidence_price"]=prepared.market_evidence_price;ev["risk_reference_price"]=prepared.risk_reference_price;ev["worst_case_order_price"]=prepared.worst_case_order_price;ev["risk_notional"]=prepared.risk_notional;ev["reservation_notional"]=prepared.reservation_notional;ev["price_deviation_bps"]=prepared.price_deviation_bps;ev["price_source_sha256"]=prepared.price_source_sha256;ev["price_calculator_version"]=prepared.price_calculator_version;ev.setdefault("account_snapshot_at_utc",state["latest_account_snapshot_at_utc"]);ev.setdefault("reconciliation_at_utc",state["latest_reconciliation_at_utc"]);ev.setdefault("reconciliation_status",state["latest_reconciliation_status"]);ev.setdefault("clock_skew_seconds",state["venue_clock_skew_seconds"]);ev.setdefault("oldest_unknown_age_seconds",self._age(manifest.paper_run_id,("submission_unknown","pending_recovery","dispatch_claimed","cancel_unknown","cancel_pending"),now));ev.setdefault("oldest_unacknowledged_age_seconds",self._age(manifest.paper_run_id,("prepared","pending_ack","submitted","cancel_requested"),now))
            risk_blocked,checks,requirement=self._risk(configuration,approval,intent,prepared,state,positions,balances,reservations,ev,now);integrity=self._fetchone("SELECT EXISTS(SELECT 1 FROM execution.paper_order_projections WHERE paper_run_id=%s AND NOT fill_application_complete) incomplete,(SELECT count(*) FROM execution.paper_order_submissions WHERE paper_run_id=%s AND counted_open) counted",(manifest.paper_run_id,manifest.paper_run_id));integrity_reasons=tuple(x for x,failed in (("fill_application_incomplete",bool(integrity["incomplete"])),("order_budget_mismatch",int(integrity["counted"])!=int(state["open_order_count"]))) if failed);blocked=tuple(dict.fromkeys((*market_reasons,*risk_blocked,*integrity_reasons)));stored=replace(prepared,state=PaperOrderState.REJECTED if blocked else PaperOrderState.PREPARED);self._submission(stored,risk_decision.record_sha256)
            decision=deterministic_paper_uuid("runtime-risk",{"submission":prepared.submission_id,"checks":checks});digest=sha256_payload({"decision":decision,"blocked":blocked,"checks":checks,"evidence":ev})
            self._strict("execution.paper_runtime_risk_decisions","runtime_risk_decision_id",decision,("paper_run_id","submission_id","order_intent_id","accepted_pre_submit_risk_sha256","decision_status","reason_codes_jsonb","evaluated_limits_jsonb","persisted_state_jsonb","evidence_jsonb","decided_at_utc","market_evidence_id","market_evidence_sha256","market_evidence_price","risk_reference_price","worst_case_order_price","risk_notional","reservation_notional","price_deviation_bps","price_source_sha256","price_calculator_version"),(manifest.paper_run_id,prepared.submission_id,intent.order_intent_id,risk_decision.record_sha256,"blocked" if blocked else "accepted",_json_param(blocked),_json_param(checks),_json_param(state),_json_param({**ev,"series_identity":intent.series_identity.as_dict()}),now,None if market_evidence is None or source_reasons else market_evidence.evidence_id,None if market_evidence is None or source_reasons else market_evidence.evidence_sha256,prepared.market_evidence_price,prepared.risk_reference_price,prepared.worst_case_order_price,prepared.risk_notional,prepared.reservation_notional,prepared.price_deviation_bps,prepared.price_source_sha256,prepared.price_calculator_version),digest)
            if not blocked:self._reserve_and_enqueue(prepared,requirement,state,now)
        if blocked:raise RuntimeRiskBlocked(blocked)
        return prepared,False
    def _event(self,dispatch,s,event,at,token=None,worker=None,classification=None,evidence=None):
        eid=deterministic_paper_uuid("dispatch-event",{"dispatch":dispatch,"event":event,"token":token,"evidence":evidence});self._strict("execution.paper_dispatch_events","dispatch_event_id",eid,("dispatch_id","paper_run_id","submission_id","event_type","occurred_at_utc","claim_token","worker_id","transport_classification","evidence_sha256"),(dispatch,s.paper_run_id,s.submission_id,event,at,token,worker,classification,evidence),sha256_payload({"event":eid,"submission":s.submission_id}))
    def _transport(self,s,token,operation,at,result,evidence=None):
        request_id=token or deterministic_paper_uuid("transport-request",{"submission":s.submission_id,"operation":operation,"at":at});attempt=deterministic_paper_uuid("transport-attempt",{"request":request_id,"operation":operation});self._strict("execution.paper_transport_attempts","transport_attempt_id",attempt,("paper_run_id","submission_id","request_id","request_type","method","approved_origin","approved_path","idempotency_key","attempted_at_utc","result_type","status_code","response_sha256","retryable","retry_ordinal"),(s.paper_run_id,s.submission_id,request_id,operation,"BOUND","paper-venue://configured",operation,s.idempotency_key,at,result,None,evidence,result in ("unknown","timeout","rate_limited","malformed"),0),sha256_payload({"attempt":attempt,"operation":operation,"result":result,"evidence":evidence}))
    def _unknown_recovery(self,s,started,at,action,parent):
        row=PaperRecoveryRecord(s.paper_run_id,s.submission_id,started,at,RecoveryStatus.PAUSED,action,"venue outcome remains unknown; query original client order ID, recent orders, and fills",(parent,));self._strict("execution.paper_recovery_records","recovery_id",row.recovery_id,("paper_run_id","submission_id","started_at_utc","completed_at_utc","status","action","explanation","parent_ids"),(row.paper_run_id,row.submission_id,row.started_at_utc,row.completed_at_utc,row.status.value,row.action,row.explanation,list(row.parent_ids)),row.record_sha256)
    def _reservation_event(self,rid,s,event,amount,quantity,at,cause):
        eid=deterministic_paper_uuid("reservation-event",{"reservation":rid,"event":event,"cause":cause,"amount":amount,"quantity":quantity});self._strict("execution.paper_reservation_events","reservation_event_id",eid,("reservation_id","paper_run_id","submission_id","event_type","amount_delta","quantity_delta","occurred_at_utc","cause_id"),(rid,s.paper_run_id,s.submission_id,event,amount,quantity,at,cause),sha256_payload({"event":eid,"reservation":rid}))
    def _budget_event(self,s,event,at,cause,prior,next_value,worker=None):
        eid=deterministic_paper_uuid("order-budget-event",{"submission":s.submission_id,"event":event,"cause":cause})
        self._strict("execution.paper_order_budget_events","order_budget_event_id",eid,("paper_run_id","submission_id","event_type","occurred_at_utc","cause_id","prior_counted_open","next_counted_open","worker_id"),(s.paper_run_id,s.submission_id,event,at,cause,prior,next_value,worker),sha256_payload({"event":eid,"submission":s.submission_id,"prior":prior,"next":next_value}))
    def _close_open_once(self,s,at,cause,worker=None):
        cur=self.connection.cursor()
        try:
            cur.execute("UPDATE execution.paper_order_submissions SET counted_open=false,open_closed_at_utc=%s,open_close_cause_id=%s WHERE submission_id=%s AND counted_open=true RETURNING submission_id",(at,cause,s.submission_id));closed=cur.fetchone() is not None
        finally:cur.close()
        if not closed:return False
        self._budget_event(s,"order_budget_closed",at,cause,True,False,worker)
        self._execute("UPDATE execution.paper_run_risk_state SET open_order_count=GREATEST(open_order_count-1,0),version=version+1,updated_at_utc=%s,record_sha256=%s WHERE paper_run_id=%s",(at,sha256_payload({"run":s.paper_run_id,"closed":s.submission_id,"cause":cause}),s.paper_run_id))
        return True

    def _reserve_and_enqueue(self,s,requirement,state,now):
        rid=deterministic_paper_uuid("reservation",{"submission":s.submission_id});self._strict("execution.paper_reservations","reservation_id",rid,("paper_run_id","submission_id","client_order_id","currency","original_amount","remaining_amount","original_quantity","remaining_quantity","state","created_at_utc","updated_at_utc","version","economics_sha256","reserve_price","maximum_fee_bps","maximum_adverse_slippage_bps","calculator_version","spent_amount","risk_notional","reservation_notional","price_source_sha256","price_calculator_version"),(s.paper_run_id,s.submission_id,s.client_order_id,requirement.currency,requirement.amount,requirement.amount,s.quantity,s.quantity,"open",now,now,0,s.economics_sha256,requirement.reserve_price,requirement.maximum_fee_bps,requirement.maximum_adverse_slippage_bps,requirement.calculator_version,Decimal(0),s.risk_notional,s.reservation_notional,s.price_source_sha256,s.price_calculator_version),sha256_payload({"reservation":rid,"amount":requirement.amount,"quantity":s.quantity,"calculator":requirement.calculator_version}));self._reservation_event(rid,s,"reserved",requirement.amount,s.quantity,now,s.submission_id)
        did=deterministic_paper_uuid("dispatch",{"submission":s.submission_id});self._strict("execution.paper_dispatch_outbox","dispatch_id",did,("paper_run_id","submission_id","client_order_id","idempotency_key","economics_sha256","state","eligible_at_utc","updated_at_utc"),(s.paper_run_id,s.submission_id,s.client_order_id,s.idempotency_key,s.economics_sha256,"prepared",now,now),sha256_payload({"dispatch":did,"state":"prepared"}));self._event(did,s,"prepared",now)
        self._execute("UPDATE execution.paper_order_submissions SET counted_open=true,open_counted_at_utc=%s WHERE submission_id=%s AND counted_open=false",(now,s.submission_id));self._budget_event(s,"order_budget_opened",now,s.submission_id,False,True)
        self._execute("UPDATE execution.paper_run_risk_state SET version=version+1,trading_day=%s,daily_submitted_notional=%s,approval_submitted_notional=approval_submitted_notional+%s,open_order_count=open_order_count+1,orders_in_current_minute=%s,rate_window_started_at_utc=%s,updated_at_utc=%s,record_sha256=%s WHERE paper_run_id=%s",(now.date(),Decimal(str(state["daily_submitted_notional"]))+s.submitted_notional,s.submitted_notional,int(state["orders_in_current_minute"])+1,state["rate_window_started_at_utc"],now,sha256_payload({"run":s.paper_run_id,"submission":s.submission_id}),s.paper_run_id))
    def claim_dispatch(self,s,*,worker_id,at_utc,lease_seconds=30):
        with self.transaction():
            row=self._fetchone("SELECT * FROM execution.paper_dispatch_outbox WHERE submission_id=%s FOR UPDATE",(s.submission_id,))
            if not row:raise DispatchNotClaimable("no prepared outbox")
            if row["state"] in ("acknowledged","explicitly_rejected","recovered"):return None
            if row["state"]!="prepared":raise DispatchNotClaimable("dispatch requires recovery")
            ordinal=int(row["attempt_count"])+1;token=deterministic_paper_uuid("dispatch-claim",{"dispatch":row["dispatch_id"],"ordinal":ordinal,"worker":worker_id});lease=at_utc+timedelta(seconds=lease_seconds);digest=sha256_payload({"dispatch":row["dispatch_id"],"claim":token,"lease":lease})
            self._execute("UPDATE execution.paper_dispatch_outbox SET state='dispatch_claimed',claimed_at_utc=%s,claim_token=%s,claimed_by=%s,claim_lease_expires_at_utc=%s,attempt_count=%s,updated_at_utc=%s,record_sha256=%s WHERE dispatch_id=%s",(at_utc,token,worker_id,lease,ordinal,at_utc,digest,row["dispatch_id"]));self._execute("UPDATE execution.paper_order_submissions SET state='dispatch_claimed',record_sha256=%s WHERE submission_id=%s",(sha256_payload({"submission":s.submission_id,"state":"dispatch_claimed"}),s.submission_id));self._event(row["dispatch_id"],s,"claimed",at_utc,token,worker_id);return token
    def claim_dispatch_recovery(self,s,*,worker_id,at_utc,lease_seconds=30):
        with self.transaction():
            row=self._fetchone("SELECT * FROM execution.paper_dispatch_outbox WHERE submission_id=%s FOR UPDATE",(s.submission_id,))
            if not row or row["state"] not in ("dispatch_claimed","unknown"):raise DispatchNotClaimable("dispatch is not recoverable")
            if row["state"]=="dispatch_claimed" and row.get("claim_lease_expires_at_utc") and row["claim_lease_expires_at_utc"]>at_utc:raise DispatchNotClaimable("original dispatch claim lease is still active")
            if row.get("recovery_lease_expires_at_utc") and row["recovery_lease_expires_at_utc"]>at_utc:raise DispatchNotClaimable("dispatch recovery already belongs to another worker")
            generation=int(row.get("recovery_generation") or 0)+1;token=deterministic_paper_uuid("dispatch-recovery-claim",{"dispatch":row["dispatch_id"],"generation":generation,"worker":worker_id});lease=at_utc+timedelta(seconds=lease_seconds)
            self._execute("UPDATE execution.paper_dispatch_outbox SET recovery_claim_token=%s,recovery_claimed_by=%s,recovery_claimed_at_utc=%s,recovery_lease_expires_at_utc=%s,recovery_generation=%s,updated_at_utc=%s,record_sha256=%s WHERE dispatch_id=%s",(token,worker_id,at_utc,lease,generation,at_utc,sha256_payload({"dispatch":row["dispatch_id"],"recovery_claim":token,"generation":generation}),row["dispatch_id"]))
            recovery=PaperRecoveryRecord(s.paper_run_id,s.submission_id,at_utc,None,RecoveryStatus.STARTED,"recover_dispatch_by_original_client_order_id","exclusive PostgreSQL recovery claim acquired",(row["dispatch_id"],));self.record_recovery(recovery);return token
    def complete_dispatch_recovery(self,s,*,recovery_claim_token,at_utc,order=None,evidence_sha256=None,classification="venue_query_evidence"):
        with self.transaction():
            row=self._fetchone("SELECT * FROM execution.paper_dispatch_outbox WHERE submission_id=%s FOR UPDATE",(s.submission_id,))
            if not row or recovery_claim_token is None or row.get("recovery_claim_token") is None or row["recovery_claim_token"]!=recovery_claim_token:raise DispatchNotClaimable("dispatch recovery claim mismatch")
            if row.get("recovery_lease_expires_at_utc") and at_utc>=row["recovery_lease_expires_at_utc"]:raise DispatchNotClaimable("dispatch recovery claim lease expired")
            prior_recovery=self._fetchone("SELECT status FROM execution.paper_recovery_records WHERE paper_run_id=%s AND submission_id=%s AND action='recover_dispatch_by_original_client_order_id' ORDER BY started_at_utc DESC LIMIT 1",(s.paper_run_id,s.submission_id))
            if prior_recovery and prior_recovery["status"]=="recovered":return True
            if prior_recovery and prior_recovery["status"]=="paused" and order is None:return False
            if order is None:
                self._execute("UPDATE execution.paper_dispatch_outbox SET state='unknown',updated_at_utc=%s,record_sha256=%s WHERE dispatch_id=%s",(at_utc,sha256_payload({"dispatch":row["dispatch_id"],"recovery":"unknown","evidence":evidence_sha256}),row["dispatch_id"]));self._execute("UPDATE execution.paper_order_submissions SET state='submission_unknown',record_sha256=%s WHERE submission_id=%s",(sha256_payload({"submission":s.submission_id,"state":"submission_unknown"}),s.submission_id));self._event(row["dispatch_id"],s,"unknown",at_utc,recovery_claim_token,row["recovery_claimed_by"],classification,evidence_sha256);self._transport(s,recovery_claim_token,"query_order",at_utc,"unknown",evidence_sha256);self.record_recovery(PaperRecoveryRecord(s.paper_run_id,s.submission_id,row["recovery_claimed_at_utc"],at_utc,RecoveryStatus.PAUSED,"recover_dispatch_by_original_client_order_id","venue query remains inconclusive; no economic resubmission occurred",(row["dispatch_id"],)));return False
            mapping={VenueOrderState.PENDING_ACK:"pending_ack",VenueOrderState.ACKNOWLEDGED:"acknowledged",VenueOrderState.PARTIALLY_FILLED:"partially_filled",VenueOrderState.FILLED:"filled",VenueOrderState.REJECTED:"rejected",VenueOrderState.CANCELLED:"cancelled",VenueOrderState.EXPIRED:"expired",VenueOrderState.CANCEL_PENDING:"cancel_pending",VenueOrderState.UNKNOWN_PENDING_RECOVERY:"pending_recovery"};state=mapping[order.state];fill_complete=self._fill_application_complete(s.submission_id,order.cumulative_filled_quantity);state="pending_recovery" if not fill_complete else state
            self._execute("UPDATE execution.paper_dispatch_outbox SET state='recovered',last_outcome_at_utc=%s,venue_order_id=%s,updated_at_utc=%s,record_sha256=%s WHERE dispatch_id=%s",(at_utc,order.venue_order_id,at_utc,sha256_payload({"dispatch":row["dispatch_id"],"recovered_state":state,"evidence":evidence_sha256}),row["dispatch_id"]));self._execute("UPDATE execution.paper_order_submissions SET state=%s,record_sha256=%s WHERE submission_id=%s",(state,sha256_payload({"submission":s.submission_id,"state":state}),s.submission_id));self.record_order(order);self._event(row["dispatch_id"],s,"recovered",at_utc,recovery_claim_token,row["recovery_claimed_by"],classification,evidence_sha256);self._transport(s,recovery_claim_token,"query_order",at_utc,"succeeded",evidence_sha256)
            terminal=order.state in (VenueOrderState.REJECTED,VenueOrderState.CANCELLED,VenueOrderState.EXPIRED,VenueOrderState.FILLED)
            if terminal and fill_complete:
                if order.state in (VenueOrderState.REJECTED,VenueOrderState.CANCELLED,VenueOrderState.EXPIRED):self._release(s,at_utc,row["dispatch_id"])
                elif order.state is VenueOrderState.FILLED:self._release(s,at_utc,row["dispatch_id"],event="consumed")
                self._close_open_once(s,at_utc,row["dispatch_id"],row["recovery_claimed_by"])
            recovery_status=RecoveryStatus.RECOVERED if fill_complete else RecoveryStatus.PAUSED;recovery=PaperRecoveryRecord(s.paper_run_id,s.submission_id,row["recovery_claimed_at_utc"],at_utc,recovery_status,"recover_dispatch_by_original_client_order_id","venue state recovered without economic resubmission" if fill_complete else "terminal venue state requires complete fill accounting",(row["dispatch_id"],));self.record_recovery(recovery);return fill_complete
    def _fill_application_complete(self,submission_id,cumulative_quantity):
        row=self._fetchone("SELECT COALESCE(sum(quantity),0) quantity,bool_and(accounting_applied) complete FROM execution.paper_fills WHERE submission_id=%s",(submission_id,));return Decimal(str(row["quantity"] or 0))==Decimal(cumulative_quantity) and bool(row["complete"] if row["complete"] is not None else Decimal(cumulative_quantity)==0)
    def _release(self,s,at,cause,event="released"):
        row=self._fetchone("SELECT * FROM execution.paper_reservations WHERE submission_id=%s FOR UPDATE",(s.submission_id,))
        if not row or row["state"]!="open":return False
        self._execute("UPDATE execution.paper_reservations SET remaining_amount=0,remaining_quantity=0,state=%s,updated_at_utc=%s,version=version+1,record_sha256=%s WHERE reservation_id=%s",("consumed" if event=="consumed" else "released",at,sha256_payload({"reservation":row["reservation_id"],"event":event,"cause":cause}),row["reservation_id"]));self._reservation_event(row["reservation_id"],s,event,-Decimal(str(row["remaining_amount"])),-Decimal(str(row["remaining_quantity"])),at,cause);return True
    def complete_dispatch(self,s,*,claim_token,outcome,at_utc,order=None,classification=None,evidence_sha256=None,worker_id=None,fail_at=None):
        if outcome not in {"acknowledged","explicitly_rejected","unknown","recovered"}:raise ValueError("invalid dispatch outcome")
        with self.transaction():
            row=self._fetchone("SELECT * FROM execution.paper_dispatch_outbox WHERE submission_id=%s FOR UPDATE",(s.submission_id,))
            if not row:raise DispatchNotClaimable("outcome has no claim")
            if row.get("claim_token") is None or claim_token is None or row["claim_token"]!=claim_token:raise DispatchNotClaimable("claim mismatch")
            if row["state"] in {"acknowledged","explicitly_rejected","recovered"}:
                if row["state"]==outcome:return False
                raise DispatchNotClaimable("terminal dispatch outcome conflict")
            if row["state"] not in {"dispatch_claimed","unknown"}:raise DispatchNotClaimable("outcome has no active claim")
            if worker_id is not None and row.get("claimed_by")!=worker_id:raise DispatchNotClaimable("dispatch belongs to another worker")
            if row.get("claim_lease_expires_at_utc") and at_utc>=row["claim_lease_expires_at_utc"]:raise DispatchNotClaimable("dispatch claim lease expired")
            if outcome=="recovered":
                if order is None:raise ValueError("recovered dispatch requires venue order evidence")
                state={VenueOrderState.PENDING_ACK:"pending_ack",VenueOrderState.ACKNOWLEDGED:"acknowledged",VenueOrderState.PARTIALLY_FILLED:"partially_filled",VenueOrderState.FILLED:"filled",VenueOrderState.REJECTED:"rejected",VenueOrderState.CANCELLED:"cancelled",VenueOrderState.EXPIRED:"expired",VenueOrderState.CANCEL_PENDING:"cancel_pending",VenueOrderState.UNKNOWN_PENDING_RECOVERY:"pending_recovery"}[order.state]
            else:state={"acknowledged":"pending_ack","explicitly_rejected":"rejected","unknown":"submission_unknown"}[outcome]
            self._execute("UPDATE execution.paper_dispatch_outbox SET state=%s,last_outcome_at_utc=%s,venue_order_id=%s,updated_at_utc=%s,record_sha256=%s WHERE dispatch_id=%s",(outcome,at_utc,None if order is None else order.venue_order_id,at_utc,sha256_payload({"dispatch":row["dispatch_id"],"outcome":outcome,"evidence":evidence_sha256}),row["dispatch_id"]));self._execute("UPDATE execution.paper_order_submissions SET state=%s,record_sha256=%s WHERE submission_id=%s",(state,sha256_payload({"submission":s.submission_id,"state":state}),s.submission_id))
            if fail_at=="after_outcome_before_order":raise RuntimeError("injected outcome/order-event crash")
            if order is not None:self.record_order(order)
            self._event(row["dispatch_id"],s,outcome,at_utc,claim_token,row.get("claimed_by"),classification,evidence_sha256)
            transport_result={"acknowledged":"succeeded","recovered":"succeeded","explicitly_rejected":"rejected","unknown":"unknown"}[outcome];transport_token=deterministic_paper_uuid("recovery-query",{"claim":claim_token,"at":at_utc}) if outcome=="recovered" else claim_token;self._transport(s,transport_token,"query_order" if outcome=="recovered" else "submit",at_utc,transport_result,evidence_sha256)
            if outcome=="unknown":self._unknown_recovery(s,row["claimed_at_utc"],at_utc,"query_original_client_order_id",row["dispatch_id"])
            delta="consecutive_transport_failures+1" if outcome=="unknown" else "0"
            fill_complete=order is None or self._fill_application_complete(s.submission_id,order.cumulative_filled_quantity)
            if order is not None and not fill_complete:state="pending_recovery";self._execute("UPDATE execution.paper_order_submissions SET state=%s,record_sha256=%s WHERE submission_id=%s",(state,sha256_payload({"submission":s.submission_id,"state":state,"missing_fill_evidence":True}),s.submission_id))
            terminal_state=state in ("filled","rejected","cancelled","expired") and fill_complete
            if fill_complete and state in ("rejected","cancelled","expired"):self._release(s,at_utc,s.submission_id)
            if state=="filled" and fill_complete:self._release(s,at_utc,s.submission_id,event="consumed")
            if terminal_state:self._close_open_once(s,at_utc,s.submission_id,row.get("claimed_by"))
            self._execute(f"UPDATE execution.paper_run_risk_state SET version=version+1,consecutive_transport_failures={delta},updated_at_utc=%s,record_sha256=%s WHERE paper_run_id=%s",(at_utc,sha256_payload({"run":s.paper_run_id,"outcome":outcome,"at":at_utc}),s.paper_run_id));return True
    def prepare_cancel(self,s,*,at_utc,maximum_cancellations_per_minute):
        with self.transaction():
            self._lock(s.paper_run_id);risk=self._fetchone("SELECT * FROM execution.paper_run_risk_state WHERE paper_run_id=%s FOR UPDATE",(s.paper_run_id,));count=0 if (at_utc-risk["rate_window_started_at_utc"]).total_seconds()>=60 else int(risk["cancellations_in_current_minute"]);window=at_utc if count==0 else risk["rate_window_started_at_utc"]
            if count+1>maximum_cancellations_per_minute:raise RuntimeRiskBlocked(("maximum_cancellations_per_minute",))
            dispatch=self._fetchone("SELECT * FROM execution.paper_dispatch_outbox WHERE submission_id=%s FOR UPDATE",(s.submission_id,));existing_cancel=self._fetchone("SELECT * FROM execution.paper_cancel_outbox WHERE submission_id=%s FOR UPDATE",(s.submission_id,))
            if existing_cancel is not None:return existing_cancel["cancel_id"]
            cid=deterministic_paper_uuid("cancel",{"dispatch":dispatch["dispatch_id"]})
            self._strict("execution.paper_cancel_outbox","cancel_id",cid,("dispatch_id","paper_run_id","submission_id","client_order_id","state","requested_at_utc","updated_at_utc"),(dispatch["dispatch_id"],s.paper_run_id,s.submission_id,s.client_order_id,"cancel_requested",at_utc,at_utc),sha256_payload({"cancel":cid,"state":"cancel_requested"}));self._execute("UPDATE execution.paper_order_submissions SET state='cancel_requested',record_sha256=%s WHERE submission_id=%s",(sha256_payload({"submission":s.submission_id,"state":"cancel_requested"}),s.submission_id));self._event(dispatch["dispatch_id"],s,"cancel_requested",at_utc);self._execute("UPDATE execution.paper_run_risk_state SET version=version+1,cancellations_in_current_minute=%s,rate_window_started_at_utc=%s,updated_at_utc=%s,record_sha256=%s WHERE paper_run_id=%s",(count+1,window,at_utc,sha256_payload({"run":s.paper_run_id,"cancel":cid}),s.paper_run_id));return cid
    def claim_cancel(self,s,*,worker_id,at_utc,lease_seconds=30):
        with self.transaction():
            row=self._fetchone("SELECT * FROM execution.paper_cancel_outbox WHERE submission_id=%s FOR UPDATE",(s.submission_id,))
            if not row or row["state"]!="cancel_requested":raise DispatchNotClaimable("cancel requires recovery")
            n=int(row["attempt_count"])+1;token=deterministic_paper_uuid("cancel-claim",{"cancel":row["cancel_id"],"ordinal":n,"worker":worker_id});lease=at_utc+timedelta(seconds=lease_seconds);self._execute("UPDATE execution.paper_cancel_outbox SET state='cancel_claimed',claimed_at_utc=%s,claim_token=%s,claimed_by=%s,claim_lease_expires_at_utc=%s,attempt_count=%s,updated_at_utc=%s,record_sha256=%s WHERE cancel_id=%s",(at_utc,token,worker_id,lease,n,at_utc,sha256_payload({"cancel":row["cancel_id"],"claim":token,"lease":lease}),row["cancel_id"]));dispatch=self._fetchone("SELECT dispatch_id FROM execution.paper_dispatch_outbox WHERE submission_id=%s",(s.submission_id,));self._event(dispatch["dispatch_id"],s,"cancel_claimed",at_utc,token,worker_id);return token
    def claim_cancel_recovery(self,s,*,worker_id,at_utc,lease_seconds=30):
        with self.transaction():
            row=self._fetchone("SELECT * FROM execution.paper_cancel_outbox WHERE submission_id=%s FOR UPDATE",(s.submission_id,))
            if not row or row["state"] not in ("cancel_claimed","cancel_unknown"):raise DispatchNotClaimable("cancel is not recoverable")
            if row["state"]=="cancel_claimed" and row.get("claim_lease_expires_at_utc") and row["claim_lease_expires_at_utc"]>at_utc:raise DispatchNotClaimable("original cancel claim lease is still active")
            if row.get("recovery_lease_expires_at_utc") and row["recovery_lease_expires_at_utc"]>at_utc:raise DispatchNotClaimable("cancel recovery already belongs to another worker")
            generation=int(row.get("recovery_generation") or 0)+1;token=deterministic_paper_uuid("cancel-recovery-claim",{"cancel":row["cancel_id"],"generation":generation,"worker":worker_id});lease=at_utc+timedelta(seconds=lease_seconds);self._execute("UPDATE execution.paper_cancel_outbox SET recovery_claim_token=%s,recovery_claimed_by=%s,recovery_claimed_at_utc=%s,recovery_lease_expires_at_utc=%s,recovery_generation=%s,updated_at_utc=%s,record_sha256=%s WHERE cancel_id=%s",(token,worker_id,at_utc,lease,generation,at_utc,sha256_payload({"cancel":row["cancel_id"],"recovery_claim":token}),row["cancel_id"]));recovery=PaperRecoveryRecord(s.paper_run_id,s.submission_id,at_utc,None,RecoveryStatus.STARTED,"recover_cancel_by_original_client_order_id","exclusive PostgreSQL cancel recovery claim acquired",(row["cancel_id"],));self.record_recovery(recovery);return token
    def complete_cancel(self,s,*,claim_token,confirmed,at_utc,evidence_sha256=None,worker_id=None):
        outcome="cancel_confirmed" if confirmed else "cancel_unknown"
        with self.transaction():
            row=self._fetchone("SELECT * FROM execution.paper_cancel_outbox WHERE submission_id=%s FOR UPDATE",(s.submission_id,))
            if not row:raise DispatchNotClaimable("cancel outcome has no claim")
            if row.get("claim_token") is None or claim_token is None or row["claim_token"]!=claim_token:raise DispatchNotClaimable("cancel claim mismatch")
            if row["state"]=="cancel_confirmed" and confirmed:return False
            if row["state"] not in {"cancel_claimed","cancel_unknown"}:raise DispatchNotClaimable("cancel outcome has no active claim")
            if worker_id is not None and row.get("claimed_by")!=worker_id:raise DispatchNotClaimable("cancel belongs to another worker")
            if row.get("claim_lease_expires_at_utc") and at_utc>=row["claim_lease_expires_at_utc"]:raise DispatchNotClaimable("cancel claim lease expired")
            self._execute("UPDATE execution.paper_cancel_outbox SET state=%s,updated_at_utc=%s,record_sha256=%s WHERE cancel_id=%s",(outcome,at_utc,sha256_payload({"cancel":row["cancel_id"],"outcome":outcome}),row["cancel_id"]));self._execute("UPDATE execution.paper_order_submissions SET state=%s,record_sha256=%s WHERE submission_id=%s",("cancelled" if confirmed else "cancel_unknown",sha256_payload({"submission":s.submission_id,"state":outcome}),s.submission_id));dispatch=self._fetchone("SELECT dispatch_id FROM execution.paper_dispatch_outbox WHERE submission_id=%s",(s.submission_id,));self._event(dispatch["dispatch_id"],s,outcome,at_utc,claim_token,row.get("claimed_by"),outcome,evidence_sha256)
            transport_token=deterministic_paper_uuid("cancel-recovery-query",{"claim":claim_token,"at":at_utc}) if confirmed and row["state"]=="cancel_unknown" else claim_token;self._transport(s,transport_token,"query_order" if confirmed and row["state"]=="cancel_unknown" else "cancel",at_utc,"succeeded" if confirmed else "unknown",evidence_sha256)
            if not confirmed:self._unknown_recovery(s,row["claimed_at_utc"],at_utc,"query_cancel_by_original_client_order_id",row["cancel_id"])
            if confirmed:self._release(s,at_utc,row["cancel_id"]);self._close_open_once(s,at_utc,row["cancel_id"],row.get("claimed_by"));failures="0"
            else:failures="consecutive_transport_failures+1"
            self._execute(f"UPDATE execution.paper_run_risk_state SET version=version+1,consecutive_transport_failures={failures},updated_at_utc=%s,record_sha256=%s WHERE paper_run_id=%s",(at_utc,sha256_payload({"run":s.paper_run_id,"cancel":outcome}),s.paper_run_id));return True
    def complete_cancel_superseded(self,s,*,claim_token,order,at_utc,evidence_sha256=None,worker_id=None):
        outcomes={VenueOrderState.FILLED:"cancel_superseded_by_fill",VenueOrderState.EXPIRED:"cancel_superseded_by_expiry",VenueOrderState.REJECTED:"cancel_superseded_by_rejection"}
        outcome=outcomes.get(order.state)
        if outcome is None:raise ValueError("cancel supersession requires FILLED, EXPIRED, or REJECTED evidence")
        with self.transaction():
            row=self._fetchone("SELECT * FROM execution.paper_cancel_outbox WHERE submission_id=%s FOR UPDATE",(s.submission_id,))
            if not row or row.get("claim_token")!=claim_token:raise DispatchNotClaimable("cancel claim mismatch")
            if worker_id is not None and row.get("claimed_by")!=worker_id:raise DispatchNotClaimable("cancel belongs to another worker")
            if row.get("claim_lease_expires_at_utc") and at_utc>=row["claim_lease_expires_at_utc"]:raise DispatchNotClaimable("cancel claim lease expired")
            self._execute("UPDATE execution.paper_cancel_outbox SET state=%s,updated_at_utc=%s,record_sha256=%s WHERE cancel_id=%s",(outcome,at_utc,sha256_payload({"cancel":row["cancel_id"],"outcome":outcome,"evidence":evidence_sha256}),row["cancel_id"]))
            self.persist_order_observation(s,order,observed_at_utc=at_utc,source="cancel_superseded",evidence_sha256=evidence_sha256)
            dispatch=self._fetchone("SELECT dispatch_id FROM execution.paper_dispatch_outbox WHERE submission_id=%s",(s.submission_id,));self._event(dispatch["dispatch_id"],s,outcome,at_utc,claim_token,row.get("claimed_by"),outcome,evidence_sha256);self._transport(s,claim_token,"query_order",at_utc,"succeeded",evidence_sha256)
            self._execute("UPDATE execution.paper_run_risk_state SET version=version+1,consecutive_transport_failures=0,updated_at_utc=%s,record_sha256=%s WHERE paper_run_id=%s",(at_utc,sha256_payload({"run":s.paper_run_id,"cancel":outcome}),s.paper_run_id));return outcome

    def complete_cancel_recovery(self,s,*,recovery_claim_token,at_utc,order=None,evidence_sha256=None):
        with self.transaction():
            row=self._fetchone("SELECT * FROM execution.paper_cancel_outbox WHERE submission_id=%s FOR UPDATE",(s.submission_id,))
            if not row or recovery_claim_token is None or row.get("recovery_claim_token") is None or row["recovery_claim_token"]!=recovery_claim_token:raise DispatchNotClaimable("cancel recovery claim mismatch")
            if row.get("recovery_lease_expires_at_utc") and at_utc>=row["recovery_lease_expires_at_utc"]:raise DispatchNotClaimable("cancel recovery claim lease expired")
            terminal=None if order is None else {VenueOrderState.CANCELLED:"cancelled",VenueOrderState.FILLED:"filled",VenueOrderState.EXPIRED:"expired",VenueOrderState.REJECTED:"rejected"}.get(order.state)
            dispatch=self._fetchone("SELECT dispatch_id FROM execution.paper_dispatch_outbox WHERE submission_id=%s",(s.submission_id,))
            if terminal is None:
                self._execute("UPDATE execution.paper_cancel_outbox SET state='cancel_unknown',updated_at_utc=%s,record_sha256=%s WHERE cancel_id=%s",(at_utc,sha256_payload({"cancel":row["cancel_id"],"recovery":"unknown","evidence":evidence_sha256}),row["cancel_id"]));self._execute("UPDATE execution.paper_order_submissions SET state='cancel_unknown',record_sha256=%s WHERE submission_id=%s",(sha256_payload({"submission":s.submission_id,"state":"cancel_unknown"}),s.submission_id));self._event(dispatch["dispatch_id"],s,"cancel_unknown",at_utc,recovery_claim_token,row["recovery_claimed_by"],"recovery_query",evidence_sha256);self._transport(s,recovery_claim_token,"query_order",at_utc,"unknown",evidence_sha256);self.record_recovery(PaperRecoveryRecord(s.paper_run_id,s.submission_id,row["recovery_claimed_at_utc"],at_utc,RecoveryStatus.PAUSED,"recover_cancel_by_original_client_order_id","venue query remains inconclusive; cancellation was not repeated",(row["cancel_id"],)));return False
            outcome={"cancelled":"cancel_confirmed","filled":"cancel_superseded_by_fill","expired":"cancel_superseded_by_expiry","rejected":"cancel_superseded_by_rejection"}[terminal]
            self._execute("UPDATE execution.paper_cancel_outbox SET state=%s,updated_at_utc=%s,record_sha256=%s WHERE cancel_id=%s",(outcome,at_utc,sha256_payload({"cancel":row["cancel_id"],"outcome":outcome,"evidence":evidence_sha256}),row["cancel_id"]))
            self.persist_order_observation(s,order,observed_at_utc=at_utc,source="cancel_recovery",evidence_sha256=evidence_sha256)
            self._event(dispatch["dispatch_id"],s,outcome,at_utc,recovery_claim_token,row["recovery_claimed_by"],"recovery_query",evidence_sha256);self._transport(s,recovery_claim_token,"query_order",at_utc,"succeeded",evidence_sha256)
            complete=self._fill_application_complete(s.submission_id,order.cumulative_filled_quantity)
            status=RecoveryStatus.RECOVERED if complete else RecoveryStatus.PAUSED;explanation="terminal venue state recovered without repeating cancellation" if complete else "terminal disposition recovered; complete fill and fee accounting remains pending"
            self.record_recovery(PaperRecoveryRecord(s.paper_run_id,s.submission_id,row["recovery_claimed_at_utc"],at_utc,status,"recover_cancel_by_original_client_order_id",explanation,(row["cancel_id"],)));return complete
    def persist_fill_bundle(self,*,fill,order,local_snapshot,venue_snapshot,reconciliation,differences,lifecycle_event,recovery_observation_bundle_id=None,fail_at=None):
        with self.transaction():
            self._lock(fill.paper_run_id);old=self._fetchone("SELECT record_sha256 FROM execution.paper_fills WHERE fill_id=%s",(fill.fill_id,))
            if old:
                if str(old["record_sha256"])!=fill.record_sha256:raise Phase7ConflictError("fill identity changed economics")
                return False
            self.record_fill(fill)
            if fail_at=="fill":raise RuntimeError("injected fill failure")
            fee=deterministic_paper_uuid("paper-fee",{"fill":fill.fill_id});self._strict("execution.paper_fee_entries","fee_entry_id",fee,("fill_id","paper_run_id","amount","currency","occurred_at_utc"),(fill.fill_id,fill.paper_run_id,fill.fee_amount,fill.fee_currency,fill.filled_at_utc),sha256_payload({"fill":fill.fill_id,"amount":fill.fee_amount,"currency":fill.fee_currency}))
            if fail_at=="fee":raise RuntimeError("injected fee failure")

            for b in local_snapshot.balances:self._execute("INSERT INTO execution.paper_account_balance_projection (paper_run_id,currency,total,version,updated_at_utc,source_fill_id,record_sha256) VALUES (%s,%s,%s,1,%s,%s,%s) ON CONFLICT (paper_run_id,currency) DO UPDATE SET total=EXCLUDED.total,version=execution.paper_account_balance_projection.version+1,updated_at_utc=EXCLUDED.updated_at_utc,source_fill_id=EXCLUDED.source_fill_id,record_sha256=EXCLUDED.record_sha256",(fill.paper_run_id,b.currency,b.total,fill.filled_at_utc,fill.fill_id,sha256_payload({"run":fill.paper_run_id,"balance":asdict(b),"fill":fill.fill_id})))
            for p in local_snapshot.positions:self._execute("INSERT INTO execution.paper_account_position_projection (paper_run_id,series_identity_sha256,instrument_id,series_identity_jsonb,accounting_mode,quantity,average_entry_price,realized_pnl,funding,version,updated_at_utc,source_fill_id,record_sha256) VALUES (%s,%s,%s,%s::jsonb,%s,%s,%s,%s,%s,1,%s,%s,%s) ON CONFLICT (paper_run_id,series_identity_sha256) DO UPDATE SET quantity=EXCLUDED.quantity,average_entry_price=EXCLUDED.average_entry_price,realized_pnl=EXCLUDED.realized_pnl,funding=EXCLUDED.funding,version=execution.paper_account_position_projection.version+1,updated_at_utc=EXCLUDED.updated_at_utc,source_fill_id=EXCLUDED.source_fill_id,record_sha256=EXCLUDED.record_sha256",(fill.paper_run_id,p.series_identity.series_identity_sha256,p.series_identity.provider_instrument_id,_json_param(p.series_identity.as_dict()),p.accounting_mode.value,p.quantity,p.average_entry_price,p.realized_pnl,p.funding,fill.filled_at_utc,fill.fill_id,sha256_payload({"run":fill.paper_run_id,"position":asdict(p),"fill":fill.fill_id})))
            if fail_at in {"balance","position","account_projection"}:raise RuntimeError("injected account projection failure")
            r=self._fetchone("SELECT * FROM execution.paper_reservations WHERE submission_id=%s FOR UPDATE",(fill.submission_id,));srow=self._fetchone("SELECT * FROM execution.paper_order_submissions WHERE submission_id=%s",(fill.submission_id,))
            if r and r["state"]=="open":
                reduced=reduce_reservation(current_amount=Decimal(str(r["remaining_amount"])),current_quantity=Decimal(str(r["remaining_quantity"])),fill_quantity=fill.quantity,fill_price=fill.price,fill_fee=fill.fee_amount,fee_currency=fill.fee_currency,reservation_currency=str(r["currency"]),side=fill.side,accounting_mode=fill.accounting_mode);state="consumed" if reduced.quantity==0 else "open";spent=Decimal(str(r.get("spent_amount") or 0))+reduced.amount_consumed;self._execute("UPDATE execution.paper_reservations SET remaining_amount=%s,remaining_quantity=%s,spent_amount=%s,state=%s,updated_at_utc=%s,version=version+1,record_sha256=%s WHERE reservation_id=%s",(reduced.amount,reduced.quantity,spent,state,fill.filled_at_utc,sha256_payload({"reservation":r["reservation_id"],"fill":fill.fill_id,"remaining_amount":reduced.amount,"remaining_quantity":reduced.quantity,"spent":spent}),r["reservation_id"]));typed=self._submission_from_row(srow);self._reservation_event(r["reservation_id"],typed,"consumed" if reduced.quantity==0 else "reduced",-reduced.amount_consumed,-reduced.quantity_consumed,fill.filled_at_utc,fill.fill_id)
            after=self._fetchone("SELECT remaining_amount,remaining_quantity FROM execution.paper_reservations WHERE submission_id=%s",(fill.submission_id,));before_amount=Decimal(0) if r is None else Decimal(str(r["remaining_amount"]));before_quantity=Decimal(0) if r is None else Decimal(str(r["remaining_quantity"]));after_amount=Decimal(0) if after is None else Decimal(str(after["remaining_amount"]));after_quantity=Decimal(0) if after is None else Decimal(str(after["remaining_quantity"]));lineage_id=deterministic_paper_uuid("fill-recovery-lineage",{"fill":fill.fill_id});self._strict("execution.paper_fill_recovery_lineage","fill_recovery_lineage_id",lineage_id,("paper_run_id","submission_id","fill_id","recovery_observation_bundle_id","venue_order_observation_id","reservation_amount_before","reservation_amount_after","reservation_quantity_before","reservation_quantity_after","applied_at_utc"),(fill.paper_run_id,fill.submission_id,fill.fill_id,recovery_observation_bundle_id,None,before_amount,after_amount,before_quantity,after_quantity,fill.filled_at_utc),sha256_payload({"lineage":lineage_id,"fill":fill.fill_id,"recovery_bundle":recovery_observation_bundle_id,"before_amount":before_amount,"after_amount":after_amount,"before_quantity":before_quantity,"after_quantity":after_quantity}))
            if fail_at=="reservation":raise RuntimeError("injected reservation failure")
            self.persist_order_observation(self._submission_from_row(srow),order,observed_at_utc=fill.filled_at_utc,source="fill_bundle",evidence_sha256=fill.record_sha256)
            if fail_at=="order":raise RuntimeError("injected order failure")
            self.record_snapshot(local_snapshot);self.record_snapshot(venue_snapshot)
            if fail_at=="snapshot":raise RuntimeError("injected snapshot failure")
            self.record_reconciliation(reconciliation,differences)
            if fail_at in {"reconciliation","difference"}:raise RuntimeError("injected reconciliation failure")
            self.record_lifecycle(lifecycle_event)
            if fail_at=="lifecycle":raise RuntimeError("injected lifecycle failure")
            equity=next((b.total for b in local_snapshot.balances if b.currency==fill.fee_currency),Decimal(0));realized=sum((p.realized_pnl for p in local_snapshot.positions),Decimal(0))
            self._execute("UPDATE execution.paper_run_risk_state SET version=version+1,current_equity=%s,high_watermark_equity=GREATEST(high_watermark_equity,%s),daily_realized_pnl=%s,latest_account_snapshot_at_utc=%s,latest_reconciliation_at_utc=%s,latest_reconciliation_status=%s,lifecycle_sequence=GREATEST(lifecycle_sequence,%s),updated_at_utc=%s,record_sha256=%s WHERE paper_run_id=%s",(equity,equity,realized,local_snapshot.venue_as_of_utc,reconciliation.reconciled_at_utc,reconciliation.status.value,lifecycle_event.sequence,fill.filled_at_utc,sha256_payload({"run":fill.paper_run_id,"fill":fill.fill_id,"reconciliation":reconciliation.reconciliation_id}),fill.paper_run_id));return True
    def persist_reconciliation_bundle(self,*,bundle:PaperReconciliationBundle|None=None,local_snapshot=None,venue_snapshot=None,reconciliation=None,differences=(),kill_switch=None,kill_event=None,recovery=None,lifecycle_event=None,fail_at=None):
        if bundle is None:
            bundle=PaperReconciliationBundle(local_snapshot,venue_snapshot,(),(),(),(),reconciliation,tuple(differences),reconciliation.reconciled_at_utc)
        with self.transaction():
            self._lock(bundle.reconciliation.paper_run_id);self.record_snapshot(bundle.local_snapshot);self.record_snapshot(bundle.venue_snapshot)
            if fail_at=="snapshot":raise RuntimeError("injected snapshot failure")
            self.record_reconciliation(bundle.reconciliation,bundle.differences)
            if fail_at in {"reconciliation","difference"}:raise RuntimeError("injected reconciliation failure")
            self._strict("execution.paper_reconciliation_bundles","reconciliation_bundle_id",bundle.bundle_id,("paper_run_id","reconciliation_id","local_snapshot_id","venue_snapshot_id","evaluated_at_utc","local_order_hashes_jsonb","venue_order_hashes_jsonb","local_fill_hashes_jsonb","venue_fill_hashes_jsonb","monitoring_evidence_jsonb","kill_evidence_jsonb"),(bundle.reconciliation.paper_run_id,bundle.reconciliation.reconciliation_id,bundle.local_snapshot.snapshot_id,bundle.venue_snapshot.snapshot_id,bundle.evaluated_at_utc,_json_param([o.record_sha256 for o in bundle.local_orders]),_json_param([o.record_sha256 for o in bundle.venue_orders]),_json_param([f.record_sha256 for f in bundle.local_fills]),_json_param([f.record_sha256 for f in bundle.venue_fills]),_json_param(bundle.monitoring_evidence),_json_param(bundle.kill_evidence)),bundle.record_sha256)
            if recovery is not None:self.record_recovery(recovery)
            if kill_switch is not None:
                if kill_event is not None:self.persist_kill_event(kill_switch,kill_event)
                else:self.record_kill_switch(kill_switch)
                if fail_at=="kill_switch":raise RuntimeError("injected reconciliation kill failure")
            if lifecycle_event is not None:self.record_lifecycle(lifecycle_event)
            self._execute("UPDATE execution.paper_run_risk_state SET version=version+1,latest_account_snapshot_at_utc=%s,latest_reconciliation_at_utc=%s,latest_reconciliation_status=%s,lifecycle_sequence=GREATEST(lifecycle_sequence,%s),updated_at_utc=%s,record_sha256=%s WHERE paper_run_id=%s",(bundle.local_snapshot.venue_as_of_utc,bundle.reconciliation.reconciled_at_utc,bundle.reconciliation.status.value,0 if lifecycle_event is None else lifecycle_event.sequence,bundle.evaluated_at_utc,sha256_payload({"run":bundle.reconciliation.paper_run_id,"reconciliation_bundle":bundle.bundle_id}),bundle.reconciliation.paper_run_id))
        return bundle
    def bind_internal_venue_economics(self,venue,paper_run_id,*,at_utc,allow_create=False):
        row=self._fetchone("SELECT * FROM execution.paper_internal_venue_economics WHERE paper_run_id=%s",(paper_run_id,))
        if row is not None:
            if str(row["internal_venue_implementation_sha256"])!=venue.implementation_sha256:raise Phase7ConflictError("persisted InternalPaperVenue implementation identity changed")
            venue.fee_bps=Decimal(str(row["fee_bps"]));venue.maximum_adverse_slippage_bps=Decimal(str(row["maximum_adverse_slippage_bps"]));venue.reservation_calculator_version=str(row["reservation_calculator_version"]);venue.fee_currency_policy=str(row["fee_currency_policy"]);venue.fill_price_policy=str(row["fill_price_policy"]);return False
        exists=self._fetchone("SELECT paper_run_id FROM execution.paper_runs WHERE paper_run_id=%s",(paper_run_id,))
        if exists is None:return False
        if not allow_create:raise Phase7ConflictError("persisted run lacks immutable InternalPaperVenue economics configuration")
        digest=sha256_payload({"run":paper_run_id,"fee_bps":venue.fee_bps,"maximum_adverse_slippage_bps":venue.maximum_adverse_slippage_bps,"reservation_calculator_version":venue.reservation_calculator_version,"fee_currency_policy":venue.fee_currency_policy,"fill_price_policy":venue.fill_price_policy,"implementation_sha256":venue.implementation_sha256})
        with self.transaction():self._strict("execution.paper_internal_venue_economics","paper_run_id",paper_run_id,("fee_bps","maximum_adverse_slippage_bps","reservation_calculator_version","fee_currency_policy","fill_price_policy","internal_venue_implementation_sha256","created_at_utc"),(venue.fee_bps,venue.maximum_adverse_slippage_bps,venue.reservation_calculator_version,venue.fee_currency_policy,venue.fill_price_policy,venue.implementation_sha256,at_utc),digest)
        return True
    def record_internal_venue_command(self,*,paper_run_id,submission_id,client_order_id,command_type,idempotency_key,at_utc,payload,parent_command_id=None):
        command_id=deterministic_paper_uuid("internal-venue-command",{"run":paper_run_id,"type":command_type,"idempotency_key":idempotency_key})
        digest=sha256_payload({"command":command_id,"run":paper_run_id,"submission":submission_id,"client":client_order_id,"type":command_type,"idempotency_key":idempotency_key,"at":at_utc,"payload":payload,"parent":parent_command_id})
        with self.transaction():
            self._lock(paper_run_id)
            self._strict("execution.paper_internal_venue_commands","command_id",command_id,("paper_run_id","submission_id","client_order_id","command_type","idempotency_key","command_at_utc","payload_jsonb","parent_command_id"),(paper_run_id,submission_id,client_order_id,command_type,idempotency_key,at_utc,_json_param(payload),parent_command_id),digest)
        return command_id

    def append_internal_venue_event(self,*,paper_run_id,command_id,submission_id,client_order_id,event_type,at_utc,details):
        event_id=deterministic_paper_uuid("internal-venue-event",{"command":command_id,"event_type":event_type})
        digest=sha256_payload({"event":event_id,"run":paper_run_id,"command":command_id,"submission":submission_id,"client":client_order_id,"type":event_type,"at":at_utc,"details":details})
        with self.transaction():
            existing=self._fetchone("SELECT venue_sequence,record_sha256 FROM execution.paper_internal_venue_events WHERE internal_venue_event_id=%s",(event_id,))
            if existing is not None:
                if str(existing["record_sha256"])!=digest:raise Phase7ConflictError("internal venue event identity changed evidence")
                return int(existing["venue_sequence"]),event_id
            self._lock(paper_run_id)
            self._execute("INSERT INTO execution.paper_internal_venue_sequences (paper_run_id,last_sequence,updated_at_utc,record_sha256) VALUES (%s,0,%s,%s) ON CONFLICT (paper_run_id) DO NOTHING",(paper_run_id,at_utc,sha256_payload({"run":paper_run_id,"sequence":0})))
            row=self._fetchone("SELECT * FROM execution.paper_internal_venue_sequences WHERE paper_run_id=%s FOR UPDATE",(paper_run_id,));sequence=int(row["last_sequence"])+1
            self._strict("execution.paper_internal_venue_events","internal_venue_event_id",event_id,("paper_run_id","command_id","submission_id","client_order_id","venue_sequence","event_type","occurred_at_utc","details_jsonb"),(paper_run_id,command_id,submission_id,client_order_id,sequence,event_type,at_utc,_json_param(details)),digest)
            self._execute("UPDATE execution.paper_internal_venue_sequences SET last_sequence=%s,updated_at_utc=%s,record_sha256=%s WHERE paper_run_id=%s",(sequence,at_utc,sha256_payload({"run":paper_run_id,"sequence":sequence,"event":event_id}),paper_run_id))
        return sequence,event_id

    def load_internal_venue_events(self,paper_run_id):
        return self._fetchall("SELECT e.*,c.command_type,c.payload_jsonb,c.idempotency_key,c.command_at_utc FROM execution.paper_internal_venue_events e JOIN execution.paper_internal_venue_commands c USING (command_id) WHERE e.paper_run_id=%s ORDER BY e.venue_sequence,e.internal_venue_event_id",(paper_run_id,))

    def persist_order_observation(self,submission,order,*,observed_at_utc,source,query_id=None,evidence_sha256=None,internal_venue_event_id=None):
        observation_id=deterministic_paper_uuid("venue-order-observation",{"submission":submission.submission_id,"venue_sequence":order.venue_sequence})
        evidence=evidence_sha256 or order.record_sha256
        digest=sha256_payload({"observation":observation_id,"order":order.record_sha256,"economics":order.economics_sha256})
        mapping={VenueOrderState.PENDING_ACK:"pending_ack",VenueOrderState.ACKNOWLEDGED:"acknowledged",VenueOrderState.PARTIALLY_FILLED:"partially_filled",VenueOrderState.FILLED:"filled",VenueOrderState.CANCEL_PENDING:"cancel_pending",VenueOrderState.CANCELLED:"cancelled",VenueOrderState.REJECTED:"rejected",VenueOrderState.EXPIRED:"expired",VenueOrderState.UNKNOWN_PENDING_RECOVERY:"pending_recovery"}
        terminal_states={"filled","cancelled","rejected","expired"}
        reverse_terminal={"filled":VenueOrderState.FILLED,"cancelled":VenueOrderState.CANCELLED,"rejected":VenueOrderState.REJECTED,"expired":VenueOrderState.EXPIRED}
        with self.transaction():
            self._lock(submission.paper_run_id)
            existing_observation=self._fetchone("SELECT record_sha256 FROM execution.paper_venue_order_observations WHERE venue_order_observation_id=%s",(observation_id,))
            if existing_observation is not None:
                if str(existing_observation["record_sha256"])!=digest:raise Phase7ConflictError("venue observation identity changed economics or status evidence")
            self._strict("execution.paper_venue_order_observations","venue_order_observation_id",observation_id,("paper_run_id","submission_id","client_order_id","venue_order_id","venue_sequence","state","original_quantity","cumulative_filled_quantity","remaining_quantity","average_fill_price","venue_created_at_utc","venue_updated_at_utc","first_observed_at_utc","observation_source","query_id","internal_venue_event_id","economics_sha256","venue_record_sha256","evidence_sha256"),(order.paper_run_id,order.submission_id,order.client_order_id,order.venue_order_id,order.venue_sequence,order.state.value,order.quantity,order.cumulative_filled_quantity,order.remaining_quantity,order.average_fill_price,order.created_at_utc,order.updated_at_utc,observed_at_utc,source,query_id,internal_venue_event_id,order.economics_sha256,order.record_sha256,evidence),digest)
            prior=self._fetchone("SELECT * FROM execution.paper_order_projections WHERE submission_id=%s FOR UPDATE",(submission.submission_id,))
            observed=mapping[order.state]
            if prior is not None:
                if str(prior["economics_sha256"])!=order.economics_sha256 or str(prior["venue_order_id"])!=order.venue_order_id:raise Phase7ConflictError("order projection immutable identity conflict")
                if int(prior["venue_sequence"])>order.venue_sequence or Decimal(str(prior["cumulative_filled_quantity"]))>order.cumulative_filled_quantity:raise Phase7ConflictError("order observation regressed venue sequence or cumulative fill")
            fill_row=self._fetchone("SELECT COALESCE(sum(quantity),0) applied_quantity,bool_and(accounting_applied) accounting_complete,COALESCE(max(venue_sequence),0) latest_fill_sequence FROM execution.paper_fills WHERE submission_id=%s",(submission.submission_id,));applied=Decimal(str(fill_row["applied_quantity"] or 0));fill_complete=applied==order.cumulative_filled_quantity and bool(fill_row["accounting_complete"] if fill_row["accounting_complete"] is not None else order.cumulative_filled_quantity==0)
            prior_disposition="active" if prior is None else str(prior.get("terminal_disposition") or (prior["authority_state"] if prior["authority_state"] in terminal_states else "active"))
            disposition=prior_disposition if prior_disposition!="active" else (observed if observed in terminal_states else "active")
            terminal=disposition!="active"
            authority=disposition if terminal else observed;blocked=None
            if not fill_complete:
                authority="pending_recovery";blocked="missing_complete_fill_fee_accounting_evidence"
            terminal_sequence=(int(prior["terminal_observation_sequence"]) if prior is not None and prior.get("terminal_observation_sequence") is not None else order.venue_sequence if terminal else None)
            latest_fill_sequence=int(fill_row["latest_fill_sequence"] or 0);remaining=order.quantity-order.cumulative_filled_quantity
            if prior is not None and prior["latest_observation_id"]==observation_id and str(prior["authority_state"])==authority and bool(prior["fill_application_complete"])==fill_complete and Decimal(str(prior["cumulative_filled_quantity"]))==order.cumulative_filled_quantity:return False
            version=0 if prior is None else int(prior["version"])+1;projection_hash=sha256_payload({"submission":submission.submission_id,"observation":observation_id,"authority":authority,"disposition":disposition,"filled":order.cumulative_filled_quantity,"complete":fill_complete,"terminal_sequence":terminal_sequence,"latest_fill_sequence":latest_fill_sequence,"version":version})
            values=(observation_id,order.venue_sequence,order.state.value,authority,order.cumulative_filled_quantity,order.average_fill_price,terminal,fill_complete,blocked,version,observed_at_utc,projection_hash,disposition,remaining,terminal_sequence,latest_fill_sequence)
            if prior is None:self._execute("INSERT INTO execution.paper_order_projections (submission_id,paper_run_id,client_order_id,venue_order_id,latest_observation_id,venue_sequence,observed_state,authority_state,cumulative_filled_quantity,average_fill_price,terminal,fill_application_complete,blocked_reason,version,updated_at_utc,economics_sha256,record_sha256,terminal_disposition,remaining_quantity,terminal_observation_sequence,latest_fill_sequence) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",(submission.submission_id,submission.paper_run_id,submission.client_order_id,order.venue_order_id,*values[:11],order.economics_sha256,projection_hash,*values[12:]))
            else:self._execute("UPDATE execution.paper_order_projections SET latest_observation_id=%s,venue_sequence=%s,observed_state=%s,authority_state=%s,cumulative_filled_quantity=%s,average_fill_price=%s,terminal=%s,fill_application_complete=%s,blocked_reason=%s,version=%s,updated_at_utc=%s,record_sha256=%s,terminal_disposition=%s,remaining_quantity=%s,terminal_observation_sequence=%s,latest_fill_sequence=%s WHERE submission_id=%s",(*values,submission.submission_id))
            canonical_order=replace(order,state=reverse_terminal[disposition]) if terminal and order.state is not reverse_terminal[disposition] else order
            self.record_order(canonical_order)
            submission_state=authority
            self._execute("UPDATE execution.paper_order_submissions SET state=%s,record_sha256=%s WHERE submission_id=%s",(submission_state,sha256_payload({"submission":submission.submission_id,"state":submission_state,"disposition":disposition,"observation":observation_id}),submission.submission_id))
            if terminal and fill_complete:
                if disposition=="filled":
                    reservation=self._fetchone("SELECT state,remaining_amount,remaining_quantity FROM execution.paper_reservations WHERE submission_id=%s FOR UPDATE",(submission.submission_id,))
                    if reservation and (reservation["state"]=="open" or Decimal(str(reservation["remaining_amount"]))!=0 or Decimal(str(reservation["remaining_quantity"]))!=0):raise IncompleteRecoveryEvidence("FILLED observation cannot close before all fills consume the reservation")
                else:self._release(submission,observed_at_utc,observation_id)
                self._close_open_once(submission,observed_at_utc,observation_id)
        return True
    def record_recovery_observation_bundle(self,bundle:PaperRecoveryObservationBundle):
        hashes=bundle.observation_hashes
        with self.transaction():
            self._strict("execution.paper_recovery_observation_bundles","recovery_observation_bundle_id",bundle.bundle_id,("paper_run_id","submission_id","query_id","query_started_at_utc","query_completed_at_utc","queried_order_sha256","recent_order_hashes_jsonb","open_order_hashes_jsonb","fill_hashes_jsonb","balance_hashes_jsonb","position_hashes_jsonb","account_snapshot_sha256","fill_evidence_complete","incompleteness_reason","observation_hashes_jsonb"),(bundle.paper_run_id,bundle.submission_id,bundle.query_id,bundle.query_started_at_utc,bundle.query_completed_at_utc,hashes["queried_order"],_json_param(hashes["recent_orders"]),_json_param(hashes["open_orders"]),_json_param(hashes["fills"]),_json_param(hashes["balances"]),_json_param(hashes["positions"]),hashes["account_snapshot"],bundle.fill_evidence_complete,bundle.incompleteness_reason,_json_param(hashes)),bundle.record_sha256)
        return bundle

    def reconciliation_authority_checks(self,paper_run_id,*,accounting_reservations,venue_reservations,venue_sequence):
        checks=[];rows=self._fetchall("SELECT client_order_id,remaining_amount,state FROM execution.paper_reservations WHERE paper_run_id=%s",(paper_run_id,));clients=set(accounting_reservations)|set(venue_reservations)|{str(x["client_order_id"]) for x in rows};postgres={str(x["client_order_id"]):Decimal(str(x["remaining_amount"])) if x["state"]=="open" else Decimal(0) for x in rows}
        for client in sorted(clients):
            pa=postgres.get(client,Decimal(0));ar=accounting_reservations.get(client);aa=Decimal(0) if ar is None else Decimal(ar.amount);vr=venue_reservations.get(client);va=Decimal(0) if vr is None else Decimal(vr["amount"])
            if not pa==aa==va:checks.append({"type":"reservation_authority_mismatch","identity":client,"local":{"postgres":str(pa),"accounting":str(aa)},"venue":str(va),"explanation":"PostgreSQL, PaperAccounting, and InternalPaperVenue reservation amounts differ"})
        risk=self._fetchone("SELECT open_order_count FROM execution.paper_run_risk_state WHERE paper_run_id=%s",(paper_run_id,));counted=self._fetchone("SELECT count(*) value FROM execution.paper_order_submissions WHERE paper_run_id=%s AND counted_open",(paper_run_id,));expected=int(counted["value"]);observed=int(risk["open_order_count"])
        if expected!=observed:checks.append({"type":"order_budget_mismatch","identity":"open_order_count","local":expected,"venue":observed,"explanation":"counted_open submissions differ from locked run risk budget"})
        sequence=self._fetchone("SELECT COALESCE(max(venue_sequence),0) value FROM execution.paper_internal_venue_events WHERE paper_run_id=%s",(paper_run_id,));persisted=int(sequence["value"])
        if persisted!=int(venue_sequence):checks.append({"type":"venue_event_sequence_mismatch","identity":"venue_sequence","local":persisted,"venue":int(venue_sequence),"explanation":"PostgreSQL internal venue event sequence differs from the reconstructed venue"})
        projections=self._fetchall("SELECT p.*,o.venue_order_observation_id,o.state observation_state,o.cumulative_filled_quantity observation_cumulative,s.state submission_state,lo.state latest_order_state,lo.cumulative_filled_quantity latest_order_cumulative FROM execution.paper_order_projections p JOIN execution.paper_order_submissions s USING (submission_id) LEFT JOIN execution.paper_venue_order_observations o ON o.venue_order_observation_id=p.latest_observation_id LEFT JOIN LATERAL (SELECT state,cumulative_filled_quantity FROM execution.paper_orders x WHERE x.submission_id=p.submission_id ORDER BY venue_sequence DESC,paper_order_record_id DESC LIMIT 1) lo ON true WHERE p.paper_run_id=%s",(paper_run_id,))
        for row in projections:
            identity=str(row["submission_id"])
            if row["venue_order_observation_id"] is None:checks.append({"type":"latest_observation_mismatch","identity":identity,"local":str(row["latest_observation_id"]),"venue":None,"explanation":"latest order projection lacks its append-only observation"})
            if not bool(row["fill_application_complete"]):checks.append({"type":"fill_application_incomplete","identity":identity,"local":False,"venue":True,"explanation":"venue cumulative fill is not fully represented by applied fill/accounting rows"})
            expected_state=str(row["terminal_disposition"]) if str(row["terminal_disposition"])!="active" and bool(row["fill_application_complete"]) else str(row["authority_state"])
            if str(row["submission_state"])!=expected_state or str(row["latest_order_state"])!=(str(row["terminal_disposition"]) if str(row["terminal_disposition"])!="active" else str(row["observed_state"])):checks.append({"type":"order_status_mismatch","identity":identity,"local":{"projection":str(row["authority_state"]),"disposition":str(row["terminal_disposition"]),"submission":str(row["submission_state"])},"venue":{"latest_order":str(row["latest_order_state"]),"observation":str(row["observation_state"])},"explanation":"projection, submission, latest order, and terminal disposition are inconsistent"})
            if Decimal(str(row["cumulative_filled_quantity"]))!=Decimal(str(row["latest_order_cumulative"])) or Decimal(str(row["cumulative_filled_quantity"]))!=Decimal(str(row["observation_cumulative"])):checks.append({"type":"quantity_mismatch","identity":identity,"local":str(row["cumulative_filled_quantity"]),"venue":{"latest_order":str(row["latest_order_cumulative"]),"observation":str(row["observation_cumulative"])},"explanation":"projection, latest order, and latest observation cumulative fills differ"})
        return tuple(checks)
    def assert_reservation_consistency(self,submission_id,*,accounting_amount,venue_amount):
        row=self._fetchone("SELECT remaining_amount,state FROM execution.paper_reservations WHERE submission_id=%s",(submission_id,));postgres_amount=Decimal(0) if row is None or row["state"]!="open" else Decimal(str(row["remaining_amount"]));accounting_amount=Decimal(accounting_amount);venue_amount=Decimal(venue_amount)
        if not (postgres_amount==accounting_amount==venue_amount):raise Phase7ConflictError(f"reservation authority mismatch postgres={postgres_amount} accounting={accounting_amount} venue={venue_amount}")
        return postgres_amount

    def prepare_expiry(self,submission,*,at_utc):
        expiry_id=deterministic_paper_uuid("expiry",{"submission":submission.submission_id})
        with self.transaction():
            self._strict("execution.paper_expiry_outbox","expiry_id",expiry_id,("paper_run_id","submission_id","client_order_id","state","requested_at_utc","updated_at_utc"),(submission.paper_run_id,submission.submission_id,submission.client_order_id,"expiry_requested",at_utc,at_utc),sha256_payload({"expiry":expiry_id,"state":"expiry_requested"}))
        return expiry_id

    def claim_expiry(self,submission,*,worker_id,at_utc,lease_seconds=30):
        with self.transaction():
            row=self._fetchone("SELECT * FROM execution.paper_expiry_outbox WHERE submission_id=%s FOR UPDATE",(submission.submission_id,))
            if not row or row["state"]!="expiry_requested":raise DispatchNotClaimable("expiry requires recovery")
            token=deterministic_paper_uuid("expiry-claim",{"expiry":row["expiry_id"],"worker":worker_id});lease=at_utc+timedelta(seconds=lease_seconds)
            self._execute("UPDATE execution.paper_expiry_outbox SET state='expiry_claimed',claimed_at_utc=%s,claim_token=%s,claimed_by=%s,claim_lease_expires_at_utc=%s,updated_at_utc=%s,record_sha256=%s WHERE expiry_id=%s",(at_utc,token,worker_id,lease,at_utc,sha256_payload({"expiry":row["expiry_id"],"claim":token,"lease":lease}),row["expiry_id"]));return token

    def complete_expiry(self,submission,*,claim_token,confirmed,at_utc,order=None,evidence_sha256=None):
        with self.transaction():
            row=self._fetchone("SELECT * FROM execution.paper_expiry_outbox WHERE submission_id=%s FOR UPDATE",(submission.submission_id,))
            if not row or row.get("claim_token")!=claim_token:raise DispatchNotClaimable("expiry claim mismatch")
            if row.get("claim_lease_expires_at_utc") and at_utc>=row["claim_lease_expires_at_utc"]:raise DispatchNotClaimable("expiry claim lease expired")
            state="expiry_confirmed" if confirmed else "expiry_unknown";self._execute("UPDATE execution.paper_expiry_outbox SET state=%s,updated_at_utc=%s,evidence_sha256=%s,record_sha256=%s WHERE expiry_id=%s",(state,at_utc,evidence_sha256,sha256_payload({"expiry":row["expiry_id"],"state":state,"evidence":evidence_sha256}),row["expiry_id"]))
            if confirmed:
                if order is None or order.state is not VenueOrderState.EXPIRED:raise ValueError("confirmed expiry requires EXPIRED order observation")
                self.persist_order_observation(submission,order,observed_at_utc=at_utc,source="durable_expiry",evidence_sha256=evidence_sha256)
            return confirmed
    def claim_expiry_recovery(self,submission,*,worker_id,at_utc,lease_seconds=30):
        with self.transaction():
            row=self._fetchone("SELECT * FROM execution.paper_expiry_outbox WHERE submission_id=%s FOR UPDATE",(submission.submission_id,))
            if not row or row["state"] not in ("expiry_claimed","expiry_unknown"):raise DispatchNotClaimable("expiry is not query-recoverable")
            if row["state"]=="expiry_claimed" and row.get("claim_lease_expires_at_utc") and row["claim_lease_expires_at_utc"]>at_utc:raise DispatchNotClaimable("original expiry claim lease is still active")
            if row.get("recovery_lease_expires_at_utc") and row["recovery_lease_expires_at_utc"]>at_utc:raise DispatchNotClaimable("expiry recovery already belongs to another worker")
            generation=int(row.get("recovery_generation") or 0)+1;token=deterministic_paper_uuid("expiry-recovery-claim",{"expiry":row["expiry_id"],"generation":generation,"worker":worker_id});lease=at_utc+timedelta(seconds=lease_seconds);recovery_id=deterministic_paper_uuid("expiry-recovery",{"expiry":row["expiry_id"],"generation":generation})
            self._execute("UPDATE execution.paper_expiry_outbox SET recovery_claim_token=%s,recovery_claimed_by=%s,recovery_claimed_at_utc=%s,recovery_lease_expires_at_utc=%s,recovery_generation=%s,updated_at_utc=%s,record_sha256=%s WHERE expiry_id=%s",(token,worker_id,at_utc,lease,generation,at_utc,sha256_payload({"expiry":row["expiry_id"],"recovery_claim":token,"generation":generation}),row["expiry_id"]))
            self._strict("execution.paper_expiry_recovery_records","expiry_recovery_id",recovery_id,("expiry_id","paper_run_id","submission_id","recovery_generation","recovery_claim_token","worker_id","claimed_at_utc","lease_expires_at_utc"),(row["expiry_id"],submission.paper_run_id,submission.submission_id,generation,token,worker_id,at_utc,lease),sha256_payload({"expiry_recovery":recovery_id,"claim":token,"lease":lease}));return token

    def complete_expiry_recovery(self,submission,*,recovery_claim_token,at_utc,order=None,evidence_sha256=None):
        with self.transaction():
            row=self._fetchone("SELECT * FROM execution.paper_expiry_outbox WHERE submission_id=%s FOR UPDATE",(submission.submission_id,))
            if not row or row.get("recovery_claim_token")!=recovery_claim_token:raise DispatchNotClaimable("expiry recovery claim mismatch")
            if row.get("recovery_lease_expires_at_utc") and at_utc>=row["recovery_lease_expires_at_utc"]:raise DispatchNotClaimable("expiry recovery claim lease expired")
            terminal=None if order is None else {VenueOrderState.EXPIRED:"expiry_confirmed",VenueOrderState.FILLED:"superseded_by_fill",VenueOrderState.CANCELLED:"superseded_by_cancel",VenueOrderState.REJECTED:"superseded_by_rejection"}.get(order.state)
            state="expiry_confirmed" if terminal is not None else "expiry_unknown";self._execute("UPDATE execution.paper_expiry_outbox SET state=%s,updated_at_utc=%s,evidence_sha256=%s,record_sha256=%s WHERE expiry_id=%s",(state,at_utc,evidence_sha256,sha256_payload({"expiry":row["expiry_id"],"state":state,"terminal":terminal,"evidence":evidence_sha256}),row["expiry_id"]))
            if terminal is not None:self.persist_order_observation(submission,order,observed_at_utc=at_utc,source="expiry_recovery",evidence_sha256=evidence_sha256)
            self._execute("UPDATE execution.paper_expiry_recovery_records SET completed_at_utc=%s,outcome=%s,query_evidence_sha256=%s,record_sha256=%s WHERE expiry_id=%s AND recovery_claim_token=%s AND completed_at_utc IS NULL",(at_utc,terminal or "expiry_unknown",evidence_sha256,sha256_payload({"expiry":row["expiry_id"],"claim":recovery_claim_token,"outcome":terminal or "expiry_unknown","evidence":evidence_sha256}),row["expiry_id"],recovery_claim_token));return terminal is not None
    def _identity(self,value):
        x=self._map(value);return SeriesIdentity(str(x["provider_name"]),str(x["exchange"]),str(x["provider_instrument_id"]),str(x["canonical_symbol"]),InstrumentType(str(x["instrument_type"])),str(x["timeframe"]),None if x.get("settlement_asset") is None else str(x["settlement_asset"]))
    def _submission_from_row(self,row):
        risk=self._fetchone("SELECT evidence_jsonb FROM execution.paper_runtime_risk_decisions WHERE submission_id=%s",(row["submission_id"],))
        if not risk:raise ValueError("submission lacks runtime identity evidence")
        identity=self._identity(self._map(risk["evidence_jsonb"])["series_identity"]);return PaperOrderSubmission(row["paper_run_id"],row["manifest_id"],row["approval_id"],row["order_intent_id"],str(row["client_order_id"]),str(row["idempotency_key"]),identity,row["side"],row["order_type"],row["time_in_force"],row["accounting_mode"],Decimal(str(row["quantity"])),Decimal(str(row["reference_price"])),Decimal(str(row["submitted_notional"])),row["submitted_at_utc"],str(row["economics_sha256"]),state=row["state"],limit_price=None if row["limit_price"] is None else Decimal(str(row["limit_price"])),stop_price=None if row["stop_price"] is None else Decimal(str(row["stop_price"])),submission_id=row["submission_id"],market_evidence_price=Decimal(str(row["market_evidence_price"])),risk_reference_price=Decimal(str(row["risk_reference_price"])),worst_case_order_price=Decimal(str(row["worst_case_order_price"])),risk_notional=Decimal(str(row["risk_notional"])),reservation_notional=Decimal(str(row["reservation_notional"])),price_deviation_bps=Decimal(str(row["price_deviation_bps"])),price_source_sha256=str(row["price_source_sha256"]),price_calculator_version=str(row["price_calculator_version"]))
    def load_state_bundle(self,run):
        return {"run":self._fetchone("SELECT * FROM execution.paper_runs WHERE paper_run_id=%s",(run,)),"configuration":self._fetchone("SELECT c.* FROM execution.paper_configuration_snapshots c JOIN execution.paper_runs r ON r.configuration_sha256=c.configuration_sha256 WHERE r.paper_run_id=%s",(run,)),"preflight":self._fetchone("SELECT * FROM execution.paper_preflight_reports WHERE paper_run_id=%s ORDER BY evaluated_at_utc DESC LIMIT 1",(run,)),"approval":self._fetchone("SELECT * FROM execution.paper_approvals WHERE paper_run_id=%s",(run,)),"approval_events":self._fetchall("SELECT * FROM execution.paper_approval_state_events WHERE paper_run_id=%s ORDER BY occurred_at_utc,approval_event_id",(run,)),"manifest":self.get_manifest(run),"kill_switch":self.get_kill_switch(run),"submissions":self._fetchall("SELECT * FROM execution.paper_order_submissions WHERE paper_run_id=%s ORDER BY submitted_at_utc,submission_id",(run,)),"orders":self._fetchall("SELECT DISTINCT ON (client_order_id) * FROM execution.paper_orders WHERE paper_run_id=%s ORDER BY client_order_id,venue_sequence DESC,paper_order_record_id DESC",(run,)),"fills":self._fetchall("SELECT * FROM execution.paper_fills WHERE paper_run_id=%s ORDER BY filled_at_utc,venue_sequence,fill_id",(run,)),"balances":self._fetchall("SELECT * FROM execution.paper_account_balance_projection WHERE paper_run_id=%s ORDER BY currency",(run,)),"positions":self._fetchall("SELECT * FROM execution.paper_account_position_projection WHERE paper_run_id=%s ORDER BY series_identity_sha256",(run,)),"reservations":self._fetchall("SELECT * FROM execution.paper_reservations WHERE paper_run_id=%s ORDER BY client_order_id",(run,)),"risk_state":self._fetchone("SELECT * FROM execution.paper_run_risk_state WHERE paper_run_id=%s",(run,)),"dispatches":self._fetchall("SELECT * FROM execution.paper_dispatch_outbox WHERE paper_run_id=%s ORDER BY eligible_at_utc,dispatch_id",(run,)),"cancellations":self._fetchall("SELECT * FROM execution.paper_cancel_outbox WHERE paper_run_id=%s ORDER BY requested_at_utc,cancel_id",(run,)),"lifecycle":self._fetchall("SELECT * FROM execution.paper_lifecycle_events WHERE paper_run_id=%s ORDER BY deterministic_sequence",(run,)),"latest_reconciliation":self._fetchone("SELECT * FROM execution.paper_reconciliations WHERE paper_run_id=%s ORDER BY reconciled_at_utc DESC,reconciliation_id DESC LIMIT 1",(run,)),"recovery":self._fetchall("SELECT * FROM execution.paper_recovery_records WHERE paper_run_id=%s ORDER BY started_at_utc,recovery_id",(run,)),"market_evidence":self._fetchall("SELECT * FROM execution.paper_market_data_evidence WHERE paper_run_id=%s ORDER BY observed_at_utc,market_evidence_id",(run,)),"order_budget_events":self._fetchall("SELECT * FROM execution.paper_order_budget_events WHERE paper_run_id=%s ORDER BY occurred_at_utc,order_budget_event_id",(run,)),"reconciliation_bundles":self._fetchall("SELECT * FROM execution.paper_reconciliation_bundles WHERE paper_run_id=%s ORDER BY evaluated_at_utc,reconciliation_bundle_id",(run,)),"internal_venue_events":self.load_internal_venue_events(run),"order_observations":self._fetchall("SELECT * FROM execution.paper_venue_order_observations WHERE paper_run_id=%s ORDER BY venue_sequence,venue_order_observation_id",(run,)),"order_projections":self._fetchall("SELECT * FROM execution.paper_order_projections WHERE paper_run_id=%s ORDER BY client_order_id",(run,)),"recovery_observation_bundles":self._fetchall("SELECT * FROM execution.paper_recovery_observation_bundles WHERE paper_run_id=%s ORDER BY query_completed_at_utc,recovery_observation_bundle_id",(run,)),"expiry":self._fetchall("SELECT * FROM execution.paper_expiry_outbox WHERE paper_run_id=%s ORDER BY requested_at_utc,expiry_id",(run,)),"expiry_recovery":self._fetchall("SELECT * FROM execution.paper_expiry_recovery_records WHERE paper_run_id=%s ORDER BY claimed_at_utc,expiry_recovery_id",(run,)),"internal_venue_economics":self._fetchone("SELECT * FROM execution.paper_internal_venue_economics WHERE paper_run_id=%s",(run,))}
    def hydrate_accounting(self,run):
        state=self.load_state_bundle(run)
        if not state["run"] or not state["configuration"]:raise ValueError("run is not reconstructable")
        a=PaperAccounting(paper_run_id=run,account_reference=str(state["run"]["account_reference"]),balances={str(x["currency"]):Decimal(str(x["total"])) for x in state["balances"]});a.positions={str(x["series_identity_sha256"]):VenuePosition(self._identity(x["series_identity_jsonb"]),x["accounting_mode"],Decimal(str(x["quantity"])),None if x["average_entry_price"] is None else Decimal(str(x["average_entry_price"])),Decimal(str(x["realized_pnl"])),Decimal(str(x["funding"]))) for x in state["positions"]};a.reservations={str(x["client_order_id"]):Reservation(str(x["currency"]),Decimal(str(x["remaining_amount"])),Decimal(str(x["original_quantity"])),Decimal(str(x["remaining_quantity"]))) for x in state["reservations"] if x["state"]=="open"};a.applied_fill_ids={x["fill_id"] for x in state["fills"]};a.total_fees=sum((Decimal(str(x["fee_amount"])) for x in state["fills"]),Decimal(0));return a
    def typed_submissions(self,run):return tuple(self._submission_from_row(x) for x in self._fetchall("SELECT * FROM execution.paper_order_submissions WHERE paper_run_id=%s ORDER BY submitted_at_utc,submission_id",(run,)))
    def typed_orders(self,run):
        subs={s.client_order_id:s for s in self.typed_submissions(run)};rows=self._fetchall("SELECT DISTINCT ON (client_order_id) * FROM execution.paper_orders WHERE paper_run_id=%s ORDER BY client_order_id,venue_sequence DESC,paper_order_record_id DESC",(run,));return tuple(VenueOrder(x["paper_run_id"],x["submission_id"],str(x["client_order_id"]),str(x["venue_order_id"]),subs[str(x["client_order_id"])].series_identity,subs[str(x["client_order_id"])].side,subs[str(x["client_order_id"])].order_type,subs[str(x["client_order_id"])].time_in_force,subs[str(x["client_order_id"])].accounting_mode,Decimal(str(x["original_quantity"])),Decimal(str(x["cumulative_filled_quantity"])),None if x["average_fill_price"] is None else Decimal(str(x["average_fill_price"])),x["state"],x["created_at_utc"],x["updated_at_utc"],int(x["venue_sequence"]),str(x["economics_sha256"]),subs[str(x["client_order_id"])].limit_price,subs[str(x["client_order_id"])].stop_price,x.get("operational_request_id"),x.get("reject_reason")) for x in rows)
    def typed_fills(self,run):
        subs={s.client_order_id:s for s in self.typed_submissions(run)};rows=self._fetchall("SELECT * FROM execution.paper_fills WHERE paper_run_id=%s ORDER BY filled_at_utc,venue_sequence,fill_id",(run,));return tuple(VenueFill(x["paper_run_id"],x["submission_id"],str(x["client_order_id"]),str(x["venue_order_id"]),str(x["venue_fill_id"]),subs[str(x["client_order_id"])].series_identity,x["side"],x["accounting_mode"],Decimal(str(x["quantity"])),Decimal(str(x["price"])),Decimal(str(x["fee_amount"])),str(x["fee_currency"]),x["filled_at_utc"],int(x["venue_sequence"]),x["environment"],fill_id=x["fill_id"]) for x in rows)
    def list_unresolved_dispatches(self,run):return self._fetchall("SELECT d.*,s.state submission_state,c.state cancel_state FROM execution.paper_dispatch_outbox d JOIN execution.paper_order_submissions s USING (submission_id) LEFT JOIN execution.paper_cancel_outbox c USING (submission_id) WHERE d.paper_run_id=%s AND (d.state IN ('prepared','dispatch_claimed','unknown') OR s.state IN ('submission_unknown','pending_recovery','cancel_requested','cancel_pending','cancel_unknown') OR c.state IN ('cancel_requested','cancel_claimed','cancel_unknown')) ORDER BY d.updated_at_utc,d.dispatch_id",(run,))
    def persist_preflight_approval(self,*,configuration,credential_reference,snapshot,report,approval):
        """Persist preflight evidence and a still-valid approval without starting a run."""
        with self.transaction():
            self._lock(report.paper_run_id);existing=self._fetchone("SELECT state,configuration_sha256 FROM execution.paper_runs WHERE paper_run_id=%s FOR UPDATE",(report.paper_run_id,))
            if existing:
                stored=self._fetchone("SELECT record_sha256 FROM execution.paper_approvals WHERE approval_id=%s",(approval.approval_id,))
                if not stored or str(stored["record_sha256"])!=approval.record_sha256:raise Phase7ConflictError("preflight approval replay conflict")
                return False
            self.record_credential_reference(credential_reference);self._configuration(configuration,report.evaluated_at_utc);self._execute("INSERT INTO execution.paper_runs (paper_run_id,provider,environment,account_reference,state,configuration_sha256,manifest_id,started_at_utc,updated_at_utc,summary_jsonb,record_sha256) VALUES (%s,%s,%s,%s,'created',%s,NULL,%s,%s,'{}'::jsonb,%s)",(report.paper_run_id,configuration.provider.value,configuration.environment.value,configuration.account_reference,configuration.config_sha256,report.evaluated_at_utc,report.evaluated_at_utc,sha256_payload({"run":report.paper_run_id,"state":"created","configuration":configuration.config_sha256})));self.record_snapshot(snapshot);self.record_preflight(report);self.record_approval(approval);self._approval_event(approval,None,"valid",report.evaluated_at_utc,"preflight_approval_created");return True
    def record_recovery(self,value,fail_at=None):
        with self.transaction():
            old=self._fetchone("SELECT * FROM execution.paper_recovery_records WHERE recovery_id=%s FOR UPDATE",(value.recovery_id,))
            if old is None:self._strict("execution.paper_recovery_records","recovery_id",value.recovery_id,("paper_run_id","submission_id","started_at_utc","completed_at_utc","status","action","explanation","parent_ids"),(value.paper_run_id,value.submission_id,value.started_at_utc,value.completed_at_utc,value.status.value,value.action,value.explanation,list(value.parent_ids)),value.record_sha256)
            elif str(old["record_sha256"])!=value.record_sha256:
                if old["paper_run_id"]!=value.paper_run_id or old["submission_id"]!=value.submission_id or old["action"]!=value.action or old["status"] not in ("started","paused") or value.status.value not in ("paused","recovered","killed","failed"):raise Phase7ConflictError("recovery identity conflict")
                self._execute("UPDATE execution.paper_recovery_records SET completed_at_utc=%s,status=%s,explanation=%s,parent_ids=%s,record_sha256=%s WHERE recovery_id=%s",(value.completed_at_utc,value.status.value,value.explanation,list(value.parent_ids),value.record_sha256,value.recovery_id))
            if fail_at=="recovery":raise RuntimeError("injected recovery failure")
