from collections.abc import Sequence
from typing import Self
from unittest.mock import AsyncMock, MagicMock

import pytest
from langchain.agents import create_agent
from langchain.agents.middleware.types import ToolCallRequest
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.tools import tool

from agent.middleware.no_progress_guard import NoProgressGuardMiddleware
from agent.middleware.timeout_wrapup import TimeoutWrapupMiddleware


class _ToolCallingFakeModel(FakeMessagesListChatModel):
    def bind_tools(self, tools: Sequence[object], **kwargs: object) -> Self:
        return self


def _request(name: str, args: dict[str, object]) -> ToolCallRequest:
    return ToolCallRequest(
        tool_call={"name": name, "args": args, "id": "call-1", "type": "tool_call"},
        tool=None,
        state={},
        runtime=MagicMock(),
    )


async def _run_tool(
    middleware: NoProgressGuardMiddleware, name: str, args: dict[str, object]
) -> None:
    request = _request(name, args)
    handler = AsyncMock(return_value=ToolMessage(content="same", tool_call_id="call-1"))
    await middleware.awrap_tool_call(request, handler)


@pytest.mark.asyncio
async def test_repeated_read_only_tool_calls_end_before_model_call_limit() -> None:
    @tool
    async def execute(command: str) -> str:
        """Return a stable read-only result."""
        return "same"

    messages = iter(
        AIMessage(
            content="",
            tool_calls=[
                {"name": "execute", "args": {"command": "git status --short"}, "id": f"call-{i}"}
            ],
        )
        for i in range(6)
    )
    model = _ToolCallingFakeModel(responses=list(messages))
    graph = create_agent(
        model=model,
        tools=[execute],
        middleware=[NoProgressGuardMiddleware(), TimeoutWrapupMiddleware(timeout_seconds=3600)],
    )

    result = await graph.ainvoke({"messages": [HumanMessage(content="check the repo")]})

    assert "Model call limits exceeded" in result["messages"][-1].content


@pytest.mark.asyncio
async def test_state_changing_edit_resets_repeated_read_only_calls() -> None:
    middleware = NoProgressGuardMiddleware()
    test_args = {"command": "pytest tests/example.py"}

    await _run_tool(middleware, "execute", test_args)
    await _run_tool(middleware, "execute", test_args)
    await _run_tool(middleware, "edit_file", {"path": "example.py", "edits": []})
    await _run_tool(middleware, "execute", test_args)
    await _run_tool(middleware, "execute", test_args)
    await _run_tool(middleware, "edit_file", {"path": "example.py", "edits": []})
    await _run_tool(middleware, "execute", test_args)

    assert middleware.before_model({}, MagicMock()) is None


@pytest.mark.asyncio
async def test_third_repeat_injects_system_instruction() -> None:
    middleware = NoProgressGuardMiddleware()
    args = {"command": "git status --short"}

    await _run_tool(middleware, "execute", args)
    await _run_tool(middleware, "execute", args)
    await _run_tool(middleware, "execute", args)

    update = middleware.before_model({}, MagicMock())

    assert update is not None
    assert isinstance(update["messages"][0], SystemMessage)
