"""Deliver a run's result to its thread's channel conversation when the run ends.

One step at the end of the top-level graph stream, for every run with a thread
origin (``runtime.thread.origin``): one a channel message started, a Studio
resume, a task notification that woke the agent, or a scheduled run. A run that
paused shows its pending interrupts in a Trigger thread, or posts a notice with a
Studio link to a GitHub or Linear conversation; a run that finished posts its
answers there. A scheduled run without an origin posts to its ``deliver_to``.

A scheduled run that starts a new Trigger thread, with its answer or with a
notice that it waits on a decision, hands its own thread to that thread's
conversation, so replies and decisions there continue the run.
"""

from __future__ import annotations

import inspect
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING

from managed_deepagents._channels.assistant_text import (
    QUIET_REPLY,
    extract_reply_content,
    extract_reply_text,
)
from managed_deepagents._channels.binding import _TriggerTargetModel
from managed_deepagents._channels.messaging_registry import (
    channel_auto_replies_for_name,
    channel_provider_for_name,
    messaging_transport_for_name,
    paused_notice_for_name,
)
from managed_deepagents._channels.schedule_delivery import (
    NormalizedScheduleDeliverTo,
    deliver_schedule_result,
    get_deployment_id,
    is_trusted_schedule_delivery_run,
    read_schedule_deliver_to,
)
from managed_deepagents._channels.trigger.client import create_trigger_client
from managed_deepagents._channels.trigger.tracing_project import tracer_session_id
from managed_deepagents._channels.trigger.transport import TriggerTransport, start_trigger_thread
from managed_deepagents._identity_runtime import _field
from managed_deepagents._log import logger
from managed_deepagents._schedule_auth import hand_off_scheduled_thread, schedule_execution
from managed_deepagents._thread_record import (
    THREAD_CONVERSATION_METADATA,
    ThreadRecord,
    origin_from_binding,
    thread_record_metadata,
)

if TYPE_CHECKING:
    from langchain_core.runnables.config import RunnableConfig
    from langgraph.types import Interrupt, StateSnapshot

    from managed_deepagents._channels.binding import ChannelBinding
    from managed_deepagents._channels.http.types import HttpDelivery
    from managed_deepagents._channels.trigger.types import (
        TriggerChannelInterrupt,
        TriggerStartedThread,
    )
    from managed_deepagents._channels.types import ChannelMessage, MessageContent
    from managed_deepagents._ingress_runs import StartedIngressRun
    from managed_deepagents._managed_thread import RunThread

#: Checkpoint metadata ``source`` of the checkpoint LangGraph writes before a run's input.
INPUT_SOURCE = "input"
#: What a new Trigger thread posted when a scheduled run pauses says.
PAUSED_NOTICE = "This scheduled run is waiting for a decision."


@dataclass(frozen=True)
class _ChannelOpening:
    """A new-thread schedule on a Trigger channel."""

    origin: ChannelBinding


@dataclass(frozen=True)
class _DeploymentOpening:
    """A ``deliver_to`` conversation Trigger delivers to."""

    deliver_to: NormalizedScheduleDeliverTo


_Opening = _ChannelOpening | _DeploymentOpening


def delivers_result(
    run: RunThread, config: RunnableConfig, routing: Mapping[str, object] | None
) -> bool:
    """Whether a finished run has somewhere to deliver: its thread origin, or a ``deliver_to``."""
    return run.origin is not None or _scheduled_deliver_to(config, routing) is not None


async def deliver_run_result(
    run: RunThread,
    config: RunnableConfig,
    final: StateSnapshot,
    boundary: StateSnapshot | None,
    *,
    routing: Mapping[str, object] | None = None,
    subagent: bool,
    stateless: bool = False,
) -> None:
    """Report a paused run, or post a finished run's answers, to the run's thread origin.

    A scheduled run without an origin posts to its trusted ``deliver_to``
    instead. ``final`` is the run's last state and ``boundary`` the latest input
    checkpoint, which marks where this run's messages start. An async subagent
    run reports its pause (to its lead's origin) but never posts an answer, and
    a ``stateless`` run, which keeps no thread to hand off, opens none.
    """
    binding = run.origin
    deliver_to = None if binding is not None or subagent else _scheduled_deliver_to(config, routing)
    if binding is None and deliver_to is None:
        return
    opening = None if subagent or stateless else _opening_of(binding, deliver_to, config, routing)
    pending = [item for item in final.interrupts if item.id]
    if pending:
        await _deliver_pause(run, config, opening, pending)
    # A run stopped at a breakpoint has not finished, and an async subagent never answers.
    elif not final.next and not subagent:
        await _deliver_answer(run, config, final, boundary, opening=opening, deliver_to=deliver_to)


