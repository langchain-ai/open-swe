from __future__ import annotations

import asyncio
import copy
import functools
import inspect
import json
import os
import re
import uuid
from collections.abc import Awaitable, Callable, Mapping
from typing import TYPE_CHECKING, Any, Literal, TypeVar, cast

from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.types import Message

from managed_deepagents import _ingress
from managed_deepagents._channels.http import ingress as http_ingress
from managed_deepagents._channels.http.types import (
    HttpChannelCancelResult,
    HttpChannelMessageResult,
    HttpChannelParseResult,
    HttpChannelRequest,
)
from managed_deepagents._channels.resolve_user import (
    post_reply,
    resolve_channel_user,
    sender_principal_id,
    user_resolution,
)
from managed_deepagents._channels.run_input import (
    InvalidRunInput,
    NormalizedRunInput,
    RunChannel,
    normalize_run_inputs,
)
from managed_deepagents._channels.runtime import is_json_value, json_copy
from managed_deepagents._channels.types import ChannelError
from managed_deepagents._log import logger
from managed_deepagents._telemetry.otel import set_log_attributes

if TYPE_CHECKING:
    from langgraph_sdk.schema import MultitaskStrategy

    from managed_deepagents._channel import ChannelEventsContext
    from managed_deepagents._channels.actor import ChannelActor
    from managed_deepagents._channels.binding import ChannelBinding
    from managed_deepagents._channels.http import HttpChannel
    from managed_deepagents._channels.http.ingress import (
        PrepareInput,
        SkippedIngressRun,
    )
    from managed_deepagents._channels.http.types import (
        HttpChannelCancelMessage,
        HttpChannelInboundMessage,
        HttpDelivery,
        HttpFinish,
        HttpFinishedDelivery,
        HttpOnError,
        HttpOnFinish,
        HttpPost,
    )
    from managed_deepagents._channels.resolve_user import (
        ChannelUser,
        ResolvedIgnore,
        ResolvedReply,
    )
    from managed_deepagents._channels.types import (
        ChannelEvent,
        ChannelReply,
        JsonValue,
        MessageContent,
    )
    from managed_deepagents._ingress_runs import StartedIngressRun

    _Finish = Callable[["HttpFinish", "str | None"], Awaitable[None]]
    """Tell the channel the message is done, with the thread's assistant when known."""

_T = TypeVar("_T")


async def _resolve(value: _T | Awaitable[_T]) -> _T:
    if inspect.isawaitable(value):
        return await cast("Awaitable[_T]", value)
    return cast("_T", value)


def _adapter_request(source: HttpChannelRequest) -> HttpChannelRequest:
    sent = False

    async def receive() -> Message:
        nonlocal sent
        if sent:
            return await source.request.receive()
        sent = True
        return {"type": "http.request", "body": source.raw_body, "more_body": False}

    return HttpChannelRequest(
        request=Request(dict(source.request.scope), receive=receive),
        raw_body=source.raw_body,
    )


async def _parse_request(
    request: HttpChannelRequest, channel: HttpChannel
) -> HttpChannelMessageResult | HttpChannelCancelResult | Response:
    """The verified request's message or cancel, or the response that ends it."""
    result = await _verified_parse(request, channel)
    if isinstance(result, Response):
        return result
    if result["type"] == "ignore":
        return result.get("response") or JSONResponse({"status": "ignored"}, status_code=202)
    valid = (
        _valid_cancel(result.get("message"))
        if result["type"] == "cancel"
        else _valid_message(result.get("message"), adapter_input=channel.input is not None)
    )
    if not valid:
        return _error(400, "invalid channel message")
    return result


async def _verified_parse(
    request: HttpChannelRequest, channel: HttpChannel
) -> HttpChannelParseResult | Response:
    try:
        verified = await _resolve(channel.verify(_adapter_request(request)))
    except Exception as error:  # noqa: BLE001
        logger.error(
            "channel verification failed (%s)", logger.safe_error_label(error), error=error
        )
        return _error(500, "channel verification failed")
    if not verified:
        return _error(401, "invalid channel signature")

    try:
        result = await _resolve(channel.parse(_adapter_request(request)))
    except Exception as error:  # noqa: BLE001
        logger.warning("channel payload rejected (%s)", logger.safe_error_label(error))
        return _error(400, "invalid channel payload")
    if (
        not isinstance(result, Mapping)
        or result.get("type") not in ("ignore", "message", "cancel")
        or ("response" in result and not isinstance(result["response"], Response))
        or ("raw_event" in result and not is_json_value(result["raw_event"]))
    ):
        return _error(400, "invalid channel parse result")
    return result


async def _channel_user(
    ctx: ChannelEventsContext, actor: ChannelActor | None, delivery_id: str
) -> tuple[ChannelUser | None, str | None]:
    """What ``resolve_user`` answers about the sender, and the sender's own
    principal when it keeps the channel default."""
    user = await resolve_channel_user(ctx.get("resolve_user"), actor, ctx["name"], delivery_id)
    return user, (await sender_principal_id(actor) if user is None else None)


