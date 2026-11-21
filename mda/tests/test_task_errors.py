import asyncio
import json
from collections.abc import Iterator
from unittest.mock import AsyncMock, MagicMock

import httpx2
import pytest
from deepagents.middleware.subagents import SubAgentMiddleware
from langchain.agents import create_agent
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage, BaseMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.runnables import RunnableLambda
from langchain_core.tools import tool
from langchain_openai import ChatOpenAI
from langgraph.errors import GraphBubbleUp, GraphInterrupt, NodeCancelledError
from langgraph.prebuilt.tool_node import ToolCallRequest
from managed_deepagents import CredentialAuthorizationRequiredError
from rt.model_calls import ModelCallTimeoutError
from rt.model_retries import ModelRetriesMiddleware
from rt.task_errors import TaskErrorMiddleware
from rt.tools import SandboxGoneError


class _ProviderError(Exception):
    def __init__(self, status: int, code: str | None = None) -> None:
        super().__init__("provider failed")
        self.status_code = status
        self.body = {"error": {"type": "invalid_request_error", "code": code}}


@pytest.mark.parametrize(
    "error",
    [
        _ProviderError(400, "invalid_prompt"),
        _ProviderError(400, "context_length_exceeded"),
        _ProviderError(400),
        _ProviderError(422),
        _ProviderError(408),
        _ProviderError(409),
        _ProviderError(425),
        _ProviderError(429),
        _ProviderError(503),
        _ProviderError(529),
        httpx2.ConnectError("connection failed"),
        ModelCallTimeoutError("timed out"),
        _ProviderError(401),
        RuntimeError("unexpected"),
    ],
)
async def test_failed_task_returns_an_error_the_parent_can_handle(
    monkeypatch: pytest.MonkeyPatch, error: Exception
) -> None:
    monkeypatch.setattr(GenericFakeChatModel, "bind_tools", lambda self, *args, **kwargs: self)
    attempts = 0

    async def fail(state: dict[str, object]) -> dict[str, object]:
        nonlocal attempts
        attempts += 1
        raise error

    parent = create_agent(
        model=GenericFakeChatModel(
            messages=iter(
                [
                    AIMessage(
                        content="",
                        tool_calls=[
                            {
                                "name": "task",
                                "args": {
                                    "description": "investigate",
                                    "subagent_type": "researcher",
                                },
                                "id": "delegated",
                            }
                        ],
                    ),
                    AIMessage(content="handled"),
                ]
            )
        ),
        middleware=[
            TaskErrorMiddleware(),
            SubAgentMiddleware(
                backend=MagicMock(),
                subagents=[
                    {
                        "name": "researcher",
                        "description": "Investigate",
                        "runnable": RunnableLambda(fail),
                    }
                ],
            ),
        ],
    )

    result = await parent.ainvoke({"messages": [("user", "investigate")]})

    failures = [message for message in result["messages"] if isinstance(message, ToolMessage)]
    assert len(failures) == 1
    failure = failures[0]
    assert failure.status == "error"
    assert failure.tool_call_id == "delegated"
    payload = json.loads(failure.text)
    assert payload["status"] == "failed"
    assert payload["source"] == "subagent"
    assert payload["error"]["message"] == str(error)
    assert result["messages"][-1].content == "handled"
    assert attempts == 1


async def test_exhausted_model_retries_do_not_repeat_subagent_side_effects(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(GenericFakeChatModel, "bind_tools", lambda self, *args, **kwargs: self)
    monkeypatch.setattr("rt.model_retries.random.uniform", lambda *args: 0.0)
    effects: list[str] = []
    attempts = 0

    @tool
    async def record() -> str:
        """Record one external effect."""
        effects.append("recorded")
        return "recorded"

    async def generate(
        self: ChatOpenAI, messages: list[BaseMessage], **kwargs: object
    ) -> ChatResult:
        nonlocal attempts
        attempts += 1
        if not any(isinstance(message, ToolMessage) for message in messages):
            reply = AIMessage(
                content="", tool_calls=[{"name": "record", "args": {}, "id": "effect"}]
            )
            return ChatResult(generations=[ChatGeneration(message=reply)])
        raise _ProviderError(503)

    monkeypatch.setattr(ChatOpenAI, "_agenerate", generate)
    child = create_agent(
        model=ChatOpenAI(model="test", api_key="test", max_retries=0),
        tools=[record],
        middleware=[ModelRetriesMiddleware()],
    )
    replies: Iterator[AIMessage] = iter(
        [
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "task",
                        "args": {"description": "record", "subagent_type": "researcher"},
                        "id": "delegated",
                    }
                ],
            ),
            AIMessage(content="handled"),
        ]
    )
    parent = create_agent(
        model=GenericFakeChatModel(messages=replies),
        middleware=[
            TaskErrorMiddleware(),
            SubAgentMiddleware(
                backend=MagicMock(),
                subagents=[{"name": "researcher", "description": "Record", "runnable": child}],
            ),
        ],
    )

    result = await parent.ainvoke({"messages": [("user", "record")]})

    assert effects == ["recorded"]
    assert attempts == 7
    failure = next(message for message in result["messages"] if isinstance(message, ToolMessage))
    assert failure.status == "error"
    assert json.loads(failure.text)["error"]["status_code"] == 503
    assert result["messages"][-1].content == "handled"


@pytest.mark.parametrize(
    "name,error",
    [
        ("task", SandboxGoneError("the thread's sandbox did not answer")),
        ("task", CredentialAuthorizationRequiredError(["notion"])),
        ("task", GraphBubbleUp("pause")),
        ("task", GraphInterrupt()),
        ("task", NodeCancelledError("child")),
        ("task", asyncio.CancelledError()),
        ("other", _ProviderError(503)),
    ],
)
async def test_task_boundary_propagates_run_ending_errors_and_control_flow(
    name: str, error: BaseException
) -> None:
    request = ToolCallRequest(
        tool_call={"name": name, "args": {}, "id": "call", "type": "tool_call"},
        tool=None,
        state={"messages": []},
        runtime=MagicMock(),
    )
    handler = AsyncMock(side_effect=error)

    with pytest.raises(type(error)) as raised:
        await TaskErrorMiddleware().awrap_tool_call(request, handler)

    assert raised.value is error
    assert handler.await_count == 1


@pytest.mark.parametrize("name", ["task", "other"])
async def test_successful_tools_are_unchanged(name: str) -> None:
    request = ToolCallRequest(
        tool_call={"name": name, "args": {}, "id": "call", "type": "tool_call"},
        tool=None,
        state={"messages": []},
        runtime=MagicMock(),
    )
    reply = ToolMessage(content="done", tool_call_id="call", name=name)
    handler = AsyncMock(return_value=reply)

    assert await TaskErrorMiddleware().awrap_tool_call(request, handler) is reply
    assert handler.await_count == 1
