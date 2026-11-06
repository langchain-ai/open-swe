import importlib
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from openswe.expedited_review.eligibility import ChangedFile, Ineligible
from openswe.github.codeowners import CodeOwners
from openswe.github.http import GitHubClient
from openswe.github.pull_request_status import PullRequestClient
from openswe.github.pull_requests import PullRequest
from openswe.github.repo_files import RepoSettings
from openswe.human_review import lifecycle
from openswe.human_review.lifecycle import ReviewCard
from openswe.human_review.requests import HumanReviewRequest
from openswe.slack.client import GitHubPrRef
from openswe.users import User, UserIdentity

expedite = importlib.import_module("openswe.tools.expedite_pr_approval")
nomination = importlib.import_module("openswe.expedited_review.nomination")


@pytest.mark.parametrize(
    ("reviews", "owners", "refused"),
    [
        ({"bob": "APPROVED"}, None, True),
        ({"bob": "APPROVED"}, "src/* @bob", True),
        ({"bob": "APPROVED"}, "src/* @carol", False),
        ({"bob": "APPROVED", "carol": "APPROVED"}, "src/* @bob\ntests/* @carol", True),
        ({"bob": "APPROVED"}, "src/* @bob\ntests/* @carol", False),
        ({"bob": "DISMISSED"}, None, False),
        ({"bob": "COMMENTED"}, None, False),
        ({"bob": "APPROVED", "carol": "CHANGES_REQUESTED"}, None, False),
        ({}, None, False),
        (None, None, True),
    ],
)
async def test_nomination_requires_outstanding_review(
    monkeypatch: pytest.MonkeyPatch,
    reviews: dict[str, str] | None,
    owners: str | None,
    refused: bool,
) -> None:
    monkeypatch.setattr(expedite, "get_config", lambda: {"configurable": {"thread_id": "thread-1"}})
    monkeypatch.setattr(
        expedite,
        "get_workspace_settings",
        AsyncMock(return_value=SimpleNamespace(expedited_review_enabled=True)),
    )
    monkeypatch.setattr(expedite, "run_slack_location", AsyncMock(return_value=("C1", "1.0")))
    monkeypatch.setattr(expedite, "resolve_github_token", AsyncMock(return_value=("token", "user")))
    github = GitHubClient.connect(token="token")
    monkeypatch.setattr(GitHubClient, "connect", lambda **_: github)
    monkeypatch.setattr(
        PullRequestClient,
        "pull",
        AsyncMock(
            return_value={
                "state": "open",
                "head": {"sha": "abc"},
                "base": {"ref": "main"},
                "user": {"login": "alice"},
            }
        ),
    )
    monkeypatch.setattr(
        ChangedFile,
        "of_pull",
        AsyncMock(
            return_value=[
                ChangedFile(filename="src/app.py", additions=1, patch="+x"),
                ChangedFile(filename="tests/test_app.py", additions=1, patch="+x"),
            ]
        ),
    )
    monkeypatch.setattr(nomination, "latest_review_states", AsyncMock(return_value=reviews))
    monkeypatch.setattr(
        CodeOwners, "fetch", AsyncMock(return_value=CodeOwners.parse(owners) if owners else None)
    )

    from openswe.users import UserPreferences

    monkeypatch.setattr(
        expedite.User, "preferences_for_login", AsyncMock(return_value=UserPreferences())
    )
    monkeypatch.setattr(User, "for_login", AsyncMock(return_value=User()))
    monkeypatch.setattr(nomination.PublishedAssessment, "latest", AsyncMock(return_value=None))
    monkeypatch.setattr(
        nomination, "assess_eligibility", lambda *_: Ineligible("diff exceeds the limit")
    )
    result = await expedite.expedite_pr_approval("https://github.com/lc/repo/pull/7")
    assert result["success"] is False
    expected = "diff exceeds the limit"
    if refused:
        expected = "already fully reviewed" if reviews is not None else "Could not read"
    assert expected in result["error"]


class NominationRecords:
    def __init__(self, requester: User | None) -> None:
        self.requester = requester
        self.pull_request = PullRequest(owner="lc", repo="repo", number=7)
        self.request: HumanReviewRequest | None = None

    async def save_pull(self, pull: PullRequest) -> PullRequest:
        return pull

    async def save_request(self, request: HumanReviewRequest) -> HumanReviewRequest:
        request.pull_request = self.pull_request
        request.requested_by = self.requester
        self.request = request
        return request


