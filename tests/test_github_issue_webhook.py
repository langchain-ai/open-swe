from __future__ import annotations

import asyncio
import hashlib
import hmac
import importlib
import json
import logging

from fastapi.testclient import TestClient

from agent import webapp
from agent.tools import request_pr_review as request_pr_review_tool
from agent.utils import github_comments
from agent.utils import slack as slack_utils
from agent.utils.slack import GitHubPrRef

request_pr_review_module = importlib.import_module("agent.tools.request_pr_review")

_TEST_WEBHOOK_SECRET = "test-secret-for-webhook"
_TEST_SLACK_SECRET = "test-slack-secret"


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


def _sign_slack_body(body: bytes, timestamp: str = "1700000000") -> str:
    base_string = f"v0:{timestamp}:{body.decode()}"
    sig = hmac.new(_TEST_SLACK_SECRET.encode(), base_string.encode(), hashlib.sha256).hexdigest()
    return f"v0={sig}"


def _post_slack_webhook(client: TestClient, payload: dict) -> object:
    body = json.dumps(payload, separators=(",", ":")).encode()
    timestamp = "1700000000"
    return client.post(
        "/webhooks/slack",
        content=body,
        headers={
            "X-Slack-Request-Timestamp": timestamp,
            "X-Slack-Signature": _sign_slack_body(body, timestamp),
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
    assert "GH_TOKEN=dummy gh issue comment" in prompt


def test_build_github_issue_followup_prompt_only_includes_comment() -> None:
    prompt = webapp.build_github_issue_followup_prompt("bracesproul", "Please handle this")

    assert prompt == "**bracesproul:**\nPlease handle this"
    assert "## Repository" not in prompt
    assert "## Title" not in prompt


def test_reviewer_repo_allowlist_allows_matching_repo(monkeypatch) -> None:
    monkeypatch.setattr(webapp, "ALLOWED_REVIEWER_GITHUB_ORGS", frozenset())
    monkeypatch.setattr(
        webapp, "ALLOWED_REVIEWER_GITHUB_REPOS", frozenset({"langchain-ai/open-swe"})
    )

    assert (
        webapp._is_repo_allowed_for_reviewer({"owner": "langchain-ai", "name": "open-swe"}) is True
    )


def test_reviewer_repo_allowlist_blocks_non_matching_repo(monkeypatch) -> None:
    monkeypatch.setattr(webapp, "ALLOWED_REVIEWER_GITHUB_ORGS", frozenset({"langchain-ai"}))
    monkeypatch.setattr(
        webapp, "ALLOWED_REVIEWER_GITHUB_REPOS", frozenset({"langchain-ai/open-swe"})
    )

    assert (
        webapp._is_repo_allowed_for_reviewer({"owner": "langchain-ai", "name": "public-demo"})
        is False
    )


def test_reviewer_org_allowlist_allows_all_repos_in_org(monkeypatch) -> None:
    monkeypatch.setattr(webapp, "ALLOWED_REVIEWER_GITHUB_ORGS", frozenset({"langchain-ai"}))
    monkeypatch.setattr(webapp, "ALLOWED_REVIEWER_GITHUB_REPOS", frozenset())

    assert (
        webapp._is_repo_allowed_for_reviewer({"owner": "langchain-ai", "name": "any-repo"}) is True
    )
    assert webapp._is_repo_allowed_for_reviewer({"owner": "other-org", "name": "any-repo"}) is False


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
            "action": "created",
            "issue": {"id": 12345, "number": 42, "title": "Fix the flaky test"},
            "comment": {"body": "@openswe please handle this"},
            "repository": {"owner": {"login": "langchain-ai"}, "name": "open-swe"},
            "sender": {"login": "octocat"},
        },
    )

    assert response.status_code == 200
    assert response.json()["status"] == "accepted"
    assert called["event_type"] == "issue_comment"


def test_github_webhook_ignores_unmentioned_comment_without_info_log(monkeypatch, caplog) -> None:
    async def fake_process_github_pr_comment(payload: dict[str, object], event_type: str) -> None:
        raise AssertionError("process_github_pr_comment should not be called")

    monkeypatch.setattr(webapp, "process_github_pr_comment", fake_process_github_pr_comment)
    monkeypatch.setattr(webapp, "GITHUB_WEBHOOK_SECRET", _TEST_WEBHOOK_SECRET)
    caplog.set_level(logging.INFO, logger=webapp.logger.name)

    client = TestClient(webapp.app)
    response = _post_github_webhook(
        client,
        "pull_request_review_comment",
        {
            "action": "created",
            "pull_request": {
                "number": 1244,
                "html_url": "https://github.com/langchain-ai/open-swe/pull/1244",
                "base": {"sha": "base-sha"},
                "head": {"sha": "head-sha", "ref": "feature-branch"},
            },
            "comment": {"body": "Looks good to me"},
            "repository": {"owner": {"login": "langchain-ai"}, "name": "open-swe"},
            "sender": {"login": "octocat"},
        },
    )

    assert response.status_code == 200
    assert response.json() == {
        "status": "ignored",
        "reason": "Comment does not mention @openswe or @open-swe",
    }
    assert "does not mention @openswe or @open-swe" not in caplog.text


