from collections.abc import Awaitable, Callable

import pytest

from agent.expedited_review.approvals import ExpeditedApproval
from agent.github.pull_requests import PullRequest
from agent.users import User

PEOPLE = {"U_ADA": ("ada", "1"), "U_GRACE": ("grace", "2"), "U_LINUS": ("linus", "3")}

OpenApproval = Callable[..., Awaitable[ExpeditedApproval]]


@pytest.fixture(autouse=True)
def _authorized_logins(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ALLOWED_GITHUB_USERS", ",".join(login for login, _ in PEOPLE.values()))


@pytest.fixture
def open_approval(registry_db: None) -> OpenApproval:
    """Register ada, grace and linus, and open a card on ada's pull request."""

    async def make(*, fingerprint: str = "", awaiting_ready: bool = False) -> ExpeditedApproval:
        for slack_id, (login, github_id) in PEOPLE.items():
            user = await User.sign_in("github", github_id, login=login)
            await user.link("slack", slack_id, team_id="T1")
        pr = await PullRequest(owner="lc", repo="repo", number=7, author="ada").save()
        assert pr.author_user_id is not None
        return await ExpeditedApproval(
            pull_request_id=pr.id,
            head_sha="abc123",
            diff_fingerprint=fingerprint,
            awaiting_ready=awaiting_ready,
            thread_id="thread-1",
            slack_channel_id="C1",
            slack_thread_ts="1.0",
            slack_message_ts="2.0",
        ).save()

    return make
