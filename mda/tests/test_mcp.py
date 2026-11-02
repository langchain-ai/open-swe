import socket
from collections.abc import Awaitable, Mapping

import pytest
from langchain.tools import ToolRuntime
from langchain_core.messages import ToolMessage
from langgraph.errors import GraphInterrupt
from managed_deepagents import CredentialStoreError
from mcp.types import Tool
from rt import mcp
from rt.backend import OpenSweBackend
from rt.mcp import McpServer, McpServerError, McpTools
from runtime_api.catalog import McpServerSpec
from runtime_api.connections import ConnectionRef

_URL = "https://mcp.example.com/mcp"


class _Connections:
    """Agent Auth answering every lookup with ``error``."""

    def __init__(self, error: Exception) -> None:
        self._error = error
        self.read: list[str] = []

    def get(self, slug: str, owner: Mapping[str, object]) -> Awaitable[str]:
        self.read.append(slug)

        async def resolve() -> str:
            raise self._error

        return resolve()


@pytest.mark.parametrize(
    ("url", "address"),
    [
        ("https://elsewhere.example.com/mcp", "93.184.216.34"),
        ("http://mcp.example.com/mcp", "93.184.216.34"),
        (_URL, "169.254.169.254"),
        (_URL, "10.0.0.5"),
    ],
)
async def test_a_servers_credential_goes_only_to_its_https_origin_at_a_public_address(
    monkeypatch: pytest.MonkeyPatch, url: str, address: str
) -> None:
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *_args, **_kwargs: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (address, 443))],
    )
    async with mcp._http_client(_URL) as client:
        with pytest.raises(McpServerError):
            await client.get(url)


async def test_a_run_for_no_one_goes_without_a_personal_server_and_a_person_is_asked(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPEN_SWE_BACKEND_URL", "https://backend.invalid")
    server = McpServerSpec(
        name="notes",
        url=_URL,
        transport="streamable_http",
        tools={"search": "mcp_notes_search"},
        connection=ConnectionRef(slug="open-swe-mcp-notes", owner="user"),
    )
    no_person = CredentialStoreError("user-owned connections require a person principal")
    monkeypatch.setattr(mcp, "connections", _Connections(no_person))
    loaded = await McpTools.load([server], OpenSweBackend(), taken=(), cites_evidence=False)
    assert loaded.tools == []

    monkeypatch.setattr(mcp, "connections", _Connections(GraphInterrupt()))
    with pytest.raises(GraphInterrupt):
        await McpTools.load([server], OpenSweBackend(), taken=(), cites_evidence=False)


async def test_a_server_never_gets_a_credential_open_swe_or_a_channel_reads_for_itself(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPEN_SWE_BACKEND_URL", "https://backend.invalid")
    server = McpServerSpec(
        name="github",
        url="https://attacker.example/mcp",
        transport="streamable_http",
        tools={"search": "mcp_github_search"},
        connection=ConnectionRef(slug="mda/github-token", owner="agent"),
    )
    # Agent Auth would answer with the GitHub App's installation token.
    agent_auth = _Connections(AssertionError("read the token"))
    monkeypatch.setattr(mcp, "connections", agent_auth)
    loaded = await McpTools.load([server], OpenSweBackend(), taken=(), cites_evidence=False)
    assert (loaded.tools, agent_auth.read) == ([], [])


async def test_a_server_tool_gets_the_arguments_langchain_names_for_itself(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPEN_SWE_BACKEND_URL", "https://backend.invalid")
    server = McpServer(
        McpServerSpec(
            name="notes",
            url=_URL,
            transport="streamable_http",
            tools={"search": "mcp_notes_search", "plan": "mcp_notes_plan"},
            connection=ConnectionRef(slug="open-swe-mcp-notes", owner="agent"),
        )
    )
    schema = {"type": "object", "properties": {"config": {}, "run_manager": {}, "query": {}}}
    listed = [
        Tool(name="search", input_schema=schema),
        # The tool node fills ``runtime`` with the call's ToolRuntime.
        Tool(name="plan", input_schema={"type": "object", "properties": {"runtime": {}}}),
    ]
    monkeypatch.setattr(McpServer, "_client", lambda _self, _credential: _Listing(listed))
    [definition] = await server.allowed("credential")
    sent: list[Mapping[str, object]] = []

    async def call(
        _definition: Tool, arguments: Mapping[str, object], tool_call_id: str
    ) -> ToolMessage:
        sent.append(arguments)
        return ToolMessage(content="ok", tool_call_id=tool_call_id)

    monkeypatch.setattr(server, "call", call)
    tool = server.tool(definition, OpenSweBackend(), cites_evidence=False)
    runtime = ToolRuntime(
        state={"messages": []},
        context=None,
        config={},
        stream_writer=lambda _event: None,
        tool_call_id="call-1",
        store=None,
    )
    arguments = {"config": "staging", "run_manager": "alice", "query": "q"}
    await tool.ainvoke({**arguments, "runtime": runtime})
    assert sent == [arguments]


class _Listing:
    """An MCP client whose server lists ``tools``."""

    def __init__(self, tools: list[Tool]) -> None:
        self._tools = tools

    async def __aenter__(self) -> _Listing:
        return self

    async def __aexit__(self, *_exc: object) -> None:
        return None

    async def list_tools(self) -> list[Tool]:
        return self._tools
