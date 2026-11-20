"""Managed MCP loading and the existing LangSmith authorization flow."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import threading
import warnings
import weakref
from collections.abc import AsyncGenerator, Awaitable, Callable, Mapping, Sequence
from functools import partial
from http import HTTPStatus
from importlib.util import find_spec
from typing import TYPE_CHECKING, Any, cast
from urllib.parse import urlsplit

from managed_deepagents._agent_access_token import amanaged_tools_token_headers
from managed_deepagents._agent_auth import AgentAuth
from managed_deepagents._connection_authorization import (
    PendingCredential,
    resolve_user_connection_grants,
)
from managed_deepagents._connections import (
    ConnectionReference,
    connections,
    is_connection_reference,
)
from managed_deepagents._connectors.mcp.instructions import with_mcp_server_instructions
from managed_deepagents._connectors.tool_selection import select_tools
from managed_deepagents._credential_gate import CredentialConsumer
from managed_deepagents._graph_build_timing import build_step
from managed_deepagents._mcp_problem import McpProblemError, is_problem, problem_detail
from managed_deepagents._memory.async_util import await_if_needed
from managed_deepagents._memory.deps import optional_runtime_symbols, require_runtime_dependency
from managed_deepagents._tool_gateway import auth as tool_gateway
from managed_deepagents._tool_gateway import connect_first
from managed_deepagents._tool_gateway.cards import answer_tool_gateway_prompt

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
    from fastmcp.client.group import ClientGroup, ToolRoute
    from fastmcp.client.transports import SSETransport, StreamableHttpTransport
    from key_value.aio.stores.memory import MemoryStore
    from mcp.client.caching import CacheConfig, CacheEntry, CacheKey, CacheMode
    from mcp_types import Tool

    _AuthBase = httpx2.Auth
    _CacheStoreBase = KeyValueResponseCacheStore
    _ClientGroupBase = ClientGroup
else:
    _AuthBase = object
    _CacheStoreBase = object
    _ClientGroupBase = object

INIT_TIMEOUT_SECONDS = 10.0
HTTP_TIMEOUT_SECONDS = 30.0
CACHE_TTL_MS = 5 * 60_000
CACHE_MAX_ENTRIES = 1024


def load_managed_mcp_tools(connectors: McpServersDefinition) -> list[BaseTool]:
    """Build eager tools synchronously, reusing FastMCP's cached listings."""
    return await_if_needed(load_eager_mcp_tools(connectors))


