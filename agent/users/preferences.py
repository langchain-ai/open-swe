"""Per-person preferences stored on the ``users`` row."""

from pydantic import BaseModel, ConfigDict, Field


class UserPreferences(BaseModel):
    model_config = ConfigDict(extra="ignore")

    # The person's Slack DM with the bot is one conversation instead of a thread per message.
    concierge_mode: bool = False
    # Sandboxes created for this person's threads suspend RAM on idle stop and resume warm.
    preserve_sandbox_memory: bool = False
    # Ask this person before Open SWE opens a PR as them in a shared thread.
    experimental_act_as_approval: bool = False
    # Open SWE acts as this person in shared threads without asking first.
    act_as_always_allowed: bool = False


class UserPreferencesPatch(BaseModel):
    concierge_mode: bool | None = None
    preserve_sandbox_memory: bool | None = Field(
        default=None, json_schema_extra={"agent_feature_flag": True}
    )
    experimental_act_as_approval: bool | None = Field(
        default=None, json_schema_extra={"agent_feature_flag": True}
    )
    act_as_always_allowed: bool | None = None
