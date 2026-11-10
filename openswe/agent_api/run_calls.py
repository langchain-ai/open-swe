"""The calls an Open SWE run on MDA makes on the backend, each answered from the
run's token and the request alone, on whichever replica it lands.

MDA keeps the model loop, its checkpoints, every tool call, and the middleware around
them; the backend answers the typed API both sides import (``runtime_api``):

- ``prepare`` readies the run's turn: its prompt and model, from what the token
  says starting the run decided (``RunSettings``), without reaching the sandbox MDA keeps.
  What the turn's start records (its transcript turn, the thread's model and usage) is
  the ``run_started`` report, off the run's path
- the inbox and skills, and the named calls MDA's middleware makes around a model call
  (``reminders``, ``model_choice``, ``incident_context``, ``refresh_github_proxy``,
  ``reply_on_behalf``), to move its Slack conversation (``slack_move``, ``slack_moved``),
  and at the run's end (``run_ended``, or ``model_failed`` when a model call fails it,
  ``step_limit_reached``, ``settle_review_check``, ``finish_scout_plan``)
- the reports of each reply and tool call, which arrive with those notices in ordered
  batches (``reports``) and are recorded once per key
- the work of the run's tools (``operation``, ``check_shell``, ``record_evidence``, the
  ``files`` calls), and the sandbox MDA keeps

None of the run's tool calls is a tool call here: no tools node, no tool-call
middleware. A call made from inside a subagent names the namespace of its ``task``
calls, so its tool calls and replies land under the subagent.
"""

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable, Iterable, Mapping, Sequence
from contextvars import ContextVar
from datetime import UTC, datetime
from functools import partial
from typing import Final, Protocol, cast, runtime_checkable
from uuid import UUID, uuid4

from deepagents.backends.protocol import BackendProtocol
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
from langgraph.runtime import Runtime
from pydantic import BaseModel, JsonValue, TypeAdapter, ValidationError
from runtime_api import files as file_api
from runtime_api.catalog import JOINED_TASK_CONTEXT, ModelSpec
from runtime_api.endpoint import Handlers
from runtime_api.loop import (
    BROWSER,
    FINISH_SCOUT_PLAN,
    GOAL,
    INBOX,
    INBOX_RECEIVED,
    INCIDENT_CONTEXT,
    MODEL_CHOICE,
    MODEL_FAILED,
    MODEL_RESULT,
    PREPARE,
    REFRESH_GITHUB_PROXY,
    REMINDERS,
    REPLY_ON_BEHALF,
    ROUTE_MODEL,
    RUN_ENDED,
    RUN_STARTED,
    SANDBOX_UNREACHABLE,
    SET_GOAL,
    SETTLE_REVIEW_CHECK,
    STEP_LIMIT_REACHED,
    TASK_CONTEXT,
    TOOL_RESULT,
    TOOL_STARTED,
    BrowserReport,
    ChosenModel,
    Done,
    GitHubProxyRefreshed,
    Goal,
    GoalChange,
    GoalRead,
    Inbox,
    InboxItems,
    InboxRead,
    InboxReceipt,
    IncidentContext,
    ModelFailed,
    ModelResult,
    PersonMessage,
    Prepare,
    Prepared,
    RefreshGitHubProxy,
    Reminders,
    ReplyOnBehalf,
    Report,
    Route,
    Routed,
    RunEnded,
    RunStarted,
    SandboxGone,
    ScoutPlanEnd,
    TaskContext,
    ToolCallReport,
    ToolResultReport,
)
from runtime_api.model_errors import LAST_MODEL_ERROR_KEY
from runtime_api.models import chosen_model
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
from runtime_api.sandbox_proxy import SANDBOX_PROXY, SandboxProxy, SandboxProxyRequest
from runtime_api.skills import SKILLS, RunSkills, SkillsRequest
from runtime_api.slack import (
    SLACK_MOVE,
    SLACK_MOVED,
    SlackMoved,
    SlackMovePlan,
    SlackMoveRequest,
    SlackMoveResult,
)
from runtime_api.tool_search import (
    RESOLVE,
    SEARCH,
    TOOL_SET,
    TOOL_SPECS,
    KeptTools,
    ResolvedTool,
    ToolIndex,
    ToolLookup,
    ToolMatches,
    ToolNames,
    ToolSearch,
    ToolSpecs,
)

