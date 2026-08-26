"""Tests for GitHub proxy auth configuration."""

import base64
from typing import cast
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from langchain.agents.middleware import AgentMiddleware, AgentState
from langchain_core.runnables import RunnableConfig
from langgraph.runtime import Runtime

from agent.dashboard.environments import Environment
from agent.integrations.langsmith import (
    PROXY_GH_TOKEN_PLACEHOLDER,
    PROXY_MODEL_KEY_PLACEHOLDER,
    _configure_github_proxy,
    _stagehand_proxy_rules,
)
from agent.utils.sandbox_state import SandboxBackendProxy


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
            module.create_local_sandbox.return_value = MagicMock(id="local", aexecute=AsyncMock())
            mock_import_module.return_value = module

            from agent.utils.sandbox import create_sandbox

            sandbox = await create_sandbox("existing")

        assert sandbox.id == "local"
        mock_import_module.assert_called_once_with("agent.integrations.local")
        module.create_local_sandbox.assert_called_once_with("existing")

    async def test_create_sandbox_passes_langsmith_resource_overrides(self) -> None:
        with (
            patch("agent.utils.sandbox.import_module") as mock_import_module,
            patch.dict("os.environ", {"SANDBOX_TYPE": "langsmith"}),
        ):
            module = MagicMock()
            module.create_langsmith_sandbox = AsyncMock(
                return_value=MagicMock(id="langsmith", aexecute=AsyncMock())
            )
            mock_import_module.return_value = module

            from agent.utils.sandbox import create_sandbox

            await create_sandbox(
                snapshot_id="env-snap",
                mem_bytes=16,
                vcpus=8,
                fs_capacity_bytes=128,
                create_params={"_internal_runtime": "v2"},
            )

        module.create_langsmith_sandbox.assert_awaited_once_with(
            None,
            snapshot_id="env-snap",
            mem_bytes=16,
            vcpus=8,
            fs_capacity_bytes=128,
            create_params={"_internal_runtime": "v2"},
        )


