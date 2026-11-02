"""Managed MCP loading and the existing LangSmith authorization flow."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import threading
import warnings
from collections.abc import AsyncGenerator, Awaitable, Callable, Mapping, Sequence
from functools import partial
from http import HTTPStatus
from importlib.util import find_spec
from typing import TYPE_CHECKING, Any, cast
from urllib.parse import urlsplit

from managed_deepagents._agent_auth import AgentAuth
from managed_deepagents._agent_auth_http import (
    AGENT_AUTH_TIMEOUT_SECONDS,
    agent_auth_headers,
    request_agent_auth_json,
)
from managed_deepagents._connection_authorization import (
    PendingCredential,
    raise_credential_authorization_required,
    resolve_user_connection_grants,
)
from managed_deepagents._connections import (
    ConnectionReference,
    connections,
    is_connection_reference,
)
from managed_deepagents._connectors.mcp.langsmith_url import (
    is_langsmith_managed_tools_path,
    is_langsmith_managed_tools_url,
)
from managed_deepagents._connectors.tool_selection import select_tools
from managed_deepagents._credential_gate import CredentialConsumer
from managed_deepagents._graph_build_timing import build_step
from managed_deepagents._log import logger
from managed_deepagents._memory.async_util import await_if_needed
from managed_deepagents._memory.deps import optional_runtime_symbols, require_runtime_dependency

if TYPE_CHECKING:
    from langchain.mcp import MCPAdapter as NativeMCPAdapter
    from langchain_core.tools import BaseTool

    from managed_deepagents._agent_auth import AgentAuthContext
    from managed_deepagents._connectors.mcp import McpServersDefinition, RemoteMcpServerConfig


# langchain.mcp imports optional provider packages at module load.
with warnings.catch_warnings():
    warnings.filterwarnings("ignore", message=r"^`langchain\.mcp` is in beta\.")
    (MCPAdapter,) = (
        optional_runtime_symbols("langchain.mcp", "MCPAdapter")
        if all(find_spec(name) is not None for name in ("fastmcp", "mcp", "httpx2", "key_value"))
        else (None,)
    )

if TYPE_CHECKING or MCPAdapter is not None:
    import httpx2
    from fastmcp import Client
    from fastmcp.client.caching import KeyValueResponseCacheStore
    from fastmcp.client.group import ClientGroup
    from fastmcp.client.transports import SSETransport, StreamableHttpTransport
    from key_value.aio.stores.memory import MemoryStore
    from mcp.client.caching import CacheConfig, CacheEntry, CacheKey

    _AuthBase = httpx2.Auth
    _CacheStoreBase = KeyValueResponseCacheStore
else:
    _AuthBase = object
    _CacheStoreBase = object

INIT_TIMEOUT_SECONDS = 10.0
HTTP_TIMEOUT_SECONDS = 30.0
CACHE_TTL_MS = 60_000
CACHE_MAX_ENTRIES = 1024


def load_managed_mcp_tools(connectors: McpServersDefinition) -> list[BaseTool]:
    """Build eager tools synchronously, reusing FastMCP's cached listings."""
    return await_if_needed(load_eager_mcp_tools(connectors))


def mcp_credential_consumers(connectors: Sequence[object] | None) -> list[CredentialConsumer]:
    """The credential gate's view of user-connected and LangSmith MCP servers."""
    out: list[CredentialConsumer] = []
    for connector in connectors or []:
        if getattr(connector, "kind", None) != "mcp_servers":
            continue
        definition = cast("McpServersDefinition", connector)
        references = _user_connection_references(definition)
        langsmith = _langsmith_server_names(definition)
        if not references and not langsmith:
            continue
        out.append(
            CredentialConsumer(
                declaration=definition,
                credentials=references,
                load=partial(_load_caller_mcp_tools_sync, definition),
                cache_tools=False,
                requires_identity=bool(references),
                collect_pending_credentials=(
                    partial(collect_pending_managed_tools_credentials, definition)
                    if langsmith
                    else None
                ),
            )
        )
    return out


