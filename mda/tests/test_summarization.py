from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import cast

import pytest
from deepagents import create_deep_agent
from deepagents.backends import FilesystemBackend
from langchain.agents.middleware import AgentMiddleware
from langchain.agents.middleware.types import ModelRequest, ModelResponse
from langchain_core.language_models import BaseChatModel
from langchain_core.language_models.fake_chat_models import FakeListChatModel
from langchain_core.messages import AIMessage, AnyMessage, HumanMessage
from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.memory import InMemorySaver
from rt.backend import Run, RunContext
from rt.middleware import BackendRunMiddleware, RunState
from rt.summarization import RunSummarizationMiddleware
from runtime_api.catalog import Catalog, ModelSpec, RunModels
from runtime_api.loop import ModelCall

_OPUS = ModelSpec(model_id="anthropic:claude-opus-5-5", kwargs={})
_SOL = ModelSpec(model_id="openai:gpt-6.1-sol", kwargs={})
_WINDOWS = {_OPUS.model_id: 1_000_000, _SOL.model_id: 272_000}


class _Planned(AgentMiddleware[RunState]):
    """Inside the summarization, as ``BackendRunMiddleware`` is: what each call carries on with."""

    state_schema = RunState

    def __init__(self) -> None:
        self.seen: list[ModelRequest] = []

    async def awrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], Awaitable[ModelResponse]],
    ) -> ModelResponse:
        self.seen.append(request)
        return await handler(request)


@pytest.mark.parametrize(
    ("built_with", "planned", "summarized"),
    [
        # A run on Opus that routes a call to Sol: 240k tokens fit Opus's window, not Sol's.
        (_OPUS, _SOL, True),
        # A run on Sol that routes a call to Opus, which has room for them all.
        (_SOL, _OPUS, False),
    ],
)
async def test_the_conversation_is_summarized_for_the_model_the_call_goes_to(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    built_with: ModelSpec,
    planned: ModelSpec,
    summarized: bool,
) -> None:
    """deepagents takes Robin's summarization in place of its own, which is sized for the
    graph's model; the call still carries on with the graph's model."""
    models = {
        spec.model_id: FakeListChatModel(
            responses=[f"answer from {spec.model_id}"],
            profile={"max_input_tokens": _WINDOWS[spec.model_id]},
        )
        for spec in (_OPUS, _SOL)
    }
    monkeypatch.setattr(FakeListChatModel, "bind_tools", lambda self, *args, **kwargs: self)
    monkeypatch.setattr("rt.middleware.build_model", lambda spec: models[spec.model_id])
    monkeypatch.setenv("OPEN_SWE_BACKEND_URL", "https://backend.invalid")
    catalog = Catalog(models=RunModels(model=built_with, subagent_model=built_with))
    run = Run(RunContext.model_validate({"__catalog": catalog.model_dump(mode="json")}))
    backend_run = BackendRunMiddleware(run.backend)
    files = FilesystemBackend(root_dir=str(tmp_path), virtual_mode=True)
    monkeypatch.setattr(backend_run, "history_files", lambda runtime: files)
    inner = _Planned()
    graph_model: BaseChatModel = models[built_with.model_id]
    graph = create_deep_agent(
        model=graph_model,
        # Middleware types are invariant in the state they extend; the agent merges them all.
        middleware=cast(list[AgentMiddleware], [RunSummarizationMiddleware(backend_run), inner]),
        backend=files,
        checkpointer=InMemorySaver(),
    )
    config: RunnableConfig = {"configurable": {"thread_id": "summarized"}}
    # About 240k tokens, between Sol's summarization threshold (85% of 272k) and Opus's.
    long = "x" * 960_000
    messages: list[AnyMessage] = [
        HumanMessage(long),
        AIMessage("read it"),
        HumanMessage("now what?"),
    ]

    await graph.ainvoke(
        cast(
            RunState,
            {
                "messages": messages,
                "open_swe_call": ModelCall(system_prompt="", model=planned).model_dump(),
            },
        ),
        config,
    )

    [call] = inner.seen
    assert call.model is graph_model
    state = (await graph.aget_state(config)).values
    assert ("_summarization_event" in state) is summarized
    assert any(long in str(message.content) for message in call.messages) is not summarized
    if summarized:
        # The model the call goes to wrote the summary, not the graph's.
        summary = state["_summarization_event"]["summary_message"].content
        assert f"answer from {planned.model_id}" in summary
