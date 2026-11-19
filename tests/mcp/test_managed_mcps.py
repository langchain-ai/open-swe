import asyncio
import importlib
import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock
from urllib.parse import parse_qs, urlparse

import httpx
import httpx2
import pytest
from fastapi import HTTPException
from mcp.types import Tool

from openswe.encryption import decrypt_token
from openswe.mcp import cards, managed, routes, runtime
from openswe.mcp.transport import MCPCredentialMissing, MCPDiscoveryError
from openswe.users.models import User
from tests.conftest import serve_threads_from

ORIGINAL_PAGES = managed.ManagedTools._pages
ORIGINAL_CACHED = managed.ManagedTools._cached
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


BOT_KIT_ID = "01a1a619-0000-7000-8000-00000000b0c1"
PROFILE_ID = "01a1a619-0000-7000-8000-0000000b0700"


@pytest.fixture(autouse=True)
def lmt(monkeypatch):
    """LMT as plain data: each caller's server list and the kit's links, with no cache."""
    monkeypatch.delenv("LMT_TENANT_ID", raising=False)
    monkeypatch.setenv("LANGSMITH_API_KEY", "openswe-key")
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
                server("sentry", "Sentry", connected=False),
            ],
        },
        bot_kit_links=[],
        listed_as=[],
        bot_listing_status=None,
        kit_headers=[],
        posts=[],
        registered=[],
    )

    async def cached(self, listing, *, fresh=False):
        return await self._loaders()[listing]()

    async def pages(self, path, headers=None):
        assert path == managed.SERVERS_PATH
        caller = getattr(self, "login", "bot")
        profile = (headers or {}).get("X-LangSmith-Profile")
        state.listed_as.append(f"{caller} as {profile}" if profile else caller)
        if caller == "bot" and state.bot_listing_status:
            raise managed.ManagedToolsError("refused", state.bot_listing_status)
        return state.servers.get("bot" if profile else caller, [])

    async def request(self, method, path, *, body=None, ok=()):
        if (method, path) == ("GET", f"{managed.KITS_PATH}/{BOT_KIT_ID}"):
            state.kit_headers.append(await self.headers())
            return httpx.Response(200, json={"id": BOT_KIT_ID, "tools": state.bot_kit_links})
        if (method, path) == ("POST", managed.SERVERS_PATH):
            kind = body["connection"]["type"]
            added = server(f"own-{len(state.registered)}", body["name"], connected=kind == "none")
            added |= {"connection": body["connection"], "scope": body["scope"]}
            state.registered.append(body)
            state.servers[self.login].append(added)
            return httpx.Response(201, json=added)
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
    serve_threads_from(monkeypatch, AsyncMock(side_effect=lambda _: {"metadata": metadata}))
    monkeypatch.setattr(
        "openswe.run_config.get_config",
        lambda: {"configurable": {"thread_id": "t", "github_login": owner}},
    )
    return metadata


@pytest.fixture
def remote(monkeypatch):
    """The kit lists the same tools to every caller, and echoes calls."""
    seen: list[tuple[str, dict[str, str]]] = []

    async def discover(record):
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
            f"https://api.smith.langchain.com{managed.KITS_PATH}/{managed.ME_KIT}/mcp",
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


async def test_a_private_owner_never_sees_the_slack_bot_built_in(monkeypatch, lmt):
    """LMT's Slack built-in runs on LangSmith's Slack bot and reads public channels only,
    which Robin's own Slack tools already do; a person's own Slack is the slack_user
    server. So private threads neither load its tools nor offer to connect it."""
    lmt.servers["alice"].append(server("slack", "Slack", connected=False))

    async def discover(record):
        return [
            Tool(name=name, input_schema={"type": "object"})
            for name in (
                "langsmith_slack_read_channel_history",
                "langsmith_slack_send_channel_message",
                "langsmith_gmail_send_email",
                "slack_user_slack_search_public",
            )
        ]

    monkeypatch.setattr(runtime, "_discover_tools", discover)
    private_thread(monkeypatch, "alice")
    alice = managed.PersonalTools("alice")
    tools = await runtime.load_mcp_tools(alice.source())

    assert sorted(tool.name for tool in tools) == [
        "mcp_lmt_langsmith_gmail_send_email",
        "mcp_lmt_slack_user_slack_search_public",
    ]
    assert "Slack" not in await alice.connectable()
    assert await alice.unconnected(["slack"]) == []


