from pathlib import Path

from deepagents.backends import FilesystemBackend
from deepagents.middleware.summarization import SummarizationState
from langchain.agents import create_agent
from langchain.agents.middleware import AgentMiddleware, AgentState, hook_config
from langchain_core.language_models.fake_chat_models import FakeListChatModel
from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.runtime import Runtime
from rt.offload import RequestedOffload


class _Offloading(AgentMiddleware):
    """Offload before the first model call and end the run, as a requested offload does."""

    # As MDA's own summarization keeps it in every graph's state.
    state_schema = SummarizationState

    def __init__(self, offload: RequestedOffload) -> None:
        self._offload = offload

    @hook_config(can_jump_to=["end"])
    async def abefore_model(self, state: AgentState, runtime: Runtime) -> dict[str, object]:
        update, _outcome = await self._offload.offload(state, runtime)
        return {**update, "jump_to": "end"}


async def test_a_requested_offload_keeps_the_history_and_streams_only_its_status(
    tmp_path: Path,
) -> None:
    model = FakeListChatModel(responses=["A private summary of the old conversation."])
    offload = RequestedOffload(model, FilesystemBackend(root_dir=str(tmp_path), virtual_mode=True))
    offload._lc_helper.keep = ("messages", 2)
    graph = create_agent(
        model=model, middleware=[_Offloading(offload)], checkpointer=InMemorySaver()
    )
    messages = [
        HumanMessage(content="Remember the deployment decision.", id="human-1"),
        AIMessage(content="Use the staging environment.", id="ai-1"),
        HumanMessage(content="What next?", id="human-2"),
        AIMessage(content="Run the smoke tests.", id="ai-2"),
    ]
    config: RunnableConfig = {"configurable": {"thread_id": "offloading-test"}}

    chunks = [
        chunk
        async for chunk in graph.astream(
            {"messages": messages}, config, stream_mode=["messages", "custom"]
        )
    ]

    assert not [data for mode, data in chunks if mode == "messages"]
    statuses = [data for mode, data in chunks if mode == "custom" and isinstance(data, dict)]
    assert [event["status"] for event in statuses] == ["started", "completed"]
    assert statuses[-1]["trigger"] == "manual"
    state = await graph.aget_state(config)
    assert state.values["messages"] == messages
    event = state.values["_summarization_event"]
    assert event["cutoff_index"] == 2
    assert "private summary" in event["summary_message"].content
    assert "deployment decision" in (tmp_path / event["file_path"].lstrip("/")).read_text()

    # Nothing new since the summary: nothing is offloaded again.
    chunks = [chunk async for chunk in graph.astream({}, config, stream_mode="custom")]
    assert chunks[-1]["status"] == "skipped"
    assert (await graph.aget_state(config)).values["_summarization_event"] == event