async def handle_http_request(ctx: ChannelEventsContext, channel: HttpChannel) -> Response:
    raw = ctx.get("raw_body")
    if isinstance(raw, str):
        raw = raw.encode()
    if not isinstance(raw, bytes):
        raise ValueError("managed channel route did not provide request bytes")
    request = HttpChannelRequest(request=cast("Request", ctx["request"]), raw_body=raw)
    result = await _parse_request(request, channel)
    if isinstance(result, Response):
        return result

    assistant_id = os.environ.get("MDA_ASSISTANT_ID", "").strip()
    if not assistant_id:
        return _error(500, "channel runtime is not configured")
    if result["type"] == "cancel":
        stop = result["message"]
        stopped_thread = stop["thread_id"].lower()
        return await _cancel_runs(
            ctx["name"],
            channel,
            {**stop, "target": json_copy(stop["target"])},
            _source_thread_key(ctx["name"], stopped_thread),
            _delivery(stopped_thread, assistant_id, result.get("raw_event")),
            result.get("response"),
        )
    message = _copied_message(result["message"])
    delivery_id = str(uuid.uuid4())
    try:
        user, sender_id = await _channel_user(ctx, result.get("actor"), delivery_id)
    except Exception as error:  # noqa: BLE001 - the app's hook may raise anything
        logger.error("channel resolve_user failed (%s)", logger.safe_error_label(error))
        return _error(500, "channel user resolution failed")
    principal_id = user["id"] if user is not None and user["type"] == "principal" else None
    user_id = principal_id or sender_id or message["user_id"]
    # Ingress copies this onto the runs it starts, for their telemetry.
    set_log_attributes(
        {"user_resolution": user_resolution(user, ran_as_sender=sender_id is not None)}
    )
    content = message.get("content")
    thread_id = message["thread_id"].lower()
    source_thread_key = _source_thread_key(ctx["name"], thread_id)
    raw_event = result.get("raw_event")
    channel_event: ChannelEvent = {
        "type": "message",
        "messages": [] if content is None else [http_ingress.normalize_http_message(content)],
    }
    binding: ChannelBinding = {
        "name": ctx["name"],
        "provider": channel.kind,
        "source_thread_key": source_thread_key,
        "delivery_id": delivery_id,
        "transport": "direct",
        "target": message["target"],
        "event": channel_event,
    }
    if "raw_event" in result:
        binding["raw_event"] = json_copy(result["raw_event"])

    # Joined before the first start, so a cancel during it reaches the message.
    waiter = _join_thread_waiters(thread_id)
    prepare = functools.partial(
        _prepare, channel, message, result.get("raw_event"), waiter, name=ctx["name"]
    )
    plan = _ConversationRun(
        run_input=_run_input(user, ctx["name"], channel, message, prepare),
        multitask_strategy=channel.multitask_strategy,
    )
    start = functools.partial(
        _start_conversation,
        plan,
        user_id=user_id,
        principal_id=principal_id,
        thread_id=thread_id,
        source_thread_key=source_thread_key,
        source_provider=channel.kind,
        binding=binding,
        assistant_id=assistant_id,
        delivery_key=message.get("delivery_key"),
    )
    planning = _Delivery(
        name=ctx["name"],
        channel=channel,
        message=message,
        binding=binding,
        raw_event=raw_event,
        user_id=user_id,
        principal_id=principal_id,
        assistant_id=assistant_id,
        delivery_id=delivery_id,
        # The channel's input decides what the delivery starts, rather than its
        # content or resolve_user's reply: it may start runs on threads of its own.
        plans=plan.run_input.get("prepare") is prepare,
    )

    # Every message gets one ``on_received`` and, once it is done, ``on_finish``.
    received = _schedule_received(
        ctx["name"],
        channel,
        json_copy(message["target"]),
        _delivery(thread_id, assistant_id, raw_event),
    )
    finish: _Finish = functools.partial(
        _finish,
        channel.on_finish,
        received,
        json_copy(message["target"]),
        thread_id,
        assistant_id,
        raw_event,
    )
    label = f'channel "{ctx["name"]}" delivery'
    deliver = functools.partial(
        _deliver_reply,
        binding,
        user_id,
        source_thread_key,
        principal_id=principal_id,
        post=channel.post,
        on_error=channel.on_error,
        finish=finish,
    )
    wait = functools.partial(
        http_ingress.wait_for_http_ingress_thread,
        user_id=user_id,
        principal_id=principal_id,
        source_thread_key=source_thread_key,
        source_provider=channel.kind,
        thread_id=thread_id,
    )
    start_when_idle = functools.partial(
        _start_when_idle, waiter, start, wait, deliver, finish, thread_id
    )

    async def start_first() -> StartedIngressRun | SkippedIngressRun | None:
        await _plan_runs(plan, planning, waiter)
        outcome = await _start_first(thread_id, waiter, start)
        if outcome is None and channel.on_finish is not None:
            # Answered with an error, the provider may redeliver it, as a new
            # delivery: only one it already accepted is done for good.
            reason: Literal["start", "retry"] = "start" if message.get("background") else "retry"
            _ingress.schedule_ingress_work(
                lambda: finish({"type": "failed", "reason": reason, "interrupts": []}, None),
                label=label,
            )
        return outcome

    return await _start_and_answer(
        start_first,
        functools.partial(
            _follow_up,
            deliver=deliver,
            start_when_idle=start_when_idle,
            finish=(
                functools.partial(_finish_skipped, finish=finish, waiter=waiter)
                if channel.on_finish is not None
                else None
            ),
            label=label,
            thread_id=thread_id,
        ),
        background=message.get("background", False),
        label=label,
        accepted={"status": "accepted", "deliveryId": delivery_id},
        response=result.get("response"),
    )


