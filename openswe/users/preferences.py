"""Per-person preferences stored on the ``users`` row."""

from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StringConstraints


class UserPreferences(BaseModel):
    model_config = ConfigDict(extra="ignore")

    # The person's Slack DM with the bot is one conversation instead of a thread per message.
    concierge_mode: bool = False
    thread_title_instructions: str = ""
    experimental_proactivity: bool = False
    # When Robin's heartbeat last offered each playbook, so a declined offer cools down.
    proactive_offered_at: dict[str, str] = Field(default_factory=dict)


class ThreadTitlePreference(BaseModel):
    # How Robin titles the threads this person starts; blank keeps Robin's own style.
    thread_title_instructions: (
        Annotated[str, StringConstraints(strip_whitespace=True, max_length=2_000)] | None
    ) = None


class UserPreferencesPatch(ThreadTitlePreference):
    concierge_mode: bool | None = None
    experimental_proactivity: bool | None = Field(
        default=None, json_schema_extra={"agent_feature_flag": True}
    )
    proactive_offered_at: dict[str, str] | None = None
