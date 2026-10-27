import asyncio
import logging
import random
import string
from collections.abc import Awaitable, Callable
from copy import deepcopy
from typing import Final, Protocol, cast
from unittest.mock import AsyncMock, MagicMock

import pytest
from langchain.agents import create_agent
from langchain.agents.middleware.types import (
    AgentState,
    ExtendedModelResponse,
    ModelRequest,
    ModelResponse,
)
from langchain_anthropic import ChatAnthropic
from langchain_core.language_models import BaseChatModel
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import (
    AIMessage,
    AnyMessage,
    BaseMessage,
    HumanMessage,
    ToolCall,
    ToolMessage,
)
from langchain_core.tools import tool
from langchain_fireworks import ChatFireworks
from langchain_openai import ChatOpenAI
from pydantic import SecretStr
from rt.backend import Run, RunContext
from rt.middleware import BackendRunMiddleware, ModelCall
from rt.model_calls import (
    ModelCallTimeoutError,
    ModelCallTimeoutMiddleware,
    ReplayableHistoryMiddleware,
    model_call_guards,
)
from rt.models import ModelSpec
from runtime_api.catalog import Catalog

_ANTHROPIC = ChatAnthropic(model_name="claude-opus-5-5", api_key=SecretStr("test"))
_OPENAI = ChatOpenAI(
    model="gpt-6.1-sol",
    api_key=SecretStr("test"),
    use_responses_api=True,
    store=False,
    include=["reasoning.encrypted_content"],
    output_version="responses/v1",
)
_FIREWORKS = ChatFireworks(model="accounts/fireworks/models/kimi-k3", api_key=SecretStr("test"))


class _WrapsModelCalls(Protocol):
    async def awrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], Awaitable[ModelResponse]],
    ) -> ModelResponse | ExtendedModelResponse: ...


async def _forwarded(
    middleware: _WrapsModelCalls,
    messages: list[AnyMessage],
    model: BaseChatModel,
    state: dict[str, object] | None = None,
) -> ModelRequest:
    request = ModelRequest(
        model=model,
        messages=messages,
        state=cast(AgentState, state or {"messages": messages}),
    )
    handler = AsyncMock(return_value=ModelResponse(result=[AIMessage(content="ok")]))
    await middleware.awrap_model_call(request, handler)
    assert handler.await_args is not None
    return handler.await_args.args[0]


def _calls(*call_ids: str) -> AIMessage:
    return AIMessage(
        content="",
        tool_calls=[
            {"name": "execute", "args": {"command": "ls"}, "id": call_id, "type": "tool_call"}
            for call_id in call_ids
        ],
    )


def _result(call_id: str) -> ToolMessage:
    return ToolMessage(content=call_id, tool_call_id=call_id, name="execute")


async def test_parallel_results_are_replayed_in_tool_call_order() -> None:
    messages: list[AnyMessage] = [
        HumanMessage(content="go"),
        _calls("a", "b", "c"),
        _result("c"),
        _result("a"),
        _result("b"),
        HumanMessage(content="next"),
    ]
    original = list(messages)

    forwarded = await _forwarded(ReplayableHistoryMiddleware(), messages, _ANTHROPIC)

    assert [getattr(m, "tool_call_id", None) for m in forwarded.messages[2:5]] == ["a", "b", "c"]
    assert forwarded.messages[5] is messages[5]
    assert messages == original


async def test_empty_thinking_blocks_are_dropped_only_for_anthropic() -> None:
    reply = AIMessage(
        content=[
            {"type": "thinking", "signature": "s", "thinking": ""},
            {"type": "thinking", "signature": "t", "thinking": "kept"},
            {"type": "text", "text": "ok"},
        ]
    )
    original = deepcopy(reply)

    anthropic = await _forwarded(ReplayableHistoryMiddleware(), [reply], _ANTHROPIC)
    openai = await _forwarded(ReplayableHistoryMiddleware(), [reply], _OPENAI)

    assert anthropic.messages[0].content == [
        {"type": "thinking", "signature": "t", "thinking": "kept"},
        {"type": "text", "text": "ok"},
    ]
    assert openai.messages[0] is reply
    assert reply == original


