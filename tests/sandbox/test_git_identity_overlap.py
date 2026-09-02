"""The bot's git identity is written while the GitHub proxy is being configured.

The identity needs the sandbox, not the proxy, so running it after the proxy
chain put a full sandbox round trip — over a second on a cold box — on the
critical path before the run's first model call.
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agent.sandboxes.lifecycle import _create_sandbox_with_proxy


def _backend(started: asyncio.Event) -> MagicMock:
    async def aexecute(_command: str) -> str:
        started.set()
        return "ok"

    return MagicMock(id="sandbox-new", aexecute=AsyncMock(side_effect=aexecute))


@pytest.mark.asyncio
async def test_identity_is_written_while_the_proxy_is_configured() -> None:
    started = asyncio.Event()
    backend = _backend(started)

    async def configure(*_args: object, **_kwargs: object) -> None:
        # Serial ordering would leave the identity unstarted until this returns.
        await asyncio.wait_for(started.wait(), timeout=2)

    with (
        patch(
            "agent.sandboxes.lifecycle.create_sandbox", new_callable=AsyncMock, return_value=backend
        ),
        patch(
            "agent.sandboxes.lifecycle._resolve_sandbox_create_config",
            new_callable=AsyncMock,
            return_value=("snap", {}, {}),
        ),
        patch(
            "agent.sandboxes.lifecycle._resolve_proxy_token",
            new_callable=AsyncMock,
            return_value=("token", None, None),
        ),
        patch("agent.sandboxes.lifecycle._configure_github_proxy", side_effect=configure),
        patch("agent.sandboxes.lifecycle.record_proxy_token_expiry"),
    ):
        assert await _create_sandbox_with_proxy(thread_id="thread-overlap") is backend

    backend.aexecute.assert_awaited_once()


@pytest.mark.asyncio
async def test_identity_failure_fails_the_sandbox() -> None:
    backend = MagicMock(
        id="sandbox-new", aexecute=AsyncMock(side_effect=RuntimeError("identity failed"))
    )

    with (
        patch(
            "agent.sandboxes.lifecycle.create_sandbox", new_callable=AsyncMock, return_value=backend
        ),
        patch(
            "agent.sandboxes.lifecycle._resolve_sandbox_create_config",
            new_callable=AsyncMock,
            return_value=("snap", {}, {}),
        ),
        patch(
            "agent.sandboxes.lifecycle._resolve_proxy_token",
            new_callable=AsyncMock,
            return_value=("token", None, None),
        ),
        patch("agent.sandboxes.lifecycle._configure_github_proxy", new_callable=AsyncMock),
        patch("agent.sandboxes.lifecycle.record_proxy_token_expiry"),
        pytest.raises(RuntimeError, match="identity failed"),
    ):
        await _create_sandbox_with_proxy(thread_id="thread-identity-fails")


@pytest.mark.asyncio
async def test_a_failed_proxy_does_not_leave_the_identity_write_running() -> None:
    release = asyncio.Event()

    async def aexecute(_command: str) -> str:
        await release.wait()
        return "ok"

    backend = MagicMock(id="sandbox-new", aexecute=AsyncMock(side_effect=aexecute))

    with (
        patch(
            "agent.sandboxes.lifecycle.create_sandbox", new_callable=AsyncMock, return_value=backend
        ),
        patch(
            "agent.sandboxes.lifecycle._resolve_sandbox_create_config",
            new_callable=AsyncMock,
            return_value=("snap", {}, {}),
        ),
        patch(
            "agent.sandboxes.lifecycle._resolve_proxy_token",
            new_callable=AsyncMock,
            return_value=("token", None, None),
        ),
        patch(
            "agent.sandboxes.lifecycle._configure_github_proxy",
            new_callable=AsyncMock,
            side_effect=RuntimeError("proxy failed"),
        ),
        patch("agent.sandboxes.lifecycle.record_proxy_token_expiry"),
        pytest.raises(RuntimeError, match="proxy failed"),
    ):
        await _create_sandbox_with_proxy(thread_id="thread-proxy-fails")

    # Cancelled with the sandbox, rather than left behind to report into a run
    # that has already given up on the box.
    assert not release.is_set()