def _follow_up(
    outcome: StartedIngressRun | SkippedIngressRun,
    *,
    deliver: Callable[[StartedIngressRun], Awaitable[None]],
    start_when_idle: Callable[[], Awaitable[None]],
    finish: Callable[[SkippedIngressRun], Awaitable[None]] | None,
    label: str,
    thread_id: str,
) -> None:
    """What follows the first start, in the background."""
    skipped = _skipped(outcome)
    if skipped is None:
        started = cast("StartedIngressRun", outcome)
        _ingress.schedule_ingress_work(lambda: deliver(started), label=label)
        return
    logger.info("channel run skipped %s", json.dumps({"threadId": thread_id, "reason": skipped}))
    _schedule_skipped(cast("SkippedIngressRun", outcome), start_when_idle, finish, label)


async def _start_and_answer(
    start_first: Callable[[], Awaitable[StartedIngressRun | SkippedIngressRun | None]],
    follow_up: Callable[[StartedIngressRun | SkippedIngressRun], None],
    *,
    background: bool,
    label: str,
    accepted: dict[str, str],
    response: Response | None,
) -> Response:
    """Start the message's run and answer the provider.

    Input that may take longer than the provider waits, such as a handler's, is
    decided after it has its answer.
    """
    if background:

        async def start_in_background() -> None:
            outcome = await start_first()
            if outcome is not None:
                follow_up(outcome)

        _ingress.schedule_ingress_work(start_in_background, label=label)
        return response or JSONResponse(accepted, status_code=202)
    outcome = await start_first()
    if outcome is None:
        return _error(500, "channel run could not be started")
    follow_up(outcome)
    skipped = _skipped(outcome)
    return response or JSONResponse(
        accepted if skipped is None else {"status": skipped}, status_code=202
    )


def _schedule_skipped(
    skipped: SkippedIngressRun,
    start_when_idle: Callable[[], Awaitable[None]],
    finish: Callable[[SkippedIngressRun], Awaitable[None]] | None,
    label: str,
) -> None:
    """Finish a message the thread took no run for, once it is no longer busy."""
    if skipped["skipped"] == "busy":
        _ingress.schedule_ingress_work(start_when_idle, label=label)
    elif finish is not None:
        _ingress.schedule_ingress_work(lambda: finish(skipped), label=label)


def _schedule_received(
    name: str, channel: HttpChannel, target: JsonValue, delivery: HttpDelivery
) -> asyncio.Event:
    """Tell the channel a message reached its thread, before MDA decides what it does.

    The event is set once the channel was told, so ``on_finish`` comes after.
    """
    received = asyncio.Event()
    on_received = channel.on_received
    if on_received is None:
        received.set()
        return received

    async def notify() -> None:
        try:
            await on_received(target, delivery)
        except Exception as error:  # noqa: BLE001
            logger.error(
                "channel received notice failed (%s)", logger.safe_error_label(error), error=error
            )
        finally:
            received.set()

    _ingress.schedule_ingress_work(notify, label=f'channel "{name}" received notice')
    return received


async def _finish(
    on_finish: HttpOnFinish | None,
    received: asyncio.Event,
    target: JsonValue,
    thread_id: str,
    assistant_id: str,
    raw_event: JsonValue | None,
    done: HttpFinish,
    thread_assistant_id: str | None,
) -> None:
    """Tell the channel the message is done, once it was told it arrived."""
    if on_finish is None:
        return
    await received.wait()
    # The thread's assistant lets a notice link to it in Studio.
    delivery = _finished(_delivery(thread_id, thread_assistant_id or assistant_id, raw_event), done)
    try:
        await on_finish(json_copy(target), delivery)
    except Exception as error:  # noqa: BLE001
        logger.error(
            "channel finish notice failed (%s)", logger.safe_error_label(error), error=error
        )


def _finished(delivery: HttpDelivery, done: HttpFinish) -> HttpFinishedDelivery:
    """The delivery with how it finished, copied so the channel can't change MDA's."""
    return cast(
        "HttpFinishedDelivery",
        {**delivery, **cast("dict[str, object]", json_copy(cast("JsonValue", done)))},
    )


