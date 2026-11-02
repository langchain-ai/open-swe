"""Tests for GitHub proxy auth configuration."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import httpx2
import pytest

from openswe.github.sandbox_access import SandboxGitHubAccess
from openswe.sandboxes.providers.langsmith import (
    PROXY_CONFIG_RETRY_WINDOW_SECONDS,
    configure_sandbox_proxy,
)
from openswe.sandboxes.tool_access import SANDBOX_PROXY_CONFIG_METADATA_KEY


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
        response = httpx2.Response(503, request=request, headers={"Retry-After": "7"})
        transient_error = httpx2.HTTPStatusError(
            "Server error",
            request=request,
            response=response,
        )
        with (
            patch("openswe.sandboxes.providers.langsmith.httpx2.AsyncClient") as mock_client_cls,
            patch("openswe.sandboxes.retry.asyncio.sleep", new_callable=AsyncMock) as mock_sleep,
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
            mock_sleep.assert_awaited_once_with(7.0)

    @pytest.mark.parametrize("suspend_seconds", [170.0, PROXY_CONFIG_RETRY_WINDOW_SECONDS + 30])
    async def test_outlasts_a_host_suspend_within_the_retry_window(
        self, suspend_seconds: float
    ) -> None:
        """While the host suspends an idle box the API fails the push with a 500."""
        request = httpx2.Request(
            "PATCH", "https://api.smith.langchain.com/v2/sandboxes/boxes/sandbox-abc"
        )
        error = httpx2.HTTPStatusError(
            "Server error",
            request=request,
            response=httpx2.Response(
                500, request=request, text="failed to push proxy config to sandbox"
            ),
        )
        clock = SimpleNamespace(now=0.0)

        async def sleep(delay: float) -> None:
            clock.now += delay

        def respond(*_args: object, **_kwargs: object) -> MagicMock:
            response = MagicMock()
            if clock.now < suspend_seconds:
                response.raise_for_status.side_effect = error
            return response

        with (
            patch("openswe.sandboxes.providers.langsmith.httpx2.AsyncClient") as mock_client_cls,
            patch("openswe.sandboxes.retry.asyncio.sleep", sleep),
            patch("openswe.sandboxes.retry.time", SimpleNamespace(monotonic=lambda: clock.now)),
            patch.dict("os.environ", {"LANGSMITH_API_KEY": "api-key"}),
        ):
            mock_client = MagicMock()
            mock_client.patch = AsyncMock(side_effect=respond)
            _mock_async_client(mock_client_cls, mock_client)

            if suspend_seconds < PROXY_CONFIG_RETRY_WINDOW_SECONDS:
                await configure_sandbox_proxy("sandbox-abc", "token")
            else:
                with pytest.raises(httpx2.HTTPStatusError, match="failed to push proxy config"):
                    await configure_sandbox_proxy("sandbox-abc", "token")
                assert clock.now == PROXY_CONFIG_RETRY_WINDOW_SECONDS

    async def test_raises_on_non_retryable_http_error(self) -> None:
        """Non-retryable HTTP errors should propagate without retrying."""
        request = httpx2.Request(
            "PATCH", "https://api.smith.langchain.com/v2/sandboxes/boxes/sandbox-abc"
        )
        response = httpx2.Response(403, request=request)
        error = httpx2.HTTPStatusError("Forbidden", request=request, response=response)
        with (
            patch("openswe.sandboxes.providers.langsmith.httpx2.AsyncClient") as mock_client_cls,
            patch("openswe.sandboxes.retry.asyncio.sleep", new_callable=AsyncMock) as mock_sleep,
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
        ):
            from openswe.sandboxes.lifecycle import (
                SandboxUnreachableError,
                _refresh_github_proxy_or_fail,
            )

            with pytest.raises(SandboxUnreachableError) as excinfo:
                await _refresh_github_proxy_or_fail(mock_sandbox, "thread-123")

            assert excinfo.value.sandbox_id == "sandbox-stale"


class TestRefreshThreadGithubProxy:
    async def test_a_replica_that_never_connected_still_gives_the_box_a_fresh_token(self) -> None:
        """Nothing is read from this process: the box and its rules come from the thread."""
        proxy_config = {"rules": [{"name": "public-api"}]}
        metadata = {"sandbox_id": "sandbox-abc", SANDBOX_PROXY_CONFIG_METADATA_KEY: proxy_config}
        with (
            patch(
                "openswe.sandboxes.lifecycle.get_sandbox_metadata",
                new_callable=AsyncMock,
                return_value=metadata,
            ),
            patch(
                "openswe.sandboxes.lifecycle.workspace_token",
                new_callable=AsyncMock,
                return_value=SandboxGitHubAccess("ghs_fresh", "2026-10-27T20:00:00Z"),
            ),
            patch(
                "openswe.sandboxes.lifecycle.thread_token_repositories",
                new_callable=AsyncMock,
                return_value=None,
            ),
            patch(
                "openswe.sandboxes.lifecycle.configure_sandbox_proxy", new_callable=AsyncMock
            ) as configure,
        ):
            from openswe.sandboxes.lifecycle import refresh_thread_github_proxy
            from openswe.sandboxes.state import SANDBOX_BACKENDS

            SANDBOX_BACKENDS.pop("thread-123", None)
            expires_at = await refresh_thread_github_proxy("thread-123")

        configure.assert_awaited_once_with(
            "sandbox-abc", "ghs_fresh", base_proxy_config=proxy_config, thread_id="thread-123"
        )
        assert expires_at is not None
        assert expires_at.isoformat() == "2026-10-27T20:00:00+00:00"
