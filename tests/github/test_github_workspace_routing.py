"""GitHub webhook events are dropped for repos no workspace owns when policy says so."""

from typing import Any

import httpx
import pytest

from agent.github import routes as github_routes
from agent.github import webhook as github_webhooks
from agent.github.repositories import Repository
from agent.webhooks import common as webhook_common
from agent.workspaces.routing import WorkspaceLookupError
from agent.workspaces.store import WORKSPACES, WORKSPACES_NAMESPACE, import_store_records
from tests.conftest import FakeStore, post_signed_github_webhook
from tests.support.repositories import FakeRepositories

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
        raise AssertionError("process_github_issue should not run for an unowned repository")

    monkeypatch.setattr(github_webhooks, "process_github_issue", fail_if_called)

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

    async def fake_process_github_issue(payload: dict[str, object], event_type: str) -> None:
        called["event_type"] = event_type

    monkeypatch.setattr(github_webhooks, "process_github_issue", fake_process_github_issue)

    response = await _post_github_webhook("issue_comment", _unowned_repo_issue_comment_payload())

    assert response.status_code == 200
    body = response.json()
    assert not (body["status"] == "ignored" and "workspace" in body.get("reason", ""))
    assert called["event_type"] == "issue_comment"


async def test_repository_of_a_stranded_store_record_asks_github_to_retry(
    registry_db: None, fake_store: FakeStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A record the import skipped still owns its repository, just not in PostgreSQL yet."""
    monkeypatch.setattr(webhook_common, "GITHUB_WEBHOOK_SECRET", _TEST_WEBHOOK_SECRET)
    monkeypatch.setattr(WORKSPACES, "import_completed", False)
    fake_store.seed(
        WORKSPACES_NAMESPACE,
        "legacy",
        {"slug": "legacy", "name": "Legacy", "repos": ["acme/unowned"], "snapshot_status": "?"},
    )
    # Another record imports fine, so the table is populated and only the
    # stranded record's repository is what keeps this delivery from routing.
    fake_store.seed(
        WORKSPACES_NAMESPACE, "oss", {"slug": "oss", "name": "OSS", "repos": ["acme/oss"]}
    )
    assert await import_store_records() == 1

    async def fail_if_called(*args: object, **kwargs: object) -> None:
        raise AssertionError("no event may be processed while ownership is unknown")

    monkeypatch.setattr(github_webhooks, "process_github_issue", fail_if_called)

    response = await _post_github_webhook("issue_comment", _unowned_repo_issue_comment_payload())

    assert response.status_code == 503
    assert response.json()["reason"] == "workspace ownership is temporarily unreadable"


async def test_unreadable_workspace_list_asks_github_to_retry(
    monkeypatch: pytest.MonkeyPatch,
    fake_repositories: FakeRepositories,
) -> None:
    """A delivery we cannot route is retryable, so it must not be answered 200."""
    monkeypatch.setattr(webhook_common, "GITHUB_WEBHOOK_SECRET", _TEST_WEBHOOK_SECRET)

    async def unreadable(_repository: Repository) -> bool:
        raise WorkspaceLookupError("workspace listing failed")

    async def fail_if_called(*args: object, **kwargs: object) -> None:
        raise AssertionError("no event may be processed while ownership is unknown")

    monkeypatch.setattr(github_routes, "repo_is_routable", unreadable)
    monkeypatch.setattr(github_webhooks, "process_github_issue", fail_if_called)

    response = await _post_github_webhook("issue_comment", _unowned_repo_issue_comment_payload())

    assert response.status_code == 503
    assert response.json() == {
        "status": "error",
        "reason": "workspace ownership is temporarily unreadable",
    }
