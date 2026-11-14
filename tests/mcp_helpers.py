"""Stand-ins for an MCP server and for the sources that name servers."""

from collections.abc import Awaitable, Callable
from uuid import uuid4

import pytest
from fastmcp.client.transports import SSETransport, StreamableHttpTransport
from mcp.types import CallToolResult, ListToolsResult, Tool
from pydantic import Field, JsonValue

from openswe.mcp import MCPConnection, MCPSource, runtime

type Transport = SSETransport | StreamableHttpTransport
type CallHandler = Callable[[Transport, str, dict[str, JsonValue]], Awaitable[CallToolResult]]
type ListHandler = Callable[[Transport], Awaitable[list[Tool]]]


def fake_mcp_server(
    monkeypatch: pytest.MonkeyPatch,
    *,
    call: CallHandler | None = None,
    tools: ListHandler | None = None,
) -> None:
    """Answer every MCP client the runtime opens; each handler sees the real transport."""

    class FakeClient:
        server_info = None

        def __init__(self, transport: Transport, **_: object) -> None:
            self.transport = transport

        async def __aenter__(self) -> FakeClient:
            return self

        async def __aexit__(self, *_: object) -> None:
            return None

        async def list_tools(self, **_: object) -> list[Tool]:
            assert tools is not None
            return await tools(self.transport)

        async def list_tools_mcp(self, **_: object) -> ListToolsResult:
            return ListToolsResult(tools=await self.list_tools())

        async def call_tool(
            self, name: str, arguments: dict[str, JsonValue], **_: object
        ) -> CallToolResult:
            assert call is not None
            return await call(self.transport, name, arguments)

    monkeypatch.setattr(runtime, "Client", FakeClient)


class HeaderConnection(MCPConnection):
    """A server the backend calls with headers the test sets."""

    headers: dict[str, str] = Field(default_factory=dict, exclude=True)

    def connection_headers(self) -> dict[str, str]:
        return self.headers


class MemorySource:
    """Connections a test edits in place, as a source the runtime reads on every lookup."""

    def __init__(self, namespace: tuple[str, ...] = ("test_mcps",)) -> None:
        self.records: dict[str, MCPConnection] = {}
        self.source = MCPSource(
            scope="managed",
            namespace=namespace,
            list_connections=self.connections,
            get_connection=self.connection,
        )

    def save(
        self, name: str = "example", url: str = "https://example.com/mcp", **fields: object
    ) -> MCPConnection:
        """Store a new revision of ``name``, as an edit to its settings would."""
        record = HeaderConnection.model_validate(
            {"name": name, "url": url, "revision": uuid4().hex, **fields}
        )
        self.records[name] = record
        return record

    async def connections(self) -> list[MCPConnection]:
        return sorted(self.records.values(), key=lambda record: record.name)

    async def connection(self, name: str) -> MCPConnection | None:
        return self.records.get(name)
