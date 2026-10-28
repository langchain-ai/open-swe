"""One Open SWE graph, played by a run on Managed Deep Agents.

MDA keeps the model loop, its checkpoints, every tool call, and the middleware
around them. The backend answers the calls MDA makes, as the typed API both sides
import (``runtime_api``):

- the catalog names the run's operations (each the work of one of its tools), its
  subagents, models and sandbox, the checks around its file and shell tools, and
  the rules of its middleware around each model call. It is worked out once as the
  run starts and rides in the run's context (``RemoteGraph.start_run``); the
  settings later calls read are signed into the run's token (``RunSettings``)
- ``prepare`` readies the run from its plan: the thread's sandbox, checkout and
  prompt, the transcript turn, skills, reply bookkeeping. It is the one call that
  plans the run again
- every other call is a ``TokenRun``, answered from the token and the request with
  the pieces the graph's ``RunParts`` build for it: the inbox and skills, the
  named calls MDA's middleware makes around a model call (``route_model``,
  ``model_choice``, ``task_context``, ``refresh_github_proxy``,
  ``reply_on_behalf``) and at the run's end (``run_ended``,
  ``step_limit_reached``, ``settle_review_check``, ``finish_scout_plan``), the
  reports of each reply and tool call, which arrive with those notices in ordered
  batches (``reports``) and are recorded once per key, the work of the run's tools (``operation``,
  ``check_shell``, the ``files`` calls), and the box MDA keeps
- a run still served from its plan (``RunParts.legacy``: a requested offload, an
  incident; or a token from before operations were granted) has its phases (``before_model``, ``after_model``, ``after_agent``) and
  tool calls answered by ``RunSession``, as is ``record_evidence``

None of the run's tool calls is a tool call here: no tools node, no tool-call
middleware. A call made from inside a subagent names the namespace of its ``task``
calls, so its tool calls and replies land under the subagent.
"""

import asyncio
import base64
import logging
import time
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import asdict, dataclass
from functools import partial
from typing import Final, Protocol, cast, runtime_checkable
from uuid import UUID, uuid4

from deepagents.backends.composite import CompositeBackend
from deepagents.backends.protocol import BackendProtocol, SandboxBackendProtocol
from langchain.agents.middleware import ModelCallLimitMiddleware
from langchain.agents.middleware.types import (
    AgentMiddleware,
    AgentState,
    ModelRequest,
    ModelResponse,
)
from langchain_core.language_models.fake_chat_models import FakeListChatModel
from langchain_core.messages import (
    AIMessage,
    BaseMessage,
    ToolCall,
    ToolMessage,
    messages_from_dict,
)
from langchain_core.runnables import RunnableConfig
from langchain_core.tools import BaseTool
from langgraph.errors import NodeCancelledError
from langgraph.runtime import Runtime
from langgraph.types import Command
from pydantic import BaseModel, JsonValue, TypeAdapter, ValidationError
from runtime_api import files as file_api
from runtime_api.catalog import (
    SEARCH_TOOLS,
    Catalog,
    FilesSpec,
    ModelSpec,
    OperationSpec,
    RunModels,
    SandboxSpec,
    SubagentSpec,
)
from runtime_api.endpoint import Handlers, UnknownEndpointError
from runtime_api.files import (
    DeleteRequest,
    DownloadRequest,
    EditRequest,
    ExecuteReply,
    ExecuteRequest,
    FileReply,
    GlobReply,
    GlobRequest,
    GrepReply,
    GrepRequest,
    LsReply,
    PathRequest,
    ReadReply,
    ReadRequest,
    TransferReplies,
    TransferReply,
    UploadRequest,
    WriteRequest,
)
from runtime_api.loop import (
    AFTER_AGENT,
    AFTER_MODEL,
    BEFORE_MODEL,
    BROWSER,
    FINISH_SCOUT_PLAN,
    INBOX,
    INBOX_RECEIVED,
    MODEL_CHOICE,
    MODEL_RESULT,
    PREPARE,
    REFRESH_GITHUB_PROXY,
    REPLY_ON_BEHALF,
    ROUTE_MODEL,
    RUN_ENDED,
    SANDBOX_REPLACED,
    SANDBOX_UNREACHABLE,
    SETTLE_REVIEW_CHECK,
    STEP_LIMIT_REACHED,
    TASK_CONTEXT,
    TOOL_RESULT,
    TOOL_STARTED,
    AfterAgent,
    BeforeModel,
    BrowserReport,
    ChosenModel,
    Done,
    GitHubProxyRefreshed,
    Inbox,
    InboxItems,
    InboxRead,
    InboxReceipt,
    ModelResult,
    PhaseResult,
    Prepare,
    ReplyOnBehalf,
    Report,
    Route,
    Routed,
    RunCall,
    RunEnded,
    SandboxGone,
    ScoutPlanEnd,
    TaskContext,
    ToolCallReport,
    ToolResultReply,
    ToolResultReport,
)
from runtime_api.operations import (
    CHECK_SHELL,
    OPERATION,
    RECORD_EVIDENCE,
    EvidenceReply,
    OperationResult,
    RunOperation,
    RunRequest,
    ShellCheck,
    ShellVerdict,
    ToolEvidence,
)
from runtime_api.reports import REPORTED, REPORTS, Reports, ReportsTaken
from runtime_api.skills import SKILLS, RunSkills

from openswe.agent_api.caller_trace import traced_under
from openswe.agent_api.failures import OperationFailure
from openswe.agent_api.git_metadata import (
    BASELINE,
    OBSERVED_TOOLS,
    TurnGitMetadata,
    collect_pr_targets,
)
from openswe.agent_api.loop import (
    Phase,
    RunLoop,
    StateRestorer,
    as_list,
    as_messages,
    call_state,
    chosen_model,
    decode_state,
    dump_messages,
    json_state,
    model_spec,
    run_hooks,
)
from openswe.agent_api.operations import Injections, invoke
from openswe.agent_api.report_receipts import Recorded, Recording, ReportReceipts
from openswe.agent_api.run_parts import RunParts
from openswe.agent_api.runs import in_graph_context, run_config
from openswe.agent_api.shell_guards import ShellGuards
from openswe.agent_api.tokens import RemoteRun, RunSettings
from openswe.dispatch import COMPLETION_WEBHOOK_URL
from openswe.github.sandbox_access import READ_ONLY_REVIEW_PERMISSIONS
from openswe.incidents.runtime import IncidentMiddleware
from openswe.middleware.client_tools import ClientToolsMiddleware
from openswe.middleware.model_selection import classify_route
from openswe.middleware.notify_step_limit import notify_step_limit
from openswe.middleware.prepare_run import BasePrepareRunMiddleware
from openswe.middleware.record_run_usage import RecordRunUsageMiddleware
from openswe.middleware.sandbox_circuit_breaker import post_sandbox_unreachable_notification
from openswe.middleware.settle_review_check import settle_review_check
from openswe.middleware.task_coordination import TaskCoordinationMiddleware
from openswe.middleware.transcript import TranscriptMiddleware, within_namespace
from openswe.prompts import common_prompt, prompt
from openswe.review_scout.graph import finish_scout_plan
from openswe.run_config import RunConfig
from openswe.sandboxes.browsers import ThreadBrowser
from openswe.sandboxes.lifecycle import (
    bind_mda_sandbox,
    reconnect_replaced_sandbox,
    refresh_thread_github_proxy,
)
from openswe.sandboxes.managed import ManagedSandbox
from openswe.sandboxes.state import SandboxUnreachableError
from openswe.sandboxes.tool_runtime import (
    EXCLUDED_TOOLS,
    ModelChoice,
    SurfaceModels,
    ToolSurface,
    tool_parameters,
)
from openswe.server import (
    DEEP_AGENT_TOOL_NAMES,
    code_name,
    code_only,
    direct_and_from_code,
)
from openswe.slack.tools.reply import send_slack_reply
from openswe.threads.inbox import ThreadInbox