async def _finish_skipped(
    skipped: SkippedIngressRun, *, finish: _Finish, waiter: _ThreadWaiter
) -> None:
    """A message the thread took no run for is done, with why; one that waits says on what."""
    reason = skipped["skipped"]
    if reason == "paused":
        await finish(
            {"type": "skipped", "reason": "waiting", "interrupts": skipped.get("interrupts", [])},
            skipped.get("assistant_id"),
        )
    elif reason == "busy":
        raise RuntimeError("a busy thread's message is not done")
    else:
        dropped = reason == "ignored" and waiter.dropped
        await finish(
            {"type": "skipped", "reason": "dropped" if dropped else reason, "interrupts": []},
            None,
        )


class _ConversationRun:
    """The run a delivery starts on its own conversation, once its input is known."""

    __slots__ = ("multitask_strategy", "reply", "run_input", "skip")

    def __init__(self, run_input: dict[str, object], multitask_strategy: MultitaskStrategy) -> None:
        self.run_input = run_input
        self.multitask_strategy = multitask_strategy
        self.reply: ChannelReply | None = None
        #: The conversation waits on a decision and the channel's input
        #: started no run on it: it takes none, as ``ignored``.
        self.skip = False


class _Delivery:
    """What every run a delivery starts shares."""

    __slots__ = (
        "assistant_id",
        "binding",
        "channel",
        "delivery_id",
        "message",
        "name",
        "plans",
        "principal_id",
        "raw_event",
        "user_id",
    )

    def __init__(
        self,
        *,
        name: str,
        channel: HttpChannel,
        message: HttpChannelInboundMessage,
        binding: ChannelBinding,
        raw_event: JsonValue | None,
        user_id: str,
        principal_id: str | None,
        assistant_id: str,
        delivery_id: str,
        plans: bool,
    ) -> None:
        self.plans = plans
        self.name = name
        self.channel = channel
        self.message = message
        self.binding = binding
        self.raw_event = raw_event
        self.user_id = user_id
        self.principal_id = principal_id
        self.assistant_id = assistant_id
        self.delivery_id = delivery_id


async def _plan_runs(plan: _ConversationRun, delivery: _Delivery, waiter: _ThreadWaiter) -> None:
    """Ask the channel's input what the delivery starts, before any thread is read for it.

    Its run on the event's conversation becomes ``plan``'s input, which the
    conversation's own start uses as before; every other run starts on its own
    thread, in the background. A conversation that waits on a decision is
    asked through the channel's ``interrupted_input``: its run there is then
    skipped, as ``waiting``, while the others start. A conversation that has
    seen the delivery already starts nothing, and the input isn't asked.
    """
    if not delivery.plans:
        return
    message = delivery.message
    thread_id = message["thread_id"].lower()
    try:
        state = await http_ingress.http_ingress_thread_state(
            user_id=delivery.user_id,
            principal_id=delivery.principal_id,
            source_thread_key=_source_thread_key(delivery.name, thread_id),
            source_provider=delivery.channel.kind,
            thread_id=thread_id,
            delivery_key=message.get("delivery_key"),
        )
    except Exception as error:  # noqa: BLE001
        # The conversation's own start reads it again, and says what it finds.
        logger.warning(
            "channel thread could not be read before its input (%s)",
            logger.safe_error_label(error),
        )
        state = None
    if state == "duplicate":
        return
    channel = delivery.channel
    paused = state == "paused"
    runs = await _prepare_runs(
        channel,
        message,
        delivery.raw_event,
        waiter,
        RunChannel(name=delivery.name, provider=channel.kind),
        interrupted=paused,
    )
    conversation = next((run for run in runs if "thread" not in run), None)
    if (paused or conversation is None) and any("thread" in run for run in runs):
        # The conversation starts no run for the delivery, so its thread records
        # it: a redelivery is then a duplicate, and starts nothing twice.
        await _record_conversation_delivery(delivery, thread_id)
    for index, run in enumerate(runs):
        if "thread" in run:
            _ingress.schedule_ingress_work(
                functools.partial(_start_threaded_run, delivery, run, index),
                label=f'channel "{delivery.name}" delivery',
            )
    if conversation is not None:
        plan.multitask_strategy = conversation.get("multitask_strategy", plan.multitask_strategy)
        plan.reply = conversation.get("reply")
    plan.run_input = {"prepare": functools.partial(_planned, conversation)}
    # A waiting conversation would say it waits for a run its input didn't ask for.
    plan.skip = paused and conversation is None


async def _record_conversation_delivery(delivery: _Delivery, thread_id: str) -> None:
    delivery_key = delivery.message.get("delivery_key")
    if delivery_key is None:
        return
    try:
        await http_ingress.record_http_ingress_delivery(
            user_id=delivery.user_id,
            principal_id=delivery.principal_id,
            source_thread_key=_source_thread_key(delivery.name, thread_id),
            source_provider=delivery.channel.kind,
            thread_id=thread_id,
            delivery_key=delivery_key,
        )
    except Exception as error:  # noqa: BLE001
        # The runs still start; only a redelivery may start them again.
        logger.warning(
            "channel delivery was not recorded on its conversation (%s)",
            logger.safe_error_label(error),
        )


