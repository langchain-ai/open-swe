"""Per-graph LangSmith tracing-project routing for langgraph.json entrypoints."""

import contextlib
from collections.abc import AsyncIterator, Awaitable, Callable

import langsmith as ls
from langgraph.graph.state import RunnableConfig
from langgraph.pregel import Pregel

AGENT_TRACING_PROJECT = "open-swe-agent"
REVIEW_TRACING_PROJECT = "open-swe-review"

# Injected by langgraph-api into factory config; must not be baked into the
# compiled graph via `.with_config(config)` or `/assistants/{id}/graph` fails
# with AttributeError: '_ReadRuntime' object has no attribute 'override'.
_SERVER_RUNTIME_CONFIG_KEY = "__pregel_runtime"


def strip_server_runtime_config(config: RunnableConfig) -> RunnableConfig:
    """Return a copy of config without the server-injected runtime key."""
    configurable = config.get("configurable")
    if not isinstance(configurable, dict) or _SERVER_RUNTIME_CONFIG_KEY not in configurable:
        return config
    return {
        **config,
        "configurable": {
            key: value for key, value in configurable.items() if key != _SERVER_RUNTIME_CONFIG_KEY
        },
    }


def traced_graph_factory(
    factory: Callable[[RunnableConfig], Awaitable[Pregel]],
    project_name: str,
) -> Callable[[RunnableConfig], contextlib.AbstractAsyncContextManager[Pregel]]:
    @contextlib.asynccontextmanager
    async def entrypoint(config: RunnableConfig) -> AsyncIterator[Pregel]:
        graph = await factory(strip_server_runtime_config(config))
        with ls.tracing_context(project_name=project_name):
            yield graph

    return entrypoint
