"""Frozen public-safe paper-trading records."""
from __future__ import annotations
import re
from dataclasses import dataclass,field,fields,is_dataclass
from datetime import datetime
from decimal import Decimal
from types import MappingProxyType
from typing import Mapping
from uuid import NAMESPACE_URL,UUID,uuid5
from secure_eval_wrapper.alpha.identity import SeriesIdentity
from secure_eval_wrapper.data_collection.hashing import sha256_payload
from secure_eval_wrapper.data_collection.time_utils import require_utc_datetime
from secure_eval_wrapper.execution.models import AccountingMode,OrderSide,OrderType,TimeInForce
from .enums import *
_SHA=re.compile(r"^[0-9a-f]{64}$")
def _text(v,n):
    if not isinstance(v,str) or not v.strip(): raise ValueError(f"{n} must be non-empty")
    return v.strip()
def _utc(v,n): return require_utc_datetime(v,field_name=n)
def _hash(v,n):
    if not isinstance(v,str) or not _SHA.fullmatch(v): raise ValueError(f"{n} must be a lowercase SHA-256 digest")
    return v
def _dec(v,n,positive=False,nonnegative=False):
    if not isinstance(v,Decimal) or not v.is_finite() or (positive and v<=0) or (nonnegative and v<0): raise ValueError(f"{n} must be a valid finite Decimal")
    return v
def _map(v): return MappingProxyType(dict(v))
def _stable(v):
    if is_dataclass(v): return {f.name:_stable(getattr(v,f.name)) for f in fields(v)}
    if isinstance(v,Mapping): return {str(k):_stable(x) for k,x in v.items()}
    if isinstance(v,(list,tuple,set,frozenset)): return [_stable(x) for x in v]
    if hasattr(v,"as_dict") and callable(v.as_dict): return _stable(v.as_dict())
    return v
def _paper_hash(v): return sha256_payload(_stable(v))
def deterministic_paper_uuid(kind,payload): return uuid5(NAMESPACE_URL,f"secure-eval-wrapper:paper:{kind}:{_paper_hash(payload)}")

@dataclass(frozen=True)
class CredentialReference:
    provider:PaperProvider; alias:str; source_type:CredentialSourceType; public_key_fingerprint:str; loaded:bool=False; verified_at_utc:datetime|None=None; permissions_summary:tuple[str,...]=()
    def __post_init__(self):
        object.__setattr__(self,"provider",PaperProvider(self.provider)); object.__setattr__(self,"source_type",CredentialSourceType(self.source_type)); object.__setattr__(self,"alias",_text(self.alias,"alias"))
        if not re.fullmatch(r"[0-9a-f]{12,32}",self.public_key_fingerprint): raise ValueError("fingerprint must derive from public key ID only")
        if self.verified_at_utc is not None:_utc(self.verified_at_utc,"verified_at_utc")
        object.__setattr__(self,"permissions_summary",tuple(sorted(set(self.permissions_summary))))
    @property
    def reference_sha256(self): return _paper_hash({"provider":self.provider,"alias":self.alias,"source_type":self.source_type,"public_key_fingerprint":self.public_key_fingerprint})

@dataclass(frozen=True)
class VenueBalance:
    currency:str; total:Decimal; available:Decimal; reserved:Decimal=Decimal(0)
    def __post_init__(self):
        object.__setattr__(self,"currency",_text(self.currency,"currency").upper())
        for n in ("total","available","reserved"):_dec(getattr(self,n),n,nonnegative=True)
        if self.available+self.reserved>self.total: raise ValueError("available plus reserved exceeds total")

@dataclass(frozen=True)
class VenuePosition:
    series_identity:SeriesIdentity; accounting_mode:AccountingMode; quantity:Decimal; average_entry_price:Decimal|None; realized_pnl:Decimal=Decimal(0); funding:Decimal=Decimal(0)
    def __post_init__(self):
        object.__setattr__(self,"accounting_mode",AccountingMode(self.accounting_mode)); _dec(self.quantity,"quantity"); _dec(self.realized_pnl,"realized_pnl"); _dec(self.funding,"funding")
        if self.average_entry_price is not None:_dec(self.average_entry_price,"average_entry_price",positive=True)
        if self.quantity==0 and self.average_entry_price is not None: raise ValueError("flat position must not have average entry")
        if self.accounting_mode is AccountingMode.SPOT and self.quantity<0: raise ValueError("Spot position cannot be negative")

