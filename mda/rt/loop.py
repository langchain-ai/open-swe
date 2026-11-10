"""Open SWE's middleware around each model call, run here as the catalog's ``LoopRules`` say.

Before each call it ends the run where a rule says to, adds the thread's task
context, keeps the sandbox's
GitHub proxy fresh, picks the call's route and model, and assembles its system
prompt and tool list. A thread's route is classified here, once, alongside the run's
preparation (``rt.routing``). After an answer that calls no tool, it
asks again for the reply or CLI result the run owes. When the run ends, it asks
for the graph's own end-of-run work and reports the end, neither of which the run
waits for.

The backend answers only what needs its data or credentials, one named call each
(``runtime_api.loop``). Graph state the loop reads or writes is the backend's held
state (``open_swe_state``), which preparation fills.
"""

import asyncio
import json
import logging
import uuid
from collections.abc import Awaitable, Collection, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Final
from xml.etree import ElementTree

from langchain_core.messages import (
    AIMessage,
    AnyMessage,
    BaseMessage,
    HumanMessage,
    ToolMessage,
    messages_to_dict,
)
from langgraph.config import get_stream_writer
from pydantic import JsonValue, TypeAdapter
from runtime_api.catalog import (
    CLIENT_CALL_PENDING,
    JOINED_TASK_CONTEXT,
    CliResultRules,
    LoopRules,
    Nudging,
    ReplyRules,
    RunModels,
    TaskContext,
    WorkStateRules,
    kept_route,
)
from runtime_api.endpoint import call
from runtime_api.loop import (
    FINISH_SCOUT_PLAN,
    GOAL,
    INCIDENT_CONTEXT,
    REFRESH_GITHUB_PROXY,
    REPLY_ON_BEHALF,
    RUN_ENDED,
    SET_GOAL,
    SETTLE_REVIEW_CHECK,
    STEP_LIMIT_REACHED,
    BeforeModel,
    GitHubProxyRefreshed,
    Goal,
    GoalChange,
    GoalRead,
    ModelCall,
    PhaseResult,
    Prepared,
    RefreshGitHubProxy,
    ReplyOnBehalf,
    Routed,
    RunEnded,
    ScoutPlanEnd,
)
from runtime_api.model_errors import is_model_failure_reply
from runtime_api.models import model_for
from runtime_api.operations import RunRequest
from runtime_api.provenance import Provenance

from rt.backend import (
    BackendCallError,
    OpenSweBackend,
    RunLocal,
    harness_message,
    human_messages,
    run_call,
)
from rt.routing import Route, RouteClassifier
from rt.tools import STATE_KEY, run_id

logger = logging.getLogger(__name__)

# What ``ModelCallLimitMiddleware`` ends a run with once it reaches its limit.
_LIMIT_MARKER: Final = "Model call limits exceeded"
_SUMMARIZATION_EVENT: Final = "_summarization_event"
_GOAL_SEEN: Final = "goal_seen_ms"
_GOAL: Final = "goal"
_STALLED_TURNS: Final = 3
_EMPTY_TURNS: Final = "goal_empty_turns"
_FAILING_TURNS: Final = "goal_failing_turns"
_FAILED_COMMAND: Final = "[Command failed with exit code"
# Held state: when the sandbox's GitHub token next needs replacing. It lives in the thread's
# checkpoint, so it holds for the next run on whichever backend replica serves it.
_GITHUB_PROXY_DUE: Final = "github_proxy_refresh_at"
# A token is replaced this long before it expires. The backend never hands out one with
# less left, so a replacement is not due again at once.
_GITHUB_PROXY_WINDOW: Final = timedelta(minutes=10)
# A thread whose sandbox has no GitHub proxy is asked again after this long.
_GITHUB_PROXY_RECHECK: Final = timedelta(minutes=30)
# A refresh that failed is tried again after this long, so a failure that keeps happening
# does not hold up every model call.
_GITHUB_PROXY_RETRY: Final = timedelta(minutes=1)
_json_dicts = TypeAdapter(list[dict[str, JsonValue]])
# Starts the id of each message that tells the model its role in the thread's task.
_ROLE: Final = "task-role:"
# Starts the id of the message holding what the backend worked out for this run.
_RUN_CONTEXT: Final = "run-context:"
# Starts the id of the message holding an incident run's instructions for its next call.
_INCIDENT: Final = "incident-context:"