def test_github_webhook_ignores_unsupported_comment_action(monkeypatch) -> None:
    async def fake_process_github_pr_comment(payload: dict[str, object], event_type: str) -> None:
        raise AssertionError("process_github_pr_comment should not be called")

    monkeypatch.setattr(webapp, "process_github_pr_comment", fake_process_github_pr_comment)
    monkeypatch.setattr(webapp, "GITHUB_WEBHOOK_SECRET", _TEST_WEBHOOK_SECRET)

    client = TestClient(webapp.app)
    response = _post_github_webhook(
        client,
        "pull_request_review",
        {
            "action": "dismissed",
            "review": {"body": "@openswe please check this"},
            "pull_request": {
                "number": 1244,
                "html_url": "https://github.com/langchain-ai/open-swe/pull/1244",
                "base": {"sha": "base-sha"},
                "head": {"sha": "head-sha", "ref": "feature-branch"},
            },
            "repository": {"owner": {"login": "langchain-ai"}, "name": "open-swe"},
            "sender": {"login": "octocat"},
        },
    )

    assert response.status_code == 200
    assert response.json() == {
        "status": "ignored",
        "reason": "Unsupported GitHub pull_request_review action: dismissed",
    }


def test_github_webhook_blocks_reviewer_repo_not_in_reviewer_repo_allowlist(monkeypatch) -> None:
    called = False

    async def fake_process_github_pr_review_request(payload: dict[str, object]) -> None:
        nonlocal called
        called = True

    monkeypatch.setattr(
        webapp, "process_github_pr_review_request", fake_process_github_pr_review_request
    )
    monkeypatch.setattr(webapp, "GITHUB_WEBHOOK_SECRET", _TEST_WEBHOOK_SECRET)
    monkeypatch.setattr(webapp, "ALLOWED_REVIEWER_GITHUB_ORGS", frozenset({"langchain-ai"}))
    monkeypatch.setattr(
        webapp, "ALLOWED_REVIEWER_GITHUB_REPOS", frozenset({"langchain-ai/open-swe"})
    )

    client = TestClient(webapp.app)
    response = _post_github_webhook(
        client,
        "pull_request",
        {
            "action": "review_requested",
            "requested_reviewer": {"login": "open-swe[bot]"},
            "pull_request": {
                "number": 1244,
                "html_url": "https://github.com/langchain-ai/public-demo/pull/1244",
                "base": {"sha": "base-sha"},
                "head": {"sha": "head-sha", "ref": "feature-branch"},
            },
            "repository": {"owner": {"login": "langchain-ai"}, "name": "public-demo"},
            "sender": {"login": "octocat"},
        },
    )

    assert response.status_code == 200
    assert response.json() == {"status": "ignored", "reason": "Repository not in allowlist"}
    assert called is False


def test_github_webhook_accepts_open_swe_review_requested(monkeypatch) -> None:
    called: dict[str, object] = {}

    async def fake_process_github_pr_review_request(payload: dict[str, object]) -> None:
        called["payload"] = payload

    monkeypatch.setattr(
        webapp, "process_github_pr_review_request", fake_process_github_pr_review_request
    )
    monkeypatch.setattr(webapp, "GITHUB_WEBHOOK_SECRET", _TEST_WEBHOOK_SECRET)
    monkeypatch.setattr(
        webapp, "ALLOWED_REVIEWER_GITHUB_REPOS", frozenset({"langchain-ai/open-swe"})
    )

    client = TestClient(webapp.app)
    response = _post_github_webhook(
        client,
        "pull_request",
        {
            "action": "review_requested",
            "requested_reviewer": {"login": "open-swe[bot]"},
            "pull_request": {
                "number": 1244,
                "html_url": "https://github.com/langchain-ai/open-swe/pull/1244",
                "base": {"sha": "base-sha"},
                "head": {"sha": "head-sha", "ref": "feature-branch"},
            },
            "repository": {"owner": {"login": "langchain-ai"}, "name": "open-swe"},
            "sender": {"login": "octocat"},
        },
    )

    assert response.status_code == 200
    assert response.json()["status"] == "accepted"
    assert called["payload"]["requested_reviewer"]["login"] == "open-swe[bot]"


