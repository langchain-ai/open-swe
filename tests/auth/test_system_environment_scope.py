from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx2
import langgraph_sdk
import pytest


@pytest.fixture
def scope(monkeypatch, fake_store):
    metadata = {
        "owner_type": "system",
        "visibility": "public",
        "environment": "backend",
        "system_repositories": ["langchain-ai/open-swe"],
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
        ["environments"],
        "backend",
        {
            "slug": "backend",
            "repos": ["langchain-ai/open-swe"],
        },
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
            "environment": "backend",
            "created_at": "2026-09-11",
        },
    )
    return metadata


async def test_scope_is_saved_not_supplied_by_actor(scope):
    from agent.github.system_scope import system_repository_scope

    assert await system_repository_scope("thread") == ["langchain-ai/open-swe"]


async def test_system_run_pins_environment_and_omits_admin_identity(scope):
    from agent.github.system_scope import system_configurable

    result = await system_configurable(
        "thread",
        {
            "environment": "other",
            "github_login": "alice",
            "user_email": "alice@example.com",
            "admin_thread": True,
            "source": "dashboard",
        },
    )
    assert result["environment"] == "backend"
    assert not result.get("github_login")
    assert not result.get("user_email")
    assert not result.get("admin_thread")


async def test_bot_cannot_dispatch_into_a_human_thread(scope):
    from agent.github.system_scope import system_configurable

    scope.clear()
    scope.update(owner_type="user", owner_login="alice", visibility="private")
    with pytest.raises(RuntimeError):
        await system_configurable(
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
        system_repositories=scope["system_repositories"],
    )
    assert result is False
    update.assert_not_awaited()
    assert human["environment"] == "human-env"


async def test_bot_upsert_accepts_equivalent_repository_scope(monkeypatch, scope):
    from agent.source_context import SourceContext
    from agent.webhooks import common

    scope["system_repositories"] = ["langchain-ai/open-swe", "langchain-ai/langgraph"]
    update = AsyncMock()
    client = SimpleNamespace(
        threads=SimpleNamespace(get=AsyncMock(return_value={"metadata": scope}), update=update)
    )
    monkeypatch.setattr(common, "get_client", lambda **kwargs: client)
    monkeypatch.setattr(
        common, "_source_context_with_slack_permalink", AsyncMock(side_effect=lambda ctx, _: ctx)
    )
    assert await common.upsert_agent_thread_metadata(
        "thread",
        source="slack",
        owner_type="system",
        environment="backend",
        source_context=SourceContext.from_metadata(scope),
        system_repositories=["LangChain-AI/LangGraph", "LangChain-AI/Open-SWE"],
    )
    update.assert_awaited_once()
    assert "system_repositories" not in update.call_args.kwargs["metadata"]


@pytest.mark.parametrize("change", ["empty", "missing", "human", "private", "removed", "changed"])
async def test_invalid_or_changed_scope_blocks(scope, fake_store, change):
    from agent.github.system_scope import system_repository_scope

    if change == "empty":
        scope["system_repositories"] = []
    elif change == "missing":
        scope.pop("environment")
    elif change == "human":
        scope["owner_type"] = "user"
    elif change == "private":
        scope["visibility"] = "private"
    elif change == "removed":
        await fake_store.delete_item(["allowed_slack_bots"], "T123:B123")
    else:
        fake_store.seed(["environments"], "backend", {"slug": "backend", "repos": ["org/other"]})
    with pytest.raises(RuntimeError):
        await system_repository_scope("thread")


async def test_unscoped_system_automation_keeps_existing_behavior(scope):
    from agent.github.system_scope import system_repository_scope

    scope.clear()
    scope.update(owner_type="system", visibility="public", source="schedule")
    assert await system_repository_scope("thread") is None


async def test_scope_without_bot_provenance_blocks(scope):
    from agent.github.system_scope import system_repository_scope

    scope["source_context"] = {}
    with pytest.raises(RuntimeError):
        await system_repository_scope("thread")


async def test_move_preserves_bot_authorization(scope):
    from agent.github.system_scope import validate_system_scope
    from agent.slack.tools.move_thread import _new_slack_context

    scope["source_context"]["slack_thread"] = _new_slack_context(
        scope["source_context"]["slack_thread"], "CNEW", "456"
    )
    assert scope["source_context"]["slack_thread"]["triggering_bot_id"] == "B123"
    assert await validate_system_scope(scope) == ["langchain-ai/open-swe"]


async def test_system_environment_lookup_never_falls_back(monkeypatch, scope):
    from agent.dashboard.environments import ENVIRONMENTS
    from agent.sandboxes import lifecycle

    monkeypatch.setattr(ENVIRONMENTS, "get", AsyncMock(side_effect=RuntimeError("store offline")))
    boot = AsyncMock()
    monkeypatch.setattr(lifecycle.SandboxCreateConfig, "boot", boot)
    with pytest.raises(RuntimeError, match="store offline"):
        await lifecycle._create_sandbox_with_proxy(thread_id="thread", environment_slug="backend")
    boot.assert_not_awaited()


async def test_token_is_restricted_to_verified_repository_ids(monkeypatch, scope):
    from agent.github import system_scope

    mint = AsyncMock(side_effect=[("lookup-token", None), ("scoped-token", "expiry")])
    monkeypatch.setattr(system_scope, "get_github_app_installation_token_with_expiry", mint)
    async_client = httpx2.AsyncClient
    monkeypatch.setattr(
        system_scope.httpx2,
        "AsyncClient",
        lambda **kwargs: async_client(
            **kwargs,
            transport=httpx2.MockTransport(
                lambda _: httpx2.Response(
                    200,
                    json={
                        "repositories": [
                            {"id": 10, "full_name": "langchain-ai/open-swe"},
                            {"id": 20, "full_name": "other-org/open-swe"},
                        ],
                    },
                )
            ),
        ),
    )
    assert await system_scope.system_installation_token("thread") == ("scoped-token", "expiry")
    assert mint.await_args.kwargs == {"repository_ids": [10]}


async def test_unavailable_repository_never_falls_back(monkeypatch, scope):
    from agent.github import system_scope

    mint = AsyncMock(return_value=("lookup-token", None))
    monkeypatch.setattr(system_scope, "get_github_app_installation_token_with_expiry", mint)
    async_client = httpx2.AsyncClient
    monkeypatch.setattr(
        system_scope.httpx2,
        "AsyncClient",
        lambda **kwargs: async_client(
            **kwargs,
            transport=httpx2.MockTransport(
                lambda _: httpx2.Response(
                    200,
                    json={
                        "repositories": [{"id": 20, "full_name": "other-org/open-swe"}],
                    },
                )
            ),
        ),
    )
    with pytest.raises(RuntimeError, match="repository"):
        await system_scope.system_installation_token("thread")
    assert mint.await_count == 1


@pytest.mark.parametrize("entrypoint", ["run", "pr", "proxy", "webhook"])
async def test_execution_paths_use_system_scope(monkeypatch, scope, entrypoint):
    import importlib

    from agent.github import system_scope

    scoped = AsyncMock(return_value=("scoped-token", "expiry"))
    monkeypatch.setattr(system_scope, "system_installation_token", scoped)
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
    assert result[0] == "scoped-token"
