"""Per-person preferences stored on the ``users`` row."""

from pydantic import BaseModel, ConfigDict


class UserPreferences(BaseModel):
    model_config = ConfigDict(extra="ignore")

    # The person's Slack DM with the bot is one conversation instead of a thread per message.
    concierge_mode: bool = False


class UserPreferencesPatch(BaseModel):
    concierge_mode: bool | None = None
