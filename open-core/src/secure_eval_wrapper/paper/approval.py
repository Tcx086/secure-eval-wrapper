"""Explicit short-lived one-run paper approvals."""
from dataclasses import replace
from datetime import datetime,timedelta
from decimal import Decimal
from .configuration import PaperRunConfiguration
from .enums import ApprovalState,PreflightStatus
from .models import CredentialReference,PaperAccountSnapshot,PaperApproval,PaperPreflightReport

class ApprovalError(ValueError):pass
class ApprovalController:
    def __init__(self):self._consumed=set()
    def create(self,*,report:PaperPreflightReport,configuration:PaperRunConfiguration,snapshot:PaperAccountSnapshot,credential_reference:CredentialReference,created_at_utc:datetime,ttl_seconds:int,actor:str,nonce:str,maximum_total_notional:Decimal):
        if report.status is not PreflightStatus.PASSED:raise ApprovalError("failed preflight cannot be approved")
        if report.configuration_sha256!=configuration.config_sha256 or report.account_snapshot_sha256!=snapshot.record_sha256 or report.credential_reference_sha256!=credential_reference.reference_sha256:raise ApprovalError("approval inputs do not match preflight bindings")
        if ttl_seconds<=0:raise ApprovalError("approval ttl must be positive")
        if maximum_total_notional>configuration.maximum_daily_submitted_notional:raise ApprovalError("approval notional exceeds configured daily limit")
        return PaperApproval(report.paper_run_id,report.report_id,configuration.config_sha256,snapshot.record_sha256,credential_reference.reference_sha256,configuration.provider,configuration.environment,configuration.allowed_instruments,maximum_total_notional,created_at_utc,created_at_utc+timedelta(seconds=ttl_seconds),actor,nonce)
    def validate(self,approval,*,paper_run_id,report,configuration,snapshot,credential_reference,at_utc,requested_total_notional=Decimal(0),consume=False):
        if approval.approval_id in self._consumed or approval.state is not ApprovalState.VALID:raise ApprovalError("approval has already been consumed or revoked")
        if at_utc>=approval.expires_at_utc:raise ApprovalError("approval expired")
        if approval.paper_run_id!=paper_run_id or approval.preflight_report_id!=report.report_id:raise ApprovalError("approval belongs to another run or report")
        if approval.configuration_sha256!=configuration.config_sha256:raise ApprovalError("configuration changed after approval")
        if approval.account_snapshot_sha256!=snapshot.record_sha256:raise ApprovalError("account snapshot changed after approval")
        if approval.credential_reference_sha256!=credential_reference.reference_sha256:raise ApprovalError("credential reference changed after approval")
        if approval.provider!=configuration.provider or approval.environment!=configuration.environment:raise ApprovalError("provider/environment changed after approval")
        if requested_total_notional>approval.maximum_approved_total_notional:raise ApprovalError("requested notional exceeds approval")
        if consume:self._consumed.add(approval.approval_id); return replace(approval,state=ApprovalState.CONSUMED)
        return approval
