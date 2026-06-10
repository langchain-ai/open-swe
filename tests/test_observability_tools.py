from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from agent import server
from agent.dashboard.team_credentials import DatadogCredentials, LangSmithCredentials
from agent.integrations import datadog_mcp, langsmith_tools


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
async def test_load_langsmith_tools_empty_when_not_connected() -> None:
    with patch.object(langsmith_tools, "get_langsmith_credentials", AsyncMock(return_value=None)):
        assert await langsmith_tools.load_langsmith_tools() == []


@pytest.mark.asyncio
async def test_load_langsmith_tools_names() -> None:
    creds = LangSmithCredentials(api_key="k", endpoint="https://api.smith.langchain.com")
    with patch.object(langsmith_tools, "get_langsmith_credentials", AsyncMock(return_value=creds)):
        tools = await langsmith_tools.load_langsmith_tools()
    assert {t.name for t in tools} == {"langsmith_get_trace", "langsmith_list_runs"}


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
        def read_run(self, run_id: str, load_child_runs: bool = False):
            assert run_id == "run-1"
            return _Run()

    tools = langsmith_tools._make_tools(creds)
    get_trace = next(t for t in tools if t.name == "langsmith_get_trace")
    with patch.object(langsmith_tools, "_client", lambda _c: _FakeClient()):
        result = await get_trace.ainvoke({"run_id": "run-1"})
    assert result["success"] is True
    assert result["run"]["name"] == "my-run"
    assert result["run"]["trace_id"] == "trace-1"


@pytest.mark.asyncio
async def test_langsmith_list_runs_caps_limit() -> None:
    creds = LangSmithCredentials(api_key="k", endpoint="https://api.smith.langchain.com")
    captured: dict[str, object] = {}

    class _FakeClient:
        def list_runs(self, *, project_name: str, filter, limit: int):
            captured["limit"] = limit
            captured["project_name"] = project_name
            return []

    tools = langsmith_tools._make_tools(creds)
    list_runs = next(t for t in tools if t.name == "langsmith_list_runs")
    with patch.object(langsmith_tools, "_client", lambda _c: _FakeClient()):
        result = await list_runs.ainvoke({"project_name": "p", "limit": 9999})
    assert result["success"] is True
    assert captured["limit"] == langsmith_tools._MAX_LIST_RUNS


@pytest.mark.asyncio
async def test_load_observability_tools_skipped_when_unauthorized() -> None:
    with (
        patch.object(server, "load_datadog_tools", AsyncMock(return_value=["dd"])),
        patch.object(server, "load_langsmith_tools", AsyncMock(return_value=["ls"])),
    ):
        assert await server._load_observability_tools(authorized=False) == []


@pytest.mark.asyncio
async def test_load_observability_tools_loaded_when_authorized() -> None:
    with (
        patch.object(server, "load_datadog_tools", AsyncMock(return_value=["dd"])),
        patch.object(server, "load_langsmith_tools", AsyncMock(return_value=["ls"])),
    ):
        assert await server._load_observability_tools(authorized=True) == ["dd", "ls"]


@pytest.mark.asyncio
async def test_observability_authorized_gates_on_admin(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CONFIGURED_ADMINS", "admin@example.com")
    monkeypatch.delenv("OBSERVABILITY_AUTHORIZED_EMAILS", raising=False)
    monkeypatch.setattr(server, "email_for_login", AsyncMock(return_value=None))

    admin_config = {"configurable": {"user_email": "admin@example.com"}}
    other_config = {"configurable": {"user_email": "attacker@example.com"}}

    assert await server._observability_authorized(admin_config, None) is True
    assert await server._observability_authorized(other_config, None) is False


@pytest.mark.asyncio
async def test_observability_authorized_allowlist(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CONFIGURED_ADMINS", "")
    monkeypatch.setenv("OBSERVABILITY_AUTHORIZED_EMAILS", "trusted@example.com")
    monkeypatch.setattr(server, "email_for_login", AsyncMock(return_value=None))

    config = {"configurable": {"user_email": "trusted@example.com"}}
    assert await server._observability_authorized(config, None) is True


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

    config = {"configurable": {"github_login": "dev"}}
    assert await server._observability_authorized(config, "dev") is True
