"""Sandbox tool authorization, discovery, and server-side execution."""

from collections.abc import AsyncIterator, Awaitable, Callable
from typing import cast
from unittest.mock import AsyncMock, MagicMock

import httpx
import jwt
import pytest
from fastapi import FastAPI, HTTPException
from langchain.agents import create_agent
from langchain.agents.middleware import AgentMiddleware
from langchain_core.language_models.fake_chat_models import FakeListChatModel
from langchain_core.messages import ToolMessage
from langchain_core.runnables import RunnableConfig
from langchain_core.tools import StructuredTool
from langgraph.prebuilt.tool_node import ToolCallRequest
from langgraph.types import Command

from agent.middleware.dynamic_tools import DynamicToolMiddleware
from agent.middleware.trace import OpenSWEMiddleware
from agent.sandboxes import tool_access, tool_data, tool_routes, tool_runtime
from agent.sandboxes.tool_data import ToolContext
from agent.sandboxes.tool_runtime import ToolSurface


@pytest.fixture
def capability_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DASHBOARD_JWT_SECRET", "test-tools-signing-key")
    monkeypatch.setenv("DASHBOARD_API_BASE_URL", "https://agent.example.test")


async def integration_echo(value: str) -> str:
    """Searchable connected integration."""
    return value


class DenyValue(OpenSWEMiddleware):
    async def awrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Awaitable[ToolMessage | Command]],
    ) -> ToolMessage | Command:
        if request.tool_call["args"].get("value") == "denied":
            return ToolMessage(
                content="blocked", status="error", tool_call_id=request.tool_call["id"]
            )
        return await handler(request)


def surface() -> ToolSurface:
    dynamic = DynamicToolMiddleware(
        {
            "MCP": [StructuredTool.from_function(coroutine=integration_echo)],
        }
    )
    graph = create_agent(
        FakeListChatModel(responses=[]),
        middleware=cast(list[AgentMiddleware], [dynamic, DenyValue()]),
    )
    return ToolSurface(graph=graph, dynamic=dynamic)


