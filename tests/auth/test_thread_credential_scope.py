import importlib
from types import SimpleNamespace
from unittest.mock import AsyncMock

import langgraph_sdk
import pytest

from agent.dashboard import profiles
from agent.github import thread_token
from agent.github import token as auth
from agent.tool_loaders import notion_mcp


@pytest.fixture
def thread_metadata(monkeypatch):
    metadata = {"visibility": "public", "owner_type": "user", "owner_login": "alice"}
    get_thread = AsyncMock(side_effect=lambda _id: {"metadata": metadata})
    monkeypatch.setattr(
        langgraph_sdk,
        "get_client",
        lambda: SimpleNamespace(threads=SimpleNamespace(get=get_thread)),
    )
    return metadata


@pytest.fixture
def credentials(monkeypatch):
    thread_token._GITHUB_TOKEN_CACHE.clear()
    user = AsyncMock(return_value="personal-token")
    monkeypatch.setattr(profiles, "get_valid_access_token", user)
    monkeypatch.setattr(profiles, "get_oauth_token_record", AsyncMock(return_value={}))
    monkeypatch.setattr(
        auth,
        "get_github_app_installation_token_with_expiry",
        AsyncMock(return_value=("bot-token", None)),
    )
    monkeypatch.setattr(auth, "is_bot_token_only_mode", lambda: False)
    return user


