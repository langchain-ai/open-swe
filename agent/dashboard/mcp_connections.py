"""Owner-scoped cloud MCP CRUD, catalog discovery and Streamable HTTP proxy."""

import asyncio
import json
import re
import time
import uuid
import weakref
from functools import partial
from typing import Any

import httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
from mcp.types import PaginatedRequestParams
from starlette.requests import Request
from starlette.responses import Response, StreamingResponse

from agent.dashboard.mcp_http import MCPConnectionError, resolve_url, safe_client
from agent.encryption import decrypt_token, encrypt_token
from agent.store import delete_value, get_value, now_iso, put_value, search_all_values

MCP_PRESETS = [
    {"name": "Notion", "url": "https://mcp.notion.com/mcp", "auth_type": "oauth"},
    {"name": "Slack", "url": "https://mcp.slack.com/mcp", "auth_type": "oauth"},
    {"name": "Datadog (US1)", "url": "https://mcp.datadoghq.com/v1/mcp", "auth_type": "oauth"},
]
_NAMESPACE = "mcp_connections"
_locks: weakref.WeakValueDictionary[tuple[str, str], asyncio.Lock] = weakref.WeakValueDictionary()
_FORBIDDEN_HEADERS = {
    "host",
    "authorization",
    "cookie",
    "set-cookie",
    "proxy-authorization",
    "connection",
    "transfer-encoding",
    "content-length",
    "content-type",
    "accept",
    "te",
    "trailer",
    "upgrade",
    "keep-alive",
    "origin",
    "referer",
    "mcp-session-id",
    "mcp-protocol-version",
    "last-event-id",
}


def _namespace(login: str) -> list[str]:
    if not isinstance(login, str) or not login or len(login) > 256:
        raise MCPConnectionError(401, "Authentication required")
    return [_NAMESPACE, login]