async def _planned(run: NormalizedRunInput | None) -> NormalizedRunInput | None:
    return run


async def _start_conversation(
    plan: _ConversationRun,
    *,
    binding: ChannelBinding,
    **options: Any,
) -> StartedIngressRun | SkippedIngressRun:
    """Start the delivery's run on its own conversation, as its plan says."""
    if plan.skip:
        return {"skipped": "ignored"}
    return await http_ingress.start_http_ingress_agent(
        binding=_with_reply(copy.deepcopy(binding), plan.reply),
        multitask_strategy=plan.multitask_strategy,
        **options,
        **plan.run_input,
    )


async def _prepare_runs(
    channel: HttpChannel,
    message: HttpChannelInboundMessage,
    raw_event: JsonValue | None,
    waiter: _ThreadWaiter,
    run_channel: RunChannel,
    *,
    interrupted: bool = False,
) -> list[NormalizedRunInput]:
    """What the channel's ``input`` starts; failures start nothing.

    While the conversation waits on a decision, the channel's
    ``interrupted_input`` is asked instead, when it has one.
    """
    input_ = (channel.interrupted_input if interrupted else None) or channel.input
    if input_ is None:
        return []
    try:
        result = await _resolve(
            input_(_copied_message(message), None if raw_event is None else json_copy(raw_event))
        )
    except Exception as error:  # noqa: BLE001
        logger.error("channel input failed (%s)", logger.safe_error_label(error), error=error)
        return []
    try:
        runs = normalize_run_inputs(result, run_channel)
    except InvalidRunInput as error:
        # MDA's own reason, bounded, unlike a raised exception's message.
        logger.error("channel input refused, no run started: %s", error.reason)
        return []
    except Exception as error:  # noqa: BLE001
        logger.error("channel input failed (%s)", logger.safe_error_label(error), error=error)
        return []
    # A slow input, such as an endpoint's, may outlast a cancel.
    if runs and waiter.cancelled:
        logger.info(
            "channel run dropped: its thread was cancelled while its input was decided %s",
            json.dumps({"threadId": message["thread_id"].lower()}),
        )
        waiter.dropped = True
        return []
    return runs


async def _start_threaded_run(delivery: _Delivery, run: NormalizedRunInput, index: int) -> None:
    """Start a run on a thread its handler chose, and see it through.

    A persistent thread is the channel's own: its source key derives from the
    channel and the thread, as a conversation's does, so the channel reaches it
    again for a later event. An ephemeral thread's id derives from the delivery
    and the run's place in it, so a redelivery finds the run it started; the
    thread is deleted once the run ends, unless it waits on a decision. The run
    posts its answer to the event's conversation only with ``deliver_to``.
    """
    thread = run["thread"]
    message = delivery.message
    key = message.get("delivery_key") or delivery.delivery_id
    persistent = thread["mode"] == "persistent"
    thread_id = thread["id"] if persistent else _ephemeral_thread_id(delivery, key, index)
    source_thread_key = _source_thread_key(delivery.name, thread_id)
    replies = run.get("deliver_to_conversation", False)
    binding: ChannelBinding = {
        **copy.deepcopy(delivery.binding),
        "source_thread_key": source_thread_key,
        "reply": "auto" if replies else "none",
    }
    channel = delivery.channel
    start = functools.partial(
        http_ingress.start_http_ingress_agent,
        user_id=delivery.user_id,
        principal_id=delivery.principal_id,
        thread_id=thread_id,
        source_thread_key=source_thread_key,
        source_provider=channel.kind,
        binding=binding,
        assistant_id=delivery.assistant_id,
        # Keyed by its thread, not its place among the handler's runs: a resent
        # delivery whose handler starts a different set still finds its run.
        delivery_key=f"{key}#{thread_id}",
        multitask_strategy=run.get("multitask_strategy", channel.multitask_strategy),
        prepare=functools.partial(_planned, run),
    )

    async def ended(done: HttpFinish, _assistant_id: str | None) -> None:
        # A run that waits on a decision keeps its thread, as a schedule's does.
        if not persistent and done["type"] == "run" and not done["interrupts"]:
            await _delete_thread(delivery, source_thread_key, thread_id)

    async def skipped_finish(_done: HttpFinish, _assistant_id: str | None) -> None:
        return

    deliver = functools.partial(
        _deliver_reply,
        binding,
        delivery.user_id,
        source_thread_key,
        # Only a run that posts where the event came from reports its failure there.
        post=channel.post if replies else None,
        on_error=channel.on_error if replies else None,
        finish=ended,
        principal_id=delivery.principal_id,
        report_failure=replies,
    )

    waiter = _join_thread_waiters(thread_id)
    outcome = await _start_first(thread_id, waiter, start)
    if outcome is None:
        return
    skipped = _skipped(outcome)
    if skipped is None:
        await deliver(cast("StartedIngressRun", outcome))
    elif skipped == "busy":
        wait = functools.partial(
            http_ingress.wait_for_http_ingress_thread,
            user_id=delivery.user_id,
            principal_id=delivery.principal_id,
            source_thread_key=source_thread_key,
            source_provider=channel.kind,
            thread_id=thread_id,
        )
        await _start_when_idle(waiter, start, wait, deliver, skipped_finish, thread_id)
    else:
        logger.info(
            "channel run skipped %s", json.dumps({"threadId": thread_id, "reason": skipped})
        )


