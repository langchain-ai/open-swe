"""Credential-safe Trigger Server callback client."""

from __future__ import annotations

import asyncio
import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
import uuid
from collections.abc import Callable, Mapping
from typing import TYPE_CHECKING, Literal, Protocol, TypedDict

from pydantic import TypeAdapter, ValidationError

from managed_deepagents._channels.trigger.types import TriggerStartedThread

if TYPE_CHECKING:
    from http.client import HTTPMessage
    from typing import IO

    from managed_deepagents._channels.trigger.types import (
        TriggerChannelActionRequest,
        TriggerChannelActionResponse,
        TriggerChannelRuntimeEventRequest,
        TriggerChannelRuntimeEventResponse,
    )

LANGSMITH_MDA_TRIGGER_SERVER_URL_ENV = "LANGSMITH_MDA_TRIGGER_SERVER_URL"
TRIGGER_HTTP_TIMEOUT_SECONDS = 30.0
HTTP_SUCCESS_MIN = 200
HTTP_SUCCESS_MAX = 300

TriggerClientOperation = Literal["actions", "events"]


class TriggerClientError(RuntimeError):
    """Fixed-message callback error safe for diagnostics."""

    def __init__(
        self,
        operation: TriggerClientOperation,
        status: int | None = None,
    ) -> None:
        self.operation = operation
        self.status = status
        suffix = "" if status is None else f" with status {status}"
        super().__init__(f"Channel {operation} request failed{suffix}.")


class TriggerHttpResponse(TypedDict):
    status: int
    body: bytes


TriggerFetch = Callable[[str, Mapping[str, str], bytes, float], TriggerHttpResponse]


class TriggerClientLike(Protocol):
    async def post_action(
        self,
        channel_id: str,
        body: TriggerChannelActionRequest,
    ) -> TriggerChannelActionResponse: ...

    async def post_runtime_event(
        self,
        channel_id: str,
        body: TriggerChannelRuntimeEventRequest,
    ) -> TriggerChannelRuntimeEventResponse: ...


class TriggerClient(TriggerClientLike):
    """Minimal Trigger Server client authenticated by the hosted server key."""

    def __init__(
        self,
        *,
        user_id: str | None = None,
        env: Mapping[str, str] | None = None,
        fetch: TriggerFetch | None = None,
        timeout: float = TRIGGER_HTTP_TIMEOUT_SECONDS,
    ) -> None:
        resolved_env = os.environ if env is None else env
        self._api_base = _trigger_server_api_base(_configured_trigger_server_url(resolved_env))
        api_key = _configured_api_key(resolved_env)
        if not api_key:
            raise ValueError("Channel runtime is not configured.")
        self._api_key = api_key
        self._user_id = _uuid_string(user_id)
        self._fetch = fetch or _default_fetch
        self._timeout = timeout

    async def post_action(
        self,
        channel_id: str,
        body: TriggerChannelActionRequest,
    ) -> TriggerChannelActionResponse:
        encoded_channel_id = urllib.parse.quote(channel_id, safe="")
        response = await self._post(
            "actions",
            f"{self._api_base}/channels/{encoded_channel_id}/actions",
            body,
        )
        return _parse_action_response(response)

    async def post_deployment_action(
        self,
        deployment_id: str,
        channel_name: str,
        body: TriggerChannelActionRequest,
    ) -> TriggerChannelActionResponse:
        query = urllib.parse.urlencode(
            {"deployment_id": deployment_id, "channel_name": channel_name}
        )
        response = await self._post(
            "actions",
            f"{self._api_base}/channels/actions?{query}",
            body,
        )
        return _parse_action_response(response)

    async def post_runtime_event(
        self,
        channel_id: str,
        body: TriggerChannelRuntimeEventRequest,
    ) -> TriggerChannelRuntimeEventResponse:
        encoded_channel_id = urllib.parse.quote(channel_id, safe="")
        response = await self._post(
            "events",
            f"{self._api_base}/channels/{encoded_channel_id}/events",
            body,
        )
        status = response.get("status") if isinstance(response, dict) else None
        if not isinstance(status, str):
            raise TriggerClientError("events")
        return {"status": status}

    async def _post(
        self,
        operation: TriggerClientOperation,
        url: str,
        body: TriggerChannelActionRequest | TriggerChannelRuntimeEventRequest,
    ) -> object:
        headers = {
            "content-type": "application/json",
            "x-api-key": self._api_key,
        }
        if self._user_id is not None:
            headers["x-user-id"] = self._user_id
        encoded_body = json.dumps(body, separators=(",", ":")).encode()
        try:
            response = await asyncio.to_thread(
                self._fetch,
                url,
                headers,
                encoded_body,
                self._timeout,
            )
        except Exception:  # noqa: BLE001 - never expose arbitrary outbound errors
            raise TriggerClientError(operation) from None
        status = response.get("status")
        if not isinstance(status, int) or not HTTP_SUCCESS_MIN <= status < HTTP_SUCCESS_MAX:
            raise TriggerClientError(operation, status if isinstance(status, int) else None)
        try:
            return json.loads(response["body"])
        except (KeyError, TypeError, ValueError):
            raise TriggerClientError(operation) from None