@pytest.mark.parametrize(
    ("tool_name", "channel", "required", "expected"),
    [
        ("slack_user_slack_search_public_and_private", "", [], {"query": "deploys"}),
        (
            "slack_user_slack_search_public_and_private",
            "C123",
            [],
            {"query": "deploys", "context_channel_id": "C123"},
        ),
        (
            "slack_user_slack_search_public_and_private",
            "",
            ["context_channel_id"],
            {"query": "deploys", "context_channel_id": ""},
        ),
        ("linear_update_issue", "", [], {"query": "deploys", "context_channel_id": ""}),
    ],
)
async def test_only_the_optional_slack_search_channel_filter_omits_an_empty_string(
    monkeypatch: pytest.MonkeyPatch,
    tool_name: str,
    channel: str,
    required: list[str],
    expected: dict[str, str],
) -> None:
    from langchain_core.tools import StructuredTool

    schema = {
        "type": "object",
        "properties": {"query": {"type": "string"}, "context_channel_id": {"type": "string"}},
        "required": ["query", *required],
    }
    definition = Tool(name=tool_name, input_schema=schema)
    sent: list[dict[str, str]] = []

    async def call(**arguments: str) -> tuple[str, None]:
        sent.append(arguments)
        return "ok", None

    remote = StructuredTool.from_function(
        coroutine=call, name=tool_name, description="Remote tool", args_schema=schema
    )
    private_thread(monkeypatch, "alice")
    monkeypatch.setattr(runtime, "_discover_tools", AsyncMock(return_value=[definition]))
    monkeypatch.setattr(runtime, "as_langchain_tool", AsyncMock(return_value=remote))
    [tool] = await runtime.load_mcp_tools(managed.PersonalTools("alice").source())

    assert await tool.ainvoke({"query": "deploys", "context_channel_id": channel}) == "ok"
    assert sent == [expected]


async def test_a_change_to_the_kit_relists_only_when_robin_refreshes_it(monkeypatch, lmt):
    """A run never waits on the kit's slow listing because a service changed: its cached
    list stays under the same key, and Robin lists it again when it makes the change."""
    cache: dict[str, object] = {}

    async def swr(key, load, **_):
        if key not in cache:
            cache[key] = await load()

        async def mutate(value):
            cache[key] = value

        return SimpleNamespace(value=cache[key], status="fresh", mutate=mutate)

    import langgraph_api.cache

    monkeypatch.setattr(langgraph_api.cache, "swr", swr)
    listed = []

    async def discover(record):
        listed.append(record.revision)
        return [Tool(name="linear_list_issues", input_schema={"type": "object"})]

    monkeypatch.setattr(runtime, "_discover_tools", discover)
    private_thread(monkeypatch, "alice")
    tools = managed.PersonalTools("alice")
    await runtime.load_mcp_tools(tools.source())
    lmt.servers["alice"][1]["connected"] = True
    await runtime.load_mcp_tools(tools.source())
    assert len(listed) == 1
    await tools.refresh()
    assert len(listed) == 2 and listed[0] == listed[1]


async def test_the_built_kit_offers_every_server_and_takes_the_persons_own(monkeypatch, lmt):
    """LangSmith builds the ``me`` kit from every server the person can use, so there is
    no stored kit to read, and a person adds and removes only their own servers."""
    monkeypatch.setattr(managed.ManagedTools, "refresh", AsyncMock())
    tools = managed.PersonalTools("alice")
    assert "Sentry" in [item.name for item in await tools.kit_servers()]
    assert lmt.posts == []

    new = managed.NewServer(
        name="Mine", url="https://mcp.example.com/mcp", kind="secret", header="X-Api-Key"
    )
    # LangSmith registered it, so a failed refresh afterwards still reports it added.
    monkeypatch.setattr(
        managed.ManagedTools, "refresh", AsyncMock(side_effect=managed.ManagedToolsError("down"))
    )
    added = await tools.add_server(new)
    assert added.personal and added.needs_connect
    assert lmt.registered == [
        {
            "name": "Mine",
            "upstream_url": "https://mcp.example.com/mcp",
            "connection": {
                "type": "secret",
                "fields": [
                    {"name": "X-Api-Key", "injection": {"type": "header", "name": "X-Api-Key"}}
                ],
            },
            "scope": "personal",
        }
    ]

    # Only a server the person registered is theirs to delete.
    with pytest.raises(managed.ManagedToolsError):
        await tools.remove_server(LINEAR_ID)
    await tools.remove_server(added.id)
    assert lmt.posts == [("DELETE", f"{managed.SERVERS_PATH}/{added.id}")]


async def test_a_run_names_connectable_services_from_its_owners_cached_list(monkeypatch, lmt):
    """A run's start names the services its owner could connect from that person's own
    list, kept up to a day: an old one comes back at once and LangSmith is read again in
    the background, so a slow listing never holds the run."""
    from langgraph_api import cache

    monkeypatch.setattr(managed.ManagedTools, "_cached", ORIGINAL_CACHED)
    tokens = {"alice": "alice-langsmith-token", "carol": "carol-langsmith-token"}

    async def access_token(login):
        return tokens.get(login)

    monkeypatch.setattr(managed, "langsmith_access_token", access_token)
    lmt.servers["carol"] = [server(LINEAR_ID, "Linear", connected=False)]
    alice, carol = managed.PersonalTools("alice"), managed.PersonalTools("carol")

    assert await alice.connectable() == ["Notion", "Exa", "Sentry"]
    assert await carol.connectable() == ["Linear"]
    listings = len(lmt.listed_as)
    lmt.servers["alice"][1]["connected"] = True
    assert await alice.connectable() == ["Notion", "Exa", "Sentry"]
    assert len(lmt.listed_as) == listings

    listed = asyncio.Event()
    fast_pages = managed.ManagedTools._pages

    async def slow_pages(self, path, headers=None):
        await listed.wait()
        return await fast_pages(self, path, headers)

    monkeypatch.setattr(managed.ManagedTools, "_pages", slow_pages)
    now = cache._now_ms
    monkeypatch.setattr(cache, "_now_ms", lambda: now() + 2 * 60 * 60 * 1000)
    assert await asyncio.wait_for(alice.connectable(), 1) == ["Notion", "Exa", "Sentry"]
    listed.set()
    await asyncio.gather(*(state.task for state in cache._SWR_STATES.values() if state.task))
    assert await alice.connectable() == ["Exa", "Sentry"]
    assert await carol.connectable() == ["Linear"]
    await asyncio.gather(*(state.task for state in cache._SWR_STATES.values() if state.task))


