from unittest.mock import AsyncMock

from langchain.agents.middleware.types import ModelRequest, ModelResponse
from langchain_core.messages import AIMessage, HumanMessage

from agent.input_messages import dynamic_context_hash, person_introduction
from agent.middleware.dynamic_context import DynamicContextMiddleware


def _context_message() -> HumanMessage:
    content = person_introduction(
        {"id": "github:octocat", "platform": "github", "github_login": "octocat"}
    )["content"]
    assert isinstance(content, str)
    return HumanMessage(content=content)


async def test_restores_dynamic_context_after_compaction() -> None:
    context = _context_message()
    summary = HumanMessage(content="summary")
    request = ModelRequest(
        model=AsyncMock(),
        messages=[summary],
        state={"messages": [context, HumanMessage(content="old"), summary]},
    )
    handler = AsyncMock(return_value=ModelResponse(result=[AIMessage(content="ok")]))

    await DynamicContextMiddleware().awrap_model_call(request, handler)

    assert handler.await_args is not None
    forwarded = handler.await_args.args[0]
    assert forwarded.messages == [context, summary]


async def test_injects_each_dynamic_context_hash_at_most_once() -> None:
    context = _context_message()
    request = ModelRequest(
        model=AsyncMock(),
        messages=[context, HumanMessage(content="latest")],
        state={"messages": [context, context, HumanMessage(content="latest")]},
    )
    handler = AsyncMock(return_value=ModelResponse(result=[AIMessage(content="ok")]))

    await DynamicContextMiddleware().awrap_model_call(request, handler)

    assert handler.await_args is not None
    forwarded = handler.await_args.args[0]
    hashes = [dynamic_context_hash(message.content) for message in forwarded.messages]
    assert len([value for value in hashes if value is not None]) == 1


async def test_restored_context_lands_in_front_of_the_trailing_human_turn() -> None:
    """The head would shift every cached byte; the tail would displace the task."""
    context = _context_message()
    earlier = AIMessage(content="working on it")
    latest = HumanMessage(content="latest")
    request = ModelRequest(
        model=AsyncMock(),
        messages=[earlier, latest],
        state={"messages": [context, earlier, latest]},
    )
    handler = AsyncMock(return_value=ModelResponse(result=[AIMessage(content="ok")]))

    await DynamicContextMiddleware().awrap_model_call(request, handler)

    assert handler.await_args is not None
    assert handler.await_args.args[0].messages == [earlier, context, latest]


async def test_restored_context_appends_when_the_request_ends_mid_turn() -> None:
    """Nothing may come between a tool result and the call it answers."""
    context = _context_message()
    pending = AIMessage(content="thinking")
    request = ModelRequest(
        model=AsyncMock(),
        messages=[HumanMessage(content="task"), pending],
        state={"messages": [context, HumanMessage(content="task"), pending]},
    )
    handler = AsyncMock(return_value=ModelResponse(result=[AIMessage(content="ok")]))

    await DynamicContextMiddleware().awrap_model_call(request, handler)

    assert handler.await_args is not None
    assert handler.await_args.args[0].messages[-1] is context
