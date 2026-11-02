"""The current time in the forms records store it."""

from datetime import UTC, datetime


def now_iso() -> str:
    return datetime.now(UTC).isoformat()


def now_ms() -> int:
    return int(datetime.now(UTC).timestamp() * 1000)


def parse_iso(value: str | None) -> datetime | None:
    """A stored timestamp, read as UTC when it carries no zone; ``None`` if absent or bad."""
    if not value or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
