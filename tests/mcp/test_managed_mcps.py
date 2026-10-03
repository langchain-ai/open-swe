from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import langgraph_sdk
import pytest
from mcp.types import Tool

from agent.mcp import managed, runtime

SERVER_ID = "2ba201a8-8751-427f-92b5-dd3ede30551c"


@pytest.fixture(autouse=True)
def lmt(monkeypatch):
    monkeypatch.delenv("LMT_TENANT_ID", raising=False)
    monkeypatch.setenv("LANGSMITH_OAUTH_CLIENT_ID", "open-swe")
    tokens = {"alice": "alice-langsmith-token"}

    async def access_token(login):
        return tokens.get(login.lower())

    monkeypatch.setattr(managed, "langsmith_access_token", access_token)


def catalog_client(monkeypatch) -> list[httpx.Request]:
    requests: list[httpx.Request] = []

    def handle(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            json={
                "items": [
                    {
                        "id": SERVER_ID,
                        "name": "Notion",
                        "upstream_url": "https://mcp.notion.com/mcp",
                        "connection": {"type": "oauth"},
                        "connected": True,
                    },
                    {"key": "parallel", "name": "Parallel", "connection": {"type": "none"}},
                ]
            },
        )

    monkeypatch.setattr(
        managed, "_client", lambda: httpx.AsyncClient(transport=httpx.MockTransport(handle))
    )
    return requests


async def test_catalog_is_read_with_the_users_own_token_and_omits_builtins(monkeypatch):
    requests = catalog_client(monkeypatch)
    servers = await managed.list_managed_servers("Alice")
    assert [(server.id, server.kind, server.connected) for server in servers] == [
        (SERVER_ID, "oauth", True)
    ]
    assert requests[-1].headers["Authorization"] == "Bearer alice-langsmith-token"
    assert "X-LangSmith-Identity" not in requests[-1].headers
    assert "X-Tenant-Id" not in requests[-1].headers


async def test_user_without_langsmith_sign_in_gets_no_catalog_or_tools(fake_store, monkeypatch):
    requests = catalog_client(monkeypatch)
    with pytest.raises(managed.LangSmithNotConnected):
        await managed.list_managed_servers("bob")
    fake_store.seed(
        ["user_managed_mcps", "bob"],
        SERVER_ID,
        {
            "server_id": SERVER_ID,
            "name": "Notion",
            "upstream_url": "https://mcp.notion.com/mcp",
            "disabled_tools": [],
            "revision": "r",
            "updated_at": "2026-10-02T00:00:00Z",
        },
    )
    assert await runtime.load_mcp_tools(managed.managed_mcp_source("bob")) == []
    assert requests == []


async def test_added_server_offers_tools_not_turned_off_only_to_the_private_owner(
    fake_store, monkeypatch
):
    catalog_client(monkeypatch)
    await managed.save_managed_selection(
        "alice", SERVER_ID, managed.ManagedSelectionUpdate(disabled_tools=["notion-delete"])
    )
    stored = next(iter(fake_store.values(["user_managed_mcps", "alice"]).values()))
    assert "alice-langsmith-token" not in str(stored)

    async def discover(record, namespace):
        assert record.connection_headers()["Authorization"] == "Bearer alice-langsmith-token"
        return [
            Tool(name=name, inputSchema={"type": "object"})
            for name in ("notion-search", "notion-delete", "notion-added-later")
        ]

    monkeypatch.setattr(runtime, "_discover_tools", discover)
    metadata = {"visibility": "private", "owner_type": "user", "owner_login": "alice"}
    monkeypatch.setattr(
        langgraph_sdk,
        "get_client",
        lambda: SimpleNamespace(
            threads=SimpleNamespace(get=AsyncMock(side_effect=lambda _: {"metadata": metadata}))
        ),
    )
    monkeypatch.setattr(
        "agent.run_config.get_config",
        lambda: {"configurable": {"thread_id": "t", "github_login": "alice"}},
    )
    tools = await runtime.load_mcp_tools(managed.managed_mcp_source("alice"))
    assert sorted(tool.metadata["mcp_tool_name"] for tool in tools) == [
        "notion-added-later",
        "notion-search",
    ]
    metadata["visibility"] = "public"
    assert "MCP call failed" in await tools[0].ainvoke({})