logger = logging.getLogger(__name__)

# MDA's own deep agent runs subagents and code.
_SYNC_SUBAGENT_TOOL: Final = "task"
_MDA_TOOLS: Final = frozenset({_SYNC_SUBAGENT_TOOL, "eval"})
# deepagents' async subagent tools. Open SWE launches a task worker with the first,
# wherever the thread may delegate; its task tools track the worker from then on.
_LAUNCH_WORKER_TOOL: Final = "start_async_task"
_ASYNC_TASK_TOOLS: Final = frozenset(
    {
        _LAUNCH_WORKER_TOOL,
        "check_async_task",
        "update_async_task",
        "cancel_async_task",
        "list_async_tasks",
    }
)
# Tools the model labels each call of, for the dashboard to show in place of a bare name.
_DESCRIBED_TOOLS: Final = ("eval",)
_MODEL_OBSERVERS: Final = (TranscriptMiddleware, RecordRunUsageMiddleware)
# The file and shell tools MDA's own filesystem middleware gives the run.
_FILE_TOOLS: Final = frozenset(DEEP_AGENT_TOOL_NAMES) - _MDA_TOOLS
_SESSION_TTL_SECONDS: Final = 6 * 60 * 60
_SESSION_LIMIT: Final = 512

_json = TypeAdapter(JsonValue)
_json_object = TypeAdapter(dict[str, JsonValue])
_json_list = TypeAdapter(list[JsonValue])
_json_dicts = TypeAdapter(list[dict[str, JsonValue]])

type PhaseWork[R: PhaseResult] = Callable[[dict[str, object], Runtime], Awaitable[R]]
RunPlanner = Callable[[RunnableConfig, ToolSurface], Awaitable[None]]


def _model_spec(choice: ModelChoice) -> ModelSpec:
    return ModelSpec(
        model_id=choice.model_id, kwargs=_json_object.validate_python(dict(choice.kwargs))
    )


class _CarriedEvents(BaseModel):
    event_match_ids: list[UUID] = []


def _carried_events(config: RunnableConfig) -> list[UUID]:
    """The event matches and task messages the run was started with as its input."""
    try:
        return _CarriedEvents.model_validate(config.get("metadata") or {}).event_match_ids
    except ValidationError:
        logger.warning("Ignoring a run's malformed event ids", exc_info=True)
        return []


@runtime_checkable
class CarriedAcrossProcesses(Protocol):
    """Middleware whose per-run state outlives the process that started the run.

    An MDA run's hooks can reach any backend replica: each replica rejoins the
    run before its first hook, and writes what a hook queued before answering it.
    """

    async def aresume_run(self, state: AgentState, runtime: Runtime) -> None: ...

    async def aflush_run(self, runtime: Runtime) -> None: ...


@runtime_checkable
class RecordsRemoteTools(Protocol):
    """Middleware that records the tool calls MDA runs itself, which never reach the tools node."""

    async def atool_started(self, tool_call: ToolCall, message_id: str | None) -> None: ...

    async def atool_finished(self, result: ToolMessage) -> None: ...