def mcp_credential_consumers(connectors: Sequence[object] | None) -> list[CredentialConsumer]:
    """The credential gate's view of the servers whose credentials depend on the run.

    User-connected servers need a person. Servers with LangSmith auth load for
    every run: as the run's person, or as the agent when there is none.
    """
    out: list[CredentialConsumer] = []
    for connector in connectors or []:
        if getattr(connector, "kind", None) != "mcp_servers":
            continue
        definition = cast("McpServersDefinition", connector)
        references = _user_connection_references(definition)
        if references:
            out.append(
                CredentialConsumer(
                    declaration=definition,
                    credentials=references,
                    load=partial(_load_caller_mcp_tools_sync, definition),
                    cache_tools=False,
                )
            )
        if any(map(tool_gateway.uses_langsmith_auth, definition.config["servers"].values())):
            out.append(
                CredentialConsumer(
                    declaration=definition,
                    credentials=(),
                    load=partial(_load_caller_mcp_tools_sync, definition, langsmith=True),
                    cache_tools=False,
                    requires_identity=False,
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
    *,
    langsmith: bool = False,
) -> list[BaseTool]:
    """Load the user-connected servers for one person, or with ``langsmith`` the
    servers with LangSmith auth, for the run's person or the agent."""
    caller = [context.base_url, context.workspace_id, context.agent_id, context.principal_id]
    servers: dict[str, RemoteMcpServerConfig] = {}
    auth = {}
    authorization: dict[str, object] = {}
    for name, server in connectors.config["servers"].items():
        if langsmith and tool_gateway.uses_langsmith_auth(server):
            if origin(server["url"]) != origin(context.base_url):
                tool_gateway.refuse_other_origin(name, server, context.base_url)
                continue
            profile = tool_gateway.langsmith_profile(server)
            headers = tool_gateway.langsmith_headers(context, profile)
            auth[name] = ManagedAuth(
                server["url"],
                headers=headers,
                agent_access_token_headers=partial(amanaged_tools_token_headers, context.api_key),
                managed_tools_server=name,
            )
            authorization[name] = [caller, profile, context.api_key]
        elif not langsmith and _uses_user_connection(server) and context.principal_id:
            reference = server["connection"]
            if not access_tokens.get(reference.slug):
                raise RuntimeError(f'MCP user connection "{reference.slug}" has no access token')
            auth[name] = ManagedAuth(
                server["url"],
                token=partial(_fresh_user_token, context, reference.slug),
                connection_slug=reference.slug,
            )
            authorization[name] = [caller, reference.slug, reference.owner]
        else:
            continue
        servers[name] = server
    if not servers:
        return []
    pending: list[PendingCredential] = []
    tools = await load_tools(
        servers,
        auth=auth,
        authorization=authorization,
        throw_on_load_error=connectors.config.get("throw_on_load_error", True),
        pending=pending if langsmith else None,
    )
    if pending and context.principal_id:
        connect_first.ask_before_the_run(pending, tools)
    return tools


def _uses_user_connection(server: RemoteMcpServerConfig) -> bool:
    reference = server.get("connection")
    return is_connection_reference(reference) and reference.owner["type"] == "user"


async def load_eager_mcp_tools(connectors: McpServersDefinition) -> list[BaseTool]:
    """Build public/agent tools from native cached listings, closing discovery connections."""
    servers = {
        name: server
        for name, server in connectors.config["servers"].items()
        if not _uses_user_connection(server) and not tool_gateway.uses_langsmith_auth(server)
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
    connectors: McpServersDefinition,
    tokens: Mapping[str, str],
    context: AgentAuthContext,
    *,
    langsmith: bool = False,
) -> list[BaseTool]:
    return await_if_needed(load_caller_mcp_tools(connectors, tokens, context, langsmith=langsmith))


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


class _CatalogGroup(_ClientGroupBase):
    """Load each new member session's tool state from the cached catalog before a call.

    The MCP session validates a tool result against output schemas it learned
    from a listing. A session that never listed fetches the catalog again, past
    the response cache, on every call.
    """

    def __init__(self, clients: Mapping[str, Client[Any]]) -> None:
        super().__init__(clients)
        self._listed: weakref.WeakSet[object] = weakref.WeakSet()

    async def list_tools(self, *, cache_mode: CacheMode = "refresh") -> list[Tool]:
        tools = await super().list_tools(cache_mode=cache_mode)
        self._listed.update(client.session for client in self.clients.values())
        return tools

    async def resolve_tool(self, name: str) -> ToolRoute:
        route = await super().resolve_tool(name)
        session = route.client.session
        if session not in self._listed:
            await route.client.list_tools(cache_mode="use")
            self._listed.add(session)
        return route


class ManagedAuth(_AuthBase):
    """Resolve current credentials for each request and restrict them to one origin."""

    def __init__(
        self,
        url: str,
        *,
        token: Callable[[], Awaitable[str]] | None = None,
        headers: Mapping[str, str] | None = None,
        agent_access_token_headers: Callable[[str], Awaitable[Mapping[str, str]]] | None = None,
        connection_slug: str | None = None,
        managed_tools_server: str | None = None,
    ) -> None:
        require_runtime_dependency(MCPAdapter, "langchain[mcp]", "load MCP tools")
        self._origin = origin(url)
        self._token = token
        self._headers = dict(headers or {})
        self._agent_access_token_headers = agent_access_token_headers
        #: Set for a LangSmith tool kit whose calls may ask the caller to connect.
        self._managed_tools_server = managed_tools_server
        connection = f'MCP connection "{connection_slug}"' if connection_slug else "MCP connection"
        self._rejected = f"{connection} was rejected by its server; reconnect it and retry"

    async def authorization_partition(self, scope: str) -> str:
        """Keep a refreshed credential from reusing a previous grant's catalog."""
        return opaque([scope, await self._token()]) if self._token is not None else scope

    def _set_headers(self, request: httpx2.Request, token: str | None) -> None:
        if token is not None:
            request.headers["Authorization"] = f"Bearer {token}"
        request.headers.update(self._headers)

    async def async_auth_flow(
        self, request: httpx2.Request
    ) -> AsyncGenerator[httpx2.Request, httpx2.Response]:
        if origin(str(request.url)) != self._origin:
            raise ValueError("Refusing to send MCP credentials to another origin")
        token = await self._token() if self._token is not None else None
        self._set_headers(request, token)
        if self._agent_access_token_headers is not None:
            request.headers.update(await self._agent_access_token_headers(str(request.url)))
        response = yield request
        if (
            response.status_code == HTTPStatus.PRECONDITION_REQUIRED
            and self._managed_tools_server is not None
        ):
            await response.aread()
            raise connect_first.connect_request(self._managed_tools_server, response)
        if (
            request.method == "POST"
            and response.status_code != HTTPStatus.UNAUTHORIZED
            and is_problem(response)
        ):
            # The MCP client reports any HTTP error as a generic failure, so keep
            # the server's own explanation (over a limit, refused, admin must connect).
            await response.aread()
            raise McpProblemError(problem_detail(response), response.status_code)
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
    name: str,
    client_factory: Callable[[], Awaitable[Client[Any]]],
    *,
    pending: list[PendingCredential] | None = None,
    include_instructions: bool = True,
) -> list[BaseTool]:
    """List one server's tools as native LangChain tools.

    Tool Gateway clients answer prompts through MDA's callback, which shows them
    to the run's person with the connect and approval cards every surface
    renders. Other servers keep the adapter's ``mcp_elicitation`` interrupt.
    """
    # One-server native groups keep invocation independent of peers.
    adapter_cls = cast(
        "type[NativeMCPAdapter]",
        require_runtime_dependency(MCPAdapter, "langchain[mcp]", "load MCP tools"),
    )
    client = await client_factory()
    group = _CatalogGroup({name: client})
    if pending is not None:
        # MDA answers Tool Gateway's prompts itself, as the cards every surface renders.
        client.set_elicitation_callback(answer_tool_gateway_prompt)
    else:
        # The adapter arms its clients to answer elicitation with interrupts.
        armed = adapter_cls(group).client
        if isinstance(armed, ClientGroup):
            group = _CatalogGroup(armed.clients)
    # Loaded with MCPAdapter above, which this path already required.
    from langchain.mcp import as_langchain_tool as convert  # noqa: PLC0415

    async with group:
        member = group.clients[name]
        tools = [await convert(tool, group) for tool in await group.list_tools(cache_mode="use")]
        if include_instructions:
            tools = with_mcp_server_instructions(name, member.instructions, tools)
        return tools


async def load_tools(
    servers: Mapping[str, RemoteMcpServerConfig],
    *,
    auth: Mapping[str, httpx2.Auth],
    authorization: Mapping[str, object],
    throw_on_load_error: bool = True,
    pending: list[PendingCredential] | None = None,
) -> list[BaseTool]:
    """Discover detached native tools using one stable, authorization-scoped cache.

    With ``pending`` set, every server uses LangSmith auth: the connections
    each one needs before listing tools are appended to ``pending``, and a server
    that needs a connection before it lists tools is skipped.
    """
    require_runtime_dependency(MCPAdapter, "langchain[mcp]", "load MCP tools")
    tools: list[BaseTool] = []
    names: set[str] = set()
    for name, server in servers.items():
        client_factory = partial(
            _client, server, auth.get(name), opaque(authorization.get(name, "public"))
        )
        try:
            with build_step(name):
                server_tools = await _discover_tools(
                    name,
                    client_factory,
                    pending=pending,
                    include_instructions=server.get("include_instructions") is not False,
                )
        except Exception as error:
            if pending is not None and connect_first.needs_a_connection(name, error, pending):
                continue
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