async def _deliver_pause(
    run: RunThread, config: RunnableConfig, opening: _Opening | None, pending: Sequence[Interrupt]
) -> None:
    """Report a paused run in the new thread it opens, or to its thread origin."""
    binding = run.origin
    thread_id = config["configurable"].get("thread_id")
    run_id = run_id_of(config)
    if opening is not None:
        action_id = paused_action_id(run_id) if run_id else str(uuid.uuid4())
        opened = await _open_thread(opening, run, config, PAUSED_NOTICE, action_id)
        if opened is not None and thread_id and run_id:
            await _report_pause(opened, str(thread_id), run_id, pending)
    elif binding is None or not thread_id:
        return
    elif binding["transport"] == "direct":
        await _notify_paused(binding, str(thread_id), _assistant_id(config))
    elif run_id:
        await _report_pause(binding, str(thread_id), run_id, pending)


async def _deliver_answer(
    run: RunThread,
    config: RunnableConfig,
    final: StateSnapshot,
    boundary: StateSnapshot | None,
    *,
    opening: _Opening | None,
    deliver_to: NormalizedScheduleDeliverTo | None,
) -> None:
    """Post a finished run's answers in the thread it opens, its origin, or ``deliver_to``."""
    binding = run.origin
    # A channel declared with ``reply="none"`` leaves replying to the agent.
    if binding is not None and not channel_auto_replies_for_name(binding["name"]):
        return
    values = final.values if isinstance(final.values, Mapping) else {}
    output = {"messages": values.get("messages")}
    after_id = run_boundary(boundary, output["messages"])
    text = extract_reply_text(output, after_id=after_id)
    if text.strip() == QUIET_REPLY:
        return
    if opening is not None:
        if text:
            run_id = run_id_of(config)
            action_id = reply_action_id(run_id) if run_id else str(uuid.uuid4())
            await _open_thread(opening, run, config, text, action_id)
    elif binding is None:
        if deliver_to is not None:
            await _deliver_to_target(deliver_to, text)
    elif binding["transport"] == "trigger_server":
        if text:
            await _post_trigger(binding, text, run_id_of(config))
    elif (content := extract_reply_content(output, after_id=after_id)) is not None:
        scheduled = is_trusted_schedule_delivery_run(
            config["configurable"], config.get("metadata") or {}
        )
        await _post_direct(binding, content, scheduled=scheduled)


def run_boundary(boundary: StateSnapshot | None, messages: object) -> str | None:
    """The id of the last message before this run's input, if the final messages still hold it.

    The input checkpoint holds the state before the run's input. A resume or a
    deepagents in-run notification turn writes none, so either belongs to the
    run that wrote the latest one. ``None`` falls back to the last user message.
    """
    if boundary is None or _field(boundary.metadata, "source") != INPUT_SOURCE:
        return None
    before = _field(boundary.values, "messages")
    last_id = _field(before[-1], "id") if isinstance(before, list) and before else None
    if not isinstance(last_id, str) or not isinstance(messages, list):
        return None
    return last_id if any(_field(message, "id") == last_id for message in messages) else None


def run_id_of(config: RunnableConfig) -> str | None:
    """The Agent Server run executing this graph, wherever the config carries it."""
    metadata = config.get("metadata") or {}
    for value in (
        config.get("run_id"),
        config["configurable"].get("run_id"),
        metadata.get("run_id"),
    ):
        if value:
            return str(value)
    return None


def reply_action_id(run_id: str) -> str:
    """The Trigger action id of a run's reply, the same when the run is retried."""
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"mda-reply:{run_id}"))


def paused_action_id(run_id: str) -> str:
    """The Trigger action id of a paused run's notice, the same when the run is retried."""
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"mda-paused:{run_id}"))


def _scheduled_deliver_to(
    config: RunnableConfig, routing: Mapping[str, object] | None
) -> NormalizedScheduleDeliverTo | None:
    """A trusted cron fire's ``deliver_to``, when it posts its result."""
    if not is_trusted_schedule_delivery_run(config["configurable"], config.get("metadata")):
        return None
    deliver_to = read_schedule_deliver_to(_field(_field(routing, "mda_schedule"), "deliver_to"))
    return deliver_to if deliver_to is not None and deliver_to["auto_post"] else None