def test_github_webhook_ignores_review_requested_for_other_reviewer(monkeypatch) -> None:
    called = False

    async def fake_process_github_pr_review_request(payload: dict[str, object]) -> None:
        nonlocal called
        called = True

    monkeypatch.setattr(
        webapp, "process_github_pr_review_request", fake_process_github_pr_review_request
    )
    monkeypatch.setattr(webapp, "GITHUB_WEBHOOK_SECRET", _TEST_WEBHOOK_SECRET)

    client = TestClient(webapp.app)
    response = _post_github_webhook(
        client,
        "pull_request",
        {
            "action": "review_requested",
            "requested_reviewer": {"login": "someone-else"},
            "pull_request": {
                "number": 1244,
                "html_url": "https://github.com/langchain-ai/open-swe/pull/1244",
                "base": {"sha": "base-sha"},
                "head": {"sha": "head-sha", "ref": "feature-branch"},
            },
            "repository": {"owner": {"login": "langchain-ai"}, "name": "open-swe"},
            "sender": {"login": "octocat"},
        },
    )

    assert response.status_code == 200
    assert response.json()["status"] == "ignored"
    assert called is False


def test_slack_webhook_routes_review_command_to_reviewer(monkeypatch) -> None:
    captured: dict[str, object] = {}

    async def fake_process_slack_pr_review_request(
        pr_ref: GitHubPrRef, channel_id: str, thread_ts: str
    ) -> None:
        captured["pr_ref"] = pr_ref
        captured["channel_id"] = channel_id
        captured["thread_ts"] = thread_ts

    monkeypatch.setattr(webapp, "SLACK_SIGNING_SECRET", _TEST_SLACK_SECRET)
    monkeypatch.setattr(webapp, "SLACK_BOT_USER_ID", "UBOT")
    monkeypatch.setattr(webapp, "SLACK_BOT_USERNAME", "open-swe")
    monkeypatch.setattr(slack_utils.time, "time", lambda: 1700000000)
    monkeypatch.setattr(webapp, "ALLOWED_REVIEWER_GITHUB_REPOS", frozenset())
    monkeypatch.setattr(webapp, "ALLOWED_REVIEWER_GITHUB_ORGS", frozenset())
    monkeypatch.setattr(
        webapp, "process_slack_pr_review_request", fake_process_slack_pr_review_request
    )

    client = TestClient(webapp.app)
    response = _post_slack_webhook(
        client,
        {
            "type": "event_callback",
            "event": {
                "type": "app_mention",
                "channel": "C123",
                "ts": "1700000000.000100",
                "user": "U123",
                "text": "<@UBOT> review https://github.com/langchain-ai/open-swe/pull/1244",
            },
        },
    )

    assert response.status_code == 200
    assert response.json()["message"] == "Slack PR review request queued"
    pr_ref = captured["pr_ref"]
    assert isinstance(pr_ref, GitHubPrRef)
    assert pr_ref.owner == "langchain-ai"
    assert pr_ref.repo == "open-swe"
    assert pr_ref.number == 1244
    assert captured["channel_id"] == "C123"
    assert captured["thread_ts"] == "1700000000.000100"


def test_slack_webhook_malformed_review_command_does_not_start_agent(monkeypatch) -> None:
    captured: dict[str, object] = {}

    async def fake_process_slack_mention(*args, **kwargs) -> None:
        captured["agent_started"] = True

    async def fake_post_slack_thread_reply(channel_id: str, thread_ts: str, text: str) -> bool:
        captured["reply"] = text
        return True

    monkeypatch.setattr(webapp, "SLACK_SIGNING_SECRET", _TEST_SLACK_SECRET)
    monkeypatch.setattr(webapp, "SLACK_BOT_USER_ID", "UBOT")
    monkeypatch.setattr(webapp, "SLACK_BOT_USERNAME", "open-swe")
    monkeypatch.setattr(slack_utils.time, "time", lambda: 1700000000)
    monkeypatch.setattr(webapp, "process_slack_mention", fake_process_slack_mention)
    monkeypatch.setattr(webapp, "post_slack_thread_reply", fake_post_slack_thread_reply)

    client = TestClient(webapp.app)
    response = _post_slack_webhook(
        client,
        {
            "type": "event_callback",
            "event": {
                "type": "app_mention",
                "channel": "C123",
                "ts": "1700000000.000100",
                "user": "U123",
                "text": "<@UBOT> review https://github.com/langchain-ai/open-swe/issues/1244",
            },
        },
    )

    assert response.status_code == 200
    assert response.json()["reason"] == "Malformed Slack PR review command"
    assert "agent_started" not in captured
    assert "OWNER/REPO/pull/NUMBER" in captured["reply"]


