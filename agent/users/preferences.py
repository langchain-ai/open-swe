"""Per-person preferences stored on the ``users`` row."""

from pydantic import BaseModel, ConfigDict


class UserPreferences(BaseModel):
    model_config = ConfigDict(extra="ignore")

    # The person's Slack DM with the bot is one conversation instead of a thread per message.
    concierge_mode: bool = False
    # Lowercased GitHub logins allowed to have Open SWE open PRs under this person's name.
    pr_attribution_requesters: list[str] = []

    def allows_pr_attribution_from(self, requester_login: str) -> bool:
        return requester_login.lower() in self.pr_attribution_requesters


class UserPreferencesPatch(BaseModel):
    concierge_mode: bool | None = None
    pr_attribution_requesters: list[str] | None = None
