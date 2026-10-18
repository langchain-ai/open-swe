import sys
from collections.abc import Sequence
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import httpx
import httpx2
import openai
import pytest
from deepagents import create_deep_agent
from langchain.tools import ToolRuntime
from langchain_core.callbacks import AsyncCallbackManagerForLLMRun
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage, BaseMessage, ToolMessage, message_to_dict
from langchain_core.outputs import ChatResult
from langgraph.types import Command

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "mda"))

from rt.backend import BackendCallError, BackendUnavailableError, OpenSweBackend, ToolSpec
from rt.middleware import RuntimeToolsMiddleware, SubagentFallbackMiddleware, model_retries
from rt.models import ModelSpec
from rt.tools import backend_tool


class SubagentTestModel(FakeMessagesListChatModel):
    failure: Exception | None = None
    attempts: int = 0

    def bind_tools(self, tools: Sequence[object], **kwargs: object) -> SubagentTestModel:
        return self

    async def _agenerate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: AsyncCallbackManagerForLLMRun | None = None,
        **kwargs: object,
    ) -> ChatResult:
        self.attempts += 1
        if self.failure is not None:
            raise self.failure
        return await super()._agenerate(messages, stop, run_manager, **kwargs)


@pytest.mark.parametrize(
    ("failure_kind", "fallback_configured", "fallback_fails", "attempts", "status"),
    [
        ("overload", True, False, 3, "success"),
        ("overload", False, False, 3, "error"),
        ("overload", True, True, 3, "error"),
        ("invalid", True, False, 1, "error"),
        ("auth", True, False, 1, "error"),
    ],
)
async def test_task_model_failure_does_not_terminate_parent(
    monkeypatch: pytest.MonkeyPatch,
    failure_kind: str,
    fallback_configured: bool,
    fallback_fails: bool,
    attempts: int,
    status: str,
) -> None:
    request = httpx.Request("POST", "https://provider.invalid")
    failure = (
        openai.APIError("The server is overloaded: secret-token", request=request, body=None)
        if failure_kind == "overload"
        else openai.AuthenticationError(
            "secret-token", response=httpx.Response(401, request=request), body=None
        )
        if failure_kind == "auth"
        else ValueError("secret-token")
    )
    primary = SubagentTestModel(responses=[AIMessage(content="unused")], failure=failure)
    fallback = SubagentTestModel(
        responses=[AIMessage(content="Fallback completed the task")],
        failure=failure if fallback_fails else None,
    )
    monkeypatch.setattr("rt.middleware.build_model", lambda *args, **kwargs: fallback)
    monkeypatch.setattr("rt.middleware.run_id", lambda: "run-1")
    monkeypatch.setattr("langchain.agents.middleware.model_retry.asyncio.sleep", AsyncMock())
    backend = MagicMock(spec=OpenSweBackend)
    parent = SubagentTestModel(
        responses=[
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "task",
                        "args": {"description": "Do the work", "subagent_type": "worker"},
                        "id": "task-1",
                    }
                ],
            ),
            AIMessage(content="Parent continued"),
        ]
    )
    graph = create_deep_agent(
        model=parent,
        middleware=[RuntimeToolsMiddleware(backend, [])],
        subagents=[
            {
                "name": "worker",
                "description": "Worker",
                "system_prompt": "Do the work",
                "model": primary,
                "tools": [],
                "middleware": [
                    SubagentFallbackMiddleware(
                        ModelSpec(model_id="test:fallback", kwargs={})
                        if fallback_configured
                        else None,
                        use_gateway=False,
                    ),
                    model_retries(),
                ],
            }
        ],
    )
    result = await graph.ainvoke({"messages": [("user", "Delegate the work")]})
    assert result["messages"][-1].content == "Parent continued"
    [reply] = [
        message
        for message in result["messages"]
        if isinstance(message, ToolMessage) and message.tool_call_id == "task-1"
    ]
    assert reply.status == status
    assert "secret-token" not in reply.content
    if status == "success":
        assert "Fallback completed the task" in reply.content
    assert primary.attempts == attempts
    assert fallback.attempts == (
        (3 if fallback_fails else 1) if fallback_configured and failure_kind == "overload" else 0
    )
    reported = backend.tool_result.call_args.args[1]
    assert reported.status == status


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
    client = MagicMock(is_closed=False)
    client.post = AsyncMock(side_effect=[failure_result] * failures + [response])
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
    assert client.post.await_count == attempts


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
