from typing import Any, cast
from unittest.mock import MagicMock

import pytest
from deepagents.backends import FilesystemBackend
from deepagents.graph import _apply_custom_middleware
from deepagents.middleware.summarization import SummarizationMiddleware
from langchain.agents import create_agent
from langchain.agents.middleware.types import ModelRequest, ModelResponse
from langchain_core.language_models.fake_chat_models import FakeListChatModel
from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.memory import InMemorySaver

from openswe.middleware.conversation_offloading import ConversationOffloadingMiddleware


async def test_a_requested_offload_ends_the_run_with_the_summary_in_its_state(tmp_path):
    from openswe.agent_api.runs import in_graph_context

    model = FakeListChatModel(responses=["A private summary of the old conversation."])
    middleware = ConversationOffloadingMiddleware(
        model, FilesystemBackend(root_dir=str(tmp_path), virtual_mode=True), manual=True
    )
    middleware._lc_helper.keep = ("messages", 2)
    messages = [
        HumanMessage(content="Remember the deployment decision.", id="human-1"),
        AIMessage(content="Use the staging environment.", id="ai-1"),
        HumanMessage(content="What next?", id="human-2"),
        AIMessage(content="Run the smoke tests.", id="ai-2"),
    ]
    config: RunnableConfig = {"configurable": {"thread_id": "offloading-test"}}
    statuses: list[object] = []

    end = await in_graph_context(
        config,
        lambda runtime: middleware.aend_instead({"messages": messages}, runtime),
        events=statuses,
    )

    assert end is not None
    assert [event["status"] for event in cast(list[dict[str, str]], statuses)] == [
        "started",
        "completed",
    ]
    assert cast(list[dict[str, str]], statuses)[-1]["trigger"] == "manual"
    # The history stays; the summary rides in state for the runtime's summarization.
    assert "messages" not in end.update
    event = cast(dict[str, Any], end.update["_summarization_event"])
    assert event["cutoff_index"] == 2
    assert "private summary" in event["summary_message"].content
    assert "deployment decision" in (tmp_path / event["file_path"].lstrip("/")).read_text()
    again: list[object] = []
    ended_again = await in_graph_context(
        config,
        lambda runtime: middleware.aend_instead(
            {"messages": messages, "_summarization_event": event}, runtime
        ),
        events=again,
    )
    assert ended_again is not None
    assert cast(list[dict[str, str]], again)[-1]["status"] == "skipped"
    automatic = ConversationOffloadingMiddleware(
        model, FilesystemBackend(root_dir=str(tmp_path), virtual_mode=True)
    )
    assert await automatic.aend_instead({"messages": messages}, MagicMock()) is None


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
