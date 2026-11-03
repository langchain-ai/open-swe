"""The status a Slack channel shows while a run works.

A line, or a list of steps, in place of Trigger Server's own working phrases.
It is a courtesy, like ``reactions``: a chooser that raises, hangs, or answers
with something that is not a status leaves what Slack shows, and posting it
never fails or slows the run.
"""

from __future__ import annotations

import asyncio
import contextlib
import inspect
import json
import time
import uuid
from collections import OrderedDict
from collections.abc import Awaitable, Callable, Mapping, Sequence
from typing import TYPE_CHECKING, Literal, NotRequired, TypedDict, TypeVar, cast

from langchain.agents.middleware import (
    AgentMiddleware,
    AgentState,
    ModelCallResult,
    ModelRequest,
    ModelResponse,
)
from langgraph.config import get_config

from managed_deepagents._channels.binding import resolve_channel_binding
from managed_deepagents._channels.trigger.client import create_trigger_client
from managed_deepagents._log import logger

if TYPE_CHECKING:
    from langchain.agents.middleware.types import ToolCallRequest
    from langchain_core.messages import ToolMessage
    from langgraph.runtime import Runtime
    from langgraph.types import Command

    from managed_deepagents._channel import Channel
    from managed_deepagents._channels.binding import ChannelBinding
    from managed_deepagents._channels.trigger.client import TriggerClientLike
    from managed_deepagents._channels.trigger.types import (
        TriggerChannelAddress,
        TriggerChannelRuntimeEventRequest,
        TriggerChannelRuntimeStatus,
        TriggerChannelStatusStep,
    )

_T = TypeVar("_T")

#: Where a step is: Slack's task statuses.
SlackStatusStepStatus = Literal["pending", "in_progress", "complete", "error"]


class SlackStatusStep(TypedDict):
    """One step of the run's plan; a step without a status is ``pending``."""

    title: str
    status: NotRequired[SlackStatusStepStatus]


#: The run's status: one line, or a list of steps that Slack shows as a plan,
#: each a title or a ``SlackStatusStep``.
SlackStatusValue = str | Sequence[str | SlackStatusStep]


class SlackStatusContext(TypedDict):
    """What a status chooser is told: the agent's state, as of now."""

    state: Mapping[str, object]


#: Picks the run's status from the agent's state, or ``None`` to leave what's
#: shown. May be sync or async.
#:
#: It is called as the run starts, before and after each model call, and every
#: few seconds while a model or tool call runs, one call at a time. A sync
#: chooser runs in a thread, where the timeout cannot stop it, so bound any I/O
#: it does.
SlackStatusChooser = Callable[
    [SlackStatusContext], SlackStatusValue | Awaitable[SlackStatusValue | None] | None
]

#: How a channel shows a run's status: always the same value, or a callable
#: of the agent's state.
SlackStatusChoice = SlackStatusValue | SlackStatusChooser

_STEP_STATUSES = frozenset({"pending", "in_progress", "complete", "error"})
#: Slack's limit on a task's or plan's title.
_MAX_TITLE_LENGTH = 256
#: Slack's limit on a plan's tasks.
_MAX_STEPS = 50
#: How long a chooser may take before what's shown stays.
_CHOOSER_TIMEOUT_S = 1.0
#: How often a chooser is asked again while a model or tool call runs.
_HEARTBEAT_S = 4.0
#: The least time between two status updates of a run.
_MIN_UPDATE_GAP_S = 1.0
#: How long the end of a run waits for its last status update.
_DRAIN_TIMEOUT_S = 2.0
#: Runs whose status is tracked at once; an interrupted run never says it ended.
_MAX_TRACKED_RUNS = 256


class _StatusFields(TypedDict, total=False):
    message: dict[str, str]
    steps: list[TriggerChannelStatusStep]


def _one_line(text: str) -> str | None:
    """``text`` on one line, at most Slack's title length; ``None`` when blank."""
    line = " ".join(text.split())
    return line[:_MAX_TITLE_LENGTH] or None


def _status_step(value: object) -> TriggerChannelStatusStep | None:
    if isinstance(value, str):
        title = _one_line(value)
        return None if title is None else {"title": title, "status": "pending"}
    if not isinstance(value, Mapping):
        return None
    raw_title = value.get("title")
    status = value.get("status", "pending")
    if not isinstance(raw_title, str) or not isinstance(status, str):
        return None
    if status not in _STEP_STATUSES:
        return None
    title = _one_line(raw_title)
    if title is None:
        return None
    return {"title": title, "status": cast("SlackStatusStepStatus", status)}


