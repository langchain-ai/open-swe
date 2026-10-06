"""Tests for TTL + revocation handling on cached GitHub OAuth tokens.

Covers:
- (a) expired-cache reads return None / fall through to re-auth
- (b) 401 on a downstream GitHub call invalidates the cached token and
  triggers a fresh resolve in the webapp
- (c) ``publish_review`` invalidates the cached token and returns a clean
  failure when GitHub responds 401
"""

import asyncio
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException

from agent.github import comments as github_comments
from agent.github import thread_token as github_token
from agent.github import webhook as github_webhooks
from agent.users import User
from agent.webhooks import common as webhook_common


@pytest.fixture(autouse=True)
def _clear_token_cache() -> None:
    github_token._GITHUB_TOKEN_CACHE.clear()


# (a) expired-cache reads -----------------------------------------------------


def test_get_github_token_returns_none_for_expired_cache() -> None:
    past = (datetime.now(UTC) - timedelta(hours=1)).isoformat()
    github_token.cache_github_token_for_thread(
        "tid", "ghp_secret", expires_at=past, is_bot_token=True
    )
    assert github_token.get_github_token({"configurable": {"thread_id": "tid"}}) is None


def test_user_token_cache_is_bound_to_github_login() -> None:
    principal = github_token.github_token_principal(login=" Alice ")
    github_token.cache_github_token_for_thread("shared-thread", "alice-token", principal=principal)

    alice_config = {"configurable": {"thread_id": "shared-thread", "github_login": "ALICE"}}
    bob_config = {"configurable": {"thread_id": "shared-thread", "github_login": "bob"}}

    assert github_token.get_github_token(alice_config) == "alice-token"
    assert github_token.get_github_token(bob_config) is None


def test_unbound_user_token_is_not_cached() -> None:
    github_token.cache_github_token_for_thread("tid", "unbound-token")
    assert github_token._GITHUB_TOKEN_CACHE == {}


def test_cached_token_expires_after_max_ttl() -> None:
    """A token with no/far expiry is still dropped once it's older than the 24h cap."""
    far_future = (datetime.now(UTC) + timedelta(days=30)).isoformat()
    old_cached_at = datetime.now(UTC) - timedelta(hours=25)
    github_token._GITHUB_TOKEN_CACHE[("tid", github_token._BOT_PRINCIPAL)] = (
        github_token._CachedToken("ghp_secret", far_future, old_cached_at)
    )
    assert github_token.get_github_token({"configurable": {"thread_id": "tid"}}) is None


# (b) 401 on a downstream GitHub call -----------------------------------------


class _MockResponse:
    def __init__(self, status_code: int, json_data: Any | None = None) -> None:
        self.status_code = status_code
        self._json = json_data or {}

    def json(self) -> Any:
        return self._json


class _MockHttpxClient:
    def __init__(self, status_code: int, json_data: Any | None = None) -> None:
        self.status_code = status_code
        self.json_data = json_data
        self.posts: list[dict[str, Any]] = []
        self.gets: list[dict[str, Any]] = []

    async def __aenter__(self) -> _MockHttpxClient:
        return self

    async def __aexit__(self, *args: Any) -> None:
        return None

    async def post(self, url: str, **kwargs: Any) -> _MockResponse:
        self.posts.append({"url": url, **kwargs})
        return _MockResponse(self.status_code, self.json_data)

    async def get(self, url: str, **kwargs: Any) -> _MockResponse:
        self.gets.append({"url": url, **kwargs})
        return _MockResponse(self.status_code, self.json_data)


# (c) successful re-auth following stale-cache invalidation -------------------


