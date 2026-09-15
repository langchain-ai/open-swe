"""GitHub webhook events are dropped for repos no workspace owns when policy says so."""

import hashlib
import hmac
import json
from typing import Any

import pytest
from fastapi.testclient import TestClient

from agent.api.app import app
from agent.github import routes as github_routes
from agent.github import webhook as github_webhooks
from agent.webhooks import common as webhook_common
from agent.workspaces.routing import WorkspaceLookupError

_TEST_WEBHOOK_SECRET = "test-secret-for-workspace-routing"


def _sign_body(body: bytes) -> str:
    sig = hmac.new(_TEST_WEBHOOK_SECRET.encode(), body, hashlib.sha256).hexdigest()
    return f"sha256={sig}"


def _post_github_webhook(client: TestClient, event_type: str, payload: dict[str, Any]):
    body = json.dumps(payload, separators=(",", ":")).encode()
    return client.post(
        "/webhooks/github",
        content=body,
        headers={
            "X-GitHub-Event": event_type,
            "X-Hub-Signature-256": _sign_body(body),
            "Content-Type": "application/json",
        },
    )


def _unowned_repo_issue_comment_payload() -> dict[str, Any]:
    return {
        "action": "created",
        "issue": {"id": 12345, "number": 42, "title": "Fix the flaky test"},
        "comment": {"body": "@open-swe help"},
        "repository": {"owner": {"login": "acme"}, "name": "unowned"},
        "sender": {"login": "octocat"},
    }


def test_unowned_repo_is_ignored_when_policy_is_ignore(
    fake_store: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("OPEN_SWE_UNASSIGNED_REPO_WORKSPACE", "ignore")
    monkeypatch.setattr(webhook_common, "GITHUB_WEBHOOK_SECRET", _TEST_WEBHOOK_SECRET)

    async def fail_if_called(*args: object, **kwargs: object) -> None:
        raise AssertionError("process_github_issue should not run for an unowned repository")

    monkeypatch.setattr(github_webhooks, "process_github_issue", fail_if_called)

    client = TestClient(app)
    response = _post_github_webhook(client, "issue_comment", _unowned_repo_issue_comment_payload())

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ignored"
    assert "workspace" in body["reason"]


def test_unowned_repo_is_not_ignored_for_workspace_when_policy_unset(
    fake_store: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("OPEN_SWE_UNASSIGNED_REPO_WORKSPACE", raising=False)
    monkeypatch.setattr(webhook_common, "GITHUB_WEBHOOK_SECRET", _TEST_WEBHOOK_SECRET)

    called: dict[str, object] = {}

    async def fake_process_github_issue(payload: dict[str, object], event_type: str) -> None:
        called["event_type"] = event_type

    monkeypatch.setattr(github_webhooks, "process_github_issue", fake_process_github_issue)

    client = TestClient(app)
    response = _post_github_webhook(client, "issue_comment", _unowned_repo_issue_comment_payload())

    assert response.status_code == 200
    body = response.json()
    assert not (body["status"] == "ignored" and "workspace" in body.get("reason", ""))
    assert called["event_type"] == "issue_comment"


def test_unreadable_workspace_list_asks_github_to_retry(
    fake_store: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A delivery we cannot route is retryable, so it must not be answered 200."""
    monkeypatch.setattr(webhook_common, "GITHUB_WEBHOOK_SECRET", _TEST_WEBHOOK_SECRET)

    async def unreadable(_owner: str, _name: str) -> bool:
        raise WorkspaceLookupError("workspace listing failed")

    async def fail_if_called(*args: object, **kwargs: object) -> None:
        raise AssertionError("no event may be processed while ownership is unknown")

    monkeypatch.setattr(github_routes, "repo_is_routable", unreadable)
    monkeypatch.setattr(github_webhooks, "process_github_issue", fail_if_called)

    client = TestClient(app)
    response = _post_github_webhook(client, "issue_comment", _unowned_repo_issue_comment_payload())

    assert response.status_code == 503
    assert response.json() == {
        "status": "error",
        "reason": "workspace ownership is temporarily unreadable",
    }