async def stored_catalog(person):
    """What the shared cache holds for the person's kit tools."""
    from langgraph_api import cache

    [record] = await person.connections()
    key = cache._swr_cache_key((await person.source().catalog(record)).key)
    entry = await cache.cache_get(key)
    return entry["value"] if entry else None


async def test_a_run_reads_its_owners_kit_tools_from_their_own_cached_listing(monkeypatch, lmt):
    """A run's start never waits on LangSmith listing the owner's kit: a cached list, up to a
    day old, comes back at once and is listed again in the background; with none cached
    the run starts without the kit's tools as soon as the listing goes out. Each person's
    list is their own, holds no credential, and Robin lists it again after a change."""
    from langgraph_api import cache

    tokens = {"alice": "alice-langsmith-token", "carol": "carol-langsmith-token"}

    async def access_token(login):
        return tokens.get(login)

    monkeypatch.setattr(managed, "langsmith_access_token", access_token)
    kits = {
        "alice-langsmith-token": ["linear_list_issues"],
        "carol-langsmith-token": ["notion_search"],
    }
    listings: list[str] = []
    gate = asyncio.Event()

    async def discover(record):
        token = record.connection_headers()["Authorization"].removeprefix("Bearer ")
        listings.append(token)
        await gate.wait()
        return [Tool(name=name, input_schema={"type": "object"}) for name in kits[token]]

    monkeypatch.setattr(runtime, "_discover_tools", discover)
    alice, carol = managed.PersonalTools("alice"), managed.PersonalTools("carol")

    async def names(person, within=0.5):
        # Well inside the second a slow cache read may take: nothing here waits on LMT.
        tools = await asyncio.wait_for(runtime.load_mcp_tools(person.source()), within)
        return sorted(tool.name for tool in tools)

    async def settled():
        await asyncio.gather(*runtime._DEFERRED_LISTINGS)
        await asyncio.gather(*(state.task for state in cache._SWR_STATES.values() if state.task))

    # Nothing cached: the run starts without the kit's tools, its prompt says they are still
    # loading, and the listing finishes.
    started = await asyncio.wait_for(runtime.load_mcp_tools(alice.source()), 0.5)
    assert list(started) == []
    assert started.loading == [managed.KIT_CONNECTION]
    assert "Do not tell them to connect or enable it" in started.catalog_note
    assert listings == ["alice-langsmith-token"]
    # An automation waits out a listing that a chat would not: its own (carol), and one it
    # joins that an earlier run started (alice), past the second a cache read gets.
    monkeypatch.setattr(runtime, "_CATALOG_GRACE", runtime.LISTING_GRACE)
    automations = [
        asyncio.ensure_future(
            runtime.load_mcp_tools(person.source(), listing_grace=runtime.UNATTENDED_LISTING_GRACE)
        )
        for person in (alice, carol)
    ]
    await asyncio.sleep(runtime.LISTING_GRACE * 3)
    gate.set()
    joined, own = await asyncio.gather(*automations)
    assert [tool.name for tool in joined] == ["mcp_lmt_linear_list_issues"]
    assert [tool.name for tool in own] == ["mcp_lmt_notion_search"]
    assert joined.loading == own.loading == []
    await settled()

    # Cached: no listing at all. Carol listed her own kit, as herself, and never sees alice's.
    assert await names(alice) == ["mcp_lmt_linear_list_issues"]
    assert await names(carol) == ["mcp_lmt_notion_search"]
    assert await names(alice) == ["mcp_lmt_linear_list_issues"]
    assert listings == ["alice-langsmith-token", "carol-langsmith-token"]

    # The cache holds each list encrypted: ciphertext that decrypts to that person's tool
    # definitions, never the token the kit was listed with.
    for person, listed in ((alice, "linear_list_issues"), (carol, "notion_search")):
        sealed = await stored_catalog(person)
        assert isinstance(sealed, str) and listed not in sealed
        plaintext = decrypt_token(sealed)
        assert [tool["name"] for tool in json.loads(plaintext)] == [listed]
        assert not any(token in plaintext for token in tokens.values())

    # Stale (an hour old): served at once, and listed again in the background.
    kits["alice-langsmith-token"].append("linear_create_issue")
    gate.clear()
    now = cache._now_ms
    monkeypatch.setattr(cache, "_now_ms", lambda: now() + 60 * 60 * 1000)
    assert await names(alice) == ["mcp_lmt_linear_list_issues"]
    gate.set()
    await settled()
    assert await names(alice) == ["mcp_lmt_linear_create_issue", "mcp_lmt_linear_list_issues"]

    # A change Robin makes (connect, disconnect, add, remove) lists the kit again at once.
    monkeypatch.setattr(cache, "_now_ms", now)
    kits["alice-langsmith-token"].remove("linear_create_issue")
    await alice.refresh()
    assert await names(alice) == ["mcp_lmt_linear_list_issues"]
    assert await names(carol) == ["mcp_lmt_notion_search"]
    await settled()


