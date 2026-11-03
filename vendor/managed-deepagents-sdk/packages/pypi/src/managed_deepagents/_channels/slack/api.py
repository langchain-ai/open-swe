"""Authenticated requests to the Slack Web API."""

from __future__ import annotations

import asyncio
import json
import urllib.error
import urllib.parse
from collections.abc import Callable, Mapping
from http import HTTPStatus
from typing import Literal

import httpx

from managed_deepagents._channels.slack_connections import SLACK_BOT_TOKEN_SLUG
from managed_deepagents._channels.types import ChannelError

SLACK_API = "https://slack.com/api"


async def slack_bot_token() -> str:
    """The Slack app's bot token, an agent connection ``mda deploy`` sets up."""
    from managed_deepagents._connections import connections  # noqa: PLC0415

    return await connections.get(SLACK_BOT_TOKEN_SLUG, {"type": "agent"})


FetchFn = Callable[[str, Mapping[str, str], bytes], Mapping[str, object]]


class SlackApiError(ChannelError):
    """A Slack error code, separate from HTTP and network failures."""

    def __init__(self, method: str, provider_error: str) -> None:
        super().__init__("channel_provider_rejected", f"Slack {method} failed: {provider_error}")
        self.provider_error = provider_error


async def slack_api(
    token: str,
    method: str,
    body: Mapping[str, object],
    *,
    api_base: str = SLACK_API,
    fetch: FetchFn | None = None,
    http_method: Literal["GET", "POST"] = "POST",
) -> Mapping[str, object]:
    url = f"{api_base}/{method}"
    headers = {"Authorization": f"Bearer {token}"}
    try:
        if http_method == "GET":
            url = f"{url}?{urllib.parse.urlencode(body)}"
            content = b""
        else:
            headers["Content-Type"] = "application/json; charset=utf-8"
            content = json.dumps(body).encode("utf-8")
        if fetch is not None:
            payload = await asyncio.to_thread(fetch, url, headers, content)
        else:
            async with httpx.AsyncClient(follow_redirects=False, timeout=30) as client:
                response = await client.request(http_method, url, headers=headers, content=content)
                response.raise_for_status()
                payload = response.json()
    except (urllib.error.HTTPError, httpx.HTTPStatusError) as error:
        status = (
            error.code if isinstance(error, urllib.error.HTTPError) else error.response.status_code
        )
        if status == HTTPStatus.TOO_MANY_REQUESTS:
            raise ChannelError(
                "channel_provider_rate_limited", f"Slack {method} rate limited"
            ) from error
        raise ChannelError(
            "channel_provider_rejected", f"Slack {method} HTTP error: {status}"
        ) from error
    except (OSError, httpx.RequestError) as error:
        raise ChannelError(
            "channel_provider_rejected", f"Slack {method} network error: {error}"
        ) from error
    except json.JSONDecodeError as error:
        raise ChannelError(
            "channel_provider_rejected", f"Slack {method} returned non-JSON"
        ) from error
    if not isinstance(payload, Mapping) or payload.get("ok") is not True:
        error = payload.get("error") if isinstance(payload, Mapping) else None
        code = error if isinstance(error, str) else "unknown"
        if code == "ratelimited":
            raise ChannelError("channel_provider_rate_limited", f"Slack {method} rate limited")
        raise SlackApiError(method, code)
    return payload