def _ephemeral_thread_id(delivery: _Delivery, key: str, index: int) -> str:
    encoded = json.dumps([delivery.name, key, index], ensure_ascii=False, separators=(",", ":"))
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"mda-thread:v1:ephemeral:{encoded}"))


async def _delete_thread(delivery: _Delivery, source_thread_key: str, thread_id: str) -> None:
    try:
        await http_ingress.delete_http_ingress_thread(
            user_id=delivery.user_id,
            principal_id=delivery.principal_id,
            source_thread_key=source_thread_key,
            source_provider=delivery.channel.kind,
            thread_id=thread_id,
        )
    except Exception as error:  # noqa: BLE001
        logger.error(
            "ephemeral channel thread could not be deleted (%s)",
            logger.safe_error_label(error),
            error=error,
        )


def _with_reply(binding: ChannelBinding, reply: ChannelReply | None) -> ChannelBinding:
    if reply is not None:
        binding["reply"] = reply
    return binding


def _source_thread_key(name: str, thread_id: str) -> str:
    return "http:v1:" + json.dumps([name, thread_id], ensure_ascii=False, separators=(",", ":"))


async def _cancel_runs(
    name: str,
    channel: HttpChannel,
    message: HttpChannelCancelMessage,
    source_thread_key: str,
    delivery: HttpDelivery,
    response: Response | None,
) -> Response:
    """Cancel the thread's runs for a ``cancel`` result, then finish it.

    Messages waiting here for the thread are dropped too.
    """
    # Before the server's cancel, so waiters it wakes see it.
    thread_id = delivery["thread_id"]
    for waiter in _thread_waiters.get(thread_id, ()):
        waiter.cancelled = True

    try:
        cancelled = len(
            await http_ingress.cancel_http_ingress_runs(
                user_id=message["user_id"],
                source_thread_key=source_thread_key,
                source_provider=channel.kind,
                thread_id=thread_id,
            )
        )
    except Exception as error:  # noqa: BLE001
        logger.error(
            "channel run could not be cancelled (%s)", logger.safe_error_label(error), error=error
        )
        return _error(500, "channel run could not be cancelled")
    logger.info(
        "channel runs cancelled %s",
        json.dumps({"threadId": thread_id, "cancelled": cancelled}),
    )
    on_finish = channel.on_finish
    if on_finish is not None:
        target = json_copy(message["target"])

        async def notify() -> None:
            # A cancel starts no run of its own. A thread that waits on a decision
            # still does after it.
            waits_on: http_ingress.ThreadWaitsOn = {"interrupts": []}
            try:
                waits_on = await http_ingress.http_ingress_thread_waits_on(
                    user_id=message["user_id"],
                    source_thread_key=source_thread_key,
                    source_provider=channel.kind,
                    thread_id=thread_id,
                )
            except Exception as error:  # noqa: BLE001
                logger.error(
                    "channel thread could not be read (%s)",
                    logger.safe_error_label(error),
                    error=error,
                )
            finished: HttpDelivery = {**delivery}
            if "assistant_id" in waits_on:
                finished["assistant_id"] = waits_on["assistant_id"]
            done: HttpFinish = {
                "type": "skipped",
                "reason": "cancel",
                "interrupts": waits_on["interrupts"],
            }
            try:
                await on_finish(target, _finished(finished, done))
            except Exception as error:  # noqa: BLE001
                logger.error(
                    "channel finish notice failed (%s)", logger.safe_error_label(error), error=error
                )

        _ingress.schedule_ingress_work(notify, label=f'channel "{name}" finish notice')
    return response or JSONResponse(
        {"status": "cancelled", "cancelled": cancelled}, status_code=202
    )


def _copied_message(message: HttpChannelInboundMessage) -> HttpChannelInboundMessage:
    copied: HttpChannelInboundMessage = {**message, "target": json_copy(message["target"])}
    if "content" in message:
        copied["content"] = cast("MessageContent", json_copy(cast("JsonValue", message["content"])))
    return copied


def _skipped(outcome: StartedIngressRun | SkippedIngressRun) -> str | None:
    skipped = cast("Mapping[str, object]", outcome).get("skipped")
    return skipped if isinstance(skipped, str) else None


class _ThreadWaiter:
    """A message in this process that may still wait for its thread."""

    __slots__ = ("cancelled", "dropped")

    def __init__(self) -> None:
        self.cancelled = False
        self.dropped = False
        """Set when a cancel dropped the message while its input was decided."""