@pytest.mark.parametrize("cached", [False, True])
@pytest.mark.parametrize("other_replica", [False, True])
async def test_a_refresh_is_not_undone_by_a_listing_begun_before_it(
    monkeypatch, cached, other_replica
):
    """A listing begun before a person connects a service, still running when Robin
    refreshes, neither stands in for the refresh nor overwrites it, on this replica or on
    another, which shares the cache but guards only its own writes."""
    from langgraph_api import cache

    from openswe.mcp.sealed import SealedListing

    started = asyncio.Event()
    connected = asyncio.Event()
    upstream = ["connect_figma"]
    loads = 0

    async def load():
        nonlocal loads
        loads += 1
        seen = list(upstream)
        if loads == (2 if cached else 1):
            started.set()
            await connected.wait()
        return seen

    listing = SealedListing("test:kit", load, fresh_for=timedelta(0), max_age=timedelta(days=1))
    if cached:
        await listing.lookup()
    # A run's lookup lists it, or a stale copy is listed again in the background.
    older = asyncio.ensure_future(listing.lookup())
    await started.wait()
    upstream[:] = ["figma_get_file"]
    replicas = [cache._SWR_STATES]
    if other_replica:
        replicas.append({})
        monkeypatch.setattr(cache, "_SWR_STATES", replicas[1])
    refresh = asyncio.ensure_future(listing.refresh())
    await asyncio.sleep(0.05)
    monkeypatch.setattr(cache, "_SWR_STATES", replicas[0])
    connected.set()
    await asyncio.wait_for(refresh, 1)
    await older
    tasks = [state.task for states in replicas for state in states.values() if state.task]
    await asyncio.gather(*tasks)
    assert await listing.read() == ["figma_get_file"]


async def test_a_cached_kit_listing_that_cannot_be_read_is_listed_again(monkeypatch, lmt):
    """A list sealed under a key since retired is nothing cached: the run starts without
    the kit's tools, never failing, and the kit is listed again under the current key.
    Entries cached before encryption stay under their own keys, for older replicas."""
    from cryptography.fernet import Fernet
    from langgraph_api import cache

    listings = 0

    async def discover(record):
        nonlocal listings
        listings += 1
        return [Tool(name="linear_list_issues", input_schema={"type": "object"})]

    monkeypatch.setattr(runtime, "_discover_tools", discover)
    alice = managed.PersonalTools("alice")

    async def names():
        tools = await asyncio.wait_for(runtime.load_mcp_tools(alice.source()), 0.5)
        return [tool.name for tool in tools]

    async def settled():
        await asyncio.gather(*runtime._DEFERRED_LISTINGS)
        await asyncio.gather(*(state.task for state in cache._SWR_STATES.values() if state.task))

    await names()
    await settled()
    assert await names() == ["mcp_lmt_linear_list_issues"] and listings == 1

    monkeypatch.setenv("TOKEN_ENCRYPTION_KEY", Fernet.generate_key().decode())
    assert await names() == []
    await settled()
    assert listings == 2
    assert await names() == ["mcp_lmt_linear_list_issues"]
    assert json.loads(decrypt_token(await stored_catalog(alice)))[0]["name"] == "linear_list_issues"

    # Replicas still on the plaintext format keep their own entries during a rollout:
    # this version neither reads nor overwrites them.
    [record] = await alice.connections()
    sealed_key = (await alice.source().catalog(record)).key
    plaintext_key = sealed_key.removeprefix("sealed-v1:")
    assert plaintext_key != sealed_key
    plain = [{"name": "linear_search", "inputSchema": {"type": "object"}}]
    await cache.swr(plaintext_key, AsyncMock(return_value=plain))
    await alice.refresh()
    assert await names() == ["mcp_lmt_linear_list_issues"]
    assert (await cache.cache_get(cache._swr_cache_key(plaintext_key)))["value"] == plain


async def test_managed_tools_listings_are_cached_encrypted(monkeypatch, lmt):
    """The person's server listing, which also names the services they could connect, is
    cached encrypted too, and one sealed under a retired key is read from LangSmith again."""
    from cryptography.fernet import Fernet
    from langgraph_api import cache

    monkeypatch.setattr(managed.ManagedTools, "_cached", ORIGINAL_CACHED)
    alice = managed.PersonalTools("alice")
    assert await alice.connectable() == ["Notion", "Exa", "Sentry"]
    digest = managed.hashlib.sha256(json.dumps(alice.namespace).encode()).hexdigest()
    entry = await cache.cache_get(cache._swr_cache_key(f"sealed-v1:lmt:servers:{digest}"))
    assert isinstance(entry["value"], str) and "Notion" not in entry["value"]
    assert "Notion" in decrypt_token(entry["value"])

    listed = len(lmt.listed_as)
    monkeypatch.setenv("TOKEN_ENCRYPTION_KEY", Fernet.generate_key().decode())
    lmt.servers["alice"][1]["connected"] = True
    assert await alice.connectable() == ["Exa", "Sentry"]
    assert len(lmt.listed_as) == listed + 1


