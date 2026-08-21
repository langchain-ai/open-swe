from typing import Any, Literal, cast
from unittest.mock import AsyncMock, call, patch

import pytest
from langchain_core.runnables import RunnableConfig
from langchain_core.tools import StructuredTool

from agent.graphs import agent as agent_graph
from agent.integrations import datadog_mcp, langsmith_tools, notion_mcp
from agent.settings.team_credentials import DatadogCredentials, LangSmithCredentials
from agent.utils import github_org_membership


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
        patch.object(agent_graph, "load_datadog_tools", AsyncMock(return_value=["dd"])),
        patch.object(agent_graph, "load_langsmith_tools", AsyncMock(return_value=["ls"])),
    ):
        assert (
            await agent_graph._load_observability_tools(authorized=False, profile_login=None) == []
        )


@pytest.mark.asyncio
async def test_load_observability_tools_loaded_when_authorized() -> None:
    with (
        patch.object(agent_graph, "load_datadog_tools", AsyncMock(return_value=["dd"])),
        patch.object(agent_graph, "load_langsmith_tools", AsyncMock(return_value=["ls"])),
    ):
        assert await agent_graph._load_observability_tools(
            authorized=True, profile_login="alice"
        ) == [
            "dd",
            "ls",
        ]


@pytest.mark.asyncio
async def test_observability_authorized_gates_on_admin(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CONFIGURED_ADMINS", "admin@example.com")
    monkeypatch.delenv("OBSERVABILITY_AUTHORIZED_EMAILS", raising=False)
    monkeypatch.setattr(agent_graph, "email_for_login", AsyncMock(return_value=None))

    admin_config = cast(RunnableConfig, {"configurable": {"user_email": "admin@example.com"}})
    other_config = cast(RunnableConfig, {"configurable": {"user_email": "attacker@example.com"}})

    assert await agent_graph._observability_authorized(admin_config, None) is True
    assert await agent_graph._observability_authorized(other_config, None) is False


@pytest.mark.asyncio
async def test_observability_authorized_allowlist(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CONFIGURED_ADMINS", "")
    monkeypatch.setenv("OBSERVABILITY_AUTHORIZED_EMAILS", "trusted@example.com")
    monkeypatch.setattr(agent_graph, "email_for_login", AsyncMock(return_value=None))

    config = cast(RunnableConfig, {"configurable": {"user_email": "trusted@example.com"}})
    assert await agent_graph._observability_authorized(config, None) is True


@pytest.mark.asyncio
async def test_allowed_org_member(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ALLOWED_GITHUB_ORGS", "primary,secondary")
    membership = AsyncMock(side_effect=[False, True])
    monkeypatch.setattr(github_org_membership, "is_user_active_org_member", membership)

    config = cast(RunnableConfig, {"configurable": {"github_login": "dev"}})
    assert await agent_graph._allowed_org_member(config, "dev") is True
    assert membership.await_args_list == [call("dev", "primary"), call("dev", "secondary")]


@pytest.mark.asyncio
async def test_observability_authorized_resolves_login_email(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CONFIGURED_ADMINS", "dev@example.com")
    monkeypatch.delenv("OBSERVABILITY_AUTHORIZED_EMAILS", raising=False)
    monkeypatch.setattr(
        agent_graph,
        "email_for_login",
        AsyncMock(side_effect=lambda login: "dev@example.com" if login else None),
    )

    config = cast(RunnableConfig, {"configurable": {"github_login": "dev"}})
    assert await agent_graph._observability_authorized(config, "dev") is True


@pytest.mark.asyncio
async def test_observability_authorized_accepts_admin_login(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CONFIGURED_ADMINS", "dev")
    monkeypatch.delenv("OBSERVABILITY_AUTHORIZED_EMAILS", raising=False)
    monkeypatch.setattr(agent_graph, "email_for_login", AsyncMock(return_value=None))

    config = cast(RunnableConfig, {"configurable": {"github_login": "dev"}})
    assert await agent_graph._observability_authorized(config, "dev") is True