def test_stagehand_proxy_rule_keeps_model_key_opaque() -> None:
    with patch.dict(
        "os.environ",
        {"STAGEHAND_MODEL": "anthropic/claude-sonnet-4-5", "STAGEHAND_MODEL_API_KEY": "secret"},
        clear=True,
    ):
        rule = _stagehand_proxy_rules()[0]

    assert rule["headers"] == [{"name": "x-api-key", "type": "opaque", "value": "secret"}]
    assert rule["env_vars"] == {"MODEL_API_KEY": PROXY_MODEL_KEY_PLACEHOLDER}
    assert "secret" not in rule["env_vars"].values()


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

            # env_vars are stored plaintext, so the real token must never land here.
            assert api_rule["env_vars"] == {"GH_TOKEN": PROXY_GH_TOKEN_PLACEHOLDER}
            assert token not in api_rule["env_vars"]["GH_TOKEN"]

            web_rule = rules[1]
            assert web_rule["name"] == "github"
            assert web_rule["match_hosts"] == ["github.com", "*.github.com"]

            headers = web_rule["headers"]
            assert len(headers) == 1
            assert headers[0]["name"] == "Authorization"
            assert headers[0]["type"] == "opaque"
            assert headers[0]["value"] == f"Basic {expected_basic}"

    async def test_preserves_custom_proxy_config_when_adding_github_auth(self) -> None:
        custom_rule = {"name": "public-api", "match_hosts": ["example.com"]}
        with (
            patch("agent.integrations.langsmith.httpx.AsyncClient") as mock_client_cls,
            patch.dict("os.environ", {"LANGSMITH_API_KEY": "ls-api-key"}),
        ):
            mock_client = MagicMock()
            mock_response = MagicMock()
            mock_response.raise_for_status = MagicMock()
            mock_client.patch = AsyncMock(return_value=mock_response)
            _mock_async_client(mock_client_cls, mock_client)

            await _configure_github_proxy(
                "sandbox-abc123",
                "token",
                base_proxy_config={"rules": [custom_rule], "enabled": True},
            )

        proxy_config = mock_client.patch.call_args.kwargs["json"]["proxy_config"]
        assert proxy_config["enabled"] is True
        assert proxy_config["rules"][0] == custom_rule
        assert [rule["name"] for rule in proxy_config["rules"][1:]] == ["github-api", "github"]

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

    async def test_sandbox_overrides_take_precedence(self) -> None:
        """SANDBOX_LANGSMITH_* override the shared key/endpoint for the proxy call."""
        with (
            patch("agent.integrations.langsmith.httpx.AsyncClient") as mock_client_cls,
            patch.dict(
                "os.environ",
                {
                    "LANGSMITH_API_KEY": "shared-key",
                    "LANGSMITH_ENDPOINT": "https://shared.smith.langchain.com",
                    "SANDBOX_LANGSMITH_API_KEY": "sandbox-key",
                    "SANDBOX_LANGSMITH_ENDPOINT": "https://sandbox.smith.langchain.com",
                },
            ),
        ):
            mock_client = MagicMock()
            mock_response = MagicMock()
            mock_response.raise_for_status = MagicMock()
            mock_client.patch = AsyncMock(return_value=mock_response)
            _mock_async_client(mock_client_cls, mock_client)

            await _configure_github_proxy("sandbox-abc", "token")

            assert (
                mock_client.patch.call_args.args[0]
                == "https://sandbox.smith.langchain.com/v2/sandboxes/boxes/sandbox-abc"
            )
            assert mock_client.patch.call_args.kwargs["headers"] == {"X-API-Key": "sandbox-key"}

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
        response = httpx.Response(403, request=request)
        error = httpx.HTTPStatusError("Forbidden", request=request, response=response)
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

    async def test_error_message_carries_response_body(self) -> None:
        """The API's explanation must survive into the raised error."""
        request = httpx.Request(
            "PATCH", "https://api.smith.langchain.com/v2/sandboxes/boxes/sandbox-abc"
        )
        response = httpx.Response(403, request=request, text="sandbox belongs to another tenant")
        error = httpx.HTTPStatusError("Forbidden", request=request, response=response)
        with (
            patch("agent.integrations.langsmith.httpx.AsyncClient") as mock_client_cls,
            patch.dict("os.environ", {"LANGSMITH_API_KEY": "api-key"}),
        ):
            mock_client = MagicMock()
            failed_response = MagicMock()
            failed_response.raise_for_status.side_effect = error
            mock_client.patch = AsyncMock(return_value=failed_response)
            _mock_async_client(mock_client_cls, mock_client)

            with pytest.raises(httpx.HTTPStatusError, match="another tenant"):
                await _configure_github_proxy("sandbox-abc", "token")


