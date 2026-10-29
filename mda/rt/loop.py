"""Open SWE's middleware around each model call, run here as the catalog's ``LoopRules`` say.

Before each call it ends the run where a rule says to, adds the thread's task
context, keeps the box's GitHub proxy fresh, picks the call's route and model, and
assembles its system prompt and tool list. After an answer that calls no tool, it
asks again for the reply or CLI result the run owes. When the run ends, it reports
the end and does the graph's own end-of-run work.

The backend answers only what needs its data or credentials, one named call each
(``runtime_api.loop``). Graph state the loop reads or writes is the backend's held
state (``open_swe_state``), which preparation fills.
"""

import asyncio
import json
import logging
import uuid
from collections.abc import Mapping, Sequence
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
    Catalog,
    CliResultRules,
    LoopRules,
    ModelSpec,
    ReplyRules,
    RunModels,
)
from runtime_api.endpoint import call
from runtime_api.loop import (
    FINISH_SCOUT_PLAN,
    MODEL_CHOICE,
    REFRESH_GITHUB_PROXY,
    REPLY_ON_BEHALF,
    ROUTE_MODEL,
    RUN_ENDED,
    SETTLE_REVIEW_CHECK,
    STEP_LIMIT_REACHED,
    TASK_CONTEXT,
    AfterAgent,
    BeforeModel,
    ChosenModel,
    ModelCall,
    PhaseResult,
    ReplyOnBehalf,
    Route,
    Routed,
    RunEnded,
    ScoutPlanEnd,
    TaskContext,
)
from runtime_api.operations import RunRequest

from rt.backend import (
    BackendCallError,
    OpenSweBackend,
    RunLocal,
    git_trace,
    human_messages,
    run_call,
)
from rt.tools import STATE_KEY, run_id

logger = logging.getLogger(__name__)

# What ``ModelCallLimitMiddleware`` ends a run with once it reaches its limit.
_LIMIT_MARKER: Final = "Model call limits exceeded"
_SUMMARIZATION_EVENT: Final = "_summarization_event"
# Held state: when the box's GitHub token next needs replacing. It lives in the thread's
# checkpoint, so it holds for the next run on whichever backend replica serves it.
_GITHUB_PROXY_DUE: Final = "github_proxy_refresh_at"
# A token is replaced this long before it expires. The backend never hands out one with
# less left, so a replacement is not due again at once.
_GITHUB_PROXY_WINDOW: Final = timedelta(minutes=10)
# A thread whose box has no GitHub proxy is asked again after this long.
_GITHUB_PROXY_RECHECK: Final = timedelta(minutes=30)
# A refresh that failed is tried again after this long, so a failure that keeps happening
# does not hold up every model call.
_GITHUB_PROXY_RETRY: Final = timedelta(minutes=1)
_json_dicts = TypeAdapter(list[dict[str, JsonValue]])
# Starts the id of each message that tells the model its role in the thread's task.
_ROLE: Final = "task-role:"
# Starts the id of the message holding what the backend worked out for this run.
_RUN_CONTEXT: Final = "run-context:"


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


def _dynamic_context(message: BaseMessage) -> bool:
    """A block that introduces who or what the conversation's messages come from."""
    return any(_root(text, "dynamic-context") is not None for text in _texts(message.content))


def _system_sender(message: BaseMessage) -> str | None:
    for text in _texts(message.content):
        root = _root(text, "input-message")
        if root is not None and root.get("kind") == "system":
            return root.get("sender")
    return None


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


def _visible(state: Mapping[str, object]) -> list[AnyMessage]:
    """The messages the model still sees: summarization drops those before its cutoff."""
    listed = _messages(state)
    event = state.get(_SUMMARIZATION_EVENT)
    cutoff = event.get("cutoff_index") if isinstance(event, Mapping) else None
    return listed[cutoff:] if isinstance(cutoff, int) and cutoff >= 0 else listed


def _latest(messages: Sequence[BaseMessage], prefix: str) -> str | None:
    """The text of the last of ``messages`` whose id starts with ``prefix``."""
    for message in reversed(messages):
        if isinstance(message.id, str) and message.id.startswith(prefix):
            return content_text(message.content)
    return None


def _run_guidance(messages: Sequence[BaseMessage]) -> dict[str, str]:
    """The latest run guidance for each block id in the conversation."""
    latest: dict[str, str] = {}
    for message in messages:
        if not isinstance(message, HumanMessage):
            continue
        for text in _texts(message.content):
            root = _root(text, "dynamic-context")
            if root is not None and root.get("kind") == "run" and (key := root.get("id")):
                latest[key] = text
    return latest


def _held(state: Mapping[str, object]) -> Mapping[str, object]:
    held = state.get(STATE_KEY)
    return held if isinstance(held, Mapping) else {}