@dataclass(frozen=True)
class PaperAccountSnapshot:
    paper_run_id:UUID; account_reference:str; status:AccountSnapshotStatus; fetched_at_utc:datetime; venue_as_of_utc:datetime; account_mode:str; balances:tuple[VenueBalance,...]; positions:tuple[VenuePosition,...]; open_client_order_ids:tuple[str,...]; venue_sequence:int|None; source:str; snapshot_id:UUID|None=None
    def __post_init__(self):
        object.__setattr__(self,"status",AccountSnapshotStatus(self.status)); _utc(self.fetched_at_utc,"fetched_at_utc"); _utc(self.venue_as_of_utc,"venue_as_of_utc")
        if self.venue_as_of_utc>self.fetched_at_utc: raise ValueError("venue timestamp cannot be in the future")
        object.__setattr__(self,"account_reference",_text(self.account_reference,"account_reference")); object.__setattr__(self,"account_mode",_text(self.account_mode,"account_mode")); object.__setattr__(self,"source",_text(self.source,"source"))
        if self.venue_sequence is not None and self.venue_sequence<0: raise ValueError("venue_sequence must be non-negative")
        p={n:getattr(self,n) for n in self.__dataclass_fields__ if n!="snapshot_id"}; expected=deterministic_paper_uuid("snapshot",p)
        if self.snapshot_id is not None and self.snapshot_id!=expected: raise ValueError("snapshot_id mismatch")
        object.__setattr__(self,"snapshot_id",expected)
    @property
    def record_sha256(self): return _paper_hash({n:getattr(self,n) for n in self.__dataclass_fields__ if n!="snapshot_id"})

@dataclass(frozen=True)
class PaperPreflightCheck:
    check_name:str; status:PreflightStatus; checked_at_utc:datetime; reason_code:str; explanation:str; required:bool; evidence_sha256:str; check_id:UUID|None=None
    def __post_init__(self):
        object.__setattr__(self,"check_name",_text(self.check_name,"check_name")); object.__setattr__(self,"status",PreflightStatus(self.status)); _utc(self.checked_at_utc,"checked_at_utc"); object.__setattr__(self,"reason_code",_text(self.reason_code,"reason_code")); object.__setattr__(self,"explanation",_text(self.explanation,"explanation")); _hash(self.evidence_sha256,"evidence_sha256")
        expected=deterministic_paper_uuid("preflight-check",{"name":self.check_name,"at":self.checked_at_utc,"status":self.status,"evidence":self.evidence_sha256})
        if self.check_id is not None and self.check_id!=expected: raise ValueError("check_id mismatch")
        object.__setattr__(self,"check_id",expected)
    @property
    def record_sha256(self): return _paper_hash({n:getattr(self,n) for n in self.__dataclass_fields__ if n!="check_id"})

@dataclass(frozen=True)
class PaperPreflightReport:
    paper_run_id:UUID; evaluated_at_utc:datetime; status:PreflightStatus; checks:tuple[PaperPreflightCheck,...]; blockers:tuple[str,...]; warnings:tuple[str,...]; configuration_sha256:str; account_snapshot_sha256:str; implementation_sha256:str; endpoint_catalog_sha256:str; credential_reference_sha256:str; report_id:UUID|None=None
    def __post_init__(self):
        _utc(self.evaluated_at_utc,"evaluated_at_utc"); object.__setattr__(self,"status",PreflightStatus(self.status))
        for n in ("configuration_sha256","account_snapshot_sha256","implementation_sha256","endpoint_catalog_sha256","credential_reference_sha256"):_hash(getattr(self,n),n)
        failed=any(c.required and c.status is PreflightStatus.FAILED for c in self.checks)
        if (self.status is PreflightStatus.PASSED)==failed: raise ValueError("report status does not match required checks")
        p={"run":self.paper_run_id,"at":self.evaluated_at_utc,"checks":tuple(c.check_id for c in self.checks),"config":self.configuration_sha256,"snapshot":self.account_snapshot_sha256,"implementation":self.implementation_sha256,"endpoint":self.endpoint_catalog_sha256,"credential":self.credential_reference_sha256}; expected=deterministic_paper_uuid("preflight-report",p)
        if self.report_id is not None and self.report_id!=expected: raise ValueError("report_id mismatch")
        object.__setattr__(self,"report_id",expected)
    @property
    def record_sha256(self): return _paper_hash({"report_id":self.report_id,"status":self.status,"blockers":self.blockers,"warnings":self.warnings})