def config(source="dashboard", login="alice"):
    return {
        "configurable": {
            "thread_id": "thread-1",
            "source": source,
            "github_login": login,
            "visibility": "private",
            "owner_type": "system",
            "owner_login": login,
        }
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("source", ["dashboard", "slack", "linear", "github", "schedule"])
@pytest.mark.parametrize("visibility", ["public", None])
async def test_public_threads_never_resolve_personal_github_auth(
    source,
    visibility,
    thread_metadata,
    credentials,
):
    if visibility is None:
        thread_metadata.pop("visibility")
    else:
        thread_metadata["visibility"] = visibility
    thread_token.cache_github_token_for_thread(
        "thread-1", "old-personal-token", principal="login:alice"
    )
    assert await auth.resolve_github_token(config(source), "thread-1") == ("bot-token", None)
    assert thread_token.get_github_token(config(source)) == "bot-token"
    credentials.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("actor", ["alice", "bob"])
async def test_public_pr_is_opened_as_initiator(monkeypatch, thread_metadata, credentials, actor):
    opr = importlib.import_module("agent.tools.open_pull_request")
    monkeypatch.setattr("agent.run_config.get_config", lambda: config(login=actor))
    bot = AsyncMock(return_value="bot-token")
    monkeypatch.setattr(opr, "get_github_app_installation_token", bot)
    assert await opr._resolve_pr_author_token() == ("personal-token", "user")
    credentials.assert_awaited_once_with("alice")
    bot.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("actor", ["bob", "alice"])
async def test_public_pr_preserves_initiator_login_case(
    monkeypatch, thread_metadata, credentials, actor
):
    opr = importlib.import_module("agent.tools.open_pull_request")
    thread_metadata.update(owner_type="user", owner_login="Alice")
    credentials.side_effect = lambda login: "personal-token" if login == "Alice" else None
    monkeypatch.setattr("agent.run_config.get_config", lambda: config(login=actor))
    assert await opr._resolve_pr_author_token() == ("personal-token", "user")
    credentials.assert_awaited_once_with("Alice")


@pytest.mark.asyncio
@pytest.mark.parametrize("actor", ["Alice", "bob"])
async def test_older_public_owner_resolves_oauth_key_independently_of_participant(
    monkeypatch, thread_metadata, credentials, actor
):
    opr = importlib.import_module("agent.tools.open_pull_request")
    thread_metadata.pop("owner_type")
    credentials.side_effect = lambda login: "personal-token" if login == "Alice" else None
    monkeypatch.setattr("agent.run_config.get_config", lambda: config(login=actor))
    monkeypatch.setattr(
        profiles,
        "search_all_values",
        AsyncMock(return_value=[{"login": "Bob"}, {"login": "Alice"}]),
        raising=False,
    )

    assert await opr._resolve_pr_author_token() == ("personal-token", "user")
    credentials.assert_awaited_once_with("Alice")


@pytest.mark.asyncio
async def test_public_pr_does_not_fall_back_to_bot(monkeypatch, thread_metadata, credentials):
    opr = importlib.import_module("agent.tools.open_pull_request")
    monkeypatch.setattr("agent.run_config.get_config", lambda: config(login="bob"))
    credentials.return_value = None
    bot = AsyncMock(return_value="bot-token")
    monkeypatch.setattr(opr, "get_github_app_installation_token", bot)
    with pytest.raises(auth.GitHubUserAuthRequired):
        await opr._resolve_pr_author_token()
    bot.assert_not_awaited()


@pytest.mark.asyncio
async def test_system_pr_uses_bot_even_with_a_triggering_user(
    monkeypatch, thread_metadata, credentials
):
    opr = importlib.import_module("agent.tools.open_pull_request")
    thread_metadata.update(owner_type="system")
    thread_metadata.pop("owner_login")
    monkeypatch.setattr("agent.run_config.get_config", config)
    monkeypatch.setattr(
        opr, "get_github_app_installation_token", AsyncMock(return_value="bot-token")
    )
    assert await opr._resolve_pr_author_token() == ("bot-token", "bot")
    credentials.assert_not_awaited()


@pytest.mark.asyncio
async def test_user_owned_pr_requires_saved_initiator(monkeypatch, thread_metadata, credentials):
    opr = importlib.import_module("agent.tools.open_pull_request")
    thread_metadata.update(owner_type="user")
    thread_metadata.pop("owner_login")
    monkeypatch.setattr("agent.run_config.get_config", config)
    with pytest.raises(RuntimeError, match="owner"):
        await opr._resolve_pr_author_token()
    credentials.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("owner_type", [True, "unknown"])
async def test_invalid_owner_type_cannot_resolve_credentials(
    owner_type, thread_metadata, credentials
):
    thread_metadata["owner_type"] = owner_type
    with pytest.raises(RuntimeError, match="owner type"):
        await auth.resolve_github_token(config(), "thread-1")
    credentials.assert_not_awaited()


@pytest.mark.asyncio
async def test_system_thread_cannot_use_private_credentials(thread_metadata, credentials):
    thread_metadata.update(owner_type="system", visibility="private")
    with pytest.raises(RuntimeError, match="System threads"):
        await auth.resolve_github_token(config(), "thread-1")
    credentials.assert_not_awaited()


@pytest.mark.asyncio
async def test_public_notion_tools_do_not_load_personal_credentials(monkeypatch, thread_metadata):
    monkeypatch.setattr("agent.run_config.get_config", config)
    get_token = AsyncMock(return_value="notion-token")
    monkeypatch.setattr(notion_mcp, "get_notion_access_token", get_token)
    monkeypatch.setattr(notion_mcp, "_build_mcp_tools", AsyncMock(return_value=[]))
    assert await notion_mcp.load_notion_tools("alice") == []
    get_token.assert_not_awaited()


@pytest.mark.asyncio
async def test_cached_notion_tool_cannot_run_in_public_thread(monkeypatch, thread_metadata):
    from langchain_core.tools import StructuredTool

    async def search(query: str) -> str:
        return query

    tool = notion_mcp._refreshing_tool(
        StructuredTool.from_function(
            coroutine=search, name="notion_search", description="Search Notion"
        )
    )
    monkeypatch.setattr("agent.run_config.get_config", config)
    monkeypatch.setattr(notion_mcp, "resolve_participant", AsyncMock(return_value="alice"))
    get_token = AsyncMock(return_value="notion-token")
    monkeypatch.setattr(notion_mcp, "get_notion_access_token", get_token)
    monkeypatch.setattr(notion_mcp, "_build_mcp_tools", AsyncMock(return_value=[]))
    with pytest.raises(RuntimeError, match="private"):
        await tool.ainvoke({"on_behalf_of": "alice", "query": "roadmap"})
    get_token.assert_not_awaited()


@pytest.mark.asyncio
async def test_private_thread_uses_its_owners_auth(thread_metadata, credentials):
    thread_metadata["visibility"] = "private"
    credentials.side_effect = lambda login: "personal-token" if login == "Alice" else None
    assert await auth.resolve_github_token(config(login="Alice"), "thread-1") == (
        "personal-token",
        None,
    )
    credentials.assert_awaited_once_with("Alice")


@pytest.mark.asyncio
@pytest.mark.parametrize("visibility", [None, "unknown", True])
async def test_invalid_visibility_does_not_enable_personal_credentials(
    visibility, thread_metadata, credentials
):
    thread_metadata["visibility"] = visibility
    with pytest.raises(RuntimeError, match="visibility"):
        await auth.resolve_github_token(config(), "thread-1")
    credentials.assert_not_awaited()


@pytest.mark.asyncio
async def test_public_thread_requires_app_auth_even_when_user_token_exists(
    monkeypatch, thread_metadata, credentials
):
    monkeypatch.setattr(
        auth, "get_github_app_installation_token_with_expiry", AsyncMock(return_value=(None, None))
    )
    with pytest.raises(RuntimeError, match="GitHub App"):
        await auth.resolve_github_token(config(), "thread-1")
    credentials.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("owner", [None, "", "bob"])
async def test_private_thread_rejects_missing_or_different_owner(
    owner,
    thread_metadata,
    credentials,
):
    thread_metadata.update(visibility="private", owner_login=owner)
    with pytest.raises(RuntimeError, match="owner"):
        await auth.resolve_github_token(config(), "thread-1")
    credentials.assert_not_awaited()


@pytest.mark.asyncio
async def test_private_thread_does_not_fall_back_to_bot(
    monkeypatch,
    thread_metadata,
    credentials,
):
    thread_metadata["visibility"] = "private"
    credentials.return_value = None
    monkeypatch.setattr(auth, "is_bot_token_only_mode", lambda: True)
    with pytest.raises(auth.GitHubUserAuthRequired):
        await auth.resolve_github_token(config(), "thread-1")


@pytest.mark.asyncio
async def test_metadata_lookup_failure_cannot_use_personal_credentials(
    monkeypatch,
    credentials,
):
    monkeypatch.setattr(
        langgraph_sdk,
        "get_client",
        lambda: SimpleNamespace(
            threads=SimpleNamespace(get=AsyncMock(side_effect=RuntimeError("store unavailable")))
        ),
    )
    with pytest.raises(RuntimeError, match="store unavailable"):
        await auth.resolve_github_token(config(), "thread-1")
    credentials.assert_not_awaited()


@pytest.mark.asyncio
async def test_github_webhook_context_always_uses_workspace_bot(
    monkeypatch, credentials, thread_metadata
):
    from agent.webhooks import common

    monkeypatch.setattr(common, "is_bot_token_only_mode", lambda: False)
    monkeypatch.setattr(
        common,
        "get_github_app_installation_token_with_expiry",
        AsyncMock(return_value=("bot-token", None)),
    )
    personal = AsyncMock(return_value={"token": "personal-token"})
    monkeypatch.setattr(auth, "resolve_github_token_from_email", personal)
    assert (
        await common.get_or_resolve_thread_github_token("thread-1", "alice@example.com")
        == "bot-token"
    )
    personal.assert_not_awaited()
