"""Tests for the public-repo org-membership gate on GitHub webhooks."""

from __future__ import annotations

import hashlib
import hmac
import json

import pytest
from fastapi.testclient import TestClient

from agent.api.app import app
from agent.webhooks import common as webhook_common
from agent.webhooks import github as github_webhooks

_TEST_WEBHOOK_SECRET = "test-secret-for-webhook"


def _sign_body(body: bytes, secret: str = _TEST_WEBHOOK_SECRET) -> str:
    sig = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return f"sha256={sig}"


def _post_github_webhook(client: TestClient, event_type: str, payload: dict) -> object:
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


def _install_membership_stub(monkeypatch, members: set[str]) -> dict[str, list[str]]:
    """Stub the org-membership lookup to return True only for ``members``."""
    seen: dict[str, list[str]] = {"calls": []}

    async def fake_is_user_active_org_member(username: str, org: str) -> bool:
        seen["calls"].append(username)
        return username in members

    monkeypatch.setattr(webhook_common, "is_user_active_org_member", fake_is_user_active_org_member)
    return seen


def _common_setup(monkeypatch, *, gate: str = "langchain-ai") -> None:
    monkeypatch.setattr(webhook_common, "GITHUB_WEBHOOK_SECRET", _TEST_WEBHOOK_SECRET)
    monkeypatch.setattr(webhook_common, "PUBLIC_REPO_ORG_GATE", gate)
    monkeypatch.setattr(webhook_common, "ALLOWED_GITHUB_ORGS", frozenset())


