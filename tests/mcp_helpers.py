"""A stand-in for ``fastmcp.Client`` that answers from callbacks instead of a remote server."""

from collections.abc import Awaitable, Callable

import pytest
from fastmcp.client.transports import SSETransport, StreamableHttpTransport
from mcp.types import CallToolResult, Tool
from pydantic import JsonValue

from openswe.mcp import runtime

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

        async def call_tool(
            self, name: str, arguments: dict[str, JsonValue], **_: object
        ) -> CallToolResult:
            assert call is not None
            return await call(self.transport, name, arguments)

    monkeypatch.setattr(runtime, "Client", FakeClient)
