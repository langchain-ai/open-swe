"""Open SWE's graphs as one Managed Deep Agent.

Open SWE dispatch names the graph a run plays (``agent``, ``reviewer`` or
``review-scout``) in its context. The model loop, its checkpoints and every tool call
run here. The Open SWE backend holds every credential and plans the run: the catalog
it works out as it starts the run names the run's operations, subagents, models,
sandbox and the rules of its loop. This runtime's own middleware runs the loop
around each model call as those rules say (``rt.loop``), asking the backend only for
what needs its data or credentials. The file and shell tools are this runtime's own,
in the thread's box or against its files on the backend (``rt.file_tools``). The run
token in the context authenticates every call.

None of that is built into the graph but the run's models. Each graph is built once
per process from what is the same for every run that plays it on the same models
(``GraphInputs``) and shared by every such run, thread and person, and by every read
of a thread's state. A run reads its catalog when it starts, and its tools and
middleware read it per call from the run in progress (``rt.backend.current_run``):
the operations each model call is offered and each tool call runs (``rt.run_tools``),
the model a call is routed to, the box (``rt.managed_sandbox``), the skills and the
loop.

The same agent runs as the async ``general-purpose`` subagent, the graph
``open-swe--general-purpose``, for the task workers a coordinator launches with
``start_async_task``. A worker is an Open SWE thread of its own: it starts with a
worker grant, adopts its own run token, and runs this agent's middleware as it.
"""

from dataclasses import dataclass
from functools import cache
from pathlib import Path
from typing import TYPE_CHECKING, Final, Self, cast

from deepagents.middleware.filesystem import FilesystemMiddleware
from deepagents.middleware.subagents import SubAgent
from langchain.agents.middleware import AgentMiddleware
from langchain_core.language_models import BaseChatModel
from managed_deepagents import (
    DeepAgentDefinition,
    ManagedServerRuntime,
    define_deep_agent,
    shared_factory,
)
from managed_deepagents.sandbox import SandboxDefinition
from pydantic import BaseModel, ConfigDict
from rt.backend import Graph, OpenSweBackend, RunContext
from rt.browser import ReportedBrowserMiddleware
from rt.factory_timing import FactoryTiming
from rt.file_tools import FileToolsMiddleware
from rt.managed_sandbox import (
    EXECUTE_TIMEOUT_SECONDS,
    ReplaceSandboxMiddleware,
    box,
    opened_sandbox,
)
from rt.middleware import (
    AsyncWorkerLaunchMiddleware,
    BackendRunMiddleware,
    DescribedToolsMiddleware,
    IncidentEvidenceMiddleware,
    ReportRepliesMiddleware,
    RuntimeToolsMiddleware,
)
from rt.model_calls import model_call_guards
from rt.models import build_model
from rt.remote_files import FILE_TOOLS, RemoteFiles, RunArtifacts
from rt.run_prompt import RunPromptMiddleware, ThreadPromptMiddleware
from rt.run_tools import (
    BackendTools,
    RunCodeInterpreter,
    RunModelCallLimit,
    RunSubagentMiddleware,
    RunToolsets,
    RunToolsMiddleware,
    subagent_description,
    subagent_prompt,
)
from rt.skills import RunSkillsMiddleware, SkillFiles
from rt.task_retry import task_retry
from rt.tools import run_id
from runtime_api.catalog import Catalog, ModelSpec

if TYPE_CHECKING:
    from managed_deepagents.define_deep_agent import ManagedAsyncSubAgent

# What a graph built for no run's models, such as a read's, is declared with.
_DEFAULT_MODEL: Final = "anthropic:claude-opus-5-5"
# The in-process graphs' cap on model calls per run, for a catalog that sets none.
_MODEL_CALL_LIMIT: Final = 5_000
# A worker's system prompt starts as this agent's; each run's follows it.
_INSTRUCTIONS: Final = Path(__file__).with_name("instructions.md").read_text()
_WORKER_DESCRIPTION: Final = (
    "An Open SWE task worker: a full agent of its own, sharing this thread's sandbox, "
    "that works on one assignment in the background and reports back as a task message."
)
# The subagent each graph delegates to with ``task``; its description, prompt and
# tools are each run's.
_SUBAGENTS: Final[dict[Graph, str]] = {
    "agent": "general-purpose",
    "reviewer": "reviewer",
    "review-scout": "general-purpose",
}
# Callable from the reviewer's code mode, so large results can be filtered or written
# to files without passing through the model.
_REVIEWER_PTC_TOOLS: Final = (
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
)