async def test_private_pr_followup_rejected_before_credentials_or_dispatch(monkeypatch):
    thread_id = "00000000-0000-0000-0000-000000000001"
    client = MagicMock()
    client.threads.get = AsyncMock(
        return_value={
            "metadata": {"source": "dashboard", "visibility": "private", "owner_login": "alice"}
        }
    )
    monkeypatch.setattr(webhook_common, "get_client", lambda **kwargs: client)
    monkeypatch.setattr(
        webhook_common,
        "extract_pr_context",
        AsyncMock(
            return_value=(
                {"owner": "o", "name": "r"},
                7,
                f"open-swe/{thread_id}",
                "bob",
                "",
                42,
                None,
            )
        ),
    )
    monkeypatch.setattr(User, "email_for_login", AsyncMock(return_value="bob@example.com"))
    token = AsyncMock()
    dispatch = AsyncMock()
    monkeypatch.setattr(webhook_common, "get_or_resolve_thread_github_token", token)
    monkeypatch.setattr(webhook_common, "dispatch_agent_run", dispatch)
    with pytest.raises(HTTPException, match="thread not found"):
        await github_webhooks.process_github_pr_comment({}, "issue_comment")
    with pytest.raises(HTTPException, match="thread not found"):
        await webhook_common.trigger_or_queue_run(
            thread_id,
            "leak transcript",
            github_login="bob",
            github_user_id=2,
            repo_config={"owner": "o", "name": "r"},
            pr_number=7,
        )
    token.assert_not_awaited()
    dispatch.assert_not_awaited()
    await webhook_common.authorize_github_thread(thread_id, "ALICE")
    client.threads.get.side_effect = RuntimeError("store unavailable")
    with pytest.raises(RuntimeError, match="store unavailable"):
        await github_webhooks.process_github_pr_comment({}, "issue_comment")
    token.assert_not_awaited()
    dispatch.assert_not_awaited()