class RunSession:
    """One run as its graph factory planned it: its tools, middleware and models."""

    def __init__(
        self,
        surface: ToolSurface,
        config: RunnableConfig,
        restore: StateRestorer | None,
        *,
        started_here: bool,
        assistant_id: str | None = None,
        run: RemoteRun | None = None,
    ) -> None:
        self.surface = surface
        self.config = config
        # The run as its token names it, whose catalog grants it its operations.
        self.run = run
        # The graph the run plays, which decides what a replaced box may reach.
        self.assistant_id = assistant_id
        self._restore = restore
        self._joined = self._prepared = started_here
        self._shell_guards = ShellGuards(surface.shell_guards)
        self.expires_at = time.monotonic() + _SESSION_TTL_SECONDS

    def _carried(self) -> list[CarriedAcrossProcesses]:
        return [m for m in self.surface.middleware if isinstance(m, CarriedAcrossProcesses)]

    async def join(self, state: Mapping[str, object], runtime: Runtime) -> None:
        """Pick up the run where another process left it, once per session."""
        if self._joined:
            return
        self._joined = self._prepared = True
        for middleware in self._carried():
            await middleware.aresume_run(cast(AgentState, dict(state)), runtime)

    async def rejoin_preparation(self, runtime: Runtime) -> None:
        """Rebuild what run preparation keeps in memory, such as tokens, once per session.

        API calls carry no run state, so only preparation, which needs none, rejoins
        for them; the transcript rejoins with the next phase.
        """
        if self._prepared:
            return
        self._prepared = True
        for middleware in self.surface.middleware:
            if isinstance(middleware, BasePrepareRunMiddleware):
                await middleware.aresume_run(cast(AgentState, {}), runtime)

    async def flush(self, runtime: Runtime) -> None:
        for middleware in self._carried():
            await middleware.aflush_run(runtime)

    @property
    def models(self) -> SurfaceModels:
        if self.surface.models is None:
            raise RuntimeError("The graph factory picked no models")
        return self.surface.models

    def state(self, call: RunCall) -> dict[str, object]:
        return call_state(call, self._restore)

    def catalog(self) -> Catalog:
        operations = {
            name: tool for name, tool in self.surface.tools.items() if name not in _MDA_TOOLS
        }
        # Code mode finds the run's tools with the runtime's own search.
        searched = {SEARCH_TOOLS} if self.surface.code_mode else set()
        offered = operations.keys() | _FILE_TOOLS | searched
        excluded = ((self.surface.excluded | EXCLUDED_TOOLS) - _MDA_TOOLS) | (
            self._unoffered_delegation_tools()
        )
        # Code mode calls tools past the model's list, so what the graph excludes stays out.
        from_code = offered - excluded if self.surface.code_mode else frozenset()
        return Catalog(
            operations=[
                OperationSpec(
                    name=described.name,
                    description=described.description,
                    parameters=described.parameters,
                    reads=(injections := Injections.of(operations[described.name])).reads,
                    reads_all=injections.reads_all,
                    code_name=code_name(described.name),
                )
                for described in self.surface.catalog()
                if described.name in operations
            ],
            subagents=[
                SubagentSpec(
                    name=subagent.name,
                    description=subagent.description,
                    system_prompt=common_prompt(subagent.system_prompt),
                    tools=None
                    if subagent.tools is None
                    else [name for name in subagent.tools if name in offered],
                )
                for subagent in (() if self._delegates else self.surface.subagents)
            ],
            models=self.start_models(),
            # Hidden from the model, as the in-process graph hides them.
            excluded=sorted(excluded),
            # Called from MDA's code mode, never offered to the model as schemas.
            code_only=sorted(
                name
                for name in operations.keys() - excluded
                if code_only(name)
                and (self.surface.code_mode or name in self.surface.integration_names)
            ),
            # Offered to the model, and callable from MDA's code mode too.
            also_from_code=sorted(name for name in from_code if direct_and_from_code(name)),
            described={name: prompt("tools/display-description") for name in _DESCRIBED_TOOLS},
            files=FilesSpec(
                backend_paths=list(self.surface.backend_paths),
                artifacts_root=self.surface.artifacts_root,
                guards=["shell_commands"] if self._shell_guards.active else [],
                checks=list(self.surface.file_checks),
            ),
            incident_evidence=self._incident is not None,
            worker_webhook=COMPLETION_WEBHOOK_URL if self._delegates else None,
            skill_sources=list(self.surface.skill_sources),
            inbox=self.surface.inbox,
            loop=None
            if self.surface.loop is None
            else self.surface.loop.model_copy(update={"deployment_prompt": common_prompt()}),
            search_tools=self.surface.code_mode,
            search_tools_description=prompt(f"tools/{SEARCH_TOOLS}")
            if self.surface.code_mode
            else None,
            model_call_limit=next(
                (
                    m.run_limit
                    for m in self.surface.middleware
                    if isinstance(m, ModelCallLimitMiddleware)
                ),
                None,
            ),
        )

    async def plan(self) -> Catalog:
        """The catalog, with who the run acts for, the box MDA creates for it, and the grant
        of each operation to the run this session serves."""
        sandbox = await self.sandbox()
        catalog = self.catalog()
        run = self.run
        return catalog.model_copy(
            update={
                "trace_metadata": TurnGitMetadata(self.config).actor,
                "sandbox": None
                if sandbox is None
                else SandboxSpec.model_validate(sandbox.model_dump(mode="json")),
                "operations": catalog.operations
                if run is None
                else [
                    operation.model_copy(update={"grant": run.grant(operation.name)})
                    for operation in catalog.operations
                ],
            }
        )

    def settings(self) -> RunSettings:
        """What planning decided for the run, which its later calls read from its token."""
        cfg = RunConfig.from_config(self.config)
        return RunSettings(
            source=cfg.source,
            draft_prs=cfg.draft_prs,
            model_id=cfg.resolved_agent_model_id,
            effort=cfg.resolved_agent_effort,
        )

    async def sandbox(self) -> ManagedSandbox | None:
        """The thread's box as MDA creates it, when MDA owns it."""
        cfg = RunConfig.from_config(self.config)
        if cfg.thread_id is None:
            return None
        return await ManagedSandbox.for_thread(cfg.thread_id, cfg.workspace_slug)

    async def bind_sandbox(self, request: Prepare) -> None:
        """Bind the thread to the box MDA opened before preparing, if MDA owns it.

        A thread with no box to work in ends the run once the person knows why.
        """
        cfg = RunConfig.from_config(self.config)
        if cfg.thread_id is None or await self.sandbox() is None:
            return
        try:
            await bind_mda_sandbox(
                cfg.thread_id,
                request.sandbox_id,
                open_error=request.sandbox_error,
                workspace_slug=cfg.workspace_slug,
            )
        except SandboxUnreachableError as exc:
            await post_sandbox_unreachable_notification(self.config, reason=exc.reason)
            raise

    async def sandbox_replaced(self, _request: RunRequest) -> Done:
        """Connect again to the thread's box, which MDA replaced with a fresh one."""
        await reconnect_sandbox(self.config, self.assistant_id)
        return Done()

    @property
    def _delegates(self) -> bool:
        return any(
            isinstance(middleware, TaskCoordinationMiddleware) and middleware.can_delegate
            for middleware in self.surface.middleware
        )

    def _unoffered_delegation_tools(self) -> frozenset[str]:
        if self._delegates:
            # A coordinator delegates only through its workers: no synchronous subagents,
            # not even the general-purpose one deepagents adds to any agent without them.
            return (_ASYNC_TASK_TOOLS - {_LAUNCH_WORKER_TOOL}) | {_SYNC_SUBAGENT_TOOL}
        return _ASYNC_TASK_TOOLS

    def start_models(self) -> RunModels:
        def spec(choice: ModelChoice) -> ModelSpec:
            return model_spec(choice.model_id, choice.kwargs)

        return RunModels(
            model=spec(self.models.main),
            subagent_model=spec(self.models.subagent),
            use_gateway=self.models.use_gateway,
            routes={route: spec(choice) for route, choice in self.models.routes.items()},
            vision=None if self.models.vision is None else spec(self.models.vision),
        )

    @property
    def loop(self) -> RunLoop:
        """The graph's middleware around each model call, as the plan has it."""
        return RunLoop(
            self.surface.middleware,
            self.start_models(),
            offered={tool.name for tool in self._offered()},
        )

    @property
    def _incident(self) -> IncidentMiddleware | None:
        return next((m for m in self.surface.middleware if isinstance(m, IncidentMiddleware)), None)

    @property
    def _client_tools(self) -> ClientToolsMiddleware | None:
        return next(
            (m for m in self.surface.middleware if isinstance(m, ClientToolsMiddleware)), None
        )

    async def in_run[T](self, name: str, work: Callable[[], Awaitable[T]]) -> T:
        """``work`` as this run: what it reads of the run's config, such as who the run is
        for, is the run's own."""

        async def run(runtime: Runtime) -> T:
            await self.rejoin_preparation(runtime)
            return await work()

        return await in_graph_context({**self.config, "run_name": name}, run)

    async def operation(self, request: RunOperation) -> OperationResult:
        """Do the work of one of the run's tools: its arguments in, its result out."""
        return await run_operation(
            self.surface.tools.get(request.name),
            request,
            config=self.config,
            state=operation_state(request, self._restore),
            guards=self._shell_guards,
            client=self._client_tools,
            ready=self.rejoin_preparation,
        )

    async def check_shell(self, request: ShellCheck) -> ShellVerdict:
        """Whether the push and pull request guards let ``request``'s command run as it is."""
        return await self.in_run("check_shell", lambda: check_shell(self._shell_guards, request))

    async def record_evidence(self, request: ToolEvidence) -> EvidenceReply:
        """An incident run's tool result, recorded as evidence the result then cites."""
        incident = self._incident
        if incident is None:
            return EvidenceReply(content=request.content)
        result = ToolMessage(
            content=_message_content(request.content),
            artifact=request.artifact,
            status=request.status,
            tool_call_id="evidence",
            name=request.tool,
        )
        observed = incident.observe(result, {"name": request.tool, "args": request.arguments})
        return EvidenceReply(content=observed.content)

    async def thread_files(self) -> BackendProtocol:
        if self.surface.files is None:
            raise RuntimeError("This run has no files on the backend")
        return self.surface.files

    async def _answer_phase[R: PhaseResult](
        self, phase: Phase, call: RunCall, work: PhaseWork[R]
    ) -> R:
        """Do ``phase``'s ``work`` on ``call``'s state as the run, and answer with what it did."""
        state = self.state(call)

        async def run(runtime: Runtime) -> R:
            await self.join(state, runtime)
            answered = await work(state, runtime)
            await self.flush(runtime)
            return answered

        events: list[object] = []
        answered = await in_graph_context({**self.config, "run_name": phase}, run, events=events)
        return answered.model_copy(update={"events": [_json_or_text(e) for e in events]})

    async def prepare(self, request: Prepare) -> PhaseResult:
        # MDA opened the box before preparing; preparation connects to it as to any other.
        await self.bind_sandbox(request)
        cfg = RunConfig.from_config(self.config)
        if self.surface.inbox is not None and cfg.thread_id is not None:
            # The events this run was started to carry are its input: it holds them now.
            await ThreadInbox(cfg.thread_id).receive(events=_carried_events(self.config))

        async def begun(state: dict[str, object], runtime: Runtime) -> PhaseResult:
            result = await run_hooks(self.surface.middleware, "before_agent", state, runtime)
            begin = await TurnGitMetadata(self.config).begin()
            return result.model_copy(update={"update": {**result.update, **begin}})

        return await self._answer_phase("before_agent", request, begun)

    async def before_model(self, request: RunCall) -> BeforeModel:
        return await self._answer_phase("before_model", request, self.loop.before_model)

    async def after_model(self, request: RunCall) -> PhaseResult:
        return await self._answer_phase("after_model", request, self.loop.after_model)

    async def after_agent(self, request: RunCall) -> AfterAgent:
        async def reconciled(state: dict[str, object], runtime: Runtime) -> AfterAgent:
            result = await run_hooks(self.surface.middleware, "after_agent", state, runtime)
            trace_metadata = await TurnGitMetadata(self.config).reconcile(state)
            return AfterAgent(
                **result.model_dump(exclude={"update"}),
                update={**result.update, BASELINE: None},
                trace_metadata=trace_metadata,
            )

        return await self._answer_phase("after_agent", request, reconciled)

    async def sandbox_unreachable(self, request: SandboxGone) -> Done:
        """A tool MDA ran in the box found it gone; the run ends once the person knows."""
        await post_sandbox_unreachable_notification(self.config, sandbox_id=request.sandbox_id)
        return Done()

    def _offered(self) -> list[BaseTool]:
        return [tool for name, tool in self.surface.tools.items() if name not in _MDA_TOOLS]