def _moment(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def _normalized(route: str) -> str:
    return "fast" if route == "fast_alt" else route


class _Turn:
    """What happened since the last thing a person said."""

    def __init__(self, messages: Sequence[AnyMessage], guard: str) -> None:
        self.messages = list(messages)
        for index in range(len(self.messages) - 1, -1, -1):
            message = self.messages[index]
            if (
                isinstance(message, HumanMessage)
                and not _dynamic_context(message)
                and _system_sender(message) != guard
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


class RunLoop:
    """What happens around each of the run's model calls, as its ``LoopRules`` say."""

    def __init__(self, backend: OpenSweBackend) -> None:
        self._backend = backend
        # Models each run calls by id, such as one a person asked for, as the backend sets them.
        self._chosen: RunLocal[dict[tuple[str, str | None], ModelSpec]] = RunLocal(dict)

    @staticmethod
    def runs(catalog: Catalog) -> bool:
        """Whether a run planned with ``catalog`` runs its loop here, not in the backend's phases."""
        return catalog.loop is not None and catalog.models is not None

    @property
    def rules(self) -> LoopRules:
        """The run's rules; a launched worker's own once it adopted its run, not its coordinator's."""
        rules = self._backend.run.catalog.loop
        if rules is None:
            raise BackendCallError("The run's catalog names no loop rules")
        return rules

    @property
    def _models(self) -> RunModels:
        models = self._backend.run.catalog.models
        if models is None:
            raise BackendCallError("The run's catalog names no models")
        return models

    @property
    def looks_at_answers(self) -> bool:
        return self.rules.reply is not None or self.rules.cli_result is not None

    async def before_model(self, state: Mapping[str, object]) -> BeforeModel:
        held = _held(state)
        if any(held.get(key) for key in self.rules.stop_when) or self._awaits_client(state):
            return BeforeModel(jump_to="end")
        proxy = asyncio.create_task(self._github_proxy(held))
        try:
            context = await self._task_context()
            update = await proxy
        finally:
            proxy.cancel()
            await asyncio.gather(proxy, return_exceptions=True)
        visible = _visible(state)
        visible_guidance = _run_guidance(visible)
        added: list[BaseMessage] = [
            HumanMessage(content=text, id=f"run-guidance:{uuid.uuid4()}")
            for key, text in _run_guidance(_messages(state)).items()
            if key != "task-role" and text != visible_guidance.get(key)
        ]
        if context.instructions and context.instructions != _latest(visible, _ROLE):
            added.append(HumanMessage(content=context.instructions, id=f"{_ROLE}{uuid.uuid4()}"))
        run_context = _latest(_messages(state), _RUN_CONTEXT)
        if run_context is not None and run_context != _latest(visible, _RUN_CONTEXT):
            added.append(HumanMessage(content=run_context, id=f"{_RUN_CONTEXT}{uuid.uuid4()}"))
        if context.message is not None and not any(
            content_text(message.content) == context.message for message in visible
        ):
            added.append(HumanMessage(content=context.message, id=f"task-context:{uuid.uuid4()}"))
        route = await self._route(held, [*_visible(state), *added])
        if route is not None:
            if route.persist:
                update["model_route"] = route.route
            else:
                update["model_route_attempted_for"] = run_id()
        current = {**held, **update}
        model = await self._model_for(current)
        return BeforeModel(
            update=update,
            messages=_json_dicts.validate_python(messages_to_dict(added)),
            call=ModelCall(
                system_prompt=self._system_prompt(current),
                hidden_tools=self._hidden(current),
                model=model,
                vision=None if model.reads_images else self._models.vision,
                fallback=model.fallback,
                use_gateway=self._models.use_gateway,
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
        """Put a new GitHub token on the box once the one there is about to expire.

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
            refreshed = await call(self._backend, REFRESH_GITHUB_PROXY, RunRequest(run_id=run_id()))
        except Exception:
            logger.warning("Could not refresh the box's GitHub token", exc_info=True)
            return {_GITHUB_PROXY_DUE: (now + _GITHUB_PROXY_RETRY).isoformat()}
        due = (
            refreshed.expires_at - _GITHUB_PROXY_WINDOW
            if refreshed.expires_at is not None
            else now + _GITHUB_PROXY_RECHECK
        )
        return {_GITHUB_PROXY_DUE: due.isoformat()}

    async def _task_context(self) -> TaskContext:
        if not self.rules.task_coordination:
            return TaskContext()
        return await call(self._backend, TASK_CONTEXT, RunRequest(run_id=run_id()))

    async def _route(
        self, held: Mapping[str, object], messages: Sequence[BaseMessage]
    ) -> Route | None:
        """The route and whether it should be kept beyond this run."""
        rules = self.rules.routing
        if rules is None:
            return None
        requested = held.get("requested_model")
        stored = held.get("model_route")
        persist = True
        if isinstance(requested, str) and requested:
            route = "default"
        elif rules.mode is None:
            route = "default"
        elif isinstance(stored, str) and stored:
            route = _normalized(stored)
        elif rules.mode == "fast":
            route = "fast"
        elif held.get("model_route_attempted_for") == run_id():
            return None
        else:
            asked = Routed(run_id=run_id(), humans=human_messages(messages))
            routed = await call(self._backend, ROUTE_MODEL, asked)
            route, persist = routed.route, routed.persist
        if rules.mode == "auto" or requested:
            model_id = requested if requested else rules.route_model_ids.get(route)
            if isinstance(model_id, str) and model_id:
                get_stream_writer()({"type": "model_routed", "route": route, "model_id": model_id})
        return Route(route=route, persist=persist)

    async def _chosen_model(self, model_id: str, effort: object) -> ModelSpec:
        key = (model_id, effort if isinstance(effort, str) else None)
        chosen = self._chosen.get(self._backend.run)
        if key not in chosen:
            choice = ChosenModel(run_id=run_id(), model_id=key[0], effort=key[1])
            chosen[key] = await call(self._backend, MODEL_CHOICE, choice)
        return chosen[key]

    async def _model_for(self, held: Mapping[str, object]) -> ModelSpec:
        """The run's own model, a route's, or the one a person asked for."""
        requested = held.get("requested_model")
        if isinstance(requested, str) and requested:
            return await self._chosen_model(requested, held.get("requested_effort"))
        route = held.get("model_route")
        routed = self._models.routes.get(_normalized(route)) if isinstance(route, str) else None
        if routed is not None and route != "default":
            return routed
        selected = held.get("selected_model_id")
        if isinstance(selected, str) and selected and selected != self._models.model.model_id:
            return await self._chosen_model(selected, held.get("selected_effort"))
        return self._models.model

    def _system_prompt(self, held: Mapping[str, object]) -> str:
        """Only parts fixed for the thread's life: a changed byte misses the provider's cache."""
        rendered = held.get("rendered_system_prompt")
        sections = [self.rules.deployment_prompt, rendered if isinstance(rendered, str) else ""]
        return "\n\n".join(section for section in sections if section)

    def _hidden(self, held: Mapping[str, object]) -> list[str]:
        hidden = set(self.rules.hidden_tools)
        for key, tools in self.rules.hidden_unless.items():
            if not held.get(key):
                hidden.update(tools)
        return sorted(hidden)

    async def after_model(self, state: Mapping[str, object]) -> PhaseResult:
        """Ask again for the CLI result, then the reply, an answer without either owes."""
        messages = state.get("messages")
        last = messages[-1] if isinstance(messages, list) and messages else None
        if not isinstance(last, AIMessage) or last.tool_calls:
            return PhaseResult()
        held = _held(state)
        update: dict[str, JsonValue] = {}
        if self.rules.cli_result is not None:
            asked = self._cli_result(self.rules.cli_result, state, held, update)
            if asked is not None:
                return asked
        if self.rules.reply is not None:
            return await self._reply(self.rules.reply, state, held, update)
        return PhaseResult(update=update)

    def _nudge(
        self, texts: Sequence[str], state: Mapping[str, object]
    ) -> list[dict[str, JsonValue]]:
        seen = {content_text(message.content) for message in _visible(state)}
        added = [
            HumanMessage(content=text, id=str(uuid.uuid4()))
            for text in texts
            # An introduction the model still sees is not made twice.
            if not (text in seen and _dynamic_context(HumanMessage(content=text)))
        ]
        return _json_dicts.validate_python(messages_to_dict(added))

    def _cli_result(
        self,
        rules: CliResultRules,
        state: Mapping[str, object],
        held: Mapping[str, object],
        update: dict[str, JsonValue],
    ) -> PhaseResult | None:
        turn = _Turn(_messages(state), self.rules.reply.nudge_sender if self.rules.reply else "")
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
            messages=self._nudge(rules.nudge, state),
            jump_to="model",
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
        turn = _Turn(_messages(state), rules.nudge_sender)
        if self._replied(rules, turn):
            return PhaseResult(update={**update, "reply_nudges": 0})
        nudges = held.get("reply_nudges")
        asked = nudges if isinstance(nudges, int) else 0
        if asked < rules.max_nudges:
            return PhaseResult(
                update={**update, "reply_nudges": asked + 1},
                messages=self._nudge(rules.nudge, state),
                jump_to="model",
            )
        # Silence is the one outcome the person cannot recover from.
        if text := turn.last_answer():
            reply = run_call(ReplyOnBehalf, run_id(), state, text=text)
            await call(self._backend, REPLY_ON_BEHALF, reply)
        else:
            logger.warning("Nothing to post on the model's behalf: it wrote no text this turn")
        return PhaseResult(update={**update, "reply_nudges": 0})

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

    async def after_agent(
        self, state: Mapping[str, object], evidence: Mapping[str, JsonValue]
    ) -> AfterAgent:
        """Ask for the graph's own end-of-run work, then report the run's end.

        The work goes out behind the run's reports, and one the backend cannot do takes
        none of the rest with it.
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
        # The transcript has every reply and tool call, and the backend the work, before
        # the run's end.
        await outbox.drain()
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
        request = RunEnded(
            run_id=current,
            git={**git_trace(state), **evidence},
            replies=_json_dicts.validate_python(messages_to_dict(replies)),
        )
        return await call(self._backend, RUN_ENDED, request)


def _messages(state: Mapping[str, object]) -> list[AnyMessage]:
    messages = state.get("messages")
    return list(messages) if isinstance(messages, list) else []
