"""Tests for the reviewer cancellation -> check-run settle wrapper."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

import pytest

from agent.reviewer import _CancellationSettleWrapper


class _FakeAgent:
    def __init__(self) -> None:
        self.ainvoke_calls = 0

    async def ainvoke(self, *_args, **_kwargs):  # noqa: ANN002, ANN003
        self.ainvoke_calls += 1
        raise asyncio.CancelledError()


@pytest.mark.asyncio
async def test_ainvoke_cancellation_settles_and_propagates() -> None:
    agent = _FakeAgent()
    wrapper = _CancellationSettleWrapper(agent)
    with patch("agent.reviewer._settle_review_check", new_callable=AsyncMock) as settle:
        with pytest.raises(asyncio.CancelledError):
            await wrapper.ainvoke({})
    settle.assert_awaited_once_with(cancelled=True)


@pytest.mark.asyncio
async def test_ainvoke_normal_path_does_not_settle() -> None:
    class _OkAgent:
        async def ainvoke(self, *_args, **_kwargs):  # noqa: ANN002, ANN003
            return {"ok": True}

    wrapper = _CancellationSettleWrapper(_OkAgent())
    with patch("agent.reviewer._settle_review_check", new_callable=AsyncMock) as settle:
        result = await wrapper.ainvoke({})
    assert result == {"ok": True}
    settle.assert_not_called()


@pytest.mark.asyncio
async def test_settle_failure_does_not_block_propagation() -> None:
    agent = _FakeAgent()
    wrapper = _CancellationSettleWrapper(agent)
    with patch(
        "agent.reviewer._settle_review_check",
        new_callable=AsyncMock,
        side_effect=RuntimeError("boom"),
    ):
        with pytest.raises(asyncio.CancelledError):
            await wrapper.ainvoke({})
