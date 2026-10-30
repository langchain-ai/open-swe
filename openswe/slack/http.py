"""Shared async Slack SDK lifecycle and application error handling."""

import asyncio
import hashlib
import logging
import math
import re
import weakref
from collections import OrderedDict
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import timedelta
from time import monotonic
from typing import Any, Self

import aiohttp
from fastapi import HTTPException
from slack_sdk.errors import SlackApiError
from slack_sdk.web.async_client import AsyncWebClient
from slack_sdk.web.async_slack_response import AsyncSlackResponse

from openswe import event_claims
from openswe.config import ENV, EnvVar
from openswe.database import postgres
from openswe.utils import ttl_cache
from openswe.utils.http_pool import pooled_connector

SLACK_API_BASE_URL = "https://slack.com/api/"
SLACK_REQUEST_ERRORS = (SlackApiError, aiohttp.ClientError, TimeoutError, ValueError)
# SDK debug logs include message bodies and upload URLs. Application callers log
# only the Slack error code, never the SDK exception's full response.
_SDK_LOGGER = logging.Logger("openswe.slack.sdk", level=logging.WARNING)


class SlackConfigurationError(RuntimeError):
    """Required deployment-level Slack configuration is missing."""


def required_slack_value(variable: EnvVar) -> str:
    """Read required Slack configuration without treating a broken deployment as an API failure."""
    try:
        return variable.require()
    except KeyError as exc:
        raise SlackConfigurationError(
            f"{variable.name} is required: every Open SWE deployment must have Slack configured"
        ) from exc


def validate_slack_startup_config() -> None:
    """Refuse to start without the Slack app every deployment needs."""
    for variable in (
        ENV.SLACK_BOT_TOKEN,
        ENV.SLACK_SIGNING_SECRET,
        ENV.SLACK_APP_ID,
        ENV.SLACK_BOT_USER_ID,
        ENV.SLACK_BOT_USERNAME,
    ):
        required_slack_value(variable)


class SlackRequestError(RuntimeError):
    def __init__(self, code: str, *, invited: list[str] | None = None) -> None:
        super().__init__(code)
        self.code = code
        self.invited = invited or []


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


@dataclass(frozen=True)
class _LookupFailure:
    code: str
    status: int | None
    retry_after: float | None = None

    @classmethod
    def from_exception(cls, exc: Exception) -> Self:
        response = getattr(exc, "response", None)
        status = getattr(response, "status_code", getattr(response, "status", None))
        data = getattr(response, "data", None)
        raw_code = data.get("error") if isinstance(data, dict) else None
        code = (
            raw_code
            if isinstance(raw_code, str) and re.fullmatch(r"[a-z_]{1,80}", raw_code)
            else "invalid_slack_response"
            if isinstance(exc, (SlackApiError, ValueError))
            else "transport_error"
        )
        try:
            delay = float(slack_retry_after(exc) or "60")
        except ValueError:
            delay = 60.0
        if not math.isfinite(delay) or not 0 <= delay <= 31_536_000:
            delay = 60.0
        limited = status == 429 or code == "ratelimited"
        return cls(
            code,
            429 if limited else status if isinstance(status, int) else None,
            max(1, delay) if limited else None,
        )

    def exception(self, client: AsyncWebClient) -> SlackApiError:
        return SlackApiError(
            "Slack channel lookup is temporarily unavailable.",
            AsyncSlackResponse(
                client=client,
                http_verb="GET",
                api_url=client.base_url + "conversations.info",
                req_args={},
                data={"ok": False, "error": self.code},
                headers={"Retry-After": str(math.ceil(self.retry_after))}
                if self.retry_after is not None
                else {},
                status_code=self.status or 503,
            ),
        )


