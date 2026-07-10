"""Transparent, low-dimensional public demonstration alphas.

These examples are educational research inputs. They do not create directions, orders, position
sizes, or claims of profitability.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Mapping, Sequence
from uuid import NAMESPACE_URL, uuid5

from secure_eval_wrapper.alpha.input_validation import (
    AlphaInputRecord,
    PointInTimeSeries,
    record_source_ids,
    record_timestamp,
)
from secure_eval_wrapper.alpha.models import (
    AlphaComputationPoint,
    AlphaDefinition,
    AlphaStatus,
)
from secure_eval_wrapper.data_collection.hashing import sha256_payload
from secure_eval_wrapper.data_collection.models import FundingRate, NormalizedBar


def _implementation_hash(name: str, formula: str, version: str = "1.0.0") -> str:
    return sha256_payload({"public_alpha": name, "version": version, "formula": formula})


def _definition(
    *,
    name: str,
    description: str,
    category: str,
    fields: tuple[str, ...],
    parameter_schema: Mapping[str, object],
    defaults: Mapping[str, object],
    warmup: int,
    semantics: str,
    horizon: str,
    formula: str,
    data_type: str = "ohlcv",
) -> AlphaDefinition:
    version = "1.0.0"
    return AlphaDefinition(
        alpha_id=uuid5(NAMESPACE_URL, f"public-alpha:{name}:{version}"),
        name=name,
        version=version,
        description=description,
        category=category,
        required_data_types=(data_type,),
        required_fields=fields,
        parameter_schema=dict(parameter_schema),
        default_parameters=dict(defaults),
        minimum_warmup=warmup,
        output_semantics=semantics,
        horizon=horizon,
        public_example=True,
        status=AlphaStatus.ACTIVE,
        implementation_sha256=_implementation_hash(name, formula, version),
    )


def _bounded_int(
    parameters: Mapping[str, object],
    defaults: Mapping[str, object],
    name: str,
    *,
    minimum: int = 1,
    maximum: int = 10000,
) -> int:
    value = parameters.get(name, defaults[name])
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{name} must be an integer")
    if not minimum <= value <= maximum:
        raise ValueError(f"{name} must be between {minimum} and {maximum}")
    return value


def _validate_keys(parameters: Mapping[str, object], allowed: set[str]) -> None:
    unknown = set(parameters) - allowed
    if unknown:
        raise ValueError(f"unknown alpha parameters: {', '.join(sorted(unknown))}")


def _mean(values: Sequence[Decimal]) -> Decimal:
    return sum(values, Decimal(0)) / Decimal(len(values))


def _population_std(values: Sequence[Decimal]) -> Decimal:
    average = _mean(values)
    variance = sum(((item - average) ** 2 for item in values), Decimal(0)) / Decimal(len(values))
    return variance.sqrt()


def _source_lineage(records: Sequence[AlphaInputRecord]):
    ids = []
    timestamps = []
    for record in records:
        ids.extend(record_source_ids(record))
        timestamps.append(record_timestamp(record))
    return tuple(dict.fromkeys(ids)), tuple(dict.fromkeys(timestamps))


def _point(
    record: AlphaInputRecord,
    *,
    score: Decimal | None,
    warmup_complete: bool,
    valid: bool,
    sources: Sequence[AlphaInputRecord],
    provenance: Mapping[str, object],
) -> AlphaComputationPoint:
    ids, timestamps = _source_lineage(sources)
    return AlphaComputationPoint(
        timestamp_utc=record_timestamp(record),
        raw_score=score,
        warmup_complete=warmup_complete,
        valid=valid,
        source_observation_ids=ids,
        source_timestamps_utc=timestamps,
        provenance=dict(provenance),
    )


class _BaseAlpha:
    DEFINITION: AlphaDefinition

    @property
    def definition(self) -> AlphaDefinition:
        return self.DEFINITION


class MomentumAlpha(_BaseAlpha):
    """Exact Decimal trailing return: close[t] / close[t-lookback] - 1."""

    DEFINITION = _definition(
        name="momentum",
        description="Trailing close-to-close return over an explicit lookback.",
        category="momentum",
        fields=("close",),
        parameter_schema={"lookback": {"type": "integer", "minimum": 1, "maximum": 10000}},
        defaults={"lookback": 3},
        warmup=3,
        semantics="Continuous trailing return; positive means the current close exceeds the lagged close.",
        horizon="next_observation_research_input",
        formula="close[t] / close[t-lookback] - 1",
    )

    def validate_parameters(self, parameters: Mapping[str, object]) -> Mapping[str, object]:
        _validate_keys(parameters, {"lookback"})
        return {"lookback": _bounded_int(parameters, self.DEFINITION.default_parameters, "lookback")}

    def evaluate(self, series: PointInTimeSeries, parameters: Mapping[str, object]):
        lookback = int(parameters["lookback"])
        points = []
        for index, current in enumerate(series.records):
            assert isinstance(current, NormalizedBar)
            if index < lookback:
                points.append(_point(current, score=None, warmup_complete=False, valid=False, sources=(current,), provenance={"reason": "warmup"}))
                continue
            previous = series.prior(index, lookback)
            assert isinstance(previous, NormalizedBar)
            if previous.close == 0:
                points.append(_point(current, score=None, warmup_complete=True, valid=False, sources=(previous, current), provenance={"reason": "zero_lagged_close"}))
                continue
            points.append(_point(current, score=current.close / previous.close - Decimal(1), warmup_complete=True, valid=True, sources=(previous, current), provenance={"formula": "close_t / close_t_minus_lookback - 1", "lookback": lookback}))
        return tuple(points)


class MovingAverageCrossoverAlpha(_BaseAlpha):
    """Trailing-only ratio: short mean / long mean - 1."""

    DEFINITION = _definition(
        name="moving_average_crossover",
        description="Ratio of a short trailing close mean to a longer trailing close mean.",
        category="trend",
        fields=("close",),
        parameter_schema={
            "short_window": {"type": "integer", "minimum": 1, "maximum": 10000},
            "long_window": {"type": "integer", "minimum": 2, "maximum": 10000},
        },
        defaults={"short_window": 2, "long_window": 5},
        warmup=4,
        semantics="Continuous trailing moving-average ratio minus one.",
        horizon="next_observation_research_input",
        formula="mean(close[t-short+1:t]) / mean(close[t-long+1:t]) - 1",
    )

    def validate_parameters(self, parameters: Mapping[str, object]) -> Mapping[str, object]:
        _validate_keys(parameters, {"short_window", "long_window"})
        short = _bounded_int(parameters, self.DEFINITION.default_parameters, "short_window")
        long = _bounded_int(parameters, self.DEFINITION.default_parameters, "long_window", minimum=2)
        if short >= long:
            raise ValueError("short_window must be less than long_window")
        return {"short_window": short, "long_window": long}

    def evaluate(self, series: PointInTimeSeries, parameters: Mapping[str, object]):
        short = int(parameters["short_window"])
        long = int(parameters["long_window"])
        points = []
        for index, current in enumerate(series.records):
            assert isinstance(current, NormalizedBar)
            if index + 1 < long:
                points.append(_point(current, score=None, warmup_complete=False, valid=False, sources=(current,), provenance={"reason": "warmup"}))
                continue
            short_records = series.trailing(index, short)
            long_records = series.trailing(index, long)
            short_mean = _mean(PointInTimeSeries.decimals(short_records, "close"))
            long_mean = _mean(PointInTimeSeries.decimals(long_records, "close"))
            if long_mean == 0:
                points.append(_point(current, score=None, warmup_complete=True, valid=False, sources=long_records, provenance={"reason": "zero_long_mean"}))
                continue
            points.append(_point(current, score=short_mean / long_mean - Decimal(1), warmup_complete=True, valid=True, sources=long_records, provenance={"short_window": short, "long_window": long, "trailing_only": True}))
        return tuple(points)


class BreakoutAlpha(_BaseAlpha):
    """Distance beyond a prior high/low channel; the current bar is excluded."""

    DEFINITION = _definition(
        name="prior_channel_breakout",
        description="Normalized close distance beyond the high/low channel of prior bars only.",
        category="breakout",
        fields=("high", "low", "close"),
        parameter_schema={"lookback": {"type": "integer", "minimum": 2, "maximum": 10000}},
        defaults={"lookback": 5},
        warmup=5,
        semantics="Positive above the prior high, negative below the prior low, zero inside the prior channel.",
        horizon="next_observation_research_input",
        formula="outside_distance(close[t], max(high[t-L:t]), min(low[t-L:t])) / prior_range",
    )

    def validate_parameters(self, parameters: Mapping[str, object]) -> Mapping[str, object]:
        _validate_keys(parameters, {"lookback"})
        return {"lookback": _bounded_int(parameters, self.DEFINITION.default_parameters, "lookback", minimum=2)}

    def evaluate(self, series: PointInTimeSeries, parameters: Mapping[str, object]):
        lookback = int(parameters["lookback"])
        points = []
        for index, current in enumerate(series.records):
            assert isinstance(current, NormalizedBar)
            if index < lookback:
                points.append(_point(current, score=None, warmup_complete=False, valid=False, sources=(current,), provenance={"reason": "warmup"}))
                continue
            prior = series.trailing(index, lookback, include_current=False)
            high = max(PointInTimeSeries.decimals(prior, "high"))
            low = min(PointInTimeSeries.decimals(prior, "low"))
            width = high - low
            if width == 0:
                points.append(_point(current, score=None, warmup_complete=True, valid=False, sources=(*prior, current), provenance={"reason": "zero_prior_channel"}))
                continue
            score = (current.close - high) / width if current.close > high else (current.close - low) / width if current.close < low else Decimal(0)
            points.append(_point(current, score=score, warmup_complete=True, valid=True, sources=(*prior, current), provenance={"lookback": lookback, "current_bar_excluded": True, "prior_high": high, "prior_low": low}))
        return tuple(points)


class MeanReversionAlpha(_BaseAlpha):
    """Negative trailing population z-score of close."""

    DEFINITION = _definition(
        name="trailing_mean_reversion",
        description="Negative trailing population z-score of close using observations available at t.",
        category="mean_reversion",
        fields=("close",),
        parameter_schema={"window": {"type": "integer", "minimum": 2, "maximum": 10000}},
        defaults={"window": 5},
        warmup=4,
        semantics="Negative trailing close z-score; zero is emitted when trailing variance is zero.",
        horizon="next_observation_research_input",
        formula="-(close[t] - trailing_mean[t]) / trailing_population_std[t]",
    )

    def validate_parameters(self, parameters: Mapping[str, object]) -> Mapping[str, object]:
        _validate_keys(parameters, {"window"})
        return {"window": _bounded_int(parameters, self.DEFINITION.default_parameters, "window", minimum=2)}

    def evaluate(self, series: PointInTimeSeries, parameters: Mapping[str, object]):
        window = int(parameters["window"])
        points = []
        for index, current in enumerate(series.records):
            assert isinstance(current, NormalizedBar)
            if index + 1 < window:
                points.append(_point(current, score=None, warmup_complete=False, valid=False, sources=(current,), provenance={"reason": "warmup"}))
                continue
            trailing = series.trailing(index, window)
            closes = PointInTimeSeries.decimals(trailing, "close")
            average = _mean(closes)
            standard_deviation = _population_std(closes)
            score = Decimal(0) if standard_deviation == 0 else -(current.close - average) / standard_deviation
            points.append(_point(current, score=score, warmup_complete=True, valid=True, sources=trailing, provenance={"window": window, "trailing_only": True, "zero_variance_score": "0"}))
        return tuple(points)


class ShortTermReturnReversalAlpha(MomentumAlpha):
    DEFINITION = _definition(
        name="short_term_return_reversal",
        description="Negative trailing return as a transparent formulaic-style reversal input.",
        category="formulaic",
        fields=("close",),
        parameter_schema={"lookback": {"type": "integer", "minimum": 1, "maximum": 10000}},
        defaults={"lookback": 2},
        warmup=2,
        semantics="Negative close-to-close return over the declared lookback.",
        horizon="next_observation_research_input",
        formula="-(close[t] / close[t-lookback] - 1)",
    )

    def evaluate(self, series: PointInTimeSeries, parameters: Mapping[str, object]):
        base = super().evaluate(series, parameters)
        return tuple(
            AlphaComputationPoint(
                timestamp_utc=item.timestamp_utc,
                raw_score=-item.raw_score if item.raw_score is not None else None,
                warmup_complete=item.warmup_complete,
                valid=item.valid,
                source_observation_ids=item.source_observation_ids,
                source_timestamps_utc=item.source_timestamps_utc,
                provenance={**dict(item.provenance), "formula": "negative_trailing_return"},
            )
            for item in base
        )


class PriorRangeClosePositionAlpha(BreakoutAlpha):
    DEFINITION = _definition(
        name="prior_range_close_position",
        description="Close position relative to the prior high/low range, scaled to [-1, 1] inside the range.",
        category="formulaic",
        fields=("high", "low", "close"),
        parameter_schema={"lookback": {"type": "integer", "minimum": 2, "maximum": 10000}},
        defaults={"lookback": 5},
        warmup=5,
        semantics="Two times prior-range close location minus one; prior channel excludes the current bar.",
        horizon="next_observation_research_input",
        formula="2 * (close[t] - prior_low) / (prior_high - prior_low) - 1",
    )

    def evaluate(self, series: PointInTimeSeries, parameters: Mapping[str, object]):
        lookback = int(parameters["lookback"])
        points = []
        for index, current in enumerate(series.records):
            assert isinstance(current, NormalizedBar)
            if index < lookback:
                points.append(_point(current, score=None, warmup_complete=False, valid=False, sources=(current,), provenance={"reason": "warmup"}))
                continue
            prior = series.trailing(index, lookback, include_current=False)
            high = max(PointInTimeSeries.decimals(prior, "high"))
            low = min(PointInTimeSeries.decimals(prior, "low"))
            score = Decimal(0) if high == low else Decimal(2) * (current.close - low) / (high - low) - Decimal(1)
            points.append(_point(current, score=score, warmup_complete=True, valid=True, sources=(*prior, current), provenance={"lookback": lookback, "current_bar_excluded": True, "zero_range_score": "0"}))
        return tuple(points)


class VolatilityAdjustedMomentumAlpha(_BaseAlpha):
    DEFINITION = _definition(
        name="volatility_adjusted_momentum",
        description="Trailing return divided by the trailing population volatility of one-period returns.",
        category="formulaic",
        fields=("close",),
        parameter_schema={
            "lookback": {"type": "integer", "minimum": 1, "maximum": 10000},
            "volatility_window": {"type": "integer", "minimum": 2, "maximum": 10000},
        },
        defaults={"lookback": 3, "volatility_window": 4},
        warmup=4,
        semantics="Trailing return divided by trailing one-period return volatility; zero when volatility is zero.",
        horizon="next_observation_research_input",
        formula="return[t,lookback] / population_std(one_period_returns[t-volatility_window+1:t])",
    )

    def validate_parameters(self, parameters: Mapping[str, object]) -> Mapping[str, object]:
        _validate_keys(parameters, {"lookback", "volatility_window"})
        return {
            "lookback": _bounded_int(parameters, self.DEFINITION.default_parameters, "lookback"),
            "volatility_window": _bounded_int(parameters, self.DEFINITION.default_parameters, "volatility_window", minimum=2),
        }

    def evaluate(self, series: PointInTimeSeries, parameters: Mapping[str, object]):
        lookback = int(parameters["lookback"])
        vol_window = int(parameters["volatility_window"])
        required_index = max(lookback, vol_window)
        points = []
        for index, current in enumerate(series.records):
            assert isinstance(current, NormalizedBar)
            if index < required_index:
                points.append(_point(current, score=None, warmup_complete=False, valid=False, sources=(current,), provenance={"reason": "warmup"}))
                continue
            lagged = series.prior(index, lookback)
            assert isinstance(lagged, NormalizedBar)
            return_records = series.trailing(index, vol_window + 1)
            closes = PointInTimeSeries.decimals(return_records, "close")
            if lagged.close == 0 or any(value == 0 for value in closes[:-1]):
                points.append(_point(current, score=None, warmup_complete=True, valid=False, sources=return_records, provenance={"reason": "zero_lagged_close"}))
                continue
            returns = tuple(closes[position] / closes[position - 1] - Decimal(1) for position in range(1, len(closes)))
            volatility = _population_std(returns)
            momentum = current.close / lagged.close - Decimal(1)
            score = Decimal(0) if volatility == 0 else momentum / volatility
            points.append(_point(current, score=score, warmup_complete=True, valid=True, sources=return_records, provenance={"lookback": lookback, "volatility_window": vol_window, "zero_volatility_score": "0"}))
        return tuple(points)


class PriceVolumeDivergenceAlpha(_BaseAlpha):
    DEFINITION = _definition(
        name="price_volume_divergence",
        description="Difference between trailing price return and trailing volume change.",
        category="formulaic",
        fields=("close", "volume"),
        parameter_schema={"lookback": {"type": "integer", "minimum": 1, "maximum": 10000}},
        defaults={"lookback": 3},
        warmup=3,
        semantics="Price return minus volume return over the same trailing lookback.",
        horizon="next_observation_research_input",
        formula="close[t]/close[t-L]-1 - (volume[t]/volume[t-L]-1)",
    )

    def validate_parameters(self, parameters: Mapping[str, object]) -> Mapping[str, object]:
        _validate_keys(parameters, {"lookback"})
        return {"lookback": _bounded_int(parameters, self.DEFINITION.default_parameters, "lookback")}

    def evaluate(self, series: PointInTimeSeries, parameters: Mapping[str, object]):
        lookback = int(parameters["lookback"])
        points = []
        for index, current in enumerate(series.records):
            assert isinstance(current, NormalizedBar)
            if index < lookback:
                points.append(_point(current, score=None, warmup_complete=False, valid=False, sources=(current,), provenance={"reason": "warmup"}))
                continue
            lagged = series.prior(index, lookback)
            assert isinstance(lagged, NormalizedBar)
            if lagged.close == 0 or lagged.volume == 0:
                points.append(_point(current, score=None, warmup_complete=True, valid=False, sources=(lagged, current), provenance={"reason": "zero_lagged_input"}))
                continue
            score = current.close / lagged.close - current.volume / lagged.volume
            points.append(_point(current, score=score, warmup_complete=True, valid=True, sources=(lagged, current), provenance={"lookback": lookback}))
        return tuple(points)


class RollingRangeExpansionAlpha(_BaseAlpha):
    DEFINITION = _definition(
        name="rolling_range_expansion",
        description="Current high-low range relative to the mean range of prior bars.",
        category="formulaic",
        fields=("high", "low"),
        parameter_schema={"lookback": {"type": "integer", "minimum": 2, "maximum": 10000}},
        defaults={"lookback": 5},
        warmup=5,
        semantics="Current range divided by prior mean range minus one; current bar is excluded from baseline.",
        horizon="next_observation_research_input",
        formula="(high[t]-low[t]) / mean(high-low over t-L:t) - 1",
    )

    def validate_parameters(self, parameters: Mapping[str, object]) -> Mapping[str, object]:
        _validate_keys(parameters, {"lookback"})
        return {"lookback": _bounded_int(parameters, self.DEFINITION.default_parameters, "lookback", minimum=2)}

    def evaluate(self, series: PointInTimeSeries, parameters: Mapping[str, object]):
        lookback = int(parameters["lookback"])
        points = []
        for index, current in enumerate(series.records):
            assert isinstance(current, NormalizedBar)
            if index < lookback:
                points.append(_point(current, score=None, warmup_complete=False, valid=False, sources=(current,), provenance={"reason": "warmup"}))
                continue
            prior = series.trailing(index, lookback, include_current=False)
            prior_ranges = tuple(item.high - item.low for item in prior if isinstance(item, NormalizedBar))
            baseline = _mean(prior_ranges)
            if baseline == 0:
                points.append(_point(current, score=None, warmup_complete=True, valid=False, sources=(*prior, current), provenance={"reason": "zero_prior_mean_range"}))
                continue
            score = (current.high - current.low) / baseline - Decimal(1)
            points.append(_point(current, score=score, warmup_complete=True, valid=True, sources=(*prior, current), provenance={"lookback": lookback, "current_bar_excluded": True}))
        return tuple(points)


class SignedVolumePressureAlpha(_BaseAlpha):
    DEFINITION = _definition(
        name="signed_volume_pressure",
        description="Signed current volume divided by trailing mean volume.",
        category="formulaic",
        fields=("open", "close", "volume"),
        parameter_schema={"window": {"type": "integer", "minimum": 2, "maximum": 10000}},
        defaults={"window": 5},
        warmup=4,
        semantics="Sign(close-open) times current volume divided by trailing mean volume.",
        horizon="next_observation_research_input",
        formula="sign(close[t]-open[t]) * volume[t] / trailing_mean(volume)[t]",
    )

    def validate_parameters(self, parameters: Mapping[str, object]) -> Mapping[str, object]:
        _validate_keys(parameters, {"window"})
        return {"window": _bounded_int(parameters, self.DEFINITION.default_parameters, "window", minimum=2)}

    def evaluate(self, series: PointInTimeSeries, parameters: Mapping[str, object]):
        window = int(parameters["window"])
        points = []
        for index, current in enumerate(series.records):
            assert isinstance(current, NormalizedBar)
            if index + 1 < window:
                points.append(_point(current, score=None, warmup_complete=False, valid=False, sources=(current,), provenance={"reason": "warmup"}))
                continue
            trailing = series.trailing(index, window)
            average_volume = _mean(PointInTimeSeries.decimals(trailing, "volume"))
            if average_volume == 0:
                points.append(_point(current, score=Decimal(0), warmup_complete=True, valid=True, sources=trailing, provenance={"window": window, "zero_volume_score": "0"}))
                continue
            sign = Decimal(1) if current.close > current.open else Decimal(-1) if current.close < current.open else Decimal(0)
            points.append(_point(current, score=sign * current.volume / average_volume, warmup_complete=True, valid=True, sources=trailing, provenance={"window": window}))
        return tuple(points)


class FundingRateContrarianAlpha(_BaseAlpha):
    DEFINITION = _definition(
        name="funding_rate_contrarian",
        description="Negative realized perpetual funding rate as a public carry-style input.",
        category="funding",
        fields=("rate", "funding_interval", "instrument_key"),
        parameter_schema={},
        defaults={},
        warmup=0,
        semantics="Negative realized funding rate. This score alone is not a complete strategy.",
        horizon="next_funding_observation_research_input",
        formula="-realized_funding_rate[t]",
        data_type="funding_rates",
    )

    def validate_parameters(self, parameters: Mapping[str, object]) -> Mapping[str, object]:
        _validate_keys(parameters, set())
        return {}

    def evaluate(self, series: PointInTimeSeries, parameters: Mapping[str, object]):
        points = []
        for current in series.records:
            assert isinstance(current, FundingRate)
            points.append(_point(current, score=-current.rate, warmup_complete=True, valid=True, sources=(current,), provenance={"funding_interval": current.funding_interval, "funding_interval_source": current.funding_interval_source.value, "instrument_identity_sha256": current.instrument_key.identity_sha256 if current.instrument_key is not None else None, "provider_instrument_id": current.instrument_key.provider_instrument_id if current.instrument_key is not None else None, "complete_strategy": False}))
        return tuple(points)


PUBLIC_ALPHA_TYPES = (
    MomentumAlpha,
    MovingAverageCrossoverAlpha,
    BreakoutAlpha,
    MeanReversionAlpha,
    ShortTermReturnReversalAlpha,
    PriorRangeClosePositionAlpha,
    VolatilityAdjustedMomentumAlpha,
    PriceVolumeDivergenceAlpha,
    RollingRangeExpansionAlpha,
    SignedVolumePressureAlpha,
    FundingRateContrarianAlpha,
)


__all__ = [item.__name__ for item in PUBLIC_ALPHA_TYPES] + ["PUBLIC_ALPHA_TYPES"]
