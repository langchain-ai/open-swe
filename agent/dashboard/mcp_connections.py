"""MCP connections for one owner: a GitHub login, or the shared workspace.

Records are encrypted at rest and written with optimistic concurrency: every
write rotates ``version`` and is verified after the fact, so two workers that
race on one record both notice instead of one silently overwriting the other.
``revision`` changes only when a person edits the connection's settings, which
is what live tool sessions compare against.
"""

import asyncio
import json
import re
import time
import uuid
from collections.abc import Callable, Coroutine
from functools import partial
from typing import Any, Literal, cast
from urllib.parse import parse_qsl

import httpx
from langchain_mcp_adapters.sessions import Connection, create_session
from mcp import ClientSession
from mcp.types import PaginatedRequestParams, Tool
from starlette.requests import Request
from starlette.responses import Response, StreamingResponse

from agent.dashboard.mcp_http import MCPConnectionError, resolve_url
from agent.encryption import decrypt_token, encrypt_token
from agent.store import delete_value, get_value, now_iso, put_value, search_all_values
from agent.tool_loaders.mcp_transport import mcp_http_client

Scope = Literal["user", "workspace"]
WORKSPACE_OWNER = "workspace"
MCP_PRESETS = [
    {"name": "Notion", "url": "https://mcp.notion.com/mcp", "auth_type": "oauth"},
    {"name": "Slack", "url": "https://mcp.slack.com/mcp", "auth_type": "oauth"},
    {"name": "Datadog (US1)", "url": "https://mcp.datadoghq.com/v1/mcp", "auth_type": "oauth"},
]
WORKSPACE_NAME_PATTERN = re.compile(r"^[a-z][a-z0-9_-]{0,31}$")
_NAMESPACE = "mcp_connections"
_LEGACY_WORKSPACE_NAMESPACE = ["workspace_mcps"]
_AUTH_TYPES = {"none", "bearer", "headers", "oauth"}
_TRANSPORTS = {"streamable_http", "sse"}
_TOKEN_METHODS = {"none", "client_secret_basic", "client_secret_post"}
_DISCOVERY_TIMEOUT = 45
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
_SECRET_QUERY_SUFFIXES = (
    "apikey",
    "apitoken",
    "authtoken",
    "accesstoken",
    "refreshtoken",
    "authorization",
    "password",
    "secret",
)
_PUBLIC_FIELDS = (
    "id",
    "name",
    "url",
    "transport",
    "enabled",
    "auth_type",
    "allowed_tools",
    "status",
    "revision",
    "created_at",
    "updated_at",
)


def _namespace(owner: str) -> list[str]:
    if not isinstance(owner, str) or not owner or len(owner) > 256:
        raise MCPConnectionError(401, "Authentication required")
    return [_NAMESPACE, owner]


