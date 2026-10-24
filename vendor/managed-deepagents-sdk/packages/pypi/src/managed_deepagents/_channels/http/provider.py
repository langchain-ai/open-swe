"""What webhook channels built on ``http_channel`` share.

GitHub and Linear read their secrets from Agent Auth, check signatures, run an
event's handler, and hand their typed events to the user's hooks the same way.
"""

from __future__ import annotations

import asyncio
import hmac
import inspect
import json
import os
import time
from collections.abc import Awaitable, Callable, Mapping
from collections.abc import Set as AbstractSet
from dataclasses import asdict, dataclass
from typing import TYPE_CHECKING, Generic, Literal, TypeVar

from managed_deepagents._log import logger

if TYPE_CHECKING:
    from _typeshed import DataclassInstance

    from managed_deepagents._channels.http import HttpChannel
    from managed_deepagents._channels.http.endpoint import (
        EventEndpoint,
        EventFields,
        _ChannelEvent,
        _ChannelThread,
    )
    from managed_deepagents._channels.http.types import (
        HttpDelivery,
        HttpFinish,
        HttpFinishedDelivery,
        HttpOnFinish,
        HttpOnReceived,
        JsonValue,
    )
    from managed_deepagents._channels.run_input import ChannelRunResult

_T = TypeVar("_T")
_Key = TypeVar("_Key", bound=str)
_E = TypeVar("_E")
_Event = TypeVar("_Event", bound="_ChannelEvent")
_Thread = TypeVar("_Thread", bound="_ChannelThread")


def verify_hmac_sha256(secret: str, raw_body: bytes, signature: str) -> bool:
    """Whether ``signature``, a hex HMAC-SHA256 the caller checked the format
    of, signs ``raw_body`` with ``secret``. Compared in constant time."""
    expected = hmac.digest(secret.encode(), raw_body, "sha256")
    return hmac.compare_digest(expected, bytes.fromhex(signature))


class CachedRead(Generic[_T]):
    """A value read at most every ``ttl(value)`` seconds.

    Concurrent callers share the read in flight; a failed read is not kept, so
    the next call reads again.
    """

    __slots__ = ("_entry", "_read", "_ttl")

    def __init__(self, read: Callable[[], Awaitable[_T]], ttl: Callable[[_T], float]) -> None:
        self._read = read
        self._ttl = ttl
        self._entry: _Entry[_T] | None = None

    async def get(self) -> _T:
        entry = self._entry
        if (
            entry is None
            or entry.expires_at <= time.monotonic()
            # A read still in flight can only be awaited on its own loop.
            or (not entry.value.done() and entry.value.get_loop() is not asyncio.get_running_loop())
        ):
            entry = _Entry(asyncio.ensure_future(self._read()), float("inf"))
            self._entry = entry
        try:
            # Shielded so one cancelled caller does not cancel the shared read.
            value = await asyncio.shield(entry.value)
        except Exception:
            if self._entry is entry:
                self._entry = None
            raise
        if entry.expires_at == float("inf"):
            entry.expires_at = time.monotonic() + self._ttl(value)
        return value

    def reset(self) -> None:
        """Read again on the next call."""
        self._entry = None


@dataclass
class _Entry(Generic[_T]):
    value: asyncio.Future[_T]
    expires_at: float


def channel_name(channel: HttpChannel) -> str:
    """The channel's name in the project, or its provider before it has one."""
    return channel.name or channel.kind


def check_event_keys(
    provider: str, handlers: Mapping[str, object], supported: AbstractSet[str]
) -> None:
    """Check a channel's ``on_event`` keys.

    The types limit them, but an untyped caller gets no error, and a key
    nothing dispatches never runs.
    """
    unknown = sorted(set(handlers) - supported)
    if unknown:
        raise ValueError(f"unsupported {provider} event key {', '.join(unknown)}")


def handled_event_keys(handlers: Mapping[_Key, object]) -> list[str]:
    """The events deploy subscribes the provider's webhook to: the handled ones."""
    return sorted(str(key) for key, handler in handlers.items() if handler is not None)