@dataclass(frozen=True)
class PaperApproval:
    paper_run_id:UUID; preflight_report_id:UUID; configuration_sha256:str; account_snapshot_sha256:str; credential_reference_sha256:str; provider:PaperProvider; environment:PaperEnvironment; allowed_instruments:tuple[str,...]; maximum_approved_total_notional:Decimal; created_at_utc:datetime; expires_at_utc:datetime; approving_actor:str; nonce:str; state:ApprovalState=ApprovalState.VALID; approval_id:UUID|None=None
    def __post_init__(self):
        object.__setattr__(self,"provider",PaperProvider(self.provider)); object.__setattr__(self,"environment",PaperEnvironment(self.environment)); object.__setattr__(self,"state",ApprovalState(self.state))
        if self.environment is PaperEnvironment.LIVE: raise ValueError("approval cannot authorize live")
        for n in ("configuration_sha256","account_snapshot_sha256","credential_reference_sha256"):_hash(getattr(self,n),n)
        _dec(self.maximum_approved_total_notional,"maximum_approved_total_notional",positive=True); _utc(self.created_at_utc,"created_at_utc"); _utc(self.expires_at_utc,"expires_at_utc")
        if self.expires_at_utc<=self.created_at_utc: raise ValueError("approval expiry must follow creation")
        object.__setattr__(self,"approving_actor",_text(self.approving_actor,"approving_actor")); object.__setattr__(self,"nonce",_text(self.nonce,"nonce")); object.__setattr__(self,"allowed_instruments",tuple(sorted({_text(v,"instrument") for v in self.allowed_instruments})))
        p={n:getattr(self,n) for n in self.__dataclass_fields__ if n not in {"approval_id","state"}}; expected=deterministic_paper_uuid("approval",p)
        if self.approval_id is not None and self.approval_id!=expected: raise ValueError("approval_id mismatch")
        object.__setattr__(self,"approval_id",expected)
    @property
    def record_sha256(self): return _paper_hash({n:getattr(self,n) for n in self.__dataclass_fields__ if n!="approval_id"})

@dataclass(frozen=True)
class PaperRunManifest:
    paper_run_id:UUID; provider:PaperProvider; environment:PaperEnvironment; account_reference:str; implementation_sha256:str; repository_commit_sha:str; configuration_sha256:str; endpoint_catalog_sha256:str; preflight_report_id:UUID; approval_id:UUID; initial_account_snapshot_id:UUID; initial_account_snapshot_sha256:str; credential_reference:CredentialReference; strategy_run_reference:str; allowed_instruments:tuple[str,...]; risk_limits:Mapping[str,object]; start_at_utc:datetime; expected_maximum_duration_seconds:int; persistence_required:bool; kill_switch_configuration:Mapping[str,object]; parent_ids:tuple[UUID,...]; manifest_id:UUID|None=None
    def __post_init__(self):
        object.__setattr__(self,"provider",PaperProvider(self.provider)); object.__setattr__(self,"environment",PaperEnvironment(self.environment))
        if self.environment is PaperEnvironment.LIVE: raise ValueError("manifest cannot authorize live")
        for n in ("implementation_sha256","configuration_sha256","endpoint_catalog_sha256","initial_account_snapshot_sha256"):_hash(getattr(self,n),n)
        object.__setattr__(self,"account_reference",_text(self.account_reference,"account_reference")); object.__setattr__(self,"repository_commit_sha",_text(self.repository_commit_sha,"repository_commit_sha")); object.__setattr__(self,"strategy_run_reference",_text(self.strategy_run_reference,"strategy_run_reference")); _utc(self.start_at_utc,"start_at_utc")
        if self.expected_maximum_duration_seconds<=0: raise ValueError("expected duration must be positive")
        object.__setattr__(self,"risk_limits",_map(self.risk_limits)); object.__setattr__(self,"kill_switch_configuration",_map(self.kill_switch_configuration))
        p={n:getattr(self,n) for n in self.__dataclass_fields__ if n!="manifest_id"}; expected=deterministic_paper_uuid("manifest",p)
        if self.manifest_id is not None and self.manifest_id!=expected: raise ValueError("manifest_id mismatch")
        object.__setattr__(self,"manifest_id",expected)
    @property
    def manifest_sha256(self): return _paper_hash({n:getattr(self,n) for n in self.__dataclass_fields__ if n!="manifest_id"})

