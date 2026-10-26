"""Event endpoints: an ``on_event`` value that is a URL or an ``httpx.Request``.

Rather than a function, MDA asks that service of yours what the event starts,
for logic that lives outside the deployment.
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import hashlib
import hmac
import ipaddress
import json
import os
import re
import time
import weakref
from collections.abc import Awaitable, Callable, Mapping, Sequence
from typing import TYPE_CHECKING, Any, Generic, Literal, NotRequired, Protocol, cast
from urllib.parse import urlsplit

import httpx
from pydantic import ConfigDict, TypeAdapter, ValidationError, with_config
from typing_extensions import TypedDict, TypeIs, TypeVar

from managed_deepagents._channels.types import JsonValue

if TYPE_CHECKING:
    from managed_deepagents._channels.run_input import ChannelRunOptions, ChannelRunResult

_TIMEOUT_SECONDS = 60.0
"""How long the service may take to answer one attempt."""
_RETRY_DELAYS_SECONDS = (1.0, 5.0)
"""The pauses before trying again after a network error, HTTP 429 or a 5xx: three
attempts in all, each with the same webhook ID, so a receiver that dedupes on it
acts once."""
_MAX_RESPONSE_BYTES = 10 * 1024 * 1024
"""The largest answer read; run input may carry images."""
_ANSWER_SHAPE = "event endpoint answer must be null or { input, context?, metadata?, webhook? }"
SERVICE_SECRET_ENV = "MDA_SERVICE_SECRET"  # noqa: S105 - an env var's name
"""The deployment's env var holding the secret it shares with your services:
every event endpoint request is signed with it, as Standard Webhooks does.
Several, separated by whitespace or commas, each sign the request, for
rotation."""
_SECRET_PREFIX = "whsec_"  # noqa: S105 - a format prefix, not a secret
"""Standard Webhooks' optional secret prefix."""
_MIN_KEY_BYTES = 24
_MAX_KEY_BYTES = 64
"""The key sizes Standard Webhooks allows."""
_SECRET_SHAPE = (
    f"{SERVICE_SECRET_ENV} must hold Standard Webhooks secrets: padded base64 of 24 to 64 bytes, "
    "optionally prefixed with whsec_"
)

EventEndpoint = str | httpx.Request
"""Where an ``on_event`` key's events go when a service of yours decides what they
start: its URL, or an ``httpx.Request`` that also carries headers, such as a
credential the service checks. See :func:`endpoint_handler`."""

_cached_keys: tuple[str, tuple[bytes, ...]] | None = None


def _signing_keys() -> tuple[bytes, ...]:
    """The signing keys ``MDA_SERVICE_SECRET`` holds; none when it's unset."""
    global _cached_keys  # noqa: PLW0603 - parsed once per value
    secrets = os.environ.get(SERVICE_SECRET_ENV, "").strip()
    if _cached_keys is None or _cached_keys[0] != secrets:
        keys = tuple(signing_key(value) for value in re.split(r"[\s,]+", secrets) if value)
        _cached_keys = (secrets, keys)
    return _cached_keys[1]


def signing_key(secret: str) -> bytes:
    """The key a Standard Webhooks secret stands for; a malformed one raises ``TypeError``."""
    encoded = secret.removeprefix(_SECRET_PREFIX)
    try:
        key = base64.b64decode(encoded, validate=True)
    except (binascii.Error, ValueError):
        raise TypeError(_SECRET_SHAPE) from None
    # Only padded, canonical base64, as the TypeScript runtime takes it.
    if base64.b64encode(key).decode() != encoded or not (
        _MIN_KEY_BYTES <= len(key) <= _MAX_KEY_BYTES
    ):
        raise TypeError(_SECRET_SHAPE)
    return key


_K = TypeVar("_K", bound=str, default=str)
_P = TypeVar("_P", default=JsonValue)


