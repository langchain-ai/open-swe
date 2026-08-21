"""Tolerant parsing of the timestamp shapes our upstreams emit.

GitHub sends ISO-8601 with a trailing ``Z``, LangSmith sends aware and naive
``datetime`` objects, and some token responses send epoch seconds. Everything
that has to compare one against "now" needs the same tolerance, so it lives
here once.
"""

from datetime import UTC, datetime
from typing import Any

__all__ = ["is_expired", "parse_expiry"]


def parse_expiry(value: Any) -> datetime | None:
    """Return ``value`` as an aware UTC-anchored datetime, or ``None`` if unusable.

    Accepts a ``datetime``, epoch seconds, or an ISO-8601 string. A naive value
    is read as UTC, which is what every upstream here means by one.
    """
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=UTC)
    if isinstance(value, bool):
        return None
    if isinstance(value, int | float):
        try:
            return datetime.fromtimestamp(float(value), tz=UTC)
        except (OverflowError, OSError, ValueError):
            return None
    if not isinstance(value, str):
        return None
    raw = value.strip()
    if not raw:
        return None
    if raw.endswith("Z"):
        raw = f"{raw[:-1]}+00:00"
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def is_expired(
    value: Any,
    *,
    now: datetime | None = None,
    skew_seconds: float = 0.0,
) -> bool:
    """Whether ``value`` is already past, or within ``skew_seconds`` of, ``now``.

    An absent or unparseable value is *not* expired: callers use this to decide
    whether to discard a credential, and discarding one we cannot read is worse
    than keeping it until the upstream rejects it.
    """
    parsed = parse_expiry(value)
    if parsed is None:
        return False
    current = (now or datetime.now(UTC)).astimezone(UTC)
    return (parsed - current).total_seconds() <= skew_seconds