async def test_a_connect_click_asks_langsmith_rather_than_the_cached_listing(monkeypatch, lmt):
    """Every read takes the cached listing except a connect click, which must not answer
    "already connected" for a service someone has since disconnected in LangSmith."""
    monkeypatch.setattr(managed.ManagedTools, "_cached", ORIGINAL_CACHED)

    async def mcp(self, path, use):
        raise self._mcp_failure(
            httpx.Response(428, json={"verification_url": "https://smith.langchain.com/c/l"})
        )

    monkeypatch.setattr(managed.ManagedTools, "_mcp", mcp)
    alice = managed.PersonalTools("alice")
    from langgraph_api import cache

    assert (await alice.server(LINEAR_ID)).usable
    lmt.servers["alice"][0]["connected"] = False
    listed = len(lmt.listed_as)
    assert (await alice.server(LINEAR_ID)).usable
    assert len(lmt.listed_as) == listed
    assert await alice.connect_link(LINEAR_ID) == managed.ConsentLink(
        url="https://smith.langchain.com/c/l"
    )
    assert len(lmt.listed_as) == listed + 1
    # The read also updated the cached copy for everything else.
    assert not (await alice.server(LINEAR_ID)).usable

    # A copy past its day is read from LangSmith once, not twice.
    lmt.servers["alice"][0]["connected"] = True
    now = cache._now_ms
    monkeypatch.setattr(cache, "_now_ms", lambda: now() + 25 * 60 * 60 * 1000)
    listed = len(lmt.listed_as)
    assert await alice.connect_link(LINEAR_ID) is None
    assert len(lmt.listed_as) == listed + 1


async def test_a_listing_backing_off_still_reports_its_failure_to_the_run(monkeypatch, lmt):
    """Not waiting on a listing must not hide a failure that is backing off, which fails
    before anything goes to LangSmith and which the run's prompt names."""
    calls = 0

    async def missing(record):
        nonlocal calls
        calls += 1
        request = httpx2.Request("POST", record.url)
        raise httpx2.HTTPStatusError(
            "gone", request=request, response=httpx2.Response(404, request=request)
        )

    monkeypatch.setattr(runtime, "_discover_tools", missing)
    alice = managed.PersonalTools("alice")
    for _ in range(2):
        tools = await asyncio.wait_for(runtime.load_mcp_tools(alice.source()), 0.5)
        assert list(tools) == []
        assert tools.failures == ["MCP tool discovery failed (HTTP 404). Check the MCP server URL"]
    assert calls == 1


async def test_an_unreachable_langsmith_leaves_the_run_without_managed_tools(monkeypatch):
    """A run starts whether or not LangSmith answers: listing failures only drop the
    services the prompt offers to connect."""
    monkeypatch.setattr(managed.ManagedTools, "_pages", ORIGINAL_PAGES)

    def refused(request):
        raise httpx2.ConnectError("refused", request=request)

    monkeypatch.setattr(
        managed.ManagedTools,
        "_client",
        lambda self: httpx2.AsyncClient(transport=httpx2.MockTransport(refused)),
    )
    assert await managed.PersonalTools("alice").connectable() == []

    # An instance signing people in to a LangSmith without HTTPS has no managed tools.
    monkeypatch.setenv("LANGSMITH_CONNECTION_URL", "http://127.0.0.1:9")
    assert not managed.managed_tools_configured()


async def test_a_server_added_after_the_last_listing_still_runs_its_tools(monkeypatch):
    """A person's new server joins the next run's catalog at once, so running one of its
    tools must not depend on what this replica listed before it was added."""
    from langchain_core.tools import StructuredTool

    from openswe import server as backend
    from openswe.agent_api import agent

    async def answer() -> str:
        return "ok"

    old, new = (
        StructuredTool.from_function(coroutine=answer, name=name, description=name)
        for name in ("mcp_lmt_linear_list_teams", "mcp_lmt_mine_fetch_docs")
    )
    monkeypatch.setattr(backend, "_integrations", {})
    monkeypatch.setattr(backend, "private_credential_login", AsyncMock(return_value="alice"))
    listings = AsyncMock(side_effect=[[old], [old, new]])
    monkeypatch.setattr(backend, "_run_mcp_tools", listings)
    monkeypatch.setattr(agent, "agent_tools_by_name", dict)
    config = {"configurable": {"thread_id": "t"}}

    kind = agent.AgentKind()
    assert await kind.tool(config, old.name) is old
    assert await kind.tool(config, new.name) is new
    assert listings.await_count == 2


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

    pages = [
        ListToolsResult(
            tools=[Tool(name="linear_list_issues", input_schema={"type": "object"})],
            next_cursor="2",
        ),
        ListToolsResult(tools=[Tool(name="notion_search", input_schema={"type": "object"})]),
    ]

    class Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return None

        async def list_tools_mcp(self, *, cursor=None, cache_mode="use"):
            return pages[0 if cursor is None else 1]

    def connections(request: httpx.Request) -> httpx.Response:
        assert request.url.path == f"/v1/managed-tools/kits/{BOT_KIT_ID}/connections"
        if request.url.params.get("cursor") == "2":
            notion = {"id": "notion", "connected": False, "tool_names": ["notion_search"]}
            return httpx.Response(200, json={"items": [notion]})
        linear = {"id": "linear", "connected": True, "tool_names": ["linear_list_issues"]}
        return httpx.Response(200, json={"items": [linear], "next_cursor": "2"})

    monkeypatch.setattr(runtime, "_client", lambda record: Client())
    monkeypatch.setattr(
        managed,
        "mcp_http_client",
        lambda url, timeout: httpx.AsyncClient(transport=httpx.MockTransport(connections)),
    )
    bot = (await managed.BotTools().connections())[0]
    assert [tool.name for tool in await runtime._discover_tools(bot)] == ["linear_list_issues"]
    # A person can connect what they lack, so their kit keeps every tool.
    private_thread(monkeypatch, "alice")
    person = (await managed.PersonalTools("alice").connections())[0]
    assert [tool.name for tool in await runtime._discover_tools(person)] == [
        "linear_list_issues",
        "notion_search",
    ]


