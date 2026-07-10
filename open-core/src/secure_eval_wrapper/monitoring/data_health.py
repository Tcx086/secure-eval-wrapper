"""Point-in-time public market-data and funding health checks."""
from __future__ import annotations
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal

from secure_eval_wrapper.monitoring.events import make_result
from secure_eval_wrapper.monitoring.models import HealthStatus, MonitoringCategory, MonitoredComponent, Severity

_FIXED_TIMEFRAMES = {"1s":1,"1m":60,"3m":180,"5m":300,"15m":900,"30m":1800,"1h":3600,"2h":7200,"4h":14400,"6h":21600,"8h":28800,"12h":43200,"1d":86400,"1w":604800}

@dataclass(frozen=True)
class DataRecordSummary:
    logical_identity: str
    economic_content_sha256: str
    available_at_utc: datetime
    open_time_utc: datetime | None = None
    close_time_utc: datetime | None = None
    is_final: bool = True
    open: Decimal | None = None
    high: Decimal | None = None
    low: Decimal | None = None
    close: Decimal | None = None
    volume: Decimal | None = None
    identity_supported: bool = True

@dataclass(frozen=True)
class FundingRecordSummary:
    logical_identity: str
    economic_content_sha256: str
    funding_time_utc: datetime
    realized: bool = True
    interval_seconds: int | None = None
    settlement_asset: str | None = None
    base_asset: str | None = None

@dataclass(frozen=True)
class DataHealthInput:
    data_type: str
    timeframe: str | None
    records: tuple[DataRecordSummary, ...] = ()
    observation_start_utc: datetime | None = None
    observation_end_utc: datetime | None = None
    funding_records: tuple[FundingRecordSummary, ...] = ()
    expected_settlement_asset: str | None = None


