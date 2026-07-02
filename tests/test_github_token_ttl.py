"""Tests for TTL + revocation handling on cached GitHub OAuth tokens.

Covers:
- (a) expired-cache reads return None / fall through to re-auth
- (b) 401 on a downstream GitHub call invalidates the cached token and
  triggers a fresh resolve in the webapp
- (c) ``publish_review`` invalidates the cached token and returns a clean
  failure when GitHub responds 401
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest

from agent.utils import github_comments, github_token


@pytest.fixture(autouse=True)
def _clear_token_cache() -> None:
    github_token._GITHUB_TOKEN_CACHE.clear()


# (a) expired-cache reads -----------------------------------------------------


def test_is_expired_handles_iso_zulu_strings() -> None:
    past = (datetime.now(UTC) - timedelta(hours=1)).isoformat().replace("+00:00", "Z")
    future = (datetime.now(UTC) + timedelta(hours=1)).isoformat().replace("+00:00", "Z")
    assert github_token._is_expired(past) is True
    assert github_token._is_expired(future) is False


def test_is_expired_handles_unix_timestamps() -> None:
    past = (datetime.now(UTC) - timedelta(hours=1)).timestamp()
    future = (datetime.now(UTC) + timedelta(hours=1)).timestamp()
    assert github_token._is_expired(past) is True
    assert github_token._is_expired(future) is False


def test_is_expired_treats_unparseable_as_not_expired() -> None:
    assert github_token._is_expired(None) is False
    assert github_token._is_expired("") is False
    assert github_token._is_expired("not-a-date") is False


def test_get_github_token_returns_none_for_expired_cache() -> None:
    past = (datetime.now(UTC) - timedelta(hours=1)).isoformat()
    github_token.cache_github_token_for_thread("tid", "ghp_secret", expires_at=past)
    assert github_token.get_github_token({"configurable": {"thread_id": "tid"}}) is None


def test_get_github_token_returns_fresh_cached_token() -> None:
    future = (datetime.now(UTC) + timedelta(hours=1)).isoformat()
    github_token.cache_github_token_for_thread("tid", "ghp_secret", expires_at=future)
    assert github_token.get_github_token({"configurable": {"thread_id": "tid"}}) == "ghp_secret"


def test_get_github_token_returns_cached_token_when_no_expires_at() -> None:
    github_token.cache_github_token_for_thread("tid", "ghp_secret")
    assert github_token.get_github_token({"configurable": {"thread_id": "tid"}}) == "ghp_secret"


def test_cached_token_expires_after_max_ttl() -> None:
    """A token with no/far expiry is still dropped once it's older than the 24h cap."""
    far_future = (datetime.now(UTC) + timedelta(days=30)).isoformat()
    old_cached_at = datetime.now(UTC) - timedelta(hours=25)
    github_token._GITHUB_TOKEN_CACHE["tid"] = ("ghp_secret", far_future, old_cached_at)
    assert github_token.get_github_token({"configurable": {"thread_id": "tid"}}) is None


def test_cache_write_sweeps_other_expired_entries() -> None:
    """Writing one entry evicts unrelated entries that have passed their expiry."""
    past = (datetime.now(UTC) - timedelta(hours=1)).isoformat()
    github_token.cache_github_token_for_thread("stale", "ghp_stale", expires_at=past)
    github_token.cache_github_token_for_thread("fresh", "ghp_fresh")
    assert "stale" not in github_token._GITHUB_TOKEN_CACHE
    assert "fresh" in github_token._GITHUB_TOKEN_CACHE


@pytest.mark.asyncio
async def test_get_github_token_from_thread_skips_expired() -> None:
    past = (datetime.now(UTC) - timedelta(hours=1)).isoformat()
    github_token.cache_github_token_for_thread("tid", "ghp_revoked", expires_at=past)
    token, expires_at = await github_token.get_github_token_from_thread("tid")
    assert token is None
    assert expires_at is None


@pytest.mark.asyncio
async def test_get_github_token_from_thread_returns_fresh() -> None:
    future = (datetime.now(UTC) + timedelta(hours=1)).isoformat()
    github_token.cache_github_token_for_thread("tid", "ghp_live", expires_at=future)
    token, expires_at = await github_token.get_github_token_from_thread("tid")
    assert token == "ghp_live"
    assert expires_at == future


