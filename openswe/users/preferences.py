"""Per-person preferences stored on the ``users`` row."""

from pydantic import BaseModel, ConfigDict, Field


class UserPreferences(BaseModel):
    model_config = ConfigDict(extra="ignore")

    # The person's Slack DM with the bot is one conversation instead of a thread per message.
    concierge_mode: bool = False
    # Code mode can classify with TypeSafe's System One model (the ``classify`` tool).
    experimental_classify: bool = False


class UserPreferencesPatch(BaseModel):
    concierge_mode: bool | None = None
    experimental_classify: bool | None = Field(
        default=None, json_schema_extra={"agent_feature_flag": True}
    )
