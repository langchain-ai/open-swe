from types import SimpleNamespace
from unittest.mock import AsyncMock

import langgraph_sdk
import pytest


@pytest.fixture
def scope(monkeypatch, fake_store):
    metadata = {
        "owner_type": "system",
        "visibility": "public",
        "source_context": {
            "slack_thread": {
                "channel_id": "C123",
                "thread_ts": "123",
                "team_id": "T123",
                "triggering_bot_id": "B123",
                "triggering_user_id": "U123",
            }
        },
    }
    monkeypatch.setattr(
        langgraph_sdk,
        "get_client",
        lambda: SimpleNamespace(
            threads=SimpleNamespace(get=AsyncMock(side_effect=lambda _: {"metadata": metadata}))
        ),
    )
    fake_store.seed(
        ["allowed_slack_bots"],
        "T123:B123",
        {
            "team_id": "T123",
            "bot_id": "B123",
            "user_id": "U123",
            "name": "Release bot",
            "created_by": "alice",
            "created_at": "2026-09-11",
        },
    )
    return metadata


async def test_system_run_omits_admin_identity(scope):
    from agent.slack.bot_authorization import bot_thread_configurable

    result = await bot_thread_configurable(
        "thread",
        {
            "environment": "other",
            "github_login": "alice",
            "user_email": "alice@example.com",
            "admin_thread": True,
            "source": "dashboard",
        },
    )
    assert result["environment"] == "other"
    assert result["slack_bot_thread"] is True
    assert not result.get("github_login")
    assert not result.get("user_email")
    assert not result.get("admin_thread")


async def test_bot_cannot_dispatch_into_a_human_thread(scope):
    from agent.slack.bot_authorization import bot_thread_configurable

    scope.clear()
    scope.update(owner_type="user", owner_login="alice", visibility="private")
    with pytest.raises(RuntimeError):
        await bot_thread_configurable(
            "thread",
            {
                "source": "slack",
                "slack_thread": {"triggering_bot_id": "B123", "team_id": "T123"},
            },
        )


async def test_runtime_admin_actor_cannot_elevate_system_tools(monkeypatch, scope):
    from agent.tools.admin_gate import require_admin
    from agent.tools.threads import _actor

    config = {
        "configurable": {"thread_id": "thread", "source": "dashboard", "github_login": "alice"}
    }
    monkeypatch.setenv("CONFIGURED_ADMINS", "alice")
    monkeypatch.setattr("agent.run_config.get_config", lambda: config)
    monkeypatch.setattr("agent.tools.threads.get_config", lambda: config)
    assert await require_admin("manage environments") is not None
    assert await _actor() is None


async def test_bot_upsert_cannot_change_competing_human_thread(monkeypatch, scope):
    from agent.source_context import SourceContext
    from agent.webhooks import common

    human = {
        "owner_type": "user",
        "visibility": "public",
        "environment": "human-env",
        "created_at_ms": 1,
    }
    update = AsyncMock()
    client = SimpleNamespace(
        threads=SimpleNamespace(get=AsyncMock(return_value={"metadata": human}), update=update)
    )
    monkeypatch.setattr(common, "get_client", lambda **kwargs: client)
    result = await common.upsert_agent_thread_metadata(
        "thread",
        source="slack",
        owner_type="system",
        environment="backend",
        source_context=SourceContext.from_metadata(scope),
    )
    assert result is False
    update.assert_not_awaited()
    assert human["environment"] == "human-env"


@pytest.mark.parametrize("change", ["human", "private", "removed", "other-team"])
async def test_invalid_or_revoked_bot_blocks(scope, fake_store, change):
    from agent.slack.bot_authorization import authorize_bot_thread

    if change == "human":
        scope["owner_type"] = "user"
    elif change == "private":
        scope["visibility"] = "private"
    elif change == "removed":
        await fake_store.delete_item(["allowed_slack_bots"], "T123:B123")
    else:
        scope["source_context"]["slack_thread"]["team_id"] = "TOTHER"
    with pytest.raises(RuntimeError):
        await authorize_bot_thread("thread")


async def test_automation_keeps_existing_authorization(scope):
    from agent.slack.bot_authorization import authorize_bot_thread

    scope.clear()
    scope.update(owner_type="system", visibility="public", source="schedule")
    assert await authorize_bot_thread("thread") is False


async def test_old_environment_fields_do_not_limit_bot_access(scope):
    from agent.slack.bot_authorization import authorize_bot_thread

    scope.update(environment="deleted", system_repositories=["old/repo"])
    assert await authorize_bot_thread("thread") is True


async def test_move_preserves_bot_authorization(scope):
    from agent.slack.bot_authorization import validate_bot_thread
    from agent.slack.tools.move_thread import _new_slack_context

    scope["source_context"]["slack_thread"] = _new_slack_context(
        scope["source_context"]["slack_thread"], "CNEW", "456"
    )
    assert scope["source_context"]["slack_thread"]["triggering_bot_id"] == "B123"
    assert await validate_bot_thread(scope) is True


async def test_bot_uses_installation_access_without_repo_restrictions(monkeypatch, scope):
    from agent.slack import bot_authorization

    mint = AsyncMock(return_value=("app-token", "expiry"))
    monkeypatch.setattr(bot_authorization, "get_github_app_installation_token_with_expiry", mint)
    assert await bot_authorization.bot_installation_token("thread") == ("app-token", "expiry")
    mint.assert_awaited_once_with()


@pytest.mark.parametrize("entrypoint", ["run", "pr", "proxy", "webhook"])
async def test_execution_paths_use_app_identity(monkeypatch, scope, entrypoint):
    import importlib

    from agent.slack import bot_authorization

    mint = AsyncMock(return_value=("app-token", "expiry"))
    monkeypatch.setattr(bot_authorization, "get_github_app_installation_token_with_expiry", mint)
    config = {"configurable": {"thread_id": "thread", "source": "slack", "github_login": "alice"}}
    monkeypatch.setattr("agent.run_config.get_config", lambda: config)
    if entrypoint == "run":
        from agent.github import token

        monkeypatch.setattr(
            token,
            "get_github_app_installation_token_with_expiry",
            AsyncMock(return_value=("broad-token", None)),
        )
        result = await token.resolve_github_token(config, "thread")
    elif entrypoint == "pr":
        pr = importlib.import_module("agent.tools.open_pull_request")
        monkeypatch.setattr(
            pr, "get_github_app_installation_token", AsyncMock(return_value="broad-token")
        )
        result = await pr._resolve_pr_author_token()
    elif entrypoint == "proxy":
        from agent.sandboxes import lifecycle

        result = await lifecycle._resolve_proxy_token("broad-token", thread_id="thread")
    else:
        from agent.webhooks import common

        monkeypatch.setattr(
            common,
            "get_github_app_installation_token_with_expiry",
            AsyncMock(return_value=("broad-token", None)),
        )
        result = (await common.get_or_resolve_thread_github_token("thread", "alice@example.com"),)
    assert result[0] == "app-token"