def test_slack_webhook_non_pr_review_request_starts_agent(monkeypatch) -> None:
    captured: dict[str, object] = {}

    async def fake_get_slack_repo_config(
        text: str, channel_id: str, thread_ts: str
    ) -> dict[str, str]:
        captured["repo_config_request"] = {
            "text": text,
            "channel_id": channel_id,
            "thread_ts": thread_ts,
        }
        return {"owner": "langchain-ai", "name": "open-swe"}

    async def fake_process_slack_mention(
        event_data: dict[str, object], repo_config: dict[str, str]
    ) -> None:
        captured["event_data"] = event_data
        captured["repo_config"] = repo_config

    monkeypatch.setattr(webapp, "SLACK_SIGNING_SECRET", _TEST_SLACK_SECRET)
    monkeypatch.setattr(webapp, "SLACK_BOT_USER_ID", "UBOT")
    monkeypatch.setattr(webapp, "SLACK_BOT_USERNAME", "open-swe")
    monkeypatch.setattr(slack_utils.time, "time", lambda: 1700000000)
    monkeypatch.setattr(webapp, "get_slack_repo_config", fake_get_slack_repo_config)
    monkeypatch.setattr(webapp, "process_slack_mention", fake_process_slack_mention)

    client = TestClient(webapp.app)
    response = _post_slack_webhook(
        client,
        {
            "type": "event_callback",
            "event": {
                "type": "app_mention",
                "channel": "C123",
                "ts": "1700000000.000100",
                "user": "U123",
                "text": "<@UBOT> review this branch",
            },
        },
    )

    assert response.status_code == 200
    assert response.json()["message"] == "Slack mention queued"
    assert captured["repo_config"] == {"owner": "langchain-ai", "name": "open-swe"}
    event_data = captured["event_data"]
    assert isinstance(event_data, dict)
    assert event_data["text"] == "<@UBOT> review this branch"


def test_slack_webhook_threaded_followup_uses_parent_thread_ts(monkeypatch) -> None:
    captured: dict[str, object] = {}

    async def fake_get_slack_repo_config(
        text: str, channel_id: str, thread_ts: str
    ) -> dict[str, str]:
        captured["repo_config_request"] = {
            "text": text,
            "channel_id": channel_id,
            "thread_ts": thread_ts,
        }
        return {"owner": "langchain-ai", "name": "open-swe"}

    async def fake_process_slack_mention(
        event_data: dict[str, object], repo_config: dict[str, str]
    ) -> None:
        captured["event_data"] = event_data
        captured["repo_config"] = repo_config

    monkeypatch.setattr(webapp, "SLACK_SIGNING_SECRET", _TEST_SLACK_SECRET)
    monkeypatch.setattr(webapp, "SLACK_BOT_USER_ID", "UBOT")
    monkeypatch.setattr(webapp, "SLACK_BOT_USERNAME", "open-swe")
    monkeypatch.setattr(slack_utils.time, "time", lambda: 1700000000)
    monkeypatch.setattr(webapp, "get_slack_repo_config", fake_get_slack_repo_config)
    monkeypatch.setattr(webapp, "process_slack_mention", fake_process_slack_mention)

    client = TestClient(webapp.app)
    response = _post_slack_webhook(
        client,
        {
            "type": "event_callback",
            "event": {
                "type": "app_mention",
                "channel": "C123",
                "ts": "1700000000.000200",
                "thread_ts": "1700000000.000100",
                "user": "U123",
                "text": "<@UBOT> continue on the branch",
            },
        },
    )

    assert response.status_code == 200
    assert response.json()["message"] == "Slack mention queued"
    assert captured["repo_config_request"] == {
        "text": "<@UBOT> continue on the branch",
        "channel_id": "C123",
        "thread_ts": "1700000000.000100",
    }
    event_data = captured["event_data"]
    assert isinstance(event_data, dict)
    assert event_data["thread_ts"] == "1700000000.000100"
    assert event_data["event_ts"] == "1700000000.000200"


