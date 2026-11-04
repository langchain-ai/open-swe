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
from openswe.users.models import User

PROFILE_ID = "01a0f8da-0000-7000-8000-0000000000aa"
LINEAR_ID = "01a0f8da-0000-7000-8000-000000000001"
NOTION_ID = "01a0f8da-0000-7000-8000-000000000002"


def server(server_id, name, *, connected, kind="oauth", enabled=True):
    return {
        "id": server_id,
        "name": name,
        "connection": {"type": kind},
        "connected": connected,
        "has_credential": connected,
        "enabled": enabled,
    }


@pytest.fixture(autouse=True)
def lmt(monkeypatch):
    """LMT as plain data: each caller's server list and the workspace's profiles, with no
    cache. A bot sees its profile's own credentials."""
    monkeypatch.delenv("LMT_TENANT_ID", raising=False)
    monkeypatch.setenv("LANGSMITH_API_KEY", "openswe-key")
    monkeypatch.setattr(managed, "langsmith_oauth_configured", lambda: True)

    async def access_token(login):
        return {"alice": "alice-langsmith-token"}.get(login.lower())

    monkeypatch.setattr(managed, "langsmith_access_token", access_token)
    state = SimpleNamespace(
        servers={
            "alice": [
                server(LINEAR_ID, "Linear", connected=True),
                server(NOTION_ID, "Notion", connected=False),
                server("exa", "Exa", connected=False, kind="secret"),
                server("off", "Off", connected=True, enabled=False),
                # Public servers are connected for everyone, but are not the person's own.
                {
                    **server("weather", "Weather", connected=True, kind="none"),
                    "has_credential": False,
                },
            ],
            f"profile:{PROFILE_ID}": [
                server(LINEAR_ID, "Linear", connected=True),
                server(NOTION_ID, "Notion", connected=False),
                {
                    **server("weather", "Weather", connected=True, kind="none"),
                    "has_credential": False,
                },
            ],
        },
        profiles=[],
        posts=[],
    )

    async def cached(self, listing):
        return await self._loaders()[listing]()

    async def pages(self, path):
        if path == managed.PROFILES_PATH:
            return state.profiles
        assert path == managed.SERVERS_PATH
        if isinstance(self, managed.BotTools):
            return state.servers.get(f"profile:{self.profile}", [])
        return state.servers.get(self.login, [])

    async def request(self, method, path, *, body=None, ok=()):
        state.posts.append((method, path))
        if path == managed.PROFILES_PATH:
            await asyncio.sleep(0)
            profile = {"id": f"01a0f8da-0000-7000-8000-0000000001{len(state.profiles):02d}", **body}
            state.profiles.append(profile)
            return httpx.Response(201, json=profile)
        return httpx.Response(201, json={"verification_url": "https://smith.langchain.com/c/1"})

    monkeypatch.setattr(managed.ManagedTools, "_cached", cached)
    monkeypatch.setattr(managed.ManagedTools, "_pages", pages)
    monkeypatch.setattr(managed.ManagedTools, "_request", request)
    return state


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


@pytest.fixture
def remote(monkeypatch):
    """Each LMT server route lists the tools named after it, and echoes calls."""
    seen: list[tuple[str, dict[str, str]]] = []

    async def discover(record, namespace):
        seen.append((record.url, record.connection_headers()))
        return [
            Tool(name=name, input_schema={"type": "object"})
            for name in ("list_issues", "save_issue", "search")
        ]

    monkeypatch.setattr(runtime, "_discover_tools", discover)
    return seen


async def test_personal_tools_are_every_connected_service_for_the_private_owner_only(
    monkeypatch, remote
):
    metadata = private_thread(monkeypatch, "alice")
    tools = await runtime.load_mcp_tools(managed.PersonalTools("alice").source())

    assert sorted(tool.name for tool in tools) == [
        "mcp_linear_list_issues",
        "mcp_linear_save_issue",
        "mcp_linear_search",
    ]
    [(url, headers)] = {(url, tuple(headers.items())) for url, headers in remote}
    assert url.endswith(f"/v1/managed-tools/servers/{LINEAR_ID}/mcp")
    assert dict(headers) == {"Authorization": "Bearer alice-langsmith-token"}
    from openswe.sandboxes.tool_runtime import ToolSurface

    for entry in ToolSurface(tools={tool.name: tool for tool in tools}).catalog():
        assert "alice-langsmith-token" not in entry.model_dump_json()
    metadata["visibility"] = "public"
    assert "MCP call failed" in await tools[0].ainvoke({})
    # Someone never signed in to LangSmith has no personal tools, and nothing fails.
    assert await runtime.load_mcp_tools(managed.PersonalTools("bob").source()) == []