def _observed_request(state: Mapping[str, object], runtime: Runtime) -> ModelRequest:
    """The call MDA made, as the observers of its response read it."""
    return ModelRequest(
        model=FakeListChatModel(responses=[""]),
        messages=as_list(state.get("messages")),
        state=cast(AgentState, dict(state)),
        runtime=runtime,
        tools=[],
        model_settings={},
    )


def _chain(
    middleware: Sequence[AgentMiddleware],
    terminal: Callable[[ModelRequest], Awaitable[ModelResponse]],
) -> Callable[[ModelRequest], Awaitable[ModelResponse]]:
    """``middleware`` around ``terminal``, the first outermost, as a graph composes them."""
    handler = terminal
    for m in reversed(middleware):
        handler = partial(_wrap, m, handler)
    return handler


async def _wrap(
    middleware: AgentMiddleware,
    handler: Callable[[ModelRequest], Awaitable[ModelResponse]],
    request: ModelRequest,
) -> ModelResponse:
    result = await middleware.awrap_model_call(request, handler)
    if isinstance(result, ModelResponse):
        return result
    if isinstance(result, AIMessage):
        return ModelResponse(result=[result])
    # An ExtendedModelResponse carries the response alongside a state command.
    model_response = getattr(result, "model_response", None)
    if isinstance(model_response, ModelResponse):
        return model_response
    raise TypeError(f"{type(middleware).__name__} returned no model response")


def operation_state(request: RunOperation, restore: StateRestorer | None) -> dict[str, object]:
    """The graph state an operation reads, as the call carries it."""
    state: dict[str, object] = {key: decode_state(value) for key, value in request.state.items()}
    if request.messages is not None:
        state["messages"] = messages_from_dict(request.messages)
    if restore is not None:
        restore(state)
    return state


