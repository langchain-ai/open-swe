import logging
from unittest.mock import MagicMock

import pytest
from langchain_core.messages import ToolMessage
from langgraph.prebuilt.tool_node import ToolCallRequest

from agent.middleware.sandbox_reset_detector import (
    _NOTICES_PENDING,
    _TRACKED_PATHS,
    SANDBOX_RESET_DETECTED_MARKER,
    SANDBOX_RESET_USER_NOTICE,
    SandboxResetDetectorMiddleware,
)


@pytest.fixture(autouse=True)
def _reset_state():
    _NOTICES_PENDING.clear()
    _TRACKED_PATHS.clear()
    yield
    _NOTICES_PENDING.clear()
    _TRACKED_PATHS.clear()


def _request(
    name: str, args: dict, thread_id: str = "thread-1", tool_call_id: str = "tc"
) -> ToolCallRequest:
    runtime = MagicMock(config={"configurable": {"thread_id": thread_id}})
    return ToolCallRequest(
        tool_call={"name": name, "args": args, "id": tool_call_id},
        tool=MagicMock(),
        state={},
        runtime=runtime,
    )


@pytest.mark.asyncio
async def test_sandbox_reset_detected_on_missing_tracked_path(caplog) -> None:
    middleware = SandboxResetDetectorMiddleware()

    clone_request = _request(
        "execute", {"command": "git clone https://x/y /workspace/repo"}, tool_call_id="tc1"
    )

    async def clone_handler(_req: ToolCallRequest) -> ToolMessage:
        return ToolMessage(content="Cloned into /workspace/repo", tool_call_id="tc1")

    write_request = _request(
        "write_file", {"file_path": "/workspace/repo/x.py", "content": "x"}, tool_call_id="tc2"
    )

    async def write_handler(_req: ToolCallRequest) -> ToolMessage:
        return ToolMessage(content="wrote 1 line", tool_call_id="tc2")

    cat_request = _request("execute", {"command": "cat /workspace/repo/x.py"}, tool_call_id="tc3")

    async def cat_handler(_req: ToolCallRequest) -> ToolMessage:
        return ToolMessage(
            content="cat: /workspace/repo/x.py: No such file or directory",
            tool_call_id="tc3",
            status="error",
        )

    await middleware.awrap_tool_call(clone_request, clone_handler)
    await middleware.awrap_tool_call(write_request, write_handler)

    assert "/workspace/repo" in _TRACKED_PATHS["thread-1"]
    assert "/workspace/repo/x.py" in _TRACKED_PATHS["thread-1"]

    with caplog.at_level(logging.WARNING, logger="agent.middleware.sandbox_reset_detector"):
        result = await middleware.awrap_tool_call(cat_request, cat_handler)

    assert isinstance(result, ToolMessage)
    assert SANDBOX_RESET_DETECTED_MARKER in result.content
    assert _NOTICES_PENDING.get("thread-1") is True

    matching = [
        r for r in caplog.records if getattr(r, "event", None) == SANDBOX_RESET_DETECTED_MARKER
    ]
    assert matching, "expected a sandbox_reset_detected log record"
    record = matching[0]
    assert record.path == "/workspace/repo/x.py"
    assert record.thread_id == "thread-1"


@pytest.mark.asyncio
async def test_no_marker_when_path_not_tracked() -> None:
    middleware = SandboxResetDetectorMiddleware()
    request = _request("execute", {"command": "cat /tmp/other"}, tool_call_id="tc1")

    async def handler(_req: ToolCallRequest) -> ToolMessage:
        return ToolMessage(
            content="cat: /tmp/other: No such file or directory",
            tool_call_id="tc1",
            status="error",
        )

    result = await middleware.awrap_tool_call(request, handler)
    assert SANDBOX_RESET_DETECTED_MARKER not in (
        result.content if isinstance(result, ToolMessage) else ""
    )
    assert "thread-1" not in _NOTICES_PENDING


@pytest.mark.asyncio
async def test_pending_notice_prepended_to_next_slack_reply() -> None:
    middleware = SandboxResetDetectorMiddleware()
    _NOTICES_PENDING["thread-1"] = True

    captured: dict = {}
    request = _request("slack_thread_reply", {"text": "Done with the task."}, tool_call_id="tc1")

    async def handler(req: ToolCallRequest) -> ToolMessage:
        captured["args"] = dict(req.tool_call["args"])
        return ToolMessage(content="ok", tool_call_id="tc1")

    await middleware.awrap_tool_call(request, handler)

    assert SANDBOX_RESET_USER_NOTICE in captured["args"]["text"]
    assert "Done with the task." in captured["args"]["text"]
    assert "thread-1" not in _NOTICES_PENDING