async def test_capability_carries_binding_and_is_revoked_on_rebinding(
    capability_settings: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    issued = await tool_access.issue_tool_access("thread-a", "sandbox-a")
    assert issued is not None
    url, token = issued
    assert url == "https://agent.example.test/dashboard/api/sandbox-tools"
    claims = jwt.decode(
        token, "test-tools-signing-key", algorithms=["HS256"], audience=tool_access.TOOLS_AUDIENCE
    )
    assert claims["thread_id"] == "thread-a"
    assert claims["sandbox_id"] == "sandbox-a"
    client = MagicMock()
    client.threads.get = AsyncMock(return_value={"metadata": {"sandbox_id": "sandbox-a"}})
    monkeypatch.setattr(tool_access, "get_client", lambda: client)
    assert (await tool_access.authenticate_tool_access(token)).thread_id == "thread-a"
    client.threads.get.return_value = {"metadata": {"sandbox_id": "sandbox-b"}}
    with pytest.raises(HTTPException, match="Invalid sandbox capability"):
        await tool_access.authenticate_tool_access(token)
    with pytest.raises(HTTPException):
        await tool_access.authenticate_tool_access("x" * 64)


async def test_proxy_refresh_preserves_tools_and_custom_rules(
    capability_settings: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agent.sandboxes.providers import langsmith

    monkeypatch.setenv("LANGSMITH_API_KEY", "test-sandbox-api-key")
    patch_proxy = AsyncMock()
    monkeypatch.setattr(langsmith, "_patch_proxy_config", patch_proxy)
    custom = {"name": "custom", "match_hosts": ["custom.example.test"]}
    await langsmith.configure_sandbox_proxy(
        "sandbox-a",
        "test-github-token",
        thread_id="thread-a",
        base_proxy_config={"enabled": True, "rules": [custom]},
    )
    rules = patch_proxy.call_args.args[2]["proxy_config"]["rules"]
    assert custom in rules
    rule = next(rule for rule in rules if rule["name"] == tool_access.TOOLS_RULE)
    assert rule["match_hosts"] == ["agent.example.test"]
    assert rule["headers"][0]["type"] == "opaque"
    assert rule["env_vars"] == {
        "OPEN_SWE_TOOLS_URL": "https://agent.example.test/dashboard/api/sandbox-tools"
    }
    assert "thread-a" not in str(rule) and "sandbox-a" not in str(rule)
    first_token = rule["headers"][0]["value"]
    await langsmith.configure_sandbox_proxy(
        "sandbox-a",
        "new-test-github-token",
        thread_id="thread-a",
        base_proxy_config={"enabled": True, "rules": [custom]},
    )
    rules = patch_proxy.call_args.args[2]["proxy_config"]["rules"]
    rule = next(rule for rule in rules if rule["name"] == tool_access.TOOLS_RULE)
    assert rule["headers"][0]["value"] == first_token


async def test_restores_idle_context_ignoring_legacy_plan_mode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agent import server

    monkeypatch.setattr(
        tool_runtime,
        "load_context",
        AsyncMock(
            return_value=ToolContext(configurable={"github_login": "owner", "plan_mode": True})
        ),
    )
    client = MagicMock()
    client.threads.get_state = AsyncMock(return_value={"values": {}})
    monkeypatch.setattr(tool_runtime, "get_client", lambda: client)
    source = surface()

    async def build_agent(config: RunnableConfig, *, tool_surface: ToolSurface) -> object:
        assert config["configurable"]["github_login"] == "owner"
        assert config["configurable"]["thread_id"] == "thread-a"
        tool_surface.graph = source.graph
        tool_surface.dynamic = source.dynamic
        return source.graph

    monkeypatch.setattr(server, "build_agent", build_agent)
    restored, _, _ = await tool_runtime.load_tool_surface("thread-a")
    assert "integration_echo" in restored.tools


async def test_mcp_discovery_invocation_and_middleware_without_a_model_call() -> None:
    tools = surface()
    state: dict[str, object] = {"messages": []}
    await tools.prepare(state)
    assert [tool.name for tool in tools.catalog("connected SEARCHABLE")] == ["integration_echo"]
    properties = tools.catalog()[0].parameters["properties"]
    assert isinstance(properties, dict) and set(properties) == {"value"}
    config: RunnableConfig = {"configurable": {"thread_id": "thread-a"}}
    response = await tools.invoke("thread-a", config, state, "integration_echo", {"value": "hello"})
    assert response == {"status": "success", "content": "hello"}
    assert state == {"messages": []}
    blocked = await tools.invoke("thread-a", config, state, "integration_echo", {"value": "denied"})
    assert blocked == {"status": "error", "content": "blocked"}
    with pytest.raises(HTTPException):
        await tools.invoke(
            "thread-a", config, state, "integration_echo", {"value": "hello", "runtime": {}}
        )
    for name in ("enter_plan_mode", "approve_plan", "load_integration_tools"):
        with pytest.raises(HTTPException):
            await tools.invoke("thread-a", config, state, name, {})
    await tools.prepare({"plan_mode": True})
    assert "integration_echo" in tools.tools


async def test_http_list_search_invoke_and_reject_context_overrides(
    capability_settings: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tools = surface()
    state: dict[str, object] = {"messages": []}
    await tools.prepare(state)
    config: RunnableConfig = {"configurable": {"thread_id": "thread-a"}}
    monkeypatch.setattr(
        tool_runtime, "load_tool_surface", AsyncMock(return_value=(tools, config, state))
    )
    client = MagicMock()
    client.threads.get = AsyncMock(return_value={"metadata": {"sandbox_id": "sandbox-a"}})
    monkeypatch.setattr(tool_access, "get_client", lambda: client)
    issued = await tool_access.issue_tool_access("thread-a", "sandbox-a")
    assert issued is not None
    _, token = issued
    app = FastAPI()
    app.include_router(tool_routes.router)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="https://test"
    ) as http:
        assert (await http.get("/dashboard/api/sandbox-tools/list")).status_code == 401
        headers = {tool_access.TOOLS_HEADER: token}
        result = await http.get("/dashboard/api/sandbox-tools/search?q=connected", headers=headers)
        assert result.status_code == 200
        assert result.json()["tools"][0]["name"] == "integration_echo"
        assert result.headers["cache-control"] == "no-store"
        listed = await http.get("/dashboard/api/sandbox-tools/list", headers=headers)
        assert listed.json()["total"] == 1
        invoked = await http.post(
            "/dashboard/api/sandbox-tools/invoke/integration_echo",
            headers=headers,
            json={"value": "hello"},
        )
        assert invoked.json() == {"status": "success", "content": "hello"}
        forged = await http.post(
            "/dashboard/api/sandbox-tools/invoke/integration_echo",
            headers=headers,
            json={
                "value": "hello",
                "thread_id": "other",
            },
        )
        assert forged.status_code == 422
        large = await http.post(
            "/dashboard/api/sandbox-tools/invoke/integration_echo",
            headers=headers,
            content=b" " * (tool_routes.MAX_REQUEST_BYTES + 1),
        )
        assert large.status_code == 413
        for content in (
            "[]",
            "null",
            "{invalid",
            '{"name":"integration_echo","arguments":{"value":"hello"}}',
        ):
            invalid = await http.post(
                "/dashboard/api/sandbox-tools/invoke/integration_echo",
                headers=headers,
                content=content,
            )
            assert invalid.status_code == 422
        unknown = await http.post(
            "/dashboard/api/sandbox-tools/invoke/unknown", headers=headers, json={}
        )
        assert unknown.status_code == 404
        unauthorized = await http.post(
            "/dashboard/api/sandbox-tools/invoke/integration_echo", json={"value": "hello"}
        )
        assert unauthorized.status_code == 401


async def test_postgres_records_round_trip_and_isolate_threads(registry_db: None) -> None:
    await tool_data.save_context("a", ToolContext(configurable={"source": "dashboard"}))
    assert await tool_data.load_context("b") is None
    assert await tool_data.load_context("a") == ToolContext(configurable={"source": "dashboard"})


@pytest.mark.parametrize(
    "overrides,secret,algorithm",
    [
        ({"sandbox_id": "other"}, "wrong-signing-secret", "HS256"),
        ({"aud": "dashboard"}, "test-tools-signing-key", "HS256"),
        ({"thread_id": None}, "test-tools-signing-key", "HS256"),
        ({"sandbox_id": 42}, "test-tools-signing-key", "HS256"),
        ({"sandbox_id": ""}, "test-tools-signing-key", "HS256"),
        ({}, "test-tools-signing-key", "HS384"),
    ],
)
async def test_invalid_claims_never_reach_thread_lookup(
    capability_settings: None,
    monkeypatch: pytest.MonkeyPatch,
    overrides: dict[str, object],
    secret: str,
    algorithm: str,
) -> None:
    client = MagicMock()
    client.threads.get = AsyncMock()
    monkeypatch.setattr(tool_access, "get_client", lambda: client)
    token = jwt.encode(
        {
            "aud": tool_access.TOOLS_AUDIENCE,
            "thread_id": "thread-a",
            "sandbox_id": "sandbox-a",
            **overrides,
        },
        secret,
        algorithm=algorithm,
    )
    with pytest.raises(HTTPException) as exc:
        await tool_access.authenticate_tool_access(token)
    assert exc.value.status_code == 401
    client.threads.get.assert_not_awaited()


def test_invocation_openapi_declares_raw_arguments_and_result() -> None:
    app = FastAPI()
    app.include_router(tool_routes.router)
    schema = app.openapi()
    operation = schema["paths"]["/dashboard/api/sandbox-tools/invoke/{tool_name}"]["post"]
    assert operation["requestBody"]["content"]["application/json"]["schema"] == {
        "$ref": "#/components/schemas/ToolArguments"
    }
    assert schema["components"]["schemas"]["ToolArguments"]["type"] == "object"
    assert operation["responses"]["200"]["content"]["application/json"]["schema"] == {
        "$ref": "#/components/schemas/ToolResult"
    }


async def test_chunked_request_limit_precedes_json_parsing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(tool_routes, "MAX_REQUEST_BYTES", 16)
    app = FastAPI()
    app.include_router(tool_routes.router)

    async def chunks() -> AsyncIterator[bytes]:
        yield b'{"value":"'
        yield b"x" * 16
        pytest.fail("Oversized request should stop reading the stream")

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="https://test"
    ) as http:
        response = await http.post(
            "/dashboard/api/sandbox-tools/invoke/integration_echo",
            content=chunks(),
            headers={"Content-Type": "application/json"},
        )
    assert response.status_code == 413


def test_agent_factory_is_accepted_by_langgraph() -> None:
    from langgraph_api._factory_utils import FACTORY_KWARGS, classify_factory

    from agent.server import traced_agent

    graph_id = "sandbox-tools-factory-test"
    try:
        classify_factory(traced_agent, graph_id)
    finally:
        FACTORY_KWARGS.pop(graph_id, None)
