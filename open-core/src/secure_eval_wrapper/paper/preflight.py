"""Deterministic fail-closed paper pre-flight evaluation."""
from __future__ import annotations
from dataclasses import dataclass
from datetime import datetime
from secure_eval_wrapper.data_collection.hashing import sha256_payload
from secure_eval_wrapper.data_collection.time_utils import require_utc_datetime
from .configuration import PaperRunConfiguration
from .endpoints import catalog_sha256
from .enums import PaperEnvironment,PaperProvider,PreflightStatus
from .models import CredentialReference,PaperAccountSnapshot,PaperPreflightCheck,PaperPreflightReport

@dataclass(frozen=True)
class PaperPreflightEvidence:
    @classmethod
    def verified_internal(cls,at_utc,*,postgresql_required=False):
        return cls(requested_paper=True,live_mode_false=True,endpoint_allowlist_valid=True,production_endpoint_absent=True,external_gate_enabled=True,credential_reference_configured=True,required_credential_fields_present=True,sandbox_credentials_verified=True,permissions_limited=True,market_data_available=True,latest_market_data_at_utc=at_utc,series_identity_matches=True,currency_matches=True,timeframe_unambiguous=True,account_exists=True,account_mode_supported=True,sandbox_status_verified=True,balances_available=True,positions_available=True,unexplained_existing_positions=False,unexplained_existing_orders=False,venue_time_at_utc=at_utc,limits_complete=True,requested_run_within_limits=True,spot_short_impossible=True,derivatives_explicitly_supported=True,postgres_reachable=True,migrations_current=True,audit_writable=True,monitoring_available=True,kill_switch_available=True,reconciliation_repository_available=True,kill_switch_active=False)
    requested_paper:bool=False; live_mode_false:bool=False; endpoint_allowlist_valid:bool=False; production_endpoint_absent:bool=False; external_gate_enabled:bool=False
    credential_reference_configured:bool=False; required_credential_fields_present:bool=False; sandbox_credentials_verified:bool=False; permissions_limited:bool=False
    market_data_available:bool=False; latest_market_data_at_utc:datetime|None=None; series_identity_matches:bool=False; currency_matches:bool=False; timeframe_unambiguous:bool=False
    account_exists:bool=False; account_mode_supported:bool=False; sandbox_status_verified:bool=False; balances_available:bool=False; positions_available:bool=False; unexplained_existing_positions:bool=False; unexplained_existing_orders:bool=False; venue_time_at_utc:datetime|None=None
    limits_complete:bool=False; requested_run_within_limits:bool=False; spot_short_impossible:bool=False; derivatives_explicitly_supported:bool=False
    postgres_reachable:bool=False; migrations_current:bool=False; audit_writable:bool=False; monitoring_available:bool=False; kill_switch_available:bool=False; reconciliation_repository_available:bool=False; kill_switch_active:bool=False

