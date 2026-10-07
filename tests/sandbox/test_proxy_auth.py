"""Tests for GitHub proxy auth configuration."""

from unittest.mock import AsyncMock, MagicMock, patch

import httpx2
import pytest

from openswe.github.sandbox_access import SandboxGitHubAccess
from openswe.sandboxes.providers.langsmith import (
    configure_sandbox_proxy,
)


def _mock_async_client(mock_client_cls: MagicMock, inner: MagicMock) -> None:
    """Wire an ``httpx2.AsyncClient`` mock class to yield ``inner`` from its
    async context manager."""
    mock_client_cls.return_value.__aenter__ = AsyncMock(return_value=inner)
    mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=False)


class TestConfigureSandboxProxy:
    """Tests for configure_sandbox_proxy payload shape and error handling."""

    async def test_without_token_sends_no_github_credentials(self) -> None:
        with (
            patch("openswe.sandboxes.providers.langsmith.httpx2.AsyncClient") as mock_client_cls,
            patch.dict("os.environ", {"LANGSMITH_API_KEY": "ls-api-key"}),
        ):
            mock_client = MagicMock()
            mock_client.patch = AsyncMock(return_value=MagicMock())
            _mock_async_client(mock_client_cls, mock_client)

            await configure_sandbox_proxy(
                "sandbox-abc123",
                None,
                base_proxy_config={"rules": [{"name": "github", "headers": [{"value": "old"}]}]},
            )

            rules = mock_client.patch.call_args.kwargs["json"]["proxy_config"]["rules"]
            assert not {"github", "github-api"} & {rule["name"] for rule in rules}

    async def test_preserves_custom_proxy_config_when_adding_github_auth(self) -> None:
        custom_rule = {"name": "public-api", "match_hosts": ["example.com"]}
        with (
            patch("openswe.sandboxes.providers.langsmith.httpx2.AsyncClient") as mock_client_cls,
            patch.dict("os.environ", {"LANGSMITH_API_KEY": "ls-api-key"}),
        ):
            mock_client = MagicMock()
            mock_response = MagicMock()
            mock_response.raise_for_status = MagicMock()
            mock_client.patch = AsyncMock(return_value=mock_response)
            _mock_async_client(mock_client_cls, mock_client)

            await configure_sandbox_proxy(
                "sandbox-abc123",
                "token",
                base_proxy_config={"rules": [custom_rule], "enabled": True},
            )

        proxy_config = mock_client.patch.call_args.kwargs["json"]["proxy_config"]
        assert proxy_config["enabled"] is True
        assert custom_rule in proxy_config["rules"]
        assert [rule["name"] for rule in proxy_config["rules"][:2]] == ["github-api", "github"]

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
            patch("openswe.sandboxes.providers.langsmith.httpx2.AsyncClient") as mock_client_cls,
            patch(
                "openswe.sandboxes.providers.langsmith.asyncio.sleep", new_callable=AsyncMock
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

            await configure_sandbox_proxy("sandbox-abc", "token")

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
            patch("openswe.sandboxes.providers.langsmith.httpx2.AsyncClient") as mock_client_cls,
            patch(
                "openswe.sandboxes.providers.langsmith.asyncio.sleep", new_callable=AsyncMock
            ) as mock_sleep,
            patch.dict("os.environ", {"LANGSMITH_API_KEY": "api-key"}),
        ):
            mock_client = MagicMock()
            failed_response = MagicMock()
            failed_response.raise_for_status.side_effect = error
            mock_client.patch = AsyncMock(return_value=failed_response)
            _mock_async_client(mock_client_cls, mock_client)

            with pytest.raises(httpx2.HTTPStatusError):
                await configure_sandbox_proxy("sandbox-abc", "token")

            mock_client.patch.assert_called_once()
            mock_sleep.assert_not_called()


class TestConfigureSandboxProxyStartsStoppedSandbox:
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
            patch("openswe.sandboxes.providers.langsmith.httpx2.AsyncClient") as mock_client_cls,
            patch(
                "openswe.sandboxes.providers.langsmith.get_async_sandbox_client"
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

            await configure_sandbox_proxy("sandbox-abc", "token")

            sandbox_client.start_sandbox.assert_awaited_once()
            assert sandbox_client.start_sandbox.await_args.args[0] == "sandbox-abc"
            sandbox_client.aclose.assert_awaited_once()
            assert mock_client.patch.call_count == 2


class TestRefreshProxyOnSandboxReuse:
    @pytest.mark.asyncio
    async def test_proxy_refresh_failure_raises_instead_of_replacing(self) -> None:
        """A sandbox we can't reconfigure fails the run, and is never swapped out.

        Replacing it would hand the agent an empty filesystem and discard any
        work the old sandbox still held.
        """
        mock_sandbox = MagicMock(id="sandbox-stale", aexecute=AsyncMock())
        request = httpx2.Request(
            "PATCH", "https://api.smith.langchain.com/v2/sandboxes/boxes/sandbox-stale"
        )
        response = httpx2.Response(400, request=request)

        with (
            patch(
                "openswe.sandboxes.lifecycle.workspace_token",
                new_callable=AsyncMock,
                return_value=SandboxGitHubAccess("ghs_fresh"),
            ),
            patch(
                "openswe.sandboxes.lifecycle.configure_sandbox_proxy",
                new_callable=AsyncMock,
                side_effect=httpx2.HTTPStatusError(
                    "Bad request",
                    request=request,
                    response=response,
                ),
            ),
            patch(
                "openswe.sandboxes.lifecycle._create_sandbox_with_proxy", new_callable=AsyncMock
            ) as mock_create,
            patch.dict("os.environ", {"SANDBOX_TYPE": "langsmith"}),
        ):
            from openswe.sandboxes.lifecycle import (
                SandboxUnreachableError,
                _refresh_github_proxy_or_fail,
            )

            with pytest.raises(SandboxUnreachableError) as excinfo:
                await _refresh_github_proxy_or_fail(mock_sandbox, "thread-123")

            assert excinfo.value.sandbox_id == "sandbox-stale"
            mock_create.assert_not_awaited()
