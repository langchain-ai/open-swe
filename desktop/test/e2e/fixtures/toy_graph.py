"""Deterministic LangGraph used as the desktop's local backend in E2E tests.

It speaks the real LangGraph dev protocol (threads, runs, SSE messages) but
never calls a provider, so a run can be killed or frozen on demand and the
timing is ours. Markers in the last human message control the run:

    TOY_STREAM_MS=<n>  how long the answer takes to stream (default 12000)
    TOY_CRASH_MS=<n>   kill the process this many ms into the answer
    TOY_TOOL_MS=<n>    run the toy tool this many ms into the answer; the tool
                       appends one line to $TOY_TOOL_LOG, so a replayed step
                       leaves a second line behind

The continuation the desktop's recovery coordinator sends is recognised by its
envelope sender, so the answer a resumed turn produces is distinguishable from
the first attempt.

Every lifecycle event is appended to ``TOY_GRAPH_LOG`` as JSONL before it is
acted on, because the process may die a moment later.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import time
from collections.abc import AsyncIterator, Iterator
from typing import Annotated, Any, TypedDict

from langchain_core.callbacks import (
    AsyncCallbackManagerForLLMRun,
    CallbackManagerForLLMRun,
)
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import (
    AIMessage,
    AIMessageChunk,
    BaseMessage,
    HumanMessage,
    ToolMessage,
)
from langchain_core.outputs import (
    ChatGeneration,
    ChatGenerationChunk,
    ChatResult,
)
from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages

RESUME_MARKER = 'sender="system:network-resume"'
DEFAULT_STREAM_MS = 12_000
DEFAULT_RESUME_MS = 3_000
CHUNK_MS = 100


def _log(event: str, **fields: Any) -> None:
    path = os.environ.get("TOY_GRAPH_LOG")
    if not path:
        return
    record = {"ts": time.time(), "event": event, "pid": os.getpid(), **fields}
    with open(path, "a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False) + "\n")
        handle.flush()


def _text_of(message: BaseMessage) -> str:
    content = message.content
    if isinstance(content, str):
        return content
    parts: list[str] = []
    for block in content:
        if isinstance(block, dict) and block.get("type") == "text":
            parts.append(str(block.get("text", "")))
        elif isinstance(block, str):
            parts.append(block)
    return "\n".join(parts)


def _marker(text: str, name: str) -> int | None:
    match = re.search(rf"{name}=(\d+)", text)
    return int(match.group(1)) if match else None


def _chunks(text: str, size: int = 4) -> Iterator[str]:
    for index in range(0, len(text), size):
        yield text[index : index + size]


class ToyChatModel(BaseChatModel):
    """Streams a deterministic answer, optionally dying partway through."""

    reply: str = ""
    stream_ms: int = DEFAULT_STREAM_MS
    crash_ms: int | None = None
    resume: bool = False
    call_tool: bool = False

    @property
    def _llm_type(self) -> str:
        return "toy-echo"

    def _delay(self, index: int) -> float:
        """Front-load the first words so the UI shows text before a crash."""
        chunks = max(1, len(self.reply) // 4)
        head = min(10, chunks)
        if index < head:
            return 0.12
        tail = chunks - head
        if tail <= 0:
            return 0.12
        remaining = max(0.0, self.stream_ms / 1000 - head * 0.12)
        return max(0.05, remaining / tail)

    def _tick(self, index: int, started: float) -> None:
        if self.crash_ms is not None and (time.monotonic() - started) * 1000 >= self.crash_ms:
            _log("crash", crash_ms=self.crash_ms, resume=self.resume)
            os._exit(17)

    def _astream_impl(self) -> Iterator[tuple[str, float]]:
        for index, chunk in enumerate(_chunks(self.reply)):
            yield chunk, self._delay(index)

    def _tool_call(self) -> AIMessageChunk:
        return AIMessageChunk(
            content="",
            tool_call_chunks=[
                {
                    "name": TOOL_NAME,
                    "args": "{}",
                    "id": f"call-{int(time.time() * 1000)}",
                    "index": 0,
                }
            ],
        )

    def _stream(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: CallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> Iterator[ChatGenerationChunk]:
        started = time.monotonic()
        for index, (chunk, delay) in enumerate(self._astream_impl()):
            time.sleep(delay)
            self._tick(index, started)
            yield ChatGenerationChunk(message=AIMessageChunk(content=chunk))

    async def _astream(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: AsyncCallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> AsyncIterator[ChatGenerationChunk]:
        started = time.monotonic()
        for index, (chunk, delay) in enumerate(self._astream_impl()):
            await asyncio.sleep(delay)
            self._tick(index, started)
            yield ChatGenerationChunk(message=AIMessageChunk(content=chunk))
        if self.call_tool:
            yield ChatGenerationChunk(message=self._tool_call())

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: CallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> ChatResult:
        started = time.monotonic()
        parts: list[str] = []
        for index, (chunk, delay) in enumerate(self._astream_impl()):
            time.sleep(delay)
            self._tick(index, started)
            parts.append(chunk)
        message = AIMessage(content="".join(parts))
        if self.call_tool:
            message = AIMessage(
                content="".join(parts),
                tool_calls=[
                    {"name": TOOL_NAME, "args": {}, "id": f"call-{int(time.time() * 1000)}"}
                ],
            )
        return ChatResult(generations=[ChatGeneration(message=message)])


class ToyState(TypedDict):
    messages: Annotated[list[BaseMessage], add_messages]


TOOL_NAME = "toy_apply_change"


def _tool_log() -> str | None:
    return os.environ.get("TOY_TOOL_LOG")


def _apply_change_once() -> str:
    """The toy's one side effect: append a line, so a replay is countable.

    The line is written *before* the tool returns, so a process that dies
    between the write and the checkpoint still shows the effect it had. That is
    the case a continuation has to avoid repeating.
    """
    path = _tool_log()
    if path is None:
        return "no-op"
    with open(path, "a", encoding="utf-8") as handle:
        handle.write(json.dumps({"ts": time.time(), "pid": os.getpid()}) + "\n")
        handle.flush()
        os.fsync(handle.fileno())
    return "applied"


async def apply_change(state: ToyState, config: RunnableConfig) -> dict[str, list[BaseMessage]]:
    last = state["messages"][-1]
    call = last.tool_calls[0] if isinstance(last, AIMessage) and last.tool_calls else None
    if call is None:
        return {}
    result = _apply_change_once()
    configurable = config.get("configurable") or {}
    _log(
        "tool_start",
        thread_id=str(configurable.get("thread_id")),
        tool=call["name"],
        result=result,
    )
    configurable_delay = _marker(_text_of(last), "TOY_TOOL_HOLD_MS") or 0
    if configurable_delay:
        await asyncio.sleep(configurable_delay / 1000)
    _log("tool_end", thread_id=str(configurable.get("thread_id")), tool=call["name"])
    return {
        "messages": [
            ToolMessage(content=result, tool_call_id=str(call["id"])),
        ]
    }


def _visible(text: str) -> str:
    """The marker tokens drive the toy, they are not part of what it says."""
    return re.sub(r"\s*TOY_[A-Z_]+=\d+", "", text).strip()


TAIL = " This is a deterministic toy answer used by the recovery test." * 4


def _reply_for(text: str) -> tuple[ToyChatModel, str]:
    resume = RESUME_MARKER in text
    stream_ms = _marker(text, "TOY_STREAM_MS")
    crash_ms = _marker(text, "TOY_CRASH_MS")
    call_tool = _marker(text, "TOY_TOOL_MS") is not None
    if resume:
        task = text.split("<content>", 1)[-1].split("</content>", 1)[0]
        reply = f"Continuation done. Previous task: {_visible(task)[:80]}{TAIL}"
        duration = stream_ms if stream_ms is not None else DEFAULT_RESUME_MS
    else:
        reply = f"Toy answer for: {_visible(text)[:120]}{TAIL}"
        duration = stream_ms if stream_ms is not None else DEFAULT_STREAM_MS
    return (
        ToyChatModel(
            reply=reply,
            stream_ms=duration,
            crash_ms=crash_ms,
            resume=resume,
            call_tool=call_tool,
        ),
        reply,
    )


async def respond(state: ToyState, config: RunnableConfig) -> dict[str, list[BaseMessage]]:
    last = state["messages"][-1]
    # The turn after a tool result is the model answering, not asking again:
    # re-issuing the call would loop forever, and answering is what gives a
    # crash-after-the-side-effect somewhere to happen.
    if isinstance(last, ToolMessage):
        # Answered with the same streaming model so a crash can be placed here,
        # after the side effect, where a replayed step would repeat it.
        original = _text_of(state["messages"][0])
        follow_up = ToyChatModel(
            reply=f"Applied: {last.content}{TAIL}",
            stream_ms=_marker(original, "TOY_AFTER_MS") or DEFAULT_RESUME_MS,
            crash_ms=_marker(original, "TOY_AFTER_CRASH_MS"),
        )
        answer = await follow_up.ainvoke([*state["messages"], HumanMessage(content="")])
        return {"messages": [answer]}
    text = _text_of(last)
    model, _ = _reply_for(text)
    configurable = config.get("configurable") or {}
    _log(
        "run_start",
        thread_id=str(configurable.get("thread_id")),
        resume=model.resume,
        stream_ms=model.stream_ms,
        crash_ms=model.crash_ms,
        input=text[:200],
    )
    answer = await model.ainvoke(state["messages"])
    _log("run_end", thread_id=str(configurable.get("thread_id")), resume=model.resume)
    return {"messages": [answer]}


builder = StateGraph(ToyState)
builder.add_node("agent", respond)
builder.add_edge(START, "agent")
builder.add_edge("agent", END)
graph = builder.compile()


def _after_agent(state: ToyState) -> str:
    last = state["messages"][-1]
    return "tools" if isinstance(last, AIMessage) and last.tool_calls else END


tooled = StateGraph(ToyState)
tooled.add_node("agent", respond)
tooled.add_node("tools", apply_change)
tooled.add_edge(START, "agent")
# The tool result feeds back into the model, the same loop a real agent runs,
# so a continuation that replays a step is visible as a second tool call.
tooled.add_conditional_edges("agent", _after_agent, ["tools", END])
tooled.add_edge("tools", "agent")
graph_with_tools = tooled.compile()

_log("imported")
