import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import httpx2
import pytest
from langchain.tools import ToolRuntime
from langchain_core.messages import ToolMessage, message_to_dict
from langgraph.types import Command

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "mda"))

from rt.backend import BackendUnavailableError, OpenSweBackend, ToolSpec
from rt.tools import backend_tool


@pytest.mark.parametrize("hook", ["call", "before_model"])
@pytest.mark.parametrize(
    ("failure", "failures", "attempts"),
    [("connect", 2, 3), ("connect", 3, 3), ("gateway", 1, 1)],
)
async def test_backend_connection_failures(
    monkeypatch: pytest.MonkeyPatch, hook: str, failure: str, failures: int, attempts: int
) -> None:
    monkeypatch.setenv("OPEN_SWE_BACKEND_URL", "https://backend.invalid")
    monkeypatch.setattr("rt.tools.run_id", lambda: "run-1")
    monkeypatch.setattr("rt.backend.asyncio.sleep", AsyncMock())
    request = httpx2.Request("POST", "https://backend.invalid")
    message = message_to_dict(ToolMessage(content="done", tool_call_id="call-1"))
    response = httpx2.Response(200, request=request, json={"message": message})
    failure_result = (
        httpx2.ConnectError("secret-token")
        if failure == "connect"
        else httpx2.Response(503, request=request)
    )
    client = MagicMock()
    client.__aenter__.return_value.post = AsyncMock(
        side_effect=[failure_result] * failures + [response]
    )
    monkeypatch.setattr("rt.backend.httpx2.AsyncClient", lambda **kwargs: client)
    backend = OpenSweBackend("secret-token")
    if hook == "before_model":
        if failures == 3 or failure == "gateway":
            with pytest.raises(BackendUnavailableError):
                await backend.phase("before_model", {})
        else:
            await backend.phase("before_model", {})
    else:
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
        assert isinstance(result, Command)
        [result_message] = result.update["messages"]
        assert result_message.tool_call_id == "call-1"
        assert result_message.status == ("success" if failures == 2 else "error")
        assert "secret-token" not in result_message.content
    assert client.__aenter__.return_value.post.await_count == attempts
