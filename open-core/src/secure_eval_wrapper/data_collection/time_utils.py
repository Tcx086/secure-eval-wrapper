"""Explicit UTC guards for provider and normalization boundaries."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone


def require_utc_datetime(value: datetime, *, field_name: str = "value") -> datetime:
    """Require a timezone-aware datetime whose UTC offset is exactly zero.

    The returned value is normalized to ``datetime.timezone.utc``. Naive datetimes and aware
    datetimes with non-zero offsets fail instead of being interpreted using local time.
    """

    if not isinstance(value, datetime):
        raise TypeError(f"{field_name} must be a datetime")
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name} must be timezone-aware UTC; naive datetime is ambiguous")
    if value.utcoffset() != timedelta(0):
        raise ValueError(f"{field_name} must have a zero UTC offset")
    return value.astimezone(timezone.utc)


def coerce_utc_datetime(
    value: datetime | str,
    *,
    assume_naive_utc: bool = False,
    field_name: str = "value",
) -> datetime:
    """Parse or convert a datetime to UTC without making local-time assumptions.

    Naive values fail by default. A caller may opt in to treating a known-naive source value as
    UTC with ``assume_naive_utc=True``; that decision remains explicit at the call site.
    """

    parsed: datetime
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str):
        text = value.strip()
        if not text:
            raise ValueError(f"{field_name} must not be empty")
        if text.endswith(("Z", "z")):
            text = f"{text[:-1]}+00:00"
        try:
            parsed = datetime.fromisoformat(text)
        except ValueError as exc:
            raise ValueError(f"{field_name} is not a valid ISO-8601 datetime") from exc
    else:
        raise TypeError(f"{field_name} must be a datetime or ISO-8601 string")

    if parsed.tzinfo is None or parsed.utcoffset() is None:
        if not assume_naive_utc:
            raise ValueError(
                f"{field_name} must include a timezone; naive datetime is ambiguous"
            )
        parsed = parsed.replace(tzinfo=timezone.utc)

    return parsed.astimezone(timezone.utc)