class PaperPreflightEngine:
    def evaluate(self,*,paper_run_id,configuration:PaperRunConfiguration,account_snapshot:PaperAccountSnapshot,credential_reference:CredentialReference,evidence:PaperPreflightEvidence,evaluated_at_utc:datetime,implementation_sha256:str)->PaperPreflightReport:
        now=require_utc_datetime(evaluated_at_utc,field_name="evaluated_at_utc"); checks=[]
        def add(name,passed,reason,explanation,required=True,detail=None):
            status=PreflightStatus.PASSED if passed else PreflightStatus.FAILED
            checks.append(PaperPreflightCheck(name,status,now,reason,explanation,required,sha256_payload({"passed":passed,"detail":detail})))
        add("requested_mode_is_paper",evidence.requested_paper,"paper_mode","paper mode must be explicit")
        add("live_mode_false",evidence.live_mode_false and configuration.environment is not PaperEnvironment.LIVE,"live_disabled","live mode must remain false")
        add("provider_environment_pair",(configuration.provider is PaperProvider.INTERNAL and configuration.environment is PaperEnvironment.PAPER_INTERNAL) or (configuration.provider is PaperProvider.OKX_DEMO and configuration.environment is PaperEnvironment.PAPER_EXCHANGE_SANDBOX),"provider_environment","provider/environment pairing must be allowlisted")
        add("endpoint_allowlist",evidence.endpoint_allowlist_valid and evidence.production_endpoint_absent,"endpoint_allowlist","production and arbitrary endpoints must be absent")
        add("external_sandbox_gate",configuration.provider is PaperProvider.INTERNAL or (configuration.external_sandbox_enabled and evidence.external_gate_enabled),"external_gate","external sandbox requires its explicit gate")
        external=configuration.provider is not PaperProvider.INTERNAL
        add("credential_reference",(not external) or evidence.credential_reference_configured,"credential_reference","external sandbox requires a public-safe credential reference")
        add("credential_fields",(not external) or evidence.required_credential_fields_present,"credential_fields","required local credential fields must be available")
        add("sandbox_credentials",(not external) or evidence.sandbox_credentials_verified,"sandbox_credentials","credentials must verify against demo trading")
        add("credential_permissions",(not external) or evidence.permissions_limited,"credential_permissions","permissions must be limited to account read and demo trade")
        add("market_data_available",evidence.market_data_available and evidence.latest_market_data_at_utc is not None,"market_data","market data evidence is required",detail=evidence.latest_market_data_at_utc)
        fresh_market=evidence.latest_market_data_at_utc is not None and 0 <= (now-evidence.latest_market_data_at_utc).total_seconds() <= configuration.stale_market_data_threshold_seconds
        add("market_data_fresh",fresh_market,"market_data_fresh","latest market data must be fresh")
        add("series_identity",evidence.series_identity_matches and evidence.timeframe_unambiguous,"series_identity","instrument/timeframe identity must be exact")
        add("currency_identity",evidence.currency_matches,"currency_identity","base and settlement currencies must match")
        add("account_exists",evidence.account_exists,"account_exists","paper account must exist")
        add("account_mode",evidence.account_mode_supported,"account_mode","account mode must be explicitly supported")
        add("sandbox_status",configuration.provider is PaperProvider.INTERNAL or evidence.sandbox_status_verified,"sandbox_status","official demo status must be verified")
        add("balances_positions",evidence.balances_available and evidence.positions_available,"account_state","balances and positions are required")
        add("existing_positions",not evidence.unexplained_existing_positions,"existing_positions","unapproved existing positions block the run")
        add("existing_orders",not evidence.unexplained_existing_orders,"existing_orders","unapproved existing orders block the run")
        clock_ok=evidence.venue_time_at_utc is not None and abs((now-evidence.venue_time_at_utc).total_seconds()) <= configuration.maximum_clock_skew_seconds
        add("venue_clock_skew",clock_ok,"clock_skew","venue clock skew must be bounded")
        snapshot_fresh=account_snapshot.status.value=="fresh" and 0 <= (now-account_snapshot.fetched_at_utc).total_seconds() <= configuration.stale_account_snapshot_threshold_seconds
        add("account_snapshot_fresh",snapshot_fresh,"account_snapshot_fresh","account snapshot must be fresh")
        add("limits_complete",evidence.limits_complete and evidence.requested_run_within_limits,"limits","all important limits must be finite and positive")
        add("spot_short_boundary",configuration.allow_short or evidence.spot_short_impossible,"spot_short","Spot short must be disabled or impossible")
        add("derivative_boundary",not configuration.allow_perpetual or evidence.derivatives_explicitly_supported,"derivative_boundary","derivatives require explicit support")
        add("postgresql",(not configuration.persistence_required) or evidence.postgres_reachable,"postgresql","required PostgreSQL authority must be reachable")
        add("migration_catalog",(not configuration.persistence_required) or evidence.migrations_current,"migration_catalog","migration catalog must be current")
        add("audit_writable",(not configuration.persistence_required) or evidence.audit_writable,"audit_writable","audit persistence must be writable")
        add("monitoring",evidence.monitoring_available,"monitoring","Phase 6 monitoring must be available")
        add("kill_switch",evidence.kill_switch_available and not evidence.kill_switch_active,"kill_switch","persisted kill switch must be available and inactive")
        add("reconciliation_repository",evidence.reconciliation_repository_available,"reconciliation","reconciliation repository must be available")
        blockers=tuple(c.check_name for c in checks if c.required and c.status is PreflightStatus.FAILED); status=PreflightStatus.FAILED if blockers else PreflightStatus.PASSED
        return PaperPreflightReport(paper_run_id,now,status,tuple(checks),blockers,(),configuration.config_sha256,account_snapshot.record_sha256,implementation_sha256,catalog_sha256(),credential_reference.reference_sha256)
