import json
from unittest.mock import AsyncMock, MagicMock

import pytest
from deepagents.backends.protocol import LsResult
from langchain_core.messages import ToolMessage
from langgraph.prebuilt.tool_node import ToolCallRequest

from agent.middleware.tool_error_handler import ToolErrorMiddleware


def _request(path: str) -> ToolCallRequest:
    runtime = MagicMock(config={"configurable": {"thread_id": "thread-1"}})
    return ToolCallRequest(
        tool_call={"name": "read_file", "args": {"file_path": path}, "id": "call-1"},
        tool=MagicMock(),
        state={},
        runtime=runtime,
    )


def _backend() -> MagicMock:
    backend = MagicMock()
    backend.als = AsyncMock(
        return_value=LsResult(entries=[{"path": "/large_tool_results/call-current"}])
    )
    return backend


@pytest.mark.asyncio
async def test_returned_offload_not_found_includes_current_files() -> None:
    middleware = ToolErrorMiddleware(backend=_backend())
    request = _request("/large_tool_results/call-old")

    async def handler(_request: ToolCallRequest) -> ToolMessage:
        return ToolMessage(
            content=json.dumps(
                {
                    "status": "error",
                    "error": "file_not_found",
                    "error_type": "FileNotFoundError",
                    "name": "read_file",
                }
            ),
            tool_call_id="call-1",
            status="error",
        )

    result = await middleware.awrap_tool_call(request, handler)

    assert isinstance(result, ToolMessage)
    payload = json.loads(result.content)
    assert payload["status"] == "error"
    assert payload["error_type"] == "FileNotFoundError"
    assert payload["name"] == "read_file"
    assert "current run's sandbox" in payload["guidance"]
    assert payload["available_offload_files"] == ["/large_tool_results/call-current"]


@pytest.mark.asyncio
async def test_thrown_offload_not_found_includes_current_files() -> None:
    middleware = ToolErrorMiddleware(backend=_backend())
    request = _request("/large_tool_results/call-old")

    async def handler(_request: ToolCallRequest) -> ToolMessage:
        raise FileNotFoundError("file_not_found: /large_tool_results/call-old")

    result = await middleware.awrap_tool_call(request, handler)

    assert isinstance(result, ToolMessage)
    payload = json.loads(result.content)
    assert payload["status"] == "error"
    assert payload["error_type"] == "FileNotFoundError"
    assert "current run's sandbox" in payload["guidance"]
    assert payload["available_offload_files"] == ["/large_tool_results/call-current"]


@pytest.mark.asyncio
async def test_workspace_not_found_is_unchanged() -> None:
    middleware = ToolErrorMiddleware(backend=_backend())
    request = _request("/workspace/missing.txt")
    message = ToolMessage(
        content=json.dumps(
            {
                "status": "error",
                "error": "file_not_found",
                "error_type": "FileNotFoundError",
                "name": "read_file",
            }
        ),
        tool_call_id="call-1",
        status="error",
    )

    async def handler(_request: ToolCallRequest) -> ToolMessage:
        return message

    result = await middleware.awrap_tool_call(request, handler)

    assert result is message
    assert result.content == message.content