def evaluate_data_health(context, data: DataHealthInput | None, configuration) -> tuple:
    results=[]; category=MonitoringCategory.DATA; component=MonitoredComponent.MARKET_DATA.value
    if data is None:
        return (make_result(context,category=category,component=component,check_name="data_freshness",health_status=HealthStatus.UNKNOWN,reason_code="data_missing",explanation="No validated public dataset summary was supplied."),)
    key=f"{data.data_type}:{data.timeframe or 'none'}"; max_age=configuration.maximum_data_age_seconds.get(key, configuration.maximum_data_age_seconds.get(data.data_type))
    aware=[r for r in data.records if r.available_at_utc.tzinfo is not None and r.available_at_utc.utcoffset() is not None]
    if not data.records:
        freshness=make_result(context,category=category,component=component,check_name="data_freshness",health_status=HealthStatus.UNKNOWN,reason_code="data_missing",explanation="The validated dataset contains no records.")
    elif len(aware)!=len(data.records):
        freshness=make_result(context,category=category,component=component,check_name="data_freshness",health_status=HealthStatus.UNHEALTHY,reason_code="naive_timestamp",explanation="At least one validated input timestamp is naive.")
    else:
        latest=max(r.available_at_utc for r in data.records); age=Decimal(str((context.as_of_utc-latest).total_seconds()))
        if age<0:
            freshness=make_result(context,category=category,component=component,check_name="data_freshness",health_status=HealthStatus.UNHEALTHY,reason_code="future_timestamp",explanation="Latest economic availability is after the declared as-of time.",observed=age)
        elif max_age is None:
            freshness=make_result(context,category=category,component=component,check_name="data_freshness",health_status=HealthStatus.UNKNOWN,reason_code="freshness_threshold_unavailable",explanation="No freshness threshold is configured for this data type and timeframe.",observed=age)
        elif age>max_age:
            freshness=make_result(context,category=category,component=component,check_name="data_freshness",health_status=HealthStatus.DEGRADED,reason_code="data_stale",explanation="Latest economic availability exceeds the configured maximum age.",observed=age,threshold=max_age)
        else:
            freshness=make_result(context,category=category,component=component,check_name="data_freshness",health_status=HealthStatus.HEALTHY,reason_code="data_fresh",explanation="Latest economic availability is within the configured maximum age.",observed=age,threshold=max_age)
    results.append(freshness)
    identities={}; duplicates=0; conflicts=0
    for record in data.records:
        prior=identities.get(record.logical_identity)
        if prior is not None:
            duplicates+=1
            if prior!=record.economic_content_sha256: conflicts+=1
        identities.setdefault(record.logical_identity,record.economic_content_sha256)
    results.append(make_result(context,category=category,component=component,check_name="logical_duplicates",health_status=HealthStatus.UNHEALTHY if conflicts else HealthStatus.DEGRADED if duplicates else HealthStatus.HEALTHY,reason_code="economic_content_conflict" if conflicts else "duplicate_logical_record" if duplicates else "no_duplicate_logical_record",explanation="Logical identity duplication and economic-content consistency were evaluated.",observed={"duplicates":duplicates,"conflicts":conflicts},severity=Severity.CRITICAL if conflicts else None))
    non_final=sum(not r.is_final for r in data.records)
    invalid_ohlcv=sum(r.open is not None and (r.high is None or r.low is None or r.close is None or r.low>min(r.open,r.close) or r.high<max(r.open,r.close) or r.high<r.low or (r.volume is not None and r.volume<0)) for r in data.records)
    unsupported=sum(not r.identity_supported for r in data.records)
    results.append(make_result(context,category=category,component=component,check_name="record_validity",health_status=HealthStatus.UNHEALTHY if invalid_ohlcv or unsupported else HealthStatus.DEGRADED if non_final else HealthStatus.HEALTHY,reason_code="invalid_ohlcv" if invalid_ohlcv else "unsupported_identity" if unsupported else "non_final_bar" if non_final else "validated_records_well_formed",explanation="Finality, OHLCV relationships, volume, and identity support were checked.",observed={"non_final":non_final,"invalid_ohlcv":invalid_ohlcv,"unsupported_identity":unsupported}))
    if data.observation_start_utc is None or data.observation_end_utc is None or data.timeframe is None:
        gap_status=HealthStatus.UNKNOWN; gap_reason="history_window_unavailable"; gap_details={}
    elif data.timeframe not in _FIXED_TIMEFRAMES:
        gap_status=HealthStatus.UNKNOWN; gap_reason="unsupported_timeframe"; gap_details={"timeframe":data.timeframe}
    else:
        step=timedelta(seconds=_FIXED_TIMEFRAMES[data.timeframe]); actual={r.open_time_utc for r in data.records if r.open_time_utc is not None}; expected=[]; point=data.observation_start_utc
        while point<data.observation_end_utc: expected.append(point); point+=step
        missing=[point for point in expected if point not in actual]; longest=0; current=0
        for point in expected:
            if point in missing: current+=1; longest=max(longest,current)
            else: current=0
        gap_details={"count":len(missing),"longest_gap_intervals":longest,"first_gap":None if not missing else missing[0],"last_gap":None if not missing else missing[-1]}
        gap_status=HealthStatus.DEGRADED if missing else HealthStatus.HEALTHY; gap_reason="bar_gap_detected" if missing else "no_bar_gap"
    results.append(make_result(context,category=category,component=component,check_name="fixed_duration_gaps",health_status=gap_status,reason_code=gap_reason,explanation="Expected fixed-duration intervals were compared without synthesizing bars.",observed=gap_details))
    if data.funding_records:
        funding_component=MonitoredComponent.FUNDING_DATA.value; funding_ids={}; fdup=0; predicted=0; missing_interval=0; mismatch=0
        for row in data.funding_records:
            if row.logical_identity in funding_ids: fdup+=1
            funding_ids.setdefault(row.logical_identity,row.economic_content_sha256); predicted+=not row.realized; missing_interval+=row.interval_seconds is None
            mismatch+=bool(data.expected_settlement_asset and row.settlement_asset!=data.expected_settlement_asset)
        unhealthy=fdup or predicted or mismatch; degraded=missing_interval
        reason="predicted_funding_as_realized" if predicted else "duplicate_logical_record" if fdup else "funding_currency_mismatch" if mismatch else "funding_interval_unavailable" if degraded else "funding_healthy"
        results.append(make_result(context,category=category,component=funding_component,check_name="funding_quality",health_status=HealthStatus.UNHEALTHY if unhealthy else HealthStatus.UNKNOWN if degraded else HealthStatus.HEALTHY,reason_code=reason,explanation="Realized funding identity, interval evidence, and settlement currency were evaluated.",observed={"duplicates":fdup,"predicted":predicted,"missing_interval":missing_interval,"currency_mismatch":mismatch}))
        realized_times=[row.funding_time_utc for row in data.funding_records if row.realized and row.funding_time_utc.tzinfo is not None and row.funding_time_utc.utcoffset() is not None]
        maximum_funding_age=configuration.maximum_data_age_seconds.get("funding_rates",configuration.maximum_data_age_seconds.get("funding"))
        if not realized_times:
            fstatus=HealthStatus.UNKNOWN; freason="data_missing"; fage=None
        else:
            fage=Decimal(str((context.as_of_utc-max(realized_times)).total_seconds()))
            fstatus=HealthStatus.UNHEALTHY if fage<0 else HealthStatus.UNKNOWN if maximum_funding_age is None else HealthStatus.DEGRADED if fage>maximum_funding_age else HealthStatus.HEALTHY
            freason="future_timestamp" if fage<0 else "freshness_threshold_unavailable" if maximum_funding_age is None else "data_stale" if fstatus is HealthStatus.DEGRADED else "data_fresh"
        results.append(make_result(context,category=category,component=funding_component,check_name="funding_freshness",health_status=fstatus,reason_code=freason,explanation="Latest realized funding time was evaluated against explicit point-in-time freshness policy.",observed=fage,threshold=maximum_funding_age))
    return tuple(results)