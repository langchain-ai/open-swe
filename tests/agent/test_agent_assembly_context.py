"""Assembly contract for the main agent's context-management + middleware wiring.

Locks in that `get_agent` hands a sandbox `backend` to `create_deep_agent` (which
is what makes deepagents auto-wire `FilesystemMiddleware` tool-result eviction and
`SummarizationMiddleware` history offloading), and that the redundant custom
`RepairOrphanedToolCallsMiddleware` is no longer added explicitly — the built-in
`PatchToolCallsMiddleware` that `create_deep_agent` adds covers it.
"""

import asyncio
import json
from collections.abc import Callable
from types import SimpleNamespace
from typing import Literal, cast
from unittest.mock import AsyncMock, MagicMock, patch

import langgraph_sdk
import pytest
from langchain.agents.middleware.types import ModelRequest, ModelResponse
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langgraph.graph.state import RunnableConfig

from agent.dashboard.workspace_settings import WorkspaceSettings
from agent.sandboxes.state import SANDBOX_BACKENDS
from agent.server import _registered_tool_name, get_agent

_MODEL_DEFAULTS = {
    "default_agent_model": "openai:gpt-6.1-sol",
    "default_agent_reasoning_effort": "medium",
    "default_agent_subagent_model": "openai:gpt-6.1-sol",
    "default_agent_subagent_reasoning_effort": "low",
}


@pytest.fixture(autouse=True)
def saved_thread_scope(monkeypatch):
    metadata = {"visibility": "private", "owner_login": "octocat"}
    monkeypatch.setattr(
        langgraph_sdk,
        "get_client",
        lambda: SimpleNamespace(
            threads=SimpleNamespace(get=AsyncMock(side_effect=lambda _id: {"metadata": metadata}))
        ),
    )
    return metadata


@pytest.mark.asyncio
async def test_public_agent_excludes_personal_skills_and_tools(saved_thread_scope):
    saved_thread_scope["visibility"] = "public"
    config = _base_config()
    config["configurable"]["source"] = "dashboard"
    with patch("agent.server._notion_tools_for", new_callable=AsyncMock, return_value=[]) as notion:
        captured = await _capture_create_deep_agent_kwargs(config)
    assert captured["skills"] == ["/organization-skills/", "/bundled-skills/"]
    assert "/skills/" not in captured["backend"].routes
    tools = captured["tools"]
    assert isinstance(tools, list)
    tool_names = {_registered_tool_name(tool) for tool in tools}
    assert not tool_names.intersection(
        {
            "save_user_instructions",
            "save_user_settings",
            "save_user_skill",
            "delete_user_skill",
            "read_user_settings",
        }
    )
    notion.assert_awaited_once_with(None)
    from agent.middleware import WorkspaceSkillsMiddleware

    middleware = cast(list[object], captured["middleware"])
    assert any(isinstance(item, WorkspaceSkillsMiddleware) for item in middleware)
    subagents = cast(list[dict], captured["subagents"])
    assert any(isinstance(item, WorkspaceSkillsMiddleware) for item in subagents[0]["middleware"])


@pytest.mark.asyncio
async def test_binary_content_is_offloaded_to_a_thread_scoped_store():
    from deepagents.backends.store import StoreBackend
    from deepagents.middleware.filesystem import FilesystemMiddleware

    captured = await _capture_create_deep_agent_kwargs()
    blobs = captured["backend"].routes["/blobs/"]
    assert isinstance(blobs, StoreBackend)
    assert blobs._namespace(MagicMock()) == ("thread_blobs", "thread-ctx")
    filesystem = next(
        item
        for item in cast(list[object], captured["middleware"])
        if isinstance(item, FilesystemMiddleware)
    )
    assert filesystem.backend is captured["backend"]
    assert filesystem._offload_binary_content


@pytest.mark.asyncio
async def test_unknown_scope_omits_workspace_and_personal_mcps():
    with (
        patch("agent.server.private_credential_login", side_effect=TimeoutError),
        patch("agent.server._mcp_tools_for", new_callable=AsyncMock) as mcps,
        patch("agent.server._notion_tools_for", new_callable=AsyncMock) as notion,
    ):
        await _capture_create_deep_agent_kwargs()
    mcps.assert_not_awaited()
    notion.assert_not_awaited()


