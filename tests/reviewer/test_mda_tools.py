import sys
from pathlib import Path
from unittest.mock import AsyncMock

import httpx2
import pytest
from langchain.tools import ToolRuntime
from langchain_core.messages import ToolMessage, message_to_dict
from langgraph.prebuilt.tool_node import ToolCallRequest
from langgraph.types import Command

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "mda"))

from rt.backend import BackendCallError, BackendTimeoutError, OpenSweBackend, ToolReply, ToolSpec
from rt.middleware import RuntimeToolsMiddleware
from rt.tools import backend_tool


@pytest.mark.parametrize("failures", [2, 3])
async def test_connection_failure_recovers_or_returns_tool_error(
    monkeypatch: pytest.MonkeyPatch, failures: int
) -> None:
    monkeypatch.setenv("OPEN_SWE_BACKEND_URL", "https://backend.invalid")
    monkeypatch.setattr("rt.tools.run_id", lambda: "run-1")
    monkeypatch.setattr("rt.tools.asyncio.sleep", AsyncMock())
    backend = OpenSweBackend("secret-token")
    calls = 0

    async def tool(body: object) -> ToolReply:
        nonlocal calls
        calls += 1
        if calls <= failures:
            try:
                raise httpx2.ConnectError("secret-token")
            except httpx2.ConnectError as exc:
                raise BackendCallError("call failed (ConnectError)") from exc
        return ToolReply(
            message=message_to_dict(ToolMessage(content="done", tool_call_id="call-1"))
        )

    monkeypatch.setattr(backend, "tool", tool)
    served = backend_tool(
        ToolSpec(
            name="execute", description="Execute", parameters={"type": "object", "properties": {}}
        ),
        backend,
    )
    runtime = ToolRuntime(
        state={"messages": []},
        context=None,
        config={},
        stream_writer=lambda event: None,
        tool_call_id="call-1",
        store=None,
    )
    middleware = RuntimeToolsMiddleware(backend, served={"execute"})
    request = ToolCallRequest(
        tool_call={"name": "execute", "args": {}, "id": "call-1", "type": "tool_call"},
        tool=served,
        state=runtime.state,
        runtime=runtime,
    )

    async def handle(request: ToolCallRequest) -> ToolMessage | Command:
        return await served.coroutine(runtime=request.runtime)

    result = await middleware.awrap_tool_call(request, handle)

    assert calls == 3
    assert isinstance(result, Command)
    [message] = result.update["messages"]
    assert message.tool_call_id == "call-1"
    assert message.status == ("error" if failures == 3 else "success")
    assert "secret-token" not in message.content


@pytest.mark.parametrize("failure", ["gateway", "read", "timeout", "auth", "unknown"])
async def test_uncertain_calls_are_not_replayed(
    monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    monkeypatch.setenv("OPEN_SWE_BACKEND_URL", "https://backend.invalid")
    monkeypatch.setattr("rt.tools.run_id", lambda: "run-1")
    backend = OpenSweBackend("secret-token")
    calls = 0

    async def tool(body: object) -> ToolReply:
        nonlocal calls
        calls += 1
        if failure == "timeout":
            raise BackendTimeoutError("call failed (ReadTimeout)")
        if failure == "unknown":
            raise BackendCallError("unknown failure")
        try:
            if failure == "read":
                raise httpx2.ReadError("secret-token")
            request = httpx2.Request("POST", "https://backend.invalid")
            raise httpx2.HTTPStatusError(
                "secret-token",
                request=request,
                response=httpx2.Response(503 if failure == "gateway" else 401, request=request),
            )
        except httpx2.HTTPError as exc:
            raise BackendCallError("call failed") from exc

    monkeypatch.setattr(backend, "tool", tool)
    served = backend_tool(
        ToolSpec(
            name="execute", description="Execute", parameters={"type": "object", "properties": {}}
        ),
        backend,
    )
    runtime = ToolRuntime(
        state={"messages": []},
        context=None,
        config={},
        stream_writer=lambda event: None,
        tool_call_id="call-1",
        store=None,
    )
    if failure in {"auth", "unknown"}:
        with pytest.raises(BackendCallError):
            await served.coroutine(runtime=runtime)
    else:
        result = await served.coroutine(runtime=runtime)
        [message] = result.update["messages"]
        assert message.status == "error"
        assert message.tool_call_id == "call-1"
        assert "secret-token" not in message.content
    assert calls == 1