_thread_waiters: dict[str, set[_ThreadWaiter]] = {}
"""Messages in this process that may still wait for their thread, by thread.

A message waiting for its busy thread (the default ``enqueue`` strategy) is no
server-side run yet, so the server's cancel can't reach it: a cancel marks the
thread's waiters, and they drop themselves. A message joins before its first
start and leaves once its fate is settled, so only threads with messages in
flight are kept. Messages waiting in another replica aren't reached.

This is not durable: a waiting message lives only in this process, so a restart
loses it after the provider was already told it arrived. It waits here rather
than queueing on the server because a queued run starts as soon as the run
before it stops on ``interrupt()``, and its new input discards the decision the
thread waits on. A server-side queue that holds messages while a thread waits on
an interrupt would replace this; see
https://github.com/langchain-ai/langgraph/issues/8838.
"""


async def _start_first(
    thread_id: str,
    waiter: _ThreadWaiter,
    start: Callable[[], Awaitable[StartedIngressRun | SkippedIngressRun]],
) -> StartedIngressRun | SkippedIngressRun | None:
    """Start the message's run, or ``None`` when that failed.

    The message joined its thread's waiters first, so a cancel during the start
    reaches it, and stays only when it waits for its busy thread.
    """
    try:
        outcome = await start()
    except Exception as error:  # noqa: BLE001
        _leave_thread_waiters(thread_id, waiter)
        logger.error(
            "channel run could not be started (%s)", logger.safe_error_label(error), error=error
        )
        return None
    if _skipped(outcome) != "busy":
        _leave_thread_waiters(thread_id, waiter)
    return outcome


def _join_thread_waiters(thread_id: str) -> _ThreadWaiter:
    waiter = _ThreadWaiter()
    _thread_waiters.setdefault(thread_id, set()).add(waiter)
    return waiter


def _leave_thread_waiters(thread_id: str, waiter: _ThreadWaiter) -> None:
    waiters = _thread_waiters.get(thread_id)
    if waiters is None:
        return
    waiters.discard(waiter)
    if not waiters:
        del _thread_waiters[thread_id]


async def _start_when_idle(
    waiter: _ThreadWaiter,
    start: Callable[[], Awaitable[StartedIngressRun | SkippedIngressRun]],
    wait: Callable[[], Awaitable[None]],
    deliver: Callable[[StartedIngressRun], Awaitable[None]],
    finish: _Finish,
    thread_id: str,
) -> None:
    """Start a message whose thread was busy.

    The thread's run may still stop for a decision, so the message waits for it
    instead of queueing behind it, then checks again. A cancel while it waits
    drops it.
    """
    outcome: StartedIngressRun | SkippedIngressRun = {"skipped": "busy"}
    try:
        while (skipped := _skipped(outcome)) == "busy":
            logger.info("channel run waits %s", json.dumps({"threadId": thread_id}))
            await wait()
            if waiter.cancelled:
                logger.info(
                    "channel run dropped: its thread was cancelled while it waited %s",
                    json.dumps({"threadId": thread_id}),
                )
                await finish({"type": "skipped", "reason": "dropped", "interrupts": []}, None)
                return
            outcome = await start()
    except Exception as error:  # noqa: BLE001
        logger.error(
            "channel run could not be started (%s)", logger.safe_error_label(error), error=error
        )
        await finish({"type": "failed", "reason": "start", "interrupts": []}, None)
        return
    finally:
        _leave_thread_waiters(thread_id, waiter)
    if skipped is None:
        await deliver(cast("StartedIngressRun", outcome))
        return
    logger.info("channel run skipped %s", json.dumps({"threadId": thread_id, "reason": skipped}))
    await _finish_skipped(cast("SkippedIngressRun", outcome), finish=finish, waiter=waiter)


def _delivery(
    thread_id: str, assistant_id: str | None, raw_event: JsonValue | None = None
) -> HttpDelivery:
    delivery: HttpDelivery = {"thread_id": thread_id}
    if assistant_id is not None:
        delivery["assistant_id"] = assistant_id
    if raw_event is not None:
        delivery["raw_event"] = json_copy(raw_event)
    return delivery


async def _prepare(
    channel: HttpChannel,
    message: HttpChannelInboundMessage,
    raw_event: JsonValue | None,
    waiter: _ThreadWaiter,
    *,
    name: str,
) -> NormalizedRunInput | None:
    """What the channel's ``input`` starts on the event's conversation; failures start nothing.

    Asked here only when its runs weren't planned before the start, as when
    reading the conversation failed.
    """
    runs = await _prepare_runs(
        channel, message, raw_event, waiter, RunChannel(name=name, provider=channel.kind)
    )
    return next((run for run in runs if "thread" not in run), None)


