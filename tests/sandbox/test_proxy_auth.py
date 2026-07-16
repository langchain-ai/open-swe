"""Tests for GitHub proxy auth configuration."""

from __future__ import annotations

import base64
from typing import cast
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from langchain.agents.middleware import AgentMiddleware, AgentState
from langchain_core.runnables import RunnableConfig
from langgraph.runtime import Runtime

from agent.integrations.langsmith import _configure_github_proxy
from agent.utils.github_app import (
    BASE_RUNTIME_PROXY_TOKEN_PERMISSIONS,
    CORE_RUNTIME_PROXY_TOKEN_PERMISSIONS,
    PROXY_TOKEN_PERMISSION_LADDER,
    RUNTIME_PROXY_TOKEN_PERMISSIONS,
)


def _mock_async_client(mock_client_cls: MagicMock, inner: MagicMock) -> None:
    """Wire an ``httpx.AsyncClient`` mock class to yield ``inner`` from its
    async context manager."""
    mock_client_cls.return_value.__aenter__ = AsyncMock(return_value=inner)
    mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=False)


class TestSandboxFactoryLoading:
    async def test_create_sandbox_loads_only_selected_provider(self) -> None:
        with (
            patch("agent.utils.sandbox.import_module") as mock_import_module,
            patch.dict("os.environ", {"SANDBOX_TYPE": "local"}),
        ):
            module = MagicMock()
            module.create_local_sandbox.return_value = MagicMock(id="local")
            mock_import_module.return_value = module

            from agent.utils.sandbox import create_sandbox

            sandbox = await create_sandbox("existing")

        assert sandbox.id == "local"
        mock_import_module.assert_called_once_with("agent.integrations.local")
        module.create_local_sandbox.assert_called_once_with("existing")


