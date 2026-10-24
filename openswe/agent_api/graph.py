"""One Open SWE graph, played by a run on Managed Deep Agents.

MDA keeps the model loop, its checkpoints and every tool call. Everything else is
the run as its graph factory plans it (``ToolSurface``), with no graph compiled,
served as the typed API both sides import (``runtime_api``):

- ``catalog`` names the run's operations (each the work of one of its tools), its
  subagents, models and sandbox, and the checks around its file and shell tools
- ``prepare`` runs every ``before_agent`` hook: run preparation (the thread's
  sandbox, checkout and prompt), the transcript turn, skills, reply bookkeeping
- ``before_model`` runs every ``before_model`` hook (follow-up queue, event matches,
  proxy refresh, task coordination, model routing), then asks the graph's
  middleware what it adds to the call (``ShapesModelCalls``: sections of the system
  prompt, tools it keeps from the call), and names the model to call
- ``model_result`` lets the transcript and usage middleware observe each response
- ``after_model`` runs ``after_model`` hooks, such as the reply nudges, which can
  send the loop back to the model
- ``after_agent`` runs ``after_agent`` hooks: usage, transcript, step-limit notice,
  review-check settle
- ``tool_started`` and ``tool_result`` let the transcript record every tool call
- ``sandbox_replaced`` connects to the box MDA put in place of the thread's, and
  ``sandbox_unreachable`` tells the person a tool MDA ran found the box gone

The run's tools call the same API for their work: an operation by name, the
thread's files where the box is not MDA's or the path is this deployment's, the
push and pull request guards on a shell command, and incident evidence. None of
them is a tool call here: no tools node, no tool-call middleware.

A call made from inside a subagent names the namespace of its ``task`` calls, so
its tool calls and replies land under the subagent, as they do in-process.

The graph's model-call shaping (caching, retries, fallback, summarization, call
limits, provider history fixes, the call deadline, and the swap to a vision model
this graph names) is MDA's own. Graph state the hooks write that MDA
does not know rides in the MDA run's state and comes back with every call.
"""