@pytest.mark.parametrize(
    ("author", "requester_source"),
    [
        (author, source)
        for author in ["robin-agent[bot]", "open-swe[bot]", "alice"]
        for source in ["caller", "run", "none", "unlinked", "app"]
        if author != "alice" or source in {"caller", "run", "none"}
    ],
)
async def test_nomination_routes_draft_to_accountable_person(
    monkeypatch: pytest.MonkeyPatch, author: str, requester_source: str
) -> None:
    person = User(
        identities=[
            UserIdentity(provider="github", external_id="1", login="alice"),
            UserIdentity(provider="slack", external_id="U1", login="alice"),
        ]
    )
    if requester_source == "unlinked":
        person.identities = [
            identity for identity in person.identities if identity.provider != "slack"
        ]
    elif requester_source == "app":
        person.identities[0].login = "robin-agent[bot]"
    requester = person if requester_source in {"caller", "unlinked", "app"} else None
    records = NominationRecords(person if requester_source != "none" else None)
    github = GitHubClient.connect(token="token")
    monkeypatch.setattr(GitHubClient, "connect", lambda **_: github)
    monkeypatch.setattr(
        PullRequestClient,
        "pull",
        AsyncMock(
            return_value={
                "state": "open",
                "draft": True,
                "head": {"sha": "abc"},
                "base": {"ref": "main"},
                "user": {"login": author},
            }
        ),
    )
    files = [ChangedFile(filename="src/app.py", additions=1, patch="+x")]
    read_files = AsyncMock(return_value=files)
    monkeypatch.setattr(ChangedFile, "of_pull", read_files)
    monkeypatch.setattr(
        User,
        "for_login",
        AsyncMock(side_effect=lambda provider, login: person if login == "alice" else None),
    )
    monkeypatch.setattr(nomination, "latest_review_states", AsyncMock(return_value={}))
    monkeypatch.setattr(
        nomination.ExpeditedReviewNomination, "covered", AsyncMock(return_value=(None, []))
    )
    monkeypatch.setattr(nomination.RepoSettings, "cached", AsyncMock(return_value=RepoSettings()))
    monkeypatch.setattr(HumanReviewRequest, "active_for", AsyncMock(return_value=None))
    monkeypatch.setattr(HumanReviewRequest, "get", AsyncMock(return_value=None))
    monkeypatch.setattr(PullRequest, "load", AsyncMock(return_value=records.pull_request))
    monkeypatch.setattr(PullRequest, "save", lambda pull: records.save_pull(pull))
    monkeypatch.setattr(HumanReviewRequest, "save", lambda request: records.save_request(request))
    monkeypatch.setattr(ReviewCard, "_files", AsyncMock(return_value=files))
    monkeypatch.setattr(ReviewCard, "_diff_image_id", AsyncMock(return_value=None))
    monkeypatch.setattr(ReviewCard, "_warn_target", AsyncMock(side_effect=lambda card: card))
    monkeypatch.setattr(ReviewCard, "remove_superseded", AsyncMock())
    monkeypatch.setattr(nomination.SlackChannel, "name_for", AsyncMock(return_value="eng"))
    dm = AsyncMock(return_value=("D1", "1.0"))
    monkeypatch.setattr(lifecycle, "send_dm_with_location", dm)
    monkeypatch.setattr(lifecycle, "origin_footer", AsyncMock(return_value=[]))
    subject = nomination.ExpeditedReviewNomination(
        GitHubPrRef(owner="lc", repo="repo", number=7, url="https://github.com/lc/repo/pull/7"),
        nomination.Origin(
            requester=requester,
            run_config={"github_login": "alice"} if requester_source == "run" else {},
        ),
        "token",
    )
    if author != "alice" and requester_source in {"none", "unlinked", "app"}:
        with pytest.raises(nomination.ExpeditedReviewIneligible, match="request_human_review"):
            await subject.start("C1")
        read_files.assert_not_awaited()
        assert records.request is None
        return
    result = await subject.start("C1")
    assert result["success"] is True
    assert result["completion_reply_required"] is True
    assert dm.await_args.args[0] == "U1"
    assert records.request is not None
    assert records.request.is_author(person.id, person.github_login)
    assert not records.request.is_author(User().id, "someone-else")
