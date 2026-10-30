"""GitHub deliveries are dropped for repos no workspace owns when policy says so."""

from typing import Any
from unittest.mock import AsyncMock

import pytest

from openswe.github import channel as github_channel
from openswe.github import webhook as github_webhooks
from openswe.workspaces.routing import WorkspaceLookupError
from tests.conftest import register_github_logins
from tests.github_channel import deliver_github_event


def _unowned_repo_issue_comment_payload() -> dict[str, Any]:
    return {
        "action": "created",
        "issue": {"id": 12345, "number": 42, "title": "Fix the flaky test"},
        "comment": {"body": "@open-swe help"},
        "repository": {"owner": {"login": "acme"}, "name": "unowned"},
        "sender": {"login": "octocat"},
    }


async def test_unowned_repo_is_ignored_when_policy_is_ignore(
    registry_db: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("OPEN_SWE_UNASSIGNED_REPO_WORKSPACE", "ignore")
    register_github_logins(monkeypatch, "octocat")

    delivered = await deliver_github_event(
        monkeypatch, "issue_comment", _unowned_repo_issue_comment_payload()
    )

    assert delivered.facts.routable is False
    assert delivered.work == ()
    assert delivered.issue_run is None


async def test_unowned_repo_is_not_ignored_for_workspace_when_policy_unset(
    registry_db: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("OPEN_SWE_UNASSIGNED_REPO_WORKSPACE", raising=False)
    prepare = AsyncMock(return_value=None)
    monkeypatch.setattr(github_webhooks, "prepare_github_issue_run", prepare)
    register_github_logins(monkeypatch, "octocat")

    delivered = await deliver_github_event(
        monkeypatch, "issue_comment", _unowned_repo_issue_comment_payload()
    )

    assert delivered.facts.routable is True
    assert delivered.issue_run is not None
    assert prepare.await_args is not None
    assert prepare.await_args.args[1] == "issue_comment"


async def test_unreadable_workspace_list_fails_the_delivery(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A delivery we cannot route fails, rather than drop work a workspace may own."""

    async def unreadable(_owner: str, _name: str) -> bool:
        raise WorkspaceLookupError("workspace listing failed")

    prepare = AsyncMock()
    monkeypatch.setattr(github_channel, "repo_is_routable", unreadable)
    monkeypatch.setattr(github_webhooks, "prepare_github_issue_run", prepare)

    with pytest.raises(WorkspaceLookupError):
        await deliver_github_event(
            monkeypatch, "issue_comment", _unowned_repo_issue_comment_payload()
        )

    prepare.assert_not_awaited()
