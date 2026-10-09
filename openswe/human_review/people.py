"""Who is behind a click on a human review card, and whether they may act on the repository."""

from dataclasses import dataclass

from openswe.expedited_review.reviews import settings_hint
from openswe.github.http import GitHubAppUnavailable, GitHubClient
from openswe.human_review.requests import HumanReviewRequest
from openswe.users import User


@dataclass(frozen=True, slots=True)
class Outcome:
    """What the clicker is told, privately; empty says nothing."""

    message: str = ""
    dm_card_success: bool = False


@dataclass(frozen=True, slots=True)
class Participant:
    user: User
    github_login: str


def _slack_link_hint() -> str:
    """A missing Slack link is fixed by the Slack connect flow, nothing else.

    Signing in with GitHub creates the GitHub identity and no Slack one, so
    telling someone already signed in to do that again sends them in a circle.
    """
    return settings_hint("Connect Slack under Connections", "/my-settings/connections")


def linked_participant(user: User | None) -> Participant | Outcome:
    if user is None or not any(identity.provider == "github" for identity in user.identities):
        return Outcome(f"Your Slack account is not linked to GitHub. {_slack_link_hint()}")
    return Participant(user=user, github_login=user.login_for("github"))


async def resolve_writer(request: HumanReviewRequest, user: User | None) -> Participant | Outcome:
    """The person behind a click if they have write access to the repository, or why not."""
    linked = linked_participant(user)
    if isinstance(linked, Outcome):
        return linked
    login = linked.github_login
    pr = request.pull_request
    try:
        async with GitHubClient.as_app(pr.owner, pr.repo) as github:
            can_write = await github.repo(pr.owner, pr.repo).can_write(login)
    except GitHubAppUnavailable:
        return Outcome("Open SWE cannot reach this repository's GitHub App installation.")
    if not can_write:
        return Outcome(f"@{login} does not have write access to {pr.owner}/{pr.repo}.")
    return linked
