"""The current time in the forms records store it."""

from datetime import UTC, datetime


def now_iso() -> str:
    return datetime.now(UTC).isoformat()


def now_ms() -> int:
    return int(datetime.now(UTC).timestamp() * 1000)
