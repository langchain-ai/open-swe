"""What every graph's MDA run shares: its config, and running as that run."""

from collections.abc import Awaitable, Callable
from contextvars import ContextVar
from dataclasses import dataclass, field
from functools import lru_cache
from typing import cast

from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from langgraph.runtime import Runtime
from pydantic import BaseModel

from openswe.agent_api.tokens import RemoteRun


class _NodeState(BaseModel):
    done: bool = False


def run_config(run: RemoteRun, *, settled: bool = True) -> RunnableConfig:
    """The config the in-process graph would have run with.

    ``settled`` adds the settings decided as the run started; working them out again
    starts without them.
    """
    return {
        "configurable": {
            **run.configurable,
            **(run.settings.configurable() if settled else {}),
            "thread_id": run.thread_id,
            "__is_for_execution__": True,
        },
        "metadata": dict(run.metadata),
    }


@dataclass
class _Call:
    """One call's work, and what it answered, for the graph its name compiled once."""

    work: Callable[[Runtime], Awaitable[object]]
    results: list[object] = field(default_factory=list)


_call: ContextVar[_Call] = ContextVar("backend_call")


async def _node(state: _NodeState, runtime: Runtime) -> dict[str, bool]:  # noqa: ARG001
    call = _call.get()
    call.results.append(await call.work(runtime))
    return {"done": True}


@lru_cache(maxsize=256)
def _graph(name: str) -> CompiledStateGraph:
    """The one-node graph ``name``'s calls run in, compiled once: every call of the
    backend compiling its own cost more than the work most of them do."""
    builder = StateGraph(_NodeState)
    builder.add_node(name, _node)
    builder.add_edge(START, name)
    builder.add_edge(name, END)
    return builder.compile(name=name)


async def in_graph_context[T](
    config: RunnableConfig,
    work: Callable[[Runtime], Awaitable[T]],
    *,
    events: list[object] | None = None,
) -> T:
    """Run a named backend operation with graph context and capture its custom stream events."""
    name = config.get("run_name", "backend_callback")
    call = _Call(work)
    config = {**config, "metadata": {**config.get("metadata", {}), "backend_operation": name}}
    # The node reads this call's work from the context its task copies.
    token = _call.set(call)
    try:
        async for chunk in _graph(name).astream(_NodeState(), config, stream_mode="custom"):
            if events is not None:
                events.append(chunk)
    finally:
        _call.reset(token)
    return cast(T, call.results[0])
