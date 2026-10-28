from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta
from typing import NotRequired, TypedDict
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


class ScheduleTiming(TypedDict):
    schedule: str
    timezone: str
    end_time: str | None
    at: NotRequired[str]


_ISO_INSTANT = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(?::\d{2}(?:\.\d{1,6})?)?(Z|[+-](?:[01]\d|2[0-3]):[0-5]\d)$"
)
_BOUNDS = ((0, 59), (0, 23), (1, 31), (1, 12), (0, 7))
_NAMES = (
    (),
    (),
    (),
    ("JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"),
    ("SUN", "MON", "TUE", "WED", "THU", "FRI", "SAT"),
)


def schedule_timing(
    *,
    cron: str | None = None,
    at: str | datetime | None = None,
    timezone: str | None = None,
    now: datetime | None = None,
) -> ScheduleTiming:
    if (cron is not None) == (at is not None):
        raise ValueError("Specify exactly one of cron or at")
    if cron is not None:
        zone_name = "UTC" if timezone is None else timezone
        try:
            ZoneInfo(zone_name)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise ValueError("Invalid IANA timezone") from exc
        _validate_cron(cron)
        return {"schedule": cron.strip(), "timezone": zone_name, "end_time": None}
    if timezone is not None:
        raise ValueError("at includes its timezone; omit timezone")
    if isinstance(at, str) and _ISO_INSTANT.fullmatch(at):
        try:
            at = datetime.fromisoformat(at)
        except ValueError:
            raise ValueError("Invalid at timestamp") from None
    if not isinstance(at, datetime) or at.utcoffset() is None:
        raise ValueError("at must be an aware datetime or ISO timestamp with Z or an offset")
    instant = at.astimezone(UTC)
    if instant.second or instant.microsecond:
        instant = instant.replace(second=0, microsecond=0) + timedelta(minutes=1)
    delta = instant - (now or datetime.now(UTC))
    if delta < timedelta(minutes=1) or delta > timedelta(days=365):
        raise ValueError("at must be at least one minute and at most 365 days in the future")
    return {
        "schedule": f"{instant.minute} {instant.hour} {instant.day} {instant.month} *",
        "timezone": "UTC",
        "at": _iso(instant),
        "end_time": _iso(instant + timedelta(minutes=5)),
    }


def _iso(value: datetime) -> str:
    return value.isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _validate_cron(cron: str) -> None:
    fields = cron.upper().split() if isinstance(cron, str) else []
    if len(fields) != len(_BOUNDS):
        raise ValueError("cron must be a five-field expression")
    for index, field in enumerate(fields):
        for item in field.split(","):
            base, separator, step = item.partition("/")
            if separator and (
                not re.fullmatch(r"[0-9]+", step) or not 1 <= int(step) <= _BOUNDS[index][1] + 1
            ):
                raise ValueError("Invalid cron step")
            if base == "*":
                continue
            first, separator, last = base.partition("-")
            lower = _cron_number(first, index)
            if separator and lower > _cron_number(last, index):
                raise ValueError("Invalid cron range")


def _cron_number(text: str, index: int) -> int:
    minimum, maximum = _BOUNDS[index]
    if text in _NAMES[index]:
        value = _NAMES[index].index(text) + minimum
    elif re.fullmatch(r"[0-9]+", text):
        value = int(text)
    else:
        raise ValueError("Invalid cron field")
    if not minimum <= value <= maximum:
        raise ValueError("Invalid cron field")
    return value