class _DummyAgent:
    def with_config(self, config: RunnableConfig) -> _DummyAgent:
        self.config = config
        return self


def _base_config() -> RunnableConfig:
    return {
        "configurable": {
            "__is_for_execution__": True,
            "thread_id": "thread-ctx",
            "github_login": "octocat",
        },
        "metadata": {},
    }


async def _capture_create_deep_agent_kwargs(
    config: RunnableConfig | None = None,
    *,
    profile: dict[str, object] | None = None,
    thread_settings: dict[str, object] | None = None,
    workspace_settings: WorkspaceSettings | None = None,
    make_model: Callable[..., BaseChatModel] | None = None,
) -> dict[str, object]:
    captured: dict[str, object] = {}
    make_model_calls: list[tuple[str, dict[str, object]]] = []
    config = config or _base_config()
    thread_id = str((config.get("configurable") or {}).get("thread_id"))

    def fake_create_deep_agent(**kwargs: object) -> _DummyAgent:
        captured.update(kwargs)
        return _DummyAgent()

    def fake_make_model(model_id: str, **kwargs: object) -> MagicMock | BaseChatModel:
        make_model_calls.append((model_id, kwargs))
        return MagicMock() if make_model is None else make_model(model_id, **kwargs)

    SANDBOX_BACKENDS.pop(thread_id, None)
    with (
        patch(
            "agent.server.resolve_github_token",
            new_callable=AsyncMock,
            return_value=("ghp", None),
        ),
        patch("agent.server.resolve_triggering_user_identity", return_value=None),
        patch(
            "agent.server.ensure_sandbox_for_thread",
            new_callable=AsyncMock,
            return_value=MagicMock(),
        ),
        patch(
            "agent.server.resolve_sandbox_work_dir",
            new_callable=AsyncMock,
            return_value="/workspace",
        ),
        patch(
            "agent.server.cached_workspace_settings",
            new_callable=AsyncMock,
            return_value=workspace_settings
            or WorkspaceSettings(
                {
                    **_MODEL_DEFAULTS,
                    "default_agent_routing_fast_model": "google_genai:gemini-3.8-flash",
                    "default_agent_routing_fast_reasoning_effort": "low",
                    "default_agent_routing_balanced_model": "openai:gpt-6.1-sol",
                    "default_agent_routing_balanced_reasoning_effort": "medium",
                    "default_agent_routing_performance_model": "anthropic:claude-opus-5-5",
                    "default_agent_routing_performance_reasoning_effort": "high",
                }
            ),
        ),
        patch("agent.server.load_profile", new_callable=AsyncMock, return_value=profile),
        patch(
            "agent.server.load_thread_settings",
            new_callable=AsyncMock,
            return_value=thread_settings or {},
        ),
        patch("agent.server.fallback_model_id_for", return_value=None),
        patch("agent.server.make_model", side_effect=fake_make_model),
        patch("agent.server.construct_system_prompt", return_value="prompt"),
        patch("agent.server.create_deep_agent", side_effect=fake_create_deep_agent),
    ):
        await get_agent(config)

    SANDBOX_BACKENDS.pop(thread_id, None)
    captured["make_model_calls"] = make_model_calls
    return captured


@pytest.mark.asyncio
async def test_existing_thread_reloads_sender_draft_preference_into_run_config(
    saved_thread_scope,
) -> None:
    saved_thread_scope["owner_login"] = "draft-preference-owner"
    config = _base_config()
    configurable = config.get("configurable")
    assert isinstance(configurable, dict)
    configurable["github_login"] = "draft-preference-owner"

    await _capture_create_deep_agent_kwargs(
        config,
        profile={"draft_prs": False},
        thread_settings={
            "owner_login": "draft-preference-owner",
            "model_id": "openai:gpt-6.1-sol",
        },
    )

    assert configurable["draft_prs"] is False