import base64
import inspect
import logging
import time
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import asdict
from functools import partial
from pathlib import PurePosixPath
from typing import Final, Literal, Protocol, cast, get_args, runtime_checkable
from uuid import uuid4

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
    AnyMessage,
    BaseMessage,
    ToolCall,
    ToolMessage,
    convert_to_messages,
    messages_from_dict,
    messages_to_dict,
)
from langchain_core.runnables import RunnableConfig
from langchain_core.tools import BaseTool
from langchain_quickjs import CodeInterpreterMiddleware
from langgraph.config import get_config
from langgraph.errors import NodeCancelledError
from langgraph.runtime import Runtime
from langgraph.types import Command
from pydantic import JsonValue, TypeAdapter, ValidationError
from runtime_api import files as file_api
from runtime_api.catalog import (
    CATALOG,
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
    MODEL_RESULT,
    PREPARE,
    SANDBOX_REPLACED,
    SANDBOX_UNREACHABLE,
    TOOL_RESULT,
    TOOL_STARTED,
    AfterAgent,
    BeforeModel,
    BrowserReport,
    Done,
    ModelCall,
    ModelResult,
    ObservedResponse,
    PhaseResult,
    Prepare,
    RunCall,
    SandboxGone,
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
from runtime_api.skills import SKILLS, RunSkills, SkillFile

from openswe.agent_api.failures import OperationFailure
from openswe.agent_api.git_metadata import (
    BASELINE,
    OBSERVED_TOOLS,
    TurnGitMetadata,
    collect_pr_targets,
)
from openswe.agent_api.operations import Injections, invoke
from openswe.agent_api.runs import in_graph_context, run_config
from openswe.agent_api.shell_guards import ShellGuards
from openswe.agent_api.tokens import RemoteRun
from openswe.dashboard.options import model_supports_images
from openswe.dispatch import COMPLETION_WEBHOOK_URL
from openswe.github.sandbox_access import READ_ONLY_REVIEW_PERMISSIONS
from openswe.incidents.runtime import IncidentMiddleware
from openswe.middleware.client_tools import ClientToolsMiddleware
from openswe.middleware.conversation_offloading import ConversationOffloadingMiddleware
from openswe.middleware.model_call_shape import CallShape, ShapesModelCalls
from openswe.middleware.model_selection import PersistedRoute, normalize_route
from openswe.middleware.prepare_run import BasePrepareRunMiddleware
from openswe.middleware.record_run_usage import RecordRunUsageMiddleware
from openswe.middleware.sandbox_circuit_breaker import post_sandbox_unreachable_notification
from openswe.middleware.task_coordination import TaskCoordinationMiddleware
from openswe.middleware.transcript import TranscriptMiddleware, within_namespace
from openswe.prompts import common_prompt, prompt
from openswe.run_config import RunConfig
from openswe.runtime.constants import DEFAULT_LLM_MAX_TOKENS
from openswe.sandboxes.browsers import ThreadBrowser
from openswe.sandboxes.lifecycle import bind_mda_sandbox, reconnect_replaced_sandbox
from openswe.sandboxes.managed import ManagedSandbox
from openswe.sandboxes.tool_runtime import (
    EXCLUDED_TOOLS,
    ModelChoice,
    SurfaceModels,
    ToolSurface,
    tool_parameters,
)
from openswe.server import (
    DEEP_AGENT_TOOL_NAMES,
    code_only,
    direct_and_from_code,
    fallback_model_choice,
)
from openswe.utils.model import provider_model_kwargs

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
# Loop bookkeeping and code mode MDA's own middleware already does.
_MDA_PHASES: Final = (ModelCallLimitMiddleware, CodeInterpreterMiddleware)
# The file and shell tools MDA's own filesystem middleware gives the run.
_FILE_TOOLS: Final = frozenset(DEEP_AGENT_TOOL_NAMES) - _MDA_TOOLS
# A message inside a state value, as it crosses to MDA and back.
MESSAGE_KEY: Final = "__lc_message__"
# PersistedRoute is a union of literals: its arguments are the literals, not the names.
_ROUTES: Final = frozenset(name for route in get_args(PersistedRoute) for name in get_args(route))
_SESSION_TTL_SECONDS: Final = 6 * 60 * 60
_SKILL_FILE: Final = "SKILL.md"
_SESSION_LIMIT: Final = 512

_json = TypeAdapter(JsonValue)
_json_object = TypeAdapter(dict[str, JsonValue])
_json_list = TypeAdapter(list[JsonValue])
_json_dicts = TypeAdapter(list[dict[str, JsonValue]])

Phase = Literal["before_agent", "before_model", "after_model", "after_agent"]
type PhaseAnswer[R: PhaseResult] = Callable[[PhaseResult, dict[str, object], Runtime], Awaitable[R]]
RunPlanner = Callable[[RunnableConfig, ToolSurface], Awaitable[None]]
StartModels = Callable[[RunConfig], Awaitable[RunModels]]
StateRestorer = Callable[[dict[str, object]], None]


def dump_messages(messages: Sequence[BaseMessage]) -> list[dict[str, JsonValue]]:
    return _json_dicts.validate_python(messages_to_dict(list(messages)))


def _with_ids(messages: Sequence[BaseMessage]) -> list[BaseMessage]:
    """Messages with ids, so both sides of the run key them the same."""
    for message in messages:
        if not message.id:
            message.id = str(uuid4())
    return list(messages)


def _as_messages(raw: object) -> list[BaseMessage]:
    items = raw if isinstance(raw, list) else [raw]
    return _with_ids(convert_to_messages([item for item in items if item is not None]))


def _encode(value: object) -> object:
    if isinstance(value, BaseMessage):
        return {MESSAGE_KEY: messages_to_dict([value])[0]}
    if isinstance(value, Mapping):
        return {str(k): _encode(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_encode(v) for v in value]
    return value


def decode_state(value: object) -> object:
    """A state value from MDA, with its messages rebuilt."""
    if isinstance(value, Mapping):
        if set(value) == {MESSAGE_KEY} and isinstance(value[MESSAGE_KEY], dict):
            return messages_from_dict([value[MESSAGE_KEY]])[0]
        return {k: decode_state(v) for k, v in value.items()}
    if isinstance(value, list):
        return [decode_state(v) for v in value]
    return value


def _json_state(update: Mapping[str, object]) -> dict[str, JsonValue]:
    state: dict[str, JsonValue] = {}
    for key, value in update.items():
        try:
            state[key] = _json.validate_python(_encode(value))
        except ValidationError:
            logger.debug("Graph state is not JSON and stays here", extra={"state_key": key})
    return state


def _model_spec(choice: ModelChoice) -> ModelSpec:
    return ModelSpec(
        model_id=choice.model_id, kwargs=_json_object.validate_python(dict(choice.kwargs))
    )


def _overrides(middleware: AgentMiddleware, hook: str) -> bool:
    return getattr(type(middleware), hook) is not getattr(AgentMiddleware, hook)


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

    async def atool_started(self, tool_call: ToolCall, messages: Sequence[BaseMessage]) -> None: ...

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
    ) -> None:
        self.surface = surface
        self.config = config
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

    def _tool_recorders(self) -> list[RecordsRemoteTools]:
        return [m for m in self.surface.middleware if isinstance(m, RecordsRemoteTools)]

    async def _record_started(self, tool_call: ToolCall, messages: Sequence[BaseMessage]) -> None:
        for middleware in self._tool_recorders():
            await middleware.atool_started(tool_call, messages)

    async def _record_finished(self, result: ToolMessage) -> None:
        for middleware in self._tool_recorders():
            await middleware.atool_finished(result)

    @property
    def models(self) -> SurfaceModels:
        if self.surface.models is None:
            raise RuntimeError("The graph factory picked no models")
        return self.surface.models

    def state(self, call: RunCall) -> dict[str, object]:
        state: dict[str, object] = {
            **{key: decode_state(value) for key, value in call.state.items()},
            "messages": messages_from_dict(call.messages),
        }
        if self._restore is not None:
            self._restore(state)
        return state

    def catalog(self) -> Catalog:
        operations = {
            name: tool for name, tool in self.surface.tools.items() if name not in _MDA_TOOLS
        }
        offered = operations.keys() | _FILE_TOOLS
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
            model_call_limit=next(
                (
                    m.run_limit
                    for m in self.surface.middleware
                    if isinstance(m, ModelCallLimitMiddleware)
                ),
                None,
            ),
        )

    async def plan(self, _request: RunRequest) -> Catalog:
        """The catalog, with who the run acts for and the box MDA creates for it."""
        sandbox = await self.sandbox()
        return self.catalog().model_copy(
            update={
                "trace_metadata": TurnGitMetadata(self.config).actor,
                "sandbox": None
                if sandbox is None
                else SandboxSpec.model_validate(sandbox.model_dump(mode="json")),
            }
        )

    async def skills(self, _request: RunRequest) -> RunSkills:
        """Every skill the run may load: each source's skill folders and their ``SKILL.md``."""

        async def listed() -> RunSkills:
            folders: list[str] = []
            for source in self.surface.skill_sources:
                listing = await self.files.als(source)
                folders += [entry["path"] for entry in listing.entries or [] if entry.get("is_dir")]
            paths = [str(PurePosixPath(folder) / _SKILL_FILE) for folder in folders]
            downloaded = await self.files.adownload_files(paths) if paths else []
            return RunSkills(
                files=[
                    SkillFile(path=file.path, content=file.content.decode())
                    for file in downloaded
                    if file.content is not None
                ]
            )

        return await self.in_run("skills", listed)

    async def sandbox(self) -> ManagedSandbox | None:
        """The thread's box as MDA creates it, when MDA owns it."""
        cfg = RunConfig.from_config(self.config)
        if cfg.thread_id is None:
            return None
        return await ManagedSandbox.for_thread(cfg.thread_id, cfg.workspace_slug)

    async def bind_sandbox(self, sandbox_id: str | None) -> None:
        """Bind the thread to the box MDA opened before preparing, if MDA owns it."""
        cfg = RunConfig.from_config(self.config)
        if cfg.thread_id is not None and await self.sandbox() is not None:
            await bind_mda_sandbox(cfg.thread_id, sandbox_id, workspace_slug=cfg.workspace_slug)

    async def sandbox_replaced(self, _request: RunCall) -> Done:
        """Connect again to the thread's box, which MDA replaced with a fresh one."""
        await in_graph_context(
            {**self.config, "run_name": "sandbox"}, lambda _runtime: self._reconnect_sandbox()
        )
        return Done()

    async def _reconnect_sandbox(self) -> None:
        cfg = RunConfig.from_config(self.config)
        if cfg.thread_id is None:
            raise ValueError("This run has no thread")
        reviewing = self.assistant_id in {"reviewer", "review-scout"}
        repositories = [cfg.repo.full_name] if cfg.repo else []
        await reconnect_replaced_sandbox(
            cfg.thread_id,
            workspace_slug=cfg.workspace_slug,
            github_proxy_repositories=repositories if reviewing else None,
            github_proxy_permissions=READ_ONLY_REVIEW_PERMISSIONS if reviewing else None,
        )

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
        return RunModels(
            model=_model_spec(self.models.main),
            subagent_model=_model_spec(self.models.subagent),
            use_gateway=self.models.use_gateway,
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

    def _operation_state(self, request: RunOperation) -> dict[str, object]:
        state: dict[str, object] = {
            key: decode_state(value) for key, value in request.state.items()
        }
        if request.messages is not None:
            state["messages"] = messages_from_dict(request.messages)
        if self._restore is not None:
            self._restore(state)
        return state

    async def operation(self, request: RunOperation) -> OperationResult:
        """Do the work of one of the run's tools: its arguments in, its result out."""
        tool = self.surface.tools.get(request.name)
        if tool is None or request.name in _MDA_TOOLS:
            return OperationResult(
                content=f"{request.name} is not one of this run's operations", status="error"
            )
        unexpected = set(request.arguments) - set(_properties(tool))
        if unexpected:
            return OperationResult(
                content=f"Unexpected arguments for {request.name}: {', '.join(sorted(unexpected))}",
                status="error",
            )
        client = self._client_tools
        if client is not None and (
            pending := client.pending_result(request.name, request.request_id)
        ):
            return _operation_result(pending, request.request_id)
        events: list[object] = []

        async def run(runtime: Runtime) -> OperationResult:
            await self.rejoin_preparation(runtime)
            decision = await self._shell_guards.decide(request.name, request.arguments)
            if decision.arguments is None:
                return OperationResult(content=decision.refusal or "", status="error")
            observed_at = time.time_ns()
            with collect_pr_targets() as pr_targets:
                try:
                    outcome = await invoke(
                        tool, decision.arguments, self._operation_state(request), request.request_id
                    )
                except NodeCancelledError:
                    # The run is being cancelled: that ends the call, not just its result.
                    raise
                except Exception as exc:
                    failure = await OperationFailure.of(
                        exc, operation=request.name, config=self.config
                    )
                    return OperationResult(
                        content=failure.content,
                        status="error",
                        sandbox_unreachable=failure.sandbox_unreachable,
                    )
            result = _operation_result(outcome, request.request_id)
            evidence = TurnGitMetadata(self.config).evidence(
                pr_targets, request.request_id, pr=True, observed_at=observed_at
            )
            return result.model_copy(update={"evidence": evidence})

        result = await in_graph_context(
            {**self.config, "run_name": request.name}, run, events=events
        )
        return result.model_copy(update={"events": [_json_or_text(e) for e in events]})

    async def check_shell(self, request: ShellCheck) -> ShellVerdict:
        """Whether the push and pull request guards let ``request``'s command run as it is."""

        async def decide() -> ShellVerdict:
            decision = await self._shell_guards.decide(request.tool, {"command": request.command})
            if decision.arguments is None:
                return ShellVerdict(allowed=False, refusal=decision.refusal)
            command = decision.arguments.get("command")
            return ShellVerdict(
                allowed=True,
                command=command
                if isinstance(command, str) and command != request.command
                else None,
            )

        return await self.in_run("check_shell", decide)

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

    @property
    def files(self) -> BackendProtocol:
        if self.surface.files is None:
            raise RuntimeError("This run has no files on the backend")
        return self.surface.files

    def _phase_middleware(self, phase: Phase) -> list[AgentMiddleware]:
        hook = f"a{phase}"
        middleware = [
            m
            for m in self.surface.middleware
            if _overrides(m, hook)
            and not isinstance(m, _MDA_PHASES)
            # MDA's own summarization offloads automatically; a requested offload runs here.
            and not (isinstance(m, ConversationOffloadingMiddleware) and not m.manual)
        ]
        # As in a graph run: before hooks in order, after hooks in reverse.
        return middleware if phase.startswith("before") else list(reversed(middleware))

    async def run_phase(
        self, phase: Phase, state: dict[str, object], runtime: Runtime
    ) -> PhaseResult:
        update: dict[str, object] = {}
        added: list[BaseMessage] = []
        jump_to: str | None = None
        for middleware in self._phase_middleware(phase):
            result = await _call_hook(getattr(middleware, f"a{phase}"), state, runtime)
            if not result:
                continue
            values = dict(result)
            target = values.pop("jump_to", None)
            if "messages" in values:
                messages = _as_messages(values.pop("messages"))
                added.extend(messages)
                state["messages"] = [*_as_list(state.get("messages")), *messages]
            update.update(values)
            state.update(values)
            if isinstance(target, str) and target:
                jump_to = target
                break
        return PhaseResult(
            update=_json_state(update), messages=dump_messages(added), jump_to=jump_to
        )

    async def _answer_phase[R: PhaseResult](
        self, phase: Phase, call: RunCall, answer: PhaseAnswer[R]
    ) -> R:
        """Run ``phase``'s hooks on ``call``'s state as the run, and answer with what they did."""
        state = self.state(call)

        async def run(runtime: Runtime) -> R:
            await self.join(state, runtime)
            result = await self.run_phase(phase, state, runtime)
            answered = await answer(result, state, runtime)
            await self.flush(runtime)
            return answered

        events: list[object] = []
        answered = await in_graph_context({**self.config, "run_name": phase}, run, events=events)
        return answered.model_copy(update={"events": [_json_or_text(e) for e in events]})

    async def prepare(self, request: Prepare) -> PhaseResult:
        # MDA opened the box before preparing; preparation connects to it as to any other.
        await self.bind_sandbox(request.sandbox_id)

        async def begun(
            result: PhaseResult, _state: dict[str, object], _runtime: Runtime
        ) -> PhaseResult:
            begin = await TurnGitMetadata(self.config).begin()
            return result.model_copy(update={"update": {**result.update, **begin}})

        return await self._answer_phase("before_agent", request, begun)

    async def before_model(self, request: RunCall) -> BeforeModel:
        async def planned(
            result: PhaseResult, state: dict[str, object], runtime: Runtime
        ) -> BeforeModel:
            if result.jump_to is not None:
                return BeforeModel(**result.model_dump())
            model = self.model_for(state)
            vision = self.vision_for(model)
            fallback = fallback_model_choice(model.model_id)
            call = (await self.model_call(state, runtime)).model_copy(
                update={
                    "model": _model_spec(model),
                    "vision": None if vision is None else _model_spec(vision),
                    "fallback": None if fallback is None else _model_spec(fallback),
                    "use_gateway": self.models.use_gateway,
                }
            )
            return BeforeModel(**result.model_dump(), call=call)

        return await self._answer_phase("before_model", request, planned)

    async def after_model(self, request: RunCall) -> PhaseResult:
        return await self._answer_phase("after_model", request, _as_is)

    async def after_agent(self, request: RunCall) -> AfterAgent:
        async def reconciled(
            result: PhaseResult, state: dict[str, object], _runtime: Runtime
        ) -> AfterAgent:
            trace_metadata = await TurnGitMetadata(self.config).reconcile(state)
            return AfterAgent(
                **result.model_dump(exclude={"update"}),
                update={**result.update, BASELINE: None},
                trace_metadata=trace_metadata,
            )

        return await self._answer_phase("after_agent", request, reconciled)

    async def model_result(self, request: ModelResult) -> ObservedResponse:
        state = self.state(request)
        response = messages_from_dict(request.response)

        async def observed(runtime: Runtime) -> list[BaseMessage]:
            await self.join(state, runtime)
            with within_namespace(request.namespace):
                seen = await self.observe(state, response, runtime)
            await self.flush(runtime)
            return seen

        seen = await in_graph_context({**self.config, "run_name": "model_result"}, observed)
        return ObservedResponse(response=dump_messages(seen))

    async def tool_started(self, request: ToolCallReport) -> Done:
        state = self.state(request)
        tool_call = ToolCall(
            name=request.name, args=dict(request.arguments), id=request.tool_call_id
        )

        async def record(runtime: Runtime) -> None:
            await self.join(state, runtime)
            with within_namespace(request.namespace):
                await self._record_started(tool_call, messages_from_dict(request.messages))
            await self.flush(runtime)

        await in_graph_context({**self.config, "run_name": "tool_started"}, record)
        return Done()

    async def tool_result(self, request: ToolResultReport) -> ToolResultReply:
        state = self.state(request)
        [result] = messages_from_dict([request.result])
        if not isinstance(result, ToolMessage):
            raise ValueError("A tool result must be a tool message")

        async def record(runtime: Runtime) -> dict[str, JsonValue]:
            await self.join(state, runtime)
            with within_namespace(request.namespace):
                await self._record_finished(result)
            await self.flush(runtime)
            # A file or shell tool may have started the turn's work; the snapshot is taken
            # now, off the call's path, as the run, which a bridged machine checks.
            git = TurnGitMetadata(self.config)
            before = git.work_baseline(state) if request.name in OBSERVED_TOOLS else None
            return await git.work_evidence(before, request.tool_call_id, time.time_ns())

        update = await in_graph_context({**self.config, "run_name": "tool_result"}, record)
        return ToolResultReply(update=update)

    async def sandbox_unreachable(self, request: SandboxGone) -> Done:
        """A tool MDA ran in the box found it gone; the run ends once the person knows."""
        await post_sandbox_unreachable_notification(self.config, sandbox_id=request.sandbox_id)
        return Done()

    def _offered(self) -> list[BaseTool]:
        return [tool for name, tool in self.surface.tools.items() if name not in _MDA_TOOLS]

    def _request(self, state: Mapping[str, object], runtime: Runtime) -> ModelRequest:
        """The call MDA made, as the observers of its response read it."""
        return ModelRequest(
            model=FakeListChatModel(responses=[""]),
            messages=_as_list(state.get("messages")),
            state=cast(AgentState, dict(state)),
            runtime=runtime,
            tools=[],
            model_settings={},
        )

    async def model_call(self, state: Mapping[str, object], runtime: Runtime) -> ModelCall:
        """The system prompt and tool list the graph's own middleware gives the next call."""
        call = CallShape()
        for middleware in self.surface.middleware:
            if isinstance(middleware, ShapesModelCalls):
                call = await middleware.ashape_model_call(call, state, runtime)
        offered = {tool.name for tool in self._offered()}
        return ModelCall(
            system_prompt=common_prompt(call.system_prompt),
            hidden_tools=sorted(call.hidden_tools & offered),
        )

    def model_for(self, state: Mapping[str, object]) -> ModelChoice:
        """The model the graph's model selection would call next."""
        requested = state.get("requested_model")
        if isinstance(requested, str) and requested:
            return self._explicit_model(requested, state.get("requested_effort"))
        route = state.get("model_route")
        if route in _ROUTES and route is not None:
            selected_route = normalize_route(cast(PersistedRoute, route))
            routed = self.models.routes.get(selected_route)
            if routed is not None and selected_route != "default":
                return routed
        selected = state.get("selected_model_id")
        if isinstance(selected, str) and selected and selected != self.models.main.model_id:
            return self._explicit_model(selected, state.get("selected_effort"))
        return self.models.main

    def vision_for(self, model: ModelChoice) -> ModelChoice | None:
        """What a call to ``model`` goes to instead once the conversation holds an image."""
        if self.models.vision is None or model_supports_images(model.model_id):
            return None
        return self.models.vision

    def _explicit_model(self, model_id: str, effort: object) -> ModelChoice:
        return ModelChoice(
            model_id,
            dict(
                provider_model_kwargs(
                    model_id,
                    effort if isinstance(effort, str) else None,
                    max_tokens=DEFAULT_LLM_MAX_TOKENS,
                )
            ),
        )

    async def observe(
        self, state: Mapping[str, object], response: list[BaseMessage], runtime: Runtime
    ) -> list[BaseMessage]:
        """Let the transcript and usage middleware see the response MDA's model gave."""

        async def respond(_request: ModelRequest) -> ModelResponse:
            return ModelResponse(result=response)

        observers = [m for m in self.surface.middleware if isinstance(m, _MODEL_OBSERVERS)]
        result = await _chain(observers, respond)(self._request(state, runtime))
        return result.result


async def _call_hook(
    hook: Callable[..., Awaitable[dict[str, object] | None]],
    state: dict[str, object],
    runtime: Runtime,
) -> dict[str, object] | None:
    """Call a middleware hook as a graph run does, with the config when it asks for one."""
    if "config" in inspect.signature(hook).parameters:
        return await hook(cast(AgentState, state), runtime, get_config())
    return await hook(cast(AgentState, state), runtime)


async def _as_is(result: PhaseResult, _state: dict[str, object], _runtime: Runtime) -> PhaseResult:
    return result


def _as_list(value: object) -> list[AnyMessage]:
    return list(value) if isinstance(value, list) else []


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


class RemoteGraph:
    """An Open SWE graph's catalog for the MDA runs that play it."""

    def __init__(
        self,
        assistant_id: str,
        plan: RunPlanner,
        start_models: StartModels,
        *,
        restore_state: StateRestorer | None = None,
    ) -> None:
        self.assistant_id = assistant_id
        self._plan = plan
        self.start_models = start_models
        self._restore_state = restore_state
        self._sessions: dict[tuple[str, str | None], RunSession] = {}

    def _config(self, run: RemoteRun, run_id: str | None) -> RunnableConfig:
        config = run_config(run)
        configurable = dict(config.get("configurable") or {})
        if run_id:
            # The transcript keys a run's turn by it; never the tracer's run id.
            configurable["run_id"] = run_id
        return {**config, "configurable": configurable}

    async def session(
        self, run: RemoteRun, run_id: str | None, *, fresh: bool = False
    ) -> RunSession:
        key = (run.thread_id, run_id)
        cached = self._sessions.get(key)
        if not fresh and cached is not None and time.monotonic() < cached.expires_at:
            return cached
        config = self._config(run, run_id)
        surface = ToolSurface()
        await self._plan(config, surface)
        await surface.prepare()
        session = RunSession(
            surface,
            config,
            self._restore_state,
            started_here=fresh,
            assistant_id=self.assistant_id,
        )
        if len(self._sessions) >= _SESSION_LIMIT:
            now = time.monotonic()
            self._sessions = {k: s for k, s in self._sessions.items() if s.expires_at > now}
        self._sessions[key] = session
        return session

    def end(self, run: RemoteRun, run_id: str | None) -> None:
        self._sessions.pop((run.thread_id, run_id), None)

    async def api(self, run: RemoteRun, endpoint: str, raw: JsonValue) -> JsonValue:
        """Answer one call the MDA run makes (``runtime_api``), as the session of its run."""
        if RUN_CALLS.serves(endpoint):
            return await RUN_CALLS.dispatch(endpoint, run, raw)
        if not API.serves(endpoint):
            raise UnknownEndpointError(endpoint)
        raw_id = raw.get("run_id") if isinstance(raw, Mapping) else None
        run_id = raw_id if isinstance(raw_id, str) else None
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


def _properties(tool: BaseTool) -> Mapping[str, object]:
    properties = tool_parameters(tool).get("properties")
    return properties if isinstance(properties, Mapping) else {}


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
        state=_json_state(state),
    )