def content_text(content: object) -> str:
    """A message's content as text, its blocks' text joined."""
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return str(content)
    return " ".join(
        str(block.get("text", "")) if isinstance(block, Mapping) else str(block)
        for block in content
    )


def _texts(content: object) -> list[str]:
    values = content if isinstance(content, list) else [content]
    texts = (value.get("text") if isinstance(value, Mapping) else value for value in values)
    return [text for text in texts if isinstance(text, str)]


def _root(text: str, tag: str) -> ElementTree.Element | None:
    if f"<{tag}" not in text:
        return None
    try:
        root = ElementTree.fromstring(text)
    except ElementTree.ParseError:
        return None
    return root if root.tag == tag else None


def _reported_failure(message: ToolMessage) -> bool:
    if message.status == "error":
        return True
    payload = _json_payload(message)
    return isinstance(payload, dict) and payload.get("success") is False


def _json_payload(message: ToolMessage) -> object:
    try:
        return json.loads(content_text(message.content))
    except ValueError:
        return None


def _messages(state: Mapping[str, object]) -> list[AnyMessage]:
    messages = state.get("messages")
    return list(messages) if isinstance(messages, list) else []


def visible_messages(state: Mapping[str, object]) -> list[AnyMessage]:
    """The messages the model still sees: summarization drops those before its cutoff."""
    listed = _messages(state)
    event = state.get(_SUMMARIZATION_EVENT)
    cutoff = event.get("cutoff_index") if isinstance(event, Mapping) else None
    return listed[cutoff:] if isinstance(cutoff, int) and cutoff >= 0 else listed


def latest_with_id(messages: Sequence[BaseMessage], prefix: str) -> str | None:
    """The text of the last of ``messages`` whose id starts with ``prefix``."""
    for message in reversed(messages):
        if isinstance(message.id, str) and message.id.startswith(prefix):
            return content_text(message.content)
    return None


@dataclass(frozen=True)
class _GoalTurn:
    """What a Goal mode turn did, as Codex counts it toward blocking the goal."""

    empty: bool
    active: bool
    failing: bool


class _RunGuidance:
    """A run's bounded cache of guidance ids, keyed by the exact current text."""

    def __init__(self) -> None:
        self._keys: dict[str, str | None] = {}

    def _key(self, text: str) -> str | None:
        if "<dynamic-context" not in text:
            return None
        if text in self._keys:
            return self._keys[text]
        root = _root(text, "dynamic-context")
        key = root.get("id") if root is not None and root.get("kind") == "run" else None
        if len(text) <= 16_384:
            if len(self._keys) >= 512:
                self._keys.clear()
            self._keys[text] = key
        return key

    def latest(self, messages: Sequence[BaseMessage]) -> dict[str, str]:
        """The latest run guidance for each block id in the current conversation."""
        latest: dict[str, str] = {}
        for message in messages:
            if isinstance(message, HumanMessage):
                for text in _texts(message.content):
                    if key := self._key(text):
                        latest[key] = text
        return latest


def _held(state: Mapping[str, object]) -> Mapping[str, object]:
    held = state.get(STATE_KEY)
    return held if isinstance(held, Mapping) else {}


def _github_proxy_due(refreshed: GitHubProxyRefreshed, now: datetime) -> dict[str, JsonValue]:
    due = (
        refreshed.expires_at - _GITHUB_PROXY_WINDOW
        if refreshed.expires_at is not None
        else now + _GITHUB_PROXY_RECHECK
    )
    return {_GITHUB_PROXY_DUE: due.isoformat()}