async def run_operation(
    tool: BaseTool | None,
    request: RunOperation,
    *,
    config: RunnableConfig,
    state: dict[str, object],
    guards: ShellGuards,
    client: ClientToolsMiddleware | None,
    ready: Callable[[Runtime], Awaitable[None]],
) -> OperationResult:
    """Do the work of ``tool`` for ``request``, as the run ``config`` names, once ``ready``."""
    if tool is None or request.name in _MDA_TOOLS:
        return OperationResult(
            content=f"{request.name} is not one of this run's operations", status="error"
        )
    unexpected = _unexpected_arguments(tool, request.arguments)
    if unexpected:
        return OperationResult(
            content=f"Unexpected arguments for {request.name}: {', '.join(sorted(unexpected))}",
            status="error",
        )
    if client is not None and (pending := client.pending_result(request.name, request.request_id)):
        return _operation_result(pending, request.request_id)
    events: list[object] = []

    async def run(runtime: Runtime) -> OperationResult:
        await ready(runtime)
        decision = await guards.decide(request.name, request.arguments)
        if decision.arguments is None:
            return OperationResult(content=decision.refusal or "", status="error")
        observed_at = time.time_ns()
        with collect_pr_targets() as pr_targets:
            try:
                outcome = await invoke(tool, decision.arguments, state, request.request_id)
            except NodeCancelledError:
                # The run is being cancelled: that ends the call, not just its result.
                raise
            except Exception as exc:
                failure = await OperationFailure.of(exc, operation=request.name, config=config)
                return OperationResult(
                    content=failure.content,
                    status="error",
                    sandbox_unreachable=failure.sandbox_unreachable,
                )
        result = _operation_result(outcome, request.request_id)
        evidence = TurnGitMetadata(config).evidence(
            pr_targets, request.request_id, pr=True, observed_at=observed_at
        )
        return result.model_copy(update={"evidence": evidence})

    result = await in_graph_context({**config, "run_name": request.name}, run, events=events)
    return result.model_copy(update={"events": [_json_or_text(e) for e in events]})


async def check_shell(guards: ShellGuards, request: ShellCheck) -> ShellVerdict:
    """Whether ``guards`` let ``request``'s command run as it is, inside the run's context."""
    decision = await guards.decide(request.tool, {"command": request.command})
    if decision.arguments is None:
        return ShellVerdict(allowed=False, refusal=decision.refusal)
    command = decision.arguments.get("command")
    return ShellVerdict(
        allowed=True,
        command=command if isinstance(command, str) and command != request.command else None,
    )


async def reconnect_sandbox(config: RunnableConfig, assistant_id: str | None) -> None:
    """Connect again to the thread's box, which MDA replaced with a fresh one."""

    async def reconnect(_runtime: Runtime) -> None:
        cfg = RunConfig.from_config(config)
        if cfg.thread_id is None:
            raise ValueError("This run has no thread")
        reviewing = assistant_id in {"reviewer", "review-scout"}
        repositories = [cfg.repo.full_name] if cfg.repo else []
        await reconnect_replaced_sandbox(
            cfg.thread_id,
            workspace_slug=cfg.workspace_slug,
            github_proxy_repositories=repositories if reviewing else None,
            github_proxy_permissions=READ_ONLY_REVIEW_PERMISSIONS if reviewing else None,
        )

    await in_graph_context({**config, "run_name": "sandbox"}, reconnect)


@dataclass(frozen=True)
class RunStart:
    """What the backend works out for a run as it starts it."""

    catalog: Catalog
    settings: RunSettings