def _user_connection_references(
    connectors: McpServersDefinition,
) -> tuple[ConnectionReference, ...]:
    """Keep every owner assertion, including references sharing one slug."""
    return tuple(
        server["connection"]
        for server in connectors.config["servers"].values()
        if _uses_user_connection(server)
    )


def _langsmith_server_names(connectors: McpServersDefinition) -> list[str]:
    return [
        name
        for name, server in connectors.config["servers"].items()
        if _is_langsmith_server(server)
    ]


async def _fresh_user_token(context: AgentAuthContext, slug: str) -> str:
    # Missing grants during native requests fail; the pre-run gate owns interrupts.
    def missing(_payload: object) -> None:
        raise RuntimeError(f'MCP user connection "{slug}" has no access token')

    tokens = await asyncio.to_thread(
        resolve_user_connection_grants, AgentAuth(context), [slug], interrupt=missing
    )
    return tokens[slug]


async def load_caller_mcp_tools(
    connectors: McpServersDefinition,
    access_tokens: Mapping[str, str],
    context: AgentAuthContext,
) -> list[BaseTool]:
    """Load user-connection and LangSmith MCP tools for one verified caller."""
    servers = {
        name: server
        for name, server in connectors.config["servers"].items()
        if _uses_user_connection(server)
    }
    ready, pending = await asyncio.to_thread(
        _resolve_langsmith_servers, connectors, _langsmith_server_names(connectors), context
    )
    if pending:
        raise_credential_authorization_required(pending)
    servers.update(ready)
    auth = {}
    authorization: dict[str, object] = {}
    caller_scope = [context.base_url, context.workspace_id, context.agent_id, context.principal_id]
    for name, server in servers.items():
        reference = server.get("connection")
        if reference is not None:
            token = access_tokens.get(reference.slug)
            if not token:
                raise RuntimeError(f'MCP user connection "{reference.slug}" has no access token')
            auth[name] = ManagedAuth(
                server["url"],
                token=partial(_fresh_user_token, context, reference.slug),
                connection_slug=reference.slug,
            )
            authorization[name] = [caller_scope, reference.slug, reference.owner]
        else:
            headers = agent_auth_headers(api_key=context.api_key, workspace_id=context.workspace_id)
            headers["x-langsmith-identity"] = context.principal_id
            auth[name] = ManagedAuth(server["url"], headers=headers)
            authorization[name] = [caller_scope, context.api_key]

    return await load_tools(
        servers,
        auth=auth,
        authorization=authorization,
        throw_on_load_error=connectors.config.get("throw_on_load_error", True),
    )


def _matching_langsmith_servers(
    connectors: McpServersDefinition,
    server_names: Sequence[str],
    origin: str,
) -> list[tuple[str, Mapping[str, object], str]]:
    matching: list[tuple[str, Mapping[str, object], str]] = []
    for server_name in server_names:
        declared = connectors.config["servers"].get(server_name)
        if not isinstance(declared, dict):
            continue
        url = declared.get("url")
        if not isinstance(url, str):
            continue
        if not is_langsmith_managed_tools_url(url, origin):
            warnings.warn(
                f'MCP server "{server_name}" is not a LangSmith managed-tools URL '
                "for this deployment origin and was skipped.",
                RuntimeWarning,
                stacklevel=3,
            )
            continue
        matching.append((server_name, declared, url))
    return matching


