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

        with patch("agent.integrations.langsmith.httpx.Client") as mock_client_cls:
            mock_client = MagicMock()
            mock_response = MagicMock()
            mock_response.raise_for_status = MagicMock()
            mock_client.patch.return_value = mock_response
            mock_client_cls.return_value.__enter__ = MagicMock(return_value=mock_client)
            mock_client_cls.return_value.__exit__ = MagicMock(return_value=False)

            _configure_github_proxy("sandbox-abc123", token, "ls-api-key")

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
                "os.environ", {"LANGSMITH_ENDPOINT": "https://test.api.smith.langchain.com"}
            ),
        ):
            mock_client = MagicMock()
            mock_response = MagicMock()
            mock_response.raise_for_status = MagicMock()
            mock_client.patch.return_value = mock_response
            mock_client_cls.return_value.__enter__ = MagicMock(return_value=mock_client)
            mock_client_cls.return_value.__exit__ = MagicMock(return_value=False)

            _configure_github_proxy("sandbox-xyz", "token", "api-key")

            url = mock_client.patch.call_args.args[0]
            assert url == "https://test.api.smith.langchain.com/v2/sandboxes/boxes/sandbox-xyz"

    def test_sends_api_key_header(self) -> None:
        """Verify the PATCH includes the LangSmith API key."""
        with patch("agent.integrations.langsmith.httpx.Client") as mock_client_cls:
            mock_client = MagicMock()
            mock_response = MagicMock()
            mock_response.raise_for_status = MagicMock()
            mock_client.patch.return_value = mock_response
            mock_client_cls.return_value.__enter__ = MagicMock(return_value=mock_client)
            mock_client_cls.return_value.__exit__ = MagicMock(return_value=False)

            _configure_github_proxy("sandbox-abc", "token", "my-api-key")

            headers = mock_client.patch.call_args.kwargs["headers"]
            assert headers == {"X-API-Key": "my-api-key"}

    def test_raises_on_http_error(self) -> None:
        """Verify HTTP errors propagate."""
        with patch("agent.integrations.langsmith.httpx.Client") as mock_client_cls:
            mock_client = MagicMock()
            mock_client.patch.side_effect = httpx.HTTPStatusError(
                "Server error", request=MagicMock(), response=MagicMock(status_code=500)
            )
            mock_client_cls.return_value.__enter__ = MagicMock(return_value=mock_client)
            mock_client_cls.return_value.__exit__ = MagicMock(return_value=False)

            with pytest.raises(httpx.HTTPStatusError):
                _configure_github_proxy("sandbox-abc", "token", "api-key")


class TestCreateSandboxWithProxy:
    """Tests for _create_sandbox_with_proxy token source selection."""

    @pytest.mark.asyncio
    async def test_prefers_installation_token(self) -> None:
        """Installation token should be used over user token."""
        with (
            patch(
                "agent.server.get_github_app_installation_token",
                new_callable=AsyncMock,
                return_value="ghs_install",
            ),
            patch("agent.server.create_langsmith_sandbox") as mock_create,
        ):
            mock_create.return_value = MagicMock()

            from agent.server import _create_sandbox_with_proxy

            await _create_sandbox_with_proxy("user_token")

            mock_create.assert_called_once_with(None, "ghs_install")

    @pytest.mark.asyncio
    async def test_falls_back_to_user_token(self) -> None:
        """User token should be used when installation token is unavailable."""
        with (
            patch(
                "agent.server.get_github_app_installation_token",
                new_callable=AsyncMock,
                return_value=None,
            ),
            patch("agent.server.create_langsmith_sandbox") as mock_create,
        ):
            mock_create.return_value = MagicMock()

            from agent.server import _create_sandbox_with_proxy

            await _create_sandbox_with_proxy("user_token")

            mock_create.assert_called_once_with(None, "user_token")

    @pytest.mark.asyncio
    async def test_raises_when_no_token_available(self) -> None:
        """Should raise ValueError when both tokens are None."""
        with patch(
            "agent.server.get_github_app_installation_token",
            new_callable=AsyncMock,
            return_value=None,
        ):
            from agent.server import _create_sandbox_with_proxy

            with pytest.raises(ValueError, match="no GitHub token available"):
                await _create_sandbox_with_proxy(None)
