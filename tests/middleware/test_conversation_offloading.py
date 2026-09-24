from dataclasses import dataclass
from typing import Any, cast
from unittest.mock import MagicMock

import pytest
from deepagents.backends import FilesystemBackend
from deepagents.graph import _apply_custom_middleware
from deepagents.middleware.summarization import SummarizationMiddleware
from langchain.agents import create_agent
from langchain.agents.middleware.types import ModelRequest, ModelResponse, ToolCallRequest
from langchain_core.language_models.fake_chat_models import FakeListChatModel
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langgraph.checkpoint.memory import InMemorySaver

from agent.middleware.conversation_offloading import ConversationOffloadingMiddleware
from agent.middleware.prepare_run import BasePrepareRunMiddleware


@dataclass
class _ToolRequest:
    tool_call: dict[str, Any]


async def _tool_handler(request: ToolCallRequest) -> ToolMessage:
    return ToolMessage(content="file contents", tool_call_id=request.tool_call["id"])


async def test_manual_offload_preserves_history_and_hides_summary_stream(tmp_path):
    model = FakeListChatModel(responses=["A private summary of the old conversation."])
    middleware = ConversationOffloadingMiddleware(
        model, FilesystemBackend(root_dir=str(tmp_path), virtual_mode=True), manual=True
    )
    middleware._lc_helper.keep = ("messages", 2)
    graph = create_agent(model=model, middleware=[middleware], checkpointer=InMemorySaver())
    messages = [
        HumanMessage(content="Remember the deployment decision.", id="human-1"),
        AIMessage(content="Use the staging environment.", id="ai-1"),
        HumanMessage(content="What next?", id="human-2"),
        AIMessage(content="Run the smoke tests.", id="ai-2"),
    ]
    config = {"configurable": {"thread_id": "offloading-test"}}
    chunks = [
        chunk
        async for chunk in graph.astream(
            {"messages": messages}, config, stream_mode=["messages", "custom"]
        )
    ]
    assert not [data for mode, data in chunks if mode == "messages"]
    statuses = [data for mode, data in chunks if mode == "custom"]
    assert [event["status"] for event in statuses] == ["started", "completed"]
    assert statuses[-1]["trigger"] == "manual"
    state = await graph.aget_state(config)
    assert state.values["messages"] == messages
    event = state.values["_summarization_event"]
    assert event["cutoff_index"] == 2
    assert "private summary" in event["summary_message"].content
    assert "deployment decision" in (tmp_path / event["file_path"].lstrip("/")).read_text()
    assert state.values["conversation_offloading"]["status"] == "completed"

    chunks = [chunk async for chunk in graph.astream({}, config, stream_mode="custom")]
    assert chunks[-1]["status"] == "skipped"
    assert (await graph.aget_state(config)).values["_summarization_event"] == event

    normal = ConversationOffloadingMiddleware(
        model, FilesystemBackend(root_dir=str(tmp_path), virtual_mode=True)
    )
    graph = create_agent(model=model, middleware=[normal], checkpointer=graph.checkpointer)
    chunks = [
        chunk
        async for chunk in graph.astream(
            {"messages": [HumanMessage(content="Continue.")]},
            config,
            stream_mode=["messages", "custom"],
        )
    ]
    assert any(mode == "messages" for mode, _ in chunks)
    assert not any(mode == "custom" for mode, _ in chunks)


def test_name_replaces_default_summarization(tmp_path):
    model = FakeListChatModel(responses=["summary"])
    backend = FilesystemBackend(root_dir=str(tmp_path), virtual_mode=True)
    default = SummarizationMiddleware(model=model, backend=backend)
    replacement = ConversationOffloadingMiddleware(model, backend)
    assert _apply_custom_middleware([default], [replacement]) == [replacement]


