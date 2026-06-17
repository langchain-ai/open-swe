"""Per-graph LangSmith tracing-project routing for langgraph.json entrypoints."""

import contextlib
from collections.abc import AsyncIterator, Awaitable, Callable

import langsmith as ls
from langgraph.graph.state import RunnableConfig
from langgraph.pregel import Pregel

AGENT_TRACING_PROJECT = "open-swe-agent"
REVIEW_TRACING_PROJECT = "open-swe-review"


def traced_graph_factory(
    factory: Callable[[RunnableConfig], Awaitable[Pregel]],
    project_name: str,
) -> Callable[[RunnableConfig], contextlib.AbstractAsyncContextManager[Pregel]]:
    @contextlib.asynccontextmanager
    async def entrypoint(config: RunnableConfig) -> AsyncIterator[Pregel]:
        graph = await factory(config)
        with ls.tracing_context(project_name=project_name):
            yield graph

    return entrypoint