class TestConfigureGithubProxy:
    """Tests for _configure_github_proxy payload shape and error handling."""

    async def test_sends_correct_payload_shape(self) -> None:
        """Verify the PATCH request uses opaque headers with correct structure."""
        token = "ghs_testtoken123"
        expected_basic = base64.b64encode(f"x-access-token:{token}".encode()).decode()

        with (
            patch("agent.integrations.langsmith.httpx.AsyncClient") as mock_client_cls,
            patch.dict("os.environ", {"LANGSMITH_API_KEY": "ls-api-key"}),
        ):
            mock_client = MagicMock()
            mock_response = MagicMock()
            mock_response.raise_for_status = MagicMock()
            mock_client.patch = AsyncMock(return_value=mock_response)
            _mock_async_client(mock_client_cls, mock_client)

            await _configure_github_proxy("sandbox-abc123", token)

            mock_client.patch.assert_called_once()
            call_kwargs = mock_client.patch.call_args
            payload = call_kwargs.kwargs["json"]

            assert "proxy_config" in payload
            rules = payload["proxy_config"]["rules"]
            assert len(rules) == 2

            api_rule = rules[0]
            assert api_rule["name"] == "github-api"
            assert api_rule["match_hosts"] == ["api.github.com"]
            api_headers = api_rule["headers"]
            assert len(api_headers) == 1
            assert api_headers[0]["name"] == "Authorization"
            assert api_headers[0]["type"] == "opaque"
            assert api_headers[0]["value"] == f"Bearer {token}"

            web_rule = rules[1]
            assert web_rule["name"] == "github"
            assert web_rule["match_hosts"] == ["github.com", "*.github.com"]

            headers = web_rule["headers"]
            assert len(headers) == 1
            assert headers[0]["name"] == "Authorization"
            assert headers[0]["type"] == "opaque"
            assert headers[0]["value"] == f"Basic {expected_basic}"

    async def test_sends_to_correct_url(self) -> None:
        """Verify the PATCH hits the right endpoint."""
        with (
            patch("agent.integrations.langsmith.httpx.AsyncClient") as mock_client_cls,
            patch.dict(
                "os.environ",
                {
                    "LANGSMITH_ENDPOINT": "https://test.api.smith.langchain.com",
                    "LANGSMITH_API_KEY": "api-key",
                },
            ),
        ):
            mock_client = MagicMock()
            mock_response = MagicMock()
            mock_response.raise_for_status = MagicMock()
            mock_client.patch = AsyncMock(return_value=mock_response)
            _mock_async_client(mock_client_cls, mock_client)

            await _configure_github_proxy("sandbox-xyz", "token")

            url = mock_client.patch.call_args.args[0]
            assert url == "https://test.api.smith.langchain.com/v2/sandboxes/boxes/sandbox-xyz"

    async def test_sends_api_key_header(self) -> None:
        """Verify the PATCH includes the LangSmith API key."""
        with (
            patch("agent.integrations.langsmith.httpx.AsyncClient") as mock_client_cls,
            patch.dict("os.environ", {"LANGSMITH_API_KEY": "my-api-key"}),
        ):
            mock_client = MagicMock()
            mock_response = MagicMock()
            mock_response.raise_for_status = MagicMock()
            mock_client.patch = AsyncMock(return_value=mock_response)
            _mock_async_client(mock_client_cls, mock_client)

            await _configure_github_proxy("sandbox-abc", "token")

            headers = mock_client.patch.call_args.kwargs["headers"]
            assert headers == {"X-API-Key": "my-api-key"}

    async def test_retries_transient_http_error(self) -> None:
        """Transient proxy API errors should be retried on the same sandbox."""
        request = httpx.Request(
            "PATCH", "https://api.smith.langchain.com/v2/sandboxes/boxes/sandbox-abc"
        )
        response = httpx.Response(503, request=request)
        transient_error = httpx.HTTPStatusError(
            "Server error",
            request=request,
            response=response,
        )
        with (
            patch("agent.integrations.langsmith.httpx.AsyncClient") as mock_client_cls,
            patch(
                "agent.integrations.langsmith.asyncio.sleep", new_callable=AsyncMock
            ) as mock_sleep,
            patch.dict("os.environ", {"LANGSMITH_API_KEY": "api-key"}),
        ):
            mock_client = MagicMock()
            failed_response = MagicMock()
            failed_response.raise_for_status.side_effect = transient_error
            successful_response = MagicMock()
            successful_response.raise_for_status = MagicMock()
            mock_client.patch = AsyncMock(side_effect=[failed_response, successful_response])
            _mock_async_client(mock_client_cls, mock_client)

            await _configure_github_proxy("sandbox-abc", "token")

            assert mock_client.patch.call_count == 2
            mock_sleep.assert_called_once()

    async def test_raises_on_non_retryable_http_error(self) -> None:
        """Non-retryable HTTP errors should propagate without retrying."""
        request = httpx.Request(
            "PATCH", "https://api.smith.langchain.com/v2/sandboxes/boxes/sandbox-abc"
        )
        response = httpx.Response(400, request=request)
        error = httpx.HTTPStatusError("Bad request", request=request, response=response)
        with (
            patch("agent.integrations.langsmith.httpx.AsyncClient") as mock_client_cls,
            patch(
                "agent.integrations.langsmith.asyncio.sleep", new_callable=AsyncMock
            ) as mock_sleep,
            patch.dict("os.environ", {"LANGSMITH_API_KEY": "api-key"}),
        ):
            mock_client = MagicMock()
            failed_response = MagicMock()
            failed_response.raise_for_status.side_effect = error
            mock_client.patch = AsyncMock(return_value=failed_response)
            _mock_async_client(mock_client_cls, mock_client)

            with pytest.raises(httpx.HTTPStatusError):
                await _configure_github_proxy("sandbox-abc", "token")

            mock_client.patch.assert_called_once()
            mock_sleep.assert_not_called()