async def test_stateless_openai_replay_drops_unresolvable_reasoning() -> None:
    messages: list[AnyMessage] = [
        HumanMessage("run pwd"),
        AIMessage(
            content=[
                {
                    "type": "reasoning",
                    "id": "rs_unstored",
                    "summary": [],
                    "encrypted_content": None,
                },
                {"type": "reasoning", "id": "rs_kept", "summary": [], "encrypted_content": "enc"},
                {
                    "type": "function_call",
                    "id": "fc_execute",
                    "call_id": "call_execute",
                    "name": "execute",
                    "arguments": '{"command":"pwd"}',
                },
            ],
            tool_calls=[
                {"name": "execute", "args": {"command": "pwd"}, "id": "call_execute"},
            ],
        ),
        ToolMessage(content="/workspace", tool_call_id="call_execute", name="execute"),
    ]

    forwarded = await _forwarded(ReplayableHistoryMiddleware(), messages, _OPENAI)
    payload = _OPENAI._get_request_payload(forwarded.messages)

    ids = [item.get("id") for item in payload["input"]]
    assert "rs_unstored" not in ids
    assert "rs_kept" in ids
    assert "fc_execute" in ids


async def test_fireworks_replay_drops_the_legacy_function_call() -> None:
    # The gateway rejects it: "Extra inputs are not permitted, field: 'messages[N].function_call'".
    reply = AIMessage(
        content="",
        tool_calls=[{"name": "read_file", "args": {"file_path": "/x"}, "id": "tc1"}],
        additional_kwargs={"function_call": {"name": "read_file", "arguments": "{}"}},
    )

    forwarded = await _forwarded(
        ReplayableHistoryMiddleware(), [HumanMessage("hi"), reply], _FIREWORKS
    )
    replayed: list[BaseMessage] = list(forwarded.messages)
    sent, _ = _FIREWORKS._create_message_dicts(replayed, None)

    assert "function_call" not in sent[1]
    assert sent[1]["tool_calls"]
    assert "function_call" in reply.additional_kwargs


async def test_a_hanging_model_call_fails_as_a_timeout() -> None:
    async def hang(_request: ModelRequest) -> ModelResponse:
        await asyncio.sleep(60)
        raise AssertionError("cancelled by the deadline")

    with pytest.raises(ModelCallTimeoutError) as raised:
        await ModelCallTimeoutMiddleware(timeout_seconds=0.01).awrap_model_call(
            cast(ModelRequest, MagicMock()), hang
        )
    # The retry and the fallback both act on TimeoutError.
    assert isinstance(raised.value, TimeoutError)


@pytest.mark.parametrize("with_image", [True, False])
async def test_a_text_only_model_hands_images_to_the_vision_model(
    monkeypatch: pytest.MonkeyPatch, with_image: bool
) -> None:
    built: dict[str, BaseChatModel] = {"fireworks:kimi": _FIREWORKS, "openai:gpt": _OPENAI}
    monkeypatch.setattr(
        "rt.middleware.build_model", lambda spec, *, use_gateway: built[spec.model_id]
    )
    call = ModelCall(
        system_prompt="prompt",
        model=ModelSpec(model_id="fireworks:kimi", kwargs={}),
        vision=ModelSpec(model_id="openai:gpt", kwargs={}),
        fallback=None,
        use_gateway=False,
    )
    image = [{"type": "image", "url": "https://example.com/screenshot.png"}]
    messages: list[AnyMessage] = [
        HumanMessage(content=image if with_image else "no image"),
        HumanMessage(content="explain it"),
    ]

    monkeypatch.setenv("OPEN_SWE_BACKEND_URL", "https://backend.invalid")
    # A run whose catalog names no models of its own, so the call's own decide.
    run = Run(RunContext.model_validate({"__catalog": Catalog().model_dump(mode="json")}))
    forwarded = await _forwarded(
        BackendRunMiddleware(run.backend),
        messages,
        _ANTHROPIC,
        state={"messages": messages, "open_swe_call": call.model_dump()},
    )

    assert forwarded.model is (_OPENAI if with_image else _FIREWORKS)