def _connection_id(value: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[a-f0-9]{32}", value):
        raise MCPConnectionError(404, "MCP connection not found")
    return value


def _lock(login: str, connection_id: str) -> asyncio.Lock:
    key = (login, connection_id)
    lock = _locks.get(key)
    if lock is None:
        lock = asyncio.Lock()
        _locks[key] = lock
    return lock


def _seal(value: dict[str, Any]) -> dict[str, str]:
    return {"encrypted_record": encrypt_token(json.dumps(value))}


def _unseal(value: dict[str, Any]) -> dict[str, Any]:
    try:
        result = json.loads(decrypt_token(value.get("encrypted_record", "")))
        if not isinstance(result, dict):
            raise ValueError
        return result
    except ValueError, TypeError:
        raise MCPConnectionError(
            503, "MCP credentials are unavailable; reconnect the connection"
        ) from None


async def _get(login: str, connection_id: str) -> dict[str, Any]:
    value = await get_value(_namespace(login), _connection_id(connection_id))
    if value is None:
        raise MCPConnectionError(404, "MCP connection not found")
    record = _unseal(value)
    if record.get("owner") != login or record.get("id") != connection_id:
        raise MCPConnectionError(404, "MCP connection not found")
    return record


async def _put(login: str, record: dict[str, Any]) -> None:
    await put_value(_namespace(login), record["id"], _seal(record))


def _public(record: dict[str, Any]) -> dict[str, Any]:
    return {
        **{
            key: record[key]
            for key in (
                "id",
                "name",
                "url",
                "enabled",
                "auth_type",
                "tool_names",
                "status",
                "created_at",
                "updated_at",
            )
        },
        "headers_configured": bool(record.get("headers")),
        "bearer_token_configured": bool(record.get("bearer_token")),
        "oauth_configured": bool(record.get("oauth", {}).get("tokens")),
        "oauth_client_configured": bool(record.get("oauth_client_id")),
        "oauth_client_secret_configured": bool(record.get("oauth_client_secret")),
        "tested_at": record.get("tested_at"),
    }


async def list_connections(login: str) -> list[dict[str, Any]]:
    records = [_unseal(value) for value in await search_all_values(_namespace(login))]
    return [_public(record) for record in records if record.get("owner") == login]


def _validate_headers(value: Any) -> dict[str, str]:
    if not isinstance(value, dict) or len(value) > 32:
        raise MCPConnectionError(400, "Invalid MCP headers")
    headers: dict[str, str] = {}
    for key, item in value.items():
        if (
            not isinstance(key, str)
            or not re.fullmatch(r"[!#$%&'*+.^_`|~0-9A-Za-z-]{1,128}", key)
            or key.lower() in _FORBIDDEN_HEADERS
            or key.lower().startswith(("proxy-", "sec-", "x-forwarded-"))
            or not isinstance(item, str)
            or len(item) > 8192
            or any(ord(c) < 32 or ord(c) > 126 for c in item)
        ):
            raise MCPConnectionError(400, "Invalid or reserved MCP header")
        headers[key.lower()] = item
    return headers


def _text(value: Any, maximum: int, *, empty: bool = False) -> str:
    if (
        not isinstance(value, str)
        or len(value) > maximum
        or (not value and not empty)
        or any(ord(c) < 32 or ord(c) == 127 for c in value)
    ):
        raise MCPConnectionError(400, "Invalid MCP connection field")
    return value


async def save_connection(login: str, data: dict[str, Any]) -> dict[str, Any]:
    _namespace(login)
    allowed = {
        "id",
        "name",
        "url",
        "enabled",
        "auth_type",
        "headers",
        "bearer_token",
        "oauth_client_id",
        "oauth_client_secret",
        "oauth_token_endpoint_auth_method",
        "oauth_scope",
    }
    if not isinstance(data, dict) or data.keys() - allowed:
        raise MCPConnectionError(400, "Invalid MCP connection fields")
    connection_id = _connection_id(data["id"]) if "id" in data else uuid.uuid4().hex
    async with _lock(login, connection_id):
        existing = await _get(login, connection_id) if "id" in data else {}
        record = {
            "id": connection_id,
            "owner": login,
            "name": "",
            "url": "",
            "enabled": True,
            "auth_type": "none",
            "headers": {},
            "bearer_token": "",
            "tool_names": [],
            "status": "untested",
            "created_at": now_iso(),
            **existing,
            **data,
        }
        record["name"] = _text(record["name"], 100)
        url = _text(record["url"], 2048)
        record["url"] = str((await resolve_url(url))[0])
        record["auth_type"] = _text(record["auth_type"], 20)
        if type(record["enabled"]) is not bool or record["auth_type"] not in {
            "none",
            "bearer",
            "headers",
            "oauth",
        }:
            raise MCPConnectionError(400, "Invalid MCP authentication type or enabled flag")
        record["headers"] = _validate_headers(record["headers"])
        for field in ("bearer_token", "oauth_client_id", "oauth_client_secret", "oauth_scope"):
            record[field] = _text(record.get(field, ""), 8192, empty=True)
        if any(ord(c) > 126 for c in record["bearer_token"]):
            raise MCPConnectionError(400, "Invalid bearer token")
        method = _text(record.get("oauth_token_endpoint_auth_method", "none"), 30)
        if method not in {"none", "client_secret_basic", "client_secret_post"}:
            raise MCPConnectionError(400, "Unsupported OAuth client authentication method")
        record["oauth_token_endpoint_auth_method"] = method
        if record["auth_type"] != "headers":
            record["headers"] = {}
        if record["auth_type"] != "bearer":
            record["bearer_token"] = ""
        security_fields = (
            "url",
            "auth_type",
            "oauth_client_id",
            "oauth_client_secret",
            "oauth_scope",
            "oauth_token_endpoint_auth_method",
        )
        if any(existing.get(field) != record.get(field) for field in security_fields):
            record.pop("oauth", None)
        if existing and existing["url"] != record["url"]:
            if "headers" not in data:
                record["headers"] = {}
            if "bearer_token" not in data:
                record["bearer_token"] = ""
        record["revision"] = uuid.uuid4().hex
        record["updated_at"] = now_iso()
        await _put(login, record)
        return await _discover(login, record)


async def delete_connection(login: str, id: str) -> None:
    async with _lock(login, id):
        await _get(login, id)
        await delete_value(_namespace(login), id)


async def _headers(login: str, record: dict[str, Any]) -> dict[str, str]:
    if record["auth_type"] == "headers":
        return record["headers"].copy()
    if record["auth_type"] == "bearer":
        token = record.get("bearer_token")
        if not token:
            raise MCPConnectionError(409, "MCP authentication must be configured")
        return {"Authorization": f"Bearer {token}"}
    if record["auth_type"] == "oauth":
        from agent.dashboard.mcp_oauth import access_token

        return {"Authorization": f"Bearer {await access_token(login, record)}"}
    return {}


class _ConnectionAuth(httpx.Auth):
    def __init__(self, login: str, record: dict[str, Any]) -> None:
        self.login = login
        self.id = record["id"]
        self.revision = record["revision"]
        self.url = record["url"]

    async def async_auth_flow(self, request: httpx.Request):
        if str(request.url) != self.url:
            raise MCPConnectionError(400, "MCP endpoint changes require reconnecting")
        async with _lock(self.login, self.id):
            record = await _get(self.login, self.id)
            if not record["enabled"] or record["revision"] != self.revision:
                raise MCPConnectionError(409, "MCP connection changed; reconnect")
            request.headers.update(await _headers(self.login, record))
        yield request


async def _config(login: str, record: dict[str, Any]) -> dict[str, Any]:
    return {
        "transport": "streamable_http",
        "url": record["url"],
        "headers": {},
        "auth": _ConnectionAuth(login, record),
        "httpx_client_factory": partial(safe_client, endpoint=record["url"]),
    }


async def connection_config(login: str, id: str) -> dict[str, Any]:
    async with _lock(login, id):
        record = await _get(login, id)
        if not record["enabled"]:
            raise MCPConnectionError(409, "MCP connection is disabled")
        return await _config(login, record)


async def _discover(login: str, record: dict[str, Any]) -> dict[str, Any]:
    record["tool_names"] = []
    try:
        async with asyncio.timeout(45):
            config = await _config(login, record)
            async with config["httpx_client_factory"](
                headers=await _headers(login, record)
            ) as client:
                async with streamable_http_client(record["url"], http_client=client) as (
                    read,
                    write,
                    _,
                ):
                    async with ClientSession(read, write) as session:
                        await session.initialize()
                        names: set[str] = set()
                        cursor = None
                        cursors: set[str] = set()
                        for _ in range(100):
                            result = await session.list_tools(
                                params=PaginatedRequestParams(cursor=cursor)
                            )
                            names.update(tool.name for tool in result.tools)
                            if len(names) > 10000:
                                raise MCPConnectionError(502, "MCP catalog exceeds the size limit")
                            cursor = result.nextCursor
                            if not cursor:
                                break
                            if cursor in cursors:
                                raise MCPConnectionError(502, "Invalid MCP catalog pagination")
                            cursors.add(cursor)
                        else:
                            raise MCPConnectionError(502, "MCP catalog exceeds the page limit")
                        record["tool_names"] = sorted(names)
        record["status"] = "connected"
    except Exception:
        record["status"] = (
            "auth_required"
            if record["auth_type"] == "oauth" and not record.get("oauth", {}).get("tokens")
            else "error"
        )
    record["tested_at"] = now_iso()
    await _put(login, record)
    return _public(record)


async def discover_connection(login: str, id: str) -> dict[str, Any]:
    async with _lock(login, id):
        return await _discover(login, await _get(login, id))


async def start_oauth(login: str, id: str, redirect_uri: str) -> str:
    from agent.dashboard.mcp_oauth import start_oauth as start

    return await start(login, id, redirect_uri)


async def finish_oauth(state: str, code: str) -> dict[str, Any]:
    from agent.dashboard.mcp_oauth import finish_oauth as finish

    return await finish(state, code)


def _session_token(login: str, record: dict[str, Any], upstream_id: str) -> str:
    return encrypt_token(
        json.dumps(
            {
                "owner": login,
                "id": record["id"],
                "revision": record["revision"],
                "upstream_id": upstream_id,
                "expires_at": time.time() + 86400,
            }
        )
    )


def _upstream_session(login: str, record: dict[str, Any], token: str) -> str:
    try:
        if len(token) > 16384:
            raise ValueError
        data = json.loads(decrypt_token(token))
        if (
            any(
                data.get(field) != value
                for field, value in (
                    ("owner", login),
                    ("id", record["id"]),
                    ("revision", record["revision"]),
                )
            )
            or data["expires_at"] < time.time()
        ):
            raise ValueError
        return _text(data["upstream_id"], 4096)
    except ValueError, KeyError, TypeError, AttributeError:
        raise MCPConnectionError(404, "MCP session not found") from None


async def proxy_connection(request: Request, login: str, id: str) -> Response:
    if request.method not in {"GET", "POST", "DELETE"}:
        raise MCPConnectionError(405, "Unsupported MCP method")
    if request.url.query:
        raise MCPConnectionError(400, "MCP proxy query parameters are not supported")
    async with _lock(login, id):
        record = await _get(login, id)
        if not record["enabled"]:
            raise MCPConnectionError(409, "MCP connection is disabled")
        headers = await _headers(login, record)
    for key in ("accept", "content-type", "mcp-protocol-version", "last-event-id"):
        if key in request.headers:
            headers[key] = request.headers[key]
    if "mcp-session-id" in request.headers:
        headers["mcp-session-id"] = _upstream_session(
            login, record, request.headers["mcp-session-id"]
        )
    body = bytearray()
    async for chunk in request.stream():
        body.extend(chunk)
        if len(body) > 4 * 1024 * 1024:
            raise MCPConnectionError(413, "MCP request exceeds the size limit")
    client = safe_client(endpoint=record["url"])
    try:
        upstream = await client.send(
            client.build_request(
                request.method, record["url"], headers=headers, content=bytes(body)
            ),
            stream=True,
        )
    except asyncio.CancelledError:
        await client.aclose()
        raise
    except Exception:
        await client.aclose()
        raise MCPConnectionError(502, "MCP proxy request failed") from None
    if not upstream.is_success:
        status = (
            upstream.status_code
            if upstream.status_code in {400, 401, 403, 404, 405, 409, 429}
            else 502
        )
        await upstream.aclose()
        await client.aclose()
        return Response(
            "MCP upstream request rejected", status_code=status, media_type="text/plain"
        )
    response_headers = {"Cache-Control": "no-store", "X-Accel-Buffering": "no"}
    if session_id := upstream.headers.get("mcp-session-id"):
        response_headers["mcp-session-id"] = _session_token(login, record, session_id)
    content_type = upstream.headers.get("content-type", "application/json").split(";", 1)[0]
    if (
        content_type not in {"application/json", "text/event-stream"}
        and upstream.status_code != 204
    ):
        await upstream.aclose()
        await client.aclose()
        raise MCPConnectionError(502, "Invalid MCP response content type")

    async def stream():
        try:
            async for chunk in upstream.aiter_bytes():
                yield chunk
        finally:
            await upstream.aclose()
            await client.aclose()

    return StreamingResponse(
        stream(),
        status_code=upstream.status_code,
        headers=response_headers,
        media_type=content_type,
    )