class TestCreateSandboxWithProxy:
    """Tests for _create_sandbox_with_proxy token source selection."""

    @pytest.mark.asyncio
    async def test_uses_installation_token_for_langsmith(self) -> None:
        """Installation token should be used for proxy auth on langsmith sandboxes."""
        with (
            patch(
                "agent.server.get_github_app_installation_token_with_expiry",
                new_callable=AsyncMock,
                return_value=("ghs_install", None),
            ) as mock_get_token,
            patch("agent.server.create_sandbox", new_callable=AsyncMock) as mock_create,
            patch("agent.server._configure_github_proxy", new_callable=AsyncMock) as mock_proxy,
            patch.dict("os.environ", {"SANDBOX_TYPE": "langsmith", "LANGSMITH_API_KEY": "ls-key"}),
        ):
            mock_create.return_value = MagicMock(id="sandbox-123")

            from agent.server import _create_sandbox_with_proxy

            await _create_sandbox_with_proxy()

            mock_create.assert_called_once_with(snapshot_id=None)
            mock_proxy.assert_called_once_with("sandbox-123", "ghs_install")
            await_args = mock_get_token.await_args
            assert await_args is not None
            assert await_args.kwargs["permissions"] == RUNTIME_PROXY_TOKEN_PERMISSIONS

    @pytest.mark.asyncio
    async def test_falls_back_when_optional_actions_permission_is_unavailable(self) -> None:
        """Sandbox creation should still keep workflow scope without Actions read."""
        with (
            patch(
                "agent.server.get_github_app_installation_token_with_expiry",
                new_callable=AsyncMock,
                side_effect=[(None, None), ("ghs_install", "expires")],
            ) as mock_get_token,
            patch("agent.server.create_sandbox", new_callable=AsyncMock) as mock_create,
            patch("agent.server._configure_github_proxy", new_callable=AsyncMock) as mock_proxy,
            patch("agent.server.record_proxy_token_expiry") as mock_record,
            patch.dict("os.environ", {"SANDBOX_TYPE": "langsmith", "LANGSMITH_API_KEY": "ls-key"}),
        ):
            mock_create.return_value = MagicMock(id="sandbox-123")

            from agent.server import _create_sandbox_with_proxy

            await _create_sandbox_with_proxy(thread_id="thread-123")

            assert mock_get_token.await_args_list[0].kwargs["permissions"] == (
                RUNTIME_PROXY_TOKEN_PERMISSIONS
            )
            assert mock_get_token.await_args_list[0].kwargs["log_errors"] is False
            assert mock_get_token.await_args_list[1].kwargs["permissions"] == (
                BASE_RUNTIME_PROXY_TOKEN_PERMISSIONS
            )
            assert mock_get_token.await_args_list[1].kwargs["permissions"]["workflows"] == "write"
            mock_proxy.assert_called_once_with("sandbox-123", "ghs_install")
            mock_record.assert_called_once_with(
                "thread-123",
                "expires",
                repositories=None,
                permissions=BASE_RUNTIME_PROXY_TOKEN_PERMISSIONS,
            )

    @pytest.mark.asyncio
    async def test_falls_back_to_core_when_workflows_grant_missing(self) -> None:
        """An install lacking workflows:write must still mint a core-scoped token."""
        with (
            patch(
                "agent.server.get_github_app_installation_token_with_expiry",
                new_callable=AsyncMock,
                side_effect=[(None, None), (None, None), ("ghs_install", "expires")],
            ) as mock_get_token,
            patch("agent.server.create_sandbox", new_callable=AsyncMock) as mock_create,
            patch("agent.server._configure_github_proxy", new_callable=AsyncMock) as mock_proxy,
            patch("agent.server.record_proxy_token_expiry") as mock_record,
            patch.dict("os.environ", {"SANDBOX_TYPE": "langsmith", "LANGSMITH_API_KEY": "ls-key"}),
        ):
            mock_create.return_value = MagicMock(id="sandbox-123")

            from agent.server import _create_sandbox_with_proxy

            await _create_sandbox_with_proxy(thread_id="thread-123")

            scopes = [call.kwargs["permissions"] for call in mock_get_token.await_args_list]
            assert scopes == list(PROXY_TOKEN_PERMISSION_LADDER)
            assert "workflows" not in scopes[-1]
            mock_proxy.assert_called_once_with("sandbox-123", "ghs_install")
            mock_record.assert_called_once_with(
                "thread-123",
                "expires",
                repositories=None,
                permissions=CORE_RUNTIME_PROXY_TOKEN_PERMISSIONS,
            )

    @pytest.mark.asyncio
    async def test_raises_only_when_even_core_scope_fails(self) -> None:
        """A hard failure requires every ladder rung — including core — to fail."""
        with (
            patch(
                "agent.server.get_github_app_installation_token_with_expiry",
                new_callable=AsyncMock,
                return_value=(None, None),
            ) as mock_get_token,
            patch("agent.server.create_sandbox", new_callable=AsyncMock) as mock_create,
            patch("agent.server._configure_github_proxy", new_callable=AsyncMock),
            patch.dict("os.environ", {"SANDBOX_TYPE": "langsmith", "LANGSMITH_API_KEY": "ls-key"}),
        ):
            mock_create.return_value = MagicMock(id="sandbox-123")

            from agent.server import _create_sandbox_with_proxy

            with pytest.raises(ValueError, match="installation token is unavailable"):
                await _create_sandbox_with_proxy(thread_id="thread-123")

            assert mock_get_token.await_count == len(PROXY_TOKEN_PERMISSION_LADDER)

    @pytest.mark.asyncio
    async def test_skips_proxy_for_non_langsmith(self) -> None:
        """Non-langsmith sandboxes should skip proxy configuration."""
        with (
            patch("agent.server.create_sandbox", new_callable=AsyncMock) as mock_create,
            patch("agent.server._configure_github_proxy", new_callable=AsyncMock) as mock_proxy,
            patch.dict("os.environ", {"SANDBOX_TYPE": "daytona"}),
        ):
            mock_create.return_value = MagicMock(id="sandbox-456")

            from agent.server import _create_sandbox_with_proxy

            await _create_sandbox_with_proxy()

            mock_create.assert_called_once_with(snapshot_id=None)
            mock_proxy.assert_not_called()

    @pytest.mark.asyncio
    async def test_raises_when_no_installation_token_for_langsmith(self) -> None:
        """Should raise ValueError when installation token is unavailable for langsmith."""
        with (
            patch("agent.server.create_sandbox", new_callable=AsyncMock) as mock_create,
            patch(
                "agent.server.get_github_app_installation_token_with_expiry",
                new_callable=AsyncMock,
                return_value=(None, None),
            ),
            patch.dict("os.environ", {"SANDBOX_TYPE": "langsmith"}),
        ):
            mock_create.return_value = MagicMock(id="sandbox-789")

            from agent.server import _create_sandbox_with_proxy

            with pytest.raises(ValueError, match="installation token is unavailable"):
                await _create_sandbox_with_proxy()


