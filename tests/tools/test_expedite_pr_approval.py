import importlib
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from openswe.expedited_review.eligibility import ChangedFile, Ineligible
from openswe.github.codeowners import CodeOwners
from openswe.github.http import GitHubClient
from openswe.github.pull_request_status import PullRequestClient

expedite = importlib.import_module("openswe.tools.expedite_pr_approval")


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
    monkeypatch.setattr(expedite, "latest_review_states", AsyncMock(return_value=reviews))
    monkeypatch.setattr(
        CodeOwners, "fetch", AsyncMock(return_value=CodeOwners.parse(owners) if owners else None)
    )

    monkeypatch.setattr(
        expedite, "assess_eligibility", lambda *_: Ineligible("diff exceeds the limit")
    )
    result = await expedite.expedite_pr_approval("https://github.com/lc/repo/pull/7")
    assert result["success"] is False
    expected = "diff exceeds the limit"
    if refused:
        expected = "already fully reviewed" if reviews is not None else "Could not read"
    assert expected in result["error"]
