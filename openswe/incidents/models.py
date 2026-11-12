"""The policy that decides which Slack channels Robin joins as incident channels."""

import re

from pydantic import BaseModel, Field, field_validator


class IncidentPolicy(BaseModel):
    enabled: bool = False
    workspace_id: str = ""
    slack_app_id: str = ""
    channel_prefix: str = "inc-"
    excluded_channel_ids: list[str] = Field(default_factory=list)
    model: str | None = None
    version: int = 0
    enabled_at: float = 0

    @field_validator("channel_prefix")
    @classmethod
    def validate_prefix(cls, value: str) -> str:
        value = value.strip().removeprefix("#").lower()
        if not re.fullmatch(r"[a-z0-9_-]{1,60}", value):
            raise ValueError("Use a nonempty Slack channel prefix, such as inc-")
        return value