@pytest.mark.asyncio
async def test_agent_starts_sandbox_while_loading_settings() -> None:
    started = asyncio.Event()
    release = asyncio.Event()

    async def ensure_sandbox(*args: object, **kwargs: object) -> MagicMock:
        del args, kwargs
        started.set()
        await release.wait()
        return MagicMock()

    async def load_defaults(*args: object) -> WorkspaceSettings:
        del args
        await started.wait()
        return WorkspaceSettings(
            {
                **_MODEL_DEFAULTS,
                "default_agent_routing_fast_model": "openai:gpt-6.1-sol",
                "default_agent_routing_fast_reasoning_effort": "low",
                "default_agent_routing_balanced_model": "openai:gpt-6.1-sol",
                "default_agent_routing_balanced_reasoning_effort": "medium",
                "default_agent_routing_performance_model": "openai:gpt-6.1-sol",
                "default_agent_routing_performance_reasoning_effort": "high",
                "gateway_enabled": False,
                "fable_enabled": True,
            }
        )

    SANDBOX_BACKENDS.pop("thread-ctx", None)
    with (
        patch("agent.server.ensure_sandbox_for_thread", side_effect=ensure_sandbox),
        patch("agent.server.cached_workspace_settings", side_effect=load_defaults),
        patch("agent.server._cached_profile", new_callable=AsyncMock, return_value=None),
        patch("agent.server._mcp_tools_for", new_callable=AsyncMock, return_value=[]),
        patch("agent.server._notion_tools_for", new_callable=AsyncMock, return_value=[]),
        patch("agent.server.make_model", return_value=MagicMock()),
        patch("agent.server.fallback_model_id_for", return_value=None),
        patch("agent.server.create_deep_agent", return_value=_DummyAgent()),
    ):
        agent_task = asyncio.create_task(get_agent(_base_config()))
        await asyncio.wait_for(started.wait(), timeout=1)
        assert not agent_task.done()
        release.set()
        await agent_task

    SANDBOX_BACKENDS.pop("thread-ctx", None)


@pytest.mark.asyncio
async def test_router_failure_uses_fast_tier_not_profile_default() -> None:
    config = _base_config()
    config["configurable"]["thread_id"] = "thread-1"
    profile = {
        "default_model": "anthropic:claude-opus-5-5",
        "reasoning_effort": "high",
        "model_routing_enabled": True,
    }
    agent = await _capture_create_deep_agent_kwargs(config, profile=profile)
    model_selection = next(
        item
        for item in cast(list[object], agent["middleware"])
        if type(item).__name__ == "ModelSelectionMiddleware"
    )
    route = await model_selection.select_route({"messages": []})

    assert route == "default"
    assert model_selection._models[route] is agent["model"]
    assert agent["make_model_calls"][0][0] == "google_genai:gemini-3.8-flash"


@pytest.mark.asyncio
@pytest.mark.parametrize("legacy_thread", [False, True])
async def test_admin_model_changes_only_affect_new_threads(legacy_thread: bool) -> None:
    initial_settings = (
        {"model_id": "openai:gpt-6.1-sol", "effort": "medium", "model_routing_enabled": True}
        if legacy_thread
        else {}
    )
    with patch("agent.server.store_thread_settings", new_callable=AsyncMock) as store:
        original = await _capture_create_deep_agent_kwargs(
            profile={"model_routing_enabled": True}, thread_settings=initial_settings
        )
    snapshot = json.loads(json.dumps(store.call_args.args[2]))
    changed_defaults = WorkspaceSettings(
        {
            "default_agent_model": "google_genai:gemini-3.8-flash",
            "default_agent_reasoning_effort": "high",
            "default_agent_subagent_model": "google_genai:gemini-3.8-flash",
            "default_agent_subagent_reasoning_effort": "high",
            "default_thread_title_model": "google_genai:gemini-3.8-flash",
            "default_thread_title_reasoning_effort": "high",
            "model_routing_enabled": True,
            **{
                f"default_agent_routing_{tier}_{field}": value
                for tier in ("fast", "balanced", "performance")
                for field, value in (
                    ("model", "google_genai:gemini-3.8-flash"),
                    ("reasoning_effort", "high"),
                )
            },
        }
    )
    existing = await _capture_create_deep_agent_kwargs(
        thread_settings=snapshot, workspace_settings=changed_defaults
    )
    fresh_config = _base_config()
    fresh_config["configurable"]["thread_id"] = "new-thread"
    fresh = await _capture_create_deep_agent_kwargs(
        fresh_config, workspace_settings=changed_defaults
    )

    original_calls = cast(list[tuple[str, dict[str, object]]], original["make_model_calls"])
    existing_calls = cast(list[tuple[str, dict[str, object]]], existing["make_model_calls"])
    fresh_calls = cast(list[tuple[str, dict[str, object]]], fresh["make_model_calls"])
    assert existing_calls[:-1] == original_calls[:-1]
    assert existing_calls[-1] == fresh_calls[-1] != original_calls[-1]
    assert fresh_calls != original_calls
    assert {model for model, _ in fresh_calls} == {"google_genai:gemini-3.8-flash"}


