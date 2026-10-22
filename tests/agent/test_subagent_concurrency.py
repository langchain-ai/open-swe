import asyncio
from collections.abc import Awaitable, Callable

import pytest
from deepagents import create_deep_agent
from deepagents.backends.state import StateBackend
from langchain.agents.middleware import ModelRequest, ModelResponse, wrap_model_call
from langchain_core.language_models.fake_chat_models import FakeListChatModel
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langgraph.checkpoint.memory import InMemorySaver

from openswe.middleware.task_retry import TaskConcurrencyMiddleware
from openswe.server import _DisableInheritedMiddleware, _SubagentToolGuard
from tests.agent.test_agent_assembly_context import _base_config, _capture_create_deep_agent_kwargs
from tests.agent.test_agent_assembly_context import saved_thread_scope as saved_thread_scope


@pytest.mark.parametrize("limit", [None, 1, 5])
async def test_ten_tasks_queue_until_a_subagent_finishes(
    monkeypatch: pytest.MonkeyPatch, limit: int | None
) -> None:
    monkeypatch.delenv("OPEN_SWE_SUBAGENT_CONCURRENCY", raising=False)
    if limit is not None:
        monkeypatch.setenv("OPEN_SWE_SUBAGENT_CONCURRENCY", str(limit))
    expected = limit or 3
    kwargs = await _capture_create_deep_agent_kwargs(_base_config())
    assembled = kwargs["middleware"]
    assert isinstance(assembled, list)
    middleware = [item for item in assembled if isinstance(item, TaskConcurrencyMiddleware)]
    subagent = kwargs["subagents"][0]
    subagent = {
        **subagent,
        "middleware": [
            item
            for item in subagent["middleware"]
            if isinstance(item, (_DisableInheritedMiddleware, _SubagentToolGuard))
        ],
    }
    started: asyncio.Queue[str] = asyncio.Queue()
    release = asyncio.Semaphore(0)
    active = 0
    peak = 0
    finished: list[str] = []
    refused = 0

    @wrap_model_call
    async def worker(
        request: ModelRequest, handler: Callable[[ModelRequest], Awaitable[ModelResponse]]
    ) -> ModelResponse:
        nonlocal active, peak, refused
        if isinstance(request.messages[-1], ToolMessage):
            refused += 1
            return ModelResponse(result=[AIMessage("done")])
        task = request.messages[-1].text.splitlines()[-1]
        active += 1
        peak = max(peak, active)
        started.put_nowait(task)
        try:
            await release.acquire()
            finished.append(task)
            return ModelResponse(
                result=[
                    AIMessage(
                        task,
                        tool_calls=[
                            {
                                "id": f"nested-{task}",
                                "name": "task",
                                "args": {
                                    "description": "nested",
                                    "subagent_type": "general-purpose",
                                },
                            }
                        ],
                    )
                ]
            )
        finally:
            active -= 1

    @wrap_model_call
    async def parent(
        request: ModelRequest, handler: Callable[[ModelRequest], Awaitable[ModelResponse]]
    ) -> ModelResponse:
        if request.state.get("_deepagents_forked_context"):
            return await worker.awrap_model_call(request, handler)
        if isinstance(request.messages[-1], HumanMessage):
            return ModelResponse(
                result=[
                    AIMessage(
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
                ]
            )
        return ModelResponse(result=[AIMessage("done")])

    model = FakeListChatModel(responses=["unused"])
    graph = create_deep_agent(
        model=model,
        backend=StateBackend(),
        middleware=[*middleware, parent],
        subagents=[
            {**subagent, "model": model, "middleware": [*subagent.get("middleware", []), worker]}
        ],
        checkpointer=InMemorySaver(),
    )
    async with asyncio.timeout(10):
        run = asyncio.create_task(
            graph.ainvoke(
                {"messages": [HumanMessage("delegate")]},
                {"configurable": {"thread_id": "batch"}},
            )
        )
        try:
            initial = [await started.get() for _ in range(expected)]
            assert len(set(initial)) == expected
            await asyncio.sleep(0.02)
            assert started.empty()
            assert active == expected
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
    assert refused == 10
    assert peak == expected
    assert set(finished) == {f"worker-{i}" for i in range(10)}
    assert (
        len([message for message in result["messages"] if isinstance(message, ToolMessage)]) == 10
    )


@pytest.mark.parametrize("value", ["0", "-1", "invalid"])
def test_invalid_subagent_limit_fails_instead_of_hanging(
    monkeypatch: pytest.MonkeyPatch, value: str
) -> None:
    monkeypatch.setenv("OPEN_SWE_SUBAGENT_CONCURRENCY", value)
    with pytest.raises(ValueError):
        TaskConcurrencyMiddleware()
