from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import langgraph_sdk
import pytest
from mcp.types import Tool

from openswe.mcp import managed, runtime

GATEWAY_ID = "01a0f8da-0f4b-7817-b2da-27b7bfa5f0f8"


@pytest.fixture(autouse=True)
def lmt(monkeypatch):
    monkeypatch.delenv("LMT_TENANT_ID", raising=False)
    monkeypatch.setattr(managed, "langsmith_oauth_configured", lambda: True)
    tokens = {"alice": "alice-langsmith-token"}

    async def access_token(login):
        return tokens.get(login.lower())

    monkeypatch.setattr(managed, "langsmith_access_token", access_token)


def test_gateway_challenge_lists_every_service_to_connect():
    response = httpx.Response(
        428,
        json={
            "credentials": [
                {
                    "kind": "oauth",
                    "slug": "notion",
                    "display_name": "Notion",
                    "verification_url": "https://smith.langchain.com/connect/notion",
                },
                {"kind": "secret", "slug": "exa", "display_name": "Exa"},
            ]
        },
    )
    required = managed._failure(response)
    assert isinstance(required, managed.GatewayCredentialsRequired)
    assert [(item.slug, item.kind) for item in required.missing] == [
        ("notion", "oauth"),
        ("exa", "secret"),
    ]
    assert required.urls == {"notion": "https://smith.langchain.com/connect/notion"}


async def test_gateways_are_read_with_the_users_own_token(monkeypatch):
    requests: list[httpx.Request] = []

    def handle(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200, json={"items": [{"id": GATEWAY_ID, "name": "Open SWE", "tools": [{}, {}]}]}
        )

    monkeypatch.setattr(
        managed, "_client", lambda: httpx.AsyncClient(transport=httpx.MockTransport(handle))
    )
    assert await managed.list_gateways("Alice") == [
        managed.Gateway(id=GATEWAY_ID, name="Open SWE", tool_count=2)
    ]
    assert requests[-1].headers["Authorization"] == "Bearer alice-langsmith-token"
    assert "X-LangSmith-Identity" not in requests[-1].headers
    assert "X-Tenant-Id" not in requests[-1].headers
    with pytest.raises(managed.LangSmithNotConnected):
        await managed.list_gateways("bob")


def private_thread(monkeypatch, owner: str) -> dict[str, str]:
    metadata = {"visibility": "private", "owner_type": "user", "owner_login": owner}
    monkeypatch.setattr(
        langgraph_sdk,
        "get_client",
        lambda: SimpleNamespace(
            threads=SimpleNamespace(get=AsyncMock(side_effect=lambda _: {"metadata": metadata}))
        ),
    )
    monkeypatch.setattr(
        "openswe.run_config.get_config",
        lambda: {"configurable": {"thread_id": "t", "github_login": owner}},
    )
    return metadata


async def test_gateway_tools_load_only_for_the_private_owner(monkeypatch):
    async def discover(record, namespace):
        assert record.url.endswith(f"/v1/managed-tools/gateways/{GATEWAY_ID}/mcp")
        assert record.connection_headers()["Authorization"] == "Bearer alice-langsmith-token"
        return [
            Tool(name=name, inputSchema={"type": "object"})
            for name in ("notion_notion-search", "linear_list_issues")
        ]

    monkeypatch.setattr(runtime, "_discover_tools", discover)
    metadata = private_thread(monkeypatch, "alice")
    tools = await runtime.load_mcp_tools(managed.managed_mcp_source("alice", GATEWAY_ID))
    assert sorted(tool.metadata["mcp_tool_name"] for tool in tools) == [
        "linear_list_issues",
        "notion_notion-search",
    ]
    metadata["visibility"] = "public"
    assert "MCP call failed" in await tools[0].ainvoke({})
    assert await runtime.load_mcp_tools(managed.managed_mcp_source("bob", GATEWAY_ID)) == []


async def test_langsmith_outage_drops_only_the_gateway(monkeypatch):
    from openswe.dashboard.langsmith_oauth import LangSmithOAuthError
    from openswe.mcp import MCPConnection

    async def unavailable(login):
        raise LangSmithOAuthError(503, "LangSmith OAuth token refresh failed: network error")

    monkeypatch.setattr(managed, "langsmith_access_token", unavailable)

    async def discover(record, namespace):
        return [Tool(name="search", inputSchema={"type": "object"})]

    monkeypatch.setattr(runtime, "_discover_tools", discover)
    workspace_record = MCPConnection(
        name="linear",
        url="https://linear.example/mcp",
        allowed_tools=["search"],
        revision="r",
        updated_at="2026-10-06T00:00:00Z",
    )

    async def list_workspace():
        return [workspace_record]

    async def get_workspace(name):
        return workspace_record if name == "linear" else None

    workspace = runtime.MCPSource(("workspace_mcps",), list_workspace, get_workspace)
    tools = await runtime.load_mcp_tools(workspace, managed.managed_mcp_source("alice", GATEWAY_ID))
    assert [tool.metadata["mcp_tool_name"] for tool in tools] == ["search"]
