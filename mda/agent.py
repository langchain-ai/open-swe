"""Open SWE's graphs as one Managed Deep Agent.

Open SWE dispatch names the graph a run plays (``agent``, ``reviewer`` or
``review-scout``) in its context. The model loop and its checkpoints run here. The Open SWE backend
holds every credential and the graph itself: this factory takes the graph's tools,
subagents and models from the backend's catalog for the run, and
``BackendRunMiddleware`` hands each phase of the loop to the graph's own
middleware there. Where MDA owns the thread's box, the file and shell tools run in
it from here (``SandboxToolsMiddleware``). The run token in the context
authenticates every call.

The same agent runs as the async ``general-purpose`` subagent, the graph
``open-swe--general-purpose``, for the task workers a coordinator launches with
``start_async_task``. A worker is an Open SWE thread of its own: it starts with a
worker grant, adopts its own run token, and runs this agent's middleware as it.
"""

from collections.abc import Collection, Mapping, Sequence
from pathlib import Path
from typing import TYPE_CHECKING, Final, Literal, cast

from deepagents.middleware.subagents import SubAgent
from langchain.agents.middleware import (
    AgentMiddleware,
    ModelCallLimitMiddleware,
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
from rt.browser import ReportedBrowserMiddleware
from rt.managed_sandbox import (
    EXECUTE_TIMEOUT_SECONDS,
    ReplaceSandboxMiddleware,
    definition,
    opened_sandbox,
)
from rt.middleware import (
    AsyncWorkerLaunchMiddleware,
    BackendRunMiddleware,
    DescribedToolsMiddleware,
    ReportRepliesMiddleware,
    RuntimeToolsMiddleware,
    ServedToolsMiddleware,
)
from rt.model_calls import model_call_guards
from rt.models import RunModels, build_model
from rt.sandbox_tools import SandboxToolsMiddleware
from rt.tools import backend_tool

if TYPE_CHECKING:
    from managed_deepagents.define_deep_agent import ManagedAsyncSubAgent

Graph = Literal["agent", "reviewer", "review-scout"]
_DEFAULT_MODEL: Final = "anthropic:claude-opus-5-5"
# Matches the in-process graphs' cap on model calls per run.
_MODEL_CALL_LIMIT: Final = 5_000
# A worker's system prompt starts as this agent's; the backend's prepends the rest.
_INSTRUCTIONS: Final = Path(__file__).with_name("instructions.md").read_text()
_WORKER_DESCRIPTION: Final = (
    "An Open SWE task worker: a full agent of its own, sharing this thread's sandbox, "
    "that works on one assignment in the background and reports back as a task message."
)
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
    # A launched task worker's first run: traded for its own run token before anything else.
    worker_grant: str | None = Field(default=None, alias="__worker_grant")
    invocation_id: str | None = None
    # JSON, not RunModels: the gRPC runtime serializes only the context's top level.
    models: dict[str, JsonValue] | None = None


def _backend_reports(backend: OpenSweBackend, served: Collection[str]) -> list[AgentMiddleware]:
    """The backend's transcript and usage see every reply, and every tool call."""
    return [ReportRepliesMiddleware(backend), RuntimeToolsMiddleware(backend, served)]


def _code_mode(
    graph: Graph, from_code: Sequence[BaseTool], described: Mapping[str, str]
) -> list[AgentMiddleware | CodeInterpreterMiddleware]:
    """The graph's code mode, with every tool the catalog offers from code callable from it."""
    if graph == "reviewer":
        interpreter = CodeInterpreterMiddleware(ptc=[*_REVIEWER_PTC_TOOLS, *from_code])
    elif from_code:
        # As the in-process agent's code mode: state per turn, subagents through `task`.
        interpreter = CodeInterpreterMiddleware(ptc=list(from_code), subagents=False, mode="turn")
    else:
        return []
    return [interpreter, *([DescribedToolsMiddleware(described)] if described else [])]


def _browser(graph: Graph, backend: OpenSweBackend) -> list[AgentMiddleware]:
    """The coding agent's browser, a LangSmith browser per thread the dashboard shows.

    The deployment's workspace needs the LangSmith browser beta.
    """
    return [ReportedBrowserMiddleware(backend)] if graph == "agent" else []


# `mda build` reads the agent's name without running this factory, so its body
# stays assignments and one `return define_deep_agent(...)`.
async def agent(runtime: ManagedServerRuntime) -> DeepAgentDefinition:
    execution = runtime.execution_runtime
    context = OpenSweRunContext.model_validate(
        execution.context if execution is not None and execution.context is not None else {}
    )
    backend = OpenSweBackend(context.run_token, worker_grant=context.worker_grant)
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
    # MDA's own file and shell tools serve the ones the thread's box runs here. Code
    # mode keeps the backend's: its calls skip tool-call middleware, the checks included.
    in_box = frozenset(catalog.sandbox.tools if catalog.sandbox is not None else ())
    registered = [tool for tool in tools if tool.name not in in_box]
    by_name = {tool.name: tool for tool in registered}
    excluded = frozenset(catalog.excluded)
    # Every schema offered costs each call its context, and OpenAI answers some requests
    # carrying hundreds with nothing; the tools the model seldom needs are called from code.
    code_only = frozenset(catalog.code_only)
    in_code = [tool for tool in tools if tool.name in code_only]
    offered = [tool for tool in registered if tool.name not in code_only]
    from_code = [
        *in_code,
        *(tool for tool in tools if tool.name in catalog.also_from_code and tool not in in_code),
    ]
    subagent_tools = {
        subagent.name: offered
        if subagent.tools is None
        else [by_name[name] for name in subagent.tools if name in by_name]
        for subagent in catalog.subagents
    }
    replace_sandbox = (
        [ReplaceSandboxMiddleware(backend, catalog.sandbox)] if catalog.sandbox is not None else []
    )
    box_tools = (
        [
            SandboxToolsMiddleware(
                backend,
                catalog.sandbox,
                EXECUTE_TIMEOUT_SECONDS,
                check_reads=context.graph == "agent",
            )
        ]
        if catalog.sandbox is not None and in_box
        else []
    )
    reported_elsewhere = {*by_name, *in_box}
    sync_subagents: list[SubAgent] = [
        {
            "name": subagent.name,
            "description": subagent.description,
            "system_prompt": subagent.system_prompt,
            "model": subagent_model,
            "tools": subagent_tools[subagent.name],
            "middleware": [
                *_backend_reports(backend, reported_elsewhere),
                *box_tools,
                *replace_sandbox,
                ServedToolsMiddleware(subagent_tools[subagent.name], excluded, backend),
                *model_call_guards(subagent=True),
            ],
        }
        for subagent in catalog.subagents
    ]
    # This agent's own, as the agent a worker is; deepagents passes no middleware down.
    # Middleware types are invariant in the state they extend; the agent merges them all.
    agent_middleware = cast(
        list[AgentMiddleware],
        [
            BackendRunMiddleware(
                backend,
                context.invocation_id,
                opened_sandbox if catalog.sandbox is not None else None,
                trace_metadata=catalog.trace_metadata,
            ),
            *replace_sandbox,
            *_backend_reports(backend, reported_elsewhere),
            *box_tools,
            ServedToolsMiddleware(offered, excluded | code_only, backend),
            *_code_mode(context.graph, from_code, catalog.described),
            *_browser(context.graph, backend),
            ModelCallLimitMiddleware(run_limit=_MODEL_CALL_LIMIT, exit_behavior="end"),
            *model_call_guards(),
        ],
    )
    worker: ManagedAsyncSubAgent = {
        # MDA's build reads `name` and `async` as literals after any spread.
        **({"webhook": catalog.worker_webhook} if catalog.worker_webhook else {}),
        "name": "general-purpose",
        "description": _WORKER_DESCRIPTION,
        "system_prompt": _INSTRUCTIONS,
        "middleware": agent_middleware,
        "async": True,
        # The backend settles a worker's runs from their webhooks and wakes the coordinator
        # itself, so deepagents must not wake it as well.
        "notify_parent": False,
    }
    return define_deep_agent(
        name="open-swe",
        replicate_traces=False,
        model=model,
        context_schema=OpenSweRunContext,
        # Named as the in-process graph names them, these replace MDA's own file and
        # shell tools where the backend runs them. Code-only tools stay callable by
        # name, as in-process, though no request offers them.
        tools=registered,
        # The thread's box, for the file and shell tools run in it from here and for
        # MDA's own use of it, such as the browser's screenshots; the backend's tools
        # run in the same box. A worker's catalog is its coordinator's, so the worker
        # shares the coordinator's box.
        sandbox=(
            definition(catalog.sandbox.create, catalog.sandbox.thread_id)
            if catalog.sandbox is not None
            else None
        ),
        # `mda build` reads `worker`'s literal name to build its graph.
        subagents=[*sync_subagents, worker],
        middleware=[*agent_middleware, AsyncWorkerLaunchMiddleware(backend)],
    )