def _resolve_langsmith_servers(
    connectors: McpServersDefinition,
    server_names: Sequence[str],
    context: AgentAuthContext,
) -> tuple[dict[str, RemoteMcpServerConfig], list[PendingCredential]]:
    """Probe LangSmith MCP servers once: ready configs plus any pending credentials.

    Shared by load (interrupt if pending) and the credential-gate collector
    (return pending only). Transport failures follow ``throw_on_load_error``;
    authorization failures are never skipped.
    """
    if not server_names:
        return {}, []
    matching = _matching_langsmith_servers(connectors, server_names, context.base_url.rstrip("/"))
    if not matching:
        return {}, []
    if not (context.api_key or "").strip():
        raise RuntimeError("LangSmith MCP servers require LANGSMITH_API_KEY at runtime")
    headers = agent_auth_headers(
        api_key=context.api_key,
        workspace_id=context.workspace_id,
    )
    headers["x-langsmith-identity"] = context.principal_id
    out: dict[str, RemoteMcpServerConfig] = {}
    pending: list[PendingCredential] = []
    for server_name, declared, url in matching:
        try:
            required = _probe_langsmith_server_access(server_name, url, headers)
        except OSError as exc:
            if connectors.config.get("throw_on_load_error", True):
                raise
            warnings.warn(
                f'MCP server "{server_name}" failed to load and was skipped: {exc}',
                RuntimeWarning,
                stacklevel=2,
            )
            continue
        if required:
            pending.extend(required)
            continue
        out[server_name] = _langsmith_server_config(declared)
    return out, pending


def collect_pending_managed_tools_credentials(
    connectors: McpServersDefinition,
    context: AgentAuthContext,
) -> list[PendingCredential]:
    """Collect pending Managed Tools credentials without interrupting.

    Incomplete 428s still raise a clear RuntimeError. Used by the
    credential gate so Managed Tools misses combine with ordinary connection misses.
    """
    return _resolve_langsmith_servers(
        connectors,
        _langsmith_server_names(connectors),
        context,
    )[1]


def _langsmith_server_config(
    declared: Mapping[str, object],
) -> RemoteMcpServerConfig:
    server = dict(declared)
    server["transport"] = "http"
    server.pop("headers", None)
    return cast("RemoteMcpServerConfig", server)


def _probe_langsmith_server_access(
    server_name: str,
    url: str,
    headers: Mapping[str, str],
) -> list[PendingCredential]:
    """Return pending credentials from a complete 428 or a partial kit's ``_meta``.

    An incomplete 428 raises. An empty list means the server is fully accessible.
    """
    status, payload = request_agent_auth_json(
        "POST",
        url,
        body={"jsonrpc": "2.0", "id": "mda-auth-probe", "method": "tools/list"},
        headers=headers,
        timeout=AGENT_AUTH_TIMEOUT_SECONDS,
    )
    if status == HTTPStatus.OK:
        return _pending_credentials_from_langsmith_meta(payload, server_name)
    if status != HTTPStatus.PRECONDITION_REQUIRED:
        return []
    credentials = payload.get("credentials")
    if isinstance(credentials, list):
        return _pending_credentials_from_langsmith_gateway_428(credentials, server_name)
    pending = _pending_credential_from_langsmith_428(payload, provider=server_name)
    if pending is not None:
        return [pending]
    verification_url = payload.get("verification_url")
    if isinstance(verification_url, str) and verification_url:
        raise RuntimeError(
            f'LangSmith MCP server "{server_name}" requires authorization. '
            f"Connect at {verification_url}, or in LangSmith Tools, then retry the run"
        )
    raise RuntimeError(
        f'LangSmith MCP server "{server_name}" requires credentials; '
        "connect it in LangSmith Tools before running the agent"
    )


def _pending_credentials_from_langsmith_meta(
    payload: Mapping[str, object],
    server_name: str,
) -> list[PendingCredential]:
    """Map the missing credentials an ALLOW_PARTIAL tool kit lists in ``_meta``.

    The kit's connected tools work without these, so entries without a
    complete link are skipped with a warning instead of failing the run.
    """
    result = payload.get("result")
    meta = result.get("_meta") if isinstance(result, Mapping) else None
    entries = (
        meta.get("com.langchain.langsmith/missingCredentials")
        if isinstance(meta, Mapping)
        else None
    )
    if not isinstance(entries, list):
        return []
    pending: list[PendingCredential] = []
    for entry in entries:
        credential = (
            _pending_credential_from_langsmith_428(entry, provider=server_name)
            if isinstance(entry, Mapping)
            else None
        )
        if credential is None:
            _warn_skipped_meta_credential(server_name, entry)
            continue
        pending.append(credential)
    return pending


