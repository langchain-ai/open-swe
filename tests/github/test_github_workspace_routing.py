"""GitHub webhook events are dropped for repos outside the allowlist or that no workspace owns."""

from typing import Any
from unittest.mock import AsyncMock

import pytest

from openswe.github import channel as github_channel
from openswe.github import webhook as github_webhooks
from openswe.webhooks import common as webhook_common
from openswe.webhooks.event_log import EventLog
from openswe.workspaces.routing import WorkspaceLookupError
from tests.conftest import register_github_logins
from tests.github_channel import deliver_github_event

pytestmark = pytest.mark.usefixtures("registry_db")


def _unowned_repo_issue_comment_payload() -> dict[str, Any]:
    return {
        "action": "created",
        "issue": {"id": 12345, "number": 42, "title": "Fix the flaky test"},
        "comment": {"body": "@open-swe help"},
        "repository": {"owner": {"login": "acme"}, "name": "unowned"},
        "sender": {"login": "octocat"},
    }


async def test_repo_outside_allowed_orgs_is_dropped_before_logging(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(webhook_common, "ALLOWED_GITHUB_ORGS", frozenset({"langchain-ai"}))

    async def fail_if_called(*args: object, **kwargs: object) -> None:
        raise AssertionError("an outside repository's delivery must not be processed")

    monkeypatch.setattr(EventLog, "record_once", fail_if_called)
    monkeypatch.setattr(github_channel, "repo_is_routable", fail_if_called)
    monkeypatch.setattr(github_webhooks, "process_github_pr_walkthrough", fail_if_called)

    delivered = await deliver_github_event(
        monkeypatch,
        "pull_request",
        {
            "action": "opened",
            "pull_request": {"number": 7, "head": {"sha": "head"}, "base": {"sha": "base"}},
            "repository": {"owner": {"login": "Joshluxr"}, "name": "tester"},
            "sender": {"login": "octocat"},
        },
    )

    assert delivered.facts.allowed is False
    assert delivered.work == ()


async def test_installation_event_for_an_allowed_repos_owner_clears_the_repo_cache(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(webhook_common, "ALLOWED_GITHUB_ORGS", frozenset({"langchain-ai"}))
    monkeypatch.setattr(webhook_common, "ALLOWED_GITHUB_REPOS", frozenset({"acme/widgets"}))
    cleared: list[bool] = []

    async def record(*args: object, **kwargs: object) -> None:
        return None

    async def forget() -> None:
        cleared.append(True)

    monkeypatch.setattr(EventLog, "record_once", record)
    monkeypatch.setattr(github_channel, "forget_cached_repos", forget)

    delivered = await deliver_github_event(
        monkeypatch,
        "installation_repositories",
        {"action": "added", "installation": {"id": 1, "account": {"login": "Acme"}}},
    )

    assert delivered.work == ("forget_cached_repos",)
    assert cleared == [True]


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
    monkeypatch.setattr(github_webhooks, "conversation_started", AsyncMock(return_value=False))
    register_github_logins(monkeypatch, "octocat")

    delivered = await deliver_github_event(
        monkeypatch, "issue_comment", _unowned_repo_issue_comment_payload()
    )

    assert delivered.facts.routable is True
    assert delivered.issue_run is not None
    assert prepare.await_args is not None
    # A new conversation runs in the workspace that owns the repository.
    assert prepare.await_args.kwargs["workspace"] is None


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