async def test_the_bot_fails_discovery_when_langsmith_cannot_check_its_connections(monkeypatch):
    """An unchecked status would otherwise cache a guess as the bot's tools."""
    unavailable = {"id": "notion", "connected": False, "credential_status": "unavailable"}
    monkeypatch.setattr(
        managed,
        "mcp_http_client",
        lambda url, timeout: httpx.AsyncClient(
            transport=httpx.MockTransport(
                lambda _: httpx.Response(200, json={"items": [unavailable]})
            )
        ),
    )
    bot = (await managed.BotTools().connections())[0]
    with pytest.raises(MCPDiscoveryError, match="could not check"):
        await bot.unusable_tools()


@pytest.mark.parametrize(
    ("status", "expected"),
    [(428, "Call connect_managed_tools"), (409, "workspace admin must connect")],
)
async def test_a_missing_credential_tells_the_model_what_to_do(status, expected):
    """MCP transports reduce LMT's 428 and 409 to a bare protocol error, so the kit's
    connection raises its own from the HTTP response, which the tool call explains."""
    kit = managed._KitConnection(
        name="lmt", kit="kit", url="https://lmt.example/mcp", revision="r", updated_at=""
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
    monkeypatch, private, remote
):
    import openswe.server as server_module

    private_thread(monkeypatch, "alice")
    tools = await server_module._mcp_tools_for("alice" if private else None)
    assert sorted(tool.name for tool in tools) == [
        "mcp_lmt_linear_list_issues",
        "mcp_lmt_notion_search",
    ]
    assert remote == [
        (
            f"https://api.smith.langchain.com{managed.KITS_PATH}/{managed.ME_KIT if private else BOT_KIT_ID}/mcp",
            {"Authorization": "Bearer alice-langsmith-token"}
            if private
            else {"X-Api-Key": "openswe-key", "X-LangSmith-Profile": PROFILE_ID},
        )
    ]
    # A private thread's prompt names the services its owner could still connect.
    assert tools.connectable == (["Notion", "Exa", "Sentry"] if private else [])
    note = runtime.catalog_note(tools.failures, tools.connectable)
    assert ("connect_managed_tools" in note) is private
    # While the kit loads, the services not yet connected keep their connect instruction.
    loading = runtime.catalog_note(tools.failures, tools.connectable, ["lmt"])
    assert ("connect_managed_tools" in loading) is private
    assert ("not one listed above as not connected yet" in loading) is private


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
    # Every enabled service the person can use and has not connected, never one turned off.
    assert [server.name for server in await tools.unconnected()] == ["Notion", "Exa", "Sentry"]


async def test_personal_tools_view_preserves_optional_lmt_icons(lmt):
    icon_url = "https://smith.example.com/logos/linear.svg"
    icon_dark_url = "https://smith.example.com/logos/linear-dark.svg"
    linear, notion, exa, *_ = lmt.servers["alice"]
    linear.update(icon_url=icon_url, icon_dark_url=icon_dark_url)
    exa.update(icon_url={"unexpected": True})

    view = await routes.api_my_managed_tools({"sub": "alice"})
    by_id = {item["id"]: item for item in view.model_dump()["servers"]}
    assert by_id[LINEAR_ID]["icon_url"] == icon_url
    assert by_id[LINEAR_ID]["icon_dark_url"] == icon_dark_url
    assert by_id[notion["id"]]["icon_url"] is None
    assert by_id[exa["id"]]["icon_url"] is None


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


