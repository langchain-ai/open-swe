from langgraph.graph.state import RunnableConfig

from agent.run_config import RunConfig


def graph_loaded_for_execution(config: RunnableConfig) -> bool:
    return RunConfig.from_config(config).get("__is_for_execution__") is True


def bindable_config(config: RunnableConfig) -> RunnableConfig:
    """``config`` without LangGraph's runtime-internal keys, for ``graph.with_config``.

    The server hands a graph factory a config whose ``configurable`` carries its
    own plumbing: the runtime (``__pregel_runtime``, a read-only runtime for state
    reads and an execution runtime for runs), checkpointer, and store. Binding
    those onto the graph bakes a read-time runtime into every later call, and the
    platform's checkpointer cannot serialize it (it reads ``runtime.context``,
    which the read runtime lacks), so ``GET /threads/{id}/state`` fails with a 500.
    LangGraph re-injects these keys on each invocation, so the bound graph never
    needs them.
    """
    configurable = {
        key: value
        for key, value in (config.get("configurable") or {}).items()
        if not str(key).startswith("__pregel_")
    }
    return {**config, "configurable": configurable}
