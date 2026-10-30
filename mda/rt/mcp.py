"""The run's tools from the MCP servers whose credential is an MDA connection.

The backend serves its MCP servers' tools as operations, with credentials it keeps.
A server whose credential is an MDA connection (``McpServerSpec``) it cannot reach,
so the run connects to it here: once per run it lists the server's tools, keeping
those the catalog allows under the names the backend gives them. Each call reads the
credential again (``managed_deepagents.connections``), as the deployment or as the
person the run acts for, and sends it only to the server's own HTTPS origin, at a
public address, as the backend reaches its own.
"""

import asyncio
import ipaddress
import logging
from collections.abc import AsyncGenerator, Collection, Mapping, Sequence
from functools import partial
from typing import Final
from urllib.parse import urlsplit, urlunsplit

import httpx2
from fastmcp import Client
from fastmcp.client.transports import SSETransport, StreamableHttpTransport
from langchain.mcp import as_langchain_tool
from langchain.tools import ToolRuntime
from langchain_core.messages import ToolMessage
from langchain_core.tools import BaseTool, StructuredTool, ToolException
from langgraph.errors import GraphBubbleUp
from langgraph.types import Command
from managed_deepagents import (
    ConnectionNotFoundError,
    CredentialAuthorizationRequiredError,
    CredentialStoreError,
    connections,
)
from mcp.types import Tool
from pydantic import JsonValue
from runtime_api.catalog import McpServerSpec

from rt.backend import OpenSweBackend
from rt.tools import called

logger = logging.getLogger(__name__)

_TIMEOUT_SECONDS: Final = 30
# The argument each tool's call gets its ToolRuntime in, which no server tool may take.
_RUNTIME: Final = "runtime"


class McpServerError(ValueError):
    """A request the run refused to send to an MCP server; its message is ours."""


def _origin(url: str) -> tuple[str, str | None, int]:
    parsed = urlsplit(url)
    return parsed.scheme, parsed.hostname, parsed.port or 443


async def _public_addresses(host: str) -> list[str]:
    infos = await asyncio.get_running_loop().getaddrinfo(host, None)
    addresses = list(dict.fromkeys(str(info[4][0]) for info in infos))
    if not addresses:
        raise McpServerError("MCP server has no addresses")
    for address in addresses:
        try:
            ip = ipaddress.ip_address(address)
        except ValueError:
            raise McpServerError("MCP server must resolve only to public addresses") from None
        if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
            ip = ip.ipv4_mapped
        if not ip.is_global:
            raise McpServerError("MCP server must resolve only to public addresses")
    return addresses


class _PublicOrigin(httpx2.AsyncBaseTransport):
    """Reaches only the server's own HTTPS origin, at an address checked as public.

    The request goes to the checked address, so a second DNS answer cannot point it,
    and the credential it carries, at a private host.
    """

    def __init__(self, url: str) -> None:
        self._origin = _origin(url)
        self._transport = httpx2.AsyncHTTPTransport(trust_env=False)

    async def handle_async_request(self, request: httpx2.Request) -> httpx2.Response:
        url = str(request.url)
        parsed = urlsplit(url)
        if parsed.scheme != "https" or _origin(url) != self._origin or not parsed.hostname:
            raise McpServerError("MCP requests must stay on the server's HTTPS origin")
        headers = request.headers.copy()
        headers["Host"] = parsed.netloc
        addresses = await _public_addresses(parsed.hostname)
        for index, address in enumerate(addresses):
            host = f"[{address}]" if ":" in address else address
            pinned = httpx2.Request(
                request.method,
                urlunsplit(parsed._replace(netloc=f"{host}:{parsed.port or 443}")),
                headers=headers,
                stream=request.stream,
                extensions={**request.extensions, "sni_hostname": parsed.hostname},
            )
            try:
                return await self._transport.handle_async_request(pinned)
            except (httpx2.ConnectError, httpx2.ConnectTimeout) as exc:
                if index == len(addresses) - 1:
                    raise
                logger.info(
                    "Trying an MCP server's next address",
                    extra={"mcp_host": parsed.hostname, "error_type": type(exc).__name__},
                )
        raise httpx2.ConnectError("MCP server has no public addresses")

    async def aclose(self) -> None:
        await self._transport.aclose()


