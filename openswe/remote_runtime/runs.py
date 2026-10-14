"""What every graph's MDA run shares: the models it starts with, and running as that run."""

from collections.abc import Awaitable, Callable

from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, START, StateGraph
from langgraph.runtime import Runtime
from pydantic import BaseModel, JsonValue

from openswe.remote_runtime.tokens import RemoteRun


class ModelSpec(BaseModel):
    model_id: str
    kwargs: dict[str, JsonValue]


class RunModels(BaseModel):
    """The models an MDA run builds its graph with."""

    model: ModelSpec
    subagent_model: ModelSpec
    use_gateway: bool


class _NodeState(BaseModel):
    done: bool = False


def run_config(run: RemoteRun) -> RunnableConfig:
    """The config the in-process graph would have run with."""
    return {
        "configurable": {
            **run.configurable,
            "thread_id": run.thread_id,
            "__is_for_execution__": True,
        }
    }


async def in_graph_context[T](
    config: RunnableConfig,
    work: Callable[[Runtime], Awaitable[T]],
    *,
    events: list[object] | None = None,
) -> T:
    """Run ``work`` inside a one-node graph so ``get_config`` and the stream writer resolve.

    What ``work`` streams through ``get_stream_writer`` lands in ``events``.
    """
    results: list[T] = []

    async def node(state: _NodeState, runtime: Runtime) -> dict[str, bool]:  # noqa: ARG001
        results.append(await work(runtime))
        return {"done": True}

    builder = StateGraph(_NodeState)
    builder.add_node("work", node)
    builder.add_edge(START, "work")
    builder.add_edge("work", END)
    graph = builder.compile()
    async for chunk in graph.astream(_NodeState(), config, stream_mode="custom"):
        if events is not None:
            events.append(chunk)
    return results[0]