def _opening_of(
    binding: ChannelBinding | None,
    deliver_to: NormalizedScheduleDeliverTo | None,
    config: RunnableConfig,
    routing: Mapping[str, object] | None,
) -> _Opening | None:
    """Where a scheduled run starts a new Trigger thread.

    A new-thread schedule on a Trigger channel, or a new-thread ``deliver_to``
    conversation Trigger delivers to. A schedule with a fixed thread keeps it.
    """
    if schedule_execution(config["configurable"]) is None:
        return None
    if binding is not None:
        if binding["transport"] == "trigger_server" and _new_thread(binding):
            return _ChannelOpening(binding)
        return None
    if (
        deliver_to is None
        or _field(_field(routing, "mda_schedule"), "new_thread") is not True
        or deliver_to["to"]["type"] != "provider_conversation"
        or get_deployment_id() is None
    ):
        return None
    try:
        client = create_trigger_client()
    except ValueError:
        return None
    return _DeploymentOpening(deliver_to) if client is not None else None


async def _open_thread(
    opening: _Opening, run: RunThread, config: RunnableConfig, text: str, action_id: str
) -> ChannelBinding | None:
    """Post the first message of a new Trigger thread, then hand the run's thread to it.

    Returns the thread's conversation, unless Trigger could not name the thread
    it started.
    """
    client = create_trigger_client()
    if client is None:
        raise RuntimeError("Channel delivery is unavailable")
    started: TriggerStartedThread | None
    if isinstance(opening, _ChannelOpening):
        name = opening.origin["name"]
        provider = opening.origin["provider"]
        started = await start_trigger_thread(
            client, target=opening.origin["target"], text=text, action_id=action_id
        )
    else:
        name = opening.deliver_to["channel"]
        provider = channel_provider_for_name(name) or name
        deployment_id = get_deployment_id()
        if deployment_id is None:
            raise RuntimeError("Channel delivery is unavailable")
        result = await client.post_deployment_action(
            deployment_id,
            name,
            {
                "type": "post",
                "action_id": action_id,
                "address": {"conversation_id": opening.deliver_to["to"]["conversation_id"]},
                "follow_thread": True,
                "message": {"text": text},
            },
        )
        started = result.get("thread")
    if started is None:
        logger.warning("scheduled post started a thread Trigger did not name")
        return None
    conversation: ChannelBinding = {
        "name": name,
        "provider": provider,
        "transport": "trigger_server",
        "source_thread_key": started["source_thread_key"],
        "delivery_id": action_id,
        "target": {
            "channel_id": started["channel_id"],
            "address": started["address"],
            "source_thread_key": started["source_thread_key"],
        },
    }
    await _hand_off(run, config, conversation)
    return conversation


async def _hand_off(run: RunThread, config: RunnableConfig, conversation: ChannelBinding) -> None:
    """Make the run's thread the conversation's, keeping its participants."""
    execution = schedule_execution(config["configurable"])
    if execution is None or not run.thread.id:
        raise RuntimeError("Scheduled thread handoff is unavailable")
    record = thread_record_metadata(
        ThreadRecord(
            origin=origin_from_binding(conversation, None),
            participants={person.id: person.role for person in run.thread.participants},
        )
    )
    await hand_off_scheduled_thread(
        {
            "thread": run.thread.id,
            "execution": execution,
            "conversation": {
                "provider": conversation["provider"],
                "source_thread_key": conversation["source_thread_key"],
            },
        },
        {**record, THREAD_CONVERSATION_METADATA: conversation["source_thread_key"]},
    )


async def _deliver_to_target(deliver_to: NormalizedScheduleDeliverTo, text: str) -> None:
    """Post a ``deliver_to`` result into an existing conversation; a failure fails the run."""
    outcome = await deliver_schedule_result(deliver_to=deliver_to, reply_text=text)
    if outcome == "failed":
        raise RuntimeError("Scheduled channel delivery failed")


def _new_thread(binding: ChannelBinding) -> bool:
    """Whether a new-thread schedule's run starts a new provider thread."""
    event = binding.get("event")
    return event is not None and event["type"] == "schedule" and bool(event["new_thread"])


