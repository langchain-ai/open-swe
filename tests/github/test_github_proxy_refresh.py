"""Tests for mid-run GitHub proxy token refresh."""

from collections.abc import Generator
from datetime import UTC, datetime, timedelta
from typing import cast
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from langchain.agents.middleware import AgentState

from openswe.github import proxy as github_proxy
from openswe.github.proxy import (
    PROXY_TOKEN_FALLBACK_TTL,
    proxy_token_needs_refresh,
    proxy_token_set_since,
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
    def test_true_when_near_expiry(self) -> None:
        now = datetime(2025, 1, 1, 12, 0, 0, tzinfo=UTC)
        record_proxy_token_expiry("thread-1", now + timedelta(minutes=2))
        assert proxy_token_needs_refresh("thread-1", now=now) is True

    def test_false_when_far_from_expiry(self) -> None:
        now = datetime(2025, 1, 1, 12, 0, 0, tzinfo=UTC)
        record_proxy_token_expiry("thread-1", now + timedelta(minutes=55))
        assert proxy_token_needs_refresh("thread-1", now=now) is False

    def test_fallback_ttl_when_expiry_unknown(self) -> None:
        now = datetime(2025, 1, 1, 12, 0, 0, tzinfo=UTC)
        record_proxy_token_expiry("thread-1", None)
        github_proxy._PROXY_TOKEN_EXPIRY["thread-1"] = (None, now, None, ())
        assert proxy_token_needs_refresh("thread-1", now=now) is False
        later = now + PROXY_TOKEN_FALLBACK_TTL
        assert proxy_token_needs_refresh("thread-1", now=later) is True


def test_only_a_token_set_since_preparation_began_is_reported() -> None:
    # A record left by an earlier run may be for a box the thread has since left.
    expires_at = datetime(2999, 1, 1, tzinfo=UTC)
    record_proxy_token_expiry("thread-1", expires_at)
    began = datetime.now(UTC)
    assert proxy_token_set_since("thread-1", began) is None
    record_proxy_token_expiry("thread-1", expires_at)
    assert proxy_token_set_since("thread-1", began) == expires_at


class TestRefreshGithubProxyMiddleware:
    @pytest.mark.asyncio
    async def test_calls_refresh_with_thread_id(self) -> None:
        from openswe.middleware.refresh_github_proxy import refresh_github_proxy_before_model

        with (
            patch(
                "openswe.middleware.refresh_github_proxy.get_config",
                return_value={"configurable": {"thread_id": "thread-9"}},
            ),
            patch(
                "openswe.middleware.refresh_github_proxy.maybe_refresh_proxy_token",
                new=AsyncMock(return_value=True),
            ) as mock_refresh,
        ):
            result = await refresh_github_proxy_before_model.abefore_model(
                cast(AgentState, {}), MagicMock()
            )

        assert result is None
        mock_refresh.assert_awaited_once_with("thread-9")

    @pytest.mark.asyncio
    async def test_swallows_refresh_errors(self) -> None:
        from openswe.middleware.refresh_github_proxy import refresh_github_proxy_before_model

        with (
            patch(
                "openswe.middleware.refresh_github_proxy.get_config",
                return_value={"configurable": {"thread_id": "thread-9"}},
            ),
            patch(
                "openswe.middleware.refresh_github_proxy.maybe_refresh_proxy_token",
                new=AsyncMock(side_effect=RuntimeError("boom")),
            ),
        ):
            result = await refresh_github_proxy_before_model.abefore_model(
                cast(AgentState, {}), MagicMock()
            )

        assert result is None
