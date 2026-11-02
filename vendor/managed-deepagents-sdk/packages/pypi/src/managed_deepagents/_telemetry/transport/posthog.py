"""Synchronous PostHog delivery for the background worker."""

from __future__ import annotations

import json
import os
import urllib.parse
from collections.abc import Callable, Mapping
from functools import partial
from typing import TYPE_CHECKING, NamedTuple

import httpx

from managed_deepagents._concurrency import BatchProcessor
from managed_deepagents._http_status import HttpStatusError, is_retryable, requested_delay
from managed_deepagents._resilience import RetryPolicy, with_retry
from managed_deepagents._telemetry.lifecycle import on_shutdown

if TYPE_CHECKING:
    from managed_deepagents._concurrency import Deadline
    from managed_deepagents._telemetry.event import TelemetryEvent

POSTHOG_API_KEY_ENV = "MDA_POSTHOG_API_KEY"
POSTHOG_HOST_ENV = "MDA_POSTHOG_HOST"
DEFAULT_POSTHOG_HOST = "https://us.i.posthog.com"
POSTHOG_BATCH_PATH = "/batch/"

#: Bound socket operations, as the OTel exporters do by default; a shutdown
#: deadline shortens it.
REQUEST_TIMEOUT_SECONDS = 10.0
MAX_BATCH_EVENTS = 100
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

#: Public write-only key: submission only, and already in every released binary.
DEFAULT_POSTHOG_API_KEY = "phc_C6RmkdQQ3kWHgj6o7kby3aP6dbewjpLzfanw9WcmU65V"

#: ``(url, headers, body, timeout_seconds)``.
PostFn = Callable[[str, Mapping[str, str], bytes, float], None]


class PosthogConfig(NamedTuple):
    api_key: str
    host: str


def _nonempty(value: str | None) -> str | None:
    trimmed = (value or "").strip()
    return trimmed or None


def batch_url(host: str) -> str:
    trimmed = host.rstrip("/")
    parsed = urllib.parse.urlsplit(trimmed)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("PostHog host must be a valid URL")
    return f"{trimmed}{POSTHOG_BATCH_PATH}"


def resolve_posthog_config(
    env: Mapping[str, str],
    baked_api_key: str = DEFAULT_POSTHOG_API_KEY,
) -> PosthogConfig | None:
    api_key = _nonempty(env.get(POSTHOG_API_KEY_ENV)) or _nonempty(baked_api_key)
    if api_key is None:
        return None
    host = _nonempty(env.get(POSTHOG_HOST_ENV)) or DEFAULT_POSTHOG_HOST
    return PosthogConfig(api_key=api_key, host=host)


class PosthogTransport:
    """Sends events to PostHog in the background, in batches.

    ``send`` only queues the event, so delivery never delays a run.
    """

    def __init__(
        self,
        *,
        api_key: str,
        host: str = DEFAULT_POSTHOG_HOST,
        post: PostFn | None = None,
        timeout_seconds: float = REQUEST_TIMEOUT_SECONDS,
        max_wait_seconds: float = BATCH_WAIT_SECONDS,
        retry: RetryPolicy = SEND_RETRY,
    ) -> None:
        if not api_key:
            raise ValueError("PostHog project ingestion key is required")
        self._api_key = api_key
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
            name="mda-posthog",
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
        body = json.dumps(
            {
                "api_key": self._api_key,
                "batch": [
                    {
                        # PostHog drops a retried event it already ingested by ``uuid``.
                        "uuid": event["event_id"],
                        "event": event["event_type"],
                        "distinct_id": event["distinct_id"],
                        "timestamp": event["created_at"],
                        "properties": {
                            # The subject is an organization, and the request
                            # leaves our infrastructure rather than the
                            # customer's, so a person profile and a geoip lookup
                            # would both describe the wrong thing.
                            "$process_person_profile": False,
                            "$geoip_disable": True,
                            **event,
                        },
                    }
                    for event in batch
                ],
            }
        ).encode()
        self._post(
            self._batch_url,
            {"content-type": "application/json"},
            body,
            min(self._timeout_seconds, deadline.remaining()),
        )


def posthog_transport(env: Mapping[str, str] | None = None) -> PosthogTransport | None:
    config = resolve_posthog_config(os.environ if env is None else env)
    if config is None:
        return None
    try:
        transport = PosthogTransport(api_key=config.api_key, host=config.host)
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