def _message_content(content: str | list[JsonValue]) -> str | list[str | dict]:
    if isinstance(content, str):
        return content
    return [block for block in content if isinstance(block, str | dict)]


def _b64(data: bytes | None) -> str | None:
    return None if data is None else base64.b64encode(data).decode()


async def _ls(session: RunSession, request: PathRequest) -> LsReply:
    result = await session.in_run("files.ls", lambda: session.files.als(request.path))
    return LsReply.model_validate(asdict(result))


async def _read(session: RunSession, request: ReadRequest) -> ReadReply:
    result = await session.in_run(
        "files.read", lambda: session.files.aread(request.file_path, request.offset, request.limit)
    )
    return ReadReply.model_validate(asdict(result))


async def _write(session: RunSession, request: WriteRequest) -> FileReply:
    result = await session.in_run(
        "files.write", lambda: session.files.awrite(request.file_path, request.content)
    )
    return FileReply.model_validate(asdict(result))


async def _edit(session: RunSession, request: EditRequest) -> FileReply:
    result = await session.in_run(
        "files.edit",
        lambda: session.files.aedit(
            request.file_path, request.old_string, request.new_string, request.replace_all
        ),
    )
    return FileReply.model_validate(asdict(result))


async def _delete(session: RunSession, request: DeleteRequest) -> FileReply:
    result = await session.in_run("files.delete", lambda: session.files.adelete(request.file_path))
    return FileReply.model_validate(asdict(result))


