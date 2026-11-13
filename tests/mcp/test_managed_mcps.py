import asyncio
import importlib
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock
from urllib.parse import parse_qs, urlparse

import httpx
import httpx2
import langgraph_sdk
import pytest
from fastapi import HTTPException
from mcp.types import Tool

from openswe.mcp import cards, managed, routes, runtime
from openswe.mcp.transport import MCPCredentialMissing
from openswe.users.models import User

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


KIT_ID = "01a11414-0000-7000-8000-00000000c1d0"
BOT_KIT_ID = "01a1a619-0000-7000-8000-00000000b0c1"
PROFILE_ID = "01a1a619-0000-7000-8000-0000000b0700"


@pytest.fixture(autouse=True)
def lmt(monkeypatch):
    """LMT as plain data: each caller's server list and the kit's links, with no cache."""
    monkeypatch.delenv("LMT_TENANT_ID", raising=False)
    monkeypatch.setenv("LANGSMITH_API_KEY", "openswe-key")
    monkeypatch.setenv("LMT_KIT_ID", KIT_ID)
    monkeypatch.setenv("LMT_BOT_KIT_ID", BOT_KIT_ID)
    monkeypatch.setenv("LMT_BOT_PROFILE_ID", PROFILE_ID)
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
                # Not in the kit, so never offered.
                server("sentry", "Sentry", connected=False),
            ],
        },
        kit_links=[
            {"server_id": LINEAR_ID, "tool_name": "list_issues"},
            {"server_id": NOTION_ID, "tool_name": "search"},
            {"server_id": "exa", "tool_name": "web_search"},
            {"builtin_server_key": "off", "tool_name": "off"},
        ],
        posts=[],
    )

    async def cached(self, listing):
        return await self._loaders()[listing]()

    async def pages(self, path):
        assert path == managed.SERVERS_PATH
        return state.servers.get(self.login, [])

    async def request(self, method, path, *, body=None, ok=()):
        if (method, path) == ("GET", f"{managed.KITS_PATH}/{KIT_ID}"):
            return httpx.Response(200, json={"id": KIT_ID, "tools": state.kit_links})
        state.posts.append((method, path))
        return httpx.Response(
            201, json={"verification_url": "https://smith.langchain.com/c/1", "auth_id": "a1"}
        )

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
    """The kit lists the same tools to every caller, and echoes calls."""
    seen: list[tuple[str, dict[str, str]]] = []

    async def discover(record, namespace):
        seen.append((record.url, record.connection_headers()))
        return [
            Tool(name=name, input_schema={"type": "object"})
            for name in ("linear_list_issues", "notion_search")
        ]

    monkeypatch.setattr(runtime, "_discover_tools", discover)
    return seen


async def test_a_private_owner_loads_the_whole_kit_as_themselves(monkeypatch, remote):
    metadata = private_thread(monkeypatch, "alice")
    tools = await runtime.load_mcp_tools(managed.PersonalTools("alice").source())

    assert sorted(tool.name for tool in tools) == [
        "mcp_lmt_linear_list_issues",
        "mcp_lmt_notion_search",
    ]
    assert remote == [
        (
            f"https://api.smith.langchain.com{managed.KITS_PATH}/{KIT_ID}/mcp",
            {"Authorization": "Bearer alice-langsmith-token"},
        )
    ]
    from openswe.sandboxes.tool_runtime import ToolSurface

    for entry in ToolSurface(tools={tool.name: tool for tool in tools}).catalog():
        assert "alice-langsmith-token" not in entry.model_dump_json()
    metadata["visibility"] = "public"
    assert "MCP call failed" in await tools[0].ainvoke({})
    # Someone never signed in to LangSmith has no personal tools, and nothing fails.
    assert await runtime.load_mcp_tools(managed.PersonalTools("bob").source()) == []


async def test_connecting_a_service_lists_the_kits_tools_again(monkeypatch, lmt):
    """LMT lists a service's tools from the service once the person connects it, so the next
    run must not keep the list it cached before, wherever the connection was made."""
    cache: dict[str, object] = {}

    async def swr(key, load, **_):
        if key not in cache:
            cache[key] = await load()
        return SimpleNamespace(value=cache[key], status="fresh")

    import langgraph_api.cache

    monkeypatch.setattr(langgraph_api.cache, "swr", swr)
    listed = []

    async def discover(record, namespace):
        listed.append(record.revision)
        return [Tool(name="linear_list_issues", input_schema={"type": "object"})]

    monkeypatch.setattr(runtime, "_discover_tools", discover)
    private_thread(monkeypatch, "alice")
    source = managed.PersonalTools("alice").source()
    await runtime.load_mcp_tools(source)
    await runtime.load_mcp_tools(source)
    lmt.servers["alice"][1]["connected"] = True
    await runtime.load_mcp_tools(source)
    assert len(listed) == 2 and listed[0] != listed[1]