def _connection_id(value: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[a-f0-9]{32}", value):
        raise MCPConnectionError(404, "MCP connection not found")
    return value


def seal(value: dict[str, Any]) -> dict[str, str]:
    return {"encrypted_record": encrypt_token(json.dumps(value))}


def unseal(value: dict[str, Any]) -> dict[str, Any]:
    try:
        result = json.loads(decrypt_token(value.get("encrypted_record", "")))
        if not isinstance(result, dict):
            raise ValueError
        return result
    except ValueError, TypeError:
        raise MCPConnectionError(
            503, "MCP credentials are unavailable; reconnect the connection"
        ) from None


async def get_record(owner: str, connection_id: str) -> dict[str, Any]:
    value = await get_value(_namespace(owner), _connection_id(connection_id))
    if value is None:
        raise MCPConnectionError(404, "MCP connection not found")
    record = unseal(value)
    if record.get("owner") != owner or record.get("id") != connection_id:
        raise MCPConnectionError(404, "MCP connection not found")
    return record


async def put_record(owner: str, record: dict[str, Any], *, expected_version: str | None) -> None:
    """Write ``record`` only if nobody else wrote since ``expected_version`` was read.

    The store has no compare-and-swap, so this checks before and verifies after:
    a concurrent writer is caught by one of the two reads. ``expected_version``
    is ``None`` for a brand-new record.
    """
    namespace = _namespace(owner)
    current = await get_value(namespace, record["id"])
    if expected_version is None:
        if current is not None:
            raise MCPConnectionError(409, "MCP connection changed; retry")
    elif current is None or unseal(current).get("version") != expected_version:
        raise MCPConnectionError(409, "MCP connection changed; retry")
    record["version"] = uuid.uuid4().hex
    await put_value(namespace, record["id"], seal(record))
    stored = await get_value(namespace, record["id"])
    if stored is None or unseal(stored).get("version") != record["version"]:
        raise MCPConnectionError(409, "MCP connection changed; retry")


def public_record(record: dict[str, Any]) -> dict[str, Any]:
    tools = record.get("tools", [])
    return {
        **{key: record.get(key) for key in _PUBLIC_FIELDS},
        "scope": "workspace" if record["owner"] == WORKSPACE_OWNER else "user",
        "tools": [
            {"name": tool["name"], "description": tool.get("description", "")} for tool in tools
        ],
        "tool_names": sorted(tool["name"] for tool in tools),
        **{
            key: record.get(key, "")
            for key in ("oauth_client_id", "oauth_scope", "oauth_authorization_server")
        },
        "oauth_token_endpoint_auth_method": record.get("oauth_token_endpoint_auth_method", "none"),
        "headers_configured": bool(record.get("headers")),
        "header_names": sorted(record.get("headers", {})),
        "bearer_token_configured": bool(record.get("bearer_token")),
        "oauth_configured": bool(record.get("oauth", {}).get("tokens")),
        "oauth_client_configured": bool(record.get("oauth_client_id")),
        "oauth_client_secret_configured": bool(record.get("oauth_client_secret")),
        "tested_at": record.get("tested_at"),
    }


async def list_records(owner: str) -> list[dict[str, Any]]:
    if owner == WORKSPACE_OWNER:
        await _migrate_legacy_workspace()
    records = [unseal(value) for value in await search_all_values(_namespace(owner))]
    return sorted(
        (record for record in records if record.get("owner") == owner),
        key=lambda record: (record.get("name", ""), record["id"]),
    )


async def list_connections(owner: str) -> list[dict[str, Any]]:
    return [public_record(record) for record in await list_records(owner)]


async def _migrate_legacy_workspace() -> None:
    """Move admin connections saved by the earlier workspace-only feature."""
    try:
        legacy = await search_all_values(_LEGACY_WORKSPACE_NAMESPACE)
    except Exception:
        return
    for value in legacy:
        name = value.get("name")
        if not isinstance(name, str):
            continue
        headers: dict[str, str] = {}
        if value.get("encrypted_headers"):
            try:
                headers = json.loads(decrypt_token(value["encrypted_headers"]))
            except ValueError, TypeError:
                headers = {}
        record = _new_record(WORKSPACE_OWNER)
        record.update(
            name=name,
            url=value.get("url", ""),
            transport=value.get("transport", "streamable_http"),
            enabled=bool(value.get("enabled", True)),
            auth_type="headers" if headers else "none",
            headers=headers,
            allowed_tools=list(value.get("allowed_tools") or []),
            revision=value.get("revision") or uuid.uuid4().hex,
            updated_at=value.get("updated_at") or now_iso(),
        )
        try:
            await put_record(WORKSPACE_OWNER, record, expected_version=None)
            await delete_value(_LEGACY_WORKSPACE_NAMESPACE, name)
        except MCPConnectionError:
            continue


def _is_secret_query_key(key: str) -> bool:
    normalized = re.sub(r"[^a-z0-9]", "", key.lower())
    return normalized in {"key", "token"} or normalized.endswith(_SECRET_QUERY_SUFFIXES)


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
            or key.lower() in headers
            or not isinstance(item, str)
            or len(item) > 8192
            or any(ord(c) < 32 or ord(c) > 126 for c in item)
        ):
            raise MCPConnectionError(400, "Invalid, duplicate or reserved MCP header")
        headers[key.lower()] = item
    return headers


def _validate_allowed_tools(value: Any) -> list[str] | None:
    if value is None:
        return None
    if not isinstance(value, list) or len(value) > 1000:
        raise MCPConnectionError(400, "Allowed tools must be a list of tool names")
    names: list[str] = []
    for item in value:
        if not isinstance(item, str) or not item.strip() or len(item) > 128:
            raise MCPConnectionError(400, "Allowed tools must be a list of tool names")
        if item.strip() not in names:
            names.append(item.strip())
    return names


def _text(value: Any, maximum: int, *, empty: bool = False) -> str:
    if (
        not isinstance(value, str)
        or len(value) > maximum
        or (not value and not empty)
        or any(ord(c) < 32 or ord(c) == 127 for c in value)
    ):
        raise MCPConnectionError(400, "Invalid MCP connection field")
    return value