def slack_status_fields(value: object) -> _StatusFields | None:
    """A status value as Trigger Server takes it, or ``None`` when it isn't one.

    A plan keeps its first steps, as many as Slack shows.
    """
    if isinstance(value, str):
        text = _one_line(value)
        return None if text is None else {"message": {"text": text}}
    if not isinstance(value, (list, tuple)):
        return None
    steps: list[TriggerChannelStatusStep] = []
    for entry in cast("Sequence[object]", value)[:_MAX_STEPS]:
        step = _status_step(entry)
        if step is None:
            return None
        steps.append(step)
    return {"steps": steps}


def is_slack_status_value(value: object) -> bool:
    """Whether ``value`` is a status value a channel can show."""
    return slack_status_fields(value) is not None


def started_status(choice: SlackStatusChoice | None) -> TriggerChannelRuntimeStatus | None:
    """What a run's ``run.started`` tells Trigger Server about its status.

    A custom status shows as a plan, whose title is the line, and a fixed one
    from the start. ``None`` keeps Trigger Server's working phrases.
    """
    if choice is None:
        return None
    status: TriggerChannelRuntimeStatus = {"display": "plan"}
    if not callable(choice):
        status.update(cast("TriggerChannelRuntimeStatus", slack_status_fields(choice) or {}))
    return status


async def _call(chooser: SlackStatusChooser, context: SlackStatusContext) -> object:
    """Call ``chooser``, giving a sync one a thread so it cannot block the loop."""
    if inspect.iscoroutinefunction(chooser):
        return await chooser(context)
    result = await asyncio.to_thread(chooser, context)
    return await result if inspect.isawaitable(result) else result


async def _choose_status(
    chooser: SlackStatusChooser,
    context: SlackStatusContext,
    warn: Callable[[str], None],
) -> _StatusFields | None:
    """What a chooser says, as Trigger Server takes it; ``None`` to leave what's shown."""
    try:
        chosen = await asyncio.wait_for(_call(chooser, context), timeout=_CHOOSER_TIMEOUT_S)
        # An empty list, as a to-do list is before the agent writes one, leaves
        # what's shown too.
        if chosen is None or (isinstance(chosen, (list, tuple)) and not chosen):
            return None
        fields = slack_status_fields(chosen)
        if fields is not None:
            return fields
        warn("Slack status chooser did not answer with a status line or steps; leaving the status.")
    except Exception as error:  # noqa: BLE001 - a courtesy must not fail a run
        warn(f"Slack status chooser failed ({type(error).__name__}); leaving the status.")
    return None


class _StatusTarget(TypedDict):
    """Where a run's status goes: its Trigger delivery, and the run itself."""

    channel_id: str
    address: TriggerChannelAddress
    delivery_id: str
    source_thread_key: str
    run_id: str
    thread_id: str


