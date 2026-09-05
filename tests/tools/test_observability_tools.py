from typing import cast
from unittest.mock import AsyncMock, call, patch

import pytest
from langchain_core.runnables import RunnableConfig

from agent import server
from agent.dashboard.team_credentials import LangSmithCredentials
from agent.tool_loaders import langsmith as langsmith_tools


@pytest.fixture(autouse=True)
def _resolve_participant():
    """Tools resolve the acting participant at call time; tests act as "alice"."""
    with (
        patch.object(langsmith_tools, "resolve_participant", AsyncMock(return_value="alice")),
    ):
        yield


@pytest.mark.asyncio
async def test_load_langsmith_tools_empty_when_not_connected() -> None:
    with (
        patch.object(
            langsmith_tools, "get_user_langsmith_credentials", AsyncMock(return_value=None)
        ),
        patch.object(
            langsmith_tools, "get_team_langsmith_credentials", AsyncMock(return_value=None)
        ),
    ):
        assert await langsmith_tools.load_langsmith_tools("alice") == []


@pytest.mark.asyncio
async def test_load_langsmith_tools_names() -> None:
    creds = LangSmithCredentials(api_key="k", endpoint="https://api.smith.langchain.com")
    team_credentials = AsyncMock()
    with (
        patch.object(
            langsmith_tools, "get_user_langsmith_credentials", AsyncMock(return_value=creds)
        ),
        patch.object(langsmith_tools, "get_team_langsmith_credentials", team_credentials),
    ):
        tools = await langsmith_tools.load_langsmith_tools("alice")
    assert {t.name for t in tools} == {"langsmith_get_trace", "langsmith_list_runs"}
    team_credentials.assert_not_awaited()


@pytest.mark.asyncio
async def test_langsmith_get_trace_serializes() -> None:
    creds = LangSmithCredentials(api_key="k", endpoint="https://api.smith.langchain.com")

    class _Run:
        id = "run-1"
        name = "my-run"
        run_type = "chain"
        status = "success"
        error = None
        start_time = "2024-01-01"
        end_time = "2024-01-02"
        trace_id = "trace-1"
        inputs = {"a": 1}
        outputs = {"b": 2}

    class _FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return None

        async def read_run(self, run_id: str):
            assert run_id == "run-1"
            return _Run()

    tools = langsmith_tools._make_tools(allow_team=True)
    get_trace = next(t for t in tools if t.name == "langsmith_get_trace")
    with (
        patch.object(langsmith_tools, "_creds_for", AsyncMock(return_value=creds)),
        patch.object(langsmith_tools, "_client", lambda _c: _FakeClient()),
    ):
        result = await get_trace.ainvoke({"on_behalf_of": "octo", "run_id": "run-1"})
    assert result["success"] is True
    assert result["run"]["name"] == "my-run"
    assert result["run"]["trace_id"] == "trace-1"


@pytest.mark.asyncio
async def test_langsmith_list_runs_caps_limit() -> None:
    creds = LangSmithCredentials(api_key="k", endpoint="https://api.smith.langchain.com")
    captured: dict[str, object] = {}

    class _FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return None

        async def list_runs(self, *, project_name: str, filter, limit: int):
            captured["limit"] = limit
            captured["project_name"] = project_name
            return
            yield

    tools = langsmith_tools._make_tools(allow_team=True)
    list_runs = next(t for t in tools if t.name == "langsmith_list_runs")
    with (
        patch.object(langsmith_tools, "_creds_for", AsyncMock(return_value=creds)),
        patch.object(langsmith_tools, "_client", lambda _c: _FakeClient()),
    ):
        result = await list_runs.ainvoke(
            {"on_behalf_of": "octo", "project_name": "p", "limit": 9999}
        )
    assert result["success"] is True
    assert captured["limit"] == langsmith_tools._MAX_LIST_RUNS


@pytest.mark.asyncio
async def test_load_observability_tools_skipped_when_unauthorized() -> None:
    with (
        patch.object(server, "load_langsmith_tools", AsyncMock(return_value=["ls"])),
    ):
        assert await server._load_observability_tools(authorized=False, profile_login=None) == []


@pytest.mark.asyncio
async def test_load_observability_tools_loaded_when_authorized() -> None:
    with (
        patch.object(server, "load_langsmith_tools", AsyncMock(return_value=["ls"])),
    ):
        assert await server._load_observability_tools(authorized=True, profile_login="alice") == [
            "ls",
        ]