from openswe.agent_api import file_calls
from openswe.agent_api.caller_trace import traced_under
from openswe.agent_api.operations import Operation
from openswe.agent_api.report_receipts import (
    Claim,
    Claimed,
    Recorded,
    Recording,
    ReportReceipts,
)
from openswe.agent_api.run_kinds import RunKind
from openswe.agent_api.run_sandbox import RunSandbox
from openswe.agent_api.run_state import (
    as_list,
    as_messages,
    call_state,
    decode_state,
    dump_messages,
    jsonable,
    run_hooks,
)
from openswe.agent_api.runs import in_graph_context, run_config
from openswe.agent_api.server import RefusedCallError
from openswe.agent_api.shell_guards import ShellGuards
from openswe.agent_api.tokens import RemoteRun
from openswe.agent_api.tool_sets import ToolSet, ToolSetNotKeptError
from openswe.analytics.segment_activity import SegmentActivity
from openswe.config import ENV
from openswe.github.proxy import proxy_token_set_since
from openswe.incidents.runtime import IncidentSession
from openswe.input_messages import run_context
from openswe.middleware.model_selection import classify_route
from openswe.middleware.notify_step_limit import notify_step_limit
from openswe.middleware.plugin_chat import PluginChatTools
from openswe.middleware.record_run_usage import RecordRunUsageMiddleware
from openswe.middleware.settle_review_check import settle_review_check
from openswe.middleware.stale_workspace import StaleWorkspaceReporter
from openswe.middleware.task_coordination import TaskCoordinationMiddleware
from openswe.middleware.transcript import TranscriptMiddleware, queue_run_notice, within_namespace
from openswe.reminders import Reminder
from openswe.review_scout.graph import finish_scout_plan
from openswe.run_config import RunConfig
from openswe.sandboxes.browsers import ThreadBrowser
from openswe.sandboxes.lifecycle import (
    mda_proxy_config,
    refresh_thread_github_proxy,
    take_stale_boot,
)
from openswe.slack.channel_moves import prepare_channel_move, record_channel_move
from openswe.slack.tools.reply import send_slack_reply
from openswe.threads.goal import ThreadGoals
from openswe.threads.inbox import ThreadInbox
from openswe.threads.store import THREADS
from openswe.utils.thread_reads import shared_thread_reads

logger = logging.getLogger(__name__)

_MODEL_OBSERVERS: Final = (TranscriptMiddleware, RecordRunUsageMiddleware)
_json_object = TypeAdapter(dict[str, JsonValue])
_json_list = TypeAdapter(list[JsonValue])
# The runs a batch of reports writes what its reporters queued for, once it recorded
# them all, by run; None writes it as each report is recorded.
_batch_flushes: ContextVar[dict[str | None, RunnableConfig] | None] = ContextVar(
    "batch_flushes", default=None
)
# Operations that can make the run's thread a task's coordinator mid-run, after its catalog
# was planned without the task: their answer hands the run the task's context.
_JOINS_TASK: Final = frozenset({"manage_baby_sit"})
# Calls that change the work, after which a goal owes a completion check before it completes.
_EDITS_WORK: Final = frozenset({"write_file", "edit_file", "delete", "open_pull_request"})
# Each run's thread files, by its thread and grant, until they expire: building them reads
# the thread and the run's skill stores, which every files call did.
_FILES_TTL_SECONDS: Final = 6 * 60 * 60
_FILES_LIMIT: Final = 512
_files: dict[tuple[str, str], tuple[float, BackendProtocol]] = {}


async def _run_files(
    kind: RunKind, run: RemoteRun, config: RunnableConfig
) -> BackendProtocol | None:
    """The thread's files as ``run``'s tools reach them, built once per run."""
    global _files
    key = (run.thread_id, run.grant_id)
    now = time.monotonic()
    kept = _files.get(key)
    if kept is not None and now < kept[0]:
        return kept[1]
    files = await kind.files(config)
    if files is None:
        return None
    if len(_files) >= _FILES_LIMIT:
        _files = {k: kept for k, kept in _files.items() if kept[0] > now}
        while len(_files) >= _FILES_LIMIT:
            del _files[next(iter(_files))]
    _files[key] = (now + _FILES_TTL_SECONDS, files)
    return files


def _forget_files(run: RemoteRun) -> None:
    """Build ``run``'s files again on its next call: its sandbox changed."""
    _files.pop((run.thread_id, run.grant_id), None)


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


