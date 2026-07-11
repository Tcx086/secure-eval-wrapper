"""Paper-specific Phase 6 monitoring evidence; it never mutates venue state."""
from dataclasses import dataclass
from decimal import Decimal
from secure_eval_wrapper.monitoring.events import make_result
from secure_eval_wrapper.monitoring.models import HealthStatus,MonitoringCategory,Severity

@dataclass(frozen=True)
class PaperMonitoringInput:
    endpoint_verified:bool|None=None; authenticated_transport_available:bool|None=None; credential_verification_age_seconds:Decimal|None=None; consecutive_transport_failures:Decimal|None=None; timeout_rate:Decimal|None=None
    unacknowledged_order_age_seconds:Decimal|None=None; unknown_submission_age_seconds:Decimal|None=None; cancel_pending_age_seconds:Decimal|None=None; rejection_rate:Decimal|None=None; duplicate_client_conflicts:Decimal|None=None; order_rate_utilization:Decimal|None=None; daily_notional_utilization:Decimal|None=None
    duplicate_fill_count:Decimal|None=None; fill_without_order_count:Decimal|None=None; fill_application_lag_seconds:Decimal|None=None; fee_mismatch_count:Decimal|None=None; partial_fill_reconciled:bool|None=None
    balance_age_seconds:Decimal|None=None; position_age_seconds:Decimal|None=None; reconciliation_ok:bool|None=None; unapproved_order_count:Decimal|None=None; unapproved_position_count:Decimal|None=None; drawdown:Decimal|None=None; daily_realized_loss:Decimal|None=None; account_mode_ok:bool|None=None; clock_skew_seconds:Decimal|None=None
    kill_switch_state:str|None=None; cancellation_pending_count:Decimal|None=None; unresolved_position_count:Decimal|None=None; reset_eligible:bool|None=None

def evaluate_paper_health(context,value,configuration):
    if value is None:return ()
    rows=[]
    def boolean(name,category,component,observed):
        status=HealthStatus.UNKNOWN if observed is None else HealthStatus.HEALTHY if observed else HealthStatus.UNHEALTHY; rows.append(make_result(context,category=category,component=component,check_name=name,health_status=status,reason_code=name if status is not HealthStatus.HEALTHY else "healthy",explanation=f"paper {name} evidence is {status.value}",observed=observed,severity=Severity.CRITICAL if status is HealthStatus.UNHEALTHY else None))
    boolean("paper_endpoint_verified",MonitoringCategory.SYSTEM,"paper_transport",value.endpoint_verified); boolean("paper_authenticated_transport",MonitoringCategory.SYSTEM,"paper_transport",value.authenticated_transport_available); boolean("paper_reconciliation",MonitoringCategory.EXECUTION,"paper_reconciliation",value.reconciliation_ok); boolean("paper_partial_fill_reconciliation",MonitoringCategory.EXECUTION,"paper_fills",value.partial_fill_reconciled); boolean("paper_account_mode",MonitoringCategory.RISK,"paper_account",value.account_mode_ok); boolean("paper_reset_eligibility",MonitoringCategory.RISK,"paper_kill_switch",value.reset_eligible)
    numeric=(("paper_consecutive_transport_failures",MonitoringCategory.SYSTEM,"paper_transport",value.consecutive_transport_failures,Decimal(3)),("paper_unknown_submission_age",MonitoringCategory.EXECUTION,"paper_orders",value.unknown_submission_age_seconds,Decimal(30)),("paper_unacknowledged_order_age",MonitoringCategory.EXECUTION,"paper_orders",value.unacknowledged_order_age_seconds,Decimal(15)),("paper_cancel_pending_age",MonitoringCategory.EXECUTION,"paper_orders",value.cancel_pending_age_seconds,Decimal(15)),("paper_duplicate_fills",MonitoringCategory.EXECUTION,"paper_fills",value.duplicate_fill_count,Decimal(0)),("paper_fill_without_order",MonitoringCategory.EXECUTION,"paper_fills",value.fill_without_order_count,Decimal(0)),("paper_fee_mismatch",MonitoringCategory.EXECUTION,"paper_fills",value.fee_mismatch_count,Decimal(0)),("paper_unapproved_orders",MonitoringCategory.RISK,"paper_account",value.unapproved_order_count,Decimal(0)),("paper_unapproved_positions",MonitoringCategory.RISK,"paper_account",value.unapproved_position_count,Decimal(0)))
    for name,cat,component,observed,limit in numeric:
        status=HealthStatus.UNKNOWN if observed is None else HealthStatus.HEALTHY if observed<=limit else HealthStatus.UNHEALTHY; rows.append(make_result(context,category=cat,component=component,check_name=name,health_status=status,reason_code=name if status is not HealthStatus.HEALTHY else "healthy",explanation=f"paper {name} is {status.value}",observed=observed,threshold=limit,severity=Severity.CRITICAL if status is HealthStatus.UNHEALTHY else None))
    return tuple(rows)
