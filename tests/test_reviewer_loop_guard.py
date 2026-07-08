from __future__ import annotations

import json

import pytest
from langchain_core.messages import AIMessage, SystemMessage, ToolMessage
from langgraph.prebuilt.tool_node import ToolCallRequest

from agent.middleware.reviewer_loop_guard import ReviewerLoopGuardMiddleware
from agent.middleware.timeout_wrapup import TimeoutWrapupMiddleware


def _request(name: str, args: dict, call_id: str) -> ToolCallRequest:
    return ToolCallRequest(
        tool_call={"name": name, "args": args, "id": call_id},
        tool=None,
        state={},
        runtime=None,
    )


@pytest.mark.asyncio
async def test_guard_short_circuits_repeated_empty_read() -> None:
    middleware = ReviewerLoopGuardMiddleware()
    calls: list[str] = []

    async def handler(request: ToolCallRequest) -> ToolMessage:
        calls.append(request.tool_call["id"])
        return ToolMessage(content="null", tool_call_id=request.tool_call["id"])

    for i in range(5):
        result = await middleware.awrap_tool_call(
            _request("read_file", {"file_path": "/app.py"}, f"c{i}"), handler
        )

    # The identical empty call must not be executed more than twice.
    assert len(calls) <= 2
    payload = json.loads(result.content)
    assert payload["reason"] == "repeated_empty_result"


@pytest.mark.asyncio
async def test_guard_allows_nonempty_and_distinct_calls() -> None:
    middleware = ReviewerLoopGuardMiddleware()
    calls: list[str] = []

    async def handler(request: ToolCallRequest) -> ToolMessage:
        calls.append(request.tool_call["args"]["file_path"])
        return ToolMessage(content="def main(): pass", tool_call_id=request.tool_call["id"])

    for i, path in enumerate(["/a.py", "/a.py", "/b.py", "/a.py"]):
        await middleware.awrap_tool_call(
            _request("read_file", {"file_path": path}, f"c{i}"), handler
        )

    assert calls == ["/a.py", "/a.py", "/b.py", "/a.py"]


@pytest.mark.asyncio
async def test_child_run_cap_forces_publish_wrapup() -> None:
    middleware = TimeoutWrapupMiddleware(timeout_seconds=10**9, child_run_cap=10)
    from langchain.agents.middleware.types import ModelRequest, ModelResponse

    messages = []
    for i in range(9):
        messages.append(
            AIMessage(content="", tool_calls=[{"name": "read_file", "args": {}, "id": f"t{i}"}])
        )
        messages.append(ToolMessage(content="null", tool_call_id=f"t{i}"))

    seen: list[ModelRequest] = []

    async def handler(request: ModelRequest) -> ModelResponse:
        seen.append(request)
        return ModelResponse(result=[AIMessage(content="ok")])

    request = ModelRequest(
        model=None, messages=messages, system_message=SystemMessage(content="base")
    )
    await middleware.awrap_model_call(request, handler)

    assert "child_run_limit_warning" in seen[0].system_message.content
    assert "publish_review" in seen[0].system_message.content