@pytest.mark.parametrize(
    "config_patch",
    [
        {"github_login": "someone-else"},
        {"github_login": None},
        {"background_task_completion": True},
        {"source": "schedule"},
    ],
)
async def test_personal_settings_tool_not_exposed_to_unauthorized_runs(
    config_patch: dict[str, object],
) -> None:
    config = _base_config()
    config["configurable"].update({"source": "dashboard", **config_patch})
    captured = await _capture_create_deep_agent_kwargs(config)
    tools = captured["tools"]
    assert isinstance(tools, list)
    assert "save_user_settings" not in {_registered_tool_name(tool) for tool in tools}


@pytest.mark.asyncio
async def test_agent_includes_sql_only_on_private_admin_surfaces(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agent.server import ADMIN_TOOLS
    from agent.tools import read_only_sql
    from agent.tools.manage_feature_flags import manage_feature_flags

    captured = await _capture_create_deep_agent_kwargs()
    tools = captured["tools"]
    assert isinstance(tools, list)
    assert read_only_sql not in tools
    assert manage_feature_flags not in tools

    monkeypatch.setenv("CONFIGURED_ADMINS", "octocat")
    config = _base_config()
    configurable = config.get("configurable")
    assert isinstance(configurable, dict)
    configurable["admin_thread"] = True
    captured = await _capture_create_deep_agent_kwargs(config)
    tools = captured["tools"]
    assert isinstance(tools, list)
    assert read_only_sql in tools
    assert manage_feature_flags in tools
    subagents = captured["subagents"]
    assert isinstance(subagents, list)
    general_purpose = next(item for item in subagents if item["name"] == "general-purpose")
    assert read_only_sql in general_purpose["tools"]
    assert manage_feature_flags in general_purpose["tools"]

    configurable["source"] = "slack"
    configurable["slack_thread"] = {
        "channel_id": "D123",
        "thread_ts": "1700000000.000100",
        "triggering_user_id": "U123",
        "channel_context": {"is_im": True},
    }
    captured = await _capture_create_deep_agent_kwargs(config)
    tools = captured["tools"]
    assert isinstance(tools, list)
    assert read_only_sql in tools
    assert manage_feature_flags in tools

    assert all(tool in tools for tool in ADMIN_TOOLS)

    configurable["github_login"] = "not-an-admin"
    captured = await _capture_create_deep_agent_kwargs(config)
    tools = captured["tools"]
    assert isinstance(tools, list)
    assert read_only_sql not in tools
    assert manage_feature_flags not in tools
    assert all(tool not in tools for tool in ADMIN_TOOLS)

    configurable["github_login"] = "octocat"
    configurable["source"] = "schedule"
    captured = await _capture_create_deep_agent_kwargs(config)
    tools = captured["tools"]
    assert isinstance(tools, list)
    assert read_only_sql not in tools
    assert manage_feature_flags not in tools


SLACK_TOOL_NAMES = {
    "slack_add_reaction",
    "slack_attach_html",
    "slack_move_thread",
    "slack_list_channel_members",
    "slack_list_channels",
    "slack_no_reply_needed",
    "slack_post_message",
    "slack_read_thread_messages",
    "slack_breakout_thread",
    "slack_reply",
}


@pytest.mark.asyncio
async def test_a_web_turn_on_a_slack_thread_keeps_the_slack_tools() -> None:
    """The tool set cannot move with the surface: that invalidates the cached prefix."""
    config = _base_config()
    configurable = config.get("configurable")
    assert isinstance(configurable, dict)
    configurable.update(
        {
            "source": "dashboard",
            "slack_thread": {"channel_id": "C123", "thread_ts": "1700000000.000100"},
        }
    )

    captured = await _capture_create_deep_agent_kwargs(config)
    tools = captured["tools"]
    assert isinstance(tools, list)

    tool_names = {getattr(tool, "name", None) or getattr(tool, "__name__", None) for tool in tools}
    assert SLACK_TOOL_NAMES <= tool_names
    middleware = captured["middleware"]
    assert isinstance(middleware, list)
    require_reply = next(
        item for item in middleware if type(item).__name__ == "RequireUserReplyMiddleware"
    )
    assert require_reply.before_agent({}, MagicMock())["reply_surface"] == "web"


@pytest.mark.asyncio
async def test_an_automation_run_can_post_to_a_channel_without_a_slack_thread() -> None:
    config = _base_config()
    configurable = config.get("configurable")
    assert isinstance(configurable, dict)
    configurable.update({"source": "schedule", "slack_thread": None})

    captured = await _capture_create_deep_agent_kwargs(config)
    tools = captured["tools"]
    assert isinstance(tools, list)

    tool_names = {getattr(tool, "name", None) or getattr(tool, "__name__", None) for tool in tools}
    # A prompt can ask it to report somewhere; the thread-bound tools stay out.
    assert tool_names & SLACK_TOOL_NAMES == {"slack_list_channels", "slack_post_message"}


@pytest.mark.asyncio
async def test_general_purpose_subagent_cannot_use_slack_tools() -> None:
    config = _base_config()
    configurable = config.get("configurable")
    assert isinstance(configurable, dict)
    configurable.update(
        {
            "source": "slack",
            "slack_thread": {"channel_id": "C123", "thread_ts": "1700000000.000100"},
        }
    )
    captured = await _capture_create_deep_agent_kwargs(config)
    parent_tools = captured["tools"]
    subagents = captured["subagents"]
    assert isinstance(parent_tools, list)
    assert isinstance(subagents, list)

    gp = next(s for s in subagents if s["name"] == "general-purpose")
    assert "cannot access Slack tools" in gp["description"]
    parent_names = {_registered_tool_name(tool) for tool in parent_tools}
    subagent_names = {_registered_tool_name(tool) for tool in gp["tools"]}
    slack_names = {
        "manage_code_channel",
        "manage_incident",
        "slack_add_reaction",
        "slack_attach_html",
        "slack_list_channel_members",
        "slack_list_channels",
        "slack_move_thread",
        "slack_post_message",
        "slack_read_thread_messages",
        "slack_breakout_thread",
        "slack_reply",
    }

    parent_only_names = {
        *slack_names,
        "background_execute",
        "background_task",
        "get_thread",
        "list_threads",
        "manage_thread",
        "read_user_settings",
        "save_user_settings",
        "submit_thread_feedback",
        "submit_review_assessment_feedback",
    }
    assert parent_only_names <= parent_names
    assert subagent_names == parent_names - {"save_user_settings"}

    from unittest.mock import AsyncMock, MagicMock

    from langchain.agents.middleware.types import ToolCallRequest
    from langchain_core.messages import ToolMessage

    guard = next(item for item in gp["middleware"] if item.name == "_SubagentToolGuard")
    handler = AsyncMock(return_value=ToolMessage(content="executed", tool_call_id="allowed"))
    for name in parent_only_names | {
        "read_only_sql",
        "read_incident",
        "search_incidents",
        "record_incident_report",
    }:
        request = MagicMock(spec=ToolCallRequest)
        request.tool_call = {"name": name, "args": {}, "id": name, "type": "tool_call"}
        result = await guard.awrap_tool_call(request, handler)
        assert isinstance(result, ToolMessage)
        assert result.tool_call_id == name
        assert "inside a subagent" in result.content
    handler.assert_not_awaited()
    request.tool_call = {"name": "execute", "args": {}, "id": "allowed", "type": "tool_call"}
    assert (await guard.awrap_tool_call(request, handler)).content == "executed"
    handler.assert_awaited_once_with(request)


@pytest.mark.asyncio
@pytest.mark.parametrize("private_thread", [True, False])
async def test_channel_reads_need_a_private_thread(
    private_thread: bool, saved_thread_scope: dict[str, str]
) -> None:
    saved_thread_scope["visibility"] = "private" if private_thread else "public"
    config = _base_config()
    configurable = config.get("configurable")
    assert isinstance(configurable, dict)
    configurable.update(
        {
            "source": "slack",
            "slack_thread": {"channel_id": "C123", "thread_ts": "1700000000.000100"},
        }
    )

    captured = await _capture_create_deep_agent_kwargs(config)
    tools = captured["tools"]
    subagents = captured["subagents"]
    assert isinstance(tools, list)
    assert isinstance(subagents, list)

    tool_names = {_registered_tool_name(tool) for tool in tools}
    assert ("slack_read_channel_messages" in tool_names) is private_thread
    # A thread-bound Slack read is unaffected either way.
    assert "slack_read_thread_messages" in tool_names

    gp = next(item for item in subagents if item["name"] == "general-purpose")
    subagent_names = {_registered_tool_name(tool) for tool in gp["tools"]}
    assert ("slack_read_channel_messages" in subagent_names) is private_thread


@pytest.fixture
def pinned_settings() -> dict[str, object]:
    return {
        "model_id": "anthropic:claude-opus-5-5",
        "effort": "high",
        "requested_model": "anthropic:claude-opus-5-5",
        "model_handoff_complete": True,
        "model_routing_enabled": False,
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("source", ["dashboard", "slack"])
async def test_requested_model_survives_auto_followups_but_explicit_selection_wins(
    source: str,
    pinned_settings: dict[str, object],
) -> None:
    config = _base_config()
    configurable = config["configurable"]
    configurable.update(
        source=source,
        model_selection="auto",
        agent_model_id="openai:gpt-6.1-sol",
        agent_effort="low",
    )
    await _capture_create_deep_agent_kwargs(config, thread_settings=pinned_settings)
    assert configurable["resolved_agent_model_id"] == "anthropic:claude-opus-5-5"
    configurable.update(
        model_selection="explicit", agent_model_id="openai:gpt-6.1-sol", agent_effort="low"
    )
    await _capture_create_deep_agent_kwargs(config, thread_settings=pinned_settings)
    assert configurable["resolved_agent_model_id"] == "openai:gpt-6.1-sol"


@pytest.mark.parametrize("image_source", ["initial", "retained", "tool"])
@pytest.mark.parametrize("route", ["fast", "balanced", "performance"])
async def test_text_only_adaptive_route_uses_vision_fallback_after_handoff(
    image_source: Literal["initial", "retained", "tool"],
    route: Literal["fast", "balanced", "performance"],
) -> None:
    from agent.middleware.image_model_fallback import ImageModelFallbackMiddleware
    from agent.middleware.model_selection import ModelSelectionMiddleware, ModelSelectionState

    model_id = "fireworks:accounts/fireworks/models/kimi-k3"
    config = _base_config()
    config["configurable"].update(source="dashboard", model_selection="auto")
    captured = await _capture_create_deep_agent_kwargs(
        config,
        thread_settings={
            "model_id": "openai:gpt-6.1-sol",
            "effort": "medium",
            "model_handoff_complete": True,
            "model_routing_enabled": True,
            "routing_models": {
                "fast": {"model_id": "openai:gpt-6.1-sol", "effort": "medium"},
                route: {"model_id": model_id, "effort": "high"},
            },
        },
        make_model=lambda model_id, **_: MagicMock(model_id=model_id),
    )
    middleware = cast(list[object], captured["middleware"])
    selection = next(item for item in middleware if isinstance(item, ModelSelectionMiddleware))
    fallback = next(
        (item for item in middleware if isinstance(item, ImageModelFallbackMiddleware)), None
    )
    image = [{"type": "image_url", "image_url": {"url": "https://example.com/image.png"}}]
    state: ModelSelectionState = {
        "messages": [
            ToolMessage(content=image, tool_call_id="screenshot")
            if image_source == "tool"
            else HumanMessage(content=image)
        ],
        "model_route": route,
    }
    if image_source == "retained":
        state["messages"].append(HumanMessage(content="Explain the screenshot"))
    handler = AsyncMock(return_value=ModelResponse(result=[AIMessage(content="Done")]))

    async def handle_selected(request: ModelRequest) -> ModelResponse:
        if fallback is not None:
            return await fallback.awrap_model_call(request, handler)
        return await handler(request)

    for with_image in (True, False):
        request = ModelRequest(
            model=cast(BaseChatModel, captured["model"]),
            messages=state["messages"] if with_image else [HumanMessage(content="Continue")],
            state=state,
        )
        await selection.awrap_model_call(request, handle_selected)
        actual = handler.call_args.args[0]
        assert actual.model.model_id == ("openai:gpt-6.1-sol" if with_image else model_id)
        assert actual.messages == request.messages


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("profile", "expected_model", "expected_effort"),
    [
        (None, "google_genai:gemini-3.8-flash", "low"),
        (
            {"default_model": "anthropic:claude-opus-5-5", "reasoning_effort": "high"},
            "google_genai:gemini-3.8-flash",
            "low",
        ),
    ],
)
async def test_explicit_auto_selection_clears_pin_and_keeps_routing_on_followups(
    profile: dict[str, object] | None,
    expected_model: str,
    expected_effort: str,
    pinned_settings: dict[str, object],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agent.middleware.model_selection import ModelSelectionMiddleware
    from agent.server import PrepareAgentRunMiddleware

    monkeypatch.setattr("agent.server._model_routing_mode", lambda _: "jev")
    monkeypatch.setattr(
        "agent.middleware.model_selection._select_jev_route", AsyncMock(return_value="default")
    )
    config = _base_config()
    config["configurable"].update(
        source="dashboard", model_selection="auto", model_selection_changed=True
    )
    with patch("agent.server.store_thread_settings", new_callable=AsyncMock) as store:
        captured = await _capture_create_deep_agent_kwargs(
            config, thread_settings=pinned_settings, profile=profile
        )
    snapshot = cast(dict[str, object], store.call_args.args[2])
    assert snapshot["requested_model"] is None
    assert snapshot["model_routing_enabled"] is True
    assert snapshot["model_handoff_complete"] is True
    assert snapshot["model_id"] == expected_model
    assert snapshot["effort"] == expected_effort
    assert snapshot["subagent_model_id"] == (expected_model if profile else "openai:gpt-6.1-sol")
    assert snapshot["subagent_effort"] == "low"

    followup = _base_config()
    followup["configurable"].update(source="dashboard", model_selection="auto")
    followup_agent = await _capture_create_deep_agent_kwargs(followup, thread_settings=snapshot)
    for agent in (captured, followup_agent):
        middleware = cast(list[object], agent["middleware"])
        selection = next(item for item in middleware if isinstance(item, ModelSelectionMiddleware))
        assert await selection.select_route({"messages": []}) == "default"
        assert selection._models["default"] is agent["model"]
        assert agent["make_model_calls"][0][0] == expected_model
        prepare = next(item for item in middleware if isinstance(item, PrepareAgentRunMiddleware))
        assert prepare._requested_models is None


async def test_queued_images_reach_vision_fallback_for_text_only_main_model() -> None:
    from langchain_core.messages import convert_to_messages
    from langgraph.store.memory import InMemoryStore

    from agent.middleware.check_message_queue import (
        LinearNotifyState,
        check_message_queue_before_model,
    )
    from agent.middleware.image_model_fallback import ImageModelFallbackMiddleware

    config = _base_config()
    captured = await _capture_create_deep_agent_kwargs(
        config,
        thread_settings={"model_id": "fireworks:accounts/fireworks/models/kimi-k3"},
        make_model=lambda model_id, **_: MagicMock(model_id=model_id),
    )
    store = InMemoryStore()
    namespace = ("queue", "thread-ctx")
    url = "https://example.com/image.png"
    image = {"type": "image_url", "image_url": {"url": url}}
    await store.aput(
        namespace,
        "pending_messages",
        {"messages": [{"content": {"text": "Explain this", "image_urls": [url]}}]},
    )
    with (
        patch("agent.middleware.check_message_queue.get_config", return_value=config),
        patch("agent.middleware.check_message_queue.get_store", return_value=store),
        patch("agent.middleware.check_message_queue.fetch_image_block", return_value=image),
    ):
        update = await check_message_queue_before_model.abefore_model(
            cast(LinearNotifyState, {"messages": []}), MagicMock()
        )
    assert update is not None
    messages = convert_to_messages(update["messages"])
    content = messages[-1].content
    assert isinstance(content, list) and image in content
    fallback = next(
        item
        for item in cast(list[object], captured["middleware"])
        if isinstance(item, ImageModelFallbackMiddleware)
    )
    handler = AsyncMock(return_value=ModelResponse(result=[AIMessage(content="Done")]))
    await fallback.awrap_model_call(
        ModelRequest(model=cast(BaseChatModel, captured["model"]), messages=messages), handler
    )
    assert handler.call_args.args[0].model is not captured["model"]
    assert handler.call_args.args[0].messages == messages
    assert await store.aget(namespace, "pending_messages") is None
