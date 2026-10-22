import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import agent
from deepagents import create_deep_agent
from deepagents.backends.state import StateBackend
from langchain_core.language_models.fake_chat_models import FakeListChatModel
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langgraph.checkpoint.memory import InMemorySaver
from rt.backend import Catalog, PhaseReply, SubagentSpec


@pytest.mark.asyncio
@pytest.mark.parametrize("limit", [None, 1, 5])
async def test_managed_factory_bounds_ten_tasks(
    monkeypatch: pytest.MonkeyPatch, limit: int | None
) -> None:
    started: asyncio.Queue[str] = asyncio.Queue()
    release = asyncio.Semaphore(0)
    active = 0
    peak = 0
    finished = 0

    class Model(FakeListChatModel):
        def bind_tools(self, tools: object, **kwargs: object) -> Model:
            return self

        async def _agenerate(self, messages: list[BaseMessage], **kwargs: object) -> ChatResult:
            nonlocal active, peak, finished
            last = messages[-1]
            if isinstance(last, HumanMessage) and last.text == "batch":
                reply = AIMessage(
                    "",
                    tool_calls=[
                        {
                            "id": f"task-{i}",
                            "name": "task",
                            "args": {
                                "description": f"worker-{i}",
                                "subagent_type": "general-purpose",
                            },
                        }
                        for i in range(10)
                    ],
                )
            elif isinstance(last, HumanMessage) and last.text.startswith("worker-"):
                active += 1
                peak = max(peak, active)
                started.put_nowait(last.text)
                try:
                    await release.acquire()
                    finished += 1
                    reply = AIMessage(last.text)
                finally:
                    active -= 1
            else:
                reply = AIMessage("done")
            return ChatResult(generations=[ChatGeneration(message=reply)])

    model = Model(responses=["unused"])
    catalog = Catalog(
        subagents=[
            SubagentSpec(name="general-purpose", description="worker", system_prompt="worker")
        ],
        **({"subagent_concurrency": limit} if limit is not None else {}),
    )
    backend = MagicMock(has_run=True, adopted=None, awaiting_adoption=False)
    backend.catalog = AsyncMock(return_value=catalog)
    backend.phase = AsyncMock(return_value=PhaseReply())
    backend.model_result = AsyncMock(return_value=[])
    backend.tool_started = AsyncMock()
    backend.tool_result = AsyncMock()
    backend.reports.drain = AsyncMock()
    runtime = SimpleNamespace(execution_runtime=SimpleNamespace(context={"invocation_id": "smoke"}))
    monkeypatch.setattr(agent, "OpenSweBackend", lambda token, **kwargs: backend)
    monkeypatch.setattr(agent, "_DEFAULT_MODEL", model)
    definition = await agent.agent(runtime)
    config = dict(definition.config)
    config.pop("sandbox")
    # The async task worker shares the sync subagent's name; only `task` is exercised here.
    config["subagents"] = [s for s in config["subagents"] if not s.get("async")]
    graph = create_deep_agent(**config, backend=StateBackend(), checkpointer=InMemorySaver())
    expected = limit or 3
    async with asyncio.timeout(10):
        run = asyncio.create_task(
            graph.ainvoke(
                {"messages": [HumanMessage("batch")]}, {"configurable": {"thread_id": "smoke"}}
            )
        )
        try:
            for _ in range(expected):
                await started.get()
            await asyncio.sleep(0.02)
            assert active == expected and started.empty(), (active, expected)
            for _ in range(10 - expected):
                release.release()
                await started.get()
                assert active <= expected
            for _ in range(expected):
                release.release()
            result = await run
        finally:
            if not run.done():
                run.cancel()
                await asyncio.gather(run, return_exceptions=True)
    assert peak == expected and finished == 10
    assert sum(isinstance(m, ToolMessage) for m in result["messages"]) == 10
