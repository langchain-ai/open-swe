import asyncio
import importlib
from types import SimpleNamespace
from unittest.mock import AsyncMock
from urllib.parse import parse_qs, urlparse

import httpx
import langgraph_sdk
import pytest
from fastapi import HTTPException
from mcp.types import ListToolsResult, Tool

from openswe.dashboard.workspace_settings import (
    WorkspaceSettingsUpdate,
    upsert_instance_settings,
    upsert_workspace_overrides,
)
from openswe.mcp import cards, managed, routes, runtime
from openswe.users.models import User

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


async def test_gateway_status_reads_missing_services_under_either_policy(monkeypatch):
    linear = {
        "kind": "oauth",
        "slug": "linear",
        "display_name": "Linear",
        "verification_url": "https://smith.langchain.com/connect/linear",
        "auth_id": "linear-auth",
    }
    listing: list[object] = [
        ListToolsResult.model_validate(
            {
                "tools": [{"name": "notion_search", "inputSchema": {"type": "object"}}],
                "_meta": {"com.langchain.langsmith/missingCredentials": [linear]},
            }
        )
    ]

    async def with_session(login, gateway, use):
        if isinstance(listing[0], Exception):
            raise listing[0]
        return listing[0]

    monkeypatch.setattr(managed, "_with_session", with_session)
    monkeypatch.setattr(
        managed,
        "_gateway_detail",
        AsyncMock(return_value={"id": GATEWAY_ID, "name": "Eng", "tool_count": 2}),
    )

    partial = await managed.gateway_status("alice", GATEWAY_ID)
    assert (partial.ready, partial.all_required, partial.tool_count) == (False, False, 1)
    assert [item.slug for item in partial.missing_for(["linear"])] == ["linear"]
    assert partial.missing_for(["Notion"]) == []
    assert (await managed.connect_link("alice", GATEWAY_ID, "linear")).auth_id == "linear-auth"
    assert await managed.connect_link("alice", GATEWAY_ID, "notion") is None

    listing[0] = managed._credentials_required(
        {"credentials": [linear, {"kind": "oauth", "slug": "notion", "display_name": "Notion"}]}
    )
    required = await managed.gateway_status("alice", GATEWAY_ID)
    assert required.all_required
    assert [item.slug for item in required.missing_for(["Linear"])] == ["linear", "notion"]


async def test_built_ins_sharing_a_langsmith_app_each_keep_their_own_consent_link(monkeypatch):
    def google(name: str) -> dict[str, str]:
        return {
            "kind": "oauth",
            "slug": "langsmith-google",
            "display_name": f"Google {name}",
            "verification_url": f"https://smith.langchain.com/connect/{name.lower()}",
            "auth_id": f"{name.lower()}-auth",
        }

    listing = [[google("Calendar"), google("Drive")]]

    async def with_session(login, gateway, use):
        return ListToolsResult.model_validate(
            {"tools": [], "_meta": {"com.langchain.langsmith/missingCredentials": listing[0]}}
        )

    monkeypatch.setattr(managed, "_with_session", with_session)
    monkeypatch.setattr(
        managed, "_gateway_detail", AsyncMock(return_value={"id": GATEWAY_ID, "name": "Eng"})
    )

    status = await managed.gateway_status("alice", GATEWAY_ID)
    [calendar] = status.missing_for(["calendar"])
    assert (await managed.connect_link("alice", GATEWAY_ID, calendar.slug)).auth_id == (
        "calendar-auth"
    )

    # Once Drive is the only one left, the card's Calendar key must read as connected.
    listing[0] = [google("Drive")]
    status = await managed.gateway_status("alice", GATEWAY_ID)
    assert status.missing_for([calendar.slug]) == []
    assert await managed.connect_link("alice", GATEWAY_ID, calendar.slug) is None