class GraphInputs(BaseModel):
    """What a graph is built from: the graph a run plays, its models, and where its
    workers report.

    They are the same for every run of the deployment that plays the graph on the same
    models, so MDA builds each graph once per process and shares it. Every graph has
    the same middleware, so a thread's state reads alike through any of them.
    """

    model_config = ConfigDict(frozen=True)

    graph: Graph = "agent"
    # Where the Agent Server reports a launched worker's run ending.
    worker_webhook: str | None = None
    # The models the agent and its subagent are built with. What deepagents does with
    # the graph's own model, such as summarizing a long conversation, then uses the
    # run's model and credentials; routing may still send a call to another model.
    model: ModelSpec | None = None
    subagent_model: ModelSpec | None = None
    use_gateway: bool = False

    @classmethod
    def of(cls, graph: Graph, catalog: Catalog) -> Self:
        models = catalog.models
        if models is None:
            return cls(graph=graph, worker_webhook=catalog.worker_webhook)
        return cls(
            graph=graph,
            worker_webhook=catalog.worker_webhook,
            model=models.model,
            subagent_model=models.subagent_model,
            use_gateway=models.use_gateway,
        )

    def agent_model(self) -> BaseChatModel | str:
        return self._built(self.model)

    def sync_subagent_model(self) -> BaseChatModel | str:
        return self._built(self.subagent_model)

    def _built(self, spec: ModelSpec | None) -> BaseChatModel | str:
        return _DEFAULT_MODEL if spec is None else build_model(spec, use_gateway=self.use_gateway)


async def _inputs(runtime: ManagedServerRuntime) -> GraphInputs:
    execution = runtime.execution_runtime
    if execution is None:
        # A read: through the agent's graph, which has every graph's shape.
        return GraphInputs()
    context = RunContext.model_validate(execution.context or {})
    return GraphInputs.of(context.graph, context.run().planned)


@dataclass(frozen=True)
class _Parts:
    """A graph's subagents and middleware, built once and shared by every run."""

    subagents: list[SubAgent]
    middleware: list[AgentMiddleware]
    worker_middleware: list[AgentMiddleware]
    sandbox: SandboxDefinition

    def sync_subagents(self, model: BaseChatModel | str) -> list[SubAgent]:
        """The graph's sync subagents, built with ``model``."""
        return [{**subagent, "model": model} for subagent in self.subagents]


def _code_mode(
    graph: Graph, toolsets: RunToolsets, files: FileToolsMiddleware, backend_tools: BackendTools
) -> RunCodeInterpreter:
    """The graph's code mode, with every tool the run offers from code callable from it:
    those its catalog lists, and those ``search_tools`` finds on the backend."""
    if graph == "reviewer":
        return RunCodeInterpreter(
            toolsets,
            named=[
                *(name for name in _REVIEWER_PTC_TOOLS if name not in FILE_TOOLS),
                *files.code_tools(_REVIEWER_PTC_TOOLS),
            ],
        )
    # As the in-process agent's code mode: state per turn, subagents through `task`,
    # for a run that offers tools from code.
    return RunCodeInterpreter(
        toolsets,
        # Also for a run whose catalog leaves tools out: code mode calls those.
        active=lambda tools: bool(tools.from_code) or tools.catalog.search_tools,
        subagents=False,
        mode="turn",
        backend_tools=backend_tools,
    )