class TestConfigureGithubProxyStartsStoppedSandbox:
    """A 400 means the sandbox is not ready; start it and retry the update."""

    @staticmethod
    def _not_ready_error() -> httpx.HTTPStatusError:
        request = httpx.Request(
            "PATCH", "https://api.smith.langchain.com/v2/sandboxes/boxes/sandbox-abc"
        )
        response = httpx.Response(
            400,
            request=request,
            text='sandbox "sandbox-abc" is in "stopped" state, must be "ready"',
        )
        return httpx.HTTPStatusError("Bad request", request=request, response=response)

    async def test_starts_sandbox_then_retries(self) -> None:
        with (
            patch("agent.integrations.langsmith.httpx.AsyncClient") as mock_client_cls,
            patch(
                "agent.integrations.langsmith.get_async_sandbox_client"
            ) as mock_sandbox_client_factory,
            patch.dict("os.environ", {"LANGSMITH_API_KEY": "api-key"}),
        ):
            mock_client = MagicMock()
            failed_response = MagicMock()
            failed_response.raise_for_status.side_effect = self._not_ready_error()
            successful_response = MagicMock()
            successful_response.raise_for_status = MagicMock()
            mock_client.patch = AsyncMock(side_effect=[failed_response, successful_response])
            _mock_async_client(mock_client_cls, mock_client)

            sandbox_client = MagicMock()
            sandbox_client.start_sandbox = AsyncMock()
            sandbox_client.aclose = AsyncMock()
            mock_sandbox_client_factory.return_value = sandbox_client

            await _configure_github_proxy("sandbox-abc", "token")

            sandbox_client.start_sandbox.assert_awaited_once()
            assert sandbox_client.start_sandbox.await_args.args[0] == "sandbox-abc"
            sandbox_client.aclose.assert_awaited_once()
            assert mock_client.patch.call_count == 2

    async def test_retries_even_when_start_fails(self) -> None:
        """A failed start is logged, not fatal: the retry reports the real state."""
        with (
            patch("agent.integrations.langsmith.httpx.AsyncClient") as mock_client_cls,
            patch(
                "agent.integrations.langsmith.get_async_sandbox_client"
            ) as mock_sandbox_client_factory,
            patch.dict("os.environ", {"LANGSMITH_API_KEY": "api-key"}),
        ):
            mock_client = MagicMock()
            failed_response = MagicMock()
            failed_response.raise_for_status.side_effect = self._not_ready_error()
            mock_client.patch = AsyncMock(return_value=failed_response)
            _mock_async_client(mock_client_cls, mock_client)

            sandbox_client = MagicMock()
            sandbox_client.start_sandbox = AsyncMock(side_effect=RuntimeError("start rejected"))
            sandbox_client.aclose = AsyncMock()
            mock_sandbox_client_factory.return_value = sandbox_client

            with pytest.raises(httpx.HTTPStatusError, match="stopped"):
                await _configure_github_proxy("sandbox-abc", "token")

            sandbox_client.start_sandbox.assert_awaited_once()
            sandbox_client.aclose.assert_awaited_once()
            assert mock_client.patch.call_count == 2

    async def test_does_not_start_twice(self) -> None:
        """Only one start attempt per configure call, even if the retry also 400s."""
        with (
            patch("agent.integrations.langsmith.httpx.AsyncClient") as mock_client_cls,
            patch(
                "agent.integrations.langsmith.get_async_sandbox_client"
            ) as mock_sandbox_client_factory,
            patch.dict("os.environ", {"LANGSMITH_API_KEY": "api-key"}),
        ):
            mock_client = MagicMock()
            failed_response = MagicMock()
            failed_response.raise_for_status.side_effect = self._not_ready_error()
            mock_client.patch = AsyncMock(return_value=failed_response)
            _mock_async_client(mock_client_cls, mock_client)

            sandbox_client = MagicMock()
            sandbox_client.start_sandbox = AsyncMock()
            sandbox_client.aclose = AsyncMock()
            mock_sandbox_client_factory.return_value = sandbox_client

            with pytest.raises(httpx.HTTPStatusError):
                await _configure_github_proxy("sandbox-abc", "token")

            sandbox_client.start_sandbox.assert_awaited_once()


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
            mock_create.return_value = MagicMock(id="sandbox-123", aexecute=AsyncMock())

            from agent.server import _create_sandbox_with_proxy

            await _create_sandbox_with_proxy()

            mock_create.assert_called_once_with(snapshot_id=None)
            mock_proxy.assert_called_once_with("sandbox-123", "ghs_install")
            mock_get_token.assert_awaited_once_with()

    @pytest.mark.asyncio
    async def test_passes_environment_resources_to_sandbox_creation(self) -> None:
        environment = Environment(
            slug="env",
            snapshot_status="ready",
            snapshot_id="env-snap",
            mem_bytes=16,
            vcpus=8,
            fs_capacity_bytes=128,
            create_params={
                "_internal_runtime": "v2",
                "proxy_config": {"rules": [{"name": "public-api", "match_hosts": ["example.com"]}]},
            },
        )
        with (
            patch(
                "agent.server.resolve_environment", new_callable=AsyncMock, return_value=environment
            ),
            patch("agent.server.create_sandbox", new_callable=AsyncMock) as mock_create,
            patch(
                "agent.server.get_github_app_installation_token_with_expiry",
                new_callable=AsyncMock,
                return_value=("ghs_install", None),
            ),
            patch(
                "agent.server._configure_github_proxy", new_callable=AsyncMock
            ) as mock_configure_proxy,
            patch.dict("os.environ", {"SANDBOX_TYPE": "langsmith"}),
        ):
            mock_create.return_value = MagicMock(id="sandbox-123", aexecute=AsyncMock())

            from agent.server import _create_sandbox_with_proxy

            await _create_sandbox_with_proxy(environment_slug="large")

        mock_create.assert_awaited_once_with(
            snapshot_id="env-snap",
            mem_bytes=16,
            vcpus=8,
            fs_capacity_bytes=128,
            create_params=environment.create_params,
        )
        mock_configure_proxy.assert_awaited_once_with(
            "sandbox-123",
            "ghs_install",
            base_proxy_config=environment.create_params["proxy_config"],
        )

    @pytest.mark.asyncio
    async def test_raises_when_installation_token_mint_fails(self) -> None:
        """A full installation token mint failure prevents proxy configuration."""
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
            mock_create.return_value = MagicMock(id="sandbox-123", aexecute=AsyncMock())

            from agent.server import _create_sandbox_with_proxy

            with pytest.raises(ValueError, match="installation token is unavailable"):
                await _create_sandbox_with_proxy(thread_id="thread-123")

            mock_get_token.assert_awaited_once_with()

    @pytest.mark.asyncio
    async def test_skips_proxy_for_non_langsmith(self) -> None:
        """Non-langsmith sandboxes should skip proxy configuration."""
        with (
            patch("agent.server.create_sandbox", new_callable=AsyncMock) as mock_create,
            patch("agent.server._configure_github_proxy", new_callable=AsyncMock) as mock_proxy,
            patch.dict("os.environ", {"SANDBOX_TYPE": "daytona"}),
        ):
            mock_create.return_value = MagicMock(id="sandbox-456", aexecute=AsyncMock())

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
            mock_create.return_value = MagicMock(id="sandbox-789", aexecute=AsyncMock())

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

    @pytest.mark.asyncio
    async def test_refreshes_proxy_for_cached_langsmith_sandbox(self) -> None:
        """Cached sandboxes should get a fresh proxy token before git operations."""
        config = self._execution_config()
        mock_sandbox = MagicMock(id="sandbox-cached", aexecute=AsyncMock())
        mock_sandbox.aexecute = AsyncMock()
        base_proxy_config = {"rules": [{"name": "public-api", "match_hosts": ["example.com"]}]}
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
                "agent.server.resolve_environment",
                new_callable=AsyncMock,
                return_value=Environment(slug="env", create_params={"proxy_config": {}}),
            ),
            patch(
                "agent.server.get_sandbox_id_from_metadata",
                new_callable=AsyncMock,
                return_value="sandbox-cached",
            ),
            patch(
                "agent.server.get_sandbox_metadata",
                new_callable=AsyncMock,
                return_value={
                    "sandbox_id": "sandbox-cached",
                    "sandbox_base_proxy_config": base_proxy_config,
                },
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
            patch("agent.server.make_model", return_value=MagicMock()),
            patch("agent.server.construct_system_prompt", return_value="prompt"),
            patch("agent.server.create_deep_agent", side_effect=fake_create_deep_agent),
            patch.dict(
                "agent.server.SANDBOX_BACKENDS",
                {"thread-123": SandboxBackendProxy(mock_sandbox, thread_id="thread-123")},
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

            mock_proxy.assert_called_once_with(
                "sandbox-cached",
                "ghs_fresh",
                base_proxy_config=base_proxy_config,
            )

    @pytest.mark.asyncio
    async def test_refreshes_proxy_when_reconnecting_to_existing_langsmith_sandbox(self) -> None:
        """Reconnected sandboxes should also get a fresh proxy token."""
        config = self._execution_config()
        mock_sandbox = MagicMock(id="sandbox-existing", aexecute=AsyncMock())
        mock_sandbox.aexecute = AsyncMock()
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
                "agent.server.get_sandbox_metadata",
                new_callable=AsyncMock,
                return_value={"sandbox_id": "sandbox-existing"},
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
            patch.dict("agent.utils.github_proxy._PROXY_BASE_CONFIGS", {}, clear=True),
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
    async def test_proxy_refresh_failure_raises_instead_of_replacing(self) -> None:
        """A sandbox we can't reconfigure fails the run, and is never swapped out.

        Replacing it would hand the agent an empty filesystem and discard any
        work the old sandbox still held.
        """
        mock_sandbox = MagicMock(id="sandbox-stale", aexecute=AsyncMock())
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
            patch("agent.server._create_sandbox_with_proxy", new_callable=AsyncMock) as mock_create,
            patch.dict("os.environ", {"SANDBOX_TYPE": "langsmith"}),
        ):
            from agent.server import SandboxUnreachableError, _refresh_github_proxy_or_fail

            with pytest.raises(SandboxUnreachableError) as excinfo:
                await _refresh_github_proxy_or_fail(mock_sandbox, "thread-123")

            assert excinfo.value.sandbox_id == "sandbox-stale"
            mock_proxy.assert_called_once_with("sandbox-stale", "ghs_fresh")
            mock_create.assert_not_awaited()