def test_process_slack_pr_review_request_posts_trace_reply(monkeypatch) -> None:
    captured: dict[str, object] = {}

    async def fake_trigger_pr_review_from_ref(
        pr_ref: GitHubPrRef,
        *,
        source: str,
        github_login: str = "",
        github_user_id: int | None = None,
        slack_channel_id: str = "",
        slack_thread_ts: str = "",
    ) -> dict[str, object]:
        captured["pr_ref"] = pr_ref
        captured["source"] = source
        captured["slack_channel_id"] = slack_channel_id
        captured["slack_thread_ts"] = slack_thread_ts
        return {"success": True, "thread_id": "reviewer-thread-id", "pr_url": pr_ref.url}

    async def fake_post_slack_trace_reply(
        channel_id: str, thread_ts: str, thread_id: str, message: str = "Working on it!"
    ) -> None:
        captured["trace_reply"] = {
            "channel_id": channel_id,
            "thread_ts": thread_ts,
            "thread_id": thread_id,
            "message": message,
        }

    monkeypatch.setattr(webapp, "trigger_pr_review_from_ref", fake_trigger_pr_review_from_ref)
    monkeypatch.setattr(webapp, "post_slack_trace_reply", fake_post_slack_trace_reply)

    asyncio.run(
        webapp.process_slack_pr_review_request(
            GitHubPrRef(
                owner="langchain-ai",
                repo="open-swe",
                number=1244,
                url="https://github.com/langchain-ai/open-swe/pull/1244",
            ),
            "C123",
            "1700000000.000100",
        )
    )

    assert captured["source"] == "slack"
    assert captured["slack_channel_id"] == "C123"
    assert captured["slack_thread_ts"] == "1700000000.000100"
    assert captured["trace_reply"] == {
        "channel_id": "C123",
        "thread_ts": "1700000000.000100",
        "thread_id": "reviewer-thread-id",
        "message": "Taking a look...",
    }


def test_process_github_pr_review_request_creates_reviewer_run(monkeypatch) -> None:
    captured: dict[str, object] = {}

    async def fake_get_github_app_installation_token() -> str | None:
        return "app-token"

    async def fake_persist_encrypted_github_token(thread_id: str, token: str) -> str:
        captured["persist_thread_id"] = thread_id
        captured["persist_token"] = token
        return "encrypted-token"

    async def fake_is_thread_active(thread_id: str) -> bool:
        captured["active_thread_id"] = thread_id
        return False

    class _FakeRunsClient:
        async def create(self, thread_id: str, graph: str, **kwargs) -> None:
            captured["thread_id"] = thread_id
            captured["graph"] = graph
            captured["kwargs"] = kwargs

    class _FakeThreadsClient:
        async def create(self, **kwargs) -> None:
            captured["thread_create_kwargs"] = kwargs

    class _FakeLangGraphClient:
        runs = _FakeRunsClient()
        threads = _FakeThreadsClient()

    async def fake_set_reviewer_thread_metadata(thread_id: str, **_kwargs: object) -> None:
        captured["set_metadata_thread_id"] = thread_id

    monkeypatch.setattr(
        webapp, "get_github_app_installation_token", fake_get_github_app_installation_token
    )
    monkeypatch.setattr(
        webapp, "persist_encrypted_github_token", fake_persist_encrypted_github_token
    )
    monkeypatch.setattr(webapp, "is_thread_active", fake_is_thread_active)
    monkeypatch.setattr(webapp, "set_reviewer_thread_metadata", fake_set_reviewer_thread_metadata)
    monkeypatch.setattr(webapp, "get_client", lambda url: _FakeLangGraphClient())

    asyncio.run(
        webapp.process_github_pr_review_request(
            {
                "action": "review_requested",
                "requested_reviewer": {"login": "open-swe[bot]"},
                "pull_request": {
                    "number": 1244,
                    "html_url": "https://github.com/langchain-ai/open-swe/pull/1244",
                    "base": {"sha": "base-sha", "ref": "main"},
                    "head": {"sha": "head-sha", "ref": "feature-branch"},
                },
                "repository": {"owner": {"login": "langchain-ai"}, "name": "open-swe"},
                "sender": {"login": "octocat", "id": 123},
            }
        )
    )

    kwargs = captured["kwargs"]
    prompt = kwargs["input"]["messages"][0]["content"]
    config = kwargs["config"]["configurable"]

    assert captured["graph"] == "reviewer"
    assert captured["thread_create_kwargs"] == {
        "thread_id": captured["thread_id"],
        "if_exists": "do_nothing",
    }
    assert captured["persist_token"] == "app-token"
    assert captured["persist_thread_id"] == captured["thread_id"]
    assert "https://github.com/langchain-ai/open-swe/pull/1244" in prompt
    assert "Base SHA: base-sha" in prompt
    assert "Head SHA: head-sha" in prompt
    assert config["source"] == "github"
    assert config["repo"] == {"owner": "langchain-ai", "name": "open-swe"}
    assert config["pr_number"] == 1244
    assert config["review_requested"] is True