def _warn_skipped_meta_credential(server_name: str, entry: object) -> None:
    """Name the skipped entry's provider and missing fields, never their values."""
    server = logger.log_identifier(server_name)
    if not isinstance(entry, Mapping):
        logger.warning(
            'skipped missing credential for LangSmith MCP server "%s": entry is not an object',
            server,
        )
        return
    label = entry.get("display_name") or entry.get("slug")
    provider = logger.log_identifier(label) if isinstance(label, str) and label else "unknown"
    missing = [
        field
        for field in ("slug", "verification_url", "auth_id")
        if not (isinstance(entry.get(field), str) and entry.get(field))
    ]
    logger.warning(
        'skipped missing credential for LangSmith MCP server "%s" (provider=%s): missing %s',
        server,
        provider,
        ", ".join(missing),
    )


def _pending_credentials_from_langsmith_gateway_428(
    credentials: list[object],
    server_name: str,
) -> list[PendingCredential]:
    """Map every entry of a gateway 428 to an interrupt credential.

    Entries without a complete link cannot be completed from an interrupt, so
    any of them raise with every missing name before an interrupt is shown.
    """
    pending: list[PendingCredential] = []
    unresolved: list[str] = []
    for entry in credentials:
        if not isinstance(entry, Mapping):
            unresolved.append(server_name)
            continue
        credential = _pending_credential_from_langsmith_428(entry, provider=server_name)
        if credential is not None:
            pending.append(credential)
            continue
        label = entry.get("display_name") or entry.get("slug")
        unresolved.append(label if isinstance(label, str) and label else server_name)
    if unresolved or not pending:
        names = ", ".join(unresolved) if unresolved else server_name
        raise RuntimeError(
            f'LangSmith MCP server "{server_name}" requires credentials for {names}; '
            "connect them in LangSmith Tools before running the agent"
        )
    return pending


def _pending_credential_from_langsmith_428(
    payload: Mapping[str, object],
    *,
    provider: str,
) -> PendingCredential | None:
    """Build an interrupt credential only when LMT returns the full triple.

    LMT marks API-key servers with ``kind: "secret"``; every other 428 is OAuth.
    ``provider`` is the MCP server key, used for Slack/Studio display labels
    when LMT does not return ``display_name``.
    """
    verification_url = payload.get("verification_url")
    auth_id = payload.get("auth_id")
    slug = payload.get("slug")
    if not (
        isinstance(verification_url, str)
        and verification_url
        and isinstance(auth_id, str)
        and auth_id
        and isinstance(slug, str)
        and slug
    ):
        return None
    display_name = payload.get("display_name")
    return {
        "slug": slug,
        "kind": "secret" if payload.get("kind") == "secret" else "oauth2",
        "connect_url": verification_url,
        "auth_id": auth_id,
        "provider": display_name if isinstance(display_name, str) and display_name else provider,
    }


def _uses_user_connection(server: RemoteMcpServerConfig) -> bool:
    reference = server.get("connection")
    return is_connection_reference(reference) and reference.owner["type"] == "user"


def _is_langsmith_server(server: RemoteMcpServerConfig) -> bool:
    return is_langsmith_managed_tools_path(server["url"])


