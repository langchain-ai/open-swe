from types import SimpleNamespace
from typing import Any, Literal, cast
from unittest.mock import AsyncMock, call, patch

import pytest
from langchain_core.runnables import RunnableConfig
from langchain_core.tools import StructuredTool

from agent import server
from agent.dashboard.team_credentials import DatadogCredentials, LangSmithCredentials
from agent.tool_loaders import datadog_mcp, notion_mcp
from agent.tool_loaders import langsmith as langsmith_tools


@pytest.fixture(autouse=True)
def _resolve_participant():
    """Tools resolve the acting participant at call time; tests act as "alice"."""
    with (
        patch.object(notion_mcp, "resolve_participant", AsyncMock(return_value="alice")),
        patch.object(langsmith_tools, "resolve_participant", AsyncMock(return_value="alice")),
    ):
        yield


@pytest.mark.asyncio
async def test_load_datadog_tools_empty_when_not_connected() -> None:
    with patch.object(datadog_mcp, "get_datadog_credentials", AsyncMock(return_value=None)):
        assert await datadog_mcp.load_datadog_tools() == []


@pytest.mark.asyncio
async def test_load_datadog_tools_degrades_on_error() -> None:
    creds = DatadogCredentials(site="datadoghq.com", api_key="a", app_key="b")
    with (
        patch.object(datadog_mcp, "get_datadog_credentials", AsyncMock(return_value=creds)),
        patch.object(datadog_mcp, "_build_mcp_tools", AsyncMock(side_effect=RuntimeError("boom"))),
    ):
        assert await datadog_mcp.load_datadog_tools() == []


@pytest.mark.asyncio
async def test_load_datadog_tools_returns_tools() -> None:
    creds = DatadogCredentials(site="datadoghq.com", api_key="a", app_key="b")
    sentinel = ["tool-a", "tool-b"]
    with (
        patch.object(datadog_mcp, "get_datadog_credentials", AsyncMock(return_value=creds)),
        patch.object(datadog_mcp, "_build_mcp_tools", AsyncMock(return_value=sentinel)),
    ):
        assert await datadog_mcp.load_datadog_tools() == sentinel


@pytest.mark.asyncio
async def test_load_notion_tools_empty_when_not_connected() -> None:
    with patch.object(notion_mcp, "get_notion_access_token", AsyncMock(return_value=None)):
        assert await notion_mcp.load_notion_tools("alice") == []


@pytest.mark.asyncio
async def test_load_notion_tools_degrades_on_error() -> None:
    with (
        patch.object(notion_mcp, "get_notion_access_token", AsyncMock(return_value="tok")),
        patch.object(notion_mcp, "_build_mcp_tools", AsyncMock(side_effect=RuntimeError("boom"))),
    ):
        assert await notion_mcp.load_notion_tools("alice") == []


def _notion_tool_for_token(
    token: str,
    response_format: Literal["content", "content_and_artifact"] = "content",
) -> StructuredTool:
    async def notion_search(query: str):
        """Search Notion."""
        content = {"query": query, "token": token}
        if response_format == "content_and_artifact":
            return content, {"artifact_token": token}
        return content

    return StructuredTool.from_function(
        coroutine=notion_search,
        name="notion_search",
        description="Search Notion",
        response_format=response_format,
    )


@pytest.mark.asyncio
async def test_load_notion_tools_returns_wrappers() -> None:
    discovered = _notion_tool_for_token("initial-token")
    with (
        patch.object(notion_mcp, "get_notion_access_token", AsyncMock(return_value="tok")),
        patch.object(notion_mcp, "_build_mcp_tools", AsyncMock(return_value=[discovered])),
    ):
        tools = await notion_mcp.load_notion_tools("alice")
    assert len(tools) == 1
    assert tools[0].name == "notion_search"
    assert tools[0].description == discovered.description
    schema = cast("dict[str, Any]", tools[0].args_schema)
    assert "query" in schema["properties"]
    assert "on_behalf_of" in schema["properties"]
    assert "on_behalf_of" in schema["required"]
    assert tools[0].response_format == "content"


@pytest.mark.asyncio
async def test_notion_wrapper_normalizes_content_and_artifact_tools() -> None:
    get_token = AsyncMock(side_effect=["initial-token", "fresh-token"])
    build_tools = AsyncMock(
        side_effect=lambda token: [_notion_tool_for_token(token, "content_and_artifact")]
    )
    with (
        patch.object(notion_mcp, "get_notion_access_token", get_token),
        patch.object(notion_mcp, "_build_mcp_tools", build_tools),
    ):
        tools = await notion_mcp.load_notion_tools("alice")
        assert tools[0].response_format == "content"
        result = await tools[0].ainvoke({"on_behalf_of": "alice", "query": "roadmap"})
    assert result == {"query": "roadmap", "token": "fresh-token"}


