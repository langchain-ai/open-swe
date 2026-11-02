"""GitHub webhook events are dropped for repos no workspace owns when policy says so."""

from typing import Any

import httpx
import pytest

from openswe.github import forwarding
from openswe.github import routes as github_routes
from openswe.webhooks import common as webhook_common
from openswe.workspaces.routing import WorkspaceLookupError
from tests.conftest import post_signed_github_webhook, register_github_logins

_TEST_WEBHOOK_SECRET = "test-secret-for-workspace-routing"


async def _post_github_webhook(event_type: str, payload: dict[str, Any]) -> httpx.Response:
    return await post_signed_github_webhook(event_type, payload, secret=_TEST_WEBHOOK_SECRET)


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
    monkeypatch.setattr(webhook_common, "GITHUB_WEBHOOK_SECRET", _TEST_WEBHOOK_SECRET)

    async def fail_if_called(*args: object, **kwargs: object) -> None:
        raise AssertionError("an unowned repository's issue run must not start")

    monkeypatch.setattr(forwarding, "queue_forward", fail_if_called)

    response = await _post_github_webhook("issue_comment", _unowned_repo_issue_comment_payload())

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ignored"
    assert "workspace" in body["reason"]


async def test_unowned_repo_is_not_ignored_for_workspace_when_policy_unset(
    registry_db: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("OPEN_SWE_UNASSIGNED_REPO_WORKSPACE", raising=False)
    monkeypatch.setattr(webhook_common, "GITHUB_WEBHOOK_SECRET", _TEST_WEBHOOK_SECRET)

    called: dict[str, object] = {}

    async def fake_forward(body: bytes, headers: dict[str, str]) -> None:
        called["event_type"] = headers["X-GitHub-Event"]

    monkeypatch.setattr(forwarding, "queue_forward", fake_forward)
    register_github_logins(monkeypatch, "octocat")

    response = await _post_github_webhook("issue_comment", _unowned_repo_issue_comment_payload())

    assert response.status_code == 200
    body = response.json()
    assert not (body["status"] == "ignored" and "workspace" in body.get("reason", ""))
    assert called["event_type"] == "issue_comment"


async def test_unreadable_workspace_list_asks_github_to_retry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A delivery we cannot route is retryable, so it must not be answered 200."""
    monkeypatch.setattr(webhook_common, "GITHUB_WEBHOOK_SECRET", _TEST_WEBHOOK_SECRET)

    async def unreadable(_owner: str, _name: str) -> bool:
        raise WorkspaceLookupError("workspace listing failed")

    async def fail_if_called(*args: object, **kwargs: object) -> None:
        raise AssertionError("no event may be processed while ownership is unknown")

    monkeypatch.setattr(github_routes, "repo_is_routable", unreadable)
    monkeypatch.setattr(forwarding, "queue_forward", fail_if_called)

    response = await _post_github_webhook("issue_comment", _unowned_repo_issue_comment_payload())

    assert response.status_code == 503
    assert response.json() == {
        "status": "error",
        "reason": "workspace ownership is temporarily unreadable",
    }