class _DummyAgent:
    def with_config(self, config):
        return self


class TestRefreshProxyOnSandboxReuse:
    """Tests for refreshing GitHub proxy auth on sandbox reuse."""

    @staticmethod
    def _execution_config() -> RunnableConfig:
        return cast(
            RunnableConfig,
            {
                "configurable": {
                    "__is_for_execution__": True,
                    "thread_id": "thread-123",
                    "repo": {"owner": "langchain-ai", "name": "open-swe"},
                },
                "metadata": {},
            },
        )

    @staticmethod
    def _async_client_mock(status: str) -> MagicMock:
        """Build an ``httpx``-style async-context-manager mock for the sandbox
        client whose status/start methods are awaitable."""
        inner = MagicMock()
        inner.get_sandbox_status = AsyncMock(return_value=MagicMock(status=status))
        inner.start_sandbox = AsyncMock()
        cm = MagicMock()
        cm.__aenter__ = AsyncMock(return_value=inner)
        cm.__aexit__ = AsyncMock(return_value=False)
        cm._inner = inner
        return cm

    @pytest.mark.asyncio
    async def test_refreshes_proxy_for_cached_langsmith_sandbox(self) -> None:
        """Cached sandboxes should get a fresh proxy token before git operations."""
        config = self._execution_config()
        mock_sandbox = MagicMock(id="sandbox-cached")
        captured: dict[str, object] = {}

        def fake_create_deep_agent(**kwargs):
            captured.update(kwargs)
            return _DummyAgent()

        with (
            patch(
                "agent.server.resolve_github_token",
                new_callable=AsyncMock,
                return_value=("ghp", None),
            ),
            patch(
                "agent.server.get_sandbox_id_from_metadata",
                new_callable=AsyncMock,
                return_value="sandbox-cached",
            ),
            patch(
                "agent.server.get_github_app_installation_token_with_expiry",
                new_callable=AsyncMock,
                return_value=("ghs_fresh", None),
            ),
            patch("agent.server._configure_github_proxy", new_callable=AsyncMock) as mock_proxy,
            patch(
                "agent.server.aresolve_sandbox_work_dir",
                new_callable=AsyncMock,
                return_value="/workspace",
            ),
            patch(
                "agent.server.check_or_recreate_sandbox",
                new_callable=AsyncMock,
                return_value=mock_sandbox,
            ),
            patch("agent.server.make_model", return_value=MagicMock()),
            patch("agent.server.construct_system_prompt", return_value="prompt"),
            patch("agent.server.create_deep_agent", side_effect=fake_create_deep_agent),
            patch.dict(
                "agent.server.SANDBOX_BACKENDS",
                {"thread-123": mock_sandbox},
                clear=True,
            ),
            patch.dict("os.environ", {"SANDBOX_TYPE": "langsmith"}),
        ):
            from agent.server import get_agent

            await get_agent(config)
            prepare = cast(AgentMiddleware, cast(list[object], captured["middleware"])[0])
            await prepare.abefore_agent(
                cast(AgentState[object], {"messages": []}),
                cast(Runtime[None], MagicMock()),
            )

            mock_proxy.assert_called_once_with("sandbox-cached", "ghs_fresh")

    @pytest.mark.asyncio
    async def test_refreshes_proxy_when_reconnecting_to_existing_langsmith_sandbox(self) -> None:
        """Reconnected sandboxes should also get a fresh proxy token."""
        config = self._execution_config()
        mock_sandbox = MagicMock(id="sandbox-existing")
        captured: dict[str, object] = {}

        def fake_create_deep_agent(**kwargs):
            captured.update(kwargs)
            return _DummyAgent()

        with (
            patch(
                "agent.server.resolve_github_token",
                new_callable=AsyncMock,
                return_value=("ghp", None),
            ),
            patch(
                "agent.server.get_sandbox_id_from_metadata",
                new_callable=AsyncMock,
                return_value="sandbox-existing",
            ),
            patch(
                "agent.server.create_sandbox",
                new_callable=AsyncMock,
                return_value=mock_sandbox,
            ) as mock_create,
            patch(
                "agent.server.get_github_app_installation_token_with_expiry",
                new_callable=AsyncMock,
                return_value=("ghs_fresh", None),
            ),
            patch("agent.server._configure_github_proxy", new_callable=AsyncMock) as mock_proxy,
            patch(
                "agent.server.aresolve_sandbox_work_dir",
                new_callable=AsyncMock,
                return_value="/workspace",
            ),
            patch("agent.server.make_model", return_value=MagicMock()),
            patch("agent.server.construct_system_prompt", return_value="prompt"),
            patch("agent.server.create_deep_agent", side_effect=fake_create_deep_agent),
            patch.dict("agent.server.SANDBOX_BACKENDS", {}, clear=True),
            patch.dict("os.environ", {"SANDBOX_TYPE": "langsmith"}),
        ):
            from agent.server import get_agent

            await get_agent(config)
            prepare = cast(AgentMiddleware, cast(list[object], captured["middleware"])[0])
            await prepare.abefore_agent(
                cast(AgentState[object], {"messages": []}),
                cast(Runtime[None], MagicMock()),
            )

            mock_create.assert_called_once_with("sandbox-existing")
            mock_proxy.assert_called_once_with("sandbox-existing", "ghs_fresh")

    @pytest.mark.asyncio
    async def test_proxy_refresh_failure_recreates_sandbox(self) -> None:
        """A stale sandbox whose proxy cannot be patched should be replaced."""
        mock_sandbox = MagicMock(id="sandbox-stale")
        replacement_sandbox = MagicMock(id="sandbox-replacement")
        request = httpx.Request(
            "PATCH", "https://api.smith.langchain.com/v2/sandboxes/boxes/sandbox-stale"
        )
        response = httpx.Response(400, request=request)

        with (
            patch(
                "agent.server.get_github_app_installation_token_with_expiry",
                new_callable=AsyncMock,
                return_value=("ghs_fresh", None),
            ),
            patch(
                "agent.server._configure_github_proxy",
                new_callable=AsyncMock,
                side_effect=httpx.HTTPStatusError(
                    "Bad request",
                    request=request,
                    response=response,
                ),
            ) as mock_proxy,
            patch(
                "agent.server._recreate_sandbox",
                new_callable=AsyncMock,
                return_value=replacement_sandbox,
            ) as mock_recreate,
            patch.dict("os.environ", {"SANDBOX_TYPE": "langsmith"}),
        ):
            from agent.server import _refresh_github_proxy_or_recreate

            sandbox = await _refresh_github_proxy_or_recreate(mock_sandbox, "thread-123")

            assert sandbox is replacement_sandbox
            mock_proxy.assert_called_once_with("sandbox-stale", "ghs_fresh")
            mock_recreate.assert_awaited_once_with(
                "thread-123",
                github_proxy_token=None,
                github_proxy_repositories=None,
                repo=None,
            )

    @pytest.mark.asyncio
    async def test_starts_stopped_langsmith_sandbox_before_proxy_refresh(self) -> None:
        """Proxy config requires a running LangSmith sandbox."""
        inner_sandbox = MagicMock(name="sandbox-stopped")
        inner_sandbox.name = "sandbox-stopped"
        client_cm = self._async_client_mock("stopped")

        with (
            patch(
                "agent.server.get_github_app_installation_token_with_expiry",
                new_callable=AsyncMock,
                return_value=("ghs_fresh", None),
            ),
            patch("agent.server._configure_github_proxy", new_callable=AsyncMock) as mock_proxy,
            patch("agent.server.get_async_sandbox_client", return_value=client_cm),
            patch.dict("os.environ", {"SANDBOX_TYPE": "langsmith"}),
        ):
            from agent.server import LangSmithSandbox, _refresh_github_proxy

            sandbox_backend = object.__new__(LangSmithSandbox)
            sandbox_backend._sandbox = inner_sandbox

            await _refresh_github_proxy(sandbox_backend)

            client_cm._inner.get_sandbox_status.assert_awaited_once_with("sandbox-stopped")
            client_cm._inner.start_sandbox.assert_awaited_once_with("sandbox-stopped")
            mock_proxy.assert_called_once_with("sandbox-stopped", "ghs_fresh")

    @pytest.mark.asyncio
    async def test_skips_start_for_ready_langsmith_sandbox_before_proxy_refresh(self) -> None:
        """Ready sandboxes can be patched without starting again."""
        inner_sandbox = MagicMock(name="sandbox-ready")
        inner_sandbox.name = "sandbox-ready"
        client_cm = self._async_client_mock("ready")

        with (
            patch(
                "agent.server.get_github_app_installation_token_with_expiry",
                new_callable=AsyncMock,
                return_value=("ghs_fresh", None),
            ),
            patch("agent.server._configure_github_proxy", new_callable=AsyncMock) as mock_proxy,
            patch("agent.server.get_async_sandbox_client", return_value=client_cm),
            patch.dict("os.environ", {"SANDBOX_TYPE": "langsmith"}),
        ):
            from agent.server import LangSmithSandbox, _refresh_github_proxy

            sandbox_backend = object.__new__(LangSmithSandbox)
            sandbox_backend._sandbox = inner_sandbox

            await _refresh_github_proxy(sandbox_backend)

            client_cm._inner.get_sandbox_status.assert_awaited_once_with("sandbox-ready")
            client_cm._inner.start_sandbox.assert_not_called()
            mock_proxy.assert_called_once_with("sandbox-ready", "ghs_fresh")
