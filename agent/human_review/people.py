"""Who is behind a click on a human review card, and whether they may act on the repository."""

from dataclasses import dataclass

from agent.expedited_review.reviews import settings_hint
from agent.github.app import (
    get_github_app_installation_id_for_repo,
    get_github_app_installation_token,
)
from agent.github.ci import has_repo_write_permission
from agent.human_review.requests import HumanReviewRequest
from agent.users import User


async def repo_token(owner: str, repo: str) -> str | None:
    installation_id = await get_github_app_installation_id_for_repo(owner, repo)
    if installation_id is None:
        return None
    return await get_github_app_installation_token(installation_id=installation_id)


@dataclass(frozen=True, slots=True)
class Outcome:
    """What the clicker is told, privately; empty says nothing."""

    message: str = ""


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
    token = await repo_token(pr.owner, pr.repo)
    if token is None:
        return Outcome("Open SWE cannot reach this repository's GitHub App installation.")
    if not await has_repo_write_permission(
        owner=pr.owner, repo=pr.repo, username=login, token=token
    ):
        return Outcome(f"@{login} does not have write access to {pr.owner}/{pr.repo}.")
    return linked