async def test_bot_loads_every_service_its_profile_connected(remote):
    bot = managed.BotTools("eng", PROFILE_ID)
    tools = await runtime.load_mcp_tools(bot.source())

    # Nobody connected Notion for the bot, and a public server is no one's own.
    assert sorted(tool.name for tool in tools) == [
        "mcp_linear_list_issues",
        "mcp_linear_save_issue",
        "mcp_linear_search",
    ]
    [(url, headers)] = {(url, tuple(headers.items())) for url, headers in remote}
    assert url.endswith(f"/v1/managed-tools/servers/{LINEAR_ID}/mcp")
    assert dict(headers) == {"X-Api-Key": "openswe-key", "X-LangSmith-Profile": PROFILE_ID}
    status = await bot.status()
    assert [(item.name, item.connected) for item in status.servers] == [
        ("Linear", True),
        ("Notion", False),
    ]


async def test_owners_own_connection_replaces_the_bots_in_their_private_thread(monkeypatch, remote):
    private_thread(monkeypatch, "alice")
    bot = managed.BotTools("eng", PROFILE_ID)
    tools = await runtime.load_mcp_tools(bot.source(), managed.PersonalTools("alice").source())

    linear = next(tool for tool in tools if tool.name == "mcp_linear_list_issues")
    provenance = linear.metadata["mcp_provenance"]
    assert (provenance.scope, provenance.overridden_scopes) == ("managed", ["bot"])
    assert sorted(tool.name for tool in tools) == [
        "mcp_linear_list_issues",
        "mcp_linear_save_issue",
        "mcp_linear_search",
    ]


async def test_the_first_bot_connect_makes_one_profile_per_workspace(lmt, registry_db):
    assert await managed.BotTools.for_workspace("eng") is None
    bot = await managed.BotTools.of("eng")
    # Admins connecting at once make one profile between them.
    await asyncio.gather(
        cards.connect_bot_service("alice", bot, NOTION_ID),
        cards.connect_bot_service("alice", bot, NOTION_ID),
    )
    assert [p["name"] for p in lmt.profiles] == ["Open SWE bot (eng)"]
    eng = await managed.BotTools.for_workspace("eng")
    assert eng is not None and eng.profile == lmt.profiles[0]["id"]
    assert (
        lmt.posts.count(("POST", f"{eng.profile_server_path(NOTION_ID)}/credential-sessions")) == 2
    )

    # A workspace whose setting was lost gets back the profile LangSmith kept for it.
    lmt.profiles.append({"id": PROFILE_ID, "name": "Open SWE bot (ops)"})
    await cards.connect_bot_service("alice", await managed.BotTools.of("ops"), NOTION_ID)
    ops = await managed.BotTools.for_workspace("ops")
    assert ops is not None and ops.profile == PROFILE_ID
    assert len(lmt.profiles) == 2


async def test_connect_links_come_from_lmt_for_oauth_and_secret_services(monkeypatch):
    tools = managed.PersonalTools("alice")
    challenge = {
        "kind": "oauth",
        "slug": "notion",
        "display_name": "Notion",
        "verification_url": "https://smith.langchain.com/connect/notion",
        "auth_id": "notion-auth",
    }

    async def mcp(self, path, use):
        assert path == f"{managed.SERVERS_PATH}/{NOTION_ID}/mcp"
        raise self._mcp_failure(httpx.Response(428, json=challenge))

    async def request(self, method, path, *, body=None, ok=()):
        assert (method, path) == ("POST", f"{managed.SERVERS_PATH}/exa/credential-sessions")
        return httpx.Response(
            201,
            json={"verification_url": "https://smith.langchain.com/secret/exa", "auth_id": "s1"},
        )

    monkeypatch.setattr(managed.ManagedTools, "_mcp", mcp)
    monkeypatch.setattr(managed.ManagedTools, "_request", request)
    assert await tools.connect_link(NOTION_ID) == managed.ConsentLink(
        url="https://smith.langchain.com/connect/notion", auth_id="notion-auth"
    )
    assert await tools.connect_link("exa") == managed.ConsentLink(
        url="https://smith.langchain.com/secret/exa"
    )
    assert await tools.connect_link(LINEAR_ID) is None
    assert [server.name for server in await tools.unconnected(["notion"])] == ["Notion"]
    assert [server.name for server in await tools.unconnected()] == ["Notion", "Exa"]


async def test_discovery_denies_shared_threads_before_reading_integrations(monkeypatch):
    tool = importlib.import_module("openswe.tools.discover_managed_tools")
    monkeypatch.setattr(tool, "private_credential_login", AsyncMock(return_value=None))
    servers = AsyncMock()
    monkeypatch.setattr(managed.PersonalTools, "servers", servers)
    result = await tool.discover_managed_tools("linear")
    assert result["success"] is False
    servers.assert_not_awaited()


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


