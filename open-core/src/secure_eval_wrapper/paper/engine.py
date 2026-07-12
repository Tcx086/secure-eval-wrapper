"""Paper run lifecycle and controller orchestration."""
from dataclasses import replace
from .approval import ApprovalController
from .enums import KillSwitchReason,KillSwitchState,PaperRunState,PreflightStatus,ReconciliationStatus
from .manifests import validate_manifest
from .models import PaperKillSwitch,PaperLifecycleEvent,PaperRun

class PaperTradingEngine:
    def __init__(self,*,configuration,broker,reconciliation_engine,kill_switch,repository=None,monitor=None,clock):
        self.configuration=configuration; self.broker=broker; self.reconciliation_engine=reconciliation_engine; self.kill_switch=kill_switch; self.repository=repository; self.monitor=monitor; self.clock=clock; self.run=None; self.events=[]; self._sequence=0
    def _event(self,kind,details,parents=()):
        self._sequence+=1; row=PaperLifecycleEvent(self.broker.manifest.paper_run_id,kind,self.clock(),self._sequence,details,tuple(parents)); self.events.append(row); return row
    def start(self,*,report,approval,snapshot,credential_reference,approval_controller:ApprovalController):
        if report.status is not PreflightStatus.PASSED:raise PermissionError("paper run requires passed preflight")
        validate_manifest(self.broker.manifest,configuration=self.configuration,report=report,approval=approval,snapshot=snapshot,credential_reference=credential_reference)
        now=self.clock(); self.run=PaperRun(report.paper_run_id,self.broker.manifest.manifest_id,PaperRunState.RUNNING,now,now); event=self._event("run_started",{"live_mode":False,"provider":self.configuration.provider.value,"environment":self.configuration.environment.value},(report.report_id,approval.approval_id,self.broker.manifest.manifest_id))
        if self.configuration.persistence_required:
            if self.repository is None:raise RuntimeError("persistence-required paper run has no PostgreSQL repository")
            if hasattr(self.repository,"prepare_submission"):self.broker.repository=self.repository
            self.repository.persist_start_run(run=self.run,configuration=self.configuration,credential_reference=credential_reference,snapshot=snapshot,report=report,approval=approval,manifest=self.broker.manifest,kill_switch=self.kill_switch.current)
            self.repository.record_lifecycle(event)
            if hasattr(self.repository,"prepare_submission"):self.repository._execute("UPDATE execution.paper_run_risk_state SET lifecycle_sequence=GREATEST(lifecycle_sequence,%s),updated_at_utc=%s,record_sha256=%s WHERE paper_run_id=%s",(event.sequence,event.occurred_at_utc,event.record_sha256,event.paper_run_id))
        else:approval_controller.validate(approval,paper_run_id=report.paper_run_id,report=report,configuration=self.configuration,snapshot=snapshot,credential_reference=credential_reference,at_utc=self.clock(),consume=True)
        return self.run
    def submit(self,intent,risk_decision):
        if self.run is None or self.run.state is not PaperRunState.RUNNING:raise RuntimeError("paper run is not active")
        result=self.broker.submit_order_intent(intent,risk_decision); self._event("submission_processed",{"order_updates":len(result.order_updates),"fills":len(result.fills)},(intent.order_intent_id,risk_decision.risk_decision_id)); return result
    def poll(self):
        fills=self.broker.sync_fills()
        for fill in fills:self._event("confirmed_fill_applied",{"venue_fill_id":fill.venue_fill_id,"paper_environment":fill.environment.value},(fill.fill_id,))
        return fills
    def reconcile(self):
        reconciliation,differences=self.broker.reconcile(self.reconciliation_engine); self._event("reconciled",{"status":reconciliation.status.value,"difference_count":len(differences)},(reconciliation.reconciliation_id,))
        if reconciliation.status in (ReconciliationStatus.BLOCKED,ReconciliationStatus.UNKNOWN):self.kill_switch.trigger(KillSwitchReason.RECONCILIATION,at_utc=self.clock(),evidence={"reconciliation_id":str(reconciliation.reconciliation_id),"status":reconciliation.status.value})
        if self.monitor:self.monitor(reconciliation,differences,self.kill_switch.current)
        if self.repository is not None and hasattr(self.repository,"persist_reconciliation_bundle"):
            venue=self.broker.fetch_account_snapshot();local=self.broker.accounting.snapshot(at_utc=self.clock(),venue_sequence=venue.venue_sequence);self.repository.persist_reconciliation_bundle(local_snapshot=local,venue_snapshot=venue,reconciliation=reconciliation,differences=differences,kill_switch=self.kill_switch.current)
        return reconciliation,differences
    def complete(self,*,summary):
        if self.run is None:raise RuntimeError("paper run was not started")
        now=self.clock(); state=PaperRunState.KILLED if self.kill_switch.current.state in (KillSwitchState.TRIGGERED,KillSwitchState.CANCELLING,KillSwitchState.KILLED) else PaperRunState.COMPLETED; self.run=replace(self.run,state=state,updated_at_utc=now,ended_at_utc=now,summary=summary); self._event("run_completed",{"state":state.value});
        if self.repository is not None:self.repository.update_run(self.run)
        return self.run