async def test_admins_read_the_bot_kit_as_themselves_and_its_state_as_the_bot(lmt):
    """LMT lets only a user read a kit, so the admin's LangSmith sign-in reads it; each
    server's state is the bot's own listing, which needs no LangSmith grant from the admin."""
    lmt.servers["bot"] = [
        server("incident", "incident-io-bot", connected=True, kind="secret"),
        server("brains", "LangChain-Brains", connected=False, kind="secret"),
        # Not in the bot kit.
        server("other", "Other", connected=True),
    ]
    lmt.bot_kit_links = [
        {"server_id": "incident", "tool_name": "incident_list"},
        {"server_id": "brains", "tool_name": "propose"},
    ]

    view = await routes.api_bot_managed_tools({"sub": "alice"})
    assert view.langsmith_connected is True
    assert [(server.name, server.connected) for server in view.servers] == [
        ("incident-io-bot", True),
        ("LangChain-Brains", False),
    ]
    [kit_headers] = lmt.kit_headers
    assert kit_headers["Authorization"] == "Bearer alice-langsmith-token"
    assert "X-LangSmith-Profile" not in kit_headers
    assert lmt.listed_as == ["bot"]

    # A bot key that may only invoke the kit falls back to listing as the admin, naming
    # the bot's profile.
    lmt.bot_listing_status = 403
    lmt.listed_as.clear()
    fallback = await routes.api_bot_managed_tools({"sub": "alice"})
    assert [server.name for server in fallback.servers] == ["incident-io-bot", "LangChain-Brains"]
    assert lmt.listed_as == ["bot", f"alice as {PROFILE_ID}"]

    # A revoked bot key stays an error rather than showing the profile's stored
    # credentials as if the bot could use them.
    lmt.bot_listing_status = 401
    lmt.listed_as.clear()
    with pytest.raises(HTTPException) as revoked:
        await routes.api_bot_managed_tools({"sub": "alice"})
    assert revoked.value.status_code == 502
    assert lmt.listed_as == ["bot"]

    # An admin who has not signed in with LangSmith is asked to, not shown an error.
    signed_out = await routes.api_bot_managed_tools({"sub": "bob"})
    assert (signed_out.configured, signed_out.langsmith_connected) == (True, False)


async def test_the_bot_kit_view_is_off_without_a_bot_profile(monkeypatch):
    monkeypatch.delenv("LMT_BOT_PROFILE_ID")
    assert (await routes.api_bot_managed_tools({"sub": "alice"})).configured is False


@pytest.mark.parametrize("slack_fails", [False, True])
async def test_card_resumes_once_its_own_services_are_connected(
    monkeypatch, lmt, card_store, slack_fails
):
    card = cards.ConnectCard(
        thread_id="t",
        card_id="call-1",
        login="alice",
        services=[NOTION_ID, "exa"],
        slack_channel_id="D123",
        slack_message_ts="123.456",
        slack_footer="<https://example.com/agents/t|Open in Robin> · $0.02",
    )
    await card.save()
    monkeypatch.setattr(
        managed.PersonalTools, "consent_status", AsyncMock(return_value="completed")
    )
    monkeypatch.setattr(managed.PersonalTools, "refresh", AsyncMock())
    claims: set[str] = set()

    async def claim(scope, key, *, ttl):
        if key in claims:
            return False
        claims.add(key)
        return True

    monkeypatch.setattr(cards, "claim", claim)
    dispatch = AsyncMock()
    monkeypatch.setattr(cards, "dispatch_agent_run", dispatch)
    update = AsyncMock(side_effect=RuntimeError("Slack unavailable") if slack_fails else None)
    monkeypatch.setattr(cards, "update_slack_message", update)
    alice = lmt.servers["alice"]

    def waiting(auth_id):
        until = datetime.now(UTC) + timedelta(minutes=5)
        return cards.ConsentWait(
            login="alice", auth_id=auth_id, until=until, thread_id="t", card_id="call-1"
        )

    alice[1] = server(NOTION_ID, "Notion", connected=True)
    await waiting("notion-auth").poll()
    dispatch.assert_not_awaited()
    update.assert_awaited_once()
    assert update.await_args.args[:2] == ("D123", "123.456")
    blocks = update.await_args.kwargs["blocks"]
    assert blocks[0]["text"]["text"] == "Connected: Notion"
    assert [element["text"]["text"] for element in blocks[-2]["elements"]] == ["Set up Exa"]
    assert blocks[-1] == {
        "type": "context",
        "elements": [{"type": "mrkdwn", "text": card.slack_footer}],
    }
    assert card.slack_footer in update.await_args.args[2]
    alice[2] = server("exa", "Exa", connected=True, kind="secret")
    await asyncio.gather(waiting("exa-auth").poll(), waiting("exa-again").poll())
    assert dispatch.await_count == 1
    blocks = update.await_args.kwargs["blocks"]
    assert blocks[:-1] == [
        {"type": "section", "text": {"type": "mrkdwn", "text": "Connected: Notion, Exa"}}
    ]
    assert blocks[-1]["elements"][0]["text"] == card.slack_footer
    configurable = dispatch.await_args.args[2]
    assert configurable["github_login"] == "alice"
    assert "workspace" not in configurable
    assert "Notion, Exa" in dispatch.await_args.args[1]


