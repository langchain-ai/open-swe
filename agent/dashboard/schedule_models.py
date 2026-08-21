"""Request bodies for the ``/schedules`` endpoints, and the validation they apply.

Cron expressions and Slack channel ids are checked here so a malformed schedule
is rejected at the edge, before any record or cron exists.
"""

import re

from pydantic import BaseModel, Field, field_validator

from ..scheduling.agent_schedules import DEFAULT_SLACK_NOTIFICATION_MODE, SlackNotificationMode

_CRON_FIELD_RANGES = ((0, 59), (0, 23), (1, 31), (1, 12), (0, 7))
_SLACK_CHANNEL_ID_RE = re.compile(r"^[CG][A-Z0-9]{8,}$")


def _validate_cron_value(value: str, low: int, high: int) -> None:
    try:
        n = int(value)
    except ValueError as exc:
        raise ValueError("cron fields must use numbers, *, ranges, steps, or lists") from exc
    if n < low or n > high:
        raise ValueError(f"cron value {n} outside allowed range {low}-{high}")


def _validate_cron_field(field: str, low: int, high: int) -> None:
    for segment in field.split(","):
        if not segment:
            raise ValueError("cron fields cannot contain empty list segments")
        base, sep, step = segment.partition("/")
        if sep:
            _validate_cron_value(step, 1, high)
        if base == "*":
            continue
        start, dash, end = base.partition("-")
        if dash:
            _validate_cron_value(start, low, high)
            _validate_cron_value(end, low, high)
            if int(start) > int(end):
                raise ValueError("cron ranges must be ascending")
        else:
            _validate_cron_value(base, low, high)


def normalize_cron_schedule(raw: str) -> str:
    value = " ".join(raw.strip().split())
    parts = value.split(" ")
    if len(parts) != 5:
        raise ValueError("schedule must be a five-field cron expression")
    for part, (low, high) in zip(parts, _CRON_FIELD_RANGES, strict=True):
        _validate_cron_field(part, low, high)
    return value


def normalize_slack_channel_id(value: str | None) -> str | None:
    channel_id = value.strip().upper() if isinstance(value, str) else ""
    if not channel_id:
        return None
    if not _SLACK_CHANNEL_ID_RE.fullmatch(channel_id):
        raise ValueError("slack_channel_id must be a Slack channel ID starting with C or G")
    return channel_id


class ScheduleCreateBody(BaseModel):
    prompt: str = Field(min_length=1, max_length=20_000)
    schedule: str = Field(min_length=1, max_length=120)
    name: str | None = Field(default=None, max_length=120)
    repo: str | None = None
    model_id: str | None = None
    effort: str | None = None
    slack_channel_id: str | None = None
    slack_notification_mode: SlackNotificationMode = DEFAULT_SLACK_NOTIFICATION_MODE

    @field_validator("schedule")
    @classmethod
    def _valid_schedule(cls, value: str) -> str:
        return normalize_cron_schedule(value)

    @field_validator("slack_channel_id")
    @classmethod
    def _valid_slack_channel_id(cls, value: str | None) -> str | None:
        return normalize_slack_channel_id(value)


class ScheduleUpdateBody(BaseModel):
    prompt: str | None = Field(default=None, min_length=1, max_length=20_000)
    schedule: str | None = Field(default=None, min_length=1, max_length=120)
    name: str | None = Field(default=None, max_length=120)
    repo: str | None = None
    model_id: str | None = None
    effort: str | None = None
    enabled: bool | None = None
    slack_channel_id: str | None = None
    slack_notification_mode: SlackNotificationMode | None = None

    @field_validator("schedule")
    @classmethod
    def _valid_schedule(cls, value: str | None) -> str | None:
        return normalize_cron_schedule(value) if value is not None else None

    @field_validator("slack_channel_id")
    @classmethod
    def _valid_slack_channel_id(cls, value: str | None) -> str | None:
        return normalize_slack_channel_id(value)