def test_gate_blocks_non_member_on_public_pr_comment(monkeypatch) -> None:
    _common_setup(monkeypatch)
    seen = _install_membership_stub(monkeypatch, members={"insider"})

    async def fake_process_github_pr_comment(*_args, **_kwargs) -> None:
        raise AssertionError("should not be called")

    monkeypatch.setattr(
        github_webhooks, "process_github_pr_comment", fake_process_github_pr_comment
    )

    client = TestClient(app)
    response = _post_github_webhook(
        client,
        "issue_comment",
        {
            "action": "created",
            "issue": {
                "id": 1,
                "number": 7,
                "title": "PR title",
                "pull_request": {
                    "url": "https://api.github.com/repos/langchain-ai/open-swe/pulls/7"
                },
            },
            "comment": {"body": "@open-swe review"},
            "repository": {
                "owner": {"login": "langchain-ai"},
                "name": "open-swe",
                "private": False,
            },
            "sender": {"login": "stranger", "type": "User"},
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ignored"
    assert "not a member" in body["reason"]
    assert seen["calls"] == ["stranger"]


def test_gate_allows_org_member_on_public_pr_comment(monkeypatch) -> None:
    _common_setup(monkeypatch)
    _install_membership_stub(monkeypatch, members={"insider"})

    called: dict[str, object] = {}

    async def fake_process_github_pr_comment(payload, event_type) -> None:
        called["event"] = event_type

    monkeypatch.setattr(
        github_webhooks, "process_github_pr_comment", fake_process_github_pr_comment
    )

    client = TestClient(app)
    response = _post_github_webhook(
        client,
        "issue_comment",
        {
            "action": "created",
            "issue": {
                "id": 1,
                "number": 7,
                "title": "PR title",
                "pull_request": {
                    "url": "https://api.github.com/repos/langchain-ai/open-swe/pulls/7"
                },
            },
            "comment": {"body": "@open-swe review"},
            "repository": {
                "owner": {"login": "langchain-ai"},
                "name": "open-swe",
                "private": False,
            },
            "sender": {"login": "insider", "type": "User"},
        },
    )

    assert response.status_code == 200
    assert response.json()["status"] == "accepted"
    assert called["event"] == "issue_comment"


def test_gate_skipped_on_private_repo(monkeypatch) -> None:
    _common_setup(monkeypatch)
    seen = _install_membership_stub(monkeypatch, members=set())

    called: dict[str, object] = {}

    async def fake_process_github_pr_comment(payload, event_type) -> None:
        called["event"] = event_type

    monkeypatch.setattr(
        github_webhooks, "process_github_pr_comment", fake_process_github_pr_comment
    )

    client = TestClient(app)
    response = _post_github_webhook(
        client,
        "issue_comment",
        {
            "action": "created",
            "issue": {
                "id": 1,
                "number": 7,
                "title": "PR title",
                "pull_request": {
                    "url": "https://api.github.com/repos/langchain-ai/open-swe/pulls/7"
                },
            },
            "comment": {"body": "@open-swe please look at this"},
            "repository": {
                "owner": {"login": "langchain-ai"},
                "name": "open-swe",
                "private": True,
            },
            "sender": {"login": "stranger", "type": "User"},
        },
    )

    assert response.status_code == 200
    assert response.json()["status"] == "accepted"
    assert called["event"] == "issue_comment"
    assert seen["calls"] == []


def test_gate_disabled_when_env_unset(monkeypatch) -> None:
    _common_setup(monkeypatch, gate="")
    seen = _install_membership_stub(monkeypatch, members=set())

    called: dict[str, object] = {}

    async def fake_process_github_pr_comment(payload, event_type) -> None:
        called["event"] = event_type

    monkeypatch.setattr(
        github_webhooks, "process_github_pr_comment", fake_process_github_pr_comment
    )

    client = TestClient(app)
    response = _post_github_webhook(
        client,
        "issue_comment",
        {
            "action": "created",
            "issue": {
                "id": 1,
                "number": 7,
                "title": "PR title",
                "pull_request": {
                    "url": "https://api.github.com/repos/langchain-ai/open-swe/pulls/7"
                },
            },
            "comment": {"body": "@open-swe please look at this"},
            "repository": {
                "owner": {"login": "langchain-ai"},
                "name": "open-swe",
                "private": False,
            },
            "sender": {"login": "stranger", "type": "User"},
        },
    )

    assert response.status_code == 200
    assert response.json()["status"] == "accepted"
    assert called["event"] == "issue_comment"
    assert seen["calls"] == []


def test_gate_blocks_non_member_on_public_issue(monkeypatch) -> None:
    _common_setup(monkeypatch)
    _install_membership_stub(monkeypatch, members={"insider"})

    async def fake_process_github_issue(*_args, **_kwargs) -> None:
        raise AssertionError("should not be called")

    monkeypatch.setattr(github_webhooks, "process_github_issue", fake_process_github_issue)

    client = TestClient(app)
    response = _post_github_webhook(
        client,
        "issues",
        {
            "action": "opened",
            "issue": {
                "id": 1,
                "number": 7,
                "title": "@openswe please help",
                "body": "x",
            },
            "repository": {
                "owner": {"login": "langchain-ai"},
                "name": "open-swe",
                "private": False,
            },
            "sender": {"login": "stranger", "type": "User"},
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ignored"
    assert "not a member" in body["reason"]


def test_review_requested_is_unsupported_before_public_repo_gate(monkeypatch) -> None:
    _common_setup(monkeypatch)
    seen = _install_membership_stub(monkeypatch, members={"insider"})

    client = TestClient(app)
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
            "repository": {
                "owner": {"login": "langchain-ai"},
                "name": "open-swe",
                "private": False,
            },
            "sender": {"login": "stranger", "type": "User"},
        },
    )

    assert response.status_code == 200
    assert response.json() == {
        "status": "ignored",
        "reason": "Unsupported GitHub pull_request action: review_requested",
    }
    assert seen["calls"] == []


def test_gate_allows_internal_bot_sender(monkeypatch) -> None:
    _common_setup(monkeypatch)
    seen = _install_membership_stub(monkeypatch, members=set())

    called: dict[str, object] = {}

    async def fake_process_github_pr_comment(payload, event_type) -> None:
        called["event"] = event_type

    monkeypatch.setattr(
        github_webhooks, "process_github_pr_comment", fake_process_github_pr_comment
    )

    client = TestClient(app)
    response = _post_github_webhook(
        client,
        "issue_comment",
        {
            "action": "created",
            "issue": {
                "id": 1,
                "number": 7,
                "title": "PR title",
                "pull_request": {
                    "url": "https://api.github.com/repos/langchain-ai/open-swe/pulls/7"
                },
            },
            "comment": {"body": "@open-swe please look at this"},
            "repository": {
                "owner": {"login": "langchain-ai"},
                "name": "open-swe",
                "private": False,
            },
            "sender": {"login": "open-swe[bot]", "type": "Bot"},
        },
    )

    assert response.status_code == 200
    assert response.json()["status"] == "accepted"
    assert called["event"] == "issue_comment"
    assert seen["calls"] == []


@pytest.mark.asyncio
async def test_is_user_active_org_member_returns_false_when_no_token(monkeypatch) -> None:
    from agent.utils import github_org_membership

    async def fake_token() -> None:
        return None

    monkeypatch.setattr(github_org_membership, "get_github_app_installation_token", fake_token)

    assert await github_org_membership.is_user_active_org_member("alice", "langchain-ai") is False


class _FakeAsyncClient:
    def __init__(self, response) -> None:
        self._response = response

    async def __aenter__(self) -> _FakeAsyncClient:
        return self

    async def __aexit__(self, *_args) -> None:
        return None

    async def get(self, *_args, **_kwargs):
        return self._response


class _FakeResponse:
    def __init__(self, status_code: int, payload: dict | None = None) -> None:
        self.status_code = status_code
        self._payload = payload or {}

    def json(self) -> dict:
        return self._payload


def _patch_membership_http(monkeypatch, response: _FakeResponse) -> None:
    from agent.utils import github_org_membership

    async def fake_token() -> str:
        return "x"

    monkeypatch.setattr(github_org_membership, "get_github_app_installation_token", fake_token)

    def factory(*_args, **_kwargs) -> _FakeAsyncClient:
        return _FakeAsyncClient(response)

    monkeypatch.setattr(github_org_membership.httpx, "AsyncClient", factory)


@pytest.mark.asyncio
async def test_is_user_active_org_member_handles_404(monkeypatch) -> None:
    from agent.utils import github_org_membership

    _patch_membership_http(monkeypatch, _FakeResponse(404))

    assert await github_org_membership.is_user_active_org_member("alice", "langchain-ai") is False


@pytest.mark.asyncio
async def test_is_user_active_org_member_active(monkeypatch) -> None:
    from agent.utils import github_org_membership

    _patch_membership_http(monkeypatch, _FakeResponse(200, {"state": "active"}))

    assert await github_org_membership.is_user_active_org_member("alice", "langchain-ai") is True


@pytest.mark.asyncio
async def test_is_user_active_org_member_pending_returns_false(monkeypatch) -> None:
    from agent.utils import github_org_membership

    _patch_membership_http(monkeypatch, _FakeResponse(200, {"state": "pending"}))

    assert await github_org_membership.is_user_active_org_member("alice", "langchain-ai") is False