class TokenRun:
    """One call of an MDA run, answered from its token and request, never from its plan.

    The graph's ``RunParts`` build what the call needs; a loop call without the
    catalog's rules is answered from the run's plan.
    """

    def __init__(self, graph: RemoteGraph, run: RemoteRun, run_id: str | None) -> None:
        self.graph = graph
        self.run = run
        self.run_id = run_id
        self.parts = graph.parts
        self.config = graph.config_for(run, run_id)
        self._files: BackendProtocol | None = None

    @property
    def legacy(self) -> bool:
        # A token that grants no operations reaches only the tools its run's plan offers.
        return self.run.grant_id is None or self.parts.legacy(RunConfig.from_config(self.config))

    async def _ready(self, _runtime: Runtime) -> None:
        await self.parts.credentials(self.config)

    async def in_run[T](self, name: str, work: Callable[[], Awaitable[T]]) -> T:
        """``work`` as this run, with its credentials ready in this process."""

        async def run(runtime: Runtime) -> T:
            await self._ready(runtime)
            return await work()

        return await in_graph_context({**self.config, "run_name": name}, run)

    async def thread_files(self) -> BackendProtocol:
        if self._files is None:
            files = await self.parts.files(self.config)
            if files is None:
                raise RuntimeError("This run has no files on the backend")
            self._files = files
        return self._files

    async def _session(self) -> RunSession:
        return await self.graph.session(self.run, self.run_id)

    async def skills(self, _request: RunRequest) -> RunSkills:
        """Every skill the run may load: each store's skill folders and their ``SKILL.md``."""

        async def listed() -> RunSkills:
            stores = await self.parts.skills(self.config)
            return RunSkills() if stores is None else await stores.listed()

        return await in_graph_context(
            {**self.config, "run_name": "skills"}, lambda _runtime: listed()
        )

    async def inbox(self, request: InboxRead) -> Inbox:
        if request.spec is None:
            return Inbox()
        taken = await ThreadInbox(self.run.thread_id).read(
            request.run_id or str(uuid4()), request.spec
        )
        return Inbox(
            messages=dump_messages(as_messages(taken.messages)),
            update={} if taken.reply_surface is None else {"reply_surface": taken.reply_surface},
            items=InboxItems(queued=taken.queued, events=taken.events),
        )

    async def inbox_received(self, request: InboxReceipt) -> Done:
        await ThreadInbox(self.run.thread_id).receive(request.items.queued, request.items.events)
        return Done()

    async def _reporting[T](
        self,
        report: Report,
        name: str,
        record: Callable[[dict[str, object], Runtime], Awaitable[T]],
        *,
        messages: Sequence[BaseMessage] = (),
        **held: object,
    ) -> T:
        """``record`` the report as the run, inside the calling subagent, with its turn rejoined.

        A report carries only what it is about: the ``messages`` it adds and the run
        state (``held``, and the turn's git trace) its recorders read. What a recorder
        queued is written before the call answers, so the next call may reach another
        process.
        """
        state: dict[str, object] = {**report.git, **held, "messages": list(messages)}
        carried = [m for m in self.parts.reporters if isinstance(m, CarriedAcrossProcesses)]

        async def reported(runtime: Runtime) -> T:
            for middleware in carried:
                await middleware.aresume_run(cast(AgentState, dict(state)), runtime)
            with within_namespace(report.namespace):
                result = await record(state, runtime)
            for middleware in carried:
                await middleware.aflush_run(runtime)
            return result

        return await in_graph_context({**self.config, "run_name": name}, reported)

    async def model_result(self, request: ModelResult) -> Done:
        """Let the transcript and usage see the response MDA's model gave."""
        response = messages_from_dict(request.response)
        observers = [m for m in self.parts.reporters if isinstance(m, _MODEL_OBSERVERS)]

        async def respond(_request: ModelRequest) -> ModelResponse:
            return ModelResponse(result=response)

        async def observe(state: dict[str, object], runtime: Runtime) -> list[BaseMessage]:
            observed = await _chain(observers, respond)(_observed_request(state, runtime))
            return observed.result

        await self._reporting(
            request,
            "model_result",
            observe,
            messages=messages_from_dict(request.humans),
            model_route=request.model_route,
            conversation_offloading=request.offloading,
        )
        return Done()

    def _tool_recorders(self) -> list[RecordsRemoteTools]:
        return [m for m in self.parts.reporters if isinstance(m, RecordsRemoteTools)]

    async def tool_started(self, request: ToolCallReport) -> Done:
        tool_call = ToolCall(
            name=request.name, args=dict(request.arguments), id=request.tool_call_id
        )

        async def record(_state: dict[str, object], _runtime: Runtime) -> None:
            for recorder in self._tool_recorders():
                await recorder.atool_started(tool_call, request.message_id)

        await self._reporting(request, "tool_started", record)
        return Done()

    async def tool_result(self, request: ToolResultReport) -> ToolResultReply:
        [result] = messages_from_dict([request.result])
        if not isinstance(result, ToolMessage):
            raise ValueError("A tool result must be a tool message")

        async def record(state: dict[str, object], _runtime: Runtime) -> dict[str, JsonValue]:
            for recorder in self._tool_recorders():
                await recorder.atool_finished(result)
            # A file or shell tool may have started the turn's work; the snapshot is taken
            # now, off the call's path, as the run, which a bridged machine checks.
            git = TurnGitMetadata(self.config)
            before = git.work_baseline(state) if request.name in OBSERVED_TOOLS else None
            return await git.work_evidence(before, request.tool_call_id, time.time_ns())

        update = await self._reporting(request, "tool_result", record)
        return ToolResultReply(update=update)

    async def operation(self, request: RunOperation) -> OperationResult:
        """Do the work of one of the run's tools: its arguments in, its result out."""
        if not self.run.granted(request.name, request.grant):
            return OperationResult(
                content=f"{request.name} is not one of this run's operations", status="error"
            )
        cfg = RunConfig.from_config(self.config)
        client = ClientToolsMiddleware(cfg.client_tools) if cfg.client_tools else None
        offered = (
            next((t for t in client.tools if t.name == request.name), None) if client else None
        )
        return await run_operation(
            offered or await self.parts.tool(self.config, request.name),
            request,
            config=self.config,
            state=operation_state(request, self.parts.restore),
            guards=ShellGuards(self.parts.shell_guards),
            client=client,
            ready=self._ready,
        )

    async def check_shell(self, request: ShellCheck) -> ShellVerdict:
        """Whether the push and pull request guards let ``request``'s command run as it is."""
        guards = ShellGuards(self.parts.shell_guards)
        return await self.in_run("check_shell", lambda: check_shell(guards, request))

    async def sandbox_replaced(self, _request: RunRequest) -> Done:
        await reconnect_sandbox(self.config, self.graph.assistant_id)
        return Done()

    async def sandbox_unreachable(self, request: SandboxGone) -> Done:
        """A tool MDA ran in the box found it gone; the run ends once the person knows."""
        await post_sandbox_unreachable_notification(self.config, sandbox_id=request.sandbox_id)
        return Done()

    async def route_model(self, request: Routed) -> Route:
        """The route the person's latest request takes."""
        return Route(route=await classify_route(messages_from_dict(request.humans)))

    async def model_choice(self, request: ChosenModel) -> ModelSpec:
        return chosen_model(request.model_id, request.effort)

    async def task_context(self, _request: RunRequest) -> TaskContext:
        """The task the thread belongs to, as each model call tells the model."""

        async def read() -> TaskContext:
            coordination = await TaskCoordinationMiddleware.for_thread(self.run.thread_id)
            if coordination is None:
                return TaskContext()
            return TaskContext(
                instructions=coordination.instructions,
                message=await coordination.context_message(),
            )

        return await self.in_run("task_context", read)

    async def refresh_github_proxy(self, _request: RunRequest) -> GitHubProxyRefreshed:
        cfg = RunConfig.from_config(self.config)
        expires_at = await refresh_thread_github_proxy(
            self.run.thread_id, workspace_slug=cfg.workspace_slug
        )
        return GitHubProxyRefreshed(expires_at=expires_at)

    async def reply_on_behalf(self, request: ReplyOnBehalf) -> Done:
        """Post the model's final answer for it, after it ignored the reply tool."""
        state = call_state(request, self.parts.restore)

        async def post() -> None:
            result = await send_slack_reply(request.text, "final", state=state)
            logger.warning(
                "Posted the model's final message on its behalf after it ignored the reply tool",
                extra={"reply_fallback_result": result},
            )

        await self.in_run("reply_on_behalf", post)
        return Done()

    async def run_ended(self, request: RunEnded) -> AfterAgent:
        """Record the run's end: its transcript turn and usage, and its git work reconciled."""
        reporters = self.parts.reporters

        async def ended(state: dict[str, object], runtime: Runtime) -> AfterAgent:
            result = await run_hooks(reporters, "after_agent", state, runtime)
            trace_metadata = await TurnGitMetadata(self.config).reconcile(state)
            return AfterAgent(
                **result.model_dump(exclude={"update"}),
                update={**result.update, BASELINE: None},
                trace_metadata=trace_metadata,
            )

        try:
            return await self._reporting(
                request, "run_ended", ended, messages=messages_from_dict(request.replies)
            )
        finally:
            self.graph.end(self.run, self.run_id)

    async def step_limit_reached(self, _request: RunRequest) -> Done:
        await self.in_run("step_limit_reached", notify_step_limit)
        return Done()

    async def settle_review_check(self, _request: RunRequest) -> Done:
        await self.in_run("settle_review_check", settle_review_check)
        return Done()

    async def finish_scout_plan(self, request: ScoutPlanEnd) -> Done:
        async def finish() -> None:
            await finish_scout_plan(self.run.thread_id, self.config, request.human_input_summary)

        await self.in_run("finish_scout_plan", finish)
        return Done()

    async def browser(self, request: BrowserReport) -> Done:
        await ThreadBrowser(browser=request.browser, opened=request.opened).report(
            self.run.thread_id
        )
        return Done()

    async def reports(self, request: Reports) -> ReportsTaken:
        """Record the run's reports and notices in order, each key once.

        Stops at the first it cannot take now, so none is recorded before one made
        before it: one that failed, or one another delivery is still recording.
        """
        receipts = ReportReceipts(self.run.thread_id)
        replies: list[dict[str, JsonValue]] = []
        for item in request.items:
            claim = (
                await receipts.claim(item.key) if REPORTED[item.endpoint] != "idempotent" else None
            )
            if isinstance(claim, Recording):
                break
            if isinstance(claim, Recorded):
                replies.append(claim.reply)
                continue
            try:
                with traced_under(item.trace):
                    answered = await self.graph.api(
                        self.run, item.endpoint, item.request.model_dump(mode="json")
                    )
            except BaseException as exc:
                if claim is not None:
                    await asyncio.shield(receipts.release(claim))
                if not isinstance(exc, Exception):
                    raise
                logger.exception(
                    "An MDA report was not recorded",
                    extra={"endpoint": item.endpoint, "report_key": item.key},
                )
                return ReportsTaken(replies=replies, refused=f"{type(exc).__name__}: {exc}")
            reply = _json_object.validate_python(answered)
            if claim is not None:
                await receipts.keep(claim, item.key, reply)
            replies.append(reply)
        return ReportsTaken(replies=replies)