async def test_gateway_status_names_the_services_already_connected(monkeypatch):
    notion_id = "01a0f8da-0000-7000-8000-000000000001"
    linear_id = "01a0f8da-0000-7000-8000-000000000002"
    detail = {
        "id": GATEWAY_ID,
        "name": "Eng",
        "tool_count": 4,
        "tools": [
            {"server_id": notion_id, "tool_name": "search", "mcp_name": "notion_search"},
            {"server_id": notion_id, "tool_name": "fetch", "mcp_name": "notion_fetch"},
            {"server_id": linear_id, "tool_name": "get_issue", "mcp_name": "linear_get_issue"},
            {
                "builtin_server_key": "langchain-docs",
                "tool_name": "search",
                "mcp_name": "langsmith_langchain_docs_search",
            },
        ],
    }
    linear = {"kind": "oauth", "slug": "linear", "display_name": "Linear"}
    listing: list[object] = [
        ListToolsResult.model_validate(
            {
                "tools": [
                    {"name": name, "inputSchema": {"type": "object"}}
                    for name in ("notion_search", "notion_fetch", "langsmith_langchain_docs_search")
                ],
                "_meta": {"com.langchain.langsmith/missingCredentials": [linear]},
            }
        )
    ]

    async def with_session(login, gateway, use):
        if isinstance(listing[0], Exception):
            raise listing[0]
        return listing[0]

    def handle(request: httpx.Request) -> httpx.Response:
        assert request.url.path == managed.SERVERS_PATH
        return httpx.Response(
            200,
            json={
                "items": [
                    {
                        "id": notion_id,
                        "key": "notion",
                        "name": "Notion MCP",
                        "has_credential": True,
                    },
                    {"id": linear_id, "key": "linear", "name": "Linear"},
                ]
            },
        )

    monkeypatch.setattr(managed, "_with_session", with_session)
    monkeypatch.setattr(managed, "_gateway_detail", AsyncMock(return_value=detail))
    monkeypatch.setattr(
        managed, "_client", lambda: httpx.AsyncClient(transport=httpx.MockTransport(handle))
    )

    assert (await managed.gateway_status("alice", GATEWAY_ID)).connected == []

    partial = await managed.gateway_status("alice", GATEWAY_ID, with_connected=True)
    assert [
        (item.id, item.name, item.tool_count, item.disconnectable) for item in partial.connected
    ] == [
        (notion_id, "Notion MCP", 2, True),
        ("langchain-docs", "Langchain docs", 1, False),
    ]

    listing[0] = managed._credentials_required({"credentials": [linear]})
    required = await managed.gateway_status("alice", GATEWAY_ID, with_connected=True)
    assert [(item.name, item.tool_count) for item in required.connected] == [
        ("Notion MCP", 2),
        ("Langchain docs", 1),
    ]

    # LMT names a missing server by its credential slug, not the server's own name.
    notion = {"kind": "oauth", "slug": f"mt-{notion_id}", "display_name": "Notion"}
    listing[0] = managed._credentials_required({"credentials": [notion]})
    required = await managed.gateway_status("alice", GATEWAY_ID, with_connected=True)
    assert [(item.name, item.tool_count) for item in required.connected] == [
        ("Linear", 1),
        ("Langchain docs", 1),
    ]


async def test_only_the_instance_picks_the_gateway(registry_db):
    await upsert_workspace_overrides(
        "oss", WorkspaceSettingsUpdate(managed_tools_gateway_id=GATEWAY_ID)
    )
    assert await managed.instance_gateway() is None

    # Saving drops the cached read, so the change applies at once.
    await upsert_instance_settings(WorkspaceSettingsUpdate(managed_tools_gateway_id=GATEWAY_ID))
    assert await managed.instance_gateway() == GATEWAY_ID


async def test_gateways_are_read_with_the_users_own_token(monkeypatch):
    requests: list[httpx.Request] = []

    def handle(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200, json={"items": [{"id": GATEWAY_ID, "name": "Open SWE", "tool_count": 200}]}
        )

    monkeypatch.setattr(
        managed, "_client", lambda: httpx.AsyncClient(transport=httpx.MockTransport(handle))
    )
    assert await managed.list_gateways("Alice") == [
        managed.Gateway(id=GATEWAY_ID, name="Open SWE", tool_count=200)
    ]
    assert requests[-1].headers["Authorization"] == "Bearer alice-langsmith-token"
    assert "X-LangSmith-Identity" not in requests[-1].headers
    assert "X-Tenant-Id" not in requests[-1].headers
    with pytest.raises(managed.LangSmithNotConnected):
        await managed.list_gateways("bob")


