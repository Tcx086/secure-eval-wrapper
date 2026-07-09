"""Offline OHLCV normalization for raw provider observations."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from decimal import Decimal, InvalidOperation
from uuid import NAMESPACE_URL, uuid5

from secure_eval_wrapper.data_collection.models import (
    MarketDataType,
    NormalizedBar,
    RawObservation,
)
from secure_eval_wrapper.data_collection.symbols import normalize_symbol
from secure_eval_wrapper.data_collection.time_utils import (
    coerce_utc_datetime,
    require_utc_datetime,
)


_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_NUMERIC_FIELDS = ("open", "high", "low", "close", "volume")


def _required_text(payload: Mapping[str, object], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"OHLCV payload '{key}' must be a non-empty string")
    return value.strip()


def _parse_decimal(payload: Mapping[str, object], key: str) -> Decimal:
    value = payload.get(key)
    if isinstance(value, bool) or not isinstance(value, (str, int, Decimal)):
        raise ValueError(f"OHLCV payload '{key}' must be an exact decimal value")
    if isinstance(value, str) and not value.strip():
        raise ValueError(f"OHLCV payload '{key}' must not be empty")
    try:
        parsed = Decimal(value)
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(f"OHLCV payload '{key}' is not a valid decimal") from exc
    if not parsed.is_finite():
        raise ValueError(f"OHLCV payload '{key}' must be finite")
    return parsed


def _optional_final_flag(payload: Mapping[str, object]) -> bool | None:
    final = payload.get("is_final")
    partial = payload.get("is_partial")
    if final is not None and not isinstance(final, bool):
        raise ValueError("OHLCV payload 'is_final' must be a boolean when present")
    if partial is not None and not isinstance(partial, bool):
        raise ValueError("OHLCV payload 'is_partial' must be a boolean when present")
    if final is not None and partial is not None and final is partial:
        raise ValueError("OHLCV payload final and partial flags are inconsistent")
    if final is not None:
        return final
    if partial is not None:
        return not partial
    return None


def normalize_ohlcv_observation(observation: RawObservation) -> NormalizedBar:
    """Convert one OHLCV ``RawObservation`` into a provider-neutral bar.

    The function performs no I/O. It rejects ambiguous symbols and timestamps rather than
    guessing, parses all OHLCV numbers as exact ``Decimal`` values, and carries the complete
    source identity and collection provenance into the normalized record.
    """

    if not isinstance(observation, RawObservation):
        raise TypeError("observation must be a RawObservation")
    if observation.data_type is not MarketDataType.OHLCV:
        raise ValueError("only OHLCV RawObservation records can be normalized as bars")
    if not isinstance(observation.payload, Mapping):
        raise ValueError("OHLCV observation payload must be a mapping")

    payload = observation.payload
    exchange = observation.exchange_name
    if not isinstance(exchange, str) or not exchange.strip():
        raise ValueError("OHLCV observation exchange_name must be a non-empty string")
    if not isinstance(observation.timeframe, str) or not observation.timeframe.strip():
        raise ValueError("OHLCV observation timeframe must be a non-empty string")
    timeframe = observation.timeframe.strip()
    payload_timeframe = payload.get("timeframe")
    if payload_timeframe is not None:
        if not isinstance(payload_timeframe, str) or payload_timeframe.strip() != timeframe:
            raise ValueError("OHLCV payload timeframe conflicts with observation provenance")

    raw_symbol = _required_text(payload, "symbol")
    symbol = normalize_symbol(raw_symbol)
    if observation.raw_symbol is not None:
        try:
            normalized_raw_symbol = normalize_symbol(observation.raw_symbol)
        except ValueError:
            # Provider-native symbols such as Binance's concatenated BTCUSDT are opaque
            # provenance. The adapter must separately supply a conservative normalized symbol.
            normalized_raw_symbol = None
            if observation.normalized_symbol is None:
                raise ValueError(
                    "opaque OHLCV raw symbol requires normalized symbol provenance"
                )
        if normalized_raw_symbol is not None and normalized_raw_symbol != symbol:
            raise ValueError("OHLCV payload symbol conflicts with raw symbol provenance")
    if (
        observation.normalized_symbol is not None
        and normalize_symbol(observation.normalized_symbol) != symbol
    ):
        raise ValueError("OHLCV payload symbol conflicts with normalized symbol provenance")

    open_time_text = _required_text(payload, "open_time_utc")
    open_time_utc = coerce_utc_datetime(
        open_time_text,
        field_name="OHLCV payload open_time_utc",
    )
    if observation.observed_at_utc is not None:
        observed_at_utc = require_utc_datetime(
            observation.observed_at_utc,
            field_name="OHLCV observation observed_at_utc",
        )
        if observed_at_utc != open_time_utc:
            raise ValueError("OHLCV payload timestamp conflicts with observation provenance")

    require_utc_datetime(
        observation.request_timestamp_utc,
        field_name="OHLCV observation request_timestamp_utc",
    )
    require_utc_datetime(
        observation.ingested_at_utc,
        field_name="OHLCV observation ingested_at_utc",
    )
    if not _SHA256_PATTERN.fullmatch(observation.source_sha256):
        raise ValueError("OHLCV observation source_sha256 must be lowercase SHA-256")

    decimals = {key: _parse_decimal(payload, key) for key in _NUMERIC_FIELDS}
    close_time_value = payload.get("close_time_utc")
    bar_close_time_utc = (
        None
        if close_time_value is None
        else coerce_utc_datetime(
            close_time_value,
            field_name="OHLCV payload close_time_utc",
        )
    )
    is_final = _optional_final_flag(payload)

    provenance = {
        "collection_run_id": str(observation.collection_run_id),
        "provider_name": observation.provider_name,
        "exchange_name": exchange.strip(),
        "source_endpoint": observation.source_endpoint,
        "source_sha256": observation.source_sha256,
        "request_parameters": dict(observation.request_parameters),
        "request_timestamp_utc": observation.request_timestamp_utc,
        "ingested_at_utc": observation.ingested_at_utc,
        "collection_status": observation.collection_status.value,
        "raw_symbol": observation.raw_symbol,
        "normalized_symbol": observation.normalized_symbol,
        "provider_timestamp": observation.provider_timestamp,
    }
    return NormalizedBar(
        bar_id=uuid5(NAMESPACE_URL, f"normalized-ohlcv:{observation.observation_id}"),
        symbol=symbol,
        exchange=exchange.strip(),
        timeframe=timeframe,
        bar_open_time_utc=open_time_utc,
        open=decimals["open"],
        high=decimals["high"],
        low=decimals["low"],
        close=decimals["close"],
        volume=decimals["volume"],
        source_observation_ids=(observation.observation_id,),
        bar_close_time_utc=bar_close_time_utc,
        is_final=is_final,
        provenance=provenance,
    )


def normalize_ohlcv_observations(
    observations: Sequence[RawObservation],
) -> tuple[NormalizedBar, ...]:
    """Normalize a sequence while preserving its provider order."""

    return tuple(normalize_ohlcv_observation(item) for item in observations)