def test_trigger_pr_review_from_ref_creates_reviewer_run(monkeypatch) -> None:
    captured: dict[str, object] = {}

    async def fake_get_github_app_installation_token() -> str | None:
        return "app-token"

    async def fake_fetch_github_pr_metadata(
        pr_ref: GitHubPrRef, *, token: str
    ) -> dict[str, object]:
        captured["metadata_token"] = token
        return {
            "html_url": pr_ref.url,
            "base": {"sha": "base-sha"},
            "head": {"sha": "head-sha", "ref": "feature-branch"},
        }

    async def fake_persist_encrypted_github_token(thread_id: str, token: str) -> str:
        captured["persist_thread_id"] = thread_id
        captured["persist_token"] = token
        return "encrypted-token"

    async def fake_is_thread_active(thread_id: str) -> bool:
        captured["active_thread_id"] = thread_id
        return False

    class _FakeRunsClient:
        async def create(self, thread_id: str, graph: str, **kwargs) -> None:
            captured["thread_id"] = thread_id
            captured["graph"] = graph
            captured["kwargs"] = kwargs

    class _FakeThreadsClient:
        async def create(self, **kwargs) -> None:
            captured["thread_create_kwargs"] = kwargs

    class _FakeLangGraphClient:
        runs = _FakeRunsClient()
        threads = _FakeThreadsClient()

    async def fake_set_reviewer_thread_metadata(thread_id: str, **_kwargs: object) -> None:
        captured["set_metadata_thread_id"] = thread_id

    monkeypatch.setattr(
        webapp, "get_github_app_installation_token", fake_get_github_app_installation_token
    )
    monkeypatch.setattr(webapp, "fetch_github_pr_metadata", fake_fetch_github_pr_metadata)
    monkeypatch.setattr(
        webapp, "persist_encrypted_github_token", fake_persist_encrypted_github_token
    )
    monkeypatch.setattr(webapp, "is_thread_active", fake_is_thread_active)
    monkeypatch.setattr(webapp, "set_reviewer_thread_metadata", fake_set_reviewer_thread_metadata)
    monkeypatch.setattr(webapp, "get_client", lambda url: _FakeLangGraphClient())
    monkeypatch.setattr(webapp, "ALLOWED_REVIEWER_GITHUB_REPOS", frozenset())
    monkeypatch.setattr(webapp, "ALLOWED_REVIEWER_GITHUB_ORGS", frozenset())

    result = asyncio.run(
        webapp.trigger_pr_review_from_ref(
            GitHubPrRef(
                owner="langchain-ai",
                repo="open-swe",
                number=1244,
                url="https://github.com/langchain-ai/open-swe/pull/1244",
            ),
            source="slack",
        )
    )

    kwargs = captured["kwargs"]
    prompt = kwargs["input"]["messages"][0]["content"]
    config = kwargs["config"]["configurable"]
    assert result["success"] is True
    assert captured["graph"] == "reviewer"
    assert captured["thread_create_kwargs"] == {
        "thread_id": captured["thread_id"],
        "if_exists": "do_nothing",
    }
    assert captured["metadata_token"] == "app-token"
    assert captured["persist_token"] == "app-token"
    assert "Base SHA: base-sha" in prompt
    assert "Head SHA: head-sha" in prompt
    assert config["source"] == "slack"
    assert config["repo"] == {"owner": "langchain-ai", "name": "open-swe"}
    assert config["pr_number"] == 1244
    assert config["review_requested"] is True


def test_trigger_pr_review_from_ref_respects_reviewer_allowlist(monkeypatch) -> None:
    called = False

    async def fake_get_github_app_installation_token() -> str | None:
        nonlocal called
        called = True
        return "app-token"

    monkeypatch.setattr(
        webapp, "get_github_app_installation_token", fake_get_github_app_installation_token
    )
    monkeypatch.setattr(
        webapp, "ALLOWED_REVIEWER_GITHUB_REPOS", frozenset({"langchain-ai/open-swe"})
    )
    monkeypatch.setattr(webapp, "ALLOWED_REVIEWER_GITHUB_ORGS", frozenset())

    result = asyncio.run(
        webapp.trigger_pr_review_from_ref(
            GitHubPrRef(
                owner="langchain-ai",
                repo="blocked",
                number=1,
                url="https://github.com/langchain-ai/blocked/pull/1",
            ),
            source="slack",
        )
    )

    assert result == {"success": False, "error": "Repository not allowed for reviewer"}
    assert called is False


