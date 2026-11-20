"""Synchronous Segment delivery for the background worker."""

from __future__ import annotations

import base64
import json
import os
import urllib.parse
from collections.abc import Callable, Mapping
from functools import partial
from typing import TYPE_CHECKING, Any, NamedTuple

import httpx

from managed_deepagents._concurrency import BatchProcessor
from managed_deepagents._http_status import HttpStatusError, is_retryable, requested_delay
from managed_deepagents._resilience import RetryPolicy, with_retry
from managed_deepagents._telemetry.lifecycle import on_shutdown

if TYPE_CHECKING:
    from managed_deepagents._concurrency import Deadline
    from managed_deepagents._telemetry.event import TelemetryEvent

SEGMENT_WRITE_KEY_ENV = "MDA_SEGMENT_WRITE_KEY"
SEGMENT_HOST_ENV = "MDA_SEGMENT_HOST"
DEFAULT_SEGMENT_HOST = "https://api.segment.io"
SEGMENT_BATCH_PATH = "/v1/batch"

#: Bound socket operations, as the OTel exporters do by default; a shutdown
#: deadline shortens it.
REQUEST_TIMEOUT_SECONDS = 10.0
MAX_BATCH_EVENTS = 100
#: Segment rejects a batch request over 500KB; leave room for the envelope.
MAX_BATCH_BYTES = 475_000
BATCH_WAIT_SECONDS = 1.0
#: Events waiting to send; later ones are dropped.
MAX_QUEUED_EVENTS = 1_000
#: How long ``idle`` waits by default.
IDLE_TIMEOUT_SECONDS = 1.0

#: 429 and 5xx (including timeouts and network failures) are tried again.
SEND_RETRY = RetryPolicy(
    max_attempts=5,
    base_delay_seconds=1.0,
    max_delay_seconds=30.0,
    retry_on=is_retryable,
    delay_for=requested_delay,
)

#: Public write-only key: it can only submit events to this source, so shipping
#: it in published packages is safe. Rotating it takes a release.
DEFAULT_SEGMENT_WRITE_KEY = "aNDGuWZAOyMHDNqEZSAOjAf5R50sWmKd"

#: Segment fills ``context.ip`` from the request when it is absent; a
#: placeholder keeps the sender's address out, as ``$geoip_disable`` does for
#: PostHog.
PLACEHOLDER_IP = "0.0.0.0"  # noqa: S104 - a payload value, not a bind address

#: ``(url, headers, body, timeout_seconds)``.
PostFn = Callable[[str, Mapping[str, str], bytes, float], None]


class SegmentConfig(NamedTuple):
    write_key: str
    host: str


def _nonempty(value: str | None) -> str | None:
    trimmed = (value or "").strip()
    return trimmed or None


def batch_url(host: str) -> str:
    trimmed = host.rstrip("/")
    parsed = urllib.parse.urlsplit(trimmed)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("Segment host must be a valid URL")
    return f"{trimmed}{SEGMENT_BATCH_PATH}"


def resolve_segment_config(
    env: Mapping[str, str],
    baked_write_key: str = DEFAULT_SEGMENT_WRITE_KEY,
) -> SegmentConfig | None:
    write_key = _nonempty(env.get(SEGMENT_WRITE_KEY_ENV)) or _nonempty(baked_write_key)
    if write_key is None:
        return None
    host = _nonempty(env.get(SEGMENT_HOST_ENV)) or DEFAULT_SEGMENT_HOST
    return SegmentConfig(write_key=write_key, host=host)


def track_message(event: TelemetryEvent) -> dict[str, Any]:
    """One ``track`` call per event, as Segment's warehouse loads it."""
    return {
        "type": "track",
        "event": event["event_type"],
        # The subject is an organization or an install, never a signed-in person.
        "anonymousId": event["distinct_id"],
        # Segment drops a retried event it already ingested by ``messageId``.
        "messageId": event["event_id"],
        "timestamp": event["created_at"],
        "context": {"ip": PLACEHOLDER_IP},
        "properties": dict(event),
    }


def _message_size(event: TelemetryEvent) -> int:
    return len(json.dumps(track_message(event)))


class SegmentTransport:
    """Sends events to Segment as ``track`` calls in the background, in batches.

    ``send`` only queues the event, so delivery never delays a run.
    """

    def __init__(
        self,
        *,
        write_key: str,
        host: str = DEFAULT_SEGMENT_HOST,
        post: PostFn | None = None,
        timeout_seconds: float = REQUEST_TIMEOUT_SECONDS,
        max_wait_seconds: float = BATCH_WAIT_SECONDS,
        retry: RetryPolicy = SEND_RETRY,
    ) -> None:
        if not write_key:
            raise ValueError("Segment write key is required")
        # The write key is the username, with an empty password.
        credentials = base64.b64encode(f"{write_key}:".encode()).decode()
        self._headers = {
            "authorization": f"Basic {credentials}",
            "content-type": "application/json",
        }
        self._batch_url = batch_url(host)
        self._client: httpx.Client | None = None
        if post is None:
            # One client for the transport's life, so batches reuse its connection.
            self._client = httpx.Client(follow_redirects=True)
            post = partial(_default_post, self._client)
        self._post = post
        self._timeout_seconds = timeout_seconds
        self._processor: BatchProcessor[TelemetryEvent] = BatchProcessor(
            handle=with_retry(self._post_batch, retry),
            max_batch_size=MAX_BATCH_EVENTS,
            max_wait_seconds=max_wait_seconds,
            max_queued=MAX_QUEUED_EVENTS,
            max_batch_bytes=MAX_BATCH_BYTES,
            size_of=_message_size,
            name="mda-segment",
        )

    def send(self, event: TelemetryEvent) -> None:
        self._processor.push(event)

    def idle(self, timeout: float = IDLE_TIMEOUT_SECONDS) -> bool:
        return self._processor.idle(timeout)

    def close(self, timeout: float) -> None:
        """Refuse new events and drain what is queued for up to ``timeout``."""
        self._processor.close(timeout)
        if self._client is not None:
            self._client.close()

    def _post_batch(self, batch: list[TelemetryEvent], deadline: Deadline) -> None:
        body = json.dumps({"batch": [track_message(event) for event in batch]}).encode()
        self._post(
            self._batch_url,
            self._headers,
            body,
            min(self._timeout_seconds, deadline.remaining()),
        )


def segment_transport(env: Mapping[str, str] | None = None) -> SegmentTransport | None:
    config = resolve_segment_config(os.environ if env is None else env)
    if config is None:
        return None
    try:
        transport = SegmentTransport(write_key=config.write_key, host=config.host)
    except ValueError:
        # A malformed host must not take the runtime down with it.
        return None
    on_shutdown(transport.close)
    return transport


def _default_post(
    client: httpx.Client,
    url: str,
    headers: Mapping[str, str],
    body: bytes,
    timeout_seconds: float,
) -> None:
    with client.stream(
        "POST", url, headers=headers, content=body, timeout=timeout_seconds
    ) as response:
        if response.status_code >= 300:  # noqa: PLR2004
            raise HttpStatusError.from_response(
                response.status_code, response.headers.get("retry-after")
            )