@dataclass(frozen=True)
class PaperRun:
    paper_run_id:UUID; manifest_id:UUID; state:PaperRunState; started_at_utc:datetime; updated_at_utc:datetime; ended_at_utc:datetime|None=None; summary:Mapping[str,object]=field(default_factory=dict)
    def __post_init__(self):
        object.__setattr__(self,"state",PaperRunState(self.state)); _utc(self.started_at_utc,"started_at_utc"); _utc(self.updated_at_utc,"updated_at_utc")
        if self.ended_at_utc is not None:_utc(self.ended_at_utc,"ended_at_utc")
        object.__setattr__(self,"summary",_map(self.summary))
    @property
    def record_sha256(self): return _paper_hash({n:getattr(self,n) for n in self.__dataclass_fields__})

@dataclass(frozen=True)
class PaperOrderSubmission:
    paper_run_id:UUID; manifest_id:UUID; approval_id:UUID; order_intent_id:UUID; client_order_id:str; idempotency_key:str; series_identity:SeriesIdentity; side:OrderSide; order_type:OrderType; time_in_force:TimeInForce; accounting_mode:AccountingMode; quantity:Decimal; reference_price:Decimal; submitted_notional:Decimal; submitted_at_utc:datetime; economics_sha256:str; state:PaperOrderState=PaperOrderState.SUBMITTED; limit_price:Decimal|None=None; stop_price:Decimal|None=None; submission_id:UUID|None=None
    def __post_init__(self):
        for n,e in (("side",OrderSide),("order_type",OrderType),("time_in_force",TimeInForce),("accounting_mode",AccountingMode),("state",PaperOrderState)):object.__setattr__(self,n,e(getattr(self,n)))
        object.__setattr__(self,"client_order_id",_text(self.client_order_id,"client_order_id")); object.__setattr__(self,"idempotency_key",_text(self.idempotency_key,"idempotency_key")); _dec(self.quantity,"quantity",positive=True); _dec(self.reference_price,"reference_price",positive=True); _dec(self.submitted_notional,"submitted_notional",positive=True); _utc(self.submitted_at_utc,"submitted_at_utc"); _hash(self.economics_sha256,"economics_sha256")
        if self.limit_price is not None:_dec(self.limit_price,"limit_price",positive=True)
        if self.stop_price is not None:_dec(self.stop_price,"stop_price",positive=True)
        economics={"series_identity":self.series_identity.as_dict(),"side":self.side,"order_type":self.order_type,"time_in_force":self.time_in_force,"accounting_mode":self.accounting_mode,"quantity":self.quantity,"limit_price":self.limit_price,"stop_price":self.stop_price}
        if self.economics_sha256!=_paper_hash(economics): raise ValueError("economics_sha256 mismatch")
        expected=deterministic_paper_uuid("submission",{"run":self.paper_run_id,"manifest":self.manifest_id,"client_order_id":self.client_order_id,"economics":self.economics_sha256})
        if self.submission_id is not None and self.submission_id!=expected: raise ValueError("submission_id mismatch")
        object.__setattr__(self,"submission_id",expected)
    @property
    def record_sha256(self): return _paper_hash({n:getattr(self,n) for n in self.__dataclass_fields__ if n!="submission_id"})