# As `create_sandbox_file_download_url` issues them: a signed token of about 800 characters.
_ISSUED_URL: Final = "https://bx-7f3a9c.smithbox.dev/" + "".join(
    random.Random(445).choices(string.ascii_letters + string.digits + "-_", k=794)
)


async def _answered(reply: AIMessage, monkeypatch: pytest.MonkeyPatch) -> AIMessage:
    """The reply the run keeps after the model shares a file with ``reply``."""

    @tool
    async def create_sandbox_file_download_url(file_path: str) -> dict[str, str]:
        """Share a sandbox file."""
        return {"url": _ISSUED_URL, "file_path": file_path}

    monkeypatch.setattr(GenericFakeChatModel, "bind_tools", lambda self, *args, **kwargs: self)
    shared = AIMessage(
        content="",
        tool_calls=[
            ToolCall(
                name="create_sandbox_file_download_url",
                args={"file_path": "/work/demo.mp4"},
                id="call-1",
            )
        ],
    )
    graph = create_agent(
        model=GenericFakeChatModel(messages=iter([shared, reply])),
        tools=[create_sandbox_file_download_url],
        middleware=model_call_guards(),
    )
    result = await graph.ainvoke({"messages": [HumanMessage("record a demo")]})
    answered = result["messages"][-1]
    assert isinstance(answered, AIMessage)
    return answered


@pytest.mark.parametrize(
    ("written", "kept"),
    [
        (
            f"[Watch the 8-second recording]({_ISSUED_URL[:445]} {_ISSUED_URL[445:]})",
            f"[Watch the 8-second recording]({_ISSUED_URL})",
        ),
        (
            f"Recording:\n{_ISSUED_URL[:300]}\n{_ISSUED_URL[300:]}\nDone.",
            f"Recording:\n{_ISSUED_URL}\nDone.",
        ),
        (
            [
                {"type": "thinking", "thinking": "link it", "signature": "s"},
                {"type": "text", "text": f"[Recording]({_ISSUED_URL[:445]}\t{_ISSUED_URL[445:]})"},
            ],
            [
                {"type": "thinking", "thinking": "link it", "signature": "s"},
                {"type": "text", "text": f"[Recording]({_ISSUED_URL})"},
            ],
        ),
    ],
    ids=["space", "newline", "text-block"],
)
async def test_a_reply_links_a_tool_issued_url_exactly_as_issued(
    monkeypatch: pytest.MonkeyPatch, written: str | list[str | dict], kept: str | list[str | dict]
) -> None:
    answered = await _answered(AIMessage(content=written), monkeypatch)

    assert answered.content == kept


@pytest.mark.parametrize(
    "written",
    [
        f"[Watch the recording]({_ISSUED_URL})",
        # No tool issued it, so its whitespace stays, however long.
        f"See https://github.com/o/r/pull/1 and https://example.com/{'a1b2' * 30} {'c3d4' * 30}",
    ],
    ids=["exact", "unrelated"],
)
async def test_a_reply_keeps_what_it_wrote_around_issued_urls(
    monkeypatch: pytest.MonkeyPatch, written: str
) -> None:
    answered = await _answered(AIMessage(content=written), monkeypatch)

    assert answered.content == written


async def test_a_changed_issued_url_is_left_and_logged_without_it(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    changed = f"{_ISSUED_URL[:445]}{'H' if _ISSUED_URL[445] != 'H' else 'J'}{_ISSUED_URL[446:]}"
    written = f"[Watch the recording]({changed})"

    with caplog.at_level(logging.WARNING, logger="rt.model_calls"):
        answered = await _answered(AIMessage(content=written), monkeypatch)

    assert answered.content == written
    [record] = [r for r in caplog.records if r.name == "rt.model_calls"]
    assert record.levelno == logging.WARNING
    # Signed URLs grant access to the file: no part of the token is logged.
    assert changed[40:80] not in repr(vars(record))