def _moment(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


class _Turn:
    """What happened since the last thing a person said."""

    def __init__(self, messages: Sequence[AnyMessage], guards: Collection[str]) -> None:
        self.messages = list(messages)
        for index in range(len(self.messages) - 1, -1, -1):
            message = self.messages[index]
            provenance = Provenance.of(message)
            if isinstance(message, HumanMessage) and (
                provenance is None
                or provenance.human
                or (provenance.kind == "system" and provenance.sender not in guards)
            ):
                self.messages = self.messages[index + 1 :]
                break

    def calls(self) -> list[Mapping[str, object]]:
        return [
            tool_call
            for message in self.messages
            if isinstance(message, AIMessage)
            for tool_call in message.tool_calls
        ]

    def succeeded(self, call_ids: set[str]) -> bool:
        return any(
            isinstance(message, ToolMessage)
            and message.tool_call_id in call_ids
            and not _reported_failure(message)
            for message in self.messages
        )

    def results(self) -> list[ToolMessage]:
        return [message for message in self.messages if isinstance(message, ToolMessage)]

    def last_answer(self) -> str:
        for message in reversed(self.messages):
            if isinstance(message, AIMessage) and (text := content_text(message.content).strip()):
                return text
        return ""


class Asked:
    """The GitHub token refresh a model call waits on, asked for alongside the inbox."""

    def __init__(self, proxy: Awaitable[dict[str, JsonValue]]) -> None:
        self._proxy = asyncio.ensure_future(proxy)
        self._answered = False

    async def answers(self) -> dict[str, JsonValue]:
        self._answered = True
        return await self._proxy

    async def close(self) -> None:
        """Stop what is still asked; what failed unread is logged."""
        self._proxy.cancel()
        [result] = await asyncio.gather(self._proxy, return_exceptions=True)
        if not self._answered and isinstance(result, Exception):
            logger.warning("A call before the model call failed unread", exc_info=result)


class RunLoop:
    """What happens around each of the run's model calls, as its ``LoopRules`` say."""

    def __init__(self, backend: OpenSweBackend) -> None:
        self._backend = backend
        self._guidance = RunLocal(_RunGuidance)
        self._classifier = RouteClassifier()

    @property
    def rules(self) -> LoopRules:
        """The run's rules; a launched worker's own once it adopted its run, not its coordinator's."""
        return self._backend.run.catalog.loop

    @property
    def _models(self) -> RunModels | None:
        """The run's models; None calls the graph's own."""
        return self._backend.run.catalog.models

    def starting(self) -> dict[str, JsonValue]:
        """The held state a turn starts from: nothing asked again yet, and answers going
        where the turn was asked from."""
        # A task joined in an earlier run is in this run's catalog.
        update: dict[str, JsonValue] = {JOINED_TASK_CONTEXT: None}
        if self.rules.reply is not None:
            update.update(reply_surface=self.rules.reply.surface, reply_nudges=0)
        if self.rules.cli_result is not None:
            update["cli_result_nudges"] = 0
        return update

    @property
    def looks_at_answers(self) -> bool:
        return self.rules.reply is not None or self.rules.cli_result is not None

    def ask(self, state: Mapping[str, object]) -> Asked | None:
        """Start what the backend is asked before a model call whose answer the inbox does
        not change; None when the run stops here whatever the inbox holds."""
        held = _held(state)
        if self._stops(held):
            return None
        return Asked(self._github_proxy(held))

    def _stops(self, held: Mapping[str, object]) -> bool:
        return any(held.get(key) for key in self.rules.stop_when)

    async def before_model(
        self, state: Mapping[str, object], asked: Asked | None = None
    ) -> BeforeModel:
        held = _held(state)
        if self._stops(held) or self._awaits_client(state):
            return BeforeModel(jump_to="end")
        if asked is None:
            asked = Asked(self._github_proxy(held))
        try:
            update = await asked.answers()
        finally:
            await asked.close()
        stopped, reminder, objective = await self._goal(state, held, update)
        if stopped:
            return BeforeModel(update=update, jump_to="end")
        context = self._task_context(held)
        messages = _messages(state)
        visible = visible_messages(state)
        guidance = self._guidance.get(self._backend.run)
        visible_guidance = guidance.latest(visible)
        added: list[BaseMessage] = [
            harness_message(
                text,
                message_id=f"run-guidance:{uuid.uuid4()}",
                source="run_guidance",
                provenance=Provenance(kind="context", sender=f"run:{key}"),
            )
            for key, text in guidance.latest(messages).items()
            if key not in {"task-role", _GOAL} and text != visible_guidance.get(key)
        ]
        if objective is not None and objective != visible_guidance.get(_GOAL):
            added.append(
                harness_message(
                    objective,
                    message_id=f"goal:{uuid.uuid4()}",
                    source="goal",
                    provenance=Provenance(kind="context", sender="run:goal"),
                )
            )
        if context.instructions and context.instructions != latest_with_id(visible, _ROLE):
            added.append(
                harness_message(
                    context.instructions,
                    message_id=f"{_ROLE}{uuid.uuid4()}",
                    source="task_context",
                    provenance=Provenance(kind="context", sender="run:task-role"),
                )
            )
        run_context = latest_with_id(messages, _RUN_CONTEXT)
        if run_context is not None and run_context != latest_with_id(visible, _RUN_CONTEXT):
            added.append(
                harness_message(
                    run_context, message_id=f"{_RUN_CONTEXT}{uuid.uuid4()}", source="run_context"
                )
            )
        if context.message is not None and not any(
            content_text(message.content) == context.message for message in visible
        ):
            added.append(
                harness_message(
                    context.message,
                    message_id=f"task-context:{uuid.uuid4()}",
                    source="task_context",
                )
            )
        if self.rules.incident:
            # In a message, not the system prompt: they change from call to call, and a
            # changed system prompt misses the provider's cache for the whole conversation.
            incident = await call(
                self._backend,
                INCIDENT_CONTEXT,
                Routed(run_id=run_id(), humans=human_messages(messages)),
            )
            if incident.instructions and incident.instructions != latest_with_id(
                visible, _INCIDENT
            ):
                added.append(
                    harness_message(
                        incident.instructions,
                        message_id=f"{_INCIDENT}{uuid.uuid4()}",
                        source="incident_context",
                        provenance=Provenance(kind="context", sender="run:incident"),
                    )
                )
        route = await self._route(held, [*visible, *added])
        if route is not None:
            update.update(self._kept(route))
        current = {**held, **update}
        model = None if self._models is None else model_for(self._models, current)
        system_prompt, workspace_prompt, thread_prompt = self._system_prompt(current)
        if self.rules.client_prompt:
            system_prompt = f"{system_prompt}\n\n{self.rules.client_prompt}"
        return BeforeModel(
            update=update,
            messages=_json_dicts.validate_python(messages_to_dict(added)),
            call=ModelCall(
                system_prompt=system_prompt,
                workspace_prompt=workspace_prompt,
                thread_prompt=thread_prompt,
                hidden_tools=self._hidden(current),
                model=model,
                vision=None
                if self._models is None or (model is not None and model.reads_images)
                else self._models.vision,
                fallback=None if model is None else model.fallback,
                use_gateway=self._models is not None and self._models.use_gateway,
                reminder=reminder,
            ),
        )

    def _awaits_client(self, state: Mapping[str, object]) -> bool:
        """Whether the run waits on a call its client runs: its result is still the placeholder."""
        if not self.rules.client_tools:
            return False
        messages = state.get("messages")
        for message in reversed(messages if isinstance(messages, list) else []):
            if not isinstance(message, ToolMessage):
                return False
            if isinstance(message.artifact, dict) and message.artifact.get(CLIENT_CALL_PENDING):
                return True
        return False

    async def _github_proxy(self, held: Mapping[str, object]) -> dict[str, JsonValue]:
        """Put a new GitHub token on the sandbox once the one there is about to expire.

        The call is awaited, so the model's first command never runs on an expired
        token. A failure leaves the run going and the call is made again a minute later.
        """
        if not self.rules.refresh_github_proxy:
            return {}
        now = datetime.now(UTC)
        due = _moment(held.get(_GITHUB_PROXY_DUE))
        if due is not None and now < due:
            return {}
        try:
            refreshed = await call(
                self._backend, REFRESH_GITHUB_PROXY, RefreshGitHubProxy(run_id=run_id())
            )
        except Exception:
            logger.warning("Could not refresh the sandbox's GitHub token", exc_info=True)
            return {_GITHUB_PROXY_DUE: (now + _GITHUB_PROXY_RETRY).isoformat()}
        return _github_proxy_due(refreshed, now)

    def prepared(self, prepared: Prepared) -> dict[str, JsonValue]:
        """Held state that keeps the token preparation put on the sandbox until it is due."""
        if prepared.github_proxy is None or not self.rules.refresh_github_proxy:
            return {}
        return _github_proxy_due(prepared.github_proxy, datetime.now(UTC))

    def _task_context(self, held: Mapping[str, object]) -> TaskContext:
        """The thread's role and task: the catalog's, or the task it joined since."""
        context = self.rules.task_context or TaskContext()
        joined = held.get(JOINED_TASK_CONTEXT)
        if isinstance(joined, str) and joined:
            return context.model_copy(update={"message": joined})
        return context

    def classify_ahead(self, state: Mapping[str, object]) -> asyncio.Task[Route] | None:
        """Start classifying the turn's route while the run prepares, where the thread's
        held state leaves its route to the classifier."""
        if not self._classifies(_held(state)):
            return None
        return asyncio.ensure_future(self._classifier.classify(_messages(state)))

    def _classifies(self, held: Mapping[str, object]) -> bool:
        rules = self.rules.routing
        requested = held.get("requested_model")
        return (
            rules is not None
            and rules.mode == "auto"
            and not (isinstance(requested, str) and requested)
            and kept_route(held.get("model_route")) is None
            and held.get("model_route_attempted_for") != run_id()
        )

    async def routed(
        self,
        held: Mapping[str, object],
        messages: Sequence[BaseMessage],
        classifying: asyncio.Task[Route] | None,
    ) -> dict[str, JsonValue]:
        """The held state the turn's route sets as the run starts, the routed model's id and
        effort with it: the turn's start reports them, and its replies are signed with them.

        ``held`` is the state the run's preparation left, which may have changed what
        ``classify_ahead`` saw: a classification it no longer needs is stopped.
        """
        try:
            route = await self._route(held, messages, classifying)
        finally:
            if classifying is not None:
                classifying.cancel()
        if route is None:
            return {}
        update = self._kept(route)
        rules = self.rules.routing
        model_id = rules.route_model_ids.get(route.route) if rules is not None else None
        if rules is not None and route.route != "default" and model_id:
            update.update(
                selected_model_id=model_id, selected_effort=rules.route_efforts.get(route.route)
            )
        return update

    @staticmethod
    def _kept(route: Route) -> dict[str, JsonValue]:
        """The route for the thread's later runs, or else for this run alone."""
        if route.persist:
            return {"model_route": route.route}
        return {"model_route_attempted_for": run_id()}

    async def _route(
        self,
        held: Mapping[str, object],
        messages: Sequence[BaseMessage],
        classifying: Awaitable[Route] | None = None,
    ) -> Route | None:
        """The route and whether it should be kept beyond this run."""
        rules = self.rules.routing
        if rules is None:
            return None
        requested = held.get("requested_model")
        stored = kept_route(held.get("model_route"))
        persist = True
        if isinstance(requested, str) and requested:
            route = "default"
        elif rules.mode is None:
            route = "default"
        elif stored is not None:
            route = stored
        elif rules.mode == "fast":
            route = "fast"
        elif held.get("model_route_attempted_for") == run_id():
            return None
        else:
            classified = await (classifying or self._classifier.classify(messages))
            route, persist = classified.route, classified.persist
        if rules.mode == "auto" or requested:
            model_id = requested if requested else rules.route_model_ids.get(route)
            if isinstance(model_id, str) and model_id:
                get_stream_writer()({"type": "model_routed", "route": route, "model_id": model_id})
        return Route(route=route, persist=persist)

    def _system_prompt(self, held: Mapping[str, object]) -> tuple[str, str, str]:
        """The prompt every thread shares, the part its workspace's threads share, and the
        part only this one's says, all fixed for the thread's life: a changed byte misses
        the provider's cache.

        The rendered prompt ends in the workspace's part and then the thread's. A thread
        rendered before the backend named a part has none, and the first holds the rest.
        """
        rendered = held.get("rendered_system_prompt")
        rest = rendered if isinstance(rendered, str) else ""
        parts: list[str] = []
        for key in ("rendered_thread_prompt", "rendered_workspace_prompt"):
            part = held.get(key)
            part = part if isinstance(part, str) and rest.endswith(part) else ""
            rest = rest.removesuffix(part).rstrip()
            parts.append(part)
        thread, workspace = parts
        sections = [self.rules.deployment_prompt, rest]
        return "\n\n".join(section for section in sections if section), workspace, thread

    def _hidden(self, held: Mapping[str, object]) -> list[str]:
        hidden = set(self.rules.hidden_tools)
        for key, tools in self.rules.hidden_unless.items():
            if not held.get(key):
                hidden.update(tools)
        return sorted(hidden)

    @property
    def _guards(self) -> set[str]:
        """The senders of the loop's own nudges, which do not start a new turn."""
        rules = self.rules
        return {
            sender
            for sender in (
                rules.reply.nudge_sender if rules.reply else "",
                rules.work_state.nudge_sender if rules.work_state else "",
            )
            if sender
        }

    async def after_model(self, state: Mapping[str, object]) -> PhaseResult:
        """Ask again for the CLI result, unfinished work, then the reply an answer owes."""
        messages = state.get("messages")
        last = messages[-1] if isinstance(messages, list) and messages else None
        if not isinstance(last, AIMessage) or last.tool_calls:
            return PhaseResult()
        held = _held(state)
        update: dict[str, JsonValue] = {}
        if is_model_failure_reply(last):
            # Asking the model again would wait out the same outage or refusal: what was
            # written for it is the run's answer.
            if self.rules.reply is not None and held.get("reply_surface") == "slack":
                await self._reply_on_behalf(state, content_text(last.content))
            return PhaseResult(update={"reply_nudges": 0, "cli_result_nudges": 0})
        if self.rules.cli_result is not None:
            asked = self._cli_result(self.rules.cli_result, state, held, update)
            if asked is not None:
                return asked
        if self.rules.work_state is not None:
            asked = await self._work_state(self.rules.work_state, state, held, update)
            if asked is not None:
                return asked
        if self.rules.reply is not None:
            return await self._reply(self.rules.reply, state, held, update)
        return PhaseResult(update=update)

    def _nudge(self, rules: Nudging, state: Mapping[str, object]) -> list[dict[str, JsonValue]]:
        seen = {content_text(message.content) for message in visible_messages(state)}
        added = [
            harness_message(
                text, message_id=str(uuid.uuid4()), source="run_nudge", provenance=provenance
            )
            for text, provenance in rules.asked
            # An introduction the model still sees is not made twice.
            if not (
                text in seen
                and (stamped := provenance or Provenance.of(HumanMessage(content=text)))
                and stamped.kind == "context"
            )
        ]
        return _json_dicts.validate_python(messages_to_dict(added))

    def _cli_result(
        self,
        rules: CliResultRules,
        state: Mapping[str, object],
        held: Mapping[str, object],
        update: dict[str, JsonValue],
    ) -> PhaseResult | None:
        turn = _Turn(_messages(state), self._guards)
        call_ids = {
            str(tool_call.get("id"))
            for tool_call in turn.calls()
            if tool_call.get("name") == rules.tool
        }
        if turn.succeeded(call_ids):
            update["cli_result_nudges"] = 0
            return None
        nudges = held.get("cli_result_nudges")
        asked = nudges if isinstance(nudges, int) else 0
        if asked >= rules.max_nudges:
            logger.warning(
                "Run ended without a CLI result after every nudge",
                extra={"result_tool": rules.tool, "result_attempts": asked},
            )
            update["cli_result_nudges"] = 0
            return None
        return PhaseResult(
            update={**update, "cli_result_nudges": asked + 1},
            messages=self._nudge(rules, state),
            jump_to="model",
        )

    async def _goal(
        self, state: Mapping[str, object], held: Mapping[str, object], update: dict[str, JsonValue]
    ) -> tuple[bool, str | None, str | None]:
        """Move the thread's goal for what this model call answers: whether the run stops
        here, what to remind the model of, and the goal's objective to keep in view.

        A person's message puts the goal back to work, starting it from their request;
        so does a wait waking the thread, unless the person stopped it, when the run
        ends without a model call. While the goal waits on its person, each input after
        the one that reported it carries a reminder of what for.
        """
        rules = self.rules.work_state
        if rules is None:
            return False, None, None
        goal = await self._read_goal(GoalRead(run_id=run_id()))
        if goal is None:
            return False, None, None
        inputs = self._inputs(_messages(state))
        requests = [text for message in inputs if (text := self._request(message)) is not None]
        woken = not requests and any(
            isinstance(message, HumanMessage) and self._wakes(message) for message in inputs
        )
        if woken and goal.state == "paused" and goal.reason in {"stopped", "requested"}:
            return True, None, goal.objective
        if (
            goal.state is None
            or (requests and goal.state != "working")
            or (woken and goal.state in {"waiting", "paused"})
        ):
            # A request after the goal completed starts it over from that request.
            started = goal.state in {None, "complete"}
            await self._set_goal(
                GoalChange(
                    run_id=run_id(),
                    state="working",
                    request="\n\n".join(requests) if started and requests else None,
                )
            )
            if started:
                goal = await self._read_goal(GoalRead(run_id=run_id())) or goal
            return False, None, goal.objective
        if goal.state != "waiting_on_user":
            return False, None, goal.objective
        seen = held.get(_GOAL_SEEN)
        update[_GOAL_SEEN] = goal.updated_at_ms
        if seen != goal.updated_at_ms:
            return False, None, goal.objective
        return (
            False,
            "\n\n".join(text for text in (rules.reminder, goal.ask) if text),
            goal.objective,
        )

    async def _work_state(
        self,
        rules: WorkStateRules,
        state: Mapping[str, object],
        held: Mapping[str, object],
        update: dict[str, JsonValue],
    ) -> PhaseResult | None:
        """Send the model back to work until the thread's goal rests: waiting on its person,
        paused, or on something that wakes the thread again.

        The backend reads what will wake the thread from where each wait lives, so a wait
        counts however the model started it, code mode included, and whenever. As Codex
        blocks a goal, three turns in a row the model was sent back for that did nothing,
        or three in a row that ran only failing commands, pause it instead.
        """
        goal = await self._read_goal(GoalRead(run_id=run_id(), waits=True))
        resting = {None, "waiting_on_user", "paused", "complete"}
        if goal is None or goal.state in resting or goal.waits:
            update.update({_EMPTY_TURNS: 0, _FAILING_TURNS: 0})
            if goal is not None and goal.waits and goal.state in {"working", "waiting"}:
                await self._set_goal(GoalChange(run_id=run_id(), state="waiting"))
            return None
        turn = self._last_turn(_messages(state))
        empty = self._counted(held, _EMPTY_TURNS, turn.empty, reset=turn.active)
        failing = self._counted(held, _FAILING_TURNS, turn.failing, reset=not turn.failing)
        update.update({_EMPTY_TURNS: empty, _FAILING_TURNS: failing})
        if empty >= _STALLED_TURNS or failing >= _STALLED_TURNS:
            reason = "no_progress" if empty >= _STALLED_TURNS else "failing_commands"
            await self._set_goal(GoalChange(run_id=run_id(), state="paused", reason=reason))
            update.update({_EMPTY_TURNS: 0, _FAILING_TURNS: 0})
            return None
        return PhaseResult(
            update=update,
            messages=self._nudge(rules, state),
            jump_to="model",
        )

    @staticmethod
    def _counted(held: Mapping[str, object], key: str, counts: bool, *, reset: bool) -> int:
        """A run of turns: one more when this turn ``counts``, none after one that ``reset``s."""
        if reset:
            return 0
        so_far = held.get(key)
        so_far = so_far if isinstance(so_far, int) else 0
        return so_far + 1 if counts else so_far

    def _last_turn(self, messages: Sequence[AnyMessage]) -> _GoalTurn:
        """What the model did since it was last prompted, by a person, an event or a nudge."""
        start = 0
        for index in range(len(messages) - 1, -1, -1):
            if isinstance(messages[index], HumanMessage):
                start = index
                break
        prompt = messages[start] if messages else None
        provenance = Provenance.of(prompt) if isinstance(prompt, HumanMessage) else None
        nudged = provenance is not None and provenance.sender == (
            self.rules.work_state.nudge_sender if self.rules.work_state else None
        )
        after = messages[start + 1 :]
        calls = [
            call
            for message in after
            if isinstance(message, AIMessage)
            for call in message.tool_calls
        ]
        results = [message for message in after if isinstance(message, ToolMessage)]
        failed = [message for message in results if self._failed_command(message)]
        succeeded = any(
            not _reported_failure(message) and message not in failed for message in results
        )
        active = bool(calls)
        return _GoalTurn(
            empty=nudged and not active,
            active=active,
            failing=bool(failed) and not succeeded,
        )

    @staticmethod
    def _failed_command(message: ToolMessage) -> bool:
        """An ``execute`` that ran its command and exited non-zero."""
        return message.name == "execute" and _FAILED_COMMAND in content_text(message.content)

    @staticmethod
    def _inputs(messages: Sequence[AnyMessage]) -> list[AnyMessage]:
        """What came in since the model last answered."""
        for index in range(len(messages) - 1, -1, -1):
            if isinstance(messages[index], AIMessage):
                return list(messages[index + 1 :])
        return list(messages)

    def _wakes(self, message: HumanMessage) -> bool:
        """An event that woke the thread: not a person, the harness's context, or a guard."""
        provenance = Provenance.of(message)
        return provenance is None or (
            provenance.kind == "system" and provenance.sender not in self._guards
        )

    @staticmethod
    def _request(message: BaseMessage) -> str | None:
        """What a person asked in ``message``; None for any other input."""
        if not isinstance(message, HumanMessage):
            return None
        for text in _texts(message.content):
            root = _root(text, "input-message")
            if root is not None and root.get("kind") == "human":
                body = (root.text or "").strip() or (root.findtext("content") or "").strip()
                return body or None
        return None

    async def _read_goal(self, request: GoalRead) -> Goal | None:
        """The thread's goal; None when the backend cannot say, which never holds the run."""
        try:
            return await call(self._backend, GOAL, request)
        except BackendCallError:
            logger.warning("Could not read the thread's goal", exc_info=True)
            return None

    async def _set_goal(self, change: GoalChange) -> None:
        try:
            await call(self._backend, SET_GOAL, change)
        except BackendCallError:
            logger.warning(
                "Could not move the thread's goal",
                extra={"goal_state": change.state},
                exc_info=True,
            )

    async def _reply(
        self,
        rules: ReplyRules,
        state: Mapping[str, object],
        held: Mapping[str, object],
        update: dict[str, JsonValue],
    ) -> PhaseResult:
        if held.get("reply_surface") != "slack":
            return PhaseResult(update=update)
        turn = _Turn(_messages(state), self._guards)
        if self._replied(rules, turn):
            return PhaseResult(update={**update, "reply_nudges": 0})
        nudges = held.get("reply_nudges")
        asked = nudges if isinstance(nudges, int) else 0
        if asked < rules.max_nudges:
            return PhaseResult(
                update={**update, "reply_nudges": asked + 1},
                messages=self._nudge(rules, state),
                jump_to="model",
            )
        # Silence is the one outcome the person cannot recover from.
        await self._reply_on_behalf(state, turn.last_answer())
        return PhaseResult(update={**update, "reply_nudges": 0})

    async def _reply_on_behalf(self, state: Mapping[str, object], text: str) -> None:
        if not text:
            logger.warning("Nothing to post on the model's behalf: it wrote no text this turn")
            return
        reply = run_call(ReplyOnBehalf, run_id(), state, text=text)
        await call(self._backend, REPLY_ON_BEHALF, reply)

    @staticmethod
    def _replied(rules: ReplyRules, turn: _Turn) -> bool:
        """Whether a call this turn answered the person, or said there is nothing to answer."""
        # A result that says a completion reply is owed still needs one: no "no reply".
        owed = any(
            isinstance(payload := _json_payload(result), dict)
            and payload.get("success") is True
            and payload.get("completion_reply_required") is True
            for result in turn.results()
        )
        answered = {rules.no_reply_tool, *rules.replies} - (
            {rules.no_reply_tool} if owed else set()
        )
        call_ids = {
            str(tool_call.get("id"))
            for tool_call in turn.calls()
            if tool_call.get("name") in answered
            or (
                tool_call.get("name") == rules.tool
                and isinstance(args := tool_call.get("args"), Mapping)
                # An acknowledgement is not an answer.
                and args.get("response_type") == "final"
            )
        }
        return turn.succeeded(call_ids)

    def after_agent(self, state: Mapping[str, object]) -> None:
        """Ask for the graph's own end-of-run work, then report the run's end.

        Both go out behind the run's reports, so the transcript has every reply and tool
        call before the end; one the backend cannot do takes none of the rest with it.
        """
        messages = _messages(state)
        current = run_id()
        outbox = self._backend.outbox
        if messages and _LIMIT_MARKER in content_text(messages[-1].content):
            outbox.notify(STEP_LIMIT_REACHED, RunRequest(run_id=current))
        for work in self.rules.at_end:
            if work == "settle_review_check":
                outbox.notify(SETTLE_REVIEW_CHECK, RunRequest(run_id=current))
            else:
                summary = _held(state).get("human_input_summary")
                finished = ScoutPlanEnd(
                    run_id=current,
                    human_input_summary=summary if isinstance(summary, str) else "",
                )
                outbox.notify(FINISH_SCOUT_PLAN, finished)
        replies = [
            # Usage reads only each reply's id, model and token counts.
            AIMessage(
                content="",
                id=message.id,
                usage_metadata=message.usage_metadata,
                response_metadata=message.response_metadata,
            )
            for message in messages
            if isinstance(message, AIMessage) and message.usage_metadata
        ]
        ended = RunEnded(
            run_id=current,
            replies=_json_dicts.validate_python(messages_to_dict(replies)),
            invocation_id=self._backend.invocation_id,
        )
        outbox.notify(RUN_ENDED, ended)


def _messages(state: Mapping[str, object]) -> list[AnyMessage]:
    messages = state.get("messages")
    return list(messages) if isinstance(messages, list) else []