@pytest.mark.asyncio
async def test_invalidate_cached_github_token_clears_cache() -> None:
    github_token.cache_github_token_for_thread("tid-42", "ghp_live")
    await github_token.invalidate_cached_github_token("tid-42")
    token, expires_at = await github_token.get_github_token_from_thread("tid-42")
    assert token is None
    assert expires_at is None


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


def test_react_to_github_comment_raises_on_401(monkeypatch: pytest.MonkeyPatch) -> None:
    mock_client = _MockHttpxClient(status_code=401)
    monkeypatch.setattr(httpx, "AsyncClient", lambda *a, **kw: mock_client)

    async def _run() -> None:
        await github_comments.react_to_github_comment(
            {"owner": "o", "name": "r"},
            comment_id=1,
            event_type="issue_comment",
            token="revoked",
        )

    with pytest.raises(github_token.GitHubAuthError):
        asyncio.run(_run())


def test_fetch_pr_comments_since_last_tag_raises_on_401(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    mock_client = _MockHttpxClient(status_code=401)
    monkeypatch.setattr(httpx, "AsyncClient", lambda *a, **kw: mock_client)

    async def _run() -> None:
        await github_comments.fetch_pr_comments_since_last_tag(
            {"owner": "o", "name": "r"},
            pr_number=42,
            token="revoked",
        )

    with pytest.raises(github_token.GitHubAuthError):
        asyncio.run(_run())


def test_fetch_issue_comments_raises_on_401(monkeypatch: pytest.MonkeyPatch) -> None:
    mock_client = _MockHttpxClient(status_code=401)
    monkeypatch.setattr(httpx, "AsyncClient", lambda *a, **kw: mock_client)

    async def _run() -> None:
        await github_comments.fetch_issue_comments(
            {"owner": "o", "name": "r"},
            issue_number=42,
            token="revoked",
        )

    with pytest.raises(github_token.GitHubAuthError):
        asyncio.run(_run())


# (c) successful re-auth following stale-cache invalidation -------------------


def test_process_github_pr_comment_invalidates_and_reauths_on_401(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """End-to-end check: a 401 on react triggers invalidate + re-resolve."""
    from agent import webapp

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
        repo_config: dict[str, str], pr_number: int, *, token: str
    ) -> list[dict[str, Any]]:
        fetch_calls.append(token)
        return [
            {
                "body": "@jarvis-aeteq please look",
                "author": "octo",
                "created_at": "2026-01-01T00:00:00Z",
            }
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

    monkeypatch.setattr(webapp, "extract_pr_context", fake_extract_pr_context)
    monkeypatch.setattr(webapp, "_get_or_resolve_thread_github_token", fake_get_or_resolve)
    monkeypatch.setattr(webapp, "invalidate_cached_github_token", fake_invalidate)
    monkeypatch.setattr(webapp, "react_to_github_comment", fake_react)
    monkeypatch.setattr(webapp, "fetch_pr_comments_since_last_tag", fake_fetch_pr_comments)
    monkeypatch.setattr(webapp, "_trigger_or_queue_run", fake_trigger_or_queue_run)
    monkeypatch.setattr(
        webapp,
        "email_for_login",
        lambda login: asyncio.sleep(0, result="octo@example.com" if login == "octo" else None),
    )

    asyncio.run(
        webapp.process_github_pr_comment(
            {"sender": {"login": "octo", "id": 1}},
            "issue_comment",
        )
    )

    assert invalidated["calls"] == 1
    assert resolves == ["stale-token", "fresh-token"]
    assert react_calls == ["stale-token", "fresh-token"]
    assert fetch_calls == ["fresh-token"]


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
    monkeypatch.setattr(publish_review_module, "get_github_token", lambda: "revoked-token")
    monkeypatch.setattr(publish_review_module, "invalidate_cached_github_token", fake_invalidate)
    monkeypatch.setattr(publish_review_module, "_publish_review_async", fake_publish)
    monkeypatch.setattr(publish_review_module, "get_thread_id_from_runtime", lambda: "thread-xyz")

    result = await publish_review_module.publish_review()
    assert result["success"] is False
    assert "401" in result["error"]
    assert invalidated["calls"] == 1
    assert invalidated.get("thread_id") == "thread-xyz"
