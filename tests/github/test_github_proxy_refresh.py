"""Tests for mid-run GitHub proxy token refresh."""

from collections.abc import Generator
from datetime import UTC, datetime, timedelta
from typing import cast
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from langchain.agents.middleware import AgentState

from agent.github import proxy as github_proxy
from agent.github.proxy import (
    PROXY_TOKEN_FALLBACK_TTL,
    clear_proxy_token_expiry,
    maybe_refresh_proxy_token,
    proxy_token_needs_refresh,
    record_proxy_token_expiry,
)


@pytest.fixture(autouse=True)
def _clear_state() -> Generator[None]:
    github_proxy._PROXY_TOKEN_EXPIRY.clear()
    github_proxy._PROXY_BASE_CONFIGS.clear()
    github_proxy._PROXY_WORKSPACES.clear()
    yield
    github_proxy._PROXY_TOKEN_EXPIRY.clear()
    github_proxy._PROXY_BASE_CONFIGS.clear()
    github_proxy._PROXY_WORKSPACES.clear()


class TestProxyTokenNeedsRefresh:
    def test_false_when_no_record(self) -> None:
        assert proxy_token_needs_refresh("thread-1") is False

    def test_false_when_thread_id_missing(self) -> None:
        assert proxy_token_needs_refresh(None) is False

    def test_true_when_near_expiry(self) -> None:
        now = datetime(2025, 1, 1, 12, 0, 0, tzinfo=UTC)
        record_proxy_token_expiry("thread-1", now + timedelta(minutes=2))
        assert proxy_token_needs_refresh("thread-1", now=now) is True

    def test_false_when_far_from_expiry(self) -> None:
        now = datetime(2025, 1, 1, 12, 0, 0, tzinfo=UTC)
        record_proxy_token_expiry("thread-1", now + timedelta(minutes=55))
        assert proxy_token_needs_refresh("thread-1", now=now) is False

    def test_parses_iso_z_suffix(self) -> None:
        now = datetime(2025, 1, 1, 12, 0, 0, tzinfo=UTC)
        record_proxy_token_expiry("thread-1", "2025-01-01T12:03:00Z")
        assert proxy_token_needs_refresh("thread-1", now=now) is True

    def test_fallback_ttl_when_expiry_unknown(self) -> None:
        now = datetime(2025, 1, 1, 12, 0, 0, tzinfo=UTC)
        record_proxy_token_expiry("thread-1", None)
        github_proxy._PROXY_TOKEN_EXPIRY["thread-1"] = (None, now, None, ())
        assert proxy_token_needs_refresh("thread-1", now=now) is False
        later = now + PROXY_TOKEN_FALLBACK_TTL
        assert proxy_token_needs_refresh("thread-1", now=later) is True

    def test_clear_removes_record(self) -> None:
        record_proxy_token_expiry("thread-1", datetime.now(UTC))
        clear_proxy_token_expiry("thread-1")
        assert proxy_token_needs_refresh("thread-1") is False


class TestMaybeRefreshProxyToken:
    @pytest.mark.asyncio
    async def test_skips_when_not_langsmith(self) -> None:
        now = datetime(2025, 1, 1, 12, 0, 0, tzinfo=UTC)
        record_proxy_token_expiry("thread-1", now + timedelta(minutes=1))
        with patch.dict("os.environ", {"SANDBOX_TYPE": "local"}):
            assert await maybe_refresh_proxy_token("thread-1", now=now) is False

    @pytest.mark.asyncio
    async def test_skips_when_not_near_expiry(self) -> None:
        now = datetime(2025, 1, 1, 12, 0, 0, tzinfo=UTC)
        record_proxy_token_expiry("thread-1", now + timedelta(minutes=55))
        with patch.dict("os.environ", {"SANDBOX_TYPE": "langsmith"}):
            assert await maybe_refresh_proxy_token("thread-1", now=now) is False

    @pytest.mark.asyncio
    async def test_skips_when_no_sandbox(self) -> None:
        now = datetime(2025, 1, 1, 12, 0, 0, tzinfo=UTC)
        record_proxy_token_expiry("thread-1", now + timedelta(minutes=1))
        with (
            patch.dict("os.environ", {"SANDBOX_TYPE": "langsmith"}),
            patch.dict(github_proxy.SANDBOX_BACKENDS, {}, clear=True),
        ):
            assert await maybe_refresh_proxy_token("thread-1", now=now) is False


class TestRefreshGithubProxyMiddleware:
    @pytest.mark.asyncio
    async def test_calls_refresh_with_thread_id(self) -> None:
        from agent.middleware.refresh_github_proxy import refresh_github_proxy_before_model

        with (
            patch(
                "agent.middleware.refresh_github_proxy.get_config",
                return_value={"configurable": {"thread_id": "thread-9"}},
            ),
            patch(
                "agent.middleware.refresh_github_proxy.maybe_refresh_proxy_token",
                new=AsyncMock(return_value=True),
            ) as mock_refresh,
        ):
            result = await refresh_github_proxy_before_model.abefore_model(
                cast(AgentState, {}), MagicMock()
            )

        assert result is None
        mock_refresh.assert_awaited_once_with("thread-9")

    @pytest.mark.asyncio
    async def test_no_thread_id_is_noop(self) -> None:
        from agent.middleware.refresh_github_proxy import refresh_github_proxy_before_model

        with (
            patch(
                "agent.middleware.refresh_github_proxy.get_config",
                return_value={"configurable": {}},
            ),
            patch(
                "agent.middleware.refresh_github_proxy.maybe_refresh_proxy_token",
                new=AsyncMock(),
            ) as mock_refresh,
        ):
            result = await refresh_github_proxy_before_model.abefore_model(
                cast(AgentState, {}), MagicMock()
            )

        assert result is None
        mock_refresh.assert_not_called()

    @pytest.mark.asyncio
    async def test_swallows_refresh_errors(self) -> None:
        from agent.middleware.refresh_github_proxy import refresh_github_proxy_before_model

        with (
            patch(
                "agent.middleware.refresh_github_proxy.get_config",
                return_value={"configurable": {"thread_id": "thread-9"}},
            ),
            patch(
                "agent.middleware.refresh_github_proxy.maybe_refresh_proxy_token",
                new=AsyncMock(side_effect=RuntimeError("boom")),
            ),
        ):
            result = await refresh_github_proxy_before_model.abefore_model(
                cast(AgentState, {}), MagicMock()
            )

        assert result is None