def test_process_github_pr_comment_invalidates_and_reauths_on_401(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """End-to-end check: a 401 on react triggers invalidate + re-resolve."""

    invalidated: dict[str, int] = {"calls": 0}
    resolves: list[str] = []
    react_calls: list[str] = []
    fetch_calls: list[str] = []

    async def fake_invalidate(thread_id: str) -> None:
        invalidated["calls"] += 1

    tokens = iter(["stale-token", "fresh-token"])

    async def fake_get_or_resolve(thread_id: str, email: str) -> str | None:
        token = next(tokens)
        resolves.append(token)
        return token

    async def fake_react(
        repo_config: dict[str, str],
        comment_id: int,
        *,
        event_type: str,
        token: str,
        pull_number: int | None = None,
        node_id: str | None = None,
    ) -> bool:
        react_calls.append(token)
        if token == "stale-token":
            raise github_comments.GitHubAuthError("revoked")
        return True

    async def fake_fetch_pr_comments(
        repo_config: dict[str, str],
        pr_number: int,
        *,
        token: str,
        event_comment: dict[str, Any],
        authorized_login: str | None = None,
    ) -> list[dict[str, Any]]:
        fetch_calls.append(token)
        return [
            {"body": "@openswe please look", "author": "octo", "created_at": "2026-01-01T00:00:00Z"}
        ]

    async def fake_extract_pr_context(
        payload: dict[str, Any], event_type: str
    ) -> tuple[dict[str, str], int, str, str, str, int, str | None]:
        return (
            {"owner": "o", "name": "r"},
            7,
            "open-swe/00000000-0000-0000-0000-000000000001",
            "octo",
            "https://github.com/o/r/pull/7",
            42,
            None,
        )

    async def fake_trigger_or_queue_run(*args: Any, **kwargs: Any) -> None:
        return None

    client = MagicMock()
    client.threads.get = AsyncMock(return_value={"metadata": {"source": "github"}})
    monkeypatch.setattr(webhook_common, "get_client", lambda **kwargs: client)
    monkeypatch.setattr(webhook_common, "extract_pr_context", fake_extract_pr_context)
    monkeypatch.setattr(webhook_common, "get_or_resolve_thread_github_token", fake_get_or_resolve)
    monkeypatch.setattr(webhook_common, "invalidate_cached_github_token", fake_invalidate)
    monkeypatch.setattr(webhook_common, "react_to_github_comment", fake_react)
    monkeypatch.setattr(webhook_common, "fetch_pr_comments_since_last_tag", fake_fetch_pr_comments)
    monkeypatch.setattr(webhook_common, "trigger_or_queue_run", fake_trigger_or_queue_run)
    monkeypatch.setattr(
        User,
        "email_for_login",
        lambda login: asyncio.sleep(0, result="octo@example.com" if login == "octo" else None),
    )
    monkeypatch.setattr(User, "known_logins", lambda logins: asyncio.sleep(0, result=frozenset()))

    asyncio.run(
        github_webhooks.process_github_pr_comment(
            {
                "sender": {"login": "octo", "id": 1},
                "comment": {
                    "id": 42,
                    "user": {"login": "octo"},
                    "body": "@openswe please look",
                    "created_at": "2026-01-01T00:00:00Z",
                },
            },
            "issue_comment",
        )
    )

    assert invalidated["calls"] == 1
    assert resolves == ["stale-token", "fresh-token"]
    assert react_calls == ["stale-token", "fresh-token"]
    assert fetch_calls == ["stale-token"]


@pytest.mark.asyncio
async def test_publish_review_invalidates_cached_token_on_401(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import importlib

    publish_review_module = importlib.import_module("agent.tools.publish_review")

    invalidated: dict[str, int] = {"calls": 0}

    async def fake_invalidate(thread_id: str) -> None:
        invalidated["calls"] += 1
        invalidated["thread_id"] = thread_id  # type: ignore[assignment]

    async def fake_publish(*args: Any, **kwargs: Any) -> dict[str, Any]:
        raise github_token.GitHubAuthError("401 from PR review")

    monkeypatch.setattr(
        publish_review_module,
        "get_config",
        lambda: {
            "configurable": {
                "repo": {"owner": "o", "name": "r"},
                "pr_number": 7,
                "head_sha": "deadbeef",
            },
        },
    )
    monkeypatch.setattr(
        publish_review_module,
        "resolve_thread_github_token",
        AsyncMock(return_value="revoked-token"),
    )
    monkeypatch.setattr(publish_review_module, "invalidate_cached_github_token", fake_invalidate)
    monkeypatch.setattr(publish_review_module, "_publish_review_async", fake_publish)
    monkeypatch.setattr(publish_review_module, "get_thread_id_from_runtime", lambda: "thread-xyz")
    monkeypatch.setattr(publish_review_module, "_record_ranking", AsyncMock(return_value=None))

    result = await publish_review_module.publish_review(ranking=[])
    assert result["success"] is False
    assert "401" in result["error"]
    assert invalidated["calls"] == 1
    assert invalidated.get("thread_id") == "thread-xyz"


@pytest.mark.asyncio
async def test_expired_bot_token_is_re_minted_at_its_original_scope(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = {"configurable": {"thread_id": "tid"}}
    expired = (datetime.now(UTC) - timedelta(minutes=1)).isoformat()
    github_token.cache_github_token_for_thread(
        "tid", "stale-token", expires_at=expired, is_bot_token=True, repositories=["r"]
    )
    fresh_expiry = (datetime.now(UTC) + timedelta(hours=1)).isoformat()
    mint = AsyncMock(return_value=("fresh-token", fresh_expiry))
    monkeypatch.setattr(github_token, "get_github_app_installation_token_with_expiry", mint)

    assert await github_token.resolve_thread_github_token(config) == "fresh-token"
    assert await github_token.resolve_thread_github_token(config) == "fresh-token"
    mint.assert_awaited_once_with(repositories=("r",))


@pytest.mark.asyncio
async def test_expired_user_token_is_never_replaced_with_a_bot_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    principal = github_token.github_token_principal(login="alice")
    config = {"configurable": {"thread_id": "tid", "github_login": "alice"}}
    expired = (datetime.now(UTC) - timedelta(minutes=1)).isoformat()
    github_token.cache_github_token_for_thread(
        "tid", "alice-token", expires_at=expired, principal=principal
    )
    mint = AsyncMock()
    monkeypatch.setattr(github_token, "get_github_app_installation_token_with_expiry", mint)

    assert await github_token.resolve_thread_github_token(config) is None
    mint.assert_not_awaited()
