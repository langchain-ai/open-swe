from unittest.mock import AsyncMock, patch

import pytest
from langsmith.sandbox import SandboxClientError, SandboxRetryableConnectionError

from agent.utils.sandbox_retry import (
    MAX_TRANSIENT_ATTEMPTS,
    is_transient_sandbox_error,
    retry_transient_sandbox_errors,
)


def _transient() -> SandboxRetryableConnectionError:
    return SandboxRetryableConnectionError(
        "WebSocket upgrade temporarily rejected by server (HTTP 503)"
    )


def test_only_the_pre_start_failure_counts_as_transient() -> None:
    assert is_transient_sandbox_error(_transient())
    assert not is_transient_sandbox_error(SandboxClientError("Sandbox request timed out: sb-dead"))


@pytest.mark.asyncio
async def test_a_gateway_blip_is_retried_until_it_clears() -> None:
    attempts = 0

    async def operation() -> str:
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            raise _transient()
        return "ok"

    with patch("agent.utils.sandbox_retry.asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
        result = await retry_transient_sandbox_errors(operation, description="test")

    assert result == "ok"
    assert attempts == 3
    assert mock_sleep.await_count == 2
    # Backoff grows so a sustained outage is not hammered.
    delays = [call.args[0] for call in mock_sleep.await_args_list]
    assert delays[1] > delays[0]


@pytest.mark.asyncio
async def test_a_terminal_sandbox_error_is_not_retried() -> None:
    operation = AsyncMock(side_effect=SandboxClientError("Sandbox request timed out: sb-dead"))

    with pytest.raises(SandboxClientError):
        await retry_transient_sandbox_errors(operation, description="test")

    operation.assert_awaited_once()


@pytest.mark.asyncio
async def test_retries_are_bounded() -> None:
    operation = AsyncMock(side_effect=_transient())

    with (
        patch("agent.utils.sandbox_retry.asyncio.sleep", new_callable=AsyncMock),
        pytest.raises(SandboxRetryableConnectionError),
    ):
        await retry_transient_sandbox_errors(operation, description="test")

    assert operation.await_count == MAX_TRANSIENT_ATTEMPTS
