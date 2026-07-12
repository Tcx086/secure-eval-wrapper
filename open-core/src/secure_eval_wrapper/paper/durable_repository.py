"""PostgreSQL-authoritative Phase 7 durable dispatch and restart state."""
from __future__ import annotations
import json
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
from .models import PaperOrderSubmission,PaperRecoveryRecord,VenueFill,VenueOrder,VenuePosition,deterministic_paper_uuid
from .persistence import Phase7ConflictError,_Phase7BaseRepository

class RuntimeRiskBlocked(PermissionError):
    def __init__(self,reasons):self.reasons=tuple(reasons);super().__init__("paper runtime risk blocked: "+", ".join(self.reasons))
class DispatchNotClaimable(RuntimeError):pass

class DurablePostgresPaperRepository(_Phase7BaseRepository):
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
    def persist_start_run(self,*,run,configuration,credential_reference,snapshot,report,approval,manifest,kill_switch,fail_at=None):
        if approval.state is not ApprovalState.VALID:raise PermissionError("run start requires valid approval")
        with self.transaction():
            self._lock(run.paper_run_id)
            old=self._fetchone("SELECT m.manifest_sha256 FROM execution.paper_runs r LEFT JOIN execution.paper_run_manifests m ON m.paper_run_id=r.paper_run_id WHERE r.paper_run_id=%s FOR UPDATE OF r",(run.paper_run_id,))
            if old is not None:
                if str(old.get("manifest_sha256"))!=manifest.manifest_sha256:raise Phase7ConflictError("paper run replay changed manifest")
                return False
            now=run.started_at_utc
            if now>=approval.expires_at_utc:raise PermissionError("paper approval expired")
            bindings=(approval.paper_run_id==run.paper_run_id and approval.preflight_report_id==report.report_id and approval.configuration_sha256==configuration.config_sha256 and approval.account_snapshot_sha256==snapshot.record_sha256 and approval.credential_reference_sha256==credential_reference.reference_sha256 and approval.provider==configuration.provider and approval.environment==configuration.environment and manifest.approval_id==approval.approval_id)
            if not bindings:raise PermissionError("approval/run/manifest binding mismatch")
            self.record_credential_reference(credential_reference);self._configuration(configuration,now)
            if fail_at=="credential":raise RuntimeError("injected credential/configuration failure")
            self.record_run(run,configuration.provider,configuration.environment,configuration.account_reference,configuration.config_sha256)
            if fail_at=="paper_run":raise RuntimeError("injected paper run failure")
            self.record_snapshot(snapshot)
            if fail_at in {"snapshot","balance","position"}:raise RuntimeError("injected snapshot failure")
            self.record_preflight(report)
            if fail_at in {"preflight","check"}:raise RuntimeError("injected preflight failure")
            self.record_approval(approval);self._approval_event(approval,None,"valid",now,"approval_created")
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
            for b in snapshot.balances:self._execute("INSERT INTO execution.paper_account_balance_projection (paper_run_id,currency,total,version,updated_at_utc,record_sha256) VALUES (%s,%s,%s,0,%s,%s)",(run.paper_run_id,b.currency,b.total,now,sha256_payload({"run":run.paper_run_id,"balance":asdict(b)})))
            for p in snapshot.positions:self._execute("INSERT INTO execution.paper_account_position_projection (paper_run_id,series_identity_sha256,instrument_id,series_identity_jsonb,accounting_mode,quantity,average_entry_price,realized_pnl,funding,version,updated_at_utc,record_sha256) VALUES (%s,%s,%s,%s::jsonb,%s,%s,%s,%s,%s,0,%s,%s)",(run.paper_run_id,p.series_identity.series_identity_sha256,p.series_identity.provider_instrument_id,_json_param(p.series_identity.as_dict()),p.accounting_mode.value,p.quantity,p.average_entry_price,p.realized_pnl,p.funding,now,sha256_payload({"run":run.paper_run_id,"position":asdict(p)})))
            equity=next((b.total for b in snapshot.balances if b.currency==configuration.base_currency),Decimal(0));digest=sha256_payload({"run":run.paper_run_id,"version":0,"equity":equity})
            self._execute("INSERT INTO execution.paper_run_risk_state (paper_run_id,version,trading_day,rate_window_started_at_utc,initial_equity,current_equity,high_watermark_equity,latest_market_data_at_utc,latest_account_snapshot_at_utc,latest_reconciliation_at_utc,latest_reconciliation_status,venue_clock_skew_seconds,updated_at_utc,record_sha256) VALUES (%s,0,%s,%s,%s,%s,%s,%s,%s,%s,'reconciled',0,%s,%s)",(run.paper_run_id,now.date(),now,equity,equity,equity,report.evaluated_at_utc,snapshot.venue_as_of_utc,report.evaluated_at_utc,now,digest))
            return True
    def _submission(self,s,risk_hash):
        cols=("paper_run_id","manifest_id","approval_id","order_intent_id","client_order_id","idempotency_key","series_identity_sha256","instrument_id","side","order_type","time_in_force","accounting_mode","quantity","reference_price","submitted_notional","limit_price","stop_price","submitted_at_utc","state","economics_sha256","pre_submit_risk_sha256")
        vals=(s.paper_run_id,s.manifest_id,s.approval_id,s.order_intent_id,s.client_order_id,s.idempotency_key,s.series_identity.series_identity_sha256,s.series_identity.provider_instrument_id,s.side.value,s.order_type.value,s.time_in_force.value,s.accounting_mode.value,s.quantity,s.reference_price,s.submitted_notional,s.limit_price,s.stop_price,s.submitted_at_utc,s.state.value,s.economics_sha256,risk_hash)
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
        ident=intent.series_identity;itype=ident.instrument_type.value;notional=s.submitted_notional
        check("allowed_instrument",ident.canonical_symbol in c.allowed_instruments,ident.canonical_symbol,c.allowed_instruments)
        check("allowed_instrument_type",itype in c.allowed_instrument_types,itype,c.allowed_instrument_types)
        check("allowed_settlement_asset",ident.settlement_asset in c.allowed_settlement_assets,ident.settlement_asset,c.allowed_settlement_assets)
        check("allowed_order_type",intent.order_type in c.allowed_order_types,intent.order_type.value,[x.value for x in c.allowed_order_types])
        check("perpetual_policy",itype!="perpetual_swap" or c.allow_perpetual,itype,c.allow_perpetual)
        current=next((Decimal(str(p["quantity"])) for p in positions if str(p["series_identity_sha256"])==ident.series_identity_sha256),Decimal(0));projected=current+intent.quantity*intent.side.sign
        check("spot_short_prohibition",intent.accounting_mode is not AccountingMode.SPOT or c.allow_short or projected>=0,projected,0)
        check("intent_position_matches_persisted",intent.current_quantity==current,intent.current_quantity,current)
        check("maximum_order_notional",notional<=c.maximum_order_notional,notional,c.maximum_order_notional)
        check("maximum_position_notional_per_instrument",abs(projected)*intent.reference_price<=c.maximum_position_notional_per_instrument,abs(projected)*intent.reference_price,c.maximum_position_notional_per_instrument)
        marks=self._map(evidence.get("marks"));gross=Decimal(0);net=Decimal(0);missing=[];seen=False
        for p in positions:
            key=str(p["series_identity_sha256"]);qty=Decimal(str(p["quantity"]))
            if key==ident.series_identity_sha256:qty=projected;mark=intent.reference_price;seen=True
            else:
                raw=marks.get(key) or marks.get(str(p["instrument_id"]))
                if raw is None and qty!=0:missing.append(key);continue
                mark=Decimal(str(raw or 0))
            gross+=abs(qty*mark);net+=qty*mark
        if not seen:gross+=abs(projected*intent.reference_price);net+=projected*intent.reference_price
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
        base,quote=self._assets(ident);currency=quote if intent.accounting_mode is AccountingMode.SPOT and intent.side is OrderSide.BUY else base if intent.accounting_mode is AccountingMode.SPOT else ident.settlement_asset;amount=intent.quantity*intent.reference_price if intent.side is OrderSide.BUY or intent.accounting_mode is not AccountingMode.SPOT else intent.quantity
        total=next((Decimal(str(x["total"])) for x in balances if str(x["currency"])==currency),None);reserved=sum((Decimal(str(x["remaining_amount"])) for x in reservations if str(x["currency"])==currency and x["state"]=="open"),Decimal(0));available=None if total is None else total-reserved
        check("reservation_balance_evidence",total is not None,available,"required");check("durable_reservation_available",available is not None and available>=amount,available,amount)
        return tuple(dict.fromkeys(reasons)),checks,currency,amount
    def prepare_submission(self,*,configuration,approval,manifest,intent,risk_decision,now,evidence=None):
        if risk_decision.status is not RiskDecisionStatus.ACCEPTED:raise PermissionError("accepted pre-submit risk required")
        client="sew"+deterministic_paper_uuid("client-order",{"run":manifest.paper_run_id,"intent":intent.order_intent_id}).hex[:29];econ=sha256_payload({"series_identity":intent.series_identity.as_dict(),"side":intent.side,"order_type":intent.order_type,"time_in_force":intent.time_in_force,"accounting_mode":intent.accounting_mode,"quantity":intent.quantity,"limit_price":intent.limit_price,"stop_price":intent.stop_price})
        prepared=PaperOrderSubmission(manifest.paper_run_id,manifest.manifest_id,approval.approval_id,intent.order_intent_id,client,client,intent.series_identity,intent.side,intent.order_type,intent.time_in_force,intent.accounting_mode,intent.quantity,intent.reference_price,intent.quantity*intent.reference_price,now,econ,state=PaperOrderState.PREPARED,limit_price=intent.limit_price,stop_price=intent.stop_price);blocked=()
        with self.transaction():
            self._lock(manifest.paper_run_id);old=self._fetchone("SELECT economics_sha256 FROM execution.paper_order_submissions WHERE submission_id=%s",(prepared.submission_id,))
            if old:
                if str(old["economics_sha256"])!=econ:raise Phase7ConflictError("stable submission identity changed economics")
                return prepared,True
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
            ev=dict(evidence or {});ev.setdefault("market_data_at_utc",intent.event_timestamp_utc);ev.setdefault("account_snapshot_at_utc",state["latest_account_snapshot_at_utc"]);ev.setdefault("reconciliation_at_utc",state["latest_reconciliation_at_utc"]);ev.setdefault("reconciliation_status",state["latest_reconciliation_status"]);ev.setdefault("clock_skew_seconds",state["venue_clock_skew_seconds"]);ev.setdefault("oldest_unknown_age_seconds",self._age(manifest.paper_run_id,("submission_unknown","pending_recovery","dispatch_claimed"),now));ev.setdefault("oldest_unacknowledged_age_seconds",self._age(manifest.paper_run_id,("pending_ack","submitted"),now))
            blocked,checks,currency,amount=self._risk(configuration,approval,intent,prepared,state,positions,balances,reservations,ev,now);stored=replace(prepared,state=PaperOrderState.REJECTED if blocked else PaperOrderState.PREPARED);self._submission(stored,risk_decision.record_sha256)
            decision=deterministic_paper_uuid("runtime-risk",{"submission":prepared.submission_id,"checks":checks});digest=sha256_payload({"decision":decision,"blocked":blocked,"checks":checks,"evidence":ev})
            self._strict("execution.paper_runtime_risk_decisions","runtime_risk_decision_id",decision,("paper_run_id","submission_id","order_intent_id","accepted_pre_submit_risk_sha256","decision_status","reason_codes_jsonb","evaluated_limits_jsonb","persisted_state_jsonb","evidence_jsonb","decided_at_utc"),(manifest.paper_run_id,prepared.submission_id,intent.order_intent_id,risk_decision.record_sha256,"blocked" if blocked else "accepted",_json_param(blocked),_json_param(checks),_json_param(state),_json_param({**ev,"series_identity":intent.series_identity.as_dict()}),now),digest)
            if not blocked:self._reserve_and_enqueue(prepared,currency,amount,state,now)
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
    def _reserve_and_enqueue(self,s,currency,amount,state,now):
        rid=deterministic_paper_uuid("reservation",{"submission":s.submission_id});self._strict("execution.paper_reservations","reservation_id",rid,("paper_run_id","submission_id","client_order_id","currency","original_amount","remaining_amount","original_quantity","remaining_quantity","state","created_at_utc","updated_at_utc","version","economics_sha256"),(s.paper_run_id,s.submission_id,s.client_order_id,currency,amount,amount,s.quantity,s.quantity,"open",now,now,0,s.economics_sha256),sha256_payload({"reservation":rid,"amount":amount,"quantity":s.quantity}));self._reservation_event(rid,s,"reserved",amount,s.quantity,now,s.submission_id)
        did=deterministic_paper_uuid("dispatch",{"submission":s.submission_id});self._strict("execution.paper_dispatch_outbox","dispatch_id",did,("paper_run_id","submission_id","client_order_id","idempotency_key","economics_sha256","state","eligible_at_utc","updated_at_utc"),(s.paper_run_id,s.submission_id,s.client_order_id,s.idempotency_key,s.economics_sha256,"prepared",now,now),sha256_payload({"dispatch":did,"state":"prepared"}));self._event(did,s,"prepared",now)
        self._execute("UPDATE execution.paper_run_risk_state SET version=version+1,trading_day=%s,daily_submitted_notional=%s,approval_submitted_notional=approval_submitted_notional+%s,open_order_count=open_order_count+1,orders_in_current_minute=%s,rate_window_started_at_utc=%s,latest_market_data_at_utc=%s,updated_at_utc=%s,record_sha256=%s WHERE paper_run_id=%s",(now.date(),Decimal(str(state["daily_submitted_notional"]))+s.submitted_notional,s.submitted_notional,int(state["orders_in_current_minute"])+1,state["rate_window_started_at_utc"],s.submitted_at_utc,now,sha256_payload({"run":s.paper_run_id,"submission":s.submission_id}),s.paper_run_id))
    def claim_dispatch(self,s,*,worker_id,at_utc):
        with self.transaction():
            row=self._fetchone("SELECT * FROM execution.paper_dispatch_outbox WHERE submission_id=%s FOR UPDATE",(s.submission_id,))
            if not row:raise DispatchNotClaimable("no prepared outbox")
            if row["state"]=="acknowledged":return None
            if row["state"]!="prepared":raise DispatchNotClaimable("dispatch requires recovery")
            ordinal=int(row["attempt_count"])+1;token=deterministic_paper_uuid("dispatch-claim",{"dispatch":row["dispatch_id"],"ordinal":ordinal,"worker":worker_id});digest=sha256_payload({"dispatch":row["dispatch_id"],"claim":token})
            self._execute("UPDATE execution.paper_dispatch_outbox SET state='dispatch_claimed',claimed_at_utc=%s,claim_token=%s,claimed_by=%s,attempt_count=%s,updated_at_utc=%s,record_sha256=%s WHERE dispatch_id=%s",(at_utc,token,worker_id,ordinal,at_utc,digest,row["dispatch_id"]));self._execute("UPDATE execution.paper_order_submissions SET state='dispatch_claimed',record_sha256=%s WHERE submission_id=%s",(sha256_payload({"submission":s.submission_id,"state":"dispatch_claimed"}),s.submission_id));self._event(row["dispatch_id"],s,"claimed",at_utc,token,worker_id);return token
    def _release(self,s,at,cause,event="released"):
        row=self._fetchone("SELECT * FROM execution.paper_reservations WHERE submission_id=%s FOR UPDATE",(s.submission_id,))
        if not row or row["state"]!="open":return False
        self._execute("UPDATE execution.paper_reservations SET remaining_amount=0,remaining_quantity=0,state=%s,updated_at_utc=%s,version=version+1,record_sha256=%s WHERE reservation_id=%s",("consumed" if event=="consumed" else "released",at,sha256_payload({"reservation":row["reservation_id"],"event":event,"cause":cause}),row["reservation_id"]));self._reservation_event(row["reservation_id"],s,event,-Decimal(str(row["remaining_amount"])),-Decimal(str(row["remaining_quantity"])),at,cause);return True
    def complete_dispatch(self,s,*,claim_token,outcome,at_utc,order=None,classification=None,evidence_sha256=None,fail_at=None):
        if outcome not in {"acknowledged","explicitly_rejected","unknown","recovered"}:raise ValueError("invalid dispatch outcome")
        with self.transaction():
            row=self._fetchone("SELECT * FROM execution.paper_dispatch_outbox WHERE submission_id=%s FOR UPDATE",(s.submission_id,))
            if not row or row["state"] not in {"dispatch_claimed","unknown"}:raise DispatchNotClaimable("outcome has no claim")
            if row.get("claim_token") and claim_token and row["claim_token"]!=claim_token:raise DispatchNotClaimable("claim mismatch")
            state={"acknowledged":"pending_ack","recovered":"pending_ack","explicitly_rejected":"rejected","unknown":"submission_unknown"}[outcome]
            self._execute("UPDATE execution.paper_dispatch_outbox SET state=%s,last_outcome_at_utc=%s,venue_order_id=%s,updated_at_utc=%s,record_sha256=%s WHERE dispatch_id=%s",(outcome,at_utc,None if order is None else order.venue_order_id,at_utc,sha256_payload({"dispatch":row["dispatch_id"],"outcome":outcome,"evidence":evidence_sha256}),row["dispatch_id"]));self._execute("UPDATE execution.paper_order_submissions SET state=%s,record_sha256=%s WHERE submission_id=%s",(state,sha256_payload({"submission":s.submission_id,"state":state}),s.submission_id))
            if fail_at=="after_outcome_before_order":raise RuntimeError("injected outcome/order-event crash")
            if order is not None:self.record_order(order)
            self._event(row["dispatch_id"],s,outcome,at_utc,claim_token,row.get("claimed_by"),classification,evidence_sha256)
            transport_result={"acknowledged":"succeeded","recovered":"succeeded","explicitly_rejected":"rejected","unknown":"unknown"}[outcome];transport_token=deterministic_paper_uuid("recovery-query",{"claim":claim_token,"at":at_utc}) if outcome=="recovered" else claim_token;self._transport(s,transport_token,"query_order" if outcome=="recovered" else "submit",at_utc,transport_result,evidence_sha256)
            if outcome=="unknown":self._unknown_recovery(s,row["claimed_at_utc"],at_utc,"query_original_client_order_id",row["dispatch_id"])
            delta="consecutive_transport_failures+1" if outcome=="unknown" else "0";extra=""
            if outcome=="explicitly_rejected":self._release(s,at_utc,s.submission_id);extra=",open_order_count=GREATEST(open_order_count-1,0)"
            self._execute(f"UPDATE execution.paper_run_risk_state SET version=version+1,consecutive_transport_failures={delta}{extra},updated_at_utc=%s,record_sha256=%s WHERE paper_run_id=%s",(at_utc,sha256_payload({"run":s.paper_run_id,"outcome":outcome,"at":at_utc}),s.paper_run_id));return True
    def prepare_cancel(self,s,*,at_utc,maximum_cancellations_per_minute):
        with self.transaction():
            self._lock(s.paper_run_id);risk=self._fetchone("SELECT * FROM execution.paper_run_risk_state WHERE paper_run_id=%s FOR UPDATE",(s.paper_run_id,));count=0 if (at_utc-risk["rate_window_started_at_utc"]).total_seconds()>=60 else int(risk["cancellations_in_current_minute"]);window=at_utc if count==0 else risk["rate_window_started_at_utc"]
            if count+1>maximum_cancellations_per_minute:raise RuntimeRiskBlocked(("maximum_cancellations_per_minute",))
            dispatch=self._fetchone("SELECT * FROM execution.paper_dispatch_outbox WHERE submission_id=%s FOR UPDATE",(s.submission_id,));cid=deterministic_paper_uuid("cancel",{"dispatch":dispatch["dispatch_id"]})
            self._strict("execution.paper_cancel_outbox","cancel_id",cid,("dispatch_id","paper_run_id","submission_id","client_order_id","state","requested_at_utc","updated_at_utc"),(dispatch["dispatch_id"],s.paper_run_id,s.submission_id,s.client_order_id,"cancel_requested",at_utc,at_utc),sha256_payload({"cancel":cid,"state":"cancel_requested"}));self._execute("UPDATE execution.paper_order_submissions SET state='cancel_requested',record_sha256=%s WHERE submission_id=%s",(sha256_payload({"submission":s.submission_id,"state":"cancel_requested"}),s.submission_id));self._event(dispatch["dispatch_id"],s,"cancel_requested",at_utc);self._execute("UPDATE execution.paper_run_risk_state SET version=version+1,cancellations_in_current_minute=%s,rate_window_started_at_utc=%s,updated_at_utc=%s,record_sha256=%s WHERE paper_run_id=%s",(count+1,window,at_utc,sha256_payload({"run":s.paper_run_id,"cancel":cid}),s.paper_run_id));return cid
    def claim_cancel(self,s,*,worker_id,at_utc):
        with self.transaction():
            row=self._fetchone("SELECT * FROM execution.paper_cancel_outbox WHERE submission_id=%s FOR UPDATE",(s.submission_id,))
            if not row or row["state"]!="cancel_requested":raise DispatchNotClaimable("cancel requires recovery")
            n=int(row["attempt_count"])+1;token=deterministic_paper_uuid("cancel-claim",{"cancel":row["cancel_id"],"ordinal":n,"worker":worker_id});self._execute("UPDATE execution.paper_cancel_outbox SET state='cancel_claimed',claimed_at_utc=%s,claim_token=%s,claimed_by=%s,attempt_count=%s,updated_at_utc=%s,record_sha256=%s WHERE cancel_id=%s",(at_utc,token,worker_id,n,at_utc,sha256_payload({"cancel":row["cancel_id"],"claim":token}),row["cancel_id"]));dispatch=self._fetchone("SELECT dispatch_id FROM execution.paper_dispatch_outbox WHERE submission_id=%s",(s.submission_id,));self._event(dispatch["dispatch_id"],s,"cancel_claimed",at_utc,token,worker_id);return token
    def complete_cancel(self,s,*,claim_token,confirmed,at_utc,evidence_sha256=None):
        outcome="cancel_confirmed" if confirmed else "cancel_unknown"
        with self.transaction():
            row=self._fetchone("SELECT * FROM execution.paper_cancel_outbox WHERE submission_id=%s FOR UPDATE",(s.submission_id,))
            if not row or row["state"] not in {"cancel_claimed","cancel_unknown"}:raise DispatchNotClaimable("cancel outcome has no claim")
            if row.get("claim_token") and claim_token and row["claim_token"]!=claim_token:raise DispatchNotClaimable("cancel claim mismatch")
            self._execute("UPDATE execution.paper_cancel_outbox SET state=%s,updated_at_utc=%s,record_sha256=%s WHERE cancel_id=%s",(outcome,at_utc,sha256_payload({"cancel":row["cancel_id"],"outcome":outcome}),row["cancel_id"]));self._execute("UPDATE execution.paper_order_submissions SET state=%s,record_sha256=%s WHERE submission_id=%s",("cancelled" if confirmed else "cancel_unknown",sha256_payload({"submission":s.submission_id,"state":outcome}),s.submission_id));dispatch=self._fetchone("SELECT dispatch_id FROM execution.paper_dispatch_outbox WHERE submission_id=%s",(s.submission_id,));self._event(dispatch["dispatch_id"],s,outcome,at_utc,claim_token,row.get("claimed_by"),outcome,evidence_sha256)
            transport_token=deterministic_paper_uuid("cancel-recovery-query",{"claim":claim_token,"at":at_utc}) if confirmed and row["state"]=="cancel_unknown" else claim_token;self._transport(s,transport_token,"query_order" if confirmed and row["state"]=="cancel_unknown" else "cancel",at_utc,"succeeded" if confirmed else "unknown",evidence_sha256)
            if not confirmed:self._unknown_recovery(s,row["claimed_at_utc"],at_utc,"query_cancel_by_original_client_order_id",row["cancel_id"])
            if confirmed:self._release(s,at_utc,row["cancel_id"]);failures="0";extra=",open_order_count=GREATEST(open_order_count-1,0)"
            else:failures="consecutive_transport_failures+1";extra=""
            self._execute(f"UPDATE execution.paper_run_risk_state SET version=version+1,consecutive_transport_failures={failures}{extra},updated_at_utc=%s,record_sha256=%s WHERE paper_run_id=%s",(at_utc,sha256_payload({"run":s.paper_run_id,"cancel":outcome}),s.paper_run_id));return True
    def persist_fill_bundle(self,*,fill,order,local_snapshot,venue_snapshot,reconciliation,differences,lifecycle_event,fail_at=None):
        with self.transaction():
            self._lock(fill.paper_run_id);old=self._fetchone("SELECT record_sha256 FROM execution.paper_fills WHERE fill_id=%s",(fill.fill_id,))
            if old:
                if str(old["record_sha256"])!=fill.record_sha256:raise Phase7ConflictError("fill identity changed economics")
                return False
            self.record_fill(fill)
            if fail_at=="fill":raise RuntimeError("injected fill failure")
            fee=deterministic_paper_uuid("paper-fee",{"fill":fill.fill_id});self._strict("execution.paper_fee_entries","fee_entry_id",fee,("fill_id","paper_run_id","amount","currency","occurred_at_utc"),(fill.fill_id,fill.paper_run_id,fill.fee_amount,fill.fee_currency,fill.filled_at_utc),sha256_payload({"fill":fill.fill_id,"amount":fill.fee_amount,"currency":fill.fee_currency}))
            if fail_at=="fee":raise RuntimeError("injected fee failure")
            self.record_order(order)
            projected_state={VenueOrderState.PENDING_ACK:"pending_ack",VenueOrderState.ACKNOWLEDGED:"acknowledged",VenueOrderState.PARTIALLY_FILLED:"partially_filled",VenueOrderState.FILLED:"filled",VenueOrderState.CANCEL_PENDING:"cancel_pending",VenueOrderState.CANCELLED:"cancelled",VenueOrderState.REJECTED:"rejected",VenueOrderState.EXPIRED:"expired",VenueOrderState.UNKNOWN_PENDING_RECOVERY:"pending_recovery"}[order.state]
            self._execute("UPDATE execution.paper_order_submissions SET state=%s,record_sha256=%s WHERE submission_id=%s",(projected_state,sha256_payload({"submission":fill.submission_id,"state":projected_state,"fill":fill.fill_id}),fill.submission_id))
            if fail_at=="order":raise RuntimeError("injected order failure")
            for b in local_snapshot.balances:self._execute("INSERT INTO execution.paper_account_balance_projection (paper_run_id,currency,total,version,updated_at_utc,source_fill_id,record_sha256) VALUES (%s,%s,%s,1,%s,%s,%s) ON CONFLICT (paper_run_id,currency) DO UPDATE SET total=EXCLUDED.total,version=execution.paper_account_balance_projection.version+1,updated_at_utc=EXCLUDED.updated_at_utc,source_fill_id=EXCLUDED.source_fill_id,record_sha256=EXCLUDED.record_sha256",(fill.paper_run_id,b.currency,b.total,fill.filled_at_utc,fill.fill_id,sha256_payload({"run":fill.paper_run_id,"balance":asdict(b),"fill":fill.fill_id})))
            for p in local_snapshot.positions:self._execute("INSERT INTO execution.paper_account_position_projection (paper_run_id,series_identity_sha256,instrument_id,series_identity_jsonb,accounting_mode,quantity,average_entry_price,realized_pnl,funding,version,updated_at_utc,source_fill_id,record_sha256) VALUES (%s,%s,%s,%s::jsonb,%s,%s,%s,%s,%s,1,%s,%s,%s) ON CONFLICT (paper_run_id,series_identity_sha256) DO UPDATE SET quantity=EXCLUDED.quantity,average_entry_price=EXCLUDED.average_entry_price,realized_pnl=EXCLUDED.realized_pnl,funding=EXCLUDED.funding,version=execution.paper_account_position_projection.version+1,updated_at_utc=EXCLUDED.updated_at_utc,source_fill_id=EXCLUDED.source_fill_id,record_sha256=EXCLUDED.record_sha256",(fill.paper_run_id,p.series_identity.series_identity_sha256,p.series_identity.provider_instrument_id,_json_param(p.series_identity.as_dict()),p.accounting_mode.value,p.quantity,p.average_entry_price,p.realized_pnl,p.funding,fill.filled_at_utc,fill.fill_id,sha256_payload({"run":fill.paper_run_id,"position":asdict(p),"fill":fill.fill_id})))
            if fail_at in {"balance","position","account_projection"}:raise RuntimeError("injected account projection failure")
            r=self._fetchone("SELECT * FROM execution.paper_reservations WHERE submission_id=%s FOR UPDATE",(fill.submission_id,));srow=self._fetchone("SELECT * FROM execution.paper_order_submissions WHERE submission_id=%s",(fill.submission_id,))
            if r and r["state"]=="open":
                oq=Decimal(str(r["original_quantity"]));rq=max(Decimal(0),Decimal(str(r["remaining_quantity"]))-fill.quantity);ra=Decimal(str(r["original_amount"]))*rq/oq;state="consumed" if rq==0 else "open";self._execute("UPDATE execution.paper_reservations SET remaining_amount=%s,remaining_quantity=%s,state=%s,updated_at_utc=%s,version=version+1,record_sha256=%s WHERE reservation_id=%s",(ra,rq,state,fill.filled_at_utc,sha256_payload({"reservation":r["reservation_id"],"fill":fill.fill_id,"remaining":rq}),r["reservation_id"]));typed=self._submission_from_row(srow);self._reservation_event(r["reservation_id"],typed,"consumed" if rq==0 else "reduced",-(Decimal(str(r["remaining_amount"]))-ra),-fill.quantity,fill.filled_at_utc,fill.fill_id)
            if fail_at=="reservation":raise RuntimeError("injected reservation failure")
            self.record_snapshot(local_snapshot);self.record_snapshot(venue_snapshot)
            if fail_at=="snapshot":raise RuntimeError("injected snapshot failure")
            self.record_reconciliation(reconciliation,differences)
            if fail_at in {"reconciliation","difference"}:raise RuntimeError("injected reconciliation failure")
            self.record_lifecycle(lifecycle_event)
            if fail_at=="lifecycle":raise RuntimeError("injected lifecycle failure")
            equity=next((b.total for b in local_snapshot.balances if b.currency==fill.fee_currency),Decimal(0));realized=sum((p.realized_pnl for p in local_snapshot.positions),Decimal(0));terminal=order.state in {VenueOrderState.FILLED,VenueOrderState.CANCELLED,VenueOrderState.REJECTED,VenueOrderState.EXPIRED}
            self._execute("UPDATE execution.paper_run_risk_state SET version=version+1,current_equity=%s,high_watermark_equity=GREATEST(high_watermark_equity,%s),daily_realized_pnl=%s,latest_account_snapshot_at_utc=%s,latest_reconciliation_at_utc=%s,latest_reconciliation_status=%s,open_order_count=GREATEST(open_order_count-%s,0),lifecycle_sequence=GREATEST(lifecycle_sequence,%s),updated_at_utc=%s,record_sha256=%s WHERE paper_run_id=%s",(equity,equity,realized,local_snapshot.venue_as_of_utc,reconciliation.reconciled_at_utc,reconciliation.status.value,1 if terminal else 0,lifecycle_event.sequence,fill.filled_at_utc,sha256_payload({"run":fill.paper_run_id,"fill":fill.fill_id,"reconciliation":reconciliation.reconciliation_id}),fill.paper_run_id));return True
    def persist_reconciliation_bundle(self,*,local_snapshot,venue_snapshot,reconciliation,differences,kill_switch=None,recovery=None,fail_at=None):
        with self.transaction():
            self._lock(reconciliation.paper_run_id);self.record_snapshot(local_snapshot);self.record_snapshot(venue_snapshot)
            if fail_at=="snapshot":raise RuntimeError("injected snapshot failure")
            self.record_reconciliation(reconciliation,differences)
            if fail_at in {"reconciliation","difference"}:raise RuntimeError("injected reconciliation failure")
            if recovery is not None:self.record_recovery(recovery)
            if kill_switch is not None:self.record_kill_switch(kill_switch)
            self._execute("UPDATE execution.paper_run_risk_state SET version=version+1,latest_account_snapshot_at_utc=%s,latest_reconciliation_at_utc=%s,latest_reconciliation_status=%s,updated_at_utc=%s,record_sha256=%s WHERE paper_run_id=%s",(local_snapshot.venue_as_of_utc,reconciliation.reconciled_at_utc,reconciliation.status.value,reconciliation.reconciled_at_utc,sha256_payload({"run":reconciliation.paper_run_id,"reconciliation":reconciliation.reconciliation_id}),reconciliation.paper_run_id))
    def _identity(self,value):
        x=self._map(value);return SeriesIdentity(str(x["provider_name"]),str(x["exchange"]),str(x["provider_instrument_id"]),str(x["canonical_symbol"]),InstrumentType(str(x["instrument_type"])),str(x["timeframe"]),None if x.get("settlement_asset") is None else str(x["settlement_asset"]))
    def _submission_from_row(self,row):
        risk=self._fetchone("SELECT evidence_jsonb FROM execution.paper_runtime_risk_decisions WHERE submission_id=%s",(row["submission_id"],))
        if not risk:raise ValueError("submission lacks runtime identity evidence")
        identity=self._identity(self._map(risk["evidence_jsonb"])["series_identity"]);return PaperOrderSubmission(row["paper_run_id"],row["manifest_id"],row["approval_id"],row["order_intent_id"],str(row["client_order_id"]),str(row["idempotency_key"]),identity,row["side"],row["order_type"],row["time_in_force"],row["accounting_mode"],Decimal(str(row["quantity"])),Decimal(str(row["reference_price"])),Decimal(str(row["submitted_notional"])),row["submitted_at_utc"],str(row["economics_sha256"]),state=row["state"],limit_price=None if row["limit_price"] is None else Decimal(str(row["limit_price"])),stop_price=None if row["stop_price"] is None else Decimal(str(row["stop_price"])),submission_id=row["submission_id"])
    def load_state_bundle(self,run):
        return {"run":self._fetchone("SELECT * FROM execution.paper_runs WHERE paper_run_id=%s",(run,)),"configuration":self._fetchone("SELECT c.* FROM execution.paper_configuration_snapshots c JOIN execution.paper_runs r ON r.configuration_sha256=c.configuration_sha256 WHERE r.paper_run_id=%s",(run,)),"preflight":self._fetchone("SELECT * FROM execution.paper_preflight_reports WHERE paper_run_id=%s ORDER BY evaluated_at_utc DESC LIMIT 1",(run,)),"approval":self._fetchone("SELECT * FROM execution.paper_approvals WHERE paper_run_id=%s",(run,)),"approval_events":self._fetchall("SELECT * FROM execution.paper_approval_state_events WHERE paper_run_id=%s ORDER BY occurred_at_utc,approval_event_id",(run,)),"manifest":self.get_manifest(run),"kill_switch":self.get_kill_switch(run),"submissions":self._fetchall("SELECT * FROM execution.paper_order_submissions WHERE paper_run_id=%s ORDER BY submitted_at_utc,submission_id",(run,)),"orders":self._fetchall("SELECT DISTINCT ON (client_order_id) * FROM execution.paper_orders WHERE paper_run_id=%s ORDER BY client_order_id,venue_sequence DESC,paper_order_record_id DESC",(run,)),"fills":self._fetchall("SELECT * FROM execution.paper_fills WHERE paper_run_id=%s ORDER BY filled_at_utc,venue_sequence,fill_id",(run,)),"balances":self._fetchall("SELECT * FROM execution.paper_account_balance_projection WHERE paper_run_id=%s ORDER BY currency",(run,)),"positions":self._fetchall("SELECT * FROM execution.paper_account_position_projection WHERE paper_run_id=%s ORDER BY series_identity_sha256",(run,)),"reservations":self._fetchall("SELECT * FROM execution.paper_reservations WHERE paper_run_id=%s ORDER BY client_order_id",(run,)),"risk_state":self._fetchone("SELECT * FROM execution.paper_run_risk_state WHERE paper_run_id=%s",(run,)),"dispatches":self._fetchall("SELECT * FROM execution.paper_dispatch_outbox WHERE paper_run_id=%s ORDER BY eligible_at_utc,dispatch_id",(run,)),"cancellations":self._fetchall("SELECT * FROM execution.paper_cancel_outbox WHERE paper_run_id=%s ORDER BY requested_at_utc,cancel_id",(run,)),"lifecycle":self._fetchall("SELECT * FROM execution.paper_lifecycle_events WHERE paper_run_id=%s ORDER BY deterministic_sequence",(run,)),"latest_reconciliation":self._fetchone("SELECT * FROM execution.paper_reconciliations WHERE paper_run_id=%s ORDER BY reconciled_at_utc DESC,reconciliation_id DESC LIMIT 1",(run,)),"recovery":self._fetchall("SELECT * FROM execution.paper_recovery_records WHERE paper_run_id=%s ORDER BY started_at_utc,recovery_id",(run,))}
    def hydrate_accounting(self,run):
        state=self.load_state_bundle(run)
        if not state["run"] or not state["configuration"]:raise ValueError("run is not reconstructable")
        a=PaperAccounting(paper_run_id=run,account_reference=str(state["run"]["account_reference"]),balances={str(x["currency"]):Decimal(str(x["total"])) for x in state["balances"]});a.positions={str(x["series_identity_sha256"]):VenuePosition(self._identity(x["series_identity_jsonb"]),x["accounting_mode"],Decimal(str(x["quantity"])),None if x["average_entry_price"] is None else Decimal(str(x["average_entry_price"])),Decimal(str(x["realized_pnl"])),Decimal(str(x["funding"]))) for x in state["positions"]};a.reservations={str(x["client_order_id"]):Reservation(str(x["currency"]),Decimal(str(x["remaining_amount"])),Decimal(str(x["original_quantity"])),Decimal(str(x["remaining_quantity"]))) for x in state["reservations"] if x["state"]=="open"};a.applied_fill_ids={x["fill_id"] for x in state["fills"]};a.total_fees=sum((Decimal(str(x["fee_amount"])) for x in state["fills"]),Decimal(0));return a
    def typed_submissions(self,run):return tuple(self._submission_from_row(x) for x in self._fetchall("SELECT * FROM execution.paper_order_submissions WHERE paper_run_id=%s ORDER BY submitted_at_utc,submission_id",(run,)))
    def typed_orders(self,run):
        subs={s.client_order_id:s for s in self.typed_submissions(run)};rows=self._fetchall("SELECT DISTINCT ON (client_order_id) * FROM execution.paper_orders WHERE paper_run_id=%s ORDER BY client_order_id,venue_sequence DESC,paper_order_record_id DESC",(run,));return tuple(VenueOrder(x["paper_run_id"],x["submission_id"],str(x["client_order_id"]),str(x["venue_order_id"]),subs[str(x["client_order_id"])].series_identity,subs[str(x["client_order_id"])].side,subs[str(x["client_order_id"])].order_type,subs[str(x["client_order_id"])].time_in_force,subs[str(x["client_order_id"])].accounting_mode,Decimal(str(x["original_quantity"])),Decimal(str(x["cumulative_filled_quantity"])),None if x["average_fill_price"] is None else Decimal(str(x["average_fill_price"])),x["state"],x["created_at_utc"],x["updated_at_utc"],int(x["venue_sequence"]),str(x["economics_sha256"]),subs[str(x["client_order_id"])].limit_price,subs[str(x["client_order_id"])].stop_price,x.get("operational_request_id"),x.get("reject_reason")) for x in rows)
    def typed_fills(self,run):
        subs={s.client_order_id:s for s in self.typed_submissions(run)};rows=self._fetchall("SELECT * FROM execution.paper_fills WHERE paper_run_id=%s ORDER BY filled_at_utc,venue_sequence,fill_id",(run,));return tuple(VenueFill(x["paper_run_id"],x["submission_id"],str(x["client_order_id"]),str(x["venue_order_id"]),str(x["venue_fill_id"]),subs[str(x["client_order_id"])].series_identity,x["side"],x["accounting_mode"],Decimal(str(x["quantity"])),Decimal(str(x["price"])),Decimal(str(x["fee_amount"])),str(x["fee_currency"]),x["filled_at_utc"],int(x["venue_sequence"]),x["environment"],fill_id=x["fill_id"]) for x in rows)
    def list_unresolved_dispatches(self,run):return self._fetchall("SELECT d.*,s.state submission_state FROM execution.paper_dispatch_outbox d JOIN execution.paper_order_submissions s USING (submission_id) WHERE d.paper_run_id=%s AND (d.state IN ('dispatch_claimed','unknown') OR s.state IN ('submission_unknown','pending_recovery','cancel_unknown')) ORDER BY d.updated_at_utc,d.dispatch_id",(run,))
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
                if old["paper_run_id"]!=value.paper_run_id or old["submission_id"]!=value.submission_id or old["action"]!=value.action or old["status"] not in ("started","paused") or value.status.value not in ("recovered","killed","failed"):raise Phase7ConflictError("recovery identity conflict")
                self._execute("UPDATE execution.paper_recovery_records SET completed_at_utc=%s,status=%s,explanation=%s,parent_ids=%s,record_sha256=%s WHERE recovery_id=%s",(value.completed_at_utc,value.status.value,value.explanation,list(value.parent_ids),value.record_sha256,value.recovery_id))
            if fail_at=="recovery":raise RuntimeError("injected recovery failure")