async def _validate_server_url(value: Any) -> str:
    url = await resolve_url(_text(value, 2048), allow_query=True)
    if any(_is_secret_query_key(key) for key, _ in parse_qsl(url.query.decode())):
        raise MCPConnectionError(400, "Put credentials in headers, not the URL query")
    return str(url)


def _new_record(owner: str) -> dict[str, Any]:
    return {
        "id": uuid.uuid4().hex,
        "owner": owner,
        "name": "",
        "url": "",
        "transport": "streamable_http",
        "enabled": True,
        "auth_type": "none",
        "headers": {},
        "bearer_token": "",
        "allowed_tools": None,
        "tools": [],
        "status": "untested",
        "created_at": now_iso(),
        "revision": uuid.uuid4().hex,
        "updated_at": now_iso(),
    }


_ALLOWED_INPUT = {
    "id",
    "name",
    "url",
    "transport",
    "enabled",
    "auth_type",
    "headers",
    "bearer_token",
    "allowed_tools",
    "oauth_client_id",
    "oauth_client_secret",
    "oauth_authorization_server",
    "oauth_token_endpoint_auth_method",
    "oauth_scope",
}
_SECURITY_FIELDS = (
    "url",
    "auth_type",
    "oauth_client_id",
    "oauth_client_secret",
    "oauth_scope",
    "oauth_authorization_server",
    "oauth_token_endpoint_auth_method",
)