@dataclass(frozen=True)
class VenueOrder:
    paper_run_id:UUID; submission_id:UUID; client_order_id:str; venue_order_id:str; series_identity:SeriesIdentity; side:OrderSide; order_type:OrderType; time_in_force:TimeInForce; accounting_mode:AccountingMode; quantity:Decimal; cumulative_filled_quantity:Decimal; average_fill_price:Decimal|None; state:VenueOrderState; created_at_utc:datetime; updated_at_utc:datetime; venue_sequence:int; economics_sha256:str; limit_price:Decimal|None=None; stop_price:Decimal|None=None; operational_request_id:str|None=None; reject_reason:str|None=None
    def __post_init__(self):
        for n,e in (("side",OrderSide),("order_type",OrderType),("time_in_force",TimeInForce),("accounting_mode",AccountingMode),("state",VenueOrderState)):object.__setattr__(self,n,e(getattr(self,n)))
        object.__setattr__(self,"client_order_id",_text(self.client_order_id,"client_order_id")); object.__setattr__(self,"venue_order_id",_text(self.venue_order_id,"venue_order_id")); _dec(self.quantity,"quantity",positive=True); _dec(self.cumulative_filled_quantity,"cumulative_filled_quantity",nonnegative=True); _hash(self.economics_sha256,"economics_sha256")
        if self.cumulative_filled_quantity>self.quantity: raise ValueError("cumulative fill exceeds quantity")
        if self.average_fill_price is not None:_dec(self.average_fill_price,"average_fill_price",positive=True)
        if self.limit_price is not None:_dec(self.limit_price,"limit_price",positive=True)
        if self.stop_price is not None:_dec(self.stop_price,"stop_price",positive=True)
        _utc(self.created_at_utc,"created_at_utc"); _utc(self.updated_at_utc,"updated_at_utc")
        if self.updated_at_utc<self.created_at_utc or self.venue_sequence<0: raise ValueError("invalid venue ordering evidence")
    @property
    def remaining_quantity(self):return self.quantity-self.cumulative_filled_quantity
    @property
    def record_sha256(self):return _paper_hash({n:getattr(self,n) for n in self.__dataclass_fields__ if n!="operational_request_id"})

@dataclass(frozen=True)
class VenueFill:
    paper_run_id:UUID; submission_id:UUID; client_order_id:str; venue_order_id:str; venue_fill_id:str; series_identity:SeriesIdentity; side:OrderSide; accounting_mode:AccountingMode; quantity:Decimal; price:Decimal; fee_amount:Decimal; fee_currency:str; filled_at_utc:datetime; venue_sequence:int; environment:PaperEnvironment; fill_id:UUID|None=None
    def __post_init__(self):
        object.__setattr__(self,"side",OrderSide(self.side)); object.__setattr__(self,"accounting_mode",AccountingMode(self.accounting_mode)); object.__setattr__(self,"environment",PaperEnvironment(self.environment))
        if self.environment not in (PaperEnvironment.PAPER_INTERNAL,PaperEnvironment.PAPER_EXCHANGE_SANDBOX): raise ValueError("paper fill requires explicit sandbox environment")
        for n in ("client_order_id","venue_order_id","venue_fill_id"):object.__setattr__(self,n,_text(getattr(self,n),n))
        _dec(self.quantity,"quantity",positive=True); _dec(self.price,"price",positive=True); _dec(self.fee_amount,"fee_amount",nonnegative=True); object.__setattr__(self,"fee_currency",_text(self.fee_currency,"fee_currency").upper()); _utc(self.filled_at_utc,"filled_at_utc")
        if self.venue_sequence<0: raise ValueError("venue_sequence must be non-negative")
        expected=deterministic_paper_uuid("venue-fill",{"run":self.paper_run_id,"venue_order_id":self.venue_order_id,"venue_fill_id":self.venue_fill_id})
        if self.fill_id is not None and self.fill_id!=expected: raise ValueError("fill_id mismatch")
        object.__setattr__(self,"fill_id",expected)
    @property
    def record_sha256(self):return _paper_hash({n:getattr(self,n) for n in self.__dataclass_fields__ if n!="fill_id"})