@pytest.mark.asyncio
async def test_notion_wrapper_refreshes_token_at_call_time() -> None:
    get_token = AsyncMock(side_effect=["initial-token", "fresh-token"])
    build_tools = AsyncMock(side_effect=lambda token: [_notion_tool_for_token(token)])
    with (
        patch.object(notion_mcp, "get_notion_access_token", get_token),
        patch.object(notion_mcp, "_build_mcp_tools", build_tools),
    ):
        tools = await notion_mcp.load_notion_tools("alice")
        result = await tools[0].ainvoke({"on_behalf_of": "alice", "query": "roadmap"})
    assert result == {"query": "roadmap", "token": "fresh-token"}
    assert get_token.await_count == 2
    assert [call.args[0] for call in build_tools.await_args_list] == [
        "initial-token",
        "fresh-token",
    ]


@pytest.mark.asyncio
async def test_notion_wrapper_fails_when_token_missing_at_call_time() -> None:
    get_token = AsyncMock(side_effect=["initial-token", None])
    build_tools = AsyncMock(return_value=[_notion_tool_for_token("initial-token")])
    with (
        patch.object(notion_mcp, "get_notion_access_token", get_token),
        patch.object(notion_mcp, "_build_mcp_tools", build_tools),
    ):
        tools = await notion_mcp.load_notion_tools("alice")
        with pytest.raises(RuntimeError, match="Notion MCP authorization unavailable"):
            await tools[0].ainvoke({"on_behalf_of": "alice", "query": "roadmap"})
    assert build_tools.await_count == 1


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
        trace_id = "run-1"
        session_id = "external-project"
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

        async def read_project(self, **kwargs):
            return SimpleNamespace(name="external-project")

    tools = langsmith_tools._make_tools(allow_team=True)
    get_trace = next(t for t in tools if t.name == "langsmith_get_trace")
    with (
        patch.object(langsmith_tools, "_creds_for", AsyncMock(return_value=creds)),
        patch.object(langsmith_tools, "langsmith_client", lambda _c: _FakeClient()),
    ):
        result = await get_trace.ainvoke({"on_behalf_of": "octo", "run_id": "run-1"})
    assert result["success"] is True
    assert result["run"]["name"] == "my-run"
    assert result["run"]["trace_id"] == "run-1"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("target", "caller", "allowed"),
    [
        ({"visibility": "private", "owner_login": "alice"}, "public", False),
        ({"visibility": "private", "owner_login": "alice"}, "private", True),
        ({"visibility": "private", "owner_login": "bob"}, "private", False),
        ({"visibility": "public"}, "public", True),
        (None, "private", False),
    ],
)
async def test_langsmith_trace_privacy(target, caller, allowed) -> None:
    root = SimpleNamespace(
        id="root",
        trace_id="root",
        extra={"metadata": {"thread_id": "target"}},
        inputs={"secret": "private content"},
    )
    run = SimpleNamespace(id="child", trace_id="root", inputs=root.inputs)
    client = AsyncMock()
    client.__aenter__.return_value = client

    async def authorize(thread_id, login):
        if thread_id == "caller":
            return {"visibility": caller, "owner_login": "alice"}
        if target is None or target.get("owner_login", login) != login:
            raise ValueError("not found")
        return target

    async def list_runs(**kwargs):
        yield run

    client.list_runs = list_runs
    client.read_run.side_effect = lambda run_id: root if run_id == "root" else run
    tools = {t.name: t for t in langsmith_tools._make_tools(allow_team=True)}
    with (
        patch.object(langsmith_tools, "_creds_for", AsyncMock()),
        patch.object(langsmith_tools, "langsmith_client", return_value=client),
        patch.object(langsmith_tools, "_authorized_thread_metadata", side_effect=authorize),
        patch.object(
            langsmith_tools, "get_config", return_value={"configurable": {"thread_id": "caller"}}
        ),
    ):
        result = await tools["langsmith_get_trace"].ainvoke(
            {"on_behalf_of": "alice", "run_id": run.id}
        )
        assert result["success"] is allowed
        if not allowed:
            assert "private content" not in str(result)
        result = await tools["langsmith_list_runs"].ainvoke(
            {"on_behalf_of": "alice", "project_name": "anything", "filter": "anything"}
        )
        assert len(result["runs"]) == int(allowed)