async def load_eager_mcp_tools(connectors: McpServersDefinition) -> list[BaseTool]:
    """Build public/agent tools from native cached listings, closing discovery connections."""
    servers = {
        name: server
        for name, server in connectors.config["servers"].items()
        if not _uses_user_connection(server) and not _is_langsmith_server(server)
    }
    auth = {}
    authorization: dict[str, object] = {}
    deployment = [
        os.environ.get("LANGSMITH_ENDPOINT"),
        os.environ.get("LANGSMITH_WORKSPACE_ID"),
        os.environ.get("LANGSMITH_HOST_PROJECT_ID"),
    ]
    for name, server in servers.items():
        reference = server.get("connection")
        if reference is None:
            authorization[name] = [deployment, "public"]
            continue
        auth[name] = ManagedAuth(
            server["url"],
            token=partial(connections.resolve_deployment_secret, reference.slug),
            connection_slug=reference.slug,
        )
        authorization[name] = [deployment, reference.slug, reference.owner]
    return await load_tools(
        servers,
        auth=auth,
        authorization=authorization,
        throw_on_load_error=connectors.config.get("throw_on_load_error", True),
    )


def _load_caller_mcp_tools_sync(
    connectors: McpServersDefinition, tokens: Mapping[str, str], context: AgentAuthContext
) -> list[BaseTool]:
    return await_if_needed(load_caller_mcp_tools(connectors, tokens, context))


def origin(url: str) -> tuple[str, str, int | None]:
    parsed = urlsplit(url)
    return (
        parsed.scheme.lower(),
        (parsed.hostname or "").lower(),
        parsed.port or (443 if parsed.scheme == "https" else 80),
    )


def opaque(value: object) -> str:
    """Hash configuration and credential generations; never retain raw secrets in keys."""
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str).encode()).hexdigest()


class _SharedCacheStore(_CacheStoreBase):
    """Serialize FastMCP's memory-backed operations across worker threads."""

    def __init__(self) -> None:
        super().__init__(storage=MemoryStore(max_entries_per_collection=CACHE_MAX_ENTRIES))
        self._lock = threading.Lock()

    async def get(self, key: CacheKey) -> CacheEntry | None:
        with self._lock:
            return await super().get(key)

    async def set(self, key: CacheKey, entry: CacheEntry) -> None:
        with self._lock:
            await super().set(key, entry)

    async def delete(self, key: CacheKey) -> None:
        with self._lock:
            await super().delete(key)

    async def clear(self) -> None:
        with self._lock:
            await super().clear()


_catalog_cache = _SharedCacheStore() if MCPAdapter is not None else None


class ManagedAuth(_AuthBase):
    """Resolve current credentials for each request and restrict them to one origin."""

    def __init__(
        self,
        url: str,
        *,
        token: Callable[[], Awaitable[str]] | None = None,
        headers: Mapping[str, str] | None = None,
        connection_slug: str | None = None,
    ) -> None:
        require_runtime_dependency(MCPAdapter, "langchain[mcp]", "load MCP tools")
        self._origin = origin(url)
        self._token = token
        self._headers = dict(headers or {})
        connection = f'MCP connection "{connection_slug}"' if connection_slug else "MCP connection"
        self._rejected = f"{connection} was rejected by its server; reconnect it and retry"

    async def authorization_partition(self, scope: str) -> str:
        """Keep a refreshed credential from reusing a previous grant's catalog."""
        return opaque([scope, await self._token()]) if self._token is not None else scope

    async def async_auth_flow(
        self, request: httpx2.Request
    ) -> AsyncGenerator[httpx2.Request, httpx2.Response]:
        if origin(str(request.url)) != self._origin:
            raise ValueError("Refusing to send MCP credentials to another origin")
        token = await self._token() if self._token is not None else None
        if token is not None:
            request.headers["Authorization"] = f"Bearer {token}"
        request.headers.update(self._headers)
        response = yield request
        if response.status_code != HTTPStatus.UNAUTHORIZED or self._token is None:
            return
        await response.aread()
        try:
            refreshed = await self._token()
        except Exception as error:
            raise RuntimeError(self._rejected) from error
        if not refreshed or refreshed == token:
            raise RuntimeError(self._rejected)
        request.headers["Authorization"] = f"Bearer {refreshed}"
        response = yield request
        if response.status_code == HTTPStatus.UNAUTHORIZED:
            await response.aclose()
            raise RuntimeError(self._rejected)