async def test_a_card_resumes_only_once_the_kit_is_listed_again(monkeypatch, lmt, card_store):
    """The resumed run must see the service just connected, so a failed re-list after
    consent is retried through the queue; past the wait's deadline the thread resumes."""
    await cards.ConnectCard(
        thread_id="t", card_id="call-1", login="alice", services=[LINEAR_ID]
    ).save()
    status = AsyncMock(return_value="completed")
    monkeypatch.setattr(managed.PersonalTools, "consent_status", status)
    refresh = AsyncMock(side_effect=[ValueError("listing failed"), None])
    monkeypatch.setattr(managed.PersonalTools, "refresh", refresh)
    monkeypatch.setattr(cards, "claim", AsyncMock(return_value=True))
    dispatch = AsyncMock()
    monkeypatch.setattr(cards, "dispatch_agent_run", dispatch)
    scheduled = AsyncMock()
    monkeypatch.setattr(cards.queue, "schedule", scheduled)

    def waiting(until):
        return cards.ConsentWait(
            login="alice", auth_id="a", until=until, thread_id="t", card_id="call-1"
        )

    await waiting(datetime.now(UTC) + timedelta(minutes=5)).poll()
    dispatch.assert_not_awaited()
    retry = cards.ConsentWait.model_validate(scheduled.await_args.args[1])
    await retry.poll()
    # The consent already completed, so the retry only lists the kit again.
    status.assert_awaited_once()
    dispatch.assert_awaited_once()

    refresh.side_effect = ValueError("listing failed")
    scheduled.reset_mock()
    await waiting(datetime.now(UTC) - timedelta(seconds=1)).poll()
    scheduled.assert_not_awaited()
    assert dispatch.await_count == 2


async def test_removed_service_does_not_block_the_connected_cards_resume(monkeypatch, lmt):
    card = cards.ConnectCard(
        thread_id="t", card_id="removed", login="alice", services=[LINEAR_ID, "removed"]
    )
    resume = AsyncMock()
    monkeypatch.setattr(cards, "claim", AsyncMock(return_value=True))
    monkeypatch.setattr(cards.ConnectCard, "_resume", resume)
    await card.resume_if_ready()
    resume.assert_awaited_once_with(["Linear"])


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


async def test_a_proposed_server_is_registered_only_when_its_cards_owner_confirms(
    monkeypatch, lmt, card_store
):
    """The agent only proposes a server; what is registered is the stored proposal, once,
    for the person the card was offered to."""
    monkeypatch.setattr(managed.ManagedTools, "refresh", AsyncMock())
    resume = AsyncMock()
    monkeypatch.setattr(cards.ConnectCard, "_resume", resume)
    queued = AsyncMock()
    monkeypatch.setattr(cards.queue, "schedule", queued)
    proposed = managed.NewServer(name="Docs", url="https://docs.example.com/mcp", kind="none")
    await cards.ConnectCard(
        thread_id="t", card_id="call-9", login="alice", proposed=proposed
    ).save()

    with pytest.raises(HTTPException) as denied:
        await routes.api_add_proposed_managed_server("t", "call-9", session={"sub": "bob"})
    assert denied.value.status_code == 404
    assert lmt.registered == []

    unconfirmed = await routes.api_get_proposed_managed_server(
        "t", "call-9", session={"sub": "alice"}
    )
    assert unconfirmed is None
    added = await routes.api_add_proposed_managed_server("t", "call-9", session={"sub": "alice"})
    again = await routes.api_add_proposed_managed_server("t", "call-9", session={"sub": "alice"})
    assert again.id == added.id
    # The card names the server it added, so a reload shows it even beside a same-named one.
    lmt.servers["alice"].append(server("older", "Docs", connected=True) | {"scope": "personal"})
    shown = await routes.api_get_proposed_managed_server("t", "call-9", session={"sub": "alice"})
    assert shown is not None and shown.id == added.id
    [registered] = lmt.registered
    assert (registered["upstream_url"], registered["scope"]) == (proposed.url, "personal")
    # A public server needs no sign-in, so the queue continues the thread, retrying a run
    # that cannot start yet.
    [(kind, payload)] = [call.args for call in queued.await_args_list]
    assert kind == cards.RESUME_JOB
    # The job names the owner by users.id, which a GitHub login rename can't break.
    assert "login" not in payload and payload["user_id"]
    resume.side_effect = [RuntimeError("runtime busy"), None]
    job = SimpleNamespace(id="j1", payload=payload)
    with pytest.raises(RuntimeError):
        await cards.ConnectCard.resume_job(job)
    await cards.ConnectCard.resume_job(job)
    assert resume.await_args_list[-1].args == (["Docs"],)


async def test_slack_card_links_to_open_swe_not_the_consent_link(monkeypatch, card_store):
    tool = importlib.import_module("openswe.tools.connect_managed_tools")

    monkeypatch.setenv("DASHBOARD_API_BASE_URL", "https://api.example")
    monkeypatch.setattr(
        tool,
        "get_config",
        lambda: {"configurable": {"source": "slack", "thread_id": "t", "github_login": "alice"}},
    )
    monkeypatch.setattr(tool, "private_credential_login", AsyncMock(return_value="alice"))
    post = AsyncMock(
        return_value={
            "success": True,
            "channel_id": "D123",
            "message_ts": "123.456",
            "footer": "Open in Robin · $0.02",
        }
    )
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
    saved = await cards.ConnectCard.load("t", "call-1", "alice")
    assert saved.services == [NOTION_ID]
    assert (saved.slack_channel_id, saved.slack_message_ts) == ("D123", "123.456")
    assert saved.slack_footer == "Open in Robin · $0.02"
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
