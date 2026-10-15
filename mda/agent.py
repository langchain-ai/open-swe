"""Open SWE's coding agent and pull request reviewer as one Managed Deep Agent.

Open SWE dispatch names the graph a run plays (``agent`` or ``reviewer``) in its
context. The model loop and its checkpoints run here. The Open SWE backend
holds every credential, the sandbox and the graph itself: this factory takes the
graph's tools, subagents and models from the backend's catalog for the run, and
``BackendRunMiddleware`` hands each phase of the loop to the graph's own
middleware there. The run token in the context authenticates every call.
"""

from collections.abc import Collection, Sequence
from typing import Final, Literal

from langchain.agents.middleware import (
    AgentMiddleware,
    ModelCallLimitMiddleware,
    ModelRetryMiddleware,
)
from langchain_core.tools import BaseTool
from langchain_quickjs import CodeInterpreterMiddleware
from managed_deepagents import (
    DeepAgentDefinition,
    ManagedServerRuntime,
    define_deep_agent,
)
from pydantic import BaseModel, ConfigDict, Field, JsonValue
from rt.backend import Catalog, OpenSweBackend
from rt.middleware import (
    BackendRunMiddleware,
    ReportRepliesMiddleware,
    RuntimeToolsMiddleware,
    ServedToolsMiddleware,
)
from rt.models import RunModels, build_model
from rt.tools import backend_tool

Graph = Literal["agent", "reviewer"]
_DEFAULT_MODEL: Final = "anthropic:claude-opus-5-5"
# Matches the in-process graphs' cap on model calls per run.
_MODEL_CALL_LIMIT: Final = 5_000
# Callable from the reviewer's code mode, so large results can be filtered or written
# to files without passing through the model.
_REVIEWER_PTC_TOOLS: Final[list[str | BaseTool]] = [
    "read_file",
    "write_file",
    "web_search",
    "fetch_url",
    "http_request",
    "list_findings",
    "add_finding",
    "update_finding",
    "publish_review",
    "resolve_finding_thread",
    "reply_to_finding_thread",
]


class OpenSweRunContext(BaseModel):
    """What Open SWE dispatch passes as the run context."""

    model_config = ConfigDict(populate_by_name=True)

    graph: Graph = "agent"
    # The Agent Server keeps only context keys this schema names, and reads the
    # protocol v2 stream marker from the context it mirrors into configurable.
    event_streaming_v2: bool = Field(default=False, alias="__event_streaming_v2")
    # Dunder keys stay out of the trace metadata the context is mirrored into.
    run_token: str | None = Field(default=None, alias="__run_token")
    invocation_id: str | None = None
    # JSON, not RunModels: the gRPC runtime serializes only the context's top level.
    models: dict[str, JsonValue] | None = None


def _backend_reports(backend: OpenSweBackend, served: Collection[str]) -> list[AgentMiddleware]:
    """The backend's transcript and usage see every reply, and every tool call."""
    return [ReportRepliesMiddleware(backend), RuntimeToolsMiddleware(backend, served)]


def _code_mode(graph: Graph, from_code: Sequence[BaseTool]) -> list[AgentMiddleware]:
    """The graph's code mode, with every tool the catalog offers from code callable from it."""
    if graph == "reviewer":
        return [CodeInterpreterMiddleware(ptc=[*_REVIEWER_PTC_TOOLS, *from_code])]
    if not from_code:
        return []
    # As the in-process agent's code mode: state per turn, subagents through `task`.
    return [CodeInterpreterMiddleware(ptc=list(from_code), subagents=False, mode="turn")]


# `mda build` reads the agent's name without running this factory, so its body
# stays assignments and one `return define_deep_agent(...)`.
async def agent(runtime: ManagedServerRuntime) -> DeepAgentDefinition:
    execution = runtime.execution_runtime
    context = OpenSweRunContext.model_validate(
        execution.context if execution is not None and execution.context is not None else {}
    )
    backend = OpenSweBackend(context.run_token)
    # Runs without a context (state reads, assistant reads) never call a model or a tool.
    catalog = await backend.catalog(None) if backend.has_run else Catalog()
    models = catalog.models or (
        RunModels.model_validate(context.models) if context.models is not None else None
    )
    model = (
        build_model(models.model, use_gateway=models.use_gateway)
        if models is not None
        else _DEFAULT_MODEL
    )
    subagent_model = (
        build_model(models.subagent_model, use_gateway=models.use_gateway)
        if models is not None
        else _DEFAULT_MODEL
    )
    tools = [backend_tool(spec, backend) for spec in catalog.tools]
    by_name = {tool.name: tool for tool in tools}
    excluded = frozenset(catalog.excluded)
    # Every schema offered costs each call its context, and OpenAI answers some requests
    # carrying hundreds with nothing; the tools the model seldom needs are called from code.
    in_code = [tool for tool in tools if tool.name in catalog.code_only]
    offered = [tool for tool in tools if tool not in in_code]
    from_code = [*in_code, *(tool for tool in offered if tool.name in catalog.also_from_code)]
    subagent_tools = {
        subagent.name: offered
        if subagent.tools is None
        else [by_name[name] for name in subagent.tools if name in by_name]
        for subagent in catalog.subagents
    }
    return define_deep_agent(
        name="open-swe",
        model=model,
        context_schema=OpenSweRunContext,
        # Named as the in-process graph names them, these replace MDA's own file
        # and shell tools: the backend runs them in the thread's sandbox.
        tools=offered,
        sandbox=None,
        subagents=[
            {
                "name": subagent.name,
                "description": subagent.description,
                "system_prompt": subagent.system_prompt,
                "model": subagent_model,
                "tools": subagent_tools[subagent.name],
                "middleware": [
                    *_backend_reports(backend, by_name.keys()),
                    ServedToolsMiddleware(subagent_tools[subagent.name], excluded),
                    ModelRetryMiddleware(retry_on=(TimeoutError,)),
                ],
            }
            for subagent in catalog.subagents
        ],
        middleware=[
            BackendRunMiddleware(backend, context.invocation_id),
            *_backend_reports(backend, by_name.keys()),
            ServedToolsMiddleware(offered, excluded),
            *_code_mode(context.graph, from_code),
            ModelCallLimitMiddleware(run_limit=_MODEL_CALL_LIMIT, exit_behavior="end"),
            ModelRetryMiddleware(retry_on=(TimeoutError,)),
        ],
    )