class TokenRun:
    """One call of an MDA run, answered from its token and the request alone.

    The run's ``RunKind`` builds what the call needs.
    """

    def __init__(self, kind: RunKind, run: RemoteRun, run_id: str | None) -> None:
        self.kind = kind
        self.run = run
        self.run_id = run_id
        self.config = self._config()
        self.activity = SegmentActivity(run)

    @classmethod
    async def answer(
        cls, kind: RunKind, run: RemoteRun, endpoint: str, raw: JsonValue
    ) -> JsonValue:
        """Answer one call the MDA run makes (``runtime_api``)."""
        raw_id = raw.get("run_id") if isinstance(raw, Mapping) else None
        call = cls(kind, run, raw_id if isinstance(raw_id, str) else None)
        if endpoint != PREPARE.name:
            return await CALLS.dispatch(endpoint, call, raw)
        # Preparing reads the same thread from many places; one read serves them.
        async with shared_thread_reads():
            return await CALLS.dispatch(endpoint, call, raw)

    def _config(self, invocation_id: str | None = None) -> RunnableConfig:
        """The run's config, as invocation ``invocation_id`` when given: a report a later run
        on the thread sends belongs to the run that made it."""
        config = run_config(self.run)
        configurable = dict(config.get("configurable") or {})
        if self.run_id:
            # The transcript keys a run's turn by it; never the tracer's run id.
            configurable["run_id"] = self.run_id
        if invocation_id is not None and configurable.get("invocation_id") != invocation_id:
            # Usage keys an invocation by it; when that one started is not this token's.
            configurable.pop("prepare_run_id", None)
            configurable.pop("invocation_started_at", None)
            configurable["invocation_id"] = invocation_id
        return {**config, "configurable": configurable}

    @property
    def _sandbox(self) -> RunSandbox:
        return RunSandbox(self.config, reviews=self.kind.reviews)

    async def _ready(self, _runtime: Runtime) -> None:
        await self.kind.credentials(self.config)

    async def in_run[T](self, name: str, work: Callable[[], Awaitable[T]]) -> T:
        """``work`` as this run, with its credentials ready in this process."""

        async def run(runtime: Runtime) -> T:
            await self._ready(runtime)
            return await work()

        return await in_graph_context({**self.config, "run_name": name}, run)

    async def thread_files(self) -> BackendProtocol:
        files = await _run_files(self.kind, self.run, self.config)
        if files is None:
            raise RuntimeError("This run has no files on the backend")
        return files

    async def skills(self, request: SkillsRequest) -> RunSkills:
        """Every skill the run may load but those the runtime serves itself: each store's
        skill folders and their ``SKILL.md``."""

        async def listed() -> RunSkills:
            stores = await self.kind.skills(self.config)
            return RunSkills() if stores is None else await stores.listed(request.served)

        return await in_graph_context(
            {**self.config, "run_name": "skills"}, lambda _runtime: listed()
        )

    async def inbox(self, request: InboxRead) -> Inbox:
        if request.spec is None:
            return Inbox()
        inbox = ThreadInbox(self.run.thread_id)
        await inbox.receive(request.received.queued, request.received.events)
        taken = await inbox.read(request.run_id or str(uuid4()), request.spec)
        return Inbox(
            messages=dump_messages(as_messages(taken.messages)),
            update=taken.state_update(request.run_id),
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
        state (``held``) its recorders read. What a recorder queued is written before the
        call answers, so the next call may reach another process: a batch of reports
        writes it once, after the last (``reports``).
        """
        state: dict[str, object] = {**held, "messages": list(messages)}
        carried = [m for m in self.kind.reporters if isinstance(m, CarriedAcrossProcesses)]

        async def reported(runtime: Runtime) -> T:
            for middleware in carried:
                await middleware.aresume_run(cast(AgentState, dict(state)), runtime)
            with within_namespace(report.namespace):
                result = await record(state, runtime)
            batch = _batch_flushes.get()
            if batch is not None:
                # The batch writes it with the rest of its reports, once they are recorded.
                batch.setdefault(self.run_id, self.config)
                return result
            for middleware in carried:
                await middleware.aflush_run(runtime)
            return result

        return await in_graph_context({**self.config, "run_name": name}, reported)

    async def model_result(self, request: ModelResult) -> Done:
        """Let the transcript and usage see the response MDA's model gave."""
        response = messages_from_dict(request.response)
        observers = [m for m in self.kind.reporters if isinstance(m, _MODEL_OBSERVERS)]

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

    async def prepare(self, request: Prepare) -> Prepared:
        """Ready the run's turn from its token and the conversation.

        Nothing here waits on the sandbox MDA keeps: the run readies the sandbox it opened, and
        its loop keeps the sandbox's GitHub token fresh (``refresh_github_proxy``).
        """
        started = datetime.now(UTC)
        preparation = await self.kind.preparation(
            self.config, self.run.settings, sandbox_id=request.sandbox_id
        )
        if request.in_sandbox:
            # MDA opened the sandbox before preparing; the thread is bound to it from here on.
            await self._sandbox.bind(request)
        await self._report_stale_workspace()
        if events := _carried_events(self.config):
            # The events this run was started to carry are its input: it holds them now.
            await ThreadInbox(self.run.thread_id).receive(events=events)
        state = call_state(request, self.kind.restore)

        async def prepared(runtime: Runtime) -> Prepared:
            result = await run_hooks(preparation, "before_agent", state, runtime)
            await self._sandbox.finish_git_identity()
            # A sandbox the backend connected to, such as a bridged machine, got a fresh token.
            expires_at = proxy_token_set_since(self.run.thread_id, started)
            return Prepared(
                **result.model_dump(),
                github_proxy=None
                if expires_at is None
                else GitHubProxyRefreshed(expires_at=expires_at),
                notices=[],
                sender=self.kind.sender(self.config, self.run.settings),
            )

        emitted: list[object] = []
        result = await in_graph_context(
            {**self.config, "run_name": "before_agent"}, prepared, events=emitted
        )
        return result.model_copy(update={"events": [jsonable(e) for e in emitted]})

    async def _report_stale_workspace(self) -> None:
        if stale := take_stale_boot(self.run.thread_id):
            await StaleWorkspaceReporter.report(self.run.thread_id, stale)

    def _as_invocation(self, invocation_id: str | None) -> None:
        """Record as the invocation a report names, which need not be this token's."""
        if invocation_id is not None:
            self.config = self._config(invocation_id)

    async def run_started(self, request: RunStarted) -> Done:
        """Record the run's start: its transcript turn, and what it runs as."""
        # A later run sends what an earlier one left undelivered, with its own token, whose
        # sender is not the earlier invocation's: the report names that one's.
        carried = request.invocation_id not in (
            None,
            RunConfig.from_config(self.config).invocation_id,
        )
        self._as_invocation(request.invocation_id)
        for notice in request.notices:
            queue_run_notice(self.run.thread_id, notice.kind, notice.data)

        async def record(_state: dict[str, object], _runtime: Runtime) -> None:
            await self.kind.started(self.config, self.run.settings, request, carried=carried)

        await self._reporting(
            request, "run_started", record, messages=messages_from_dict(request.humans)
        )
        await self.activity.request_started(request, carried=carried)
        return Done()

    def _tool_recorders(self) -> list[RecordsRemoteTools]:
        return [m for m in self.kind.reporters if isinstance(m, RecordsRemoteTools)]

    async def tool_started(self, request: ToolCallReport) -> Done:
        tool_call = ToolCall(
            name=request.name, args=dict(request.arguments), id=request.tool_call_id
        )

        async def record(_state: dict[str, object], _runtime: Runtime) -> None:
            for recorder in self._tool_recorders():
                await recorder.atool_started(tool_call, request.message_id)

        await self._reporting(request, "tool_started", record)
        return Done()

    async def tool_result(self, request: ToolResultReport) -> Done:
        [result] = messages_from_dict([request.result])
        if not isinstance(result, ToolMessage):
            raise ValueError("A tool result must be a tool message")

        async def record(_state: dict[str, object], _runtime: Runtime) -> None:
            for recorder in self._tool_recorders():
                await recorder.atool_finished(result)

        await self._reporting(request, "tool_result", record)
        turn = self.run.settings.agent
        if (
            request.name in _EDITS_WORK
            and result.status != "error"
            and turn is not None
            and turn.goal_mode
        ):
            await ThreadGoals.edited(self.run.thread_id)
        await self.activity.tool_executed(request, is_error=result.status == "error")
        return Done()

    async def operation(self, request: RunOperation) -> OperationResult:
        """Do the work of one of the run's tools: its arguments in, its result out."""
        if not self.run.granted(request.name, request.grant):
            return OperationResult(
                content=f"{request.name} is not one of this run's operations", status="error"
            )
        cfg = RunConfig.from_config(self.config)
        client = PluginChatTools.of_run(cfg, self.run.thread_id)
        offered = (
            next((t for t in client.tools if t.name == request.name), None) if client else None
        )
        result = await Operation(
            offered or await self.kind.tool(self.config, request.name), request
        ).run(
            config=self.config,
            state=self._operation_state(request),
            guards=ShellGuards(self.kind.shell_guards),
            client=client,
            ready=self._ready,
            notify=not self.kind.reviews,
        )
        if request.name not in _JOINS_TASK or result.status == "error":
            return result
        joined = await self._task_context_message()
        if joined is None:
            return result
        return result.model_copy(update={"state": {**result.state, JOINED_TASK_CONTEXT: joined}})

    async def _task_context_message(self) -> str | None:
        """The context of the task the run's thread belongs to now, if it belongs to one."""
        try:
            coordination = await TaskCoordinationMiddleware.for_thread(self.run.thread_id)
        except Exception:
            logger.warning(
                "Could not read the task the thread joined",
                extra={"agent_thread_id": self.run.thread_id},
                exc_info=True,
            )
            return None
        return coordination.context_message() if coordination is not None else None

    def _operation_state(self, request: RunOperation) -> dict[str, object]:
        """The graph state an operation reads, as the call carries it."""
        state: dict[str, object] = {
            key: decode_state(value) for key, value in request.state.items()
        }
        if request.messages is not None:
            state["messages"] = messages_from_dict(request.messages)
        self.kind.restore(state)
        return state

    async def _tool_set(self) -> ToolSet:
        if self.run.settings.tools is None:
            raise ToolSetNotKeptError("This run started before the backend kept its tools")
        return await ToolSet.kept(self.run.settings.tools)

    async def tool_specs(self, request: ToolNames) -> ToolSpecs:
        """The specs of the tools the run's catalog names, with the run's grant of each."""
        specs = (await self._tool_set()).specs(request.names)
        return ToolSpecs(
            tools=[tool.model_copy(update={"grant": self.run.grant(tool.name)}) for tool in specs]
        )

    async def tool_set(self, _request: RunRequest) -> KeptTools:
        """Every tool the run may call, as its token names the set, for the run to search
        and resolve itself; the grant of each it calls it still reads from here."""
        kept = await self._tool_set()
        # What the token names it by: digesting a set again would cost what it saves.
        digest = cast(str, self.run.settings.tools)
        return KeptTools(digest=digest, tools=kept.tools, excluded=kept.excluded)

    async def search_tools(self, request: ToolSearch) -> ToolMatches:
        """The run's tools that best match what the model looks for."""
        matches = (await self._tool_set()).search(request.query)
        return ToolMatches(matches=[ToolIndex.match(tool) for tool in matches])

    async def resolve_tool(self, request: ToolLookup) -> ResolvedTool:
        """The tool the run calls as ``request.name``, with the run's grant of it."""
        tool = (await self._tool_set()).resolve(request.name)
        if tool is None:
            return ResolvedTool()
        return ResolvedTool(tool=tool.model_copy(update={"grant": self.run.grant(tool.name)}))

    async def check_shell(self, request: ShellCheck) -> ShellVerdict:
        """Whether the push and pull request guards let ``request``'s command run as it is."""
        guards = ShellGuards(self.kind.shell_guards)
        return await self.in_run("check_shell", lambda: guards.verdict(request))

    async def sandbox_unreachable(self, request: SandboxGone) -> Done:
        """A tool MDA ran in the sandbox found it gone; the run ends once the person knows."""
        _forget_files(self.run)
        await self._sandbox.tell_gone(sandbox_id=request.sandbox_id, reason=request.reason)
        return Done()

    async def route_model(self, request: Routed) -> Route:
        """Deprecated: an MDA deployment from before it classified routes itself asks here."""
        selection = await classify_route(messages_from_dict(request.humans))
        return Route(route=selection.route, persist=selection.persist)

    async def reminders(self, request: PersonMessage) -> Reminders:
        """The reminders a person's message calls for."""
        [message] = messages_from_dict([request.message])
        found = await Reminder.called_for(message)
        return Reminders(messages=dump_messages(as_messages(found)))

    async def model_choice(self, request: ChosenModel) -> ModelSpec:
        """A model a person asked for. MDA now answers this itself from the catalog's
        models; kept for one release, for MDA deployments that still ask."""
        fallback_model_id = ENV.LLM_FALLBACK_MODEL_ID.optional()
        return chosen_model(request.model_id, request.effort, fallback_model_id=fallback_model_id)

    async def task_context(self, _request: RunRequest) -> TaskContext:
        """Deprecated: an MDA deployment from before ``LoopRules.task_context`` asks here."""

        async def read() -> TaskContext:
            coordination = await TaskCoordinationMiddleware.for_thread(self.run.thread_id)
            return TaskContext() if coordination is None else coordination.task_context()

        return await in_graph_context(
            {**self.config, "run_name": "task_context"}, lambda _runtime: read()
        )

    async def refresh_github_proxy(self, request: RefreshGitHubProxy) -> GitHubProxyRefreshed:
        cfg = RunConfig.from_config(self.config)
        if not cfg.github_linked:
            return GitHubProxyRefreshed()
        turn = self.run.settings.agent
        if turn is not None and turn.coordinator_thread_id is not None:
            # A task worker works in its coordinator's sandbox, whose token keeps the
            # coordinator's own scope.
            expires_at = await refresh_thread_github_proxy(
                turn.coordinator_thread_id,
                workspace_slug=cfg.workspace_slug,
                force_refresh=request.force_refresh,
            )
            return GitHubProxyRefreshed(expires_at=expires_at)
        expires_at = await self._sandbox.refresh_github_proxy(force_refresh=request.force_refresh)
        return GitHubProxyRefreshed(expires_at=expires_at)

    async def sandbox_proxy(self, request: SandboxProxyRequest) -> SandboxProxy:
        """The backend's part of the proxy config MDA writes on the run's sandbox."""
        cfg = RunConfig.from_config(self.config)
        managed = await self._sandbox.managed()
        if managed is None or managed.github_proxy is None:
            # The sandbox's capability goes only to a run that writes its proxy config.
            raise RefusedCallError("The backend writes this thread's sandbox proxy config")
        config = await mda_proxy_config(
            managed.thread_id or self.run.thread_id,
            request.sandbox_id,
            workspace_slug=cfg.workspace_slug,
        )
        return SandboxProxy(config=config)

    async def reply_on_behalf(self, request: ReplyOnBehalf) -> Done:
        """Post the model's final answer for it, after it ignored the reply tool."""
        state = call_state(request, self.kind.restore)

        async def post() -> None:
            result = await send_slack_reply(request.text, "final", state=state)
            logger.warning(
                "Posted the model's final message on its behalf after it ignored the reply tool",
                extra={"reply_fallback_result": result},
            )

        await self.in_run("reply_on_behalf", post)
        return Done()

    async def goal(self, request: GoalRead) -> Goal:
        """The thread's goal, with what will wake the thread again when asked."""
        kept = await ThreadGoals.current(self.run.thread_id)
        if kept is None:
            return Goal()
        return Goal(
            state=kept.state,
            reason=kept.reason,
            objective=None if kept.objective is None else run_context("goal", kept.objective),
            ask=kept.ask,
            updated_at_ms=kept.updated_at_ms,
            waits=[wait.kind for wait in await ThreadGoals.waits(self.run.thread_id)]
            if request.waits
            else [],
        )

    async def set_goal(self, request: GoalChange) -> Done:
        await ThreadGoals.change(
            self.run.thread_id, request.state, request.reason, request=request.request
        )
        return Done()

    async def slack_move(self, request: SlackMoveRequest) -> SlackMovePlan:
        """Check a move of the run's own Slack conversation into a new Slack thread."""
        return await self.in_run("slack_move", lambda: prepare_channel_move(request))

    async def slack_moved(self, request: SlackMoved) -> SlackMoveResult:
        """Record where the run's thread went once MDA handed it to the new Slack thread."""
        return await self.in_run("slack_moved", lambda: record_channel_move(request))

    async def run_ended(self, request: RunEnded) -> Done:
        """Record the run's end: its transcript turn and its usage."""
        self._as_invocation(request.invocation_id)
        reporters = self.kind.reporters

        async def ended(state: dict[str, object], runtime: Runtime) -> None:
            await run_hooks(reporters, "after_agent", state, runtime)

        await self._reporting(
            request, "run_ended", ended, messages=messages_from_dict(request.replies)
        )
        return Done()

    async def model_failed(self, request: ModelFailed) -> Done:
        """Leave why a model call failed the run on its thread, for the failure reply."""
        await THREADS.update(
            self.run.thread_id,
            {
                LAST_MODEL_ERROR_KEY: {
                    "run_id": self.run_id,
                    "error_type": request.error_type,
                    "code": request.code,
                }
            },
        )
        return Done()

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

    async def _incident(self) -> IncidentSession:
        incident = await self.kind.incident(self.config)
        if incident is None:
            raise RefusedCallError("This run investigates no incident")
        return incident

    async def incident_context(self, request: Routed) -> IncidentContext:
        """The incident the run investigates, as the next model call tells the model."""
        humans = [m.text for m in messages_from_dict(request.humans)]

        async def read() -> IncidentContext:
            incident = await self._incident()
            instructions = await incident.context(humans)
            return IncidentContext(
                instructions=run_context("incident", instructions) if instructions.strip() else ""
            )

        return await in_graph_context(
            {**self.config, "run_name": "incident_context"}, lambda _runtime: read()
        )

    async def record_evidence(self, request: ToolEvidence) -> EvidenceReply:
        """An incident run's tool result, recorded as evidence the result then cites."""
        content = request.content
        result = ToolMessage(
            content=content
            if isinstance(content, str)
            else [block for block in content if isinstance(block, str | dict)],
            artifact=request.artifact,
            status=request.status,
            tool_call_id="evidence",
            name=request.tool,
        )

        async def record() -> EvidenceReply:
            incident = await self._incident()
            observed = await incident.observe(
                result, {"name": request.tool, "args": request.arguments}
            )
            cited = observed.content
            return EvidenceReply(
                content=cited if isinstance(cited, str) else _json_list.validate_python(cited)
            )

        return await in_graph_context(
            {**self.config, "run_name": "record_evidence"}, lambda _runtime: record()
        )

    async def browser(self, request: BrowserReport) -> Done:
        await ThreadBrowser(browser=request.browser, opened=request.opened).report(
            self.run.thread_id
        )
        return Done()

    async def reports(self, request: Reports) -> ReportsTaken:
        """Record the run's reports and notices in order, each key once.

        Stops at the first it cannot take now, so none is recorded before one made
        before it: one that failed, or one another delivery is still recording. The
        batch's keys are claimed together, what its reports queued for the transcript
        is written once they are all recorded, and their replies are kept together.
        """
        receipts = ReportReceipts(self.run.thread_id)
        claims = await receipts.claim(
            item.key for item in request.items if REPORTED[item.endpoint] != "idempotent"
        )
        replies: list[dict[str, JsonValue]] = []
        taken: dict[str, tuple[Claimed, dict[str, JsonValue]]] = {}
        refused: str | None = None
        flushes: dict[str | None, RunnableConfig] = {}
        batch = _batch_flushes.set(flushes)
        try:
            for item in request.items:
                claim = claims.get(item.key) if REPORTED[item.endpoint] != "idempotent" else None
                if isinstance(claim, Recording):
                    break
                if isinstance(claim, Recorded):
                    replies.append(claim.reply)
                    continue
                if item.key in taken:
                    replies.append(taken[item.key][1])
                    continue
                try:
                    with traced_under(item.trace):
                        answered = await TokenRun.answer(
                            self.kind, self.run, item.endpoint, item.request.model_dump(mode="json")
                        )
                except Exception as exc:
                    logger.exception(
                        "An MDA report was not recorded",
                        extra={"endpoint": item.endpoint, "report_key": item.key},
                    )
                    refused = f"{type(exc).__name__}: {exc}"
                    break
                reply = _json_object.validate_python(answered)
                if claim is not None:
                    taken[item.key] = (claim, reply)
                replies.append(reply)
            await self._flush_reporters(flushes.values())
        except BaseException:
            # Cancelled: what was recorded keeps its reply, and the rest is given up.
            await asyncio.shield(self._settle(receipts, claims, taken))
            raise
        finally:
            _batch_flushes.reset(batch)
        await self._settle(receipts, claims, taken)
        return ReportsTaken(replies=replies, refused=refused)

    async def _flush_reporters(self, configs: Iterable[RunnableConfig]) -> None:
        """Write what a batch's reports queued, once for each run they were about."""
        carried = [m for m in self.kind.reporters if isinstance(m, CarriedAcrossProcesses)]

        async def flushed(runtime: Runtime) -> None:
            for middleware in carried:
                await middleware.aflush_run(runtime)

        for config in configs if carried else ():
            try:
                await in_graph_context({**config, "run_name": "reports_flushed"}, flushed)
            except Exception:
                # The reports are recorded; their run's writer goes on writing them.
                logger.exception("Writing what MDA reports queued failed")

    @staticmethod
    async def _settle(
        receipts: ReportReceipts,
        claims: Mapping[str, Claim],
        taken: Mapping[str, tuple[Claimed, dict[str, JsonValue]]],
    ) -> None:
        """Keep the replies of the reports recorded, and give up the keys of the rest."""
        await receipts.keep([(claim, key, reply) for key, (claim, reply) in taken.items()])
        await receipts.release(
            claim
            for key, claim in claims.items()
            if isinstance(claim, Claimed) and key not in taken
        )


# Every call a run makes, answered from its token and request.
CALLS: Final = Handlers[TokenRun]()
CALLS.serve(PREPARE, TokenRun.prepare)
CALLS.serve(BROWSER, TokenRun.browser)
CALLS.serve(SKILLS, TokenRun.skills)
CALLS.serve(TOOL_SPECS, TokenRun.tool_specs)
CALLS.serve(SEARCH, TokenRun.search_tools)
CALLS.serve(RESOLVE, TokenRun.resolve_tool)
CALLS.serve(TOOL_SET, TokenRun.tool_set)
CALLS.serve(INBOX, TokenRun.inbox)
CALLS.serve(INBOX_RECEIVED, TokenRun.inbox_received)
CALLS.serve(MODEL_RESULT, TokenRun.model_result)
CALLS.serve(TOOL_STARTED, TokenRun.tool_started)
CALLS.serve(TOOL_RESULT, TokenRun.tool_result)
CALLS.serve(ROUTE_MODEL, TokenRun.route_model)
CALLS.serve(REMINDERS, TokenRun.reminders)
CALLS.serve(MODEL_CHOICE, TokenRun.model_choice)
CALLS.serve(TASK_CONTEXT, TokenRun.task_context)
CALLS.serve(INCIDENT_CONTEXT, TokenRun.incident_context)
CALLS.serve(REFRESH_GITHUB_PROXY, TokenRun.refresh_github_proxy)
CALLS.serve(SANDBOX_PROXY, TokenRun.sandbox_proxy)
CALLS.serve(REPLY_ON_BEHALF, TokenRun.reply_on_behalf)
CALLS.serve(GOAL, TokenRun.goal)
CALLS.serve(SET_GOAL, TokenRun.set_goal)
CALLS.serve(SLACK_MOVE, TokenRun.slack_move)
CALLS.serve(SLACK_MOVED, TokenRun.slack_moved)
CALLS.serve(RUN_STARTED, TokenRun.run_started)
CALLS.serve(RUN_ENDED, TokenRun.run_ended)
CALLS.serve(MODEL_FAILED, TokenRun.model_failed)
CALLS.serve(STEP_LIMIT_REACHED, TokenRun.step_limit_reached)
CALLS.serve(SETTLE_REVIEW_CHECK, TokenRun.settle_review_check)
CALLS.serve(FINISH_SCOUT_PLAN, TokenRun.finish_scout_plan)
CALLS.serve(REPORTS, TokenRun.reports)
CALLS.serve(OPERATION, TokenRun.operation)
CALLS.serve(CHECK_SHELL, TokenRun.check_shell)
CALLS.serve(RECORD_EVIDENCE, TokenRun.record_evidence)
CALLS.serve(SANDBOX_UNREACHABLE, TokenRun.sandbox_unreachable)
CALLS.serve(file_api.LS, file_calls.files_ls)
CALLS.serve(file_api.READ, file_calls.files_read)
CALLS.serve(file_api.WRITE, file_calls.files_write)
CALLS.serve(file_api.EDIT, file_calls.files_edit)
CALLS.serve(file_api.DELETE, file_calls.files_delete)
CALLS.serve(file_api.GLOB, file_calls.files_glob)
CALLS.serve(file_api.GREP, file_calls.files_grep)
CALLS.serve(file_api.EXECUTE, file_calls.files_execute)
CALLS.serve(file_api.UPLOAD, file_calls.files_upload)
CALLS.serve(file_api.DOWNLOAD, file_calls.files_download)