EventCallback = Callable[
    [_Event, _Thread], "ChannelRunResult | Awaitable[ChannelRunResult | None] | None"
]
"""A channel's event handler, as the channel calls it with the event's thread."""


def resolve_endpoints(
    provider: str,
    handlers: Mapping[str, EventCallback[_Event, _Thread] | EventEndpoint],
    fields: EventFields[_Event] | None = None,
) -> dict[str, EventCallback[_Event, _Thread]]:
    """A channel's ``on_event`` with each endpoint replaced by the handler it stands for.

    An endpoint is a URL or an ``httpx.Request``; the channel then calls handlers
    alone. A bad endpoint, or a value that is neither a handler nor an endpoint,
    raises when the channel is declared. The endpoint's
    handler is typed as the channel's own: ``_Event`` and ``_Thread`` carry what it sends,
    and ``fields`` are the channel's own fields of an event it sends beside key,
    delivery ID and payload.
    """
    from managed_deepagents._channels.http.endpoint import (  # noqa: PLC0415
        endpoint_handler,
        is_event_endpoint,
    )

    resolved: dict[str, EventCallback[_Event, _Thread]] = {}
    for key, value in handlers.items():
        label = f'{provider} on_event["{key}"]'
        if is_event_endpoint(value):
            resolved[key] = endpoint_handler(value, label, fields)
        elif value is None or callable(value):
            # A ``None`` handler handles nothing, as an absent one.
            resolved[key] = value
        else:
            raise TypeError(f"{label} must be callable, a URL or an httpx.Request")
    return resolved


EventFinished = Callable[[_E, _T, "HttpFinish"], "Awaitable[None] | None"]
"""A provider channel's ``on_finish``: a delivery of one of its events is done,
with the event, its thread, and how it finished, to switch on by ``type``: the
run it started, why it started none, or that MDA lost it; see ``HttpFinish``."""


def finish_events(
    notice: HttpOnFinish,
    on_finish: EventFinished[_E, _T] | None,
    parse: Callable[[JsonValue | None], _E | Awaitable[_E | None] | None],
    thread: Callable[[HttpFinishedDelivery], _T],
) -> HttpOnFinish:
    """The HTTP channel's ``on_finish`` for a provider channel.

    The channel's own notice comes first, such as a link to a pending decision,
    then the user's hook, for the deliveries whose event the channel parsed. A
    failing notice is logged and doesn't keep the user's hook from running.
    """

    async def finished(target: JsonValue, delivery: HttpFinishedDelivery) -> None:
        try:
            await notice(target, delivery)
        except Exception as error:  # noqa: BLE001
            logger.error("channel finish notice failed (%s)", type(error).__name__)
        if on_finish is None:
            return
        parsed = parse(delivery.get("raw_event"))
        event = await parsed if inspect.isawaitable(parsed) else parsed
        if event is None:
            return
        result = on_finish(event, thread(delivery), _finish_of(delivery))
        if inspect.isawaitable(result):
            await result

    return finished


def _finish_of(delivery: HttpFinishedDelivery) -> HttpFinish:
    """How a delivery finished, without the delivery's own fields."""
    interrupts = delivery["interrupts"]
    if delivery["type"] == "run":
        return {"type": "run", "run": delivery["run"], "interrupts": interrupts}
    if delivery["type"] == "skipped":
        return {"type": "skipped", "reason": delivery["reason"], "interrupts": interrupts}
    return {"type": "failed", "reason": delivery["reason"], "interrupts": interrupts}


EventReceived = Callable[[_E, _T], "Awaitable[None] | None"]
"""A provider channel's ``on_received``: a delivery of one of its events reached
its thread, before MDA reads the thread; see ``http_channel``'s ``on_received``."""


def receive_events(
    on_received: EventReceived[_E, _T] | None,
    parse: Callable[[JsonValue | None], _E | None],
    thread: Callable[[str], _T],
) -> HttpOnReceived | None:
    """The HTTP channel's ``on_received`` for a provider channel's user hook.

    Only for the deliveries whose event the channel parsed.
    """
    if on_received is None:
        return None
    hook = on_received

    async def received(_target: JsonValue, delivery: HttpDelivery) -> None:
        event = parse(delivery.get("raw_event"))
        if event is None:
            return
        result = hook(event, thread(delivery["thread_id"]))
        if inspect.isawaitable(result):
            await result

    return received