def test_request_pr_review_tool_uses_shared_trigger(monkeypatch) -> None:
    captured: dict[str, object] = {}

    async def fake_trigger_pr_review_from_ref(
        pr_ref: GitHubPrRef,
        *,
        source: str,
        github_login: str = "",
        github_user_id: int | None = None,
    ) -> dict[str, object]:
        captured["pr_ref"] = pr_ref
        captured["source"] = source
        return {"success": True, "thread_id": "thread-id"}

    monkeypatch.setattr(
        request_pr_review_module, "trigger_pr_review_from_ref", fake_trigger_pr_review_from_ref
    )

    result = request_pr_review_tool("https://github.com/langchain-ai/open-swe/pull/1244")

    pr_ref = captured["pr_ref"]
    assert isinstance(pr_ref, GitHubPrRef)
    assert pr_ref.number == 1244
    assert captured["source"] == "slack"
    assert result["success"] is True


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
    monkeypatch.setattr(webapp, "get_client", lambda url: _FakeLangGraphClient())
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
    monkeypatch.setattr(webapp, "get_client", lambda url: _FakeLangGraphClient())
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


def test_parse_github_review_command_standalone() -> None:
    is_review, url = github_comments.parse_github_review_command("@open-swe review")
    assert is_review is True
    assert url is None


def test_parse_github_review_command_with_url() -> None:
    is_review, url = github_comments.parse_github_review_command(
        "@open-swe review https://github.com/langchain-ai/open-swe/pull/1244"
    )
    assert is_review is True
    assert url == "https://github.com/langchain-ai/open-swe/pull/1244"


def test_parse_github_review_command_case_insensitive_and_aliases() -> None:
    assert github_comments.parse_github_review_command("@OpenSWE Review") == (True, None)
    assert github_comments.parse_github_review_command("@openswe review") == (True, None)
    assert github_comments.parse_github_review_command("@openswe-dev review") == (True, None)


def test_parse_github_review_command_freeform_does_not_match() -> None:
    assert github_comments.parse_github_review_command("@open-swe review my code") == (False, None)
    assert github_comments.parse_github_review_command("@open-swe please review") == (False, None)
    assert github_comments.parse_github_review_command("@open-swe fix this") == (False, None)
    assert github_comments.parse_github_review_command("") == (False, None)


def test_github_webhook_routes_pr_comment_review_to_reviewer(monkeypatch) -> None:
    captured: dict[str, object] = {}

    async def fake_process_pr_comment(payload: dict[str, object], event_type: str) -> None:
        raise AssertionError("process_github_pr_comment should not be called for review command")

    async def fake_process_review_command(
        payload: dict[str, object], event_type: str, pr_url_override: str | None
    ) -> None:
        captured["payload"] = payload
        captured["event_type"] = event_type
        captured["pr_url_override"] = pr_url_override

    monkeypatch.setattr(webapp, "process_github_pr_comment", fake_process_pr_comment)
    monkeypatch.setattr(webapp, "process_github_pr_review_command", fake_process_review_command)
    monkeypatch.setattr(webapp, "GITHUB_WEBHOOK_SECRET", _TEST_WEBHOOK_SECRET)
    monkeypatch.setattr(
        webapp, "ALLOWED_REVIEWER_GITHUB_REPOS", frozenset({"langchain-ai/open-swe"})
    )
    monkeypatch.setattr(webapp, "ALLOWED_GITHUB_ORGS", frozenset({"langchain-ai"}))

    client = TestClient(webapp.app)
    response = _post_github_webhook(
        client,
        "issue_comment",
        {
            "action": "created",
            "issue": {
                "id": 12345,
                "number": 1244,
                "pull_request": {"url": "https://api.github.com/repos/x/y/pulls/1244"},
            },
            "comment": {"id": 9, "body": "@open-swe review"},
            "repository": {"owner": {"login": "langchain-ai"}, "name": "open-swe"},
            "sender": {"login": "octocat"},
        },
    )

    assert response.status_code == 200
    assert response.json() == {
        "status": "accepted",
        "message": "Processing GitHub PR review command",
    }
    assert captured["event_type"] == "issue_comment"
    assert captured["pr_url_override"] is None


