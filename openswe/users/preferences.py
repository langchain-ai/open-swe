"""Per-person preferences stored on the ``users`` row."""

from pydantic import BaseModel, ConfigDict, Field


class UserPreferences(BaseModel):
    model_config = ConfigDict(extra="ignore")

    # The person's Slack DM with the bot is one conversation instead of a thread per message.
    concierge_mode: bool = False
    pr_failure_reactions: bool = False
    experimental_fleet: bool = False
    # Threads this person starts carry what their workspace's curator learned, and the
    # workspace memories they saved in their private threads reach their own runs there
    # until the curator processes them.
    experimental_memory: bool = False


class UserPreferencesPatch(BaseModel):
    concierge_mode: bool | None = None
    pr_failure_reactions: bool | None = Field(
        default=None, json_schema_extra={"agent_feature_flag": True}
    )
    experimental_fleet: bool | None = Field(
        default=None, json_schema_extra={"agent_feature_flag": True}
    )
    experimental_memory: bool | None = Field(
        default=None, json_schema_extra={"agent_feature_flag": True}
    )
