"""Restart and unknown-submission recovery using original client order identity."""
from .enums import KillSwitchReason,RecoveryStatus,VenueOrderState
from .models import PaperRecoveryRecord

class PaperRecoveryEngine:
    def recover_unknown(self,*,broker,client_order_id,started_at_utc,at_utc,maximum_unknown_seconds,kill_switch):
        submission=next((s for s in broker.submissions if s.client_order_id==client_order_id),None)
        if submission is None:raise ValueError("unknown submission has no local durable record")
        order=broker.query_order(client_order_id); broker.sync_fills()
        if order is not None and order.state is not VenueOrderState.UNKNOWN_PENDING_RECOVERY:
            return PaperRecoveryRecord(broker.manifest.paper_run_id,submission.submission_id,started_at_utc,at_utc,RecoveryStatus.RECOVERED,"query_original_client_order_id","venue evidence recovered without resubmission",(submission.submission_id,))
        if (at_utc-started_at_utc).total_seconds()>=maximum_unknown_seconds:
            kill_switch.trigger(KillSwitchReason.UNKNOWN_ORDER,at_utc=at_utc,evidence={"client_order_id":client_order_id,"submission_id":str(submission.submission_id)})
            return PaperRecoveryRecord(broker.manifest.paper_run_id,submission.submission_id,started_at_utc,at_utc,RecoveryStatus.KILLED,"query_original_client_order_id","unknown submission exceeded configured duration",(submission.submission_id,))
        return PaperRecoveryRecord(broker.manifest.paper_run_id,submission.submission_id,started_at_utc,at_utc,RecoveryStatus.PAUSED,"query_original_client_order_id","venue outcome remains unknown; no resubmission",(submission.submission_id,))
    def recover_after_restart(self,*,repository,broker,reconciliation_engine,at_utc):
        run=repository.get_active_run(broker.manifest.paper_run_id)
        if run is None:raise ValueError("no active PostgreSQL paper run to recover")
        persisted_kill=repository.get_kill_switch(broker.manifest.paper_run_id)
        if persisted_kill and persisted_kill.get("state") in ("triggered","cancelling","killed","reset_pending"):return {"status":"killed_or_paused","resubmitted":False}
        for row in repository.list_unresolved_submissions(broker.manifest.paper_run_id):broker.query_order(str(row["client_order_id"]))
        broker.sync_fills(); reconciliation,differences=broker.reconcile(reconciliation_engine)
        return {"status":"recovered" if not differences else "paused","resubmitted":False,"reconciliation":reconciliation,"differences":differences}