@pytest.mark.parametrize("suppress", [True, False])
async def test_automatic_completion_precedes_handler_and_suppresses_tokens(
    tmp_path, monkeypatch, suppress
):
    model = FakeListChatModel(responses=["PRIVATE SUMMARY"])
    middleware = ConversationOffloadingMiddleware(
        model, FilesystemBackend(root_dir=str(tmp_path), virtual_mode=True)
    )
    middleware._lc_helper.keep = ("messages", 2)
    middleware._lc_helper._trigger_clauses = [{"messages": 4}]
    if not suppress:
        middleware.model.tags = [tag for tag in (middleware.model.tags or []) if tag != "nostream"]
    events = []
    original_status = middleware._status

    def status(value, **details):
        events.append(value)
        return original_status(value, **details)

    monkeypatch.setattr(middleware, "_status", status)

    async def handler(request: ModelRequest) -> ModelResponse:
        assert events == ["started", "completed"]
        assert list(tmp_path.rglob("*.md"))
        return ModelResponse(result=[AIMessage(content="NORMAL ANSWER")])

    from langchain.agents.middleware import wrap_model_call

    @wrap_model_call
    async def answer(request, next_handler):
        return await handler(request)

    graph = create_agent(model=model, middleware=[middleware, answer], checkpointer=InMemorySaver())
    config = {"configurable": {"thread_id": "automatic"}}
    chunks = [
        chunk
        async for chunk in graph.astream(
            {
                "messages": [
                    HumanMessage(content="old"),
                    AIMessage(content="old reply"),
                    HumanMessage(content="recent"),
                    AIMessage(content="recent reply"),
                ]
            },
            config,
            stream_mode=["messages", "custom"],
        )
    ]
    streamed = "".join(str(data[0].content) for mode, data in chunks if mode == "messages")
    assert ("PRIVATE SUMMARY" in streamed) is not suppress
    assert events == ["started", "completed"]
    assert (await graph.aget_state(config)).values["conversation_offloading"][
        "status"
    ] == "completed"


async def test_manual_before_model_runs_after_prepare(tmp_path, monkeypatch):
    prepared = []

    class Prepare(BasePrepareRunMiddleware):
        async def _prepare(self, state, runtime):
            prepared.append(True)
            return {"work_dir": str(tmp_path)}

    model = FakeListChatModel(responses=["summary"])
    middleware = ConversationOffloadingMiddleware(
        model, FilesystemBackend(root_dir=str(tmp_path), virtual_mode=True), manual=True
    )
    original_status = middleware._status

    def status(value, **details):
        assert prepared == [True]
        return original_status(value, **details)

    monkeypatch.setattr(middleware, "_status", status)
    graph = create_agent(model=model, middleware=[middleware, Prepare()])
    state = await graph.ainvoke({"messages": [HumanMessage(content="hello")]})
    assert state["run_prepared"] is True
    assert state["conversation_offloading"]["status"] == "skipped"


async def test_degraded_summary_keeps_recent_tail(tmp_path):
    model = FakeListChatModel(responses=["Previous conversation was too long to summarize."])
    middleware = ConversationOffloadingMiddleware(
        model, FilesystemBackend(root_dir=str(tmp_path), virtual_mode=True), manual=True
    )
    middleware._lc_helper.keep = ("messages", 1)
    graph = create_agent(model=model, middleware=[middleware], checkpointer=InMemorySaver())
    config = {"configurable": {"thread_id": "degraded"}}
    chunks = [
        chunk
        async for chunk in graph.astream(
            {
                "messages": [
                    HumanMessage(content="old context"),
                    AIMessage(content="old answer"),
                    HumanMessage(content="recent context"),
                ]
            },
            config,
            stream_mode="custom",
        )
    ]
    assert [chunk["status"] for chunk in chunks] == ["started", "degraded", "completed"]
    event = (await graph.aget_state(config)).values["_summarization_event"]
    assert "old context" in event["summary_message"].content


async def test_history_reads_return_notices_for_duplicates_and_after_five_pages():
    middleware = ConversationOffloadingMiddleware(
        FakeListChatModel(responses=["summary"]), MagicMock()
    )

    async def call(offset: int) -> ToolMessage:
        request = _ToolRequest(
            tool_call={
                "name": "read_file",
                "args": {"file_path": "/conversation_history/session_test.md", "offset": offset},
                "id": f"call-{offset}",
            }
        )
        return cast(
            ToolMessage,
            await middleware.awrap_tool_call(cast(ToolCallRequest, request), _tool_handler),
        )

    assert (await call(0)).content == "file contents"
    assert "already served" in (await call(0)).content
    for offset in range(1, 5):
        assert (await call(offset)).content == "file contents"
    assert "budget exhausted" in (await call(5)).content


async def test_compaction_limit_returns_explicit_message():
    middleware = ConversationOffloadingMiddleware(
        FakeListChatModel(responses=["summary"]), MagicMock()
    )
    middleware._invocation_state().summarizations = 10
    called = False

    async def handler(request: ModelRequest) -> ModelResponse:
        nonlocal called
        called = True
        return ModelResponse(result=[])

    response = await middleware.awrap_model_call(cast(ModelRequest, object()), handler)
    assert (
        response.result[0].content
        == "Turn stopped because conversation compaction exceeded its limit."
    )
    assert called is False