async def _prepare(owner: str, data: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    """Validate ``data`` against the saved record; return (existing, merged)."""
    if not isinstance(data, dict) or data.keys() - _ALLOWED_INPUT:
        raise MCPConnectionError(400, "Invalid MCP connection fields")
    existing = await get_record(owner, _connection_id(data["id"])) if "id" in data else {}
    record = {**_new_record(owner), **existing, **data}
    record["name"] = _text(record["name"], 100)
    if owner == WORKSPACE_OWNER and not WORKSPACE_NAME_PATTERN.match(record["name"]):
        raise MCPConnectionError(
            400,
            "Connection name must start with a lowercase letter and contain only lowercase "
            "letters, numbers, hyphens, or underscores (1-32 characters); for example, incident",
        )
    record["url"] = await _validate_server_url(record["url"])
    issuer = _text(record.get("oauth_authorization_server", ""), 2048, empty=True)
    record["oauth_authorization_server"] = str(await resolve_url(issuer)) if issuer else ""
    record["auth_type"] = _text(record["auth_type"], 20)
    record["transport"] = _text(record.get("transport", "streamable_http"), 20)
    if (
        type(record["enabled"]) is not bool
        or record["auth_type"] not in _AUTH_TYPES
        or record["transport"] not in _TRANSPORTS
    ):
        raise MCPConnectionError(400, "Invalid MCP authentication type, transport or enabled flag")
    if owner == WORKSPACE_OWNER and record["auth_type"] == "oauth":
        raise MCPConnectionError(400, "Workspace MCP connections cannot use OAuth")
    record["headers"] = _validate_headers(record["headers"])
    record["allowed_tools"] = _validate_allowed_tools(record.get("allowed_tools"))
    for field in ("bearer_token", "oauth_client_id", "oauth_client_secret", "oauth_scope"):
        record[field] = _text(record.get(field, ""), 8192, empty=True)
    if any(ord(c) > 126 for c in cast(str, record["bearer_token"])):
        raise MCPConnectionError(400, "Invalid bearer token")
    method = _text(record.get("oauth_token_endpoint_auth_method", "none"), 30)
    if method not in _TOKEN_METHODS:
        raise MCPConnectionError(400, "Unsupported OAuth client authentication method")
    record["oauth_token_endpoint_auth_method"] = method
    if record["auth_type"] != "headers":
        record["headers"] = {}
    if record["auth_type"] != "bearer":
        record["bearer_token"] = ""
    if any(existing.get(field, "") != record.get(field, "") for field in _SECURITY_FIELDS):
        record.pop("oauth", None)
    if existing and existing["url"] != record["url"]:
        if "headers" not in data:
            record["headers"] = {}
        if "bearer_token" not in data:
            record["bearer_token"] = ""
    if existing and any(
        existing.get(field, "") != record[field] for field in ("url", "oauth_authorization_server")
    ):
        for field in ("oauth_client_id", "oauth_client_secret"):
            if field not in data:
                record[field] = ""
    if (
        existing.get("oauth_client_id", "") != record["oauth_client_id"]
        and "oauth_client_secret" not in data
    ):
        record["oauth_client_secret"] = ""
    return existing, record


async def save_connection(owner: str, data: dict[str, Any]) -> dict[str, Any]:
    existing, record = await _prepare(owner, data)
    record["revision"] = uuid.uuid4().hex
    record["updated_at"] = now_iso()
    await put_record(owner, record, expected_version=existing.get("version"))
    return await _discover_and_store(owner, record)


async def delete_connection(owner: str, id: str) -> None:
    await delete_value(_namespace(owner), _connection_id(id))


async def reveal_headers(owner: str, id: str) -> dict[str, str]:
    """Saved header values, for admins editing a shared connection."""
    if owner != WORKSPACE_OWNER:
        raise MCPConnectionError(404, "MCP connection not found")
    return dict((await get_record(owner, id)).get("headers", {}))


async def connection_headers(owner: str, record: dict[str, Any]) -> dict[str, str]:
    if record["auth_type"] == "headers":
        return record["headers"].copy()
    if record["auth_type"] == "bearer":
        token = record.get("bearer_token")
        if not token:
            raise MCPConnectionError(409, "MCP authentication must be configured")
        return {"Authorization": f"Bearer {token}"}
    if record["auth_type"] == "oauth":
        from agent.dashboard.mcp_oauth import access_token_for

        return {"Authorization": f"Bearer {await access_token_for(owner, record)}"}
    return {}


class ConnectionAuth(httpx.Auth):
    """Re-read the saved connection before every request so edits apply at once."""

    def __init__(self, owner: str, record: dict[str, Any]) -> None:
        self.owner = owner
        self.id = record["id"]
        self.revision = record["revision"]
        self.url = record["url"]

    async def async_auth_flow(self, request: httpx.Request):
        if str(request.url) != self.url:
            raise MCPConnectionError(400, "MCP endpoint changes require reconnecting")
        record = await get_record(self.owner, self.id)
        if not record["enabled"] or record["revision"] != self.revision:
            raise MCPConnectionError(409, "MCP connection changed; reconnect")
        request.headers.update(await connection_headers(self.owner, record))
        yield request


def connection_for(owner: str, record: dict[str, Any]) -> Connection:
    connection: dict[str, Any] = {
        "transport": record.get("transport", "streamable_http"),
        "url": record["url"],
        "headers": {},
        "auth": ConnectionAuth(owner, record),
        "httpx_client_factory": partial(mcp_http_client, record["url"]),
        "timeout": 30,
        "sse_read_timeout": 300,
    }
    return cast(Connection, connection)


async def connection_config(owner: str, id: str) -> Connection:
    record = await get_record(owner, id)
    if not record["enabled"]:
        raise MCPConnectionError(409, "MCP connection is disabled")
    return connection_for(owner, record)


def runnable_tools(record: dict[str, Any]) -> list[str]:
    """Tool names a run may call: the catalog, narrowed by an explicit allowlist."""
    catalog = sorted(tool["name"] for tool in record.get("tools", []))
    allowed = record.get("allowed_tools")
    if record["owner"] == WORKSPACE_OWNER and not allowed:
        return []
    if allowed is None:
        return catalog
    return [name for name in catalog if name in set(allowed)]


async def list_tools(owner: str, record: dict[str, Any]) -> list[Tool]:
    """Read the server's full catalog through a short-lived session."""
    connection = cast(
        Connection,
        {
            **connection_for(owner, record),
            "auth": None,
            "headers": await connection_headers(owner, record),
        },
    )
    async with asyncio.timeout(_DISCOVERY_TIMEOUT):
        async with create_session(connection) as session:
            await session.initialize()
            return await catalog(session)


async def catalog(session: ClientSession) -> list[Tool]:
    """Every tool the server advertises, following pagination cursors."""
    tools: list[Tool] = []
    names: set[str] = set()
    cursor = None
    cursors: set[str] = set()
    for _ in range(100):
        result = await session.list_tools(params=PaginatedRequestParams(cursor=cursor))
        for tool in result.tools:
            if tool.name in names:
                raise MCPConnectionError(502, "MCP server returned duplicate tool names")
            names.add(tool.name)
            tools.append(tool)
        if len(tools) > 10000:
            raise MCPConnectionError(502, "MCP catalog exceeds the size limit")
        cursor = result.nextCursor
        if not cursor:
            return tools
        if cursor in cursors:
            raise MCPConnectionError(502, "Invalid MCP catalog pagination")
        cursors.add(cursor)
    raise MCPConnectionError(502, "MCP catalog exceeds the page limit")


def _discovery_error(error: BaseException) -> MCPConnectionError:
    pending: list[BaseException] = [error]
    while pending:
        current = pending.pop()
        if isinstance(current, MCPConnectionError):
            return current
        if isinstance(current, BaseExceptionGroup):
            pending.extend(reversed(current.exceptions))
        elif isinstance(current, httpx.HTTPStatusError):
            status = current.response.status_code
            hint = {
                401: "Check the authentication settings",
                403: "Check credentials, permissions, the server region, and MCP access settings",
                404: "Check the MCP server URL",
                429: "Wait before retrying; the MCP server is rate limiting requests",
            }.get(status, "Check the MCP server availability")
            return MCPConnectionError(400, f"MCP tool discovery failed (HTTP {status}). {hint}")
        elif isinstance(current, (TimeoutError, httpx.TimeoutException)):
            return MCPConnectionError(400, "MCP tool discovery timed out; check the server")
    return MCPConnectionError(
        400, "Could not discover MCP tools; check the URL and authentication settings"
    )


async def discover_draft(owner: str, data: dict[str, Any]) -> list[dict[str, str]]:
    """List a draft's tools for someone choosing an allowlist; persist nothing."""
    _, record = await _prepare(owner, data)
    try:
        tools = await list_tools(owner, record)
    except Exception as exc:
        raise _discovery_error(exc) from None
    return [{"name": tool.name, "description": tool.description or ""} for tool in tools]


async def _discover_and_store(owner: str, record: dict[str, Any]) -> dict[str, Any]:
    try:
        tools = await list_tools(owner, record)
        record["tools"] = [
            {"name": tool.name, "description": tool.description or ""} for tool in tools
        ]
        record["status"] = "connected"
    except Exception:
        record["tools"] = []
        record["status"] = (
            "auth_required"
            if record["auth_type"] == "oauth" and not record.get("oauth", {}).get("tokens")
            else "error"
        )
    record["tested_at"] = now_iso()
    await put_record(owner, record, expected_version=record["version"])
    return public_record(record)


async def discover_connection(owner: str, id: str) -> dict[str, Any]:
    return await _discover_and_store(owner, await get_record(owner, id))


async def update_record[T](
    owner: str,
    id: str,
    change: Callable[[dict[str, Any]], Coroutine[Any, Any, T]],
) -> tuple[dict[str, Any], T]:
    """Read, apply ``change`` and write, retrying when another writer got in between."""
    for attempt in range(3):
        record = await get_record(owner, id)
        result = await change(record)
        try:
            await put_record(owner, record, expected_version=record["version"])
        except MCPConnectionError as exc:
            if exc.status_code == 409 and attempt < 2:
                await asyncio.sleep(0.05 * (attempt + 1))
                continue
            raise
        return record, result
    raise MCPConnectionError(409, "MCP connection changed; retry")


def _session_token(owner: str, record: dict[str, Any], upstream_id: str) -> str:
    return encrypt_token(
        json.dumps(
            {
                "owner": owner,
                "id": record["id"],
                "revision": record["revision"],
                "upstream_id": upstream_id,
                "expires_at": time.time() + 86400,
            }
        )
    )


def _upstream_session(owner: str, record: dict[str, Any], token: str) -> str:
    try:
        if len(token) > 16384:
            raise ValueError
        data = json.loads(decrypt_token(token))
        if (
            any(
                data.get(field) != value
                for field, value in (
                    ("owner", owner),
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


async def proxy_connection(request: Request, owner: str, id: str) -> Response:
    """Relay one Streamable HTTP exchange for the desktop app, adding the saved credentials."""
    if request.method not in {"GET", "POST", "DELETE"}:
        raise MCPConnectionError(405, "Unsupported MCP method")
    if request.url.query:
        raise MCPConnectionError(400, "MCP proxy query parameters are not supported")
    record = await get_record(owner, id)
    if not record["enabled"]:
        raise MCPConnectionError(409, "MCP connection is disabled")
    headers = await connection_headers(owner, record)
    for key in ("accept", "content-type", "mcp-protocol-version", "last-event-id"):
        if key in request.headers:
            headers[key] = request.headers[key]
    if "mcp-session-id" in request.headers:
        headers["mcp-session-id"] = _upstream_session(
            owner, record, request.headers["mcp-session-id"]
        )
    body = bytearray()
    async for chunk in request.stream():
        body.extend(chunk)
        if len(body) > 4 * 1024 * 1024:
            raise MCPConnectionError(413, "MCP request exceeds the size limit")
    client = mcp_http_client(record["url"], timeout=httpx.Timeout(30, read=300))
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
        response_headers["mcp-session-id"] = _session_token(owner, record, session_id)
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
