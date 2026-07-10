"""Realized funding cash-flow construction for linear perpetual positions."""

from __future__ import annotations

from secure_eval_wrapper.data_collection.models import FundingIntervalSource, FundingRate, InstrumentType
from secure_eval_wrapper.execution.models import AccountingMode, FundingPayment, PositionState


def funding_payment_for_position(
    rate: FundingRate,
    *,
    position: PositionState,
    mark_price,
    config_sha256: str,
    record_zero: bool = False,
) -> FundingPayment | None:
    if position.accounting_mode is not AccountingMode.LINEAR_PERPETUAL or position.series_identity.instrument_type is not InstrumentType.PERPETUAL_SWAP:
        return None
    if rate.predicted_rate is not None and rate.provenance.get("predicted_only") is True:
        return None
    if rate.funding_interval is None or rate.funding_interval_source is FundingIntervalSource.UNAVAILABLE:
        raise ValueError("funding payment requires grounded interval evidence")
    cash_flow = -position.quantity * mark_price * rate.rate
    if cash_flow == 0 and not record_zero:
        return None
    return FundingPayment(
        run_id=position.run_id,
        series_identity=position.series_identity,
        funding_rate_id=rate.funding_rate_id,
        funding_timestamp_utc=rate.funding_time_utc,
        signed_quantity=position.quantity,
        mark_price=mark_price,
        funding_rate=rate.rate,
        cash_flow=cash_flow,
        funding_interval=rate.funding_interval,
        funding_interval_source=rate.funding_interval_source.value,
        config_sha256=config_sha256,
        source_observation_ids=rate.source_observation_ids,
        provenance={"rate_record_hash_stable": True},
    )
