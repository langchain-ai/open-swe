import sys
from pathlib import Path
from typing import Literal
from unittest.mock import AsyncMock, MagicMock

import httpx2
import pytest
from langchain.agents import create_agent
from langchain.tools import ToolRuntime
from langchain_core.language_models.fake_chat_models import FakeListChatModel
from langchain_core.messages import ToolMessage, message_to_dict
from langgraph.prebuilt.tool_node import ToolCallRequest
from langgraph.types import Command

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "mda"))

from rt.backend import (
    BackendCallError,
    BackendUnavailableError,
    OpenSweBackend,
    PhaseReply,
    ToolReply,
    ToolSpec,
)
from rt.middleware import BackendRunMiddleware
from rt.sandbox import SandboxCreate, SandboxSpec
from rt.sandbox_tools import SandboxToolsMiddleware
from rt.tools import backend_tool


@pytest.mark.parametrize(
    ("failure", "failures", "attempts"),
    [("connect", 10, 11), ("connect", 11, 11), (503, 1, 1), (504, 1, 1)],
)
async def test_a_tool_call_the_backend_never_answered(
    monkeypatch: pytest.MonkeyPatch, failure: str | int, failures: int, attempts: int
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
        else httpx2.Response(failure, request=request)
    )
    client = MagicMock(is_closed=False)
    client.post = AsyncMock(side_effect=[failure_result] * failures + [response])
    monkeypatch.setattr("rt.backend.httpx2.AsyncClient", lambda **kwargs: client)
    served = backend_tool(
        ToolSpec(name="execute", description="Execute", parameters={"type": "object"}),
        OpenSweBackend("secret-token"),
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
    assert result_message.status == ("success" if failures == 10 else "error")
    assert "secret-token" not in result_message.content
    assert client.post.await_count == attempts


@pytest.mark.parametrize(
    ("hook", "status"),
    [
        ("prepare", 504),
        ("before_model", 502),
        ("before_model", 504),
        ("after_model", 503),
        ("after_agent", 504),
    ],
)
async def test_phase_gateway_failure_is_not_replayed(
    monkeypatch: pytest.MonkeyPatch,
    hook: Literal["prepare", "before_model", "after_model", "after_agent"],
    status: int,
) -> None:
    monkeypatch.setenv("OPEN_SWE_BACKEND_URL", "https://backend.invalid")
    sleep = AsyncMock()
    monkeypatch.setattr("rt.backend.asyncio.sleep", sleep)
    request = httpx2.Request("POST", "https://backend.invalid")
    client = MagicMock(is_closed=False)
    client.post = AsyncMock(
        side_effect=[httpx2.Response(status, request=request)]
        + [httpx2.Response(200, request=request, json={"update": {"recovered": True}})]
    )
    monkeypatch.setattr("rt.backend.httpx2.AsyncClient", lambda **kwargs: client)

    with pytest.raises(BackendUnavailableError):
        await OpenSweBackend("secret-token").phase(hook, {})

    assert client.post.await_count == 1
    sleep.assert_not_awaited()


@pytest.mark.parametrize(("failures", "recovers"), [(10, True), (11, False)])
@pytest.mark.parametrize("hook", ["prepare", "before_model"])
async def test_a_run_waits_out_a_backend_rollout(
    monkeypatch: pytest.MonkeyPatch,
    hook: Literal["prepare", "before_model"],
    failures: int,
    recovers: bool,
) -> None:
    monkeypatch.setenv("OPEN_SWE_BACKEND_URL", "https://backend.invalid")
    sleep = AsyncMock()
    monkeypatch.setattr("rt.backend.asyncio.sleep", sleep)
    request = httpx2.Request("POST", "https://backend.invalid")
    # Only a call the backend could not have run is resent; prepare is also resent after a 502.
    unavailable = [httpx2.Response(503, request=request), httpx2.ConnectError("secret-token")]
    if hook == "prepare":
        unavailable.append(httpx2.Response(502, request=request))
    client = MagicMock(is_closed=False)
    client.post = AsyncMock(
        side_effect=[unavailable[i % len(unavailable)] for i in range(failures)]
        + [httpx2.Response(200, request=request, json={"update": {"prepared": True}})]
    )
    monkeypatch.setattr("rt.backend.httpx2.AsyncClient", lambda **kwargs: client)
    backend = OpenSweBackend("secret-token")

    if recovers:
        reply = await backend.phase(hook, {})
        assert reply.update == {"prepared": True}
        assert sum(call.args[0] for call in sleep.await_args_list) >= 120
    else:
        with pytest.raises(BackendUnavailableError):
            await backend.phase(hook, {})
    assert client.post.await_count == min(failures + 1, 11)


@pytest.mark.parametrize("hook", ["prepare", "before_model", "after_model"])
async def test_an_unreachable_backend_fails_the_run(
    monkeypatch: pytest.MonkeyPatch, hook: str
) -> None:
    monkeypatch.setenv("OPEN_SWE_BACKEND_URL", "https://backend.invalid")
    monkeypatch.setattr("rt.middleware.run_id", lambda: "run-1")
    backend = OpenSweBackend("secret-token")

    async def phase(name: str, body: object) -> PhaseReply:
        if name == hook:
            raise BackendUnavailableError(f"{hook} failed")
        assert name != "after_agent"
        return PhaseReply()

    monkeypatch.setattr(backend, "phase", phase)
    graph = create_agent(
        model=FakeListChatModel(responses=["done"]),
        middleware=[BackendRunMiddleware(backend, "invocation-1")],
    )

    with pytest.raises(BackendUnavailableError):
        await graph.ainvoke({"messages": [("user", "hello")]})


async def test_a_failing_backend_hook_fails_the_run_with_what_it_raised(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPEN_SWE_BACKEND_URL", "https://backend.invalid")
    request = httpx2.Request("POST", "https://backend.invalid")
    failure = {"error": "RuntimeError", "message": "no sandbox provider"}
    client = MagicMock(is_closed=False)
    client.post = AsyncMock(return_value=httpx2.Response(500, request=request, json=failure))
    monkeypatch.setattr("rt.backend.httpx2.AsyncClient", lambda **kwargs: client)

    with pytest.raises(BackendCallError, match="prepare failed: RuntimeError: no sandbox provider"):
        await OpenSweBackend("secret-token").phase("prepare", {})


@pytest.mark.parametrize(
    ("name", "arguments", "on_backend"),
    [
        ("execute", {"command": "cd /workspace/repo && git push origin HEAD"}, True),
        ("execute", {"command": "bash -c 'gh pr create --fill'"}, True),
        ("execute", {"command": "curl -X POST https://api.github.com/repos/o/r/pulls"}, True),
        ("execute", {"command": 'g"h" pr create --fill'}, True),
        ("execute", {"command": "git pu''sh origin HEAD"}, True),
        ("execute", {"command": "git $'pu\\x73h' origin HEAD"}, True),
        ("execute", {"command": 'eval "$(echo Z2ggcHIgY3JlYXRl | base64 -d)"'}, True),
        ("execute", {"command": "git pu\\\nsh origin HEAD"}, True),
        ("read_file", {"file_path": "/skills/deploy/SKILL.md"}, True),
        ("execute", {"command": "uv run pytest -q tests/unit"}, False),
        ("read_file", {"file_path": "/workspace/repo/README.md"}, False),
    ],
)
async def test_box_tools_leave_to_the_backend_what_its_guards_and_paths_settle(
    monkeypatch: pytest.MonkeyPatch, name: str, arguments: dict[str, str], on_backend: bool
) -> None:
    """A command the push guards may stop never runs in the box, where they cannot see it."""
    monkeypatch.setenv("OPEN_SWE_BACKEND_URL", "https://backend.invalid")
    for module in ("rt.middleware", "rt.tools"):
        monkeypatch.setattr(f"{module}.run_id", lambda: "run-1")
    backend = OpenSweBackend("secret-token")
    reply = ToolReply(
        message=message_to_dict(ToolMessage(content="guarded", tool_call_id="call-1"))
    )
    monkeypatch.setattr(backend, "tool", AsyncMock(return_value=reply))
    monkeypatch.setattr(backend, "tool_started", AsyncMock())
    monkeypatch.setattr(backend, "tool_result", AsyncMock())
    spec = SandboxSpec(
        create=SandboxCreate(fs_capacity_bytes=1, idle_ttl_seconds=1, delete_after_stop_seconds=1),
        tools=["execute", "read_file"],
        checks=["push_guards"],
        backend_paths=["/skills/"],
    )
    runtime = ToolRuntime(
        state={"messages": []},
        context=None,
        config={},
        stream_writer=lambda event: None,
        tool_call_id="call-1",
        store=None,
    )
    request = ToolCallRequest(
        tool_call={"name": name, "args": arguments, "id": "call-1", "type": "tool_call"},
        tool=None,
        state={"messages": []},
        runtime=runtime,
    )
    in_box = AsyncMock(return_value=ToolMessage(content="ran", tool_call_id="call-1"))

    await SandboxToolsMiddleware(backend, spec, 300).awrap_tool_call(request, in_box)
    await backend.reports.drain()

    assert backend.tool.await_count == int(on_backend)
    assert in_box.await_count == int(not on_backend)
    # The backend records the calls it runs; the ones run in the box are reported to it.
    assert backend.tool_started.await_count == int(not on_backend)
    assert backend.tool_result.await_count == int(not on_backend)
