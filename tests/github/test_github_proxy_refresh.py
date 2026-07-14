"""Tests for mid-run GitHub proxy token refresh."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agent.utils import github_proxy
from agent.utils.github_proxy import (
    PROXY_TOKEN_FALLBACK_TTL,
    clear_proxy_token_expiry,
    maybe_refresh_proxy_token,
    proxy_token_needs_refresh,
    record_proxy_token_expiry,
)


@pytest.fixture(autouse=True)
def _clear_state() -> None:
    github_proxy._PROXY_TOKEN_EXPIRY.clear()
    yield
    github_proxy._PROXY_TOKEN_EXPIRY.clear()


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
        github_proxy._PROXY_TOKEN_EXPIRY["thread-1"] = (None, now, None)
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

    @pytest.mark.asyncio
    async def test_refreshes_and_records_new_expiry(self) -> None:
        now = datetime(2025, 1, 1, 12, 0, 0, tzinfo=UTC)
        record_proxy_token_expiry("thread-1", now + timedelta(minutes=1))
        backend = MagicMock(id="sb-1")
        new_expiry = "2025-01-01T13:00:00Z"

        with (
            patch.dict("os.environ", {"SANDBOX_TYPE": "langsmith"}),
            patch.dict(github_proxy.SANDBOX_BACKENDS, {"thread-1": backend}, clear=True),
            patch(
                "agent.utils.github_proxy.get_github_app_installation_token_with_expiry",
                new=AsyncMock(return_value=("ghs_new", new_expiry)),
            ),
            patch(
                "agent.integrations.langsmith._configure_github_proxy",
                new_callable=AsyncMock,
            ) as mock_configure,
        ):
            result = await maybe_refresh_proxy_token("thread-1", now=now)

        assert result is True
        mock_configure.assert_called_once_with("sb-1", "ghs_new")
        expires_at, _recorded, _scope, permissions = github_proxy._PROXY_TOKEN_EXPIRY["thread-1"]
        assert expires_at == datetime(2025, 1, 1, 13, 0, 0, tzinfo=UTC)
        assert permissions == ()

    @pytest.mark.asyncio
    async def test_preserves_repo_scope_on_refresh(self) -> None:
        now = datetime(2025, 1, 1, 12, 0, 0, tzinfo=UTC)
        record_proxy_token_expiry("thread-1", now + timedelta(minutes=1), repositories=["open-swe"])
        backend = MagicMock(id="sb-1")
        token_mock = AsyncMock(return_value=("ghs_new", "2025-01-01T13:00:00Z"))

        with (
            patch.dict("os.environ", {"SANDBOX_TYPE": "langsmith"}),
            patch.dict(github_proxy.SANDBOX_BACKENDS, {"thread-1": backend}, clear=True),
            patch(
                "agent.utils.github_proxy.get_github_app_installation_token_with_expiry",
                new=token_mock,
            ),
            patch(
                "agent.integrations.langsmith._configure_github_proxy",
                new_callable=AsyncMock,
            ),
        ):
            result = await maybe_refresh_proxy_token("thread-1", now=now)

        assert result is True
        token_mock.assert_awaited_once_with(repositories=["open-swe"])
        _expires, _recorded, scope, permissions = github_proxy._PROXY_TOKEN_EXPIRY["thread-1"]
        assert scope == ("open-swe",)
        assert permissions == ()

    @pytest.mark.asyncio
    async def test_no_refresh_when_token_unavailable(self) -> None:
        now = datetime(2025, 1, 1, 12, 0, 0, tzinfo=UTC)
        record_proxy_token_expiry("thread-1", now + timedelta(minutes=1))
        backend = MagicMock(id="sb-1")

        with (
            patch.dict("os.environ", {"SANDBOX_TYPE": "langsmith"}),
            patch.dict(github_proxy.SANDBOX_BACKENDS, {"thread-1": backend}, clear=True),
            patch(
                "agent.utils.github_proxy.get_github_app_installation_token_with_expiry",
                new=AsyncMock(return_value=(None, None)),
            ),
            patch("agent.integrations.langsmith._configure_github_proxy") as mock_configure,
        ):
            result = await maybe_refresh_proxy_token("thread-1", now=now)

        assert result is False
        mock_configure.assert_not_called()


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
            result = await refresh_github_proxy_before_model.abefore_model({}, MagicMock())

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
            result = await refresh_github_proxy_before_model.abefore_model({}, MagicMock())

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
            result = await refresh_github_proxy_before_model.abefore_model({}, MagicMock())

        assert result is None


class TestConfigureSandboxGithubAuth:
    @pytest.mark.asyncio
    async def test_executes_python_script_in_sandbox(self) -> None:
        from agent.utils.github_proxy import configure_sandbox_github_auth
        backend = MagicMock()
        backend.execute = MagicMock(return_value=MagicMock(exit_code=0))

        await configure_sandbox_github_auth(backend, "test-token")

        backend.execute.assert_called_once()
        cmd = backend.execute.call_args[0][0]
        assert "python" in cmd
        assert "hosts.yml" in cmd
        assert "credential.helper" in cmd
        assert "test-token" in cmd


class TestNonLangSmithTokenRefresh:
    @pytest.mark.asyncio
    async def test_refreshes_for_non_langsmith_sandbox(self) -> None:
        from agent.utils.github_proxy import refresh_proxy_token
        backend = MagicMock(id="sb-local")
        new_expiry = "2025-01-01T13:00:00Z"

        with (
            patch.dict("os.environ", {"SANDBOX_TYPE": "local"}),
            patch.dict(github_proxy.SANDBOX_BACKENDS, {"thread-local": backend}, clear=True),
            patch(
                "agent.utils.github_proxy.get_github_app_installation_token_with_expiry",
                new=AsyncMock(return_value=("ghs_local", new_expiry)),
            ),
            patch("agent.utils.github_proxy.configure_sandbox_github_auth", new=AsyncMock()) as mock_configure_auth,
            patch("agent.utils.github_token.cache_github_token_for_thread") as mock_cache_token,
        ):
            record_proxy_token_expiry("thread-local", "2025-01-01T12:00:00Z")
            res = await refresh_proxy_token("thread-local")

            assert res is True
            mock_configure_auth.assert_called_once_with(backend, "ghs_local")
            mock_cache_token.assert_called_once_with("thread-local", "ghs_local", expires_at=new_expiry, is_bot_token=True)

            # verify record updated
            record = github_proxy._PROXY_TOKEN_EXPIRY["thread-local"]
            assert record[0] == datetime(2025, 1, 1, 13, 0, 0, tzinfo=UTC)
