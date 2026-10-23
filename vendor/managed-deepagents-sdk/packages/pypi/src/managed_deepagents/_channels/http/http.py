from __future__ import annotations

import asyncio
import copy
import functools
import inspect
import json
import logging
import os
import re
import uuid
from collections.abc import Awaitable, Callable, Mapping
from typing import TYPE_CHECKING, Literal, TypeVar, cast

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
from managed_deepagents._channels.run_input import NormalizedRunInput, normalize_run_input
from managed_deepagents._channels.runtime import is_json_value, json_copy
from managed_deepagents._channels.types import ChannelError
from managed_deepagents._log import logger

if TYPE_CHECKING:
    from managed_deepagents._channel import ChannelEventsContext
    from managed_deepagents._channels.binding import ChannelBinding
    from managed_deepagents._channels.http import HttpChannel
    from managed_deepagents._channels.http.ingress import SkippedIngressRun
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
    from managed_deepagents._channels.types import ChannelEvent, JsonValue, MessageContent
    from managed_deepagents._ingress_runs import StartedIngressRun

    _Finish = Callable[["HttpFinish", "str | None"], Awaitable[None]]
    """Tell the channel the message is done, with the thread's assistant when known."""

_LOG = logging.getLogger("mda")
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
        _LOG.error("channel verification failed (%s)", type(error).__name__)
        return _error(500, "channel verification failed")
    if not verified:
        return _error(401, "invalid channel signature")

    try:
        result = await _resolve(channel.parse(_adapter_request(request)))
    except Exception as error:  # noqa: BLE001
        _LOG.warning("channel payload rejected (%s)", type(error).__name__)
        return _error(400, "invalid channel payload")
    if (
        not isinstance(result, Mapping)
        or result.get("type") not in ("ignore", "message", "cancel")
        or ("response" in result and not isinstance(result["response"], Response))
        or ("raw_event" in result and not is_json_value(result["raw_event"]))
    ):
        return _error(400, "invalid channel parse result")
    return result


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
    start = functools.partial(
        http_ingress.start_http_ingress_agent,
        user_id=message["user_id"],
        thread_id=thread_id,
        source_thread_key=source_thread_key,
        source_provider=channel.kind,
        binding=copy.deepcopy(binding),
        input=copy.deepcopy(content),
        assistant_id=assistant_id,
        delivery_key=message.get("delivery_key"),
        multitask_strategy=channel.multitask_strategy,
        **(
            {}
            if content is not None
            else {
                "prepare": functools.partial(
                    _prepare, channel, message, result.get("raw_event"), waiter
                )
            }
        ),
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
        message["user_id"],
        source_thread_key,
        post=channel.post,
        on_error=channel.on_error,
        finish=finish,
    )
    wait = functools.partial(
        http_ingress.wait_for_http_ingress_thread,
        user_id=message["user_id"],
        source_thread_key=source_thread_key,
        source_provider=channel.kind,
        thread_id=thread_id,
    )
    start_when_idle = functools.partial(
        _start_when_idle, waiter, start, wait, deliver, finish, thread_id
    )

    async def start_first() -> StartedIngressRun | SkippedIngressRun | None:
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
    _LOG.info("channel run skipped %s", json.dumps({"threadId": thread_id, "reason": skipped}))
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

    Input that may take longer than the provider waits, such as an endpoint's, is
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
            _LOG.error("channel received notice failed (%s)", type(error).__name__)
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
        _LOG.error("channel finish notice failed (%s)", type(error).__name__)


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
        _LOG.error("channel run could not be cancelled (%s)", type(error).__name__)
        return _error(500, "channel run could not be cancelled")
    _LOG.info(
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
                _LOG.error("channel thread could not be read (%s)", type(error).__name__)
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
                _LOG.error("channel finish notice failed (%s)", type(error).__name__)

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
        _LOG.error("channel run could not be started (%s)", type(error).__name__)
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
            _LOG.info("channel run waits %s", json.dumps({"threadId": thread_id}))
            await wait()
            if waiter.cancelled:
                _LOG.info(
                    "channel run dropped: its thread was cancelled while it waited %s",
                    json.dumps({"threadId": thread_id}),
                )
                await finish({"type": "skipped", "reason": "dropped", "interrupts": []}, None)
                return
            outcome = await start()
    except Exception as error:  # noqa: BLE001
        _LOG.error("channel run could not be started (%s)", type(error).__name__)
        await finish({"type": "failed", "reason": "start", "interrupts": []}, None)
        return
    finally:
        _leave_thread_waiters(thread_id, waiter)
    if skipped is None:
        await deliver(cast("StartedIngressRun", outcome))
        return
    _LOG.info("channel run skipped %s", json.dumps({"threadId": thread_id, "reason": skipped}))
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
) -> NormalizedRunInput | None:
    """What the channel's ``input`` starts the run with; failures start nothing."""
    if channel.input is None:
        return None
    try:
        result = await _resolve(
            channel.input(
                _copied_message(message), None if raw_event is None else json_copy(raw_event)
            )
        )
        prepared = normalize_run_input(result)
    except Exception as error:  # noqa: BLE001
        _LOG.error("channel input failed (%s)", type(error).__name__)
        return None
    # A slow input, such as an endpoint's, may outlast a cancel.
    if prepared is not None and waiter.cancelled:
        _LOG.info(
            "channel run dropped: its thread was cancelled while its input was decided %s",
            json.dumps({"threadId": message["thread_id"].lower()}),
        )
        waiter.dropped = True
        return None
    return prepared


async def _deliver_reply(
    binding: ChannelBinding,
    user_id: str,
    source_thread_key: str,
    started: StartedIngressRun,
    post: HttpPost | None,
    on_error: HttpOnError | None,
    finish: _Finish | None = None,
) -> None:
    run_id = started["run_id"]
    _LOG.info(
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
        )
    except Exception as error:  # noqa: BLE001
        _LOG.error("channel run could not be observed (%s)", type(error).__name__)
        outcome = None
    # The run may have answered already, or may still, so no error is reported;
    # how it ended is unknown.
    if outcome is None:
        if finish is not None:
            await finish({"type": "failed", "reason": "observe", "interrupts": []}, None)
        return
    if outcome["status"] in ("error", "timeout"):
        _LOG.error("channel run failed %s", json.dumps({"threadId": started["thread_id"]}))
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
        _LOG.error("channel error %s failed (%s)", action, type(reply_error).__name__)


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


def _error(status: int, message: str) -> Response:
    return JSONResponse({"error": message}, status_code=status)
