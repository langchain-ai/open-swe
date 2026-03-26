from __future__ import annotations

import asyncio
import hashlib
import hmac
import json

from fastapi.testclient import TestClient

from agent import webapp
from agent.utils import github_comments

_TEST_WEBHOOK_SECRET = "test-secret-for-webhook"


def _sign_body(body: bytes, secret: str = _TEST_WEBHOOK_SECRET) -> str:
    """Compute the X-Hub-Signature-256 header value for raw bytes."""
    sig = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return f"sha256={sig}"


def _post_github_webhook(client: TestClient, event_type: str, payload: dict) -> object:
    """Send a signed GitHub webhook POST request."""
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


def test_generate_thread_id_from_github_issue_is_deterministic() -> None:
    first = webapp.generate_thread_id_from_github_issue("12345")
    second = webapp.generate_thread_id_from_github_issue("12345")

    assert first == second
    assert len(first) == 36


def test_build_github_issue_prompt_includes_issue_context() -> None:
    prompt = webapp.build_github_issue_prompt(
        {"owner": "langchain-ai", "name": "open-swe"},
        42,
        "12345",
        "Fix the flaky test",
        "The test is failing intermittently.",
        [{"author": "octocat", "body": "Please take a look", "created_at": "2026-03-09T00:00:00Z"}],
        github_login="octocat",
    )

    assert "Fix the flaky test" in prompt
    assert "The test is failing intermittently." in prompt
    assert "Please take a look" in prompt
    assert "github_comment" in prompt


def test_build_github_issue_followup_prompt_only_includes_comment() -> None:
    prompt = webapp.build_github_issue_followup_prompt("bracesproul", "Please handle this")

    assert prompt == "**bracesproul:**\nPlease handle this"
    assert "## Repository" not in prompt
    assert "## Title" not in prompt


def test_github_webhook_accepts_issue_events(monkeypatch) -> None:
    called: dict[str, object] = {}

    async def fake_process_github_issue(payload: dict[str, object], event_type: str) -> None:
        called["payload"] = payload
        called["event_type"] = event_type

    monkeypatch.setattr(webapp, "process_github_issue", fake_process_github_issue)
    monkeypatch.setattr(webapp, "GITHUB_WEBHOOK_SECRET", _TEST_WEBHOOK_SECRET)

    client = TestClient(webapp.app)
    response = _post_github_webhook(
        client,
        "issues",
        {
            "action": "opened",
            "issue": {
                "id": 12345,
                "number": 42,
                "title": "@openswe fix the flaky test",
                "body": "The test is failing intermittently.",
            },
            "repository": {"owner": {"login": "langchain-ai"}, "name": "open-swe"},
            "sender": {"login": "octocat"},
        },
    )

    assert response.status_code == 200
    assert response.json()["status"] == "accepted"
    assert called["event_type"] == "issues"


def test_github_webhook_ignores_issue_events_without_body_or_title_change(monkeypatch) -> None:
    called = False

    async def fake_process_github_issue(payload: dict[str, object], event_type: str) -> None:
        nonlocal called
        called = True

    monkeypatch.setattr(webapp, "process_github_issue", fake_process_github_issue)
    monkeypatch.setattr(webapp, "GITHUB_WEBHOOK_SECRET", _TEST_WEBHOOK_SECRET)

    client = TestClient(webapp.app)
    response = _post_github_webhook(
        client,
        "issues",
        {
            "action": "edited",
            "changes": {"labels": {"from": []}},
            "issue": {
                "id": 12345,
                "number": 42,
                "title": "@openswe fix the flaky test",
                "body": "The test is failing intermittently.",
            },
            "repository": {"owner": {"login": "langchain-ai"}, "name": "open-swe"},
            "sender": {"login": "octocat"},
        },
    )

    assert response.status_code == 200
    assert response.json()["status"] == "ignored"
    assert called is False


def test_github_webhook_accepts_issue_comment_events(monkeypatch) -> None:
    called: dict[str, object] = {}

    async def fake_process_github_issue(payload: dict[str, object], event_type: str) -> None:
        called["payload"] = payload
        called["event_type"] = event_type

    monkeypatch.setattr(webapp, "process_github_issue", fake_process_github_issue)
    monkeypatch.setattr(webapp, "GITHUB_WEBHOOK_SECRET", _TEST_WEBHOOK_SECRET)

    client = TestClient(webapp.app)
    response = _post_github_webhook(
        client,
        "issue_comment",
        {
            "issue": {"id": 12345, "number": 42, "title": "Fix the flaky test"},
            "comment": {"body": "@openswe please handle this"},
            "repository": {"owner": {"login": "langchain-ai"}, "name": "open-swe"},
            "sender": {"login": "octocat"},
        },
    )

    assert response.status_code == 200
    assert response.json()["status"] == "accepted"
    assert called["event_type"] == "issue_comment"