@dataclass(frozen=True)
class PaperReconciliation:
    paper_run_id:UUID; local_snapshot_id:UUID; venue_snapshot_id:UUID; reconciled_at_utc:datetime; status:ReconciliationStatus; local_sequence:int|None; venue_sequence:int|None; difference_count:int; material_difference_count:int; reconciliation_id:UUID|None=None
    def __post_init__(self):
        object.__setattr__(self,"status",ReconciliationStatus(self.status)); _utc(self.reconciled_at_utc,"reconciled_at_utc")
        if self.difference_count<0 or self.material_difference_count<0 or self.material_difference_count>self.difference_count: raise ValueError("invalid difference counts")
        expected=deterministic_paper_uuid("reconciliation",{"run":self.paper_run_id,"local":self.local_snapshot_id,"venue":self.venue_snapshot_id,"at":self.reconciled_at_utc})
        if self.reconciliation_id is not None and self.reconciliation_id!=expected: raise ValueError("reconciliation_id mismatch")
        object.__setattr__(self,"reconciliation_id",expected)
    @property
    def record_sha256(self):return _paper_hash({n:getattr(self,n) for n in self.__dataclass_fields__ if n!="reconciliation_id"})

@dataclass(frozen=True)
class PaperReconciliationDifference:
    reconciliation_id:UUID; difference_type:ReconciliationDifferenceType; material:bool; identity:str; local_value:object; venue_value:object; explanation:str; difference_id:UUID|None=None
    def __post_init__(self):
        object.__setattr__(self,"difference_type",ReconciliationDifferenceType(self.difference_type)); object.__setattr__(self,"identity",_text(self.identity,"identity")); object.__setattr__(self,"explanation",_text(self.explanation,"explanation"))
        expected=deterministic_paper_uuid("difference",{"reconciliation":self.reconciliation_id,"type":self.difference_type,"identity":self.identity,"local":self.local_value,"venue":self.venue_value})
        if self.difference_id is not None and self.difference_id!=expected: raise ValueError("difference_id mismatch")
        object.__setattr__(self,"difference_id",expected)
    @property
    def record_sha256(self):return _paper_hash({n:getattr(self,n) for n in self.__dataclass_fields__ if n!="difference_id"})

@dataclass(frozen=True)
class PaperKillSwitch:
    paper_run_id:UUID; state:KillSwitchState; reason:KillSwitchReason|None; updated_at_utc:datetime; triggered_at_utc:datetime|None=None; evidence_sha256:str|None=None; incident_id:UUID|None=None; kill_switch_id:UUID|None=None
    def __post_init__(self):
        object.__setattr__(self,"state",KillSwitchState(self.state)); _utc(self.updated_at_utc,"updated_at_utc")
        if self.reason is not None:object.__setattr__(self,"reason",KillSwitchReason(self.reason))
        if self.triggered_at_utc is not None:_utc(self.triggered_at_utc,"triggered_at_utc")
        if self.evidence_sha256 is not None:_hash(self.evidence_sha256,"evidence_sha256")
        if self.state not in (KillSwitchState.ARMED,KillSwitchState.RESET) and (self.reason is None or self.triggered_at_utc is None):raise ValueError("triggered kill state requires evidence")
        expected=deterministic_paper_uuid("kill-switch",{"run":self.paper_run_id})
        if self.kill_switch_id is not None and self.kill_switch_id!=expected:raise ValueError("kill_switch_id mismatch")
        object.__setattr__(self,"kill_switch_id",expected)
    @property
    def record_sha256(self):return _paper_hash({n:getattr(self,n) for n in self.__dataclass_fields__ if n!="kill_switch_id"})