_THREAD = TypeAdapter(TriggerStartedThread)


def _parse_action_response(response: object) -> TriggerChannelActionResponse:
    response_id = response.get("id") if isinstance(response, dict) else None
    if not isinstance(response_id, str) or not response_id.strip():
        raise TriggerClientError("actions")
    parsed: TriggerChannelActionResponse = {"id": response_id}
    try:
        thread = _THREAD.validate_python(response, strict=True)
    except ValidationError:
        return parsed
    if thread["channel_id"].strip() and thread["source_thread_key"].strip():
        parsed["thread"] = thread
    return parsed


def create_trigger_client(
    *,
    user_id: str | None = None,
    env: Mapping[str, str] | None = None,
    fetch: TriggerFetch | None = None,
    timeout: float = TRIGGER_HTTP_TIMEOUT_SECONDS,
) -> TriggerClient | None:
    """Build a client when both hosted Trigger settings are present."""
    resolved_env = os.environ if env is None else env
    if _configured_trigger_server_url(resolved_env) is None or not _configured_api_key(
        resolved_env
    ):
        return None
    return TriggerClient(user_id=user_id, env=resolved_env, fetch=fetch, timeout=timeout)


def _uuid_string(value: str | None) -> str | None:
    try:
        return str(uuid.UUID(value)) if value else None
    except (AttributeError, ValueError):
        return None


def _configured_trigger_server_url(env: Mapping[str, str]) -> str | None:
    return (env.get(LANGSMITH_MDA_TRIGGER_SERVER_URL_ENV) or "").strip() or None


def _configured_api_key(env: Mapping[str, str]) -> str:
    # Self-hosted Host injects the deployment key under its legacy name.
    return (env.get("LANGSMITH_API_KEY") or "").strip() or (
        env.get("LANGCHAIN_API_KEY") or ""
    ).strip()


def _trigger_server_api_base(raw: str | None) -> str:
    if not raw or not raw.strip():
        raise ValueError("Channel runtime is not configured.")
    # Match the raw path as well as the parsed URL so both runtimes reject normalization tricks.
    match = re.fullmatch(r"(?i:https)://[^/\\?#\s]+((?:/[A-Za-z0-9._~-]+)*/?)?", raw.strip())
    path = (match[1] or "").removesuffix("/") if match else ""
    if (
        match is None
        or (path and not path.endswith("/v1"))
        or any(segment in {".", ".."} for segment in path.split("/"))
    ):
        raise ValueError("Channel runtime configuration is invalid.")
    try:
        parsed = urllib.parse.urlsplit(raw.strip())
        hostname = parsed.hostname
        _ = parsed.port
    except ValueError:
        raise ValueError("Channel runtime configuration is invalid.") from None
    if (
        parsed.scheme != "https"
        or not hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("Channel runtime configuration is invalid.")
    return f"https://{parsed.netloc}{path or '/v1'}"


class _NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(
        self,
        req: urllib.request.Request,
        fp: IO[bytes],
        code: int,
        msg: str,
        headers: HTTPMessage,
        newurl: str,
    ) -> urllib.request.Request | None:
        del req, fp, code, msg, headers, newurl
        return None


def _default_fetch(
    url: str,
    headers: Mapping[str, str],
    body: bytes,
    timeout: float,
) -> TriggerHttpResponse:
    request = urllib.request.Request(  # noqa: S310 - origin is validated HTTPS
        url,
        data=body,
        headers=dict(headers),
        method="POST",
    )
    opener = urllib.request.build_opener(_NoRedirectHandler())
    try:
        with opener.open(request, timeout=timeout) as response:
            return {"status": response.status, "body": response.read()}
    except urllib.error.HTTPError as error:
        return {"status": error.code, "body": b""}


__all__ = [
    "LANGSMITH_MDA_TRIGGER_SERVER_URL_ENV",
    "TRIGGER_HTTP_TIMEOUT_SECONDS",
    "TriggerClient",
    "TriggerClientError",
    "TriggerClientLike",
    "TriggerFetch",
    "TriggerHttpResponse",
    "create_trigger_client",
]