def _http_client(
    url: str,
    headers: dict[str, str] | None = None,
    timeout: httpx2.Timeout | None = None,
    auth: httpx2.Auth | None = None,
    follow_redirects: bool = False,  # noqa: ARG001
) -> httpx2.AsyncClient:
    """The MCP client's HTTP client; FastMCP asks for redirects, but every request stays on
    the server's origin."""
    return httpx2.AsyncClient(
        headers=headers,
        timeout=timeout or httpx2.Timeout(_TIMEOUT_SECONDS),
        auth=auth,
        transport=_PublicOrigin(url),
        follow_redirects=False,
        trust_env=False,
    )


class _Credential(httpx2.Auth):
    """The connection's credential, in the header the server takes it in."""

    def __init__(self, header: str | None, credential: str) -> None:
        self._header = header or "Authorization"
        self._value = credential if header else f"Bearer {credential}"

    async def async_auth_flow(
        self, request: httpx2.Request
    ) -> AsyncGenerator[httpx2.Request, httpx2.Response]:
        request.headers[self._header] = self._value
        yield request


class _McpTool(StructuredTool):
    """Hands its coroutine every argument, those named as LangChain's own included.

    ``StructuredTool`` keeps ``config`` and ``run_manager`` for itself, so a server
    tool that takes an argument of either name would never get it.
    """

    def _run(self, /, *args: object, **kwargs: object) -> object:
        raise NotImplementedError("MCP tools run async")

    async def _arun(self, /, *args: object, **kwargs: object) -> object:
        if self.coroutine is None:
            raise ToolException("MCP tool has no async implementation")
        return await self.coroutine(*args, **kwargs)


class McpServer:
    """One of the run's MCP servers, as the run reaches it with its connection."""

    def __init__(self, spec: McpServerSpec) -> None:
        self.spec = spec

    async def credential(self) -> str:
        """The connection's credential now; a person who has not granted it is asked to."""
        connection = self.spec.connection
        if not connection.for_mcp_server:
            # The backend names none other; this keeps a credential Open SWE or a channel
            # reads for itself from a URL an admin or a person picked, if it ever did.
            raise McpServerError("not a connection made for MCP servers")
        return await connections.get(connection.slug, {"type": connection.owner})

    def _client(self, credential: str) -> Client[SSETransport | StreamableHttpTransport]:
        auth = _Credential(self.spec.header, credential)
        factory = partial(_http_client, self.spec.url)
        transport: SSETransport | StreamableHttpTransport = (
            SSETransport(
                self.spec.url,
                auth=auth,
                sse_read_timeout=_TIMEOUT_SECONDS,
                httpx_client_factory=factory,
            )
            if self.spec.transport == "sse"
            else StreamableHttpTransport(self.spec.url, auth=auth, httpx_client_factory=factory)
        )
        return Client(transport, timeout=_TIMEOUT_SECONDS)

    async def allowed(self, credential: str) -> list[Tool]:
        """The server's tools the catalog lets the run call, as the server lists them; none
        when it does not answer."""
        try:
            async with asyncio.timeout(_TIMEOUT_SECONDS), self._client(credential) as client:
                listed = await client.list_tools()
        except Exception as exc:
            # What the request raised can quote what it carried, the credential included.
            logger.warning(
                "Leaving out an MCP server that did not list its tools",
                extra={"mcp_name": self.spec.name, "error_type": type(exc).__name__},
            )
            return []
        allowed: list[Tool] = []
        for tool in listed:
            if tool.name not in self.spec.tools:
                continue
            if _RUNTIME in tool.input_schema.get("properties", {}):
                logger.warning(
                    "Leaving out an MCP tool with an argument named runtime",
                    extra={"mcp_name": self.spec.name, "tool_name": tool.name},
                )
                continue
            allowed.append(tool)
        return allowed

    def unavailable(self, error: Exception) -> str:
        """Why the server's connection gave no credential, naming no secret."""
        slug = self.spec.connection.slug
        if isinstance(error, ConnectionNotFoundError):
            return (
                f"MCP server {self.spec.name}'s connection {slug} is not set; create it with "
                f"`mda connections create {slug}` from Open SWE's MDA project"
            )
        if isinstance(error, CredentialAuthorizationRequiredError):
            return f"MCP server {self.spec.name} needs {slug}, which was not connected"
        if isinstance(error, CredentialStoreError | McpServerError):
            # Fixed text, such as a run that acts for no person; never a response body.
            return f"MCP server {self.spec.name} cannot read {slug}: {error}"
        return f"MCP server {self.spec.name} cannot read {slug} ({type(error).__name__})"

    async def call(
        self, definition: Tool, arguments: Mapping[str, JsonValue], tool_call_id: str
    ) -> ToolMessage:
        name = self.spec.tools[definition.name]

        def failed(content: str) -> ToolMessage:
            return ToolMessage(
                content=content, tool_call_id=tool_call_id, name=name, status="error"
            )

        try:
            credential = await self.credential()
        except GraphBubbleUp:
            raise
        except Exception as exc:
            return failed(f"Error: {self.unavailable(exc)}")
        try:
            tool = await as_langchain_tool(definition, self._client(credential))
            if not isinstance(tool, StructuredTool) or tool.coroutine is None:
                raise ToolException("MCP tool has no async implementation")
            # Called directly so arguments LangChain reserves for itself reach the server.
            content, artifact = await asyncio.wait_for(
                tool.coroutine(**arguments), timeout=_TIMEOUT_SECONDS
            )
        except ToolException as exc:
            # The server ran the tool and says it failed.
            return failed(str(exc))
        except Exception as exc:
            # What the request raised can quote what it carried, the credential included.
            logger.warning(
                "MCP call failed",
                extra={"mcp_name": self.spec.name, "error_type": type(exc).__name__},
            )
            return failed("MCP call failed; check its connection and credentials")
        return ToolMessage(content=content, artifact=artifact, tool_call_id=tool_call_id, name=name)

    def tool(
        self, definition: Tool, backend: OpenSweBackend, *, cites_evidence: bool
    ) -> StructuredTool:
        """``definition`` as the model sees it: the backend's name and description for it."""
        name = self.spec.tools[definition.name]

        async def call(runtime: ToolRuntime, **arguments: JsonValue) -> ToolMessage | Command:
            return await called(
                backend,
                name,
                arguments,
                runtime,
                partial(self.call, definition, arguments),
                cites_evidence=cites_evidence,
            )

        description = definition.description or definition.name
        if self.spec.provenance:
            description = f"{description}\n\n{self.spec.provenance}"
        return _McpTool.from_function(
            coroutine=call, name=name, description=description, args_schema=definition.input_schema
        )


