"""Per-person preferences stored on the ``users`` row."""

from pydantic import BaseModel, ConfigDict, Field


class UserPreferences(BaseModel):
    model_config = ConfigDict(extra="ignore")

    # The person's Slack DM with the bot is one conversation instead of a thread per message.
    concierge_mode: bool = False
    pr_review_links: bool = False
    pr_failure_reactions: bool = False
    # Threads this person starts reach MCP and large-result tools only through the sandbox.
    prefer_tools_in_sandbox: bool = False
    experimental_task_coordination: bool = False
    # Threads this person starts run their model loop on Managed Deep Agents, when deployed.
    run_on_mda: bool = True


class UserPreferencesPatch(BaseModel):
    experimental_task_coordination: bool | None = Field(
        default=None, json_schema_extra={"agent_feature_flag": True}
    )
    concierge_mode: bool | None = None
    pr_review_links: bool | None = Field(
        default=None, json_schema_extra={"agent_feature_flag": True}
    )
    pr_failure_reactions: bool | None = Field(
        default=None, json_schema_extra={"agent_feature_flag": True}
    )
    prefer_tools_in_sandbox: bool | None = Field(
        default=None, json_schema_extra={"agent_feature_flag": True}
    )
    run_on_mda: bool | None = None
