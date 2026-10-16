import sys
from pathlib import Path
from unittest.mock import AsyncMock

import httpx2
import pytest
from langchain.tools import ToolRuntime
from langchain_core.messages import ToolMessage, message_to_dict

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "mda"))

from rt.backend import BackendCallError, OpenSweBackend, ToolReply, ToolSpec
from rt.tools import backend_tool


@pytest.mark.parametrize(
    ("failure", "failures", "attempts", "status"),
    [("connect", 2, 3, "success"), ("connect", 3, 3, "error"), ("gateway", 1, 1, "error")],
)
async def test_backend_tool_connection_failures(
    monkeypatch: pytest.MonkeyPatch, failure: str, failures: int, attempts: int, status: str
) -> None:
    monkeypatch.setenv("OPEN_SWE_BACKEND_URL", "https://backend.invalid")
    monkeypatch.setattr("rt.tools.run_id", lambda: "run-1")
    monkeypatch.setattr("rt.tools.asyncio.sleep", AsyncMock())
    request = httpx2.Request("POST", "https://backend.invalid")
    cause = (
        httpx2.ConnectError("secret-token")
        if failure == "connect"
        else httpx2.HTTPStatusError(
            "secret-token", request=request, response=httpx2.Response(503, request=request)
        )
    )
    error = BackendCallError("call failed")
    error.__cause__ = cause
    backend = OpenSweBackend("secret-token")
    call = AsyncMock(
        side_effect=[error] * failures
        + [ToolReply(message=message_to_dict(ToolMessage(content="done", tool_call_id="call-1")))]
    )
    monkeypatch.setattr(backend, "tool", call)
    served = backend_tool(
        ToolSpec(name="execute", description="Execute", parameters={"type": "object"}), backend
    )
    runtime = ToolRuntime(
        state={"messages": []},
        context=None,
        config={},
        stream_writer=lambda event: None,
        tool_call_id="call-1",
        store=None,
    )
    result = await served.coroutine(runtime=runtime)

    assert call.await_count == attempts
    [message] = result.update["messages"]
    assert message.tool_call_id == "call-1"
    assert message.status == status
    assert "secret-token" not in message.content