@pytest.mark.asyncio
async def test_observability_authorized_gates_on_admin(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CONFIGURED_ADMINS", "admin@example.com")
    monkeypatch.delenv("OBSERVABILITY_AUTHORIZED_EMAILS", raising=False)
    monkeypatch.setattr(server, "email_for_login", AsyncMock(return_value=None))

    admin_config = cast(RunnableConfig, {"configurable": {"user_email": "admin@example.com"}})
    other_config = cast(RunnableConfig, {"configurable": {"user_email": "attacker@example.com"}})

    assert await server._observability_authorized(admin_config, None) is True
    assert await server._observability_authorized(other_config, None) is False


@pytest.mark.asyncio
async def test_observability_authorized_allowlist(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CONFIGURED_ADMINS", "")
    monkeypatch.setenv("OBSERVABILITY_AUTHORIZED_EMAILS", "trusted@example.com")
    monkeypatch.setattr(server, "email_for_login", AsyncMock(return_value=None))

    config = cast(RunnableConfig, {"configurable": {"user_email": "trusted@example.com"}})
    assert await server._observability_authorized(config, None) is True


@pytest.mark.asyncio
async def test_allowed_org_member(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ALLOWED_GITHUB_ORGS", "primary,secondary")
    membership = AsyncMock(side_effect=[False, True])
    monkeypatch.setattr(server, "is_user_active_org_member", membership)

    config = cast(RunnableConfig, {"configurable": {"github_login": "dev"}})
    assert await server._allowed_org_member(config, "dev") is True
    assert membership.await_args_list == [call("dev", "primary"), call("dev", "secondary")]


@pytest.mark.asyncio
async def test_observability_authorized_resolves_login_email(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CONFIGURED_ADMINS", "dev@example.com")
    monkeypatch.delenv("OBSERVABILITY_AUTHORIZED_EMAILS", raising=False)
    monkeypatch.setattr(
        server,
        "email_for_login",
        AsyncMock(side_effect=lambda login: "dev@example.com" if login else None),
    )

    config = cast(RunnableConfig, {"configurable": {"github_login": "dev"}})
    assert await server._observability_authorized(config, "dev") is True


@pytest.mark.asyncio
async def test_observability_authorized_accepts_admin_login(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CONFIGURED_ADMINS", "dev")
    monkeypatch.delenv("OBSERVABILITY_AUTHORIZED_EMAILS", raising=False)
    monkeypatch.setattr(server, "email_for_login", AsyncMock(return_value=None))

    config = cast(RunnableConfig, {"configurable": {"github_login": "dev"}})
    assert await server._observability_authorized(config, "dev") is True


@pytest.mark.asyncio
async def test_org_membership_lookup_is_cached(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ALLOWED_GITHUB_ORGS", "primary")
    membership = AsyncMock(return_value=True)
    monkeypatch.setattr(server, "is_user_active_org_member", membership)

    config = cast(RunnableConfig, {"configurable": {"github_login": "dev"}})
    assert await server._cached_allowed_org_member(config, "dev") is True
    assert await server._cached_allowed_org_member(config, "dev") is True
    assert membership.await_count == 1


@pytest.mark.asyncio
async def test_observability_tools_reuse_the_credential_lookup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CONFIGURED_ADMINS", "admin@example.com")
    monkeypatch.delenv("OBSERVABILITY_AUTHORIZED_EMAILS", raising=False)
    monkeypatch.setattr(server, "email_for_login", AsyncMock(return_value=None))
    langsmith = AsyncMock(return_value=["ls"])
    monkeypatch.setattr(server, "load_langsmith_tools", langsmith)

    config = cast(RunnableConfig, {"configurable": {"user_email": "admin@example.com"}})
    assert await server._observability_tools_for(config, "alice") == ["ls"]
    assert await server._observability_tools_for(config, "alice") == ["ls"]
    assert langsmith.await_count == 1


@pytest.mark.asyncio
async def test_observability_authorization_is_rechecked_per_run(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A cached tool list must never carry team access into an unauthorized run."""
    monkeypatch.setenv("CONFIGURED_ADMINS", "admin@example.com")
    monkeypatch.delenv("OBSERVABILITY_AUTHORIZED_EMAILS", raising=False)
    monkeypatch.setenv("ALLOWED_GITHUB_ORGS", "primary")
    monkeypatch.setattr(server, "email_for_login", AsyncMock(return_value=None))
    monkeypatch.setattr(server, "is_user_active_org_member", AsyncMock(return_value=False))
    monkeypatch.setattr(
        server,
        "load_langsmith_tools",
        AsyncMock(side_effect=lambda _login, allow_team=True: ["team"] if allow_team else []),
    )

    admin = cast(RunnableConfig, {"configurable": {"user_email": "admin@example.com"}})
    attacker = cast(RunnableConfig, {"configurable": {"user_email": "attacker@example.com"}})

    assert await server._observability_tools_for(admin, "alice") == ["team"]
    assert await server._observability_tools_for(attacker, "alice") == []