async def test_card_resumes_once_its_own_services_are_connected(monkeypatch, lmt, card_store):
    card = cards.ConnectCard(
        thread_id="t", card_id="call-1", login="alice", services=[NOTION_ID, "exa"]
    )
    monkeypatch.setattr(
        managed.PersonalTools, "consent_outcome", AsyncMock(return_value="completed")
    )
    # Re-listing is best effort; a refusal must not keep the thread waiting.
    monkeypatch.setattr(
        managed.PersonalTools, "refresh", AsyncMock(side_effect=ValueError("listing failed"))
    )
    claims: set[str] = set()

    async def claim(scope, key, *, ttl):
        if key in claims:
            return False
        claims.add(key)
        return True

    monkeypatch.setattr(cards, "claim", claim)
    dispatch = AsyncMock()
    monkeypatch.setattr(cards, "dispatch_agent_run", dispatch)
    alice = lmt.servers["alice"]

    alice[1] = server(NOTION_ID, "Notion", connected=True)
    await card._resume_after("notion-auth")
    dispatch.assert_not_awaited()
    alice[2] = server("exa", "Exa", connected=True, kind="secret")
    await asyncio.gather(card._resume_after("exa-auth"), card._resume_after("exa-again"))
    assert dispatch.await_count == 1
    configurable = dispatch.await_args.args[2]
    assert configurable["github_login"] == "alice"
    assert configurable["workspace"] == "eng"
    assert "Notion, Exa" in dispatch.await_args.args[1]


async def test_only_the_card_owner_can_mint_its_consent_links(monkeypatch, card_store):
    await cards.ConnectCard(
        thread_id="t", card_id="call-1", login="alice", services=[NOTION_ID]
    ).save()
    link = managed.ConsentLink(url="https://linear.app/oauth/authorize?x=1", auth_id="a1")
    connect = AsyncMock(return_value=link)
    monkeypatch.setattr(managed.PersonalTools, "connect_link", connect)
    monkeypatch.setattr(managed.PersonalTools, "consent_outcome", AsyncMock(return_value="expired"))

    with pytest.raises(HTTPException) as denied:
        await routes.connect_managed_tool_from_card(
            NOTION_ID, "t", "call-1", session={"sub": "bob"}
        )
    assert denied.value.status_code == 404
    with pytest.raises(HTTPException) as other:
        await routes.connect_managed_tool_from_card(
            LINEAR_ID, "t", "call-1", session={"sub": "alice"}
        )
    assert other.value.status_code == 400
    redirect = await routes.connect_managed_tool_from_card(
        NOTION_ID, "t", "call-1", session={"sub": "Alice"}
    )
    assert redirect.headers["location"] == link.url
    connect.assert_awaited_once_with(NOTION_ID)


async def test_slack_card_links_to_open_swe_not_the_consent_link(monkeypatch, card_store):
    tool = importlib.import_module("openswe.tools.connect_managed_tools")

    monkeypatch.setenv("DASHBOARD_API_BASE_URL", "https://api.example")
    monkeypatch.setattr(
        tool,
        "get_config",
        lambda: {"configurable": {"source": "slack", "thread_id": "t", "github_login": "alice"}},
    )
    monkeypatch.setattr(tool, "private_credential_login", AsyncMock(return_value="alice"))
    post = AsyncMock(return_value={"success": True})
    monkeypatch.setattr(tool, "send_slack_reply", post)

    result = await tool.connect_managed_tools(["notion"], {"reply_surface": "slack"}, "call-1")

    assert result["status"] == "connection_required"
    assert isinstance(missing := result["missing"], list)
    assert [item["name"] for item in missing] == ["Notion"]
    [button] = post.await_args.kwargs["blocks"][1]["elements"]
    parsed = urlparse(button["url"])
    assert (parsed.netloc, parsed.path) == (
        "api.example",
        f"/dashboard/api/my-managed-tools/connect/{NOTION_ID}",
    )
    assert parse_qs(parsed.query) == {"thread_id": ["t"], "card": ["call-1"]}
    assert (await cards.ConnectCard.load("t", "call-1", "alice")).services == [NOTION_ID]
    assert result["continues_automatically"] is True

    # The Slack call ends the turn, so "already connected" must still reach the person.
    post.reset_mock()
    assert (await tool.connect_managed_tools(["linear"], {"reply_surface": "slack"}, "call-2"))[
        "status"
    ] == "nothing_to_connect"
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
    tools = managed.PersonalTools("alice")
    assert await tools.consent_outcome("auth-1") == "completed"
    assert pauses == [1, 1]
    with pytest.raises(managed.ManagedToolsError):
        await tools.consent_outcome("auth-2")