class EndpointEvent(TypedDict, Generic[_K, _P]):
    """The event a channel sends its endpoint, as the provider sent it.

    ``EndpointEvent[Literal["AgentSessionEvent.created"], LinearSessionCreatedPayload]``
    types one event key's payload; ``LinearEndpointRequest`` and
    ``GitHubEndpointRequest`` are the unions of a channel's keys.
    """

    key: _K
    """The channel's event key, such as ``AgentSessionEvent.created``."""
    deliveryId: str
    """The provider's delivery ID."""
    payload: _P
    """The provider's payload, as the channel validated it."""


class _EndpointThreadChannel(TypedDict):
    # A TypedDict, as a nested object must be one; TypeScript writes it inline.
    name: str
    """The channel's name in the project."""


class EndpointThread(TypedDict):
    """The conversation's thread an endpoint request is about."""

    id: str
    """The thread the run starts on, or that waits on an interrupt."""
    channel: _EndpointThreadChannel
    interrupted: NotRequired[Literal[True]]
    """Set when the thread waits on an interrupt: no run starts, whatever the answer."""


_E = TypeVar("_E", default=EndpointEvent)


class EndpointRequest(TypedDict, Generic[_E]):
    """What a channel POSTs its endpoint: the event, and the thread it belongs to.

    TypedDicts, so a receiver validates a request with pydantic, by the channel's
    event keys, such as ``TypeAdapter(LinearEndpointRequest)``: the key picks the
    payload's type. TypeScript's ``LinearEndpointRequest<K>`` also narrows to one
    key; in Python, a union alias can't take the key, so a receiver of one key
    names it as ``EndpointRequest[EndpointEvent[Literal[key], Payload]]``, with the
    payload types the package exports, or narrows on ``request["event"]["key"]``.
    """

    event: _E
    thread: EndpointThread


@with_config(ConfigDict(extra="forbid"))
class EndpointAnswer(TypedDict):
    """The run an endpoint answers with: its input, and the run options a handler
    may set, all JSON. An endpoint answers ``null`` (or HTTP 204) instead to start
    nothing."""

    input: str | dict[str, JsonValue]
    """The run's input, as a handler returns it: text, or state such as
    ``{"messages": ...}``. Never a list, which a handler's ``(input, options)``
    tuple would be confused with."""
    context: NotRequired[dict[str, JsonValue]]
    """Run context, as ``agent.invoke(input, context=...)`` takes it."""
    metadata: NotRequired[dict[str, JsonValue]]
    """Metadata for the run; see ``ChannelRunOptions``."""
    webhook: NotRequired[str]
    """A URL the Agent Server POSTs the finished run to; see ``ChannelRunOptions``."""


_ANSWER: TypeAdapter[EndpointAnswer | None] = TypeAdapter(EndpointAnswer | None)


def signature_headers(
    keys: Sequence[bytes], msg_id: str, timestamp: int, body: bytes
) -> dict[str, str]:
    """The Standard Webhooks headers that sign one request with ``keys``; none without a key.

    Each key signs ``<id>.<timestamp>.<body>``.
    """
    if not keys:
        return {}
    signed = f"{msg_id}.{timestamp}.".encode() + body
    signatures = (
        "v1," + base64.b64encode(hmac.digest(key, signed, hashlib.sha256)).decode() for key in keys
    )
    return {
        "webhook-id": msg_id,
        "webhook-timestamp": str(timestamp),
        "webhook-signature": " ".join(signatures),
    }


class _ChannelEvent(Protocol):
    """The channel's event a handler is called with; it is sent as an ``EndpointEvent``."""

    @property
    def key(self) -> str: ...
    @property
    def delivery_id(self) -> str: ...
    @property
    def payload(self) -> Any: ...


class _ChannelName(Protocol):
    @property
    def name(self) -> str: ...


class _ChannelThread(Protocol):
    """The handler's thread; it is sent as an ``EndpointThread``."""

    @property
    def id(self) -> str: ...
    @property
    def channel(self) -> _ChannelName: ...


