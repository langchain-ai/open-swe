"""What every graph's MDA run shares: its config, and running as that run."""

from collections.abc import Awaitable, Callable

from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, START, StateGraph
from langgraph.runtime import Runtime
from pydantic import BaseModel

from openswe.agent_api.tokens import RemoteRun


class _NodeState(BaseModel):
    done: bool = False


def run_config(run: RemoteRun, *, planned: bool = True) -> RunnableConfig:
    """The config the in-process graph would have run with.

    ``planned`` adds what planning the run added to it; planning starts without it.
    """
    return {
        "configurable": {
            **run.configurable,
            **(run.planned_configurable if planned else {}),
            "thread_id": run.thread_id,
            "__is_for_execution__": True,
        },
        "metadata": {**run.metadata, **(run.planned_metadata if planned else {})},
    }


async def in_graph_context[T](
    config: RunnableConfig,
    work: Callable[[Runtime], Awaitable[T]],
    *,
    events: list[object] | None = None,
) -> T:
    """Run a named backend operation with graph context and capture its custom stream events."""
    name = config.get("run_name", "backend_callback")
    results: list[T] = []

    async def node(state: _NodeState, runtime: Runtime) -> dict[str, bool]:  # noqa: ARG001
        results.append(await work(runtime))
        return {"done": True}

    builder = StateGraph(_NodeState)
    builder.add_node(name, node)
    builder.add_edge(START, name)
    builder.add_edge(name, END)
    graph = builder.compile(name=name)
    config = {**config, "metadata": {**config.get("metadata", {}), "backend_operation": name}}
    async for chunk in graph.astream(_NodeState(), config, stream_mode="custom"):
        if events is not None:
            events.append(chunk)
    return results[0]
