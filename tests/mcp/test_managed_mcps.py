import asyncio
import importlib
from types import SimpleNamespace
from unittest.mock import AsyncMock
from urllib.parse import parse_qs, urlparse

import httpx
import langgraph_sdk
import pytest
from fastapi import HTTPException
from mcp.types import Tool

from openswe.mcp import cards, managed, routes, runtime

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
                    "auth_id": "notion-auth",
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
    assert required.links == {
        "notion": managed.ConsentLink(
            url="https://smith.langchain.com/connect/notion", auth_id="notion-auth"
        )
    }


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


@pytest.fixture
def card_store(monkeypatch) -> dict[tuple[tuple[str, ...], str], dict[str, object]]:
    items: dict[tuple[tuple[str, ...], str], dict[str, object]] = {}

    async def get_item(namespace, key):
        value = items.get((tuple(namespace), key))
        return {"value": value} if value is not None else None

    async def put_item(namespace, key, value):
        items[(tuple(namespace), key)] = value

    store = SimpleNamespace(get_item=get_item, put_item=put_item)
    thread = {"metadata": {"source": "slack", "github_login": "carol", "workspace": "eng"}}
    monkeypatch.setattr(
        cards,
        "langgraph_client",
        lambda: SimpleNamespace(
            store=store, threads=SimpleNamespace(get=AsyncMock(return_value=thread))
        ),
    )
    return items


async def test_card_resumes_the_thread_once_every_service_is_connected(monkeypatch, card_store):
    card = cards.ConnectCard(thread_id="t", card_id="call-1", login="alice", gateway=GATEWAY_ID)
    monkeypatch.setattr(cards, "consent_outcome", AsyncMock(return_value="completed"))
    connected: set[str] = set()

    async def status(login, gateway, workspaces):
        return SimpleNamespace(
            ready=connected == {"notion", "linear"}, gateway=SimpleNamespace(name="Eng")
        )

    monkeypatch.setattr(cards, "gateway_status", status)
    claims: set[str] = set()

    async def claim(scope, key, *, ttl):
        if key in claims:
            return False
        claims.add(key)
        return True

    monkeypatch.setattr(cards, "claim", claim)
    dispatch = AsyncMock()
    monkeypatch.setattr(cards, "dispatch_agent_run", dispatch)

    connected.add("notion")
    await card._resume_after("notion-auth")
    dispatch.assert_not_awaited()
    connected.add("linear")
    await asyncio.gather(card._resume_after("linear-auth"), card._resume_after("linear-again"))
    assert dispatch.await_count == 1
    configurable = dispatch.await_args.args[2]
    assert configurable["github_login"] == "alice"
    assert configurable["workspace"] == "eng"
    assert dispatch.await_args.kwargs["multitask_strategy"] == "enqueue"


async def test_only_the_card_owner_can_mint_its_consent_links(monkeypatch, card_store):
    await cards.ConnectCard(
        thread_id="t", card_id="call-1", login="alice", gateway=GATEWAY_ID
    ).save()
    link = managed.ConsentLink(url="https://linear.app/oauth/authorize?x=1", auth_id="a1")
    monkeypatch.setattr(cards, "connect_link", AsyncMock(return_value=link))
    monkeypatch.setattr(cards, "consent_outcome", AsyncMock(return_value="expired"))

    with pytest.raises(HTTPException) as denied:
        await routes.connect_managed_tool_from_card(
            GATEWAY_ID, "linear", "t", "call-1", session={"sub": "bob"}
        )
    assert denied.value.status_code == 404
    redirect = await routes.connect_managed_tool_from_card(
        GATEWAY_ID, "linear", "t", "call-1", session={"sub": "Alice"}
    )
    assert redirect.headers["location"] == link.url
    cards.connect_link.assert_awaited_once_with("alice", GATEWAY_ID, "linear")


async def test_slack_card_links_to_open_swe_not_the_consent_link(monkeypatch, card_store):
    tool = importlib.import_module("openswe.tools.connect_managed_tools")

    monkeypatch.setenv("DASHBOARD_API_BASE_URL", "https://api.example")
    monkeypatch.setattr(
        tool,
        "get_config",
        lambda: {"configurable": {"source": "slack", "thread_id": "t", "github_login": "alice"}},
    )
    monkeypatch.setattr(tool, "private_credential_login", AsyncMock(return_value="alice"))
    monkeypatch.setattr(
        tool,
        "cached_workspace_settings",
        AsyncMock(return_value=SimpleNamespace(managed_tools_gateway_id=GATEWAY_ID)),
    )
    missing = managed.MissingCredential(slug="linear", display_name="Linear", kind="oauth")
    monkeypatch.setattr(
        tool,
        "gateway_status",
        AsyncMock(
            return_value=managed.GatewayStatus(
                gateway=managed.Gateway(id=GATEWAY_ID, name="Eng", tool_count=3),
                workspaces=[],
                ready=False,
                missing=[missing],
            )
        ),
    )
    post = AsyncMock(return_value={"success": True})
    monkeypatch.setattr(tool, "slack_reply", post)

    result = await tool.connect_managed_tools({"reply_surface": "slack"}, "call-1")

    assert result["status"] == "connection_required"
    button = post.await_args.kwargs["blocks"][1]["elements"][0]
    parsed = urlparse(button["url"])
    assert (parsed.netloc, parsed.path) == (
        "api.example",
        f"/dashboard/api/my-managed-tools/{GATEWAY_ID}/connect/linear",
    )
    assert parse_qs(parsed.query) == {"thread_id": ["t"], "card": ["call-1"]}
    assert await cards.ConnectCard.load("t", "call-1") is not None
    assert result["continues_automatically"] is True

    # The Slack call ends the turn, so "already connected" must still reach the person.
    tool.gateway_status.return_value = tool.gateway_status.return_value.model_copy(
        update={"ready": True, "missing": []}
    )
    post.reset_mock()
    assert (await tool.connect_managed_tools({"reply_surface": "slack"}, "call-2"))[
        "status"
    ] == "connected"
    assert post.await_args.args[1] == "final"


async def test_consent_wait_never_busy_polls(monkeypatch):
    statuses = iter(["pending", "pending", "completed", "pending", "weird"])
    pauses: list[float] = []

    def handle(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"status": next(statuses)})

    monkeypatch.setattr(
        managed,
        "mcp_http_client",
        lambda url, timeout: httpx.AsyncClient(transport=httpx.MockTransport(handle)),
    )

    async def pause(seconds):
        pauses.append(seconds)

    monkeypatch.setattr(managed.asyncio, "sleep", pause)
    assert await managed.consent_outcome("alice", "auth-1") == "completed"
    assert pauses == [1, 1]
    with pytest.raises(managed.ManagedToolsError):
        await managed.consent_outcome("alice", "auth-2")