async def _post_trigger(binding: ChannelBinding, text: str, run_id: str | None) -> None:
    """Post to a Trigger thread; a failure fails the run, so Trigger reports it."""
    client = create_trigger_client()
    if client is None:
        raise RuntimeError("Channel delivery is unavailable")
    logger.info(
        "Channel outgoing action (type=post, delivery_id=%s, run_id=%s)",
        logger.log_identifier(binding["delivery_id"]),
        logger.log_identifier(run_id or ""),
    )
    transport = TriggerTransport(
        client, action_id=reply_action_id(run_id) if run_id is not None else None
    )
    await transport.post(
        message={"type": "content", "content": text},
        target=binding["target"],
        new_thread=_new_thread(binding),
    )


async def _post_direct(
    binding: ChannelBinding, content: MessageContent, *, scheduled: bool
) -> None:
    """Post to an HTTP channel.

    The answer stays in the thread when that fails, so the run still succeeds;
    only a scheduled run, which no one else reads, fails.
    """
    try:
        await _post_to_transport(binding, {"type": "content", "content": content})
    except Exception as error:
        if scheduled:
            raise
        logger.error("channel reply failed (%s)", logger.safe_error_label(error))


async def _post_to_transport(binding: ChannelBinding, message: ChannelMessage) -> None:
    transport = messaging_transport_for_name(binding["name"])
    if transport is None:
        raise RuntimeError("Channel delivery is unavailable")
    posted = transport.post(
        message=message,
        target=binding["target"],
        **({"new_thread": True} if _new_thread(binding) else {}),
    )
    if inspect.isawaitable(posted):
        await posted


async def _report_pause(
    binding: ChannelBinding, run_thread_id: str, run_id: str, pending: Sequence[Interrupt]
) -> None:
    """Show a paused run's pending interrupts in its Trigger thread.

    Sends ``run.interrupted``, so Trigger shows the approval in the thread and
    its answer resumes ``run_thread_id`` (an async subagent's own thread). Only
    Trigger channels show interrupts this way; HTTP channels post a notice.
    """
    if binding["transport"] != "trigger_server":
        return
    from managed_deepagents._channels.trigger.http import (  # noqa: PLC0415 - avoids an import cycle
        RuntimeEventRoute,
        _best_effort,
        _runtime_event,
    )

    try:
        target = _TriggerTargetModel.model_validate(binding["target"])
        client = create_trigger_client()
        if client is None:
            logger.error("channel interrupt: Trigger channel is unavailable")
            return
        route: RuntimeEventRoute = {
            "delivery_id": binding["delivery_id"],
            "source_thread_key": binding["source_thread_key"],
            "address": {**target.address},
        }
        started: StartedIngressRun = {"thread_id": run_thread_id, "run_id": run_id}
        body = _runtime_event(
            "run.interrupted",
            route,
            started,
            _forwarded(pending),
            tracer_session_id=await tracer_session_id(),
        )
        await _best_effort(client.post_runtime_event(target.channel_id, body))
    except Exception as error:  # noqa: BLE001 - reporting a pause never fails the run
        logger.error("channel interrupt report failed (%s)", logger.safe_error_label(error))


async def _notify_paused(
    binding: ChannelBinding, run_thread_id: str, assistant_id: str | None
) -> None:
    """Tell an HTTP channel's conversation that the run waits on a decision.

    The notice links to ``run_thread_id`` in Studio, where the decision is made
    (an async subagent's own thread). Only channels with a notice (GitHub,
    Linear) post one.
    """
    notice = paused_notice_for_name(binding["name"])
    if notice is None:
        return
    delivery: HttpDelivery = {"thread_id": run_thread_id}
    if assistant_id is not None:
        delivery["assistant_id"] = assistant_id
    try:
        await notice(binding["target"], delivery)
    except Exception as error:  # noqa: BLE001 - reporting a pause never fails the run
        logger.error("channel paused notice failed (%s)", logger.safe_error_label(error))


def _assistant_id(config: RunnableConfig) -> str | None:
    metadata = config.get("metadata") or {}
    for value in (config["configurable"].get("assistant_id"), metadata.get("assistant_id")):
        if isinstance(value, str) and value:
            return value
    return None


def _forwarded(pending: Sequence[Interrupt]) -> list[TriggerChannelInterrupt]:
    interrupts: list[TriggerChannelInterrupt] = []
    for item in pending:
        interrupt: TriggerChannelInterrupt = {
            "id": item.id,
            "value": item.value,
        }
        if isinstance(item.response_schema, dict):
            interrupt["response_schema"] = item.response_schema
        interrupts.append(interrupt)
    return interrupts


__all__ = [
    "INPUT_SOURCE",
    "PAUSED_NOTICE",
    "deliver_run_result",
    "delivers_result",
    "reply_action_id",
    "run_boundary",
    "run_id_of",
]