class RemoteGraph:
    """An Open SWE graph's catalog for the MDA runs that play it."""

    def __init__(self, assistant_id: str, plan: RunPlanner, parts: RunParts | None = None) -> None:
        self.assistant_id = assistant_id
        self._plan = plan
        self.parts = parts or RunParts()
        self._sessions: dict[tuple[str, str | None], RunSession] = {}

    def config_for(
        self, run: RemoteRun, run_id: str | None, *, settled: bool = True
    ) -> RunnableConfig:
        config = run_config(run, settled=settled)
        configurable = dict(config.get("configurable") or {})
        if run_id:
            # The transcript keys a run's turn by it; never the tracer's run id.
            configurable["run_id"] = run_id
        return {**config, "configurable": configurable}

    async def _planned(self, run: RemoteRun, run_id: str | None, *, fresh: bool) -> RunSession:
        # Planning starts from the run as dispatch stamped it, never from the settings it decides.
        config = self.config_for(run, run_id, settled=False)
        surface = ToolSurface()
        await self._plan(config, surface)
        await surface.prepare()
        return RunSession(
            surface,
            config,
            self.parts.restore,
            started_here=fresh,
            assistant_id=self.assistant_id,
            run=run,
        )

    async def start_run(self, run: RemoteRun) -> RunStart:
        """What ``run``'s MDA graph is built from, and the settings its calls read."""
        session = await self._planned(run, None, fresh=False)
        return RunStart(catalog=await session.plan(), settings=session.settings())

    async def session(
        self, run: RemoteRun, run_id: str | None, *, fresh: bool = False
    ) -> RunSession:
        key = (run.thread_id, run_id)
        cached = self._sessions.get(key)
        if not fresh and cached is not None and time.monotonic() < cached.expires_at:
            return cached
        session = await self._planned(run, run_id, fresh=fresh)
        if len(self._sessions) >= _SESSION_LIMIT:
            now = time.monotonic()
            self._sessions = {k: s for k, s in self._sessions.items() if s.expires_at > now}
        self._sessions[key] = session
        return session

    def end(self, run: RemoteRun, run_id: str | None) -> None:
        self._sessions.pop((run.thread_id, run_id), None)

    async def api(self, run: RemoteRun, endpoint: str, raw: JsonValue) -> JsonValue:
        """Answer one call the MDA run makes (``runtime_api``), without its plan when it can."""
        raw_id = raw.get("run_id") if isinstance(raw, Mapping) else None
        run_id = raw_id if isinstance(raw_id, str) else None
        if RUN_CALLS.serves(endpoint):
            return await RUN_CALLS.dispatch(endpoint, TokenRun(self, run, run_id), raw)
        if TOOL_CALLS.serves(endpoint):
            call = TokenRun(self, run, run_id)
            if not call.legacy:
                return await TOOL_CALLS.dispatch(endpoint, call, raw)
        if not API.serves(endpoint):
            raise UnknownEndpointError(endpoint)
        # Preparation starts the run's session; the end of the run closes it.
        session = await self.session(run, run_id, fresh=endpoint == PREPARE.name)
        try:
            return await API.dispatch(endpoint, session, raw)
        finally:
            if endpoint == AFTER_AGENT.name:
                self.end(run, run_id)


def _json_or_text(value: object) -> JsonValue:
    try:
        return _json.validate_python(value)
    except ValidationError:
        return str(value)


def _unexpected_arguments(tool: BaseTool, arguments: Mapping[str, JsonValue]) -> set[str]:
    schema = tool_parameters(tool)
    additional = schema.get("additionalProperties")
    if additional is True or isinstance(additional, Mapping):
        return set()
    properties = schema.get("properties")
    return set(arguments) - (set(properties) if isinstance(properties, Mapping) else set())


def _operation_result(outcome: ToolMessage | Command, request_id: str) -> OperationResult:
    """What a tool's own result says, as the operation's result."""
    state: Mapping[str, object] = {}
    message = outcome
    if isinstance(outcome, Command):
        update = outcome.update if isinstance(outcome.update, Mapping) else {}
        messages = update.get("messages")
        message = next(
            (
                m
                for m in (messages if isinstance(messages, list) else [])
                if isinstance(m, ToolMessage) and m.tool_call_id == request_id
            ),
            ToolMessage(content="", tool_call_id=request_id),
        )
        state = {key: value for key, value in update.items() if key != "messages"}
    if not isinstance(message, ToolMessage):
        raise TypeError("An operation answered with no result")
    return OperationResult(
        content=_json.validate_python(message.content),
        status=message.status,
        artifact=_json_or_text(message.artifact),
        message_id=message.id,
        state=json_state(state),
    )


def _message_content(content: str | list[JsonValue]) -> str | list[str | dict]:
    if isinstance(content, str):
        return content
    return [block for block in content if isinstance(block, str | dict)]


def _b64(data: bytes | None) -> str | None:
    return None if data is None else base64.b64encode(data).decode()


class ReachesFiles(Protocol):
    """A run whose calls reach the thread's files: from its plan, or its token."""

    async def thread_files(self) -> BackendProtocol: ...

    async def in_run[T](self, name: str, work: Callable[[], Awaitable[T]]) -> T: ...


async def _ls(run: ReachesFiles, request: PathRequest) -> LsReply:
    files = await run.thread_files()
    result = await run.in_run("files.ls", lambda: files.als(request.path))
    return LsReply.model_validate(asdict(result))


async def _read(run: ReachesFiles, request: ReadRequest) -> ReadReply:
    files = await run.thread_files()
    result = await run.in_run(
        "files.read", lambda: files.aread(request.file_path, request.offset, request.limit)
    )
    return ReadReply.model_validate(asdict(result))


async def _write(run: ReachesFiles, request: WriteRequest) -> FileReply:
    files = await run.thread_files()
    result = await run.in_run(
        "files.write", lambda: files.awrite(request.file_path, request.content)
    )
    return FileReply.model_validate(asdict(result))