async def _glob(session: RunSession, request: GlobRequest) -> GlobReply:
    result = await session.in_run(
        "files.glob", lambda: session.files.aglob(request.pattern, request.path)
    )
    return GlobReply.model_validate(asdict(result))


async def _grep(session: RunSession, request: GrepRequest) -> GrepReply:
    result = await session.in_run(
        "files.grep",
        lambda: session.files.agrep(
            request.pattern, request.path, request.glob, max_count=request.max_count
        ),
    )
    return GrepReply.model_validate(asdict(result))


async def _execute(session: RunSession, request: ExecuteRequest) -> ExecuteReply:
    files = session.files
    if not isinstance(files, SandboxBackendProtocol | CompositeBackend):
        return ExecuteReply(output="Error: this thread's files cannot run commands", exit_code=1)
    result = await session.in_run(
        "files.execute", lambda: files.aexecute(request.command, timeout=request.timeout)
    )
    return ExecuteReply(
        output=result.output, exit_code=result.exit_code, truncated=result.truncated
    )


async def _upload(session: RunSession, request: UploadRequest) -> TransferReplies:
    uploads = [(file.path, base64.b64decode(file.content_base64)) for file in request.files]
    uploaded = await session.in_run("files.upload", lambda: session.files.aupload_files(uploads))
    return TransferReplies(
        files=[TransferReply(path=reply.path, error=reply.error) for reply in uploaded]
    )