@dataclass(frozen=True)
class PaperLifecycleEvent:
    paper_run_id:UUID; event_type:str; occurred_at_utc:datetime; sequence:int; details:Mapping[str,object]; parent_ids:tuple[UUID,...]=(); event_id:UUID|None=None
    def __post_init__(self):
        object.__setattr__(self,"event_type",_text(self.event_type,"event_type")); _utc(self.occurred_at_utc,"occurred_at_utc")
        if self.sequence<0:raise ValueError("sequence must be non-negative")
        object.__setattr__(self,"details",_map(self.details)); expected=deterministic_paper_uuid("lifecycle",{"run":self.paper_run_id,"sequence":self.sequence,"type":self.event_type,"parents":self.parent_ids})
        if self.event_id is not None and self.event_id!=expected:raise ValueError("event_id mismatch")
        object.__setattr__(self,"event_id",expected)
    @property
    def record_sha256(self):return _paper_hash({n:getattr(self,n) for n in self.__dataclass_fields__ if n!="event_id"})

@dataclass(frozen=True)
class PaperRecoveryRecord:
    paper_run_id:UUID; submission_id:UUID|None; started_at_utc:datetime; completed_at_utc:datetime|None; status:RecoveryStatus; action:str; explanation:str; parent_ids:tuple[UUID,...]; recovery_id:UUID|None=None
    def __post_init__(self):
        object.__setattr__(self,"status",RecoveryStatus(self.status)); _utc(self.started_at_utc,"started_at_utc")
        if self.completed_at_utc is not None:_utc(self.completed_at_utc,"completed_at_utc")
        object.__setattr__(self,"action",_text(self.action,"action")); object.__setattr__(self,"explanation",_text(self.explanation,"explanation")); expected=deterministic_paper_uuid("recovery",{"run":self.paper_run_id,"submission":self.submission_id,"started":self.started_at_utc,"action":self.action})
        if self.recovery_id is not None and self.recovery_id!=expected:raise ValueError("recovery_id mismatch")
        object.__setattr__(self,"recovery_id",expected)
    @property
    def record_sha256(self):return _paper_hash({n:getattr(self,n) for n in self.__dataclass_fields__ if n!="recovery_id"})

@dataclass(frozen=True)
class TransportRequest:
    request_type:TransportRequestType; method:str; url:str; path_with_query:str; body:bytes; idempotency_key:str|None; requested_at_utc:datetime; request_id:UUID|None=None
    def __post_init__(self):
        object.__setattr__(self,"request_type",TransportRequestType(self.request_type)); object.__setattr__(self,"method",_text(self.method,"method").upper()); object.__setattr__(self,"url",_text(self.url,"url")); object.__setattr__(self,"path_with_query",_text(self.path_with_query,"path_with_query")); _utc(self.requested_at_utc,"requested_at_utc")
        expected=deterministic_paper_uuid("transport-request",{"type":self.request_type,"method":self.method,"url":self.url,"path":self.path_with_query,"body":_paper_hash(self.body.decode("utf-8") if self.body else ""),"idempotency":self.idempotency_key})
        if self.request_id is not None and self.request_id!=expected:raise ValueError("request_id mismatch")
        object.__setattr__(self,"request_id",expected)

@dataclass(frozen=True)
class TransportResult:
    request_id:UUID; result_type:TransportResultType; status_code:int|None; received_at_utc:datetime; response_sha256:str|None; payload:Mapping[str,object]; retryable:bool; retry_after_seconds:Decimal|None=None; explanation:str="transport result"
    def __post_init__(self):
        object.__setattr__(self,"result_type",TransportResultType(self.result_type)); _utc(self.received_at_utc,"received_at_utc")
        if self.response_sha256 is not None:_hash(self.response_sha256,"response_sha256")
        if self.retry_after_seconds is not None:_dec(self.retry_after_seconds,"retry_after_seconds",nonnegative=True)
        object.__setattr__(self,"payload",_map(self.payload)); object.__setattr__(self,"explanation",_text(self.explanation,"explanation"))

__all__=[n for n in globals() if n.startswith(("Paper","Venue","Transport","Credential")) or n=="deterministic_paper_uuid"]