def test_process_github_issue_uses_resolved_user_token_for_reaction(monkeypatch) -> None:
    captured: dict[str, object] = {}

    async def fake_get_or_resolve_thread_github_token(thread_id: str, email: str) -> str | None:
        captured["thread_id"] = thread_id
        captured["email"] = email
        return "user-token"

    async def fake_get_github_app_installation_token() -> str | None:
        return None

    async def fake_react_to_github_comment(
        repo_config: dict[str, str],
        comment_id: int,
        *,
        event_type: str,
        token: str,
        pull_number: int | None = None,
        node_id: str | None = None,
    ) -> bool:
        captured["reaction_token"] = token
        captured["comment_id"] = comment_id
        return True

    async def fake_fetch_issue_comments(
        repo_config: dict[str, str], issue_number: int, *, token: str | None = None
    ) -> list[dict[str, object]]:
        captured["fetch_token"] = token
        return []

    async def fake_is_thread_active(thread_id: str) -> bool:
        return False

    class _FakeRunsClient:
        async def create(self, *args, **kwargs) -> None:
            captured["run_created"] = True

    class _FakeLangGraphClient:
        runs = _FakeRunsClient()

    monkeypatch.setattr(
        webapp, "_get_or_resolve_thread_github_token", fake_get_or_resolve_thread_github_token
    )
    monkeypatch.setattr(
        webapp, "get_github_app_installation_token", fake_get_github_app_installation_token
    )
    monkeypatch.setattr(webapp, "_thread_exists", lambda thread_id: asyncio.sleep(0, result=False))
    monkeypatch.setattr(webapp, "react_to_github_comment", fake_react_to_github_comment)
    monkeypatch.setattr(webapp, "fetch_issue_comments", fake_fetch_issue_comments)
    monkeypatch.setattr(webapp, "is_thread_active", fake_is_thread_active)
    monkeypatch.setattr(webapp, "langgraph_client", _FakeLangGraphClient())
    monkeypatch.setattr(webapp, "GITHUB_USER_EMAIL_MAP", {"octocat": "octocat@example.com"})

    asyncio.run(
        webapp.process_github_issue(
            {
                "issue": {
                    "id": 12345,
                    "number": 42,
                    "title": "Fix the flaky test",
                    "body": "The test is failing intermittently.",
                    "html_url": "https://github.com/langchain-ai/open-swe/issues/42",
                },
                "comment": {"id": 999, "body": "@openswe please handle this"},
                "repository": {"owner": {"login": "langchain-ai"}, "name": "open-swe"},
                "sender": {"login": "octocat"},
            },
            "issue_comment",
        )
    )

    assert captured["reaction_token"] == "user-token"
    assert captured["fetch_token"] == "user-token"
    assert captured["comment_id"] == 999
    assert captured["run_created"] is True


def test_process_github_issue_existing_thread_uses_followup_prompt(monkeypatch) -> None:
    captured: dict[str, object] = {}

    async def fake_get_or_resolve_thread_github_token(thread_id: str, email: str) -> str | None:
        return "user-token"

    async def fake_get_github_app_installation_token() -> str | None:
        return None

    async def fake_react_to_github_comment(
        repo_config: dict[str, str],
        comment_id: int,
        *,
        event_type: str,
        token: str,
        pull_number: int | None = None,
        node_id: str | None = None,
    ) -> bool:
        return True

    async def fake_fetch_issue_comments(
        repo_config: dict[str, str], issue_number: int, *, token: str | None = None
    ) -> list[dict[str, object]]:
        raise AssertionError("fetch_issue_comments should not be called for follow-up prompts")

    async def fake_thread_exists(thread_id: str) -> bool:
        return True

    async def fake_is_thread_active(thread_id: str) -> bool:
        return False

    class _FakeRunsClient:
        async def create(self, *args, **kwargs) -> None:
            captured["prompt"] = kwargs["input"]["messages"][0]["content"]

    class _FakeLangGraphClient:
        runs = _FakeRunsClient()

    monkeypatch.setattr(
        webapp, "_get_or_resolve_thread_github_token", fake_get_or_resolve_thread_github_token
    )
    monkeypatch.setattr(
        webapp, "get_github_app_installation_token", fake_get_github_app_installation_token
    )
    monkeypatch.setattr(webapp, "_thread_exists", fake_thread_exists)
    monkeypatch.setattr(webapp, "react_to_github_comment", fake_react_to_github_comment)
    monkeypatch.setattr(webapp, "fetch_issue_comments", fake_fetch_issue_comments)
    monkeypatch.setattr(webapp, "is_thread_active", fake_is_thread_active)
    monkeypatch.setattr(webapp, "langgraph_client", _FakeLangGraphClient())
    monkeypatch.setattr(webapp, "GITHUB_USER_EMAIL_MAP", {"octocat": "octocat@example.com"})
    monkeypatch.setattr(
        github_comments, "GITHUB_USER_EMAIL_MAP", {"octocat": "octocat@example.com"}
    )

    asyncio.run(
        webapp.process_github_issue(
            {
                "issue": {
                    "id": 12345,
                    "number": 42,
                    "title": "Fix the flaky test",
                    "body": "The test is failing intermittently.",
                    "html_url": "https://github.com/langchain-ai/open-swe/issues/42",
                },
                "comment": {
                    "id": 999,
                    "body": "@openswe please handle this",
                    "user": {"login": "octocat"},
                },
                "repository": {"owner": {"login": "langchain-ai"}, "name": "open-swe"},
                "sender": {"login": "octocat"},
            },
            "issue_comment",
        )
    )

    assert captured["prompt"] == "**octocat:**\n@openswe please handle this"
    assert "## Repository" not in captured["prompt"]
