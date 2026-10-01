"""Per-person preferences stored on the ``users`` row."""

from pydantic import BaseModel, ConfigDict, Field


class UserPreferences(BaseModel):
    model_config = ConfigDict(extra="ignore")

    # The person's Slack DM with the bot is one conversation instead of a thread per message.
    concierge_mode: bool = False
    # Sandboxes created for this person's threads suspend RAM on idle stop and resume warm.
    preserve_sandbox_memory: bool = False
    # This person may ask for a human review in Slack, from the dashboard or through the agent.
    human_review_requests: bool = False
    # Pull requests this person links in a repository's review channel get approved and
    # merged reactions, and a bump with a picked reviewer once they sit green unapproved.
    review_channel_watch: bool = False


class UserPreferencesPatch(BaseModel):
    concierge_mode: bool | None = None
    preserve_sandbox_memory: bool | None = Field(
        default=None, json_schema_extra={"agent_feature_flag": True}
    )
    human_review_requests: bool | None = Field(
        default=None, json_schema_extra={"agent_feature_flag": True}
    )
    review_channel_watch: bool | None = Field(
        default=None, json_schema_extra={"agent_feature_flag": True}
    )