async def test_disconnect_removes_only_the_callers_credential_for_a_gateway_service(monkeypatch):
    notion_id = "01a0f8da-0000-7000-8000-000000000001"
    detail = {
        "id": GATEWAY_ID,
        "tools": [{"server_id": notion_id, "tool_name": "search", "mcp_name": "notion_search"}],
    }
    requests: list[httpx.Request] = []
    status = [204]

    def handle(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(status[0])

    monkeypatch.setattr(managed, "_gateway_detail", AsyncMock(return_value=detail))
    monkeypatch.setattr(managed, "refresh_tools", AsyncMock())
    monkeypatch.setattr(
        managed, "_client", lambda: httpx.AsyncClient(transport=httpx.MockTransport(handle))
    )

    with pytest.raises(managed.ManagedToolsError, match="no such service"):
        await managed.disconnect_service("alice", GATEWAY_ID, "../../kits")
    assert requests == []

    await managed.disconnect_service("alice", GATEWAY_ID, notion_id)
    assert (requests[-1].method, requests[-1].url.path) == (
        "DELETE",
        f"{managed.SERVERS_PATH}/{notion_id}/credential",
    )
    assert requests[-1].headers["Authorization"] == "Bearer alice-langsmith-token"

    status[0] = 404
    with pytest.raises(managed.ManagedToolsError, match="service no longer exists"):
        await managed.disconnect_service("alice", GATEWAY_ID, notion_id)


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
        assert record.url.endswith(f"/v1/managed-tools/kits/{GATEWAY_ID}/mcp")
        assert record.connection_headers()["Authorization"] == "Bearer alice-langsmith-token"
        return [
            Tool(name=name, input_schema={"type": "object"})
            for name in ("notion_notion-search", "linear_list_issues")
        ]

    monkeypatch.setattr(runtime, "_discover_tools", discover)
    metadata = private_thread(monkeypatch, "alice")
    tools = await runtime.load_mcp_tools(managed.managed_mcp_source("alice", GATEWAY_ID))
    assert sorted(tool.metadata["mcp_tool_name"] for tool in tools) == [
        "linear_list_issues",
        "notion_notion-search",
    ]
    from openswe.sandboxes.tool_runtime import ToolSurface

    catalog = ToolSurface(tools={tool.name: tool for tool in tools}).catalog()
    assert len(catalog) == 2
    for entry in catalog:
        assert entry.model_dump()["mcp_provenance"] == {
            "connection_name": managed.CONNECTION_NAME,
            "scope": "managed",
            "overridden_scopes": [],
        }
        assert "alice-langsmith-token" not in entry.model_dump_json()
        assert GATEWAY_ID not in entry.model_dump_json()
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
        return [Tool(name="search", input_schema={"type": "object"})]

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

    workspace = runtime.MCPSource(
        ("workspace_mcps",), list_workspace, get_workspace, scope="workspace"
    )
    tools = await runtime.load_mcp_tools(workspace, managed.managed_mcp_source("alice", GATEWAY_ID))
    assert [tool.metadata["mcp_tool_name"] for tool in tools] == ["search"]


@pytest.fixture
async def card_store(monkeypatch, registry_db) -> None:
    monkeypatch.setenv("ALLOWED_GITHUB_USERS", "alice,bob")
    await User.sign_in("github", "1", login="alice")
    await User.sign_in("github", "2", login="bob")
    thread = {"metadata": {"source": "slack", "github_login": "carol", "workspace": "eng"}}
    monkeypatch.setattr(
        cards,
        "langgraph_client",
        lambda: SimpleNamespace(threads=SimpleNamespace(get=AsyncMock(return_value=thread))),
    )


async def test_card_resumes_once_its_own_services_are_connected(monkeypatch, card_store):
    card = cards.ConnectCard(
        thread_id="t",
        card_id="call-1",
        login="alice",
        gateway=GATEWAY_ID,
        services=["linear", "pylon"],
    )
    monkeypatch.setattr(cards, "consent_outcome", AsyncMock(return_value="completed"))
    # Re-listing is best effort; a gateway refusing it must not keep the thread waiting.
    refresh = AsyncMock(side_effect=ValueError("MCP tool discovery failed (HTTP 428)"))
    monkeypatch.setattr(cards, "refresh_tools", refresh)
    unconnected = {"notion", "linear", "pylon"}

    async def status(login, gateway):
        return managed.GatewayStatus(
            gateway=managed.Gateway(id=GATEWAY_ID, name="Eng", tool_count=3),
            ready=not unconnected,
            missing=[
                managed.MissingCredential(slug=slug, display_name=slug, kind="oauth")
                for slug in sorted(unconnected)
            ],
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

    unconnected.discard("linear")
    await card._resume_after("linear-auth")
    dispatch.assert_not_awaited()
    # Notion was not on this card, so it does not hold the thread back.
    unconnected.discard("pylon")
    await asyncio.gather(card._resume_after("pylon-auth"), card._resume_after("pylon-again"))
    assert dispatch.await_count == 1
    refresh.assert_awaited_with("alice", GATEWAY_ID)
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
    monkeypatch.setattr(tool, "instance_gateway", AsyncMock(return_value=GATEWAY_ID))
    missing = [
        managed.MissingCredential(slug="mt-1", display_name="Linear", kind="oauth"),
        managed.MissingCredential(slug="mt-2", display_name="Notion", kind="oauth"),
    ]
    monkeypatch.setattr(
        tool,
        "gateway_status",
        AsyncMock(
            return_value=managed.GatewayStatus(
                gateway=managed.Gateway(id=GATEWAY_ID, name="Eng", tool_count=3),
                ready=False,
                missing=missing,
            )
        ),
    )
    post = AsyncMock(return_value={"success": True})
    monkeypatch.setattr(tool, "send_slack_reply", post)

    result = await tool.connect_managed_tools(["linear"], {"reply_surface": "slack"}, "call-1")

    assert result["status"] == "connection_required"
    assert isinstance(offered := result["missing"], list)
    assert [item["display_name"] for item in offered] == ["Linear"]
    [button] = post.await_args.kwargs["blocks"][1]["elements"]
    parsed = urlparse(button["url"])
    assert (parsed.netloc, parsed.path) == (
        "api.example",
        f"/dashboard/api/my-managed-tools/{GATEWAY_ID}/connect/mt-1",
    )
    assert parse_qs(parsed.query) == {"thread_id": ["t"], "card": ["call-1"]}
    assert (await cards.ConnectCard.load("t", "call-1", "alice", GATEWAY_ID)).services == ["mt-1"]
    assert result["continues_automatically"] is True

    # The Slack call ends the turn, so "already connected" must still reach the person.
    tool.gateway_status.return_value = tool.gateway_status.return_value.model_copy(
        update={"ready": True, "missing": []}
    )
    post.reset_mock()
    assert (await tool.connect_managed_tools(None, {"reply_surface": "slack"}, "call-2"))[
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


@pytest.mark.parametrize(
    ("tenant", "status", "expected"),
    [
        ("", 404, "inaccessible in the current LangSmith workspace"),
        ("pinned", 404, "The tool kit no longer exists"),
        ("", 403, "LangSmith refused access to this tool kit"),
        ("", 503, "Managed tools failed (HTTP 503); retry later"),
    ],
)
def test_kit_access_failure_guidance(monkeypatch, tenant, status, expected):
    monkeypatch.setenv("LMT_TENANT_ID", tenant)
    error = managed._failure(httpx.Response(status))
    assert isinstance(error, managed.ManagedToolsError)
    assert expected in str(error)
    if status == 404 and not tenant:
        assert "possible cause" in str(error)
        assert "Sign in" in str(error)
        assert "administrator" in str(error)
        assert "LMT_TENANT_ID" in str(error)


@pytest.mark.parametrize("status", [-32601, 404, 503])
async def test_managed_discovery_backoff_isolated_and_reported(monkeypatch, status):
    import httpx2
    from mcp.shared.exceptions import MCPError

    attempts: list[tuple[str, ...]] = []
    failing = True

    async def discover(record, namespace):
        attempts.append(namespace)
        if failing:
            if status < 0:
                error = MCPError(status, "secret-token")
            else:
                error = httpx2.HTTPStatusError(
                    "secret-token",
                    request=httpx2.Request("GET", "https://example.com/secret-token"),
                    response=httpx2.Response(status),
                )
            raise ExceptionGroup("secret-token", [error])
        return [Tool(name="search", input_schema={"type": "object"})]

    monkeypatch.setattr(runtime, "_discover_tools", discover)
    private_thread(monkeypatch, "alice")
    source = managed.managed_mcp_source("alice", GATEWAY_ID)
    first = await runtime.load_mcp_tools(source)
    assert first == []
    assert first.failures
    reason = first.failures[0]
    assert first.catalog_note.count(reason) == 1
    assert "secret-token" not in first.catalog_note
    assert "workspace mismatch" not in first.catalog_note
    await runtime.load_mcp_tools(source)
    with pytest.raises(ValueError):
        await managed.refresh_tools("alice", GATEWAY_ID)
    assert len(attempts) == (1 if status != 503 else 3)

    failing = False
    record = (await source.list_connections())[0]
    assert await runtime.discover_tools(record, ("managed_tools", "bob", GATEWAY_ID))
    assert await runtime.discover_tools(record, ("managed_tools", "alice", "other-gateway"))

    if status != 503:
        now = runtime.time()
        monkeypatch.setattr(runtime, "time", lambda: now + 61)
    recovered = await runtime.load_mcp_tools(source)
    assert [tool.metadata["mcp_tool_name"] for tool in recovered] == ["search"]
    assert recovered.catalog_note == ""