def test_github_webhook_blocks_pr_review_command_outside_reviewer_allowlist(monkeypatch) -> None:
    async def fake_process_review_command(
        payload: dict[str, object], event_type: str, pr_url_override: str | None
    ) -> None:
        raise AssertionError("process_github_pr_review_command should not run")

    monkeypatch.setattr(webapp, "process_github_pr_review_command", fake_process_review_command)
    monkeypatch.setattr(webapp, "GITHUB_WEBHOOK_SECRET", _TEST_WEBHOOK_SECRET)
    monkeypatch.setattr(
        webapp, "ALLOWED_REVIEWER_GITHUB_REPOS", frozenset({"langchain-ai/open-swe"})
    )
    monkeypatch.setattr(webapp, "ALLOWED_GITHUB_ORGS", frozenset({"langchain-ai"}))

    client = TestClient(webapp.app)
    response = _post_github_webhook(
        client,
        "issue_comment",
        {
            "action": "created",
            "issue": {
                "id": 12345,
                "number": 1244,
                "pull_request": {"url": "https://api.github.com/repos/x/y/pulls/1244"},
            },
            "comment": {"id": 9, "body": "@open-swe review"},
            "repository": {"owner": {"login": "langchain-ai"}, "name": "public-demo"},
            "sender": {"login": "octocat"},
        },
    )

    assert response.status_code == 200
    assert response.json() == {
        "status": "ignored",
        "reason": "Repository not in reviewer allowlist",
    }


def test_process_github_pr_review_command_uses_payload_pr(monkeypatch) -> None:
    captured: dict[str, object] = {}

    async def fake_get_app_token() -> str:
        return "app-token"

    async def fake_react(*args, **kwargs) -> None:
        captured["reacted"] = True

    async def fake_trigger(
        pr_ref: GitHubPrRef,
        *,
        source: str,
        github_login: str = "",
        github_user_id: int | None = None,
        slack_channel_id: str = "",
        slack_thread_ts: str = "",
    ) -> dict[str, object]:
        captured["pr_ref"] = pr_ref
        captured["source"] = source
        captured["github_login"] = github_login
        captured["github_user_id"] = github_user_id
        return {"success": True, "thread_id": "tid", "pr_url": pr_ref.url}

    monkeypatch.setattr(webapp, "get_github_app_installation_token", fake_get_app_token)
    monkeypatch.setattr(webapp, "react_to_github_comment", fake_react)
    monkeypatch.setattr(webapp, "trigger_pr_review_from_ref", fake_trigger)

    asyncio.run(
        webapp.process_github_pr_review_command(
            {
                "issue": {
                    "number": 1244,
                    "html_url": "https://github.com/langchain-ai/open-swe/pull/1244",
                    "pull_request": {"url": "..."},
                },
                "comment": {"id": 9, "body": "@open-swe review"},
                "repository": {"owner": {"login": "langchain-ai"}, "name": "open-swe"},
                "sender": {"login": "octocat", "id": 123},
            },
            "issue_comment",
            None,
        )
    )

    pr_ref = captured["pr_ref"]
    assert isinstance(pr_ref, GitHubPrRef)
    assert pr_ref.owner == "langchain-ai"
    assert pr_ref.repo == "open-swe"
    assert pr_ref.number == 1244
    assert pr_ref.url == "https://github.com/langchain-ai/open-swe/pull/1244"
    assert captured["source"] == "github"
    assert captured["github_login"] == "octocat"
    assert captured["github_user_id"] == 123
    assert captured.get("reacted") is True


def test_process_github_pr_review_command_uses_url_override(monkeypatch) -> None:
    captured: dict[str, object] = {}

    async def fake_get_app_token() -> str:
        return "app-token"

    async def fake_react(*args, **kwargs) -> None:
        return None

    async def fake_trigger(
        pr_ref: GitHubPrRef,
        *,
        source: str,
        github_login: str = "",
        github_user_id: int | None = None,
        slack_channel_id: str = "",
        slack_thread_ts: str = "",
    ) -> dict[str, object]:
        captured["pr_ref"] = pr_ref
        return {"success": True, "thread_id": "tid", "pr_url": pr_ref.url}

    monkeypatch.setattr(webapp, "get_github_app_installation_token", fake_get_app_token)
    monkeypatch.setattr(webapp, "react_to_github_comment", fake_react)
    monkeypatch.setattr(webapp, "trigger_pr_review_from_ref", fake_trigger)

    asyncio.run(
        webapp.process_github_pr_review_command(
            {
                "issue": {"number": 99, "pull_request": {"url": "..."}},
                "comment": {"id": 1, "body": "@open-swe review https://github.com/x/y/pull/7"},
                "repository": {"owner": {"login": "x"}, "name": "y"},
                "sender": {"login": "octocat", "id": 1},
            },
            "issue_comment",
            "https://github.com/x/y/pull/7",
        )
    )

    pr_ref = captured["pr_ref"]
    assert isinstance(pr_ref, GitHubPrRef)
    assert pr_ref.number == 7
    assert pr_ref.owner == "x"
    assert pr_ref.repo == "y"