async def _edit(run: ReachesFiles, request: EditRequest) -> FileReply:
    files = await run.thread_files()
    result = await run.in_run(
        "files.edit",
        lambda: files.aedit(
            request.file_path, request.old_string, request.new_string, request.replace_all
        ),
    )
    return FileReply.model_validate(asdict(result))


async def _delete(run: ReachesFiles, request: DeleteRequest) -> FileReply:
    files = await run.thread_files()
    result = await run.in_run("files.delete", lambda: files.adelete(request.file_path))
    return FileReply.model_validate(asdict(result))


async def _glob(run: ReachesFiles, request: GlobRequest) -> GlobReply:
    files = await run.thread_files()
    result = await run.in_run("files.glob", lambda: files.aglob(request.pattern, request.path))
    return GlobReply.model_validate(asdict(result))


async def _grep(run: ReachesFiles, request: GrepRequest) -> GrepReply:
    files = await run.thread_files()
    result = await run.in_run(
        "files.grep",
        lambda: files.agrep(
            request.pattern, request.path, request.glob, max_count=request.max_count
        ),
    )
    return GrepReply.model_validate(asdict(result))


async def _execute(run: ReachesFiles, request: ExecuteRequest) -> ExecuteReply:
    files = await run.thread_files()
    if not isinstance(files, SandboxBackendProtocol | CompositeBackend):
        return ExecuteReply(output="Error: this thread's files cannot run commands", exit_code=1)
    result = await run.in_run(
        "files.execute", lambda: files.aexecute(request.command, timeout=request.timeout)
    )
    return ExecuteReply(
        output=result.output, exit_code=result.exit_code, truncated=result.truncated
    )


async def _upload(run: ReachesFiles, request: UploadRequest) -> TransferReplies:
    files = await run.thread_files()
    uploads = [(file.path, base64.b64decode(file.content_base64)) for file in request.files]
    uploaded = await run.in_run("files.upload", lambda: files.aupload_files(uploads))
    return TransferReplies(
        files=[TransferReply(path=reply.path, error=reply.error) for reply in uploaded]
    )


async def _download(run: ReachesFiles, request: DownloadRequest) -> TransferReplies:
    files = await run.thread_files()
    downloaded = await run.in_run("files.download", lambda: files.adownload_files(request.paths))
    return TransferReplies(
        files=[
            TransferReply(path=reply.path, error=reply.error, content_base64=_b64(reply.content))
            for reply in downloaded
        ]
    )


# Calls any run answers from its token and request: what it reads and what it reports.
RUN_CALLS: Final = Handlers[TokenRun]()
RUN_CALLS.serve(BROWSER, TokenRun.browser)
RUN_CALLS.serve(SKILLS, TokenRun.skills)
RUN_CALLS.serve(INBOX, TokenRun.inbox)
RUN_CALLS.serve(INBOX_RECEIVED, TokenRun.inbox_received)
RUN_CALLS.serve(MODEL_RESULT, TokenRun.model_result)
RUN_CALLS.serve(TOOL_STARTED, TokenRun.tool_started)
RUN_CALLS.serve(TOOL_RESULT, TokenRun.tool_result)
RUN_CALLS.serve(ROUTE_MODEL, TokenRun.route_model)
RUN_CALLS.serve(MODEL_CHOICE, TokenRun.model_choice)
RUN_CALLS.serve(TASK_CONTEXT, TokenRun.task_context)
RUN_CALLS.serve(REFRESH_GITHUB_PROXY, TokenRun.refresh_github_proxy)
RUN_CALLS.serve(REPLY_ON_BEHALF, TokenRun.reply_on_behalf)
RUN_CALLS.serve(RUN_ENDED, TokenRun.run_ended)
RUN_CALLS.serve(STEP_LIMIT_REACHED, TokenRun.step_limit_reached)
RUN_CALLS.serve(SETTLE_REVIEW_CHECK, TokenRun.settle_review_check)
RUN_CALLS.serve(FINISH_SCOUT_PLAN, TokenRun.finish_scout_plan)
RUN_CALLS.serve(REPORTS, TokenRun.reports)
# The work of a run's tools, answered from its token unless the run is still served
# from its plan (``RunParts.legacy``, or a token without grants).
TOOL_CALLS: Final = Handlers[TokenRun]()
TOOL_CALLS.serve(OPERATION, TokenRun.operation)
TOOL_CALLS.serve(CHECK_SHELL, TokenRun.check_shell)
TOOL_CALLS.serve(SANDBOX_REPLACED, TokenRun.sandbox_replaced)
TOOL_CALLS.serve(SANDBOX_UNREACHABLE, TokenRun.sandbox_unreachable)
# The calls answered from the run's plan: its preparation, and every phase and tool
# call of a run still served from it.
API: Final = Handlers[RunSession]()
API.serve(PREPARE, RunSession.prepare)
API.serve(BEFORE_MODEL, RunSession.before_model)
API.serve(AFTER_MODEL, RunSession.after_model)
API.serve(AFTER_AGENT, RunSession.after_agent)
API.serve(SANDBOX_REPLACED, RunSession.sandbox_replaced)
API.serve(SANDBOX_UNREACHABLE, RunSession.sandbox_unreachable)
API.serve(OPERATION, RunSession.operation)
API.serve(CHECK_SHELL, RunSession.check_shell)
API.serve(RECORD_EVIDENCE, RunSession.record_evidence)
TOOL_CALLS.serve(file_api.LS, _ls)
API.serve(file_api.LS, _ls)
TOOL_CALLS.serve(file_api.READ, _read)
API.serve(file_api.READ, _read)
TOOL_CALLS.serve(file_api.WRITE, _write)
API.serve(file_api.WRITE, _write)
TOOL_CALLS.serve(file_api.EDIT, _edit)
API.serve(file_api.EDIT, _edit)
TOOL_CALLS.serve(file_api.DELETE, _delete)
API.serve(file_api.DELETE, _delete)
TOOL_CALLS.serve(file_api.GLOB, _glob)
API.serve(file_api.GLOB, _glob)
TOOL_CALLS.serve(file_api.GREP, _grep)
API.serve(file_api.GREP, _grep)
TOOL_CALLS.serve(file_api.EXECUTE, _execute)
API.serve(file_api.EXECUTE, _execute)
TOOL_CALLS.serve(file_api.UPLOAD, _upload)
API.serve(file_api.UPLOAD, _upload)
TOOL_CALLS.serve(file_api.DOWNLOAD, _download)
API.serve(file_api.DOWNLOAD, _download)