_Event = TypeVar("_Event", bound=_ChannelEvent)

EndpointHandler = Callable[[_Event, _ChannelThread], Awaitable["ChannelRunResult | None"]]
"""The handler an endpoint stands for; see :func:`endpoint_handler`."""

EventFields = Callable[[_Event], Mapping[str, object]]
"""The channel's own fields of an event it sends an endpoint beside its key,
delivery ID and payload, such as GitHub's ``repository`` and ``mentions``: one
function per channel, typed by the event it reads."""

_endpoint_handlers: weakref.WeakSet[Callable[..., object]] = weakref.WeakSet()
"""The handlers endpoints stand for, so a channel knows to answer first."""


def is_event_endpoint(value: object) -> TypeIs[EventEndpoint]:
    """Whether an ``on_event`` value is an endpoint rather than a handler function."""
    return isinstance(value, str | httpx.Request)


def is_endpoint_handler(handler: object) -> bool:
    """Whether a handler is one an endpoint stands for."""
    return callable(handler) and handler in _endpoint_handlers


def endpoint_handler(
    endpoint: EventEndpoint, label: str, fields: EventFields[_Event] | None = None
) -> EndpointHandler[_Event]:
    """The handler an endpoint stands for.

    When the event's thread takes a turn, MDA POSTs ``{"event": {"key",
    "deliveryId", "payload"}, "thread": {"id", "channel": {"name"},
    "interrupted"?}}`` as JSON to the endpoint's URL, with the channel's own
    event ``fields`` beside the payload and an ``httpx.Request``'s
    headers, signed when the deployment has ``MDA_SERVICE_SECRET``, and
    starts the run the service answers with: ``{"input",
    "context"?, "metadata"?, "webhook"?}``. ``null`` or HTTP 204 starts nothing. A
    network error before the answer, HTTP 429 or a 5xx is tried again, up to three
    attempts. Any other answer, one cut off, a redirect, or no answer within 60
    seconds fails the handler, as a handler that raises does. The URL is https,
    or http to a loopback host for local development, without credentials in it.

    The webhook ID is ``msg_<deliveryId>`` for the call that starts the run, and
    ``msg_<deliveryId>_interrupted`` for the one that asks whether to tell the
    conversation its thread waits on a decision.
    """
    template = endpoint if isinstance(endpoint, httpx.Request) else None
    target = _endpoint_url(str(template.url) if template is not None else str(endpoint), label)
    # A malformed secret raises when the channel is declared, not on each event.
    _signing_keys()
    sent = {
        **dict(template.headers if template else {}),
        "content-type": "application/json",
        "accept": "application/json",
    }
    # The template's own framing doesn't describe the JSON MDA sends.
    for name in ("content-length", "host", "transfer-encoding"):
        sent.pop(name, None)

    async def handler(event: _Event, thread: _ChannelThread) -> ChannelRunResult | None:
        interrupted = bool(getattr(thread, "interrupted", False))
        # The channel's own fields can't stand in for the ones every event has.
        sent_event: dict[str, object] = {
            **(fields(event) if fields is not None else {}),
            "key": event.key,
            "deliveryId": event.delivery_id,
            "payload": event.payload,
        }
        sent_thread: EndpointThread = {"id": thread.id, "channel": {"name": thread.channel.name}}
        if interrupted:
            sent_thread["interrupted"] = True
        # The exact bytes sent are the ones signed.
        body = json.dumps({"event": sent_event, "thread": sent_thread}).encode()
        msg_id = f"msg_{event.delivery_id}{'_interrupted' if interrupted else ''}"
        for delay in _RETRY_DELAYS_SECONDS:
            try:
                return await _post(target, sent, msg_id, body)
            except (_RetryableError, httpx.TransportError):
                await asyncio.sleep(delay)
        return await _post(target, sent, msg_id, body)

    _endpoint_handlers.add(handler)
    return handler


