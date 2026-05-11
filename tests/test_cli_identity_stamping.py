"""Tests that webhook handlers call ``upsert_identity`` with the right surface."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from agent import webapp


class _FakeThreads:
    def __init__(self) -> None:
        self.create = AsyncMock(return_value=None)


class _FakeRuns:
    def __init__(self) -> None:
        self.create = AsyncMock(return_value={"run_id": "run-1"})
        self.list = AsyncMock(return_value=[])


class _FakeStore:
    def __init__(self) -> None:
        self.items: dict[Any, Any] = {}

    async def get_item(self, namespace: Any, key: str) -> None:  # noqa: ARG002
        return None

    async def put_item(self, namespace: Any, key: str, value: Any) -> None:  # noqa: ARG002
        self.items[(namespace, key)] = value


class _FakeLangGraphClient:
    def __init__(self) -> None:
        self.threads = _FakeThreads()
        self.runs = _FakeRuns()
        self.store = _FakeStore()


@pytest.fixture
def fake_lg_client(monkeypatch: pytest.MonkeyPatch) -> _FakeLangGraphClient:
    client = _FakeLangGraphClient()
    monkeypatch.setattr(webapp, "get_client", lambda url=None: client)  # noqa: ARG005
    return client


async def test_process_linear_issue_calls_upsert_identity(
    monkeypatch: pytest.MonkeyPatch, fake_lg_client: _FakeLangGraphClient
) -> None:
    upsert_mock = AsyncMock(return_value=None)
    monkeypatch.setattr(webapp, "upsert_identity", upsert_mock)
    monkeypatch.setattr(webapp, "react_to_linear_comment", AsyncMock(return_value=True))
    monkeypatch.setattr(
        webapp,
        "fetch_linear_issue_details",
        AsyncMock(
            return_value={
                "id": "issue-1",
                "title": "T",
                "description": "D",
                "url": "https://linear.app/foo/issue-1",
                "identifier": "ENG-1",
                "comments": {"nodes": []},
                "creator": {"id": "linear-user-1", "email": "alice@example.com", "name": "Alice"},
            }
        ),
    )
    monkeypatch.setattr(webapp, "is_thread_active", AsyncMock(return_value=False))
    monkeypatch.setattr(webapp, "queue_message_for_thread", AsyncMock(return_value=True))

    await webapp.process_linear_issue(
        {
            "id": "issue-1",
            "url": "https://linear.app/foo/issue-1",
            "identifier": "ENG-1",
            "comment_author": {
                "id": "linear-user-1",
                "email": "alice@example.com",
                "name": "Alice",
            },
        },
        {"owner": "acme", "name": "repo"},
    )

    assert upsert_mock.await_count >= 1
    found = False
    for call in upsert_mock.await_args_list:
        if call.kwargs.get("surface") == "linear":
            assert call.args[0] == "alice@example.com"
            assert call.kwargs.get("linear_user_id") == "linear-user-1"
            found = True
    assert found, "Expected upsert_identity to be called with surface='linear'"


async def test_process_slack_mention_calls_upsert_identity(
    monkeypatch: pytest.MonkeyPatch, fake_lg_client: _FakeLangGraphClient
) -> None:
    upsert_mock = AsyncMock(return_value=None)
    monkeypatch.setattr(webapp, "upsert_identity", upsert_mock)
    monkeypatch.setattr(webapp, "add_slack_reaction", AsyncMock(return_value=True))
    monkeypatch.setattr(webapp, "set_slack_assistant_status", AsyncMock(return_value=None))
    monkeypatch.setattr(
        webapp,
        "get_slack_user_info",
        AsyncMock(
            return_value={
                "profile": {"email": "bob@example.com", "display_name": "Bob"},
                "real_name": "Bob",
            }
        ),
    )
    monkeypatch.setattr(
        webapp,
        "fetch_slack_thread_messages",
        AsyncMock(return_value=[{"ts": "1.000", "text": "hello", "user": "U123"}]),
    )
    monkeypatch.setattr(webapp, "get_slack_user_names", AsyncMock(return_value={}))
    monkeypatch.setattr(
        webapp,
        "resolve_slack_links_in_context",
        AsyncMock(return_value=("", [])),
    )
    monkeypatch.setattr(webapp, "_thread_exists", AsyncMock(return_value=False))
    monkeypatch.setattr(webapp, "_upsert_slack_thread_repo_metadata", AsyncMock(return_value=None))
    monkeypatch.setattr(webapp, "is_thread_active", AsyncMock(return_value=False))
    monkeypatch.setattr(webapp, "queue_message_for_thread", AsyncMock(return_value=True))
    # Disable the slack_run mapping path which would call other helpers.
    monkeypatch.setattr(webapp, "store_slack_run_mapping", AsyncMock(return_value=None))

    await webapp.process_slack_mention(
        {
            "channel_id": "C123",
            "thread_ts": "1.000",
            "event_ts": "1.000",
            "user_id": "U123",
            "text": "<@BOT> hello",
            "bot_user_id": "BOT",
            "team_id": "T123",
        },
        {"owner": "acme", "name": "repo"},
    )

    found = False
    for call in upsert_mock.await_args_list:
        if call.kwargs.get("surface") == "slack":
            assert call.args[0] == "bob@example.com"
            assert call.kwargs.get("slack_user_id") == "U123"
            found = True
    assert found, "Expected upsert_identity to be called with surface='slack'"


async def test_process_github_issue_calls_upsert_identity(
    monkeypatch: pytest.MonkeyPatch, fake_lg_client: _FakeLangGraphClient
) -> None:
    upsert_mock = AsyncMock(return_value=None)
    monkeypatch.setattr(webapp, "upsert_identity", upsert_mock)
    monkeypatch.setitem(webapp.GITHUB_USER_EMAIL_MAP, "carol", "carol@example.com")
    monkeypatch.setattr(webapp, "_thread_exists", AsyncMock(return_value=False))
    monkeypatch.setattr(
        webapp,
        "_get_or_resolve_thread_github_token",
        AsyncMock(return_value="ghp_test"),
    )
    monkeypatch.setattr(
        webapp, "get_github_app_installation_token", AsyncMock(return_value="ghs_test")
    )
    monkeypatch.setattr(webapp, "fetch_issue_comments", AsyncMock(return_value=[]))
    monkeypatch.setattr(webapp, "build_github_issue_prompt", MagicMock(return_value="prompt"))
    monkeypatch.setattr(webapp, "is_thread_active", AsyncMock(return_value=False))
    monkeypatch.setattr(webapp, "queue_message_for_thread", AsyncMock(return_value=True))

    payload = {
        "issue": {
            "id": 999,
            "number": 42,
            "title": "Bug",
            "body": "broken",
            "html_url": "https://github.com/acme/repo/issues/42",
            "user": {"login": "carol"},
        },
        "repository": {"owner": {"login": "acme"}, "name": "repo"},
        "sender": {"login": "carol", "id": 12345},
    }
    await webapp.process_github_issue(payload, "issues")

    found = False
    for call in upsert_mock.await_args_list:
        if call.kwargs.get("surface") == "github":
            assert call.args[0] == "carol@example.com"
            assert call.kwargs.get("github_login") == "carol"
            found = True
    assert found, "Expected upsert_identity to be called with surface='github'"
