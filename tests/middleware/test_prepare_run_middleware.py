import asyncio
from typing import Any, cast
from unittest.mock import MagicMock
from xml.etree import ElementTree

import pytest
from langchain.agents.middleware import AgentState
from langchain.agents.middleware.types import ModelRequest, ModelResponse
from langchain_core.messages import HumanMessage
from langgraph.runtime import Runtime

from agent.input_messages import human_input
from agent.middleware.prepare_run import BasePrepareRunMiddleware, PrepareRunState
from agent.server import PrepareAgentRunMiddleware
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
    prompt = ElementTree.fromstring(seen["system_prompt"])
    assert prompt.tag == "system-instructions"
    entity = prompt.find("dynamic-context")
    message = prompt.find("input-message")
    assert entity is not None
    assert message is not None
    assert entity.attrib["id"] == "system:open-swe"
    assert message.findtext("content") == "prepared prompt"


def test_sender_context_arrives_as_its_own_message():
    first = HumanMessage("first", id="first")
    latest = HumanMessage(
        content=[{"type": "text", "text": "second"}],
        id="second",
        name="participant",
    )

    messages = PrepareAgentRunMiddleware._sender_context_messages(
        cast(PrepareRunState, {"messages": [first, latest]}),
        "sender",
    )

    assert first.content == "first"
    assert latest.content == [{"type": "text", "text": "second"}]
    assert messages
    envelope = ElementTree.fromstring(cast(str, messages[-1]["content"]))
    assert envelope.attrib["sender"] == "system:sender-context"
    assert envelope.attrib["kind"] == "system"
    assert envelope.findtext("content") == "sender"


def test_sender_context_escapes_untrusted_identity_text():
    message = HumanMessage(
        content=cast(
            str,
            human_input(
                "ship it <now> & fast",
                {"sender_id": "slack:U1", "surface": "slack", "kind": "human"},
            )["content"],
        ),
        id="turn",
    )
    original = message.content

    messages = PrepareAgentRunMiddleware._sender_context_messages(
        cast(PrepareRunState, {"messages": [message]}),
        "identity: 'ramon' & <team>",
    )

    assert message.content == original
    envelope = ElementTree.fromstring(cast(str, messages[-1]["content"]))
    assert envelope.findtext("content") == "identity: 'ramon' & <team>"


def test_sender_context_skipped_without_a_human_message():
    assert (
        PrepareAgentRunMiddleware._sender_context_messages(
            cast(PrepareRunState, {"messages": []}), "sender"
        )
        == []
    )


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
