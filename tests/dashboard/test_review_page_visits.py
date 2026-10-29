from dataclasses import dataclass
from typing import Any, cast

import pytest

from openswe.github.pull_request_status import PullRequestClient
from openswe.github.pull_requests import PullRequest
from openswe.review.visits import ReviewPageVisit
from openswe.users.models import User


@dataclass
class _Repo:
    owner: str
    name: str

    @property
    def full_name(self) -> str:
        return f"{self.owner}/{self.name}"


@dataclass
class _Pull:
    repo: _Repo
    number: int
    fetched: int = 0

    async def pull(self) -> dict[str, Any]:
        self.fetched += 1
        return {
            "number": self.number,
            "title": "Fetched",
            "state": "open",
            "user": {"login": "bob"},
        }


def _client(owner: str, name: str, number: int) -> PullRequestClient:
    return cast(PullRequestClient, _Pull(_Repo(owner, name), number))


async def test_recent_lists_latest_visits_in_accessible_repos(
    registry_db: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ALLOWED_GITHUB_USERS", "alice,bob")
    alice = await User.sign_in("github", "1", login="alice")
    bob = await User.sign_in("github", "2", login="bob")
    await PullRequest(owner="acme", repo="api", number=1, title="Known", author="alice").save()

    known = _client("acme", "api", 1)
    unseen = _client("acme", "web", 2)
    hidden = _client("other", "secret", 3)
    await ReviewPageVisit.record(alice, known)
    await ReviewPageVisit.record(alice, unseen)
    await ReviewPageVisit.record(alice, hidden)
    await ReviewPageVisit.record(alice, known)
    await ReviewPageVisit.record(bob, unseen)

    recent = await ReviewPageVisit.recent(
        alice, repositories=frozenset({"acme/api", "acme/web"}), limit=10
    )

    assert [(r.repo, r.number, r.title, r.author) for r in recent] == [
        ("api", 1, "Known", "alice"),
        ("web", 2, "Fetched", "bob"),
    ]
    assert cast(_Pull, known).fetched == 0
    assert cast(_Pull, unseen).fetched == 1
