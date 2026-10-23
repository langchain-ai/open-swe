import pytest
from deepagents.backends import FilesystemBackend
from deepagents.graph import _apply_custom_middleware
from deepagents.middleware.summarization import SummarizationMiddleware
from langchain.agents import create_agent
from langchain.agents.middleware import wrap_model_call
from langchain.agents.middleware.types import ModelRequest, ModelResponse
from langchain_core.exceptions import ContextOverflowError
from langchain_core.language_models.fake_chat_models import FakeListChatModel
from langchain_core.messages import AIMessage, HumanMessage
from langgraph.checkpoint.memory import InMemorySaver

from agent.middleware import conversation_offloading as module
from agent.middleware.conversation_offloading import (
    CompactionSettings,
    ConversationOffloadingMiddleware,
)
from agent.middleware.prepare_run import BasePrepareRunMiddleware


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
    offloading = (await graph.aget_state(config)).values["conversation_offloading"]
    assert offloading["status"] == "completed"
    assert offloading["messages_summarized"] == 2
    assert offloading["summarized_tokens"] > 0
    assert offloading["summary_tokens"] > 0
    assert offloading["duration_ms"] >= 0
    assert offloading["count"] == 1
    assert offloading["total_messages_summarized"] == 2
    assert offloading["total_summarized_tokens"] == offloading["summarized_tokens"]
    assert offloading["total_summary_tokens"] == offloading["summary_tokens"]
    assert offloading["total_duration_ms"] == offloading["duration_ms"]


async def test_repeated_compaction_accumulates_totals(tmp_path):
    model = FakeListChatModel(responses=["SUMMARY ONE", "SUMMARY TWO"])
    middleware = ConversationOffloadingMiddleware(
        model, FilesystemBackend(root_dir=str(tmp_path), virtual_mode=True)
    )
    middleware._lc_helper.keep = ("messages", 2)
    middleware._lc_helper._trigger_clauses = [{"messages": 4}]
    graph = create_agent(model=model, middleware=[middleware], checkpointer=InMemorySaver())
    config = {"configurable": {"thread_id": "accumulate"}}
    await graph.ainvoke(
        {
            "messages": [
                HumanMessage(content="old"),
                AIMessage(content="old reply"),
                HumanMessage(content="recent"),
                AIMessage(content="recent reply"),
            ]
        },
        config,
    )
    first = (await graph.aget_state(config)).values["conversation_offloading"]
    assert first["count"] == 1
    await graph.ainvoke(
        {"messages": [HumanMessage(content="next"), AIMessage(content="next reply")]},
        config,
    )
    second = (await graph.aget_state(config)).values["conversation_offloading"]
    assert second["count"] == 2
    assert second["total_messages_summarized"] > first["total_messages_summarized"]
    assert second["total_summarized_tokens"] >= first["total_summarized_tokens"]
    assert second["total_summary_tokens"] >= first["total_summary_tokens"]
    assert second["total_duration_ms"] >= first["total_duration_ms"]


async def test_failed_summary_records_partial_metrics(tmp_path, monkeypatch):
    from agent.middleware import conversation_offloading as module

    recorded = []

    async def record(**kwargs):
        recorded.append(kwargs)

    async def fail(self, messages_to_summarize):
        raise RuntimeError("summary model unavailable")

    monkeypatch.setattr(module, "record_context_offloaded", record)
    monkeypatch.setattr(SummarizationMiddleware, "_acreate_summary", fail)

    model = FakeListChatModel(responses=["unused"])
    middleware = ConversationOffloadingMiddleware(
        model, FilesystemBackend(root_dir=str(tmp_path), virtual_mode=True)
    )
    middleware._lc_helper.keep = ("messages", 2)
    middleware._lc_helper._trigger_clauses = [{"messages": 4}]
    graph = create_agent(model=model, middleware=[middleware], checkpointer=InMemorySaver())
    config = {"configurable": {"thread_id": "failure"}}
    with pytest.raises(RuntimeError, match="summary model unavailable"):
        async for _ in graph.astream(
            {
                "messages": [
                    HumanMessage(content="old"),
                    AIMessage(content="old reply"),
                    HumanMessage(content="recent"),
                    AIMessage(content="recent reply"),
                ]
            },
            config,
            stream_mode="custom",
        ):
            pass

    [event] = recorded
    assert event["status"] == "failed"
    assert event["messages_summarized"] == 2
    assert event["summarized_tokens"] > 0
    assert "summary_tokens" not in event
    assert event["duration_ms"] >= 0


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


def test_settings_override_trigger_and_summary_prompt(tmp_path):
    model = FakeListChatModel(responses=["summary"])
    middleware = ConversationOffloadingMiddleware(
        model,
        FilesystemBackend(root_dir=str(tmp_path), virtual_mode=True),
        settings=CompactionSettings(trigger_tokens=1234, summary_prompt="CUSTOM PROMPT {messages}"),
    )
    assert middleware._lc_helper._trigger_clauses == [{"tokens": 1234}]
    assert middleware._lc_helper.summary_prompt == "CUSTOM PROMPT {messages}"


async def test_context_overflow_escape_is_recorded(tmp_path, monkeypatch):
    model = FakeListChatModel(responses=["SUMMARY", "ANSWER"])
    middleware = ConversationOffloadingMiddleware(
        model,
        FilesystemBackend(root_dir=str(tmp_path), virtual_mode=True),
        settings=CompactionSettings(trigger_tokens=10**9),
    )
    middleware._lc_helper.keep = ("messages", 2)
    recorded = []

    async def record(**kwargs):
        recorded.append(kwargs)

    monkeypatch.setattr(module, "record_context_offloaded", record)

    attempts = {"count": 0}

    @wrap_model_call
    async def overflow_once(request, handler):
        if attempts["count"] == 0:
            attempts["count"] += 1
            raise ContextOverflowError("prompt too long")
        return await handler(request)

    graph = create_agent(
        model=model, middleware=[middleware, overflow_once], checkpointer=InMemorySaver()
    )
    config = {"configurable": {"thread_id": "overflow"}}
    await graph.ainvoke(
        {
            "messages": [
                HumanMessage(content="old"),
                AIMessage(content="old reply"),
                HumanMessage(content="recent"),
                AIMessage(content="recent reply"),
            ]
        },
        config,
    )
    state = (await graph.aget_state(config)).values["conversation_offloading"]
    assert state["trigger"] == "overflow"
    assert state["count"] == 1
    assert state["overflow_count"] == 1
    [event] = recorded
    assert event["trigger"] == "overflow"
    assert event["status"] == "completed"
