"""Persistable paper kill switch controller; never auto-flattens positions."""
from dataclasses import replace
from secure_eval_wrapper.data_collection.hashing import sha256_payload
from .enums import KillSwitchReason,KillSwitchState,PreflightStatus,ReconciliationStatus
from .models import PaperKillSwitch

class PaperKillSwitchController:
    def __init__(self,initial:PaperKillSwitch,*,persist=None):self.current=initial; self.persist=persist; self.events=[]
    @property
    def accepts_new_orders(self):return self.current.state in (KillSwitchState.ARMED,KillSwitchState.RESET)
    def _save(self,value,event):
        self.current=value; self.events.append(event)
        if self.persist:self.persist(value,event)
        return value
    def trigger(self,reason,*,at_utc,evidence,incident_id=None):
        reason=KillSwitchReason(reason)
        if self.current.state in (KillSwitchState.TRIGGERED,KillSwitchState.CANCELLING,KillSwitchState.KILLED):return self.current
        digest=sha256_payload(evidence); value=PaperKillSwitch(self.current.paper_run_id,KillSwitchState.TRIGGERED,reason,at_utc,at_utc,digest,incident_id,self.current.kill_switch_id)
        return self._save(value,{"state":"triggered","reason":reason.value,"at_utc":at_utc,"evidence_sha256":digest})
    def cancel_open_orders(self,broker,*,at_utc,durable_cancel_intent):
        if self.current.state is not KillSwitchState.TRIGGERED:raise ValueError("kill switch must be triggered before cancellation")
        value=replace(self.current,state=KillSwitchState.CANCELLING,updated_at_utc=at_utc); self._save(value,{"state":"cancelling","at_utc":at_utc})
        outcomes=[]
        for order in broker.list_open_orders():
            durable_cancel_intent(order,at_utc)
            try:outcomes.append(broker.cancel_paper_order(order.client_order_id,at_utc=at_utc,reason="kill_switch"))
            except Exception as exc:outcomes.append({"client_order_id":order.client_order_id,"status":"unknown","reason":type(exc).__name__})
        return tuple(outcomes)
    def finalize(self,*,at_utc,terminal_handling_documented):
        if not terminal_handling_documented:raise ValueError("kill switch cannot assume cancellation succeeded")
        value=replace(self.current,state=KillSwitchState.KILLED,updated_at_utc=at_utc); return self._save(value,{"state":"killed","at_utc":at_utc,"positions_unchanged":True})
    def request_reset(self,*,at_utc):
        if self.current.state is not KillSwitchState.KILLED:raise ValueError("only killed switch may request reset")
        return self._save(replace(self.current,state=KillSwitchState.RESET_PENDING,updated_at_utc=at_utc),{"state":"reset_pending","at_utc":at_utc})
    def reset(self,*,at_utc,new_preflight,new_approval,fresh_snapshot,reconciliation):
        if self.current.state is not KillSwitchState.RESET_PENDING:raise ValueError("reset must be pending")
        if new_preflight.status is not PreflightStatus.PASSED or fresh_snapshot.status.value!="fresh" or reconciliation.status in (ReconciliationStatus.BLOCKED,ReconciliationStatus.UNKNOWN):raise ValueError("kill switch reset evidence is incomplete")
        if new_approval.preflight_report_id!=new_preflight.report_id:raise ValueError("reset approval does not bind new preflight")
        return self._save(PaperKillSwitch(self.current.paper_run_id,KillSwitchState.RESET,None,at_utc,kill_switch_id=self.current.kill_switch_id),{"state":"reset","at_utc":at_utc,"preflight_report_id":str(new_preflight.report_id),"approval_id":str(new_approval.approval_id)})