def defined_fields(fields: Mapping[str, JsonValue | None]) -> dict[str, JsonValue]:
    """App options for deploy, without unset fields."""
    return {key: value for key, value in fields.items() if value is not None}


def app_connection_slug(provider: str, deployment_id: str, suffix: str | None = None) -> str:
    """The connection ``mda deploy`` stores for the provider's app.

    Or one of the values stored beside it, such as ``webhook-secret``, when
    ``suffix`` is given.
    """
    slug = f"mda/{provider}-app/{deployment_id}"
    return f"{slug}/{suffix}" if suffix else slug


def event_snapshot(event: DataclassInstance, provider: str) -> JsonValue:
    """A copy of a parsed event, kept as the delivery's raw event.

    A handler that changes its argument can't change what MDA keeps.
    """
    from managed_deepagents._channels.runtime import is_json_value  # noqa: PLC0415

    snapshot = asdict(event)
    if not is_json_value(snapshot):
        raise TypeError(f"invalid {provider} event")
    return snapshot


_SECRET_TTL_SECONDS = 300.0
"""How long a value read from Agent Auth is reused before it is read again."""
_MISSING_SECRET_TTL_SECONDS = 60.0
"""How long a missing value is remembered, so unsigned traffic can't hammer Agent Auth."""
_deployment_secrets: dict[str, CachedRead[str | None]] = {}


async def read_deployment_secret(env: str, slug: Callable[[str], str], label: str) -> str | None:
    """A value ``mda deploy`` stored for the deployment, such as a webhook secret.

    The ``env`` override wins; otherwise the agent secret Agent Auth keeps at
    ``slug(deployment_id)``. Only a missing connection reads as ``None``, and is
    logged as ``label``; credential store failures propagate.
    """
    from managed_deepagents._connections import DEPLOYMENT_ID_ENV  # noqa: PLC0415

    override = os.environ.get(env, "").strip()
    if override:
        return override
    deployment_id = os.environ.get(DEPLOYMENT_ID_ENV, "").strip()
    if not deployment_id:
        return None
    key = slug(deployment_id)
    cached = _deployment_secrets.get(key)
    if cached is None:

        async def read() -> str | None:
            from managed_deepagents._connections import (  # noqa: PLC0415
                ConnectionNotFoundError,
                connections,
            )

            try:
                return await connections.resolve_deployment_secret(key)
            except ConnectionNotFoundError:
                logger.error("%s is not stored in Agent Auth", label)
                return None

        cached = CachedRead(
            read,
            lambda value: _MISSING_SECRET_TTL_SECONDS if value is None else _SECRET_TTL_SECONDS,
        )
        _deployment_secrets[key] = cached
    return await cached.get()


def reset_deployment_secrets() -> None:
    """Forget values read from Agent Auth; for tests."""
    _deployment_secrets.clear()


@dataclass(frozen=True)
class EventHandlerOutcome:
    """What an event's handler did: returned run input, skipped, or threw."""

    type: Literal["input", "skipped", "failed"]
    input: ChannelRunResult | None = None


async def run_event_handler(
    provider: str,
    key: str,
    delivery_id: str,
    call: Callable[[], ChannelRunResult | Awaitable[ChannelRunResult | None] | None],
) -> EventHandlerOutcome:
    """Run an event's handler. A throw is logged, not raised."""
    receipt = json.dumps(
        {"event": logger.log_identifier(key), "deliveryId": logger.log_identifier(delivery_id)}
    )
    try:
        result = call()
        content = await result if inspect.isawaitable(result) else result
    except Exception as error:  # noqa: BLE001
        logger.error(
            "%s callback failed %s (%s)",
            provider.lower(),
            receipt,
            logger.safe_error_label(error),
        )
        return EventHandlerOutcome("failed")
    if content is None:
        return EventHandlerOutcome("skipped")
    logger.info("%s handler returned run input %s", provider, receipt)
    return EventHandlerOutcome("input", content)