class _RunStatus:
    """One run's status updates.

    One chooser call at a time, at most one update a second, and only when the
    status changed. Updates are posted in order, in the background; a failed
    post is logged.
    """

    def __init__(
        self, chooser: SlackStatusChooser, client: TriggerClientLike, target: _StatusTarget
    ) -> None:
        self._chooser = chooser
        self._client = client
        self._target = target
        #: The newest state not yet shown to the chooser.
        self._pending: Mapping[str, object] | None = None
        self._draining: asyncio.Task[None] | None = None
        self._shown: str | None = None
        self._updated_at: float | None = None
        self._closed = False
        #: Bumped when what the chooser was asked about is out of date.
        self._generation = 0
        self._warned = False
        self._wake = asyncio.Event()
        #: The model and tool calls under way, and the state the heartbeat asks with.
        self._calls = 0
        self._call_state: Mapping[str, object] | None = None
        self._heartbeat: asyncio.Task[None] | None = None

    def update(self, state: Mapping[str, object]) -> None:
        """Ask the chooser about ``state``, once the previous call is done."""
        if self._closed:
            return
        self._pending = state
        if self._draining is None or self._draining.done():
            self._draining = asyncio.get_running_loop().create_task(self._drain())

    async def during(self, state: Mapping[str, object], call: Callable[[], Awaitable[_T]]) -> _T:
        """Run a model or tool call, asking the chooser again while it runs."""
        self._calls += 1
        self._call_state = state
        if self._calls == 1 and not self._closed:
            self._heartbeat = asyncio.get_running_loop().create_task(self._beat())
        try:
            return await call()
        except BaseException:
            # The run is failing, interrupted, or cancelled, and may never
            # reach `after_agent`: show nothing more, as Trigger Server hears
            # it ended.
            self.stop()
            raise
        finally:
            self._calls -= 1
            if self._calls == 0:
                self._stop_heartbeat()
                self._drop_stale()

    @property
    def closed(self) -> bool:
        """Whether the run's status updates are over."""
        return self._closed

    def _drop_stale(self) -> None:
        """A finished call leaves what was asked during it out of date.

        The next hook asks again, unless the run stops first, as an interrupt
        in a later hook stops it.
        """
        self._pending = None
        self._generation += 1
        self._wake.set()

    def stop(self) -> None:
        """Stop asking; an update already on its way still arrives."""
        self._closed = True
        self._pending = None
        self._stop_heartbeat()
        self._wake.set()

    async def close(self) -> None:
        """Stop asking, and wait a little for an update already on its way."""
        self.stop()
        draining = self._draining
        if draining is not None and not draining.done():
            await asyncio.wait({draining}, timeout=_DRAIN_TIMEOUT_S)

    def _stop_heartbeat(self) -> None:
        """Stop asking again every few seconds."""
        if self._heartbeat is not None:
            self._heartbeat.cancel()
        self._heartbeat = None
        self._call_state = None

    async def _beat(self) -> None:
        while True:
            await asyncio.sleep(_HEARTBEAT_S)
            if self._call_state is not None:
                self.update(self._call_state)

    async def _drain(self) -> None:
        while self._pending is not None and not self._closed:
            await self._gap()
            state, self._pending = self._pending, None
            if state is None or self._closed:
                return
            generation = self._generation
            fields = await _choose_status(self._chooser, {"state": state}, self._warn_once)
            if fields is None or self._closed or generation != self._generation:
                continue
            shown = json.dumps(fields, sort_keys=True)
            if shown == self._shown:
                continue
            self._updated_at = time.monotonic()
            # Only a status Trigger Server took counts as shown; a failed one
            # is posted again when the chooser answers it again.
            if await self._post(fields):
                self._shown = shown

    async def _gap(self) -> None:
        """Wait out the least time between updates; closing ends the wait."""
        if self._updated_at is None:
            return
        wait = self._updated_at + _MIN_UPDATE_GAP_S - time.monotonic()
        if wait <= 0:
            return
        self._wake.clear()
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(self._wake.wait(), timeout=wait)

    async def _post(self, fields: _StatusFields) -> bool:
        target = self._target
        try:
            await self._client.post_runtime_event(
                target["channel_id"],
                cast(
                    "TriggerChannelRuntimeEventRequest",
                    {
                        "id": str(uuid.uuid4()),
                        "type": "run.thinking",
                        "delivery_id": target["delivery_id"],
                        "source_thread_key": target["source_thread_key"],
                        "address": target["address"],
                        "run_thread_id": target["thread_id"],
                        "run_id": target["run_id"],
                        "display": "plan",
                        **fields,
                    },
                ),
            )
        except Exception as error:  # noqa: BLE001 - a courtesy must not fail a run
            logger.warning("Slack status update failed (%s).", type(error).__name__)
            return False
        return True

    def _warn_once(self, message: str) -> None:
        """A broken chooser fails on every call; say so once per run."""
        if self._warned:
            return
        self._warned = True
        logger.warning(message)


def _nonblank(value: object) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None


def _status_target(binding: ChannelBinding, config: Mapping[str, object]) -> _StatusTarget | None:
    """The run's Trigger Slack delivery, verified against the run's own source."""
    target = cast("Mapping[str, object]", binding["target"])
    metadata = config.get("metadata")
    configurable = config.get("configurable")
    run_id = _nonblank(metadata.get("run_id")) if isinstance(metadata, Mapping) else None
    thread_id = (
        _nonblank(configurable.get("thread_id")) if isinstance(configurable, Mapping) else None
    )
    address = target.get("address")
    if run_id is None or thread_id is None or not isinstance(address, Mapping):
        return None
    return {
        "channel_id": cast("str", target["channel_id"]),
        "address": cast("TriggerChannelAddress", address),
        "delivery_id": binding["delivery_id"],
        "source_thread_key": binding["source_thread_key"],
        "run_id": run_id,
        "thread_id": thread_id,
    }


def _nested_graph(configurable: object) -> bool:
    """LangGraph's checkpoint namespaces nest with ``|``."""
    namespace = configurable.get("checkpoint_ns") if isinstance(configurable, Mapping) else None
    return isinstance(namespace, str) and "|" in namespace


def _current_config() -> Mapping[str, object] | None:
    try:
        return cast("Mapping[str, object]", get_config())
    except RuntimeError:
        # Outside a runnable context.
        return None