async def test_the_bot_calls_its_own_kit_as_its_profile_with_robins_key(monkeypatch, remote):
    tools = await runtime.load_mcp_tools(managed.BotTools().source())

    assert sorted(tool.name for tool in tools) == [
        "mcp_lmt_linear_list_issues",
        "mcp_lmt_notion_search",
    ]
    assert remote == [
        (
            f"https://api.smith.langchain.com{managed.KITS_PATH}/{BOT_KIT_ID}/mcp",
            {"X-Api-Key": "openswe-key", "X-LangSmith-Profile": PROFILE_ID},
        )
    ]
    # The bot never falls back to the personal kit, or loads its kit without its profile.
    monkeypatch.delenv("LMT_BOT_KIT_ID")
    assert await runtime.load_mcp_tools(managed.BotTools().source()) == []
    monkeypatch.setenv("LMT_BOT_KIT_ID", BOT_KIT_ID)
    monkeypatch.delenv("LMT_BOT_PROFILE_ID")
    assert await runtime.load_mcp_tools(managed.BotTools().source()) == []


async def test_the_bot_leaves_out_tools_of_services_its_profile_has_not_connected(monkeypatch):
    from mcp.types import ListToolsResult

    meta = {
        managed._MISSING_CREDENTIALS_META: [
            {"slug": "notion", "tools": [{"name": "notion_search"}]},
        ]
    }
    pages = [
        ListToolsResult(
            tools=[Tool(name="linear_list_issues", input_schema={"type": "object"})],
            next_cursor="2",
        ),
        ListToolsResult(
            tools=[Tool(name="notion_search", input_schema={"type": "object"})], meta=meta
        ),
    ]

    class Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return None

        async def list_tools_mcp(self, *, cursor=None, cache_mode="use"):
            return pages[0 if cursor is None else 1]

    monkeypatch.setattr(runtime, "_client", lambda record, namespace: Client())
    bot = (await managed.BotTools().connections())[0]
    assert [tool.name for tool in await runtime._discover_tools(bot, ("b",))] == [
        "linear_list_issues"
    ]
    private_thread(monkeypatch, "alice")
    person = (await managed.PersonalTools("alice").connections())[0]
    assert [tool.name for tool in await runtime._discover_tools(person, ("p",))] == [
        "linear_list_issues",
        "notion_search",
    ]


@pytest.mark.parametrize(
    ("status", "expected"),
    [(428, "Call connect_managed_tools"), (409, "workspace admin must connect")],
)
async def test_a_missing_credential_tells_the_model_what_to_do(status, expected):
    """MCP transports reduce LMT's 428 and 409 to a bare protocol error, so the kit's
    connection raises its own from the HTTP response, which the tool call explains."""
    kit = managed._KitConnection(
        name="lmt", url="https://lmt.example/mcp", revision="r", updated_at=""
    )
    await kit.check_response(httpx2.Response(200))
    try:
        try:
            await kit.check_response(httpx2.Response(status))
        except MCPCredentialMissing as exc:
            raise RuntimeError("tool call failed") from exc
    except RuntimeError as wrapped:
        assert expected in runtime._call_failure(wrapped)


@pytest.mark.parametrize("private", [True, False])
async def test_private_threads_load_the_kit_as_the_owner_and_others_as_the_bot(
    monkeypatch, private
):
    import openswe.server as server_module

    loaded = []

    async def load(*sources):
        loaded.extend(source.namespace for source in sources)
        return runtime.MCPTools()

    monkeypatch.setattr(server_module, "load_mcp_tools", load)
    tools = await server_module._mcp_tools_for("alice" if private else None, "eng")
    managed_sources = [namespace for namespace in loaded if namespace[0] == "managed_tools"]
    assert managed_sources == (
        [("managed_tools", "alice")] if private else [("managed_tools", "bot", PROFILE_ID)]
    )
    # A private thread's prompt names the kit's services its owner could still connect.
    assert tools.connectable == (["Notion", "Exa"] if private else [])
    note = runtime.catalog_note(tools.failures, tools.connectable)
    assert ("connect_managed_tools" in note) is private


def test_a_connect_challenge_survives_the_client_wrapping_it():
    """The MCP client re-raises a failed connect as a RuntimeError caused by LMT's 428."""
    link = managed.ConsentLink(url="https://www.linkedin.com/oauth/v2/authorization")
    required = managed._CredentialRequired(link)
    try:
        try:
            raise required
        except managed._CredentialRequired as exc:
            raise RuntimeError("Client failed to connect: Connect this service first") from exc
    except RuntimeError as wrapped:
        assert managed._wrapped(wrapped, managed.ManagedToolsError) is required


async def test_connect_links_come_from_lmt_for_the_kits_oauth_and_secret_services(monkeypatch, lmt):
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

    real_request = managed.ManagedTools._request

    async def request(self, method, path, *, body=None, ok=()):
        if method == "GET":
            return await real_request(self, method, path, body=body, ok=ok)
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
    # Sentry is in the workspace but not the kit, so connecting it would load nothing.
    assert [server.name for server in await tools.unconnected()] == ["Notion", "Exa"]