@pytest.mark.asyncio
async def test_langsmith_private_children_and_missing_caller_are_denied() -> None:
    private = SimpleNamespace(
        id="private", trace_id="private", extra={"metadata": {"thread_id": "private"}}
    )
    public = SimpleNamespace(
        id="public",
        trace_id="public",
        extra={"metadata": {"thread_id": "public"}},
        child_runs=[private],
    )
    threads = AsyncMock()
    threads.threads.get.side_effect = lambda thread_id: {
        "metadata": {"source": "dashboard", "visibility": thread_id, "owner_login": "alice"}
    }
    with (
        patch.object(langsmith_tools, "langgraph_client", return_value=threads),
        patch.object(langsmith_tools, "get_config", return_value={}),
    ):
        assert not await langsmith_tools._run_is_readable(AsyncMock(), private, "alice")
        assert not await langsmith_tools._run_is_readable(AsyncMock(), private, "bob")
        assert not await langsmith_tools._run_is_readable(AsyncMock(), public, "alice")
        public.child_runs = []
        assert await langsmith_tools._run_is_readable(AsyncMock(), public, "alice")


@pytest.mark.asyncio
@pytest.mark.parametrize("project", [None, "application", "external"])
async def test_langsmith_unidentified_trace_fails_closed(project) -> None:
    client = AsyncMock()
    client.read_project.return_value = SimpleNamespace(name=project)
    run = SimpleNamespace(id="root", trace_id="root", session_id="project")
    with patch.object(langsmith_tools, "tracing_project", return_value="application"):
        assert await langsmith_tools._run_is_readable(client, run, "alice") is (
            project == "external"
        )
        client.read_project.side_effect = ValueError("unavailable")
        assert not await langsmith_tools._run_is_readable(client, run, "alice")
        client.read_run.side_effect = ValueError("root unavailable")
        run.trace_id = "missing-root"
        assert not await langsmith_tools._run_is_readable(client, run, "alice")


@pytest.mark.asyncio
@pytest.mark.parametrize("limit,expected", [(1, 1), (9999, 50)])
async def test_langsmith_list_runs_caps_authorized_results(limit, expected) -> None:
    client = AsyncMock()
    client.__aenter__.return_value = client

    async def candidates(*, project_name, filter, limit):
        assert 50 < limit <= 500
        for index in range(limit):
            yield SimpleNamespace(id=str(index))

    client.list_runs = candidates
    tool = next(
        t for t in langsmith_tools._make_tools(allow_team=True) if t.name == "langsmith_list_runs"
    )
    with (
        patch.object(langsmith_tools, "_creds_for", AsyncMock()),
        patch.object(langsmith_tools, "langsmith_client", return_value=client),
        patch.object(
            langsmith_tools,
            "_run_is_readable",
            AsyncMock(side_effect=lambda client, run, login: int(run.id) >= 50),
        ),
    ):
        result = await tool.ainvoke({"on_behalf_of": "octo", "project_name": "p", "limit": limit})
    assert result["success"] is True
    assert [run["id"] for run in result["runs"]] == [str(i) for i in range(50, 50 + expected)]


@pytest.mark.asyncio
async def test_load_observability_tools_skipped_when_unauthorized() -> None:
    with (
        patch.object(server, "load_datadog_tools", AsyncMock(return_value=["dd"])),
        patch.object(server, "load_langsmith_tools", AsyncMock(return_value=["ls"])),
    ):
        assert await server._load_observability_tools(authorized=False, profile_login=None) == []


@pytest.mark.asyncio
async def test_load_observability_tools_loaded_when_authorized() -> None:
    with (
        patch.object(server, "load_datadog_tools", AsyncMock(return_value=["dd"])),
        patch.object(server, "load_langsmith_tools", AsyncMock(return_value=["ls"])),
    ):
        assert await server._load_observability_tools(authorized=True, profile_login="alice") == [
            "dd",
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
    monkeypatch.setattr(server, "load_datadog_tools", AsyncMock(return_value=["dd"]))

    config = cast(RunnableConfig, {"configurable": {"user_email": "admin@example.com"}})
    assert await server._observability_tools_for(config, "alice") == ["dd", "ls"]
    assert await server._observability_tools_for(config, "alice") == ["dd", "ls"]
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
    monkeypatch.setattr(server, "load_datadog_tools", AsyncMock(return_value=["dd"]))
    monkeypatch.setattr(
        server,
        "load_langsmith_tools",
        AsyncMock(side_effect=lambda _login, allow_team=True: ["team"] if allow_team else []),
    )

    admin = cast(RunnableConfig, {"configurable": {"user_email": "admin@example.com"}})
    attacker = cast(RunnableConfig, {"configurable": {"user_email": "attacker@example.com"}})

    assert await server._observability_tools_for(admin, "alice") == ["dd", "team"]
    assert await server._observability_tools_for(attacker, "alice") == []
