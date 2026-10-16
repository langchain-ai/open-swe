"""A rollout check is recorded on the thread that was asked for it."""

from unittest.mock import AsyncMock, patch

import pytest

from openswe.run_config import RunConfig
from openswe.tools.request_rollout_check import request_rollout_check


@pytest.mark.asyncio
async def test_request_rollout_check_marks_the_current_thread() -> None:
    client = AsyncMock()
    with (
        patch(
            "openswe.tools.request_rollout_check.RunConfig.from_runtime",
            return_value=RunConfig(thread_id="thread-1"),
        ),
        patch(
            "openswe.tools.request_rollout_check.langgraph_client",
            return_value=client,
        ),
    ):
        result = await request_rollout_check()
    assert result == {"success": True, "rollout_check": True}
    client.threads.update.assert_awaited_once_with(
        thread_id="thread-1",
        metadata={"rollout_check": True},
    )


@pytest.mark.asyncio
async def test_request_rollout_check_without_a_thread_does_not_write() -> None:
    with patch(
        "openswe.tools.request_rollout_check.RunConfig.from_runtime",
        return_value=RunConfig(),
    ):
        result = await request_rollout_check()
    assert result == {"success": False, "error": "No thread_id in current run config"}