async def test_discovery_denies_shared_threads_before_reading_integrations(monkeypatch):
    tool = importlib.import_module("openswe.tools.discover_managed_tools")
    monkeypatch.setattr(tool, "private_credential_login", AsyncMock(return_value=None))
    servers = AsyncMock()
    monkeypatch.setattr(managed.PersonalTools, "kit_servers", servers)
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
    await card.save()
    monkeypatch.setattr(
        managed.PersonalTools, "consent_status", AsyncMock(return_value="completed")
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

    def waiting(auth_id):
        until = datetime.now(UTC) + timedelta(minutes=5)
        return cards.ConsentWait(
            login="alice", auth_id=auth_id, until=until, thread_id="t", card_id="call-1"
        )

    alice[1] = server(NOTION_ID, "Notion", connected=True)
    await waiting("notion-auth").poll()
    dispatch.assert_not_awaited()
    alice[2] = server("exa", "Exa", connected=True, kind="secret")
    await asyncio.gather(waiting("exa-auth").poll(), waiting("exa-again").poll())
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
    waits = AsyncMock()
    monkeypatch.setattr(cards.queue, "schedule", waits)

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
    # Settings links the same route with no card, connecting outside any thread.
    connect.reset_mock()
    outside = await routes.connect_managed_tool_from_card(NOTION_ID, session={"sub": "alice"})
    assert outside.headers["location"] == link.url
    connect.assert_awaited_once_with(NOTION_ID)
    # The job queue waits on the link, so a restarted backend still resumes the thread.
    queued = waits.await_args_list[0].args
    assert queued[0] == cards.CONSENT_JOB
    assert (queued[1]["auth_id"], queued[1]["card_id"]) == ("a1", "call-1")


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


async def test_a_consent_wait_follows_its_owner_through_a_github_rename(monkeypatch, card_store):
    """The queued job names the person, not their GitHub login, so a rename while the
    consent link is open still polls and resumes as them."""
    scheduled = AsyncMock()
    monkeypatch.setattr(cards.queue, "schedule", scheduled)
    await cards.ConsentWait.start("alice", "a1")
    payload = scheduled.await_args.args[1]
    await User.sign_in("github", "1", login="alice-renamed")
    polled_as: list[str] = []

    async def consent_status(self, auth_id):
        polled_as.append(self.login)
        return "expired"

    monkeypatch.setattr(managed.PersonalTools, "consent_status", consent_status)
    await cards.ConsentWait.model_validate(payload).poll()
    assert polled_as == ["alice-renamed"]


async def test_a_pending_consent_polls_again_through_the_queue_until_it_expires(monkeypatch):
    """Each job long polls once; a pending link queues the next poll, never sooner than
    the floor, and the wait ends once the link has expired or LangSmith answers."""
    statuses = iter(["pending", "pending", "completed", "weird"])

    def handle(request: httpx.Request) -> httpx.Response:
        assert request.url.params["wait_seconds"] == "25"
        return httpx.Response(200, json={"status": next(statuses)})

    monkeypatch.setattr(
        managed,
        "mcp_http_client",
        lambda url, timeout: httpx.AsyncClient(transport=httpx.MockTransport(handle)),
    )
    monkeypatch.setattr(managed.PersonalTools, "refresh", AsyncMock())
    scheduled = AsyncMock()
    monkeypatch.setattr(cards.queue, "schedule", scheduled)

    now = datetime.now(UTC)
    await cards.ConsentWait(login="alice", auth_id="a", until=now + timedelta(minutes=5)).poll()
    assert scheduled.await_args.kwargs["due_at"] >= now + timedelta(seconds=1)
    scheduled.reset_mock()
    await cards.ConsentWait(login="alice", auth_id="a", until=now - timedelta(seconds=1)).poll()
    scheduled.assert_not_awaited()
    await cards.ConsentWait(login="alice", auth_id="a", until=now + timedelta(minutes=5)).poll()
    scheduled.assert_not_awaited()
    with pytest.raises(managed.ManagedToolsError):
        await managed.PersonalTools("alice").consent_status("b")


async def test_new_personal_mcp_servers_go_through_lmt_while_stored_ones_stay_editable(
    monkeypatch,
):
    from openswe.dashboard import langsmith_oauth
    from openswe.mcp.models import MCPConnection, MCPConnectionUpdate, prepare_connection

    monkeypatch.setattr(langsmith_oauth, "langsmith_oauth_configured", lambda: True)
    update = MCPConnectionUpdate(name="docs", url="https://docs.example/mcp")
    with pytest.raises(ValueError, match="LangSmith Managed Tools"):
        await prepare_connection(update, scope="user")
    assert (await prepare_connection(update, scope="workspace")).name == "docs"
    stored = MCPConnection(
        name="docs", url="https://docs.example/mcp", revision="r1", updated_at="2026-11-01"
    )
    edited = update.model_copy(update={"enabled": False})
    assert (await prepare_connection(edited, stored, scope="user")).enabled is False