class _RetryableError(RuntimeError):
    """An answer worth trying again: HTTP 429 or a 5xx."""


async def _post(
    target: str, sent: dict[str, str], msg_id: str, body: bytes
) -> ChannelRunResult | None:
    """One attempt, signed afresh: Standard Webhooks timestamps each."""
    headers = {**sent, **signature_headers(_signing_keys(), msg_id, int(time.time()), body)}
    # One deadline for the whole answer, as the TypeScript runtime's: httpx's
    # own timeouts limit each read, so a trickled answer would outlast them.
    try:
        async with (
            asyncio.timeout(_TIMEOUT_SECONDS),
            httpx.AsyncClient(timeout=_TIMEOUT_SECONDS, follow_redirects=False) as client,
            client.stream("POST", target, headers=headers, content=body) as response,
        ):
            if response.status_code == 204:  # noqa: PLR2004 - No Content
                return None
            if not response.is_success:
                status = response.status_code
                retried = status == 429 or status >= 500  # noqa: PLR2004 - Too Many Requests, 5xx
                error = _RetryableError if retried else RuntimeError
                raise error(f"event endpoint answered HTTP {status}")
            try:
                answer = await _bounded_json(response)
            except httpx.TimeoutException:
                raise
            except httpx.TransportError:
                # Not tried again: the service answered, so it may have acted on it.
                raise RuntimeError("event endpoint answer was cut off") from None
    except (TimeoutError, httpx.TimeoutException):
        # Not tried again: the service may still be acting on it.
        raise RuntimeError(
            f"event endpoint did not answer within {_TIMEOUT_SECONDS:g} seconds"
        ) from None
    return _run_result(answer)


def _endpoint_url(url: str, label: str) -> str:
    """The endpoint's URL.

    Plain http only reaches a loopback host: the answer isn't signed, so anyone
    on the path could write the run's input otherwise.
    """
    parts = urlsplit(url)
    if (
        not parts.hostname
        or not (
            parts.scheme == "https" or (parts.scheme == "http" and _is_loopback(parts.hostname))
        )
        or parts.username
        or parts.password
    ):
        raise TypeError(
            f"{label} must be an absolute https URL, or http to localhost, "
            "without credentials in it"
        )
    return url


def _is_loopback(hostname: str) -> bool:
    """Whether a URL's hostname is this machine."""
    if hostname == "localhost" or hostname.endswith(".localhost"):
        return True
    try:
        return ipaddress.ip_address(hostname).is_loopback
    except ValueError:
        return False


async def _bounded_json(response: httpx.Response) -> JsonValue:
    """The answer as JSON, read only up to ``_MAX_RESPONSE_BYTES``."""
    declared = response.headers.get("content-length", "")
    if declared.isdigit() and int(declared) > _MAX_RESPONSE_BYTES:
        raise RuntimeError("event endpoint answer is too large")
    body = bytearray()
    async for chunk in response.aiter_bytes():
        body.extend(chunk)
        if len(body) > _MAX_RESPONSE_BYTES:
            raise RuntimeError("event endpoint answer is too large")
    try:
        return cast("JsonValue", json.loads(body))
    except ValueError:
        raise RuntimeError("event endpoint answer is not JSON") from None


def _run_result(body: JsonValue) -> ChannelRunResult | None:
    """The run the answer names; the channel checks the input and options as a handler's."""
    try:
        answer = _ANSWER.validate_python(body)
    except ValidationError:
        raise RuntimeError(_ANSWER_SHAPE) from None
    if answer is None:
        return None
    options: ChannelRunOptions = {}
    if "context" in answer:
        options["context"] = answer["context"]
    if "metadata" in answer:
        options["metadata"] = answer["metadata"]
    if "webhook" in answer:
        options["webhook"] = answer["webhook"]
    return (answer["input"], options)
