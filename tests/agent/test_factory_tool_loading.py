"""Integration tools the graph factory loads into a run."""

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import langgraph_sdk
import pytest
from langchain.agents import create_agent
from langchain.agents.middleware.types import AgentMiddleware, ModelRequest, ModelResponse
from langchain_core.language_models.fake_chat_models import FakeListChatModel
from langchain_core.messages import AIMessage
from langchain_core.tools import StructuredTool
from langgraph.graph.state import RunnableConfig

from openswe import server
from openswe.dashboard.workspace_settings import WorkspaceSettings
from openswe.middleware.dynamic_tools import DynamicToolMiddleware
from openswe.sandboxes.state import SANDBOX_BACKENDS
from openswe.sandboxes.tool_runtime import ToolSurface

_MODEL_DEFAULTS = {
    "default_agent_model": "openai:gpt-5.6-sol",
    "default_agent_reasoning_effort": "medium",
    "default_agent_subagent_model": "openai:gpt-5.6-sol",
    "default_agent_subagent_reasoning_effort": "low",
}


def _config() -> RunnableConfig:
    return {
        "configurable": {
            "__is_for_execution__": True,
            "thread_id": "thread-factory-tools",
            "github_login": "octocat",
        },
        "metadata": {},
    }


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("initial_plan_mode", "mcp_ptc", "include_ordinary"),
    [(False, False, True), (True, False, True), (False, True, True), (True, True, False)],
)
@pytest.mark.parametrize("github_login", ["octocat", None])
async def test_workspace_mcps_load_for_non_admins_with_legacy_plan_state(
    mcp_ptc: bool,
    include_ordinary: bool,
    initial_plan_mode: bool,
    github_login: str | None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CONFIGURED_ADMINS", "workspace-admin")
    monkeypatch.setattr(
        langgraph_sdk,
        "get_client",
        lambda: SimpleNamespace(
            threads=SimpleNamespace(
                get=AsyncMock(
                    return_value={"metadata": {"visibility": "public", "owner_login": "octocat"}}
                )
            )
        ),
    )

    async def delete_incident() -> str:
        return "deleted"

    mcp_tool = StructuredTool.from_function(
        coroutine=delete_incident,
        name="mcp_incident_delete_0123456789",
        description="Delete an incident",
    )

    ordinary_tool = StructuredTool.from_function(
        coroutine=delete_incident, name="mcp_github-2_tool", description="Ordinary integration"
    )
    mcp_tools = [mcp_tool, *([ordinary_tool] if include_ordinary else [])]
    surface = ToolSurface()

    def assemble(*, middleware: list[AgentMiddleware], **kwargs: object) -> object:
        return create_agent(
            FakeListChatModel(responses=[]),
            middleware=[
                item
                for item in middleware
                if isinstance(item, DynamicToolMiddleware | server.CodeModeMiddleware)
            ],
        )

    thread_id = "thread-factory-tools"
    SANDBOX_BACKENDS.pop(thread_id, None)
    with (
        patch(
            "openswe.server.resolve_github_token",
            new_callable=AsyncMock,
            return_value=("ghp", None),
        ),
        patch("openswe.server.resolve_triggering_user_identity", return_value=None),
        patch(
            "openswe.server.ensure_sandbox_for_thread",
            new_callable=AsyncMock,
            return_value=MagicMock(),
        ),
        patch(
            "openswe.server.resolve_sandbox_work_dir",
            new_callable=AsyncMock,
            return_value="/workspace",
        ),
        patch(
            "openswe.server.cached_workspace_settings",
            new_callable=AsyncMock,
            return_value=WorkspaceSettings(_MODEL_DEFAULTS),
        ),
        patch("openswe.server.load_profile", new_callable=AsyncMock, return_value=None),
        patch(
            "openswe.server._cached_profile",
            new_callable=AsyncMock,
            return_value={"experimental_mcp_ptc": mcp_ptc},
        ),
        patch("openswe.server.load_thread_settings", new_callable=AsyncMock, return_value={}),
        patch("openswe.server.fallback_model_id_for", return_value=None),
        patch("openswe.server.make_model", return_value=MagicMock()),
        patch("openswe.server.construct_system_prompt", return_value="prompt"),
        patch("openswe.server.create_deep_agent", side_effect=assemble) as build_agent,
        patch("openswe.users.User.email_for_login", new_callable=AsyncMock, return_value=None),
        patch(
            "openswe.server._mcp_tools_for",
            new_callable=AsyncMock,
            return_value=mcp_tools,
        ),
    ):
        config = _config()
        config["configurable"]["github_login"] = github_login
        config["configurable"]["plan_mode"] = initial_plan_mode
        await server.build_agent(config, tool_surface=surface)

    tool_names = {
        tool.name if hasattr(tool, "name") else tool.__name__
        for tool in build_agent.call_args.kwargs["tools"]
    }
    assert not tool_names.intersection(
        {
            "linear_comment",
            "linear_create_issue",
            "linear_delete_issue",
            "linear_get_issue",
            "linear_get_issue_comments",
            "linear_list_teams",
            "linear_search_issues",
            "linear_update_issue",
        }
    )

    middleware = build_agent.call_args.kwargs["middleware"]
    dynamic = next(item for item in middleware if isinstance(item, DynamicToolMiddleware))
    captured: list[str] = []

    async def capture(request: ModelRequest) -> ModelResponse:
        captured.extend(tool.name for tool in request.tools)
        return ModelResponse(result=[AIMessage(content="")])

    expected = [tool.name for tool in mcp_tools if not mcp_ptc or tool is ordinary_tool]
    for state in [{}, {"plan_mode": True}, {"plan_mode": False}]:
        captured.clear()
        request = ModelRequest(
            model=MagicMock(),
            messages=[],
            tools=[],
            runtime=MagicMock(),
            state={
                **state,
                "messages": [],
                "loaded_integration_tools": [mcp_tool.name, ordinary_tool.name],
            },
        )
        await dynamic.awrap_model_call(request, capture)
        assert captured == expected

    await surface.prepare({})
    discovered = {tool.name for tool in surface.catalog()}
    assert {tool.name for tool in mcp_tools} <= discovered
    for name in (tool.name for tool in mcp_tools):
        result = await surface.run(config, {"messages": []}, name, {})
        assert result.message.status == "success", result.content
        assert result.content == "deleted"

    if mcp_ptc and include_ordinary:
        load = await dynamic.tools[0].ainvoke(
            {
                "name": "load_integration_tools",
                "args": {"tool_names": [mcp_tool.name]},
                "id": "load-hidden",
                "type": "tool_call",
            }
        )
        assert load.update["messages"][0].status == "error"
        assert mcp_tool.name not in dynamic.tools[0].description
    elif mcp_ptc:
        assert dynamic.tools == []
    SANDBOX_BACKENDS.pop(thread_id, None)


@pytest.mark.asyncio
async def test_code_mode_keeps_invalid_names_in_dynamic_catalog(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from langchain_quickjs.middleware import filter_tools_for_ptc

    async def invoke() -> str:
        return "ok"

    tools = [
        StructuredTool.from_function(coroutine=invoke, name=name, description="Integration")
        for name in ("mcp_valid_tool", "mcp_github-2_tool", "mcp_sentry_get-Issue", "mcp_tail-")
    ]
    monkeypatch.setattr(
        langgraph_sdk,
        "get_client",
        lambda: SimpleNamespace(
            threads=SimpleNamespace(
                get=AsyncMock(return_value={"metadata": {"owner_login": "alice"}})
            )
        ),
    )
    monkeypatch.setattr(
        server, "_cached_profile", AsyncMock(return_value={"experimental_mcp_ptc": True})
    )
    code_mode, ordinary = await server._mcp_code_mode(
        "thread",
        tools,
        additional_tools=[
            "http_request",
            "read_file",
            "write_file",
            "task",
            "background_execute",
            "slack_reply",
            "slack_no_reply_needed",
            "cli_result",
        ],
    )
    assert code_mode is not None
    http_tool = StructuredTool.from_function(
        coroutine=invoke, name="http_request", description="HTTP"
    )
    file_tool = StructuredTool.from_function(coroutine=invoke, name="read_file", description="Read")
    write_tool = StructuredTool.from_function(
        coroutine=invoke, name="write_file", description="Write"
    )
    guarded_tools = [
        StructuredTool.from_function(coroutine=invoke, name=name, description="Guarded")
        for name in ("background_execute", "slack_reply", "slack_no_reply_needed", "cli_result")
    ]
    exposed = filter_tools_for_ptc(
        [http_tool, file_tool, write_tool, *guarded_tools], code_mode._ptc, self_tool_name="eval"
    )
    assert exposed == [tools[0], http_tool, file_tool, write_tool]
    state: dict[str, Any] = {"messages": []}
    state.update(await code_mode.abefore_agent(state, MagicMock()) or {})
    offered: list[str] = []

    async def model(request: ModelRequest) -> ModelResponse:
        offered.extend(tool.name for tool in request.tools)
        assert request.system_message is not None and "httpRequest" in request.system_message.text
        return ModelResponse(result=[AIMessage(content="")])

    await code_mode.awrap_model_call(
        ModelRequest(
            model=MagicMock(),
            messages=[],
            tools=[http_tool, file_tool, write_tool, *guarded_tools],
            state=state,
            runtime=MagicMock(),
        ),
        model,
    )
    assert offered == [tool.name for tool in [file_tool, write_tool, *guarded_tools]]
    assert list(ordinary) == tools[1:]
    dynamic = server._integration_middleware(ordinary, set())
    full = server._integration_middleware(tools, set())
    assert dynamic is not None and full is not None
    assert {tool.name for tool in await dynamic.catalog_tools()} == {
        tool.name for tool in tools[1:]
    }
    catalog = await full.catalog_tools()
    assert {tool.name for tool in catalog} == {tool.name for tool in tools}
    assert await catalog[0].ainvoke({}) == "ok"
