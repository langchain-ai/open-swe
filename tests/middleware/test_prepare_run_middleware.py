from __future__ import annotations

import asyncio
from typing import Any, cast
from unittest.mock import MagicMock

import pytest
from langchain.agents.middleware import AgentState
from langchain.agents.middleware.types import ModelRequest, ModelResponse
from langchain_core.messages import HumanMessage
from langgraph.runtime import Runtime

from agent.middleware.prepare_run import BasePrepareRunMiddleware
from agent.utils import ttl_cache


class DummyPrepareMiddleware(BasePrepareRunMiddleware):
    def __init__(self) -> None:
        self.calls = 0

    async def _prepare(self, state, runtime):
        self.calls += 1
        return {"work_dir": "/tmp/work", "rendered_system_prompt": "prepared prompt"}


@pytest.mark.asyncio
async def test_prepare_latch_skips_second_call():
    middleware = DummyPrepareMiddleware()

    update = await middleware.abefore_agent(
        cast(AgentState, {"messages": []}), cast(Runtime[None], MagicMock())
    )
    assert update is not None
    fingerprint = update.pop("run_prepared_for")
    assert isinstance(fingerprint, str)
    assert update == {
        "run_prepared": True,
        "work_dir": "/tmp/work",
        "rendered_system_prompt": "prepared prompt",
    }
    assert (
        await middleware.abefore_agent(
            cast(
                AgentState, {"messages": [], "run_prepared": True, "run_prepared_for": fingerprint}
            ),
            cast(Runtime[None], MagicMock()),
        )
        is None
    )
    assert middleware.calls == 1


class FailingPrepareMiddleware(BasePrepareRunMiddleware):
    async def _prepare(self, state, runtime):
        raise RuntimeError("sandbox create 409")


@pytest.mark.asyncio
async def test_prepare_failure_surfaces_message_instead_of_raising():
    middleware = FailingPrepareMiddleware()

    update = await middleware.abefore_agent(
        cast(AgentState, {"messages": []}), cast(Runtime[None], MagicMock())
    )

    assert update is not None
    assert update["run_prepared"] is False
    assert "run_prepared_for" not in update
    messages = update["messages"]
    assert len(messages) == 1
    assert "sandbox create 409" in messages[0].content


@pytest.mark.asyncio
async def test_prepare_latch_reruns_when_fingerprint_changes():
    middleware = DummyPrepareMiddleware()

    assert await middleware.abefore_agent(
        cast(AgentState, {"messages": [], "run_prepared": True, "run_prepared_for": "stale"}),
        cast(Runtime[None], MagicMock()),
    )
    assert middleware.calls == 1


@pytest.mark.asyncio
async def test_prepare_prompt_injection():
    middleware = DummyPrepareMiddleware()
    seen = {}

    async def handler(request: ModelRequest[None]) -> ModelResponse[Any]:
        seen["system_prompt"] = request.system_prompt
        return cast(ModelResponse[Any], MagicMock())

    request = type(
        "Request",
        (),
        {
            "state": {
                "rendered_system_prompt": "prepared prompt",
                "messages": [HumanMessage("hi")],
            },
            "system_message": None,
            "override": lambda self, **kwargs: type(
                "Request",
                (),
                {
                    "state": self.state,
                    "system_prompt": kwargs["system_message"].text,
                    "override": self.override,
                },
            )(),
        },
    )()
    await middleware.awrap_model_call(cast(ModelRequest[None], request), handler)
    assert seen["system_prompt"] == "prepared prompt"


@pytest.mark.asyncio
async def test_ttl_cache_single_flight_and_stale_while_error():
    ttl_cache.clear()
    calls = 0

    async def loader():
        nonlocal calls
        calls += 1
        await asyncio.sleep(0)
        return calls

    results = await asyncio.gather(*(ttl_cache.cached("k", 60, loader) for _ in range(10)))
    assert results == [1] * 10
    assert calls == 1

    ttl_cache.set_cached("k", "stale", -1)

    async def failing_loader():
        raise RuntimeError("boom")

    assert await ttl_cache.cached("k", 60, failing_loader) == "stale"


@pytest.mark.asyncio
async def test_ttl_cache_stale_while_revalidate_refreshes_in_background():
    ttl_cache.clear()
    ttl_cache.set_cached("k", "stale", -1)
    refresh_started = asyncio.Event()
    allow_refresh = asyncio.Event()
    calls = 0

    async def loader():
        nonlocal calls
        calls += 1
        refresh_started.set()
        await allow_refresh.wait()
        return "fresh"

    assert await ttl_cache.cached_stale_while_revalidate("k", 60, loader) == "stale"
    await asyncio.wait_for(refresh_started.wait(), timeout=1)
    assert calls == 1

    allow_refresh.set()
    for _ in range(20):
        if await ttl_cache.cached_stale_while_revalidate("k", 60, loader) == "fresh":
            break
        await asyncio.sleep(0.01)
    else:
        raise AssertionError("stale cache entry was not refreshed")


@pytest.mark.asyncio
async def test_ttl_cache_exception_without_stale_is_not_cached():
    ttl_cache.clear()
    calls = 0

    async def failing_loader():
        nonlocal calls
        calls += 1
        raise RuntimeError("boom")

    with pytest.raises(RuntimeError):
        await ttl_cache.cached("k", 60, failing_loader)
    with pytest.raises(RuntimeError):
        await ttl_cache.cached("k", 60, failing_loader)
    assert calls == 2
