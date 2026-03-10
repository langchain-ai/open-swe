"""Unit tests for the Linear webhook early-filter logic.

These tests cover the comment-body checks that run *before* any external I/O
(Linear API calls, LangGraph runs, etc.), so no network stubs are needed.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from agent import webapp
from agent.utils.github_comments import OPEN_SWE_TAGS

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _linear_comment_payload(body: str, *, bot_actor: bool = False) -> dict:
    """Return a minimal Linear Comment webhook payload."""
    return {
        "type": "Comment",
        "action": "create",
        "data": {
            "body": body,
            # Linear sets botActor to a non-empty actor object for bot comments,
            # or null/absent for human comments.
            "botActor": {"id": "bot-id", "name": "OpenSWE Bot"} if bot_actor else None,
            "issue": {"id": "issue-123", "title": "Test issue"},
        },
    }


@pytest.fixture()
def client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    """TestClient with webhook-secret verification disabled."""
    monkeypatch.setattr(webapp, "LINEAR_WEBHOOK_SECRET", "")
    return TestClient(webapp.app)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_linear_webhook_ignores_non_comment_event(client: TestClient) -> None:
    response = client.post(
        "/webhooks/linear",
        json={"type": "Issue", "action": "create", "data": {}},
    )

    assert response.status_code == 200
    assert response.json()["status"] == "ignored"


def test_linear_webhook_ignores_non_create_action(client: TestClient) -> None:
    response = client.post(
        "/webhooks/linear",
        json={"type": "Comment", "action": "update", "data": {"body": "@openswe do something"}},
    )

    assert response.status_code == 200
    assert response.json()["status"] == "ignored"


def test_linear_webhook_ignores_bot_actor_comment(client: TestClient) -> None:
    response = client.post(
        "/webhooks/linear",
        json=_linear_comment_payload("@openswe do something", bot_actor=True),
    )

    assert response.status_code == 200
    assert response.json()["status"] == "ignored"


@pytest.mark.parametrize(
    "prefix",
    [
        "🔐 **GitHub Authentication Required**",
        "✅ **Pull Request Created**",
        "✅ **Pull Request Updated**",
        "**Pull Request Created**",
        "**Pull Request Updated**",
        "🤖 **Agent Response**",
        "❌ **Agent Error**",
    ],
)
def test_linear_webhook_ignores_our_own_bot_messages(client: TestClient, prefix: str) -> None:
    """Comments that start with one of our bot-message prefixes must be filtered."""
    response = client.post(
        "/webhooks/linear",
        json=_linear_comment_payload(f"{prefix}\n\nsome content here"),
    )

    assert response.status_code == 200
    assert response.json()["status"] == "ignored"


def test_linear_webhook_ignores_comment_without_openswe_tag(client: TestClient) -> None:
    response = client.post(
        "/webhooks/linear",
        json=_linear_comment_payload("Please look at this issue"),
    )

    assert response.status_code == 200
    assert response.json()["status"] == "ignored"


@pytest.mark.parametrize("tag", list(OPEN_SWE_TAGS))
def test_linear_webhook_accepts_all_openswe_tags(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, tag: str
) -> None:
    """@openswe, @open-swe, and @openswe-dev must all trigger processing."""

    # Patch downstream I/O so we don't need real Linear/LangGraph services.
    async def fake_process_linear_issue(issue: dict, repo_config: dict) -> None:
        pass

    monkeypatch.setattr(webapp, "process_linear_issue", fake_process_linear_issue)
    monkeypatch.setattr(
        webapp,
        "fetch_linear_issue_details",
        lambda issue_id: _async_return(
            {
                "id": issue_id,
                "title": "Test issue",
                "team": {"name": "Open SWE"},
                "project": None,
            }
        ),
    )

    response = client.post(
        "/webhooks/linear",
        json=_linear_comment_payload(f"Hey {tag} please fix this"),
    )

    assert response.status_code == 200
    assert response.json()["status"] == "accepted"


# ---------------------------------------------------------------------------
# Small async helper
# ---------------------------------------------------------------------------


async def _async_return(value):  # noqa: ANN001, ANN201
    return value
