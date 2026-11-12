"""Per-person preferences stored on the ``users`` row."""

import re
from typing import TYPE_CHECKING

from pydantic import BaseModel, ConfigDict, Field, field_validator

if TYPE_CHECKING:
    from openswe.users.models import User


class KitchenChannelPreference(BaseModel):
    personal_kitchen_channel_id: str | None = None

    @field_validator("personal_kitchen_channel_id")
    @classmethod
    def normalize_channel(cls, value: str | None) -> str | None:
        if value is None or not value.strip():
            return value.strip() if value is not None else None
        channel_id = value.strip().upper()
        if re.fullmatch(r"[CG][A-Z0-9]{8,}", channel_id) is None:
            raise ValueError("Choose a Slack channel ID")
        return channel_id

    async def validate_channel(self, user: User) -> None:
        from openswe.slack.channels import SlackChannel

        if not self.personal_kitchen_channel_id:
            return
        if not user.slack_user_id:
            raise ValueError("Link your Slack account before choosing a personal kitchen channel")
        channel = await SlackChannel.load(self.personal_kitchen_channel_id, use_cache=False)
        if channel is None or not channel.can_be_kitchen:
            raise ValueError("Choose an internal Slack channel that Open SWE has joined")


class UserPreferences(BaseModel):
    model_config = ConfigDict(extra="ignore")

    # The person's Slack DM with the bot is one conversation instead of a thread per message.
    concierge_mode: bool = False
    personal_kitchen_channel_id: str = ""
    # Code mode can classify with TypeSafe's System One model (the ``classify`` tool).
    experimental_classify: bool = False


class UserPreferencesPatch(KitchenChannelPreference):
    concierge_mode: bool | None = None
    experimental_classify: bool | None = Field(
        default=None, json_schema_extra={"agent_feature_flag": True}
    )