class SlackStatusMiddleware(AgentMiddleware[AgentState[object], None, object]):
    """Show what each Slack channel's status chooser says while a run works.

    Runs from other channels, and channels with a fixed status or none, pass
    through: ``run.started`` carries a fixed status. Only async runs post; a
    sync run passes through too.
    """

    def __init__(self, choosers: Mapping[str, SlackStatusChooser]) -> None:
        self._choosers = dict(choosers)
        self._runs: OrderedDict[str, _RunStatus] = OrderedDict()

    @property
    def name(self) -> str:
        return "mda-slack-status"

    def _current(self, context: object, *, start: bool = True) -> tuple[str, _RunStatus] | None:
        """The current run's status updates, when its channel has a chooser.

        New ones only with ``start``.
        """
        config = _current_config()
        configurable = config.get("configurable") if config is not None else None
        # A subagent forked inside a tool call shares the run's config; only
        # the run's own agent, at the root of its graph, shows the status.
        if config is None or _nested_graph(configurable):
            return None
        binding = resolve_channel_binding(
            context,
            cast("Mapping[str, object]", configurable)
            if isinstance(configurable, Mapping)
            else None,
        )
        if binding is None or binding["transport"] != "trigger_server":
            return None
        chooser = self._choosers.get(binding["name"])
        target = _status_target(binding, config) if chooser is not None else None
        if chooser is None or target is None:
            return None
        key = target["run_id"]
        run = self._runs.get(key)
        if run is not None and run.closed:
            self._runs.pop(key, None)
            run = None
        if run is None:
            if not start:
                return None
            client = create_trigger_client()
            if client is None:
                return None
            if len(self._runs) >= _MAX_TRACKED_RUNS:
                _, stale = self._runs.popitem(last=False)
                stale.stop()
            run = _RunStatus(chooser, client, target)
            self._runs[key] = run
        return key, run

    def _update(self, state: object, context: object) -> None:
        current = self._current(context)
        if current is not None:
            current[1].update(cast("Mapping[str, object]", state))

    async def abefore_agent(
        self, state: AgentState[object], runtime: Runtime[None]
    ) -> dict[str, object] | None:
        self._update(state, runtime.context)
        return None

    async def abefore_model(
        self, state: AgentState[object], runtime: Runtime[None]
    ) -> dict[str, object] | None:
        self._update(state, runtime.context)
        return None

    async def aafter_model(
        self, state: AgentState[object], runtime: Runtime[None]
    ) -> dict[str, object] | None:
        self._update(state, runtime.context)
        return None

    async def aafter_agent(
        self, state: AgentState[object], runtime: Runtime[None]
    ) -> dict[str, object] | None:
        _ = state
        current = self._current(runtime.context, start=False)
        if current is not None:
            key, run = current
            self._runs.pop(key, None)
            await run.close()
        return None

    async def awrap_model_call(
        self,
        request: ModelRequest[None],
        handler: Callable[[ModelRequest[None]], Awaitable[ModelResponse[object]]],
    ) -> ModelCallResult[object]:
        current = self._current(request.runtime.context)
        if current is None:
            return await handler(request)
        return await current[1].during(
            cast("Mapping[str, object]", request.state), lambda: handler(request)
        )

    async def awrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Awaitable[ToolMessage | Command[object]]],
    ) -> ToolMessage | Command[object]:
        current = self._current(request.runtime.context)
        if current is None:
            return await handler(request)
        return await current[1].during(
            cast("Mapping[str, object]", request.state), lambda: handler(request)
        )

    # A sync run has no loop to post from; it shows Trigger Server's phrases.
    def before_agent(
        self, state: AgentState[object], runtime: Runtime[None]
    ) -> dict[str, object] | None:
        _ = state, runtime
        return None

    def before_model(
        self, state: AgentState[object], runtime: Runtime[None]
    ) -> dict[str, object] | None:
        _ = state, runtime
        return None

    def after_model(
        self, state: AgentState[object], runtime: Runtime[None]
    ) -> dict[str, object] | None:
        _ = state, runtime
        return None

    def after_agent(
        self, state: AgentState[object], runtime: Runtime[None]
    ) -> dict[str, object] | None:
        _ = state, runtime
        return None

    def wrap_model_call(
        self,
        request: ModelRequest[None],
        handler: Callable[[ModelRequest[None]], ModelResponse[object]],
    ) -> ModelCallResult[object]:
        return handler(request)

    def wrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], ToolMessage | Command[object]],
    ) -> ToolMessage | Command[object]:
        return handler(request)


def slack_status_choosers(channels: Sequence[Channel] | None) -> dict[str, SlackStatusChooser]:
    """Each Slack channel's status chooser, by channel name."""
    choosers: dict[str, SlackStatusChooser] = {}
    for channel in channels or []:
        status = getattr(channel, "status", None)
        name = getattr(channel, "name", None)
        if getattr(channel, "kind", None) == "slack" and isinstance(name, str) and callable(status):
            choosers[name] = cast("SlackStatusChooser", status)
    return choosers


__all__ = [
    "SlackStatusChoice",
    "SlackStatusChooser",
    "SlackStatusContext",
    "SlackStatusMiddleware",
    "SlackStatusStep",
    "SlackStatusStepStatus",
    "SlackStatusValue",
    "is_slack_status_value",
    "slack_status_choosers",
    "slack_status_fields",
    "started_status",
]
