"""Shared async Slack SDK lifecycle and application error handling."""

import hashlib
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import aiohttp
from fastapi import HTTPException
from slack_sdk.errors import SlackApiError
from slack_sdk.web.async_client import AsyncWebClient

from agent.config import ENV
from agent.utils import ttl_cache

SLACK_API_BASE_URL = "https://slack.com/api/"
SLACK_REQUEST_ERRORS = (SlackApiError, aiohttp.ClientError, TimeoutError, ValueError)
# SDK debug logs include message bodies and upload URLs. Application callers log
# only the Slack error code, never the SDK exception's full response.
_SDK_LOGGER = logging.Logger("agent.slack.sdk", level=logging.WARNING)


class _SlackResponse(aiohttp.ClientResponse):
    async def json(self, **kwargs: Any) -> dict[str, Any]:
        data = await super().json(**kwargs)
        # The SDK assumes a JSON object and otherwise raises AttributeError.
        if not isinstance(data, dict):
            raise SlackApiError("Slack returned an invalid response.", self)
        return data

    async def text(self, encoding: str | None = None, errors: str = "strict") -> str:
        # The SDK routes text/plain responses here; its Web API calls require JSON.
        raise SlackApiError("Slack returned a non-JSON response.", self)


@asynccontextmanager
async def slack_client(
    *, token: str | None = None, timeout: int = 30
) -> AsyncIterator[AsyncWebClient]:
    token = ENV.SLACK_BOT_TOKEN.get() if token is None else token
    if not token:
        raise HTTPException(400, "Slack is not configured.")
    async with aiohttp.ClientSession(
        timeout=aiohttp.ClientTimeout(total=timeout, sock_connect=min(10, timeout)),
        response_class=_SlackResponse,
    ) as session:
        yield AsyncWebClient(
            token=token,
            base_url=SLACK_API_BASE_URL,
            timeout=timeout,
            session=session,
            logger=_SDK_LOGGER,
            # Callers own backoff; retrying writes after a disconnect can duplicate them.
            retry_handlers=[],
        )


def slack_retry_after(exc: Exception) -> str | None:
    if isinstance(exc, SlackApiError):
        return exc.response.headers.get("Retry-After")
    return None


def slack_error(exc: Exception) -> str:
    """Normalize SDK errors to the error codes consumed by our tools."""
    if isinstance(exc, ValueError):
        return "invalid_slack_response"
    if not isinstance(exc, SlackApiError):
        return f"http_error: {type(exc).__name__}"
    response = exc.response
    # Malformed JSON errors carry aiohttp's response instead of AsyncSlackResponse.
    status = getattr(response, "status_code", getattr(response, "status", None))
    data = getattr(response, "data", None)
    code = str(data.get("error") or "slack_api_error") if isinstance(data, dict) else ""
    if status == 429 or code == "ratelimited":
        delay = slack_retry_after(exc)
        return f"rate_limited: {delay}" if delay else "rate_limited"
    if status != 200:
        return "http_error: HTTPStatusError"
    return code or "invalid_slack_response"


def slack_cache_key(client: AsyncWebClient) -> str:
    return "slack:" + hashlib.sha256((client.token or "").encode()).hexdigest()


@asynccontextmanager
async def slack_http_errors() -> AsyncIterator[None]:
    """Translate SDK failures for dashboard routes, without exposing response bodies."""
    try:
        yield
    except SLACK_REQUEST_ERRORS as exc:
        error = slack_error(exc)
        if error.startswith("rate_limited"):
            raise HTTPException(
                429,
                "Slack is rate limiting requests. Try again shortly.",
                headers={"Retry-After": slack_retry_after(exc) or "60"},
            ) from exc
        if error == "missing_scope" and isinstance(exc, SlackApiError):
            needed = exc.response.get("needed") or "the required permissions"
            raise HTTPException(400, f"Reinstall the Slack app with {needed}.") from exc
        if error.startswith("http_error") or error == "invalid_slack_response":
            raise HTTPException(502, "Could not reach Slack. Try again.") from exc
        raise HTTPException(400, "Slack rejected the request. Check the app permissions.") from exc


async def slack_identity(client: AsyncWebClient) -> dict[str, Any]:
    async def load() -> dict[str, Any]:
        response = await client.auth_test()
        data = response.data
        if (
            not isinstance(data, dict)
            or not isinstance(data.get("team_id"), str)
            or not data["team_id"]
        ):
            raise HTTPException(502, "Slack did not return a workspace ID.")
        return data

    return await ttl_cache.cached(f"{slack_cache_key(client)}:identity", 300, load)


async def slack_bot_members(client: AsyncWebClient) -> AsyncIterator[dict[str, Any]]:
    cursor = ""
    seen_cursors: set[str] = set()
    while True:
        page = await client.users_list(limit=200, cursor=cursor)
        items = page.get("members")
        if not isinstance(items, list):
            raise HTTPException(502, "Slack returned an invalid collection.")
        for item in items:
            if isinstance(item, dict):
                yield item
        metadata = page.get("response_metadata") or {}
        cursor = metadata.get("next_cursor", "") if isinstance(metadata, dict) else ""
        if not cursor:
            return
        if not isinstance(cursor, str) or cursor in seen_cursors:
            raise HTTPException(502, "Slack returned an invalid pagination cursor.")
        seen_cursors.add(cursor)