@dataclass
class _ChannelInfoRequests:
    scope: str
    pending: dict[str, asyncio.Task[AsyncSlackResponse]] = field(default_factory=dict)
    failures: OrderedDict[str, tuple[float, _LookupFailure]] = field(default_factory=OrderedDict)
    limited_until: float = 0

    async def fetch(self, client: AsyncWebClient, params: dict[str, object]) -> AsyncSlackResponse:
        key = repr(sorted(params.items()))
        task = self.pending.get(key)
        if task is None:
            task = asyncio.create_task(self._fetch(client, key, params))
            self.pending[key] = task

            def finished(done: asyncio.Task[AsyncSlackResponse]) -> None:
                self.pending.pop(key, None)
                if not done.cancelled():
                    done.exception()

            task.add_done_callback(finished)
        return await asyncio.shield(task)

    async def _fetch(
        self, client: AsyncWebClient, key: str, params: dict[str, object]
    ) -> AsyncSlackResponse:
        delay = max(0, self.limited_until - monotonic())
        if postgres.configured():
            delay = max(delay, await event_claims.remaining("slack-api-cooldown", self.scope))
        if delay:
            raise _LookupFailure("ratelimited", 429, delay).exception(client)
        if (failed := self.failures.get(key)) is not None:
            if failed[0] > monotonic():
                raise failed[1].exception(client)
            self.failures.pop(key)
        try:
            async with aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=client.timeout, sock_connect=10),
                response_class=_SlackResponse,
                connector=pooled_connector(),
                connector_owner=False,
            ) as session:
                request_client = AsyncWebClient(
                    token=client.token,
                    base_url=client.base_url,
                    timeout=client.timeout,
                    ssl=client.ssl,
                    session=session,
                    headers=client.headers,
                    proxy=client.proxy,
                    logger=_SDK_LOGGER,
                    retry_handlers=[],
                )
                return await request_client.api_call(
                    "conversations.info", http_verb="GET", params=params
                )
        except SLACK_REQUEST_ERRORS as exc:
            failure = _LookupFailure.from_exception(exc)
            self.failures[key] = (monotonic() + 2, failure)
            while len(self.failures) > 256:
                self.failures.popitem(last=False)
            logging.getLogger(__name__).warning(
                "Slack channel lookup failed",
                extra={
                    "slack_method": "conversations.info",
                    "slack_error": failure.code,
                    "slack_status": failure.status,
                    "slack_retry_after": failure.retry_after,
                },
            )
            if failure.retry_after is not None:
                self.limited_until = max(self.limited_until, monotonic() + failure.retry_after)
                if postgres.configured():
                    await event_claims.defer(
                        "slack-api-cooldown",
                        self.scope,
                        ttl=timedelta(seconds=failure.retry_after),
                    )
            raise failure.exception(client) from None


_CHANNEL_INFO: weakref.WeakKeyDictionary[
    asyncio.AbstractEventLoop, dict[str, _ChannelInfoRequests]
] = weakref.WeakKeyDictionary()


class SlackClient(AsyncWebClient):
    async def conversations_info(
        self,
        *,
        channel: str,
        include_locale: bool | None = None,
        include_num_members: bool | None = None,
        **kwargs: object,
    ) -> AsyncSlackResponse:
        scope = slack_cache_key(self) + ":conversations.info"
        requests = _CHANNEL_INFO.setdefault(asyncio.get_running_loop(), {})
        state = requests.setdefault(scope, _ChannelInfoRequests(scope))
        return await state.fetch(
            self,
            {
                **kwargs,
                "channel": channel,
                "include_locale": include_locale,
                "include_num_members": include_num_members,
            },
        )

    @classmethod
    @asynccontextmanager
    async def bot(cls) -> AsyncIterator[Self]:
        token = required_slack_value(ENV.SLACK_BOT_TOKEN)
        async with aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=30, sock_connect=10),
            response_class=_SlackResponse,
            connector=pooled_connector(),
            connector_owner=False,
        ) as session:
            yield cls(
                token=token,
                base_url=SLACK_API_BASE_URL,
                timeout=30,
                session=session,
                logger=_SDK_LOGGER,
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


def slack_error_details(exc: Exception) -> list[str]:
    """Slack's ``response_metadata.messages``, which name the offending block for ``invalid_blocks``."""
    data = getattr(getattr(exc, "response", None), "data", None)
    metadata = data.get("response_metadata") if isinstance(data, dict) else None
    messages = metadata.get("messages") if isinstance(metadata, dict) else None
    if not isinstance(messages, list):
        return []
    return [message[:300] for message in messages if isinstance(message, str)][:10]


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