@cache
def _parts(graph: Graph) -> _Parts:
    timing = FactoryTiming()
    # Called as the run in progress: the graph serves every run.
    backend = OpenSweBackend()
    remote = RemoteFiles(backend, run_id)
    # The run's skills, listed for the agent and its subagents and read from here.
    skill_files = SkillFiles(backend, run_id)
    files = FileToolsMiddleware(
        backend,
        EXECUTE_TIMEOUT_SECONDS,
        remote_files=remote,
        skills={tool.name: tool for tool in FilesystemMiddleware(backend=skill_files).tools},
    )
    toolsets = RunToolsets(backend, files)
    # What the run calls that its catalog leaves out, as the backend names it.
    backend_tools = BackendTools(backend)
    skills = RunSkillsMiddleware(backend, skill_files)
    replace_sandbox = ReplaceSandboxMiddleware(backend)
    # The backend's transcript and usage see every reply and every tool call.
    reports: list[AgentMiddleware] = [
        ReportRepliesMiddleware(backend),
        RuntimeToolsMiddleware(backend, FILE_TOOLS),
        IncidentEvidenceMiddleware(backend),
    ]
    subagent = _SUBAGENTS[graph]
    subagents: list[SubAgent] = [
        {
            "name": subagent,
            "description": subagent_description(subagent),
            "system_prompt": subagent_prompt(subagent),
            "tools": [],
            "middleware": cast(
                list[AgentMiddleware],
                [
                    *reports,
                    skills,
                    files,
                    replace_sandbox,
                    RunToolsMiddleware(toolsets, subagent=subagent),
                    RunSubagentMiddleware(toolsets, subagent),
                    *model_call_guards(subagent=True),
                ],
            ),
        }
    ]
    # This agent's own, as the agent a worker is; deepagents passes no middleware down.
    # Middleware types are invariant in the state they extend; the agent merges them all.
    middleware = cast(
        list[AgentMiddleware],
        [
            BackendRunMiddleware(backend, opened_sandbox, read_ahead=[skill_files.list_ahead]),
            replace_sandbox,
            *reports,
            files,
            task_retry(),
            RunToolsMiddleware(toolsets, backend_tools=backend_tools),
            _code_mode(graph, toolsets, files, backend_tools),
            DescribedToolsMiddleware(backend),
            # The coding agent's browser, a LangSmith browser per thread the dashboard
            # shows; the deployment's workspace needs the LangSmith browser beta.
            ReportedBrowserMiddleware(backend, enabled=graph == "agent"),
            # The system prompt in the order of how many runs share each part, for the
            # provider's prompt cache (``rt.run_prompt``).
            RunPromptMiddleware(),
            skills,
            ThreadPromptMiddleware(),
            RunModelCallLimit(backend, _MODEL_CALL_LIMIT),
            *model_call_guards(),
        ],
    )
    timing.lap("middleware")
    timing.log(graph_name=graph, subagents=len(subagents) + 1, middleware=len(middleware))
    return _Parts(
        subagents=subagents,
        middleware=middleware,
        worker_middleware=[*middleware, AsyncWorkerLaunchMiddleware(backend)],
        # A run without MDA's box keeps the graph's own files where its file tools are.
        sandbox=box(
            RunArtifacts(backend, run_id, lambda: backend.run.catalog.files.artifacts_root)
        ),
    )


# MDA builds this once per distinct inputs, from them alone, and shares its graph.
# `mda build` reads the agent's name without running it, so its body stays
# assignments and one `return define_deep_agent(...)`.
@shared_factory(_inputs)
def agent(inputs: GraphInputs) -> DeepAgentDefinition:
    parts = _parts(inputs.graph)
    sync_subagents: list[SubAgent] = parts.sync_subagents(inputs.sync_subagent_model())
    worker: ManagedAsyncSubAgent = {
        # MDA's build reads `name` and `async` as literals after any spread.
        **({"webhook": inputs.worker_webhook} if inputs.worker_webhook else {}),
        "name": "general-purpose",
        "description": _WORKER_DESCRIPTION,
        "system_prompt": _INSTRUCTIONS,
        "middleware": parts.middleware,
        "async": True,
        # The backend settles a worker's runs from their webhooks and wakes the coordinator
        # itself, so deepagents must not wake it as well.
        "notify_parent": False,
    }
    return define_deep_agent(
        name="open-swe",
        replicate_traces=False,
        model=inputs.agent_model(),
        context_schema=RunContext,
        # The thread's box, for the file and shell tools run in it and for MDA's own use
        # of it, such as the browser's screenshots; the backend's operations work in the
        # same box. Which box, and whether a run has one, is the run's catalog's: a
        # worker's catalog is its coordinator's, so it shares the coordinator's box.
        sandbox=parts.sandbox,
        # `mda build` reads `worker`'s literal name to build its graph.
        subagents=[*sync_subagents, worker],
        middleware=parts.worker_middleware,
    )
