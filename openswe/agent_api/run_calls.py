"""The calls an Open SWE graph's MDA run makes on the backend, each answered from the
run's token and the request alone, on whichever replica it lands.

MDA keeps the model loop, its checkpoints, every tool call, and the middleware around
them; the backend answers the typed API both sides import (``runtime_api``):

- ``prepare`` readies the run's turn: its prompt, model and route, from what the token
  says starting the run decided (``RunSettings``), without reaching the sandbox MDA keeps.
  What the turn's start records (its transcript turn, the thread's model and usage) is
  the ``run_started`` report, off the run's path
- the inbox and skills, and the named calls MDA's middleware makes around a model call
  (``route_model``, ``model_choice``, ``task_context``, ``incident_context``,
  ``refresh_github_proxy``, ``reply_on_behalf``) and at the run's end (``run_ended``,
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
from collections.abc import Awaitable, Callable, Mapping, Sequence
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
from runtime_api.catalog import ModelSpec
from runtime_api.endpoint import Handlers
from runtime_api.loop import (
    BROWSER,
    FINISH_SCOUT_PLAN,
    INBOX,
    INBOX_RECEIVED,
    INCIDENT_CONTEXT,
    MODEL_CHOICE,
    MODEL_RESULT,
    PREPARE,
    REFRESH_GITHUB_PROXY,
    REPLY_ON_BEHALF,
    ROUTE_MODEL,
    RUN_ENDED,
    RUN_STARTED,
    SANDBOX_REPLACED,
    SANDBOX_UNREACHABLE,
    SETTLE_REVIEW_CHECK,
    STEP_LIMIT_REACHED,
    TASK_CONTEXT,
    TOOL_RESULT,
    TOOL_STARTED,
    BrowserReport,
    ChosenModel,
    Done,
    GitHubProxyRefreshed,
    Inbox,
    InboxItems,
    InboxRead,
    InboxReceipt,
    IncidentContext,
    ModelResult,
    Prepare,
    Prepared,
    ReplyOnBehalf,
    Report,
    Route,
    Routed,
    RunEnded,
    RunNotice,
    RunStarted,
    SandboxGone,
    ScoutPlanEnd,
    TaskContext,
    ToolCallReport,
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
from runtime_api.sandbox_proxy import SANDBOX_PROXY, SandboxProxy, SandboxProxyRequest
from runtime_api.skills import SKILLS, RunSkills
from runtime_api.tool_search import (
    RESOLVE,
    SEARCH,
    TOOL_SPECS,
    ResolvedTool,
    ToolLookup,
    ToolMatch,
    ToolMatches,
    ToolNames,
    ToolSearch,
    ToolSpecs,
)

from openswe.agent_api import file_calls
from openswe.agent_api.caller_trace import traced_under
from openswe.agent_api.operations import Operation
from openswe.agent_api.report_receipts import Recorded, Recording, ReportReceipts
from openswe.agent_api.run_parts import RunParts
from openswe.agent_api.run_sandbox import RunSandbox
from openswe.agent_api.run_start import chosen_model
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
from openswe.config import ENV
from openswe.github.proxy import proxy_token_set_since
from openswe.incidents.runtime import IncidentSession
from openswe.middleware.client_tools import ClientToolsMiddleware
from openswe.middleware.model_selection import classify_route
from openswe.middleware.notify_step_limit import notify_step_limit
from openswe.middleware.record_run_usage import RecordRunUsageMiddleware
from openswe.middleware.settle_review_check import settle_review_check
from openswe.middleware.stale_workspace import stale_workspace_notice
from openswe.middleware.task_coordination import TaskCoordinationMiddleware
from openswe.middleware.transcript import TranscriptMiddleware, queue_run_notice, within_namespace
from openswe.review_scout.graph import finish_scout_plan
from openswe.run_config import RunConfig
from openswe.sandboxes.browsers import ThreadBrowser
from openswe.sandboxes.lifecycle import mda_proxy_config, take_stale_boot
from openswe.slack.tools.reply import send_slack_reply
from openswe.threads.inbox import ThreadInbox
from openswe.utils.thread_reads import shared_thread_reads

logger = logging.getLogger(__name__)

_MODEL_OBSERVERS: Final = (TranscriptMiddleware, RecordRunUsageMiddleware)
_json_object = TypeAdapter(dict[str, JsonValue])
_json_list = TypeAdapter(list[JsonValue])


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

    The graph's ``RunParts`` build what the call needs.
    """

    def __init__(self, parts: RunParts, run: RemoteRun, run_id: str | None) -> None:
        self.parts = parts
        self.run = run
        self.run_id = run_id
        self.config = self._config()
        self._files: BackendProtocol | None = None

    @classmethod
    async def answer(
        cls, parts: RunParts, run: RemoteRun, endpoint: str, raw: JsonValue
    ) -> JsonValue:
        """Answer one call the MDA run makes (``runtime_api``)."""
        raw_id = raw.get("run_id") if isinstance(raw, Mapping) else None
        call = cls(parts, run, raw_id if isinstance(raw_id, str) else None)
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
        return RunSandbox(self.config, reviews=self.parts.reviews)

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
        call answers, so the next call may reach another process.
        """
        state: dict[str, object] = {**held, "messages": list(messages)}
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

    async def prepare(self, request: Prepare) -> Prepared:
        """Ready the run's turn from its token and the conversation.

        Nothing here waits on the sandbox MDA keeps: the run readies the sandbox it opened, and
        its loop keeps the sandbox's GitHub token fresh (``refresh_github_proxy``).
        """
        started = datetime.now(UTC)
        preparation = await self.parts.preparation(
            self.config, self.run.settings, sandbox_id=request.sandbox_id
        )
        if request.in_sandbox:
            # MDA opened the sandbox before preparing; the thread is bound to it from here on.
            await self._sandbox.bind(request)
        notices = self._turn_notices()
        if events := _carried_events(self.config):
            # The events this run was started to carry are its input: it holds them now.
            await ThreadInbox(self.run.thread_id).receive(events=events)
        state = call_state(request, self.parts.restore)

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
                notices=notices,
                sender=self.parts.sender(self.config, self.run.settings),
            )

        emitted: list[object] = []
        result = await in_graph_context(
            {**self.config, "run_name": "before_agent"}, prepared, events=emitted
        )
        return result.model_copy(update={"events": [jsonable(e) for e in emitted]})

    def _turn_notices(self) -> list[RunNotice]:
        """What the turn's transcript shows from binding its sandbox: a stale workspace image."""
        stale = take_stale_boot(self.run.thread_id)
        data = None if stale is None else stale_workspace_notice(self.run.thread_id, stale)
        return [] if data is None else [RunNotice(kind="workspace_stale", data=data)]

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
            await self.parts.started(self.config, self.run.settings, request, carried=carried)

        await self._reporting(
            request, "run_started", record, messages=messages_from_dict(request.humans)
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

    async def tool_result(self, request: ToolResultReport) -> Done:
        [result] = messages_from_dict([request.result])
        if not isinstance(result, ToolMessage):
            raise ValueError("A tool result must be a tool message")

        async def record(_state: dict[str, object], _runtime: Runtime) -> None:
            for recorder in self._tool_recorders():
                await recorder.atool_finished(result)

        await self._reporting(request, "tool_result", record)
        return Done()

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
        return await Operation(
            offered or await self.parts.tool(self.config, request.name), request
        ).run(
            config=self.config,
            state=self._operation_state(request),
            guards=ShellGuards(self.parts.shell_guards),
            client=client,
            ready=self._ready,
            notify=not self.parts.reviews,
        )

    def _operation_state(self, request: RunOperation) -> dict[str, object]:
        """The graph state an operation reads, as the call carries it."""
        state: dict[str, object] = {
            key: decode_state(value) for key, value in request.state.items()
        }
        if request.messages is not None:
            state["messages"] = messages_from_dict(request.messages)
        self.parts.restore(state)
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

    async def search_tools(self, request: ToolSearch) -> ToolMatches:
        """The run's tools that best match what the model looks for."""
        matches = (await self._tool_set()).search(request.query, request.limit)
        return ToolMatches(
            matches=[
                ToolMatch(
                    name=tool.name,
                    description=tool.description,
                    parameters=tool.parameters,
                    code_name=tool.code_name,
                )
                for tool in matches
            ]
        )

    async def resolve_tool(self, request: ToolLookup) -> ResolvedTool:
        """The tool the run calls as ``request.name``, with the run's grant of it."""
        tool = (await self._tool_set()).resolve(request.name)
        if tool is None:
            return ResolvedTool()
        return ResolvedTool(tool=tool.model_copy(update={"grant": self.run.grant(tool.name)}))

    async def check_shell(self, request: ShellCheck) -> ShellVerdict:
        """Whether the push and pull request guards let ``request``'s command run as it is."""
        guards = ShellGuards(self.parts.shell_guards)
        return await self.in_run("check_shell", lambda: guards.verdict(request))

    async def sandbox_replaced(self, _request: RunRequest) -> Done:
        """Connect again to the thread's sandbox, which MDA replaced with a fresh one."""
        await self._sandbox.reconnect()
        return Done()

    async def sandbox_unreachable(self, request: SandboxGone) -> Done:
        """A tool MDA ran in the sandbox found it gone; the run ends once the person knows."""
        await self._sandbox.tell_gone(sandbox_id=request.sandbox_id, reason=request.reason)
        return Done()

    async def route_model(self, request: Routed) -> Route:
        """The route the person's latest request takes."""
        selection = await classify_route(messages_from_dict(request.humans))
        return Route(route=selection.route, persist=selection.persist)

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

        return await in_graph_context(
            {**self.config, "run_name": "task_context"}, lambda _runtime: read()
        )

    async def refresh_github_proxy(self, _request: RunRequest) -> GitHubProxyRefreshed:
        cfg = RunConfig.from_config(self.config)
        if cfg.auth_provider == "langsmith":
            return GitHubProxyRefreshed()
        # Where MDA may write the sandbox's proxy, a review's narrowing decides that it does
        # not, as it does for the catalog. Otherwise it keeps the thread's own scope.
        expires_at = await self._sandbox.refresh_github_proxy(
            narrowed=ENV.MDA_GITHUB_PROXY_CONNECTION.is_set()
        )
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
        state = call_state(request, self.parts.restore)

        async def post() -> None:
            result = await send_slack_reply(request.text, "final", state=state)
            logger.warning(
                "Posted the model's final message on its behalf after it ignored the reply tool",
                extra={"reply_fallback_result": result},
            )

        await self.in_run("reply_on_behalf", post)
        return Done()

    async def run_ended(self, request: RunEnded) -> Done:
        """Record the run's end: its transcript turn and its usage."""
        self._as_invocation(request.invocation_id)
        reporters = self.parts.reporters

        async def ended(state: dict[str, object], runtime: Runtime) -> None:
            await run_hooks(reporters, "after_agent", state, runtime)

        await self._reporting(
            request, "run_ended", ended, messages=messages_from_dict(request.replies)
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
        incident = await self.parts.incident(self.config)
        if incident is None:
            raise RefusedCallError("This run investigates no incident")
        return incident

    async def incident_context(self, request: Routed) -> IncidentContext:
        """The incident the run investigates, as the next model call tells the model."""
        humans = [m.text for m in messages_from_dict(request.humans)]

        async def read() -> IncidentContext:
            incident = await self._incident()
            return IncidentContext(instructions=await incident.context(humans))

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
                    answered = await TokenRun.answer(
                        self.parts, self.run, item.endpoint, item.request.model_dump(mode="json")
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


# Every call a run makes, answered from its token and request.
CALLS: Final = Handlers[TokenRun]()
CALLS.serve(PREPARE, TokenRun.prepare)
CALLS.serve(BROWSER, TokenRun.browser)
CALLS.serve(SKILLS, TokenRun.skills)
CALLS.serve(TOOL_SPECS, TokenRun.tool_specs)
CALLS.serve(SEARCH, TokenRun.search_tools)
CALLS.serve(RESOLVE, TokenRun.resolve_tool)
CALLS.serve(INBOX, TokenRun.inbox)
CALLS.serve(INBOX_RECEIVED, TokenRun.inbox_received)
CALLS.serve(MODEL_RESULT, TokenRun.model_result)
CALLS.serve(TOOL_STARTED, TokenRun.tool_started)
CALLS.serve(TOOL_RESULT, TokenRun.tool_result)
CALLS.serve(ROUTE_MODEL, TokenRun.route_model)
CALLS.serve(MODEL_CHOICE, TokenRun.model_choice)
CALLS.serve(TASK_CONTEXT, TokenRun.task_context)
CALLS.serve(INCIDENT_CONTEXT, TokenRun.incident_context)
CALLS.serve(REFRESH_GITHUB_PROXY, TokenRun.refresh_github_proxy)
CALLS.serve(SANDBOX_PROXY, TokenRun.sandbox_proxy)
CALLS.serve(REPLY_ON_BEHALF, TokenRun.reply_on_behalf)
CALLS.serve(RUN_STARTED, TokenRun.run_started)
CALLS.serve(RUN_ENDED, TokenRun.run_ended)
CALLS.serve(STEP_LIMIT_REACHED, TokenRun.step_limit_reached)
CALLS.serve(SETTLE_REVIEW_CHECK, TokenRun.settle_review_check)
CALLS.serve(FINISH_SCOUT_PLAN, TokenRun.finish_scout_plan)
CALLS.serve(REPORTS, TokenRun.reports)
CALLS.serve(OPERATION, TokenRun.operation)
CALLS.serve(CHECK_SHELL, TokenRun.check_shell)
CALLS.serve(RECORD_EVIDENCE, TokenRun.record_evidence)
CALLS.serve(SANDBOX_REPLACED, TokenRun.sandbox_replaced)
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
