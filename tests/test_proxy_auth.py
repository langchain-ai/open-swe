"""Tests for GitHub proxy auth configuration."""

from __future__ import annotations

import base64
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from agent.integrations.langsmith import _configure_github_proxy


class TestConfigureGithubProxy:
    """Tests for _configure_github_proxy payload shape and error handling."""

    def test_sends_correct_payload_shape(self) -> None:
        """Verify the PATCH request uses opaque headers with correct structure."""
        token = "ghs_testtoken123"
        expected_basic = base64.b64encode(f"x-access-token:{token}".encode()).decode()

        with (
            patch("agent.integrations.langsmith.httpx.Client") as mock_client_cls,
            patch.dict("os.environ", {"LANGSMITH_API_KEY": "ls-api-key"}),
        ):
            mock_client = MagicMock()
            mock_response = MagicMock()
            mock_response.raise_for_status = MagicMock()
            mock_client.patch.return_value = mock_response
            mock_client_cls.return_value.__enter__ = MagicMock(return_value=mock_client)
            mock_client_cls.return_value.__exit__ = MagicMock(return_value=False)

            _configure_github_proxy("sandbox-abc123", token)

            mock_client.patch.assert_called_once()
            call_kwargs = mock_client.patch.call_args
            payload = call_kwargs.kwargs["json"]

            # Verify proxy_config structure
            assert "proxy_config" in payload
            rules = payload["proxy_config"]["rules"]
            assert len(rules) == 1

            rule = rules[0]
            assert rule["name"] == "github"
            assert rule["match_hosts"] == ["github.com", "*.github.com"]

            headers = rule["headers"]
            assert len(headers) == 1
            assert headers[0]["name"] == "Authorization"
            assert headers[0]["type"] == "opaque"
            assert headers[0]["value"] == f"Basic {expected_basic}"

    def test_sends_to_correct_url(self) -> None:
        """Verify the PATCH hits the right endpoint."""
        with (
            patch("agent.integrations.langsmith.httpx.Client") as mock_client_cls,
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
            mock_client.patch.return_value = mock_response
            mock_client_cls.return_value.__enter__ = MagicMock(return_value=mock_client)
            mock_client_cls.return_value.__exit__ = MagicMock(return_value=False)

            _configure_github_proxy("sandbox-xyz", "token")

            url = mock_client.patch.call_args.args[0]
            assert url == "https://test.api.smith.langchain.com/v2/sandboxes/boxes/sandbox-xyz"

    def test_sends_api_key_header(self) -> None:
        """Verify the PATCH includes the LangSmith API key."""
        with (
            patch("agent.integrations.langsmith.httpx.Client") as mock_client_cls,
            patch.dict("os.environ", {"LANGSMITH_API_KEY": "my-api-key"}),
        ):
            mock_client = MagicMock()
            mock_response = MagicMock()
            mock_response.raise_for_status = MagicMock()
            mock_client.patch.return_value = mock_response
            mock_client_cls.return_value.__enter__ = MagicMock(return_value=mock_client)
            mock_client_cls.return_value.__exit__ = MagicMock(return_value=False)

            _configure_github_proxy("sandbox-abc", "token")

            headers = mock_client.patch.call_args.kwargs["headers"]
            assert headers == {"X-API-Key": "my-api-key"}

    def test_raises_on_http_error(self) -> None:
        """Verify HTTP errors propagate."""
        with (
            patch("agent.integrations.langsmith.httpx.Client") as mock_client_cls,
            patch.dict("os.environ", {"LANGSMITH_API_KEY": "api-key"}),
        ):
            mock_client = MagicMock()
            mock_client.patch.side_effect = httpx.HTTPStatusError(
                "Server error", request=MagicMock(), response=MagicMock(status_code=500)
            )
            mock_client_cls.return_value.__enter__ = MagicMock(return_value=mock_client)
            mock_client_cls.return_value.__exit__ = MagicMock(return_value=False)

            with pytest.raises(httpx.HTTPStatusError):
                _configure_github_proxy("sandbox-abc", "token")


class TestCreateSandboxWithProxy:
    """Tests for _create_sandbox_with_proxy token source selection."""

    @pytest.mark.asyncio
    async def test_uses_installation_token_for_langsmith(self) -> None:
        """Installation token should be used for proxy auth on langsmith sandboxes."""
        with (
            patch(
                "agent.server.get_github_app_installation_token",
                new_callable=AsyncMock,
                return_value="ghs_install",
            ),
            patch("agent.server.create_sandbox") as mock_create,
            patch("agent.server._configure_github_proxy") as mock_proxy,
            patch.dict("os.environ", {"SANDBOX_TYPE": "langsmith", "LANGSMITH_API_KEY": "ls-key"}),
        ):
            mock_create.return_value = MagicMock(id="sandbox-123")

            from agent.server import _create_sandbox_with_proxy

            await _create_sandbox_with_proxy()

            mock_create.assert_called_once_with()
            mock_proxy.assert_called_once_with("sandbox-123", "ghs_install")

    @pytest.mark.asyncio
    async def test_skips_proxy_for_non_langsmith(self) -> None:
        """Non-langsmith sandboxes should skip proxy configuration."""
        with (
            patch("agent.server.create_sandbox") as mock_create,
            patch("agent.server._configure_github_proxy") as mock_proxy,
            patch.dict("os.environ", {"SANDBOX_TYPE": "daytona"}),
        ):
            mock_create.return_value = MagicMock(id="sandbox-456")

            from agent.server import _create_sandbox_with_proxy

            await _create_sandbox_with_proxy()

            mock_create.assert_called_once_with()
            mock_proxy.assert_not_called()

    @pytest.mark.asyncio
    async def test_raises_when_no_installation_token_for_langsmith(self) -> None:
        """Should raise ValueError when installation token is unavailable for langsmith."""
        with (
            patch("agent.server.create_sandbox") as mock_create,
            patch(
                "agent.server.get_github_app_installation_token",
                new_callable=AsyncMock,
                return_value=None,
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
    def _execution_config() -> dict:
        return {
            "configurable": {
                "__is_for_execution__": True,
                "thread_id": "thread-123",
                "repo": {"owner": "langchain-ai", "name": "open-swe"},
            },
            "metadata": {},
        }

    @pytest.mark.asyncio
    async def test_refreshes_proxy_for_cached_langsmith_sandbox(self) -> None:
        """Cached sandboxes should get a fresh proxy token before git operations."""
        config = self._execution_config()
        mock_sandbox = MagicMock(id="sandbox-cached")

        with (
            patch(
                "agent.server.resolve_github_token",
                new_callable=AsyncMock,
                return_value=("ghp", "enc"),
            ),
            patch(
                "agent.server.get_sandbox_id_from_metadata",
                new_callable=AsyncMock,
                return_value="sandbox-cached",
            ),
            patch(
                "agent.server.get_github_app_installation_token",
                new_callable=AsyncMock,
                return_value="ghs_fresh",
            ),
            patch("agent.server._configure_github_proxy") as mock_proxy,
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
            patch("agent.server.create_deep_agent", return_value=_DummyAgent()),
            patch.dict(
                "agent.server.SANDBOX_BACKENDS",
                {"thread-123": mock_sandbox},
                clear=True,
            ),
            patch.dict("os.environ", {"SANDBOX_TYPE": "langsmith"}),
        ):
            from agent.server import get_agent

            await get_agent(config)

            mock_proxy.assert_called_once_with("sandbox-cached", "ghs_fresh")

    @pytest.mark.asyncio
    async def test_refreshes_proxy_when_reconnecting_to_existing_langsmith_sandbox(self) -> None:
        """Reconnected sandboxes should also get a fresh proxy token."""
        config = self._execution_config()
        mock_sandbox = MagicMock(id="sandbox-existing")

        with (
            patch(
                "agent.server.resolve_github_token",
                new_callable=AsyncMock,
                return_value=("ghp", "enc"),
            ),
            patch(
                "agent.server.get_sandbox_id_from_metadata",
                new_callable=AsyncMock,
                return_value="sandbox-existing",
            ),
            patch("agent.server.create_sandbox", return_value=mock_sandbox) as mock_create,
            patch(
                "agent.server.get_github_app_installation_token",
                new_callable=AsyncMock,
                return_value="ghs_fresh",
            ),
            patch("agent.server._configure_github_proxy") as mock_proxy,
            patch(
                "agent.server.aresolve_sandbox_work_dir",
                new_callable=AsyncMock,
                return_value="/workspace",
            ),
            patch("agent.server.make_model", return_value=MagicMock()),
            patch("agent.server.construct_system_prompt", return_value="prompt"),
            patch("agent.server.create_deep_agent", return_value=_DummyAgent()),
            patch.dict("agent.server.SANDBOX_BACKENDS", {}, clear=True),
            patch.dict("os.environ", {"SANDBOX_TYPE": "langsmith"}),
        ):
            from agent.server import get_agent

            await get_agent(config)

            mock_create.assert_called_once_with("sandbox-existing")
            mock_proxy.assert_called_once_with("sandbox-existing", "ghs_fresh")