class McpTools:
    """The tools of a run's MCP servers, loaded once for the run, as the model calls them."""

    def __init__(self, tools: Sequence[BaseTool] = ()) -> None:
        self.tools = list(tools)

    @classmethod
    async def load(
        cls,
        servers: Sequence[McpServerSpec],
        backend: OpenSweBackend,
        *,
        taken: Collection[str],
        cites_evidence: bool,
    ) -> McpTools:
        """The tools of ``servers`` the run can reach now, leaving out names in ``taken``.

        A server the run cannot read the connection of, or that does not answer, offers
        none. Credentials are read one server at a time, so a person who has not granted
        one is asked for it alone, before the rest are listed together.
        """
        reachable: list[tuple[McpServer, str]] = []
        for spec in servers:
            server = McpServer(spec)
            try:
                reachable.append((server, await server.credential()))
            except GraphBubbleUp:
                raise
            except Exception as exc:
                logger.warning(
                    "Leaving out an MCP server the run cannot read the connection of",
                    extra={"mcp_name": spec.name, "reason": server.unavailable(exc)},
                )
        listed = await asyncio.gather(
            *(server.allowed(credential) for server, credential in reachable)
        )
        tools: list[BaseTool] = []
        names = set(taken)
        for (server, _credential), definitions in zip(reachable, listed, strict=True):
            for definition in definitions:
                tool = server.tool(definition, backend, cites_evidence=cites_evidence)
                if tool.name in names:
                    logger.warning(
                        "Leaving out an MCP tool whose name the run already has",
                        extra={"mcp_name": server.spec.name, "tool_name": tool.name},
                    )
                    continue
                names.add(tool.name)
                tools.append(tool)
        return cls(tools)
