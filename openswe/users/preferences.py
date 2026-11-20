"""Per-person preferences stored on the ``users`` row."""

from typing import Annotated

from pydantic import BaseModel, ConfigDict, StringConstraints


class UserPreferences(BaseModel):
    model_config = ConfigDict(extra="ignore")

    # The person's Slack DM with the bot is one conversation instead of a thread per message.
    concierge_mode: bool = False
    thread_title_instructions: str = ""


class ThreadTitlePreference(BaseModel):
    # How Robin titles the threads this person starts; blank keeps Robin's own style.
    thread_title_instructions: (
        Annotated[str, StringConstraints(strip_whitespace=True, max_length=2_000)] | None
    ) = None


class UserPreferencesPatch(ThreadTitlePreference):
    concierge_mode: bool | None = None
