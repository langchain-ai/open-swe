"""Tests for GitHub proxy auth configuration."""

import base64
from typing import cast
from unittest.mock import AsyncMock, MagicMock, patch

import httpx2
import pytest
from langchain.agents.middleware import AgentMiddleware, AgentState
from langchain_core.runnables import RunnableConfig
from langgraph.runtime import Runtime

from agent.dashboard.environments import Environment
from agent.sandboxes.credentials import GitHubProxyCredentials
from agent.sandboxes.lifecycle import resolve_open_swe_create_config
from coding_agent.sandboxes.providers.langsmith import (
    PROXY_GH_TOKEN_PLACEHOLDER,
    PROXY_MODEL_KEY_PLACEHOLDER,
    _stagehand_proxy_rules,
    configure_github_proxy,
)
from coding_agent.sandboxes.state import SandboxBackendProxy


def _mock_async_client(mock_client_cls: MagicMock, inner: MagicMock) -> None:
    """Wire an ``httpx2.AsyncClient`` mock class to yield ``inner`` from its
    async context manager."""
    mock_client_cls.return_value.__aenter__ = AsyncMock(return_value=inner)
    mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=False)


class TestSandboxFactoryLoading:
    async def test_create_sandbox_loads_only_selected_provider(self) -> None:
        with (
            patch("coding_agent.sandboxes.providers.registry.import_module") as mock_import_module,
            patch.dict("os.environ", {"SANDBOX_TYPE": "local"}),
        ):
            module = MagicMock()
            module.create_local_sandbox.return_value = MagicMock(id="local", aexecute=AsyncMock())
            mock_import_module.return_value = module

            from coding_agent.sandboxes.providers.registry import create_sandbox

            sandbox = await create_sandbox("existing")

        assert sandbox.id == "local"
        mock_import_module.assert_called_once_with("coding_agent.sandboxes.providers.local")
        module.create_local_sandbox.assert_called_once_with("existing")

    async def test_create_sandbox_passes_langsmith_resource_overrides(self) -> None:
        with (
            patch("coding_agent.sandboxes.providers.registry.import_module") as mock_import_module,
            patch.dict("os.environ", {"SANDBOX_TYPE": "langsmith"}),
        ):
            module = MagicMock()
            module.create_langsmith_sandbox = AsyncMock(
                return_value=MagicMock(id="langsmith", aexecute=AsyncMock())
            )
            mock_import_module.return_value = module

            from coding_agent.sandboxes.providers.registry import create_sandbox

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
    """Tests for configure_github_proxy payload shape and error handling."""

    async def test_sends_correct_payload_shape(self) -> None:
        """Verify the PATCH request uses opaque headers with correct structure."""
        token = "ghs_testtoken123"
        expected_basic = base64.b64encode(f"x-access-token:{token}".encode()).decode()

        with (
            patch(
                "coding_agent.sandboxes.providers.langsmith.httpx2.AsyncClient"
            ) as mock_client_cls,
            patch.dict("os.environ", {"LANGSMITH_API_KEY": "ls-api-key"}),
        ):
            mock_client = MagicMock()
            mock_response = MagicMock()
            mock_response.raise_for_status = MagicMock()
            mock_client.patch = AsyncMock(return_value=mock_response)
            _mock_async_client(mock_client_cls, mock_client)

            await configure_github_proxy("sandbox-abc123", token)

            mock_client.patch.assert_called_once()
            call_kwargs = mock_client.patch.call_args
            payload = call_kwargs.kwargs["json"]

            assert "proxy_config" in payload
            rules = payload["proxy_config"]["rules"]
            assert [rule["name"] for rule in rules[:2]] == ["github-api", "github"]

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
            patch(
                "coding_agent.sandboxes.providers.langsmith.httpx2.AsyncClient"
            ) as mock_client_cls,
            patch.dict("os.environ", {"LANGSMITH_API_KEY": "ls-api-key"}),
        ):
            mock_client = MagicMock()
            mock_response = MagicMock()
            mock_response.raise_for_status = MagicMock()
            mock_client.patch = AsyncMock(return_value=mock_response)
            _mock_async_client(mock_client_cls, mock_client)

            await configure_github_proxy(
                "sandbox-abc123",
                "token",
                base_proxy_config={"rules": [custom_rule], "enabled": True},
            )

        proxy_config = mock_client.patch.call_args.kwargs["json"]["proxy_config"]
        assert proxy_config["enabled"] is True
        assert proxy_config["rules"][0] == custom_rule
        assert [rule["name"] for rule in proxy_config["rules"][1:3]] == ["github-api", "github"]

    async def test_removes_retired_langsmith_rule(self) -> None:
        stale_rule = {"name": "open-swe-langsmith", "headers": [{"value": "old-secret"}]}
        with (
            patch(
                "coding_agent.sandboxes.providers.langsmith.httpx2.AsyncClient"
            ) as mock_client_cls,
            patch.dict("os.environ", {"LANGSMITH_API_KEY": "control-key"}, clear=True),
        ):
            mock_client = MagicMock()
            response = MagicMock()
            response.raise_for_status = MagicMock()
            mock_client.patch = AsyncMock(return_value=response)
            _mock_async_client(mock_client_cls, mock_client)
            await configure_github_proxy(
                "sandbox-abc123",
                "github-token",
                base_proxy_config={"rules": [stale_rule]},
            )

        rules = mock_client.patch.call_args.kwargs["json"]["proxy_config"]["rules"]
        assert "open-swe-langsmith" not in [rule["name"] for rule in rules]
        assert "old-secret" not in str(rules)
        assert "LANGSMITH_API_KEY" not in str(rules)
        assert "control-key" not in str(rules)

    async def test_sends_to_correct_url(self) -> None:
        """Verify the PATCH hits the right endpoint."""
        with (
            patch(
                "coding_agent.sandboxes.providers.langsmith.httpx2.AsyncClient"
            ) as mock_client_cls,
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

            await configure_github_proxy("sandbox-xyz", "token")

            url = mock_client.patch.call_args.args[0]
            assert url == "https://test.api.smith.langchain.com/v2/sandboxes/boxes/sandbox-xyz"

    async def test_sends_api_key_header(self) -> None:
        """Verify the PATCH includes the LangSmith API key."""
        with (
            patch(
                "coding_agent.sandboxes.providers.langsmith.httpx2.AsyncClient"
            ) as mock_client_cls,
            patch.dict("os.environ", {"LANGSMITH_API_KEY": "my-api-key"}),
        ):
            mock_client = MagicMock()
            mock_response = MagicMock()
            mock_response.raise_for_status = MagicMock()
            mock_client.patch = AsyncMock(return_value=mock_response)
            _mock_async_client(mock_client_cls, mock_client)

            await configure_github_proxy("sandbox-abc", "token")

            headers = mock_client.patch.call_args.kwargs["headers"]
            assert headers == {"X-API-Key": "my-api-key"}

    async def test_uses_shared_credentials_despite_legacy_overrides(self) -> None:
        """Retired sandbox overrides must not select another workspace."""
        with (
            patch(
                "coding_agent.sandboxes.providers.langsmith.httpx2.AsyncClient"
            ) as mock_client_cls,
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

            await configure_github_proxy("sandbox-abc", "token")

            assert (
                mock_client.patch.call_args.args[0]
                == "https://shared.smith.langchain.com/v2/sandboxes/boxes/sandbox-abc"
            )
            assert mock_client.patch.call_args.kwargs["headers"] == {"X-API-Key": "shared-key"}

    async def test_retries_transient_http_error(self) -> None:
        """Transient proxy API errors should be retried on the same sandbox."""
        request = httpx2.Request(
            "PATCH", "https://api.smith.langchain.com/v2/sandboxes/boxes/sandbox-abc"
        )
        response = httpx2.Response(503, request=request)
        transient_error = httpx2.HTTPStatusError(
            "Server error",
            request=request,
            response=response,
        )
        with (
            patch(
                "coding_agent.sandboxes.providers.langsmith.httpx2.AsyncClient"
            ) as mock_client_cls,
            patch(
                "coding_agent.sandboxes.providers.langsmith.asyncio.sleep", new_callable=AsyncMock
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

            await configure_github_proxy("sandbox-abc", "token")

            assert mock_client.patch.call_count == 2
            mock_sleep.assert_called_once()

    async def test_raises_on_non_retryable_http_error(self) -> None:
        """Non-retryable HTTP errors should propagate without retrying."""
        request = httpx2.Request(
            "PATCH", "https://api.smith.langchain.com/v2/sandboxes/boxes/sandbox-abc"
        )
        response = httpx2.Response(403, request=request)
        error = httpx2.HTTPStatusError("Forbidden", request=request, response=response)
        with (
            patch(
                "coding_agent.sandboxes.providers.langsmith.httpx2.AsyncClient"
            ) as mock_client_cls,
            patch(
                "coding_agent.sandboxes.providers.langsmith.asyncio.sleep", new_callable=AsyncMock
            ) as mock_sleep,
            patch.dict("os.environ", {"LANGSMITH_API_KEY": "api-key"}),
        ):
            mock_client = MagicMock()
            failed_response = MagicMock()
            failed_response.raise_for_status.side_effect = error
            mock_client.patch = AsyncMock(return_value=failed_response)
            _mock_async_client(mock_client_cls, mock_client)

            with pytest.raises(httpx2.HTTPStatusError):
                await configure_github_proxy("sandbox-abc", "token")

            mock_client.patch.assert_called_once()
            mock_sleep.assert_not_called()

    async def test_error_message_carries_response_body(self) -> None:
        """The API's explanation must survive into the raised error."""
        request = httpx2.Request(
            "PATCH", "https://api.smith.langchain.com/v2/sandboxes/boxes/sandbox-abc"
        )
        response = httpx2.Response(403, request=request, text="sandbox belongs to another tenant")
        error = httpx2.HTTPStatusError("Forbidden", request=request, response=response)
        with (
            patch(
                "coding_agent.sandboxes.providers.langsmith.httpx2.AsyncClient"
            ) as mock_client_cls,
            patch.dict("os.environ", {"LANGSMITH_API_KEY": "api-key"}),
        ):
            mock_client = MagicMock()
            failed_response = MagicMock()
            failed_response.raise_for_status.side_effect = error
            mock_client.patch = AsyncMock(return_value=failed_response)
            _mock_async_client(mock_client_cls, mock_client)

            with pytest.raises(httpx2.HTTPStatusError, match="another tenant"):
                await configure_github_proxy("sandbox-abc", "token")


class TestConfigureGithubProxyStartsStoppedSandbox:
    """A 400 means the sandbox is not ready; start it and retry the update."""

    @staticmethod
    def _not_ready_error() -> httpx2.HTTPStatusError:
        request = httpx2.Request(
            "PATCH", "https://api.smith.langchain.com/v2/sandboxes/boxes/sandbox-abc"
        )
        response = httpx2.Response(
            400,
            request=request,
            text='sandbox "sandbox-abc" is in "stopped" state, must be "ready"',
        )
        return httpx2.HTTPStatusError("Bad request", request=request, response=response)

    async def test_starts_sandbox_then_retries(self) -> None:
        with (
            patch(
                "coding_agent.sandboxes.providers.langsmith.httpx2.AsyncClient"
            ) as mock_client_cls,
            patch(
                "coding_agent.sandboxes.providers.langsmith.get_async_sandbox_client"
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

            await configure_github_proxy("sandbox-abc", "token")

            sandbox_client.start_sandbox.assert_awaited_once()
            assert sandbox_client.start_sandbox.await_args.args[0] == "sandbox-abc"
            sandbox_client.aclose.assert_awaited_once()
            assert mock_client.patch.call_count == 2

    async def test_retries_even_when_start_fails(self) -> None:
        """A failed start is logged, not fatal: the retry reports the real state."""
        with (
            patch(
                "coding_agent.sandboxes.providers.langsmith.httpx2.AsyncClient"
            ) as mock_client_cls,
            patch(
                "coding_agent.sandboxes.providers.langsmith.get_async_sandbox_client"
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

            with pytest.raises(httpx2.HTTPStatusError, match="stopped"):
                await configure_github_proxy("sandbox-abc", "token")

            sandbox_client.start_sandbox.assert_awaited_once()
            sandbox_client.aclose.assert_awaited_once()
            assert mock_client.patch.call_count == 2

    async def test_does_not_start_twice(self) -> None:
        """Only one start attempt per configure call, even if the retry also 400s."""
        with (
            patch(
                "coding_agent.sandboxes.providers.langsmith.httpx2.AsyncClient"
            ) as mock_client_cls,
            patch(
                "coding_agent.sandboxes.providers.langsmith.get_async_sandbox_client"
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

            with pytest.raises(httpx2.HTTPStatusError):
                await configure_github_proxy("sandbox-abc", "token")

            sandbox_client.start_sandbox.assert_awaited_once()


class TestGitHubProxyCredentials:
    """Which token the sandbox proxy is configured with, and when it is skipped."""

    @pytest.mark.asyncio
    async def test_uses_installation_token_for_langsmith(self) -> None:
        with (
            patch(
                "agent.sandboxes.credentials.get_github_app_installation_token_with_expiry",
                new_callable=AsyncMock,
                return_value=("ghs_install", None),
            ) as mock_get_token,
            patch(
                "agent.sandboxes.credentials.configure_github_proxy", new_callable=AsyncMock
            ) as mock_proxy,
            patch.dict("os.environ", {"SANDBOX_TYPE": "langsmith", "LANGSMITH_API_KEY": "ls-key"}),
        ):
            installed = await GitHubProxyCredentials().install("sandbox-123")

        assert installed.base_config is None
        mock_proxy.assert_awaited_once_with("sandbox-123", "ghs_install")
        mock_get_token.assert_awaited_once_with()

    @pytest.mark.asyncio
    async def test_uses_the_callers_token_and_records_its_scope(self) -> None:
        base_config = {"rules": [{"name": "public-api", "match_hosts": ["example.com"]}]}
        with (
            patch(
                "agent.sandboxes.credentials.get_github_app_installation_token_with_expiry",
                new_callable=AsyncMock,
            ) as mock_get_token,
            patch(
                "agent.sandboxes.credentials.configure_github_proxy", new_callable=AsyncMock
            ) as mock_proxy,
            patch("agent.sandboxes.credentials.record_proxy_token_expiry") as mock_record,
            patch.dict("os.environ", {"SANDBOX_TYPE": "langsmith"}),
        ):
            installed = await GitHubProxyCredentials(
                token="ghs_caller", repositories=["open-swe"]
            ).install("sandbox-123", thread_id="thread-1", base_proxy_config=base_config)
            await installed.bind("thread-1")

        mock_get_token.assert_not_awaited()
        mock_proxy.assert_awaited_once_with(
            "sandbox-123", "ghs_caller", base_proxy_config=base_config
        )
        assert installed.base_config == base_config
        mock_record.assert_called_once_with(
            "thread-1",
            None,
            repositories=["open-swe"],
            permissions=None,
            base_proxy_config=base_config,
        )

    @pytest.mark.asyncio
    async def test_skips_the_proxy_for_other_providers(self) -> None:
        with (
            patch(
                "agent.sandboxes.credentials.configure_github_proxy", new_callable=AsyncMock
            ) as mock_proxy,
            patch("agent.sandboxes.credentials.record_proxy_token_expiry") as mock_record,
            patch.dict("os.environ", {"SANDBOX_TYPE": "daytona"}),
        ):
            installed = await GitHubProxyCredentials().install("sandbox-456")
            await installed.bind("thread-1")

        assert installed.base_config is None
        mock_proxy.assert_not_called()
        mock_record.assert_not_called()

    @pytest.mark.asyncio
    async def test_raises_when_the_installation_token_mint_fails(self) -> None:
        with (
            patch(
                "agent.sandboxes.credentials.get_github_app_installation_token_with_expiry",
                new_callable=AsyncMock,
                return_value=(None, None),
            ) as mock_get_token,
            patch("agent.sandboxes.credentials.configure_github_proxy", new_callable=AsyncMock),
            patch.dict("os.environ", {"SANDBOX_TYPE": "langsmith", "LANGSMITH_API_KEY": "ls-key"}),
        ):
            with pytest.raises(ValueError, match="installation token is unavailable"):
                await GitHubProxyCredentials().install("sandbox-123", thread_id="thread-123")

            mock_get_token.assert_awaited_once_with()


class TestOpenSweCreateConfig:
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
                "agent.sandboxes.lifecycle.resolve_environment",
                new_callable=AsyncMock,
                return_value=environment,
            ),
            patch(
                "coding_agent.sandboxes.lifecycle.create_sandbox", new_callable=AsyncMock
            ) as mock_create,
            patch.dict("os.environ", {"SANDBOX_TYPE": "langsmith"}),
        ):
            mock_create.return_value = MagicMock(id="sandbox-123", aexecute=AsyncMock())

            config = await resolve_open_swe_create_config("large")
            await config.boot()

        mock_create.assert_awaited_once_with(
            snapshot_id="env-snap",
            mem_bytes=16,
            vcpus=8,
            fs_capacity_bytes=128,
            create_params=environment.create_params,
        )
        # The environment's proxy rules are what new credentials are layered onto.
        assert config.proxy_config == environment.create_params["proxy_config"]


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
                "coding_agent.sandboxes.lifecycle.get_sandbox_id_from_metadata",
                new_callable=AsyncMock,
                return_value="sandbox-cached",
            ),
            patch(
                "coding_agent.sandboxes.lifecycle.get_sandbox_metadata",
                new_callable=AsyncMock,
                return_value={
                    "sandbox_id": "sandbox-cached",
                    "sandbox_base_proxy_config": base_proxy_config,
                },
            ),
            patch(
                "agent.sandboxes.credentials.get_github_app_installation_token_with_expiry",
                new_callable=AsyncMock,
                return_value=("ghs_fresh", None),
            ),
            patch(
                "agent.sandboxes.credentials.configure_github_proxy", new_callable=AsyncMock
            ) as mock_proxy,
            patch(
                "agent.server.resolve_sandbox_work_dir",
                new_callable=AsyncMock,
                return_value="/workspace",
            ),
            patch("coding_agent.builder.make_model", return_value=MagicMock()),
            patch("agent.server.construct_system_prompt", return_value="prompt"),
            patch("coding_agent.builder.create_deep_agent", side_effect=fake_create_deep_agent),
            patch.dict(
                "coding_agent.sandboxes.lifecycle.SANDBOX_BACKENDS",
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
                "coding_agent.sandboxes.lifecycle.get_sandbox_id_from_metadata",
                new_callable=AsyncMock,
                return_value="sandbox-existing",
            ),
            patch(
                "coding_agent.sandboxes.lifecycle.get_sandbox_metadata",
                new_callable=AsyncMock,
                return_value={"sandbox_id": "sandbox-existing"},
            ),
            patch(
                "coding_agent.sandboxes.lifecycle.create_sandbox",
                new_callable=AsyncMock,
                return_value=mock_sandbox,
            ) as mock_create,
            patch(
                "agent.sandboxes.credentials.get_github_app_installation_token_with_expiry",
                new_callable=AsyncMock,
                return_value=("ghs_fresh", None),
            ),
            patch(
                "agent.sandboxes.credentials.configure_github_proxy", new_callable=AsyncMock
            ) as mock_proxy,
            patch(
                "agent.server.resolve_sandbox_work_dir",
                new_callable=AsyncMock,
                return_value="/workspace",
            ),
            patch("coding_agent.builder.make_model", return_value=MagicMock()),
            patch("agent.server.construct_system_prompt", return_value="prompt"),
            patch("coding_agent.builder.create_deep_agent", side_effect=fake_create_deep_agent),
            patch.dict("coding_agent.sandboxes.lifecycle.SANDBOX_BACKENDS", {}, clear=True),
            patch.dict("agent.github.proxy._PROXY_BASE_CONFIGS", {}, clear=True),
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