def _http_client_factory(
    headers: dict[str, str] | None = None,
    timeout: httpx2.Timeout | None = None,
    auth: httpx2.Auth | None = None,
    follow_redirects: bool = False,  # noqa: ARG001 - native factory signature
) -> httpx2.AsyncClient:
    native = timeout or httpx2.Timeout(HTTP_TIMEOUT_SECONDS)
    budget = httpx2.Timeout(
        **{
            key: value if value is not None else HTTP_TIMEOUT_SECONDS
            for key, value in native.as_dict().items()
        }
    )
    return httpx2.AsyncClient(
        headers=headers,
        auth=auth,
        timeout=budget,
        follow_redirects=False,
        trust_env=False,
    )


async def _client(
    server: RemoteMcpServerConfig, auth: httpx2.Auth | None, partition: str
) -> Client[Any]:
    """Build fresh transport/session state while retaining the shared listing store."""
    if isinstance(auth, ManagedAuth):
        partition = await auth.authorization_partition(partition)
    transport = (
        StreamableHttpTransport(
            server["url"],
            headers=server.get("headers"),
            auth=auth,
            httpx_client_factory=_http_client_factory,
        )
        if server["transport"] == "http"
        else SSETransport(
            server["url"],
            headers=server.get("headers"),
            auth=auth,
            httpx_client_factory=_http_client_factory,
            sse_read_timeout=cast("Any", server.get("sse_read_timeout", 300)),
        )
    )
    return Client(
        transport,
        timeout=server.get("timeout"),
        init_timeout=INIT_TIMEOUT_SECONDS,
        cache=CacheConfig(
            store=require_runtime_dependency(_catalog_cache, "fastmcp", "cache MCP catalogs"),
            target_id=opaque(server),
            partition=partition,
            default_ttl_ms=CACHE_TTL_MS,
            share_public=False,
        ),
    )


async def _discover_tools(
    name: str, client_factory: Callable[[], Awaitable[Client[Any]]]
) -> list[BaseTool]:
    # One-server native groups keep invocation independent of peers.
    adapter_cls = cast(
        "type[NativeMCPAdapter]",
        require_runtime_dependency(MCPAdapter, "langchain[mcp]", "load MCP tools"),
    )
    adapter = adapter_cls(ClientGroup({name: await client_factory()}))
    return await adapter.list_tools(cache_mode="use")


async def load_tools(
    servers: Mapping[str, RemoteMcpServerConfig],
    *,
    auth: Mapping[str, httpx2.Auth],
    authorization: Mapping[str, object],
    throw_on_load_error: bool = True,
) -> list[BaseTool]:
    """Discover detached native tools using one stable, authorization-scoped cache."""
    require_runtime_dependency(MCPAdapter, "langchain[mcp]", "load MCP tools")
    tools: list[BaseTool] = []
    names: set[str] = set()
    for name, server in servers.items():
        client_factory = partial(
            _client, server, auth.get(name), opaque(authorization.get(name, "public"))
        )
        try:
            with build_step(name):
                server_tools = await _discover_tools(name, client_factory)
        except Exception as error:
            if throw_on_load_error:
                raise
            warnings.warn(
                f'MCP server "{name}" failed to load and was skipped: {error}',
                RuntimeWarning,
                stacklevel=2,
            )
            continue
        # Qualify authored raw selections for this native group; never parse a route.
        selection = {
            field: [f"{name}_{raw}" for raw in server[field]]
            for field in ("include_tools", "exclude_tools")
            if field in server
        }
        selected = select_tools(f'MCP server "{name}"', selection, server_tools)
        for tool in selected:
            if tool.name in names:
                raise ValueError(
                    f"MCP tool name collision: {tool.name!r}; rename the server or upstream tool"
                )
            names.add(tool.name)
        tools.extend(selected)
    return tools
