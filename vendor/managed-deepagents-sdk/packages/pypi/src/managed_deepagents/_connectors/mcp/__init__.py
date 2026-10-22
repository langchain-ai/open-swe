"""Remote MCP declarations, validated without requiring the native MCP runtime.

``RemoteMcpServerConfig`` is the union of Streamable HTTP and legacy HTTP+SSE
configurations. Tools use native ``{server}_{tool}`` names; selection uses raw
upstream names. Native policies (auth, protocol, HTTP factories, caching and TLS)
are managed by the runtime rather than accepted as declaration fields.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from typing import TYPE_CHECKING, Annotated, Any, Literal, NotRequired
from urllib.parse import urlsplit

from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    field_validator,
    model_validator,
)
from typing_extensions import TypedDict, Unpack

from managed_deepagents._connections import (
    ConnectionReference,
    is_connection_reference,
)
from managed_deepagents._connector import Connector
from managed_deepagents._connectors.mcp.langsmith_url import is_langsmith_managed_tools_path
from managed_deepagents._connectors.mcp.load import load_managed_mcp_tools
from managed_deepagents._extension import define_extension

if TYPE_CHECKING:
    from langchain_core.tools import BaseTool

    from managed_deepagents._channels.manifest import ChannelManifest


class _McpToolSelectionConfig(TypedDict, total=False):
    """Select raw upstream tool names independently for each server."""

    include_tools: list[str]
    """Optional raw upstream names to include, before native group prefixing.

    Omission includes all tools; an empty list selects none. Duplicates and
    overlap with ``exclude_tools`` fail declaration validation; names absent
    from the discovered catalog fail loading.
    """

    exclude_tools: list[str]
    """Optional raw upstream names to exclude, before native group prefixing.

    Omission or an empty list excludes none. Duplicates and overlap with
    ``include_tools`` fail declaration validation; names absent from the
    discovered catalog fail loading.
    """


class _RemoteMcpServerBaseConfig(_McpToolSelectionConfig):
    """Common remote-server fields, inheriting raw-name tool selection.

    Local and subprocess targets are unsupported.
    """

    url: str
    """Required HTTP(S) endpoint compatible with the selected transport.

    Embedded credentials are rejected.
    """

    headers: NotRequired[dict[str, str]]
    """Optional authored request headers; omission supplies none.

    LangSmith managed-tools endpoints reject authored headers. A managed
    ``connection`` rejects a competing Authorization header.
    """

    connection: NotRequired[ConnectionReference]
    """Optional managed reference from ``connections.get``.

    Agent ownership uses deployment credentials; user ownership checks the
    verified caller's grants through LangSmith's managed authorization flow.
    An explicit owner ID is an assertion, never a selector. Omission uses public
    access or runtime-owned authentication for LangSmith managed tools, which
    reject authored connection overrides.
    """

    timeout: NotRequired[float]
    """Optional positive finite seconds per native MCP request.

    Applies to discovery and tool calls. Omission leaves the request deadline
    unset; finite initialization/HTTP limits still apply. Multiple elicitation
    rounds are separate requests, not one total invocation deadline. Boolean,
    zero, negative and nonfinite values are rejected.
    """


class HttpMcpServerConfig(_RemoteMcpServerBaseConfig):
    """Streamable HTTP declaration, inheriting common server and selection fields."""

    transport: Literal["http"]
    """Required ``"http"`` for a server-supported Streamable HTTP endpoint."""


class SseMcpServerConfig(_RemoteMcpServerBaseConfig):
    """Deprecated HTTP+SSE declaration, inheriting common server and selection fields."""

    transport: Literal["sse"]
    """Required ``"sse"`` for a legacy HTTP+SSE-compatible endpoint.

    Migrate to Streamable HTTP only when the server supplies a compatible
    endpoint. LangSmith managed tools require ``"http"`` instead.
    """

    sse_read_timeout: NotRequired[float]
    """Optional positive finite seconds between legacy SSE events; default 300.

    Separate from per-request and initialization limits. Boolean, zero, negative
    and nonfinite values are rejected.
    """


RemoteMcpServerConfig = HttpMcpServerConfig | SseMcpServerConfig


class McpServersConfig(TypedDict, closed=True):
    """Closed configuration accepted by ``define_mcp``."""

    servers: dict[str, RemoteMcpServerConfig]
    """Required nonempty logical-name map to HTTP/SSE declarations.

    Values are ``HttpMcpServerConfig`` or ``SseMcpServerConfig``. Native tool
    names are ``{server}_{tool}``; ambiguous names fail discovery.
    """

    throw_on_load_error: NotRequired[bool]
    """Optional discovery policy, default True.

    False warns and omits servers whose native discovery fails, retrying next
    preparation. Pre-run authorization requirements and invalid selections
    always surface.
    """

    on_connection_error: NotRequired[Literal["throw"]]
    """Optional invocation policy, default and only value ``"throw"`` in 0.9.

    Invocation failures propagate, including connection entry, execution and
    teardown.
    """


__all__ = [
    "HttpMcpServerConfig",
    "McpServersConfig",
    "McpServersDefinition",
    "RemoteMcpServerConfig",
    "SseMcpServerConfig",
    "define_mcp",
]

_REMOTE_TRANSPORTS = ("http", "sse")


class McpServersDefinition(Connector):
    """The MCP connector returned by ``define_mcp(...)``.

    A branded managed :class:`Connector` whose ``tools`` hook loads the declared
    MCP tools at runtime; MDA's generated entry hands it to the managed runtime,
    which appends those tools to the agent's tools.
    """

    kind = "mcp_servers"

    config: McpServersConfig
    """Validated configuration with a nonempty server map and managed policies.

    Native clients manage connections during discovery and each asynchronous
    tool invocation; catalogs are cached per authorization scope.
    """

    def __init__(self, **config: Unpack[McpServersConfig]) -> None:
        self.config = config

    def tools(self, ctx: Mapping[str, object] | None = None) -> Sequence[BaseTool]:  # noqa: ARG002
        """Load tools whose native clients connect for each asynchronous invocation."""
        return load_managed_mcp_tools(self)

    def requirements(self, name: str) -> ChannelManifest:
        """Describe user and agent connections that deploy must validate."""
        user_slugs: set[str] = set()
        agent_slugs: set[str] = set()
        for server in self.config["servers"].values():
            connection = server.get("connection") if isinstance(server, dict) else None
            if not is_connection_reference(connection):
                continue
            if connection.owner["type"] == "user":
                user_slugs.add(connection.slug)
            else:
                agent_slugs.add(connection.slug)
        return {
            "name": name,
            "provider": "mcp",
            "autoReply": False,
            "requiredPermissions": [],
            "requiredEnv": [],
            "userConnections": sorted(user_slugs),
            "agentConnections": sorted(agent_slugs),
            "userCredentials": sorted(user_slugs),
            "needsDeveloperPrincipal": any(
                is_langsmith_managed_tools_path(server.get("url"))
                for server in self.config["servers"].values()
            ),
            "providerConfig": {},
        }

    def __repr__(self) -> str:  # pragma: no cover - debug helper
        servers = ", ".join(sorted(self.config.get("servers", {})))
        return f"McpServersDefinition(servers=[{servers}])"


#: Stdio servers are out of scope; the message points at the alternatives.
_STDIO_REJECTION = (
    "uses a stdio transport, which is not supported. Managed Deep Agents "
    "support remote MCP servers over HTTP/SSE only; expose the server over "
    "HTTP or write a normal authored tool instead."
)

_REMOVED_FIELDS = {
    "credential": "credential was removed in 0.9; use connection instead",
    "mcp_servers": "mcp_servers was removed in 0.9; use servers instead",
    "default_tool_timeout": (
        "default_tool_timeout was removed in 0.9; "
        "use timeout for individual MCP requests, rather than a whole-tool deadline"
    ),
    "prefix_tool_name_with_server_name": (
        "prefix_tool_name_with_server_name was removed in 0.9; native names are {server}_{tool}"
    ),
    "automatic_sse_fallback": (
        "automatic_sse_fallback was removed in 0.9; "
        "choose an explicit transport and compatible endpoint"
    ),
    "reconnect": "reconnect was removed in 0.9; automatic tool-call retries are unsupported",
    "use_standard_content_blocks": (
        "use_standard_content_blocks was removed in 0.9; "
        "standard content conversion is unconditional"
    ),
}


def _reject_removed(data: Mapping[str, object]) -> None:
    for key, message in _REMOVED_FIELDS.items():
        if key in data:
            raise ValueError(message)


_LANGSMITH_MANAGED_CONFLICT = "a LangSmith managed-tools URL cannot declare headers or connection"
_LANGSMITH_MANAGED_TRANSPORT = 'a LangSmith managed-tools URL must declare transport "http"'


def _non_empty_tool_name(name: str) -> str:
    if not name.strip():
        raise ValueError("must be a non-empty tool name")
    return name


#: A raw MCP tool name, before the managed ``{server}_`` prefix is applied.
_ToolName = Annotated[str, AfterValidator(_non_empty_tool_name)]


class _RemoteMcpServer(BaseModel):
    """A single remote MCP server."""

    model_config = ConfigDict(
        extra="forbid",
        validate_default=True,
        arbitrary_types_allowed=True,
        hide_input_in_errors=True,
    )

    transport: str = ""
    url: str = ""
    headers: dict[str, str] | None = None
    timeout: float | None = Field(default=None, validate_default=False)
    sse_read_timeout: float | None = Field(default=None, validate_default=False)
    include_tools: list[_ToolName] | None = None
    exclude_tools: list[_ToolName] | None = None
    connection: ConnectionReference | None = None

    @field_validator("connection")
    @classmethod
    def _valid_connection(
        cls, connection: ConnectionReference | None
    ) -> ConnectionReference | None:
        if connection is not None and not is_connection_reference(connection):
            raise ValueError("must be a connections.get(slug, owner) reference")
        return connection

    @field_validator("timeout", "sse_read_timeout", mode="before")
    @classmethod
    def _positive_timeout(cls, timeout: object) -> object:
        if (
            isinstance(timeout, bool)
            or not isinstance(timeout, int | float)
            or not math.isfinite(timeout)
            or timeout <= 0
        ):
            raise ValueError("must be positive finite seconds (not bool)")
        return timeout

    @model_validator(mode="before")
    @classmethod
    def _closed_fields(cls, server: Any) -> Any:
        if isinstance(server, Mapping):
            if server.get("transport") == "stdio" or "command" in server:
                raise ValueError(_STDIO_REJECTION)
            _reject_removed(server)
            if server.get("transport") == "http" and "sse_read_timeout" in server:
                raise ValueError("sse_read_timeout is only supported for transport sse")
        return server

    @field_validator("transport", mode="before")
    @classmethod
    def _remote_transport(cls, transport: object) -> object:
        if transport not in _REMOTE_TRANSPORTS:
            raise ValueError('must declare a remote transport ("http" or "sse")')
        return transport

    @field_validator("url")
    @classmethod
    def _non_empty_url(cls, url: str) -> str:
        if not url.strip():
            raise ValueError("must be a non-empty URL")
        parsed = urlsplit(url)
        try:
            _ = parsed.port
        except ValueError:
            raise ValueError("must be an HTTP(S) URL with a valid port") from None
        if (
            any(character.isspace() for character in url)
            or parsed.scheme not in ("http", "https")
            or not parsed.hostname
            or parsed.username
            or parsed.password
        ):
            raise ValueError("must be an HTTP(S) URL without embedded credentials")
        return url

    @field_validator("include_tools", "exclude_tools")
    @classmethod
    def _no_duplicate_tool_names(cls, names: list[str] | None) -> list[str] | None:
        seen: set[str] = set()
        for name in names or []:
            if name in seen:
                raise ValueError(f'contains duplicate tool name "{name}"')
            seen.add(name)
        return names

    @model_validator(mode="after")
    def _no_overlapping_selection(self) -> _RemoteMcpServer:
        if is_langsmith_managed_tools_path(self.url):
            if self.transport != "http":
                raise ValueError(_LANGSMITH_MANAGED_TRANSPORT)
            if self.headers is not None or self.connection is not None:
                raise ValueError(_LANGSMITH_MANAGED_CONFLICT)
        if self.connection is not None and any(
            key.lower() == "authorization" for key in self.headers or {}
        ):
            raise ValueError("connection cannot declare a Authorization header")
        overlap = sorted(set(self.include_tools or []) & set(self.exclude_tools or []))
        if overlap:
            raise ValueError(
                f"selects tool(s) in both `include_tools` and `exclude_tools`: {', '.join(overlap)}"
            )
        return self


class _McpServersArgs(BaseModel):
    """The MCP connector's arguments.

    Shape, types, and tool-name lists are the schema's job; the rules that need
    more than one field — a stdio server, a tool selected and deselected at once
    — are validators with hand-written messages matching the npm runtime.
    """

    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)

    servers: dict[str, _RemoteMcpServer]
    throw_on_load_error: StrictBool = True
    on_connection_error: str = "throw"

    @model_validator(mode="before")
    @classmethod
    def _closed_fields(cls, data: Any) -> Any:
        if isinstance(data, Mapping):
            _reject_removed(data)
        return data

    @field_validator("servers")
    @classmethod
    def _at_least_one_server(
        cls, servers: dict[str, _RemoteMcpServer]
    ) -> dict[str, _RemoteMcpServer]:
        if not servers:
            raise ValueError("requires at least one entry")
        if any(not name.strip() for name in servers):
            raise ValueError("server names must be non-empty")
        return servers

    @field_validator("on_connection_error")
    @classmethod
    def _throw_on_connection_error(cls, mode: str) -> str:
        if mode != "throw":
            raise ValueError('only supports on_connection_error="throw"')
        return mode


#: Declare the MCP servers whose tools should be loaded into the agent.
_define_mcp = define_extension(
    "connector",
    label="define_mcp",
    connector=McpServersDefinition,
    args=_McpServersArgs,
)


def define_mcp(**config: Unpack[McpServersConfig]) -> McpServersDefinition:
    """Declare remote MCP tools without connecting or requiring FastMCP.

    Args:
        servers: Required nonempty map of logical names to ``HttpMcpServerConfig``
            or ``SseMcpServerConfig``. See those types for fields and defaults.
            Tools use native ``{server}_{tool}`` names; collisions fail discovery.
        throw_on_load_error: Default True. False warns and omits failed native
            discovery; preparation retries next run. Pre-run authorization
            requirements and invalid selections always surface.
        on_connection_error: Default and only value ``"throw"``. Native invocation
            failures propagate; native MCP tool errors retain failed tool output.

    Returns:
        A ``McpServersDefinition`` consumed by the managed runtime.
        Catalogs are cached per verified authorization scope, not tool results or
        credentials. User connections use LangSmith's existing grant flow.

    Raises:
        ValueError: Invalid/missing fields, unsupported/removed options, competing
            authentication, invalid selections or timeout values. Later discovery
            can fail or require authorization; invocation errors propagate.

    Examples:
        >>> from managed_deepagents import connections
        >>> mcp = define_mcp(
        ...     servers={"docs": {"transport": "http", "url": "https://docs.langchain.com/mcp"}}
        ... )
        >>> mcp = define_mcp(
        ...     servers={
        ...         "docs": {"transport": "http", "url": "https://docs.langchain.com/mcp"},
        ...         "notion": {
        ...             "transport": "http",
        ...             "url": "https://mcp.notion.com/mcp",
        ...             "connection": connections.get("notion", {"type": "user"}),
        ...             "include_tools": ["search"],
        ...             "timeout": 30,
        ...         },
        ...     }
        ... )
    """
    return _define_mcp(**config)