async def _download(session: RunSession, request: DownloadRequest) -> TransferReplies:
    downloaded = await session.in_run(
        "files.download", lambda: session.files.adownload_files(request.paths)
    )
    return TransferReplies(
        files=[
            TransferReply(path=reply.path, error=reply.error, content_base64=_b64(reply.content))
            for reply in downloaded
        ]
    )


async def _browser(run: RemoteRun, request: BrowserReport) -> Done:
    await ThreadBrowser(browser=request.browser, opened=request.opened).report(run.thread_id)
    return Done()


# Calls about the run's thread that need none of its plan.
RUN_CALLS: Final = Handlers[RemoteRun]()
RUN_CALLS.serve(BROWSER, _browser)
# The typed API every graph's MDA runs call (``runtime_api``), as the session of their run.
API: Final = Handlers[RunSession]()
API.serve(CATALOG, RunSession.plan)
API.serve(PREPARE, RunSession.prepare)
API.serve(BEFORE_MODEL, RunSession.before_model)
API.serve(MODEL_RESULT, RunSession.model_result)
API.serve(AFTER_MODEL, RunSession.after_model)
API.serve(AFTER_AGENT, RunSession.after_agent)
API.serve(TOOL_STARTED, RunSession.tool_started)
API.serve(TOOL_RESULT, RunSession.tool_result)
API.serve(SANDBOX_REPLACED, RunSession.sandbox_replaced)
API.serve(SANDBOX_UNREACHABLE, RunSession.sandbox_unreachable)
API.serve(SKILLS, RunSession.skills)
API.serve(OPERATION, RunSession.operation)
API.serve(CHECK_SHELL, RunSession.check_shell)
API.serve(RECORD_EVIDENCE, RunSession.record_evidence)
API.serve(file_api.LS, _ls)
API.serve(file_api.READ, _read)
API.serve(file_api.WRITE, _write)
API.serve(file_api.EDIT, _edit)
API.serve(file_api.DELETE, _delete)
API.serve(file_api.GLOB, _glob)
API.serve(file_api.GREP, _grep)
API.serve(file_api.EXECUTE, _execute)
API.serve(file_api.UPLOAD, _upload)
API.serve(file_api.DOWNLOAD, _download)