async def _deliver_reply(
    binding: ChannelBinding,
    user_id: str,
    source_thread_key: str,
    started: StartedIngressRun,
    post: HttpPost | None,
    on_error: HttpOnError | None,
    finish: _Finish | None = None,
    principal_id: str | None = None,
    *,
    report_failure: bool = True,
) -> None:
    """See the run through: report a failure, and tell the channel how it ended."""
    run_id = started["run_id"]
    logger.info(
        "channel run started %s",
        json.dumps({"threadId": started["thread_id"], "runId": logger.log_identifier(run_id)}),
    )
    # The run posts its own answer (see ``reply.py``): this reports how it
    # ended, which the run can't say itself.
    try:
        outcome = await http_ingress.observe_http_ingress_agent(
            user_id=user_id,
            source_thread_key=source_thread_key,
            source_provider=binding["provider"],
            thread_id=started["thread_id"],
            run_id=run_id,
            principal_id=principal_id,
        )
    except Exception as error:  # noqa: BLE001
        logger.error(
            "channel run could not be observed (%s)", logger.safe_error_label(error), error=error
        )
        outcome = None
    # The run may have answered already, or may still, so no error is reported;
    # how it ended is unknown.
    if outcome is None:
        if finish is not None:
            await finish({"type": "failed", "reason": "observe", "interrupts": []}, None)
        return
    if outcome["status"] in ("error", "timeout"):
        logger.error("channel run failed %s", json.dumps({"threadId": started["thread_id"]}))
        if report_failure:
            await _notify_failed(
                ChannelError("channel_run_failed", "channel run failed"), binding, post, on_error
            )
    if finish is not None:
        await finish(
            {
                "type": "run",
                "run": {"id": run_id, "status": outcome["status"]},
                "interrupts": outcome["interrupts"],
            },
            outcome.get("assistant_id"),
        )


async def _notify_failed(
    error: Exception, binding: ChannelBinding, post: HttpPost | None, on_error: HttpOnError | None
) -> None:
    try:
        if on_error is not None:
            await on_error(error, json_copy(binding["target"]))
        elif post is not None:
            await post(
                {
                    "type": "content",
                    "content": "Something went wrong while processing your request.",
                    "target": json_copy(binding["target"]),
                }
            )
    except Exception as reply_error:  # noqa: BLE001
        action = "callback" if on_error is not None else "reply"
        logger.error(
            "channel error %s failed (%s)",
            action,
            logger.safe_error_label(reply_error),
            error=reply_error,
        )


_CANCEL_MESSAGE_KEYS = frozenset({"user_id", "thread_id", "target"})


def _valid_cancel(message: object) -> bool:
    """A cancel names a thread and who to tell; it starts nothing."""
    return (
        isinstance(message, Mapping)
        and set(message) <= _CANCEL_MESSAGE_KEYS
        and _valid_address(message)
    )


def _valid_address(message: Mapping[str, object]) -> bool:
    user_id = message.get("user_id")
    thread_id = message.get("thread_id")
    return (
        "target" in message
        and is_json_value(message["target"])
        and isinstance(user_id, str)
        and bool(user_id.strip())
        and isinstance(thread_id, str)
        and re.fullmatch(
            r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}",
            thread_id,
        )
        is not None
    )


def _valid_message(message: object, *, adapter_input: bool) -> bool:
    if not isinstance(message, Mapping) or not _valid_address(message):
        return False
    delivery_key = message.get("delivery_key", "key")
    if not isinstance(delivery_key, str) or not delivery_key.strip():
        return False
    if not isinstance(message.get("background", False), bool):
        return False
    # The channel's ``input`` supplies what a message without content starts.
    if "content" not in message and adapter_input:
        return True
    return _valid_content(message.get("content"))


def _valid_content(content: object) -> bool:
    if not is_json_value(content):
        return False
    if isinstance(content, str):
        return bool(content.strip())
    return (
        isinstance(content, list)
        and bool(content)
        and all(
            isinstance(block, Mapping)
            and isinstance(block_type := block.get("type"), str)
            and block_type.strip()
            for block in content
        )
    )


def _run_input(
    user: ChannelUser | None,
    name: str,
    channel: HttpChannel,
    message: HttpChannelInboundMessage,
    prepare: PrepareInput,
) -> dict[str, object]:
    """What the message's run starts with: its content or the channel's input,
    unless resolve_user holds the run back."""
    content = message.get("content")
    if user is not None and user["type"] != "principal":
        return {
            "prepare": functools.partial(
                _withhold, user, name, channel, message, prepare if content is None else None
            )
        }
    if content is not None:
        return {"input": copy.deepcopy(content)}
    return {"prepare": prepare}


async def _withhold(
    withheld: ResolvedReply | ResolvedIgnore,
    name: str,
    channel: HttpChannel,
    message: HttpChannelInboundMessage,
    prepare: PrepareInput | None,
) -> None:
    """Instead of the run the message would start, post resolve_user's reply, if
    any, once per delivery. The message then finishes as skipped."""
    if prepare is not None and await prepare() is None:
        return
    if withheld["type"] == "reply":
        await post_reply(
            withheld["message"],
            name=name,
            provider=channel.kind,
            target=message["target"],
            transport=channel.messaging(os.environ) if channel.messaging else None,
            delivery_key=message.get("delivery_key"),
        )
    logger.info(
        "channel run withheld by resolve_user %s",
        json.dumps({"threadId": message["thread_id"].lower()}),
    )
    return


def _error(status: int, message: str) -> Response:
    return JSONResponse({"error": message}, status_code=status)
