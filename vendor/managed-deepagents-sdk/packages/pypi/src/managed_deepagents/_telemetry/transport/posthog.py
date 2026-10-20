"""Synchronous PostHog delivery for the background worker."""

from __future__ import annotations

import json
import os
import urllib.parse
from collections.abc import Callable, Mapping
from functools import partial
from typing import TYPE_CHECKING, NamedTuple

import httpx

if TYPE_CHECKING:
    from managed_deepagents._telemetry.event import TelemetryEvent

POSTHOG_API_KEY_ENV = "MDA_POSTHOG_API_KEY"
POSTHOG_HOST_ENV = "MDA_POSTHOG_HOST"
DEFAULT_POSTHOG_HOST = "https://us.i.posthog.com"
POSTHOG_CAPTURE_PATH = "/i/v0/e/"

#: Bound socket operations; the daemon worker's shutdown wait also bounds exit.
REQUEST_TIMEOUT_SECONDS = 1.0

#: Public write-only key: submission only, and already in every released binary.
DEFAULT_POSTHOG_API_KEY = "phc_C6RmkdQQ3kWHgj6o7kby3aP6dbewjpLzfanw9WcmU65V"

PostFn = Callable[[str, Mapping[str, str], bytes], None]


class PosthogConfig(NamedTuple):
    api_key: str
    host: str


def _nonempty(value: str | None) -> str | None:
    trimmed = (value or "").strip()
    return trimmed or None


def capture_url(host: str) -> str:
    trimmed = host.rstrip("/")
    parsed = urllib.parse.urlsplit(trimmed)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("PostHog host must be a valid URL")
    return f"{trimmed}{POSTHOG_CAPTURE_PATH}"


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
    def __init__(
        self,
        *,
        api_key: str,
        host: str = DEFAULT_POSTHOG_HOST,
        post: PostFn | None = None,
        timeout_seconds: float = REQUEST_TIMEOUT_SECONDS,
    ) -> None:
        if not api_key:
            raise ValueError("PostHog project ingestion key is required")
        self._api_key = api_key
        self._capture_url = capture_url(host)
        self._post = post or partial(_default_post, timeout_seconds=timeout_seconds)

    def send(self, event: TelemetryEvent) -> None:
        body = json.dumps(
            {
                "api_key": self._api_key,
                "event": event["event_type"],
                "distinct_id": event["distinct_id"],
                "timestamp": event["created_at"],
                "properties": {
                    # The subject is an organization, and the request leaves our
                    # infrastructure rather than the customer's, so a person
                    # profile and a geoip lookup would both describe the wrong
                    # thing.
                    "$process_person_profile": False,
                    "$geoip_disable": True,
                    **event,
                },
            }
        ).encode()
        self._post(self._capture_url, {"content-type": "application/json"}, body)


def posthog_transport(env: Mapping[str, str] | None = None) -> PosthogTransport | None:
    config = resolve_posthog_config(os.environ if env is None else env)
    if config is None:
        return None
    try:
        return PosthogTransport(api_key=config.api_key, host=config.host)
    except ValueError:
        # A malformed host must not take the runtime down with it.
        return None


def _default_post(
    url: str, headers: Mapping[str, str], body: bytes, *, timeout_seconds: float
) -> None:
    with (
        httpx.Client(timeout=timeout_seconds, follow_redirects=True) as client,
        client.stream("POST", url, headers=headers, content=body) as response,
    ):
        if response.status_code >= 300:  # noqa: PLR2004
            raise RuntimeError(f"PostHog rejected analytics event: {response.status_code}")
