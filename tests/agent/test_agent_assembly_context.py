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
from typing import cast
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
    "default_agent_model": "openai:gpt-6-sol",
    "default_agent_reasoning_effort": "medium",
    "default_agent_subagent_model": "openai:gpt-6-sol",
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
    private_thread: bool = False,
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
                    "default_agent_routing_balanced_model": "openai:gpt-6-sol",
                    "default_agent_routing_balanced_reasoning_effort": "medium",
                    "default_agent_routing_performance_model": "anthropic:claude-opus-5-5",
                    "default_agent_routing_performance_reasoning_effort": "high",
                }
            ),
        ),
        patch(
            "agent.server._private_thread",
            new_callable=AsyncMock,
            return_value=private_thread,
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
            "model_id": "openai:gpt-6-sol",
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
                "default_agent_routing_fast_model": "openai:gpt-6-sol",
                "default_agent_routing_fast_reasoning_effort": "low",
                "default_agent_routing_balanced_model": "openai:gpt-6-sol",
                "default_agent_routing_balanced_reasoning_effort": "medium",
                "default_agent_routing_performance_model": "openai:gpt-6-sol",
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
async def test_router_failure_uses_same_model_as_routing_off() -> None:
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
    assert agent["make_model_calls"][0][0] == "anthropic:claude-opus-5-5"


@pytest.mark.asyncio
@pytest.mark.parametrize("legacy_thread", [False, True])
async def test_admin_model_changes_only_affect_new_threads(legacy_thread: bool) -> None:
    initial_settings = (
        {"model_id": "openai:gpt-6-sol", "effort": "medium", "model_routing_enabled": True}
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
    "slack_list_channels",
    "slack_no_reply_needed",
    "slack_post_message",
    "slack_read_thread_messages",
    "slack_start_new_thread",
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
        "notify_automation_channel",
        "slack_add_reaction",
        "slack_attach_html",
        "slack_list_channels",
        "slack_move_thread",
        "slack_post_message",
        "slack_read_thread_messages",
        "slack_start_new_thread",
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
async def test_channel_reads_need_a_private_thread(private_thread: bool) -> None:
    config = _base_config()
    configurable = config.get("configurable")
    assert isinstance(configurable, dict)
    configurable.update(
        {
            "source": "slack",
            "slack_thread": {"channel_id": "C123", "thread_ts": "1700000000.000100"},
        }
    )

    captured = await _capture_create_deep_agent_kwargs(config, private_thread=private_thread)
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


@pytest.mark.asyncio
@pytest.mark.parametrize("source", ["dashboard", "slack"])
async def test_requested_model_survives_auto_followups_but_explicit_selection_wins(
    source: str,
) -> None:
    config = _base_config()
    configurable = config["configurable"]
    configurable.update(
        source=source,
        model_selection="auto",
        agent_model_id="openai:gpt-6-sol",
        agent_effort="low",
    )
    settings = {
        "model_id": "anthropic:claude-opus-5-5",
        "effort": "high",
        "requested_model": "anthropic:claude-opus-5-5",
        "model_handoff_complete": True,
        "model_routing_enabled": False,
    }
    await _capture_create_deep_agent_kwargs(config, thread_settings=settings)
    assert configurable["resolved_agent_model_id"] == "anthropic:claude-opus-5-5"
    configurable.update(
        model_selection="explicit", agent_model_id="openai:gpt-6-sol", agent_effort="low"
    )
    await _capture_create_deep_agent_kwargs(config, thread_settings=settings)
    assert configurable["resolved_agent_model_id"] == "openai:gpt-6-sol"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "pinned_model",
    ["fireworks:accounts/fireworks/models/kimi-k3", "anthropic:claude-opus-5-5"],
)
async def test_image_fallback_temporarily_overrides_only_incompatible_pinned_models(
    pinned_model: str,
) -> None:
    from agent.middleware.image_model_fallback import ImageModelFallbackMiddleware

    config = _base_config()
    config["configurable"].update(
        source="dashboard",
        model_selection="auto",
        agent_model_id="openai:gpt-6-sol",
        agent_effort="medium",
        model_override_reason="image_input",
    )
    with patch("agent.server.store_thread_settings", new_callable=AsyncMock) as store:
        await _capture_create_deep_agent_kwargs(
            config,
            thread_settings={
                "model_id": pinned_model,
                "effort": "high",
                "subagent_model_id": "google_genai:gemini-3.8-flash",
                "subagent_effort": "low",
                "requested_model": pinned_model,
                "model_handoff_complete": True,
                "model_routing_enabled": False,
            },
        )
    expected = "openai:gpt-6-sol" if pinned_model.endswith("kimi-k3") else pinned_model
    assert config["configurable"]["resolved_agent_model_id"] == expected
    snapshot = cast(dict[str, object], store.call_args.args[2])
    assert snapshot["model_id"] == pinned_model
    assert snapshot["effort"] == "high"
    assert snapshot["requested_model"] == pinned_model
    assert snapshot["subagent_model_id"] == "google_genai:gemini-3.8-flash"
    assert snapshot["subagent_effort"] == "low"

    followup = _base_config()
    followup["configurable"].update(source="dashboard", model_selection="auto")
    captured = await _capture_create_deep_agent_kwargs(
        followup,
        thread_settings=snapshot,
        make_model=lambda model_id, **_: MagicMock(model_id=model_id),
    )
    primary = cast(BaseChatModel, captured["model"])
    fallbacks = [
        item
        for item in cast(list[object], captured["middleware"])
        if isinstance(item, ImageModelFallbackMiddleware)
    ]
    screenshot = HumanMessage(
        content=[{"type": "image_url", "image_url": {"url": "https://example.com/image.png"}}]
    )
    followup_message = HumanMessage(content="Explain the screenshot in more detail")
    handler = AsyncMock(return_value=ModelResponse(result=[AIMessage(content="Done")]))
    for retained_images in (True, False):
        request = ModelRequest(
            model=primary,
            messages=[screenshot, followup_message] if retained_images else [followup_message],
            state={"messages": [screenshot, followup_message]},
        )
        if fallbacks:
            await fallbacks[0].awrap_model_call(request, handler)
        else:
            await handler(request)
        actual = handler.call_args.args[0]
        assert actual.model.model_id == (expected if retained_images else pinned_model)
        assert actual.messages == request.messages


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("default_model", "requested_model", "image_model"),
    [
        pytest.param(
            "google_genai:gemini-3.8-flash",
            "fireworks:accounts/fireworks/models/kimi-k3",
            "google_genai:gemini-3.8-flash",
            id="vision-default",
        ),
        pytest.param(
            "fireworks:accounts/fireworks/models/kimi-k3",
            "fireworks:accounts/fireworks/models/kimi-k3",
            None,
            id="text-only-default",
        ),
        pytest.param(
            "fireworks:accounts/fireworks/models/kimi-k3",
            "anthropic:claude-opus-5-5",
            "anthropic:claude-opus-5-5",
            id="vision-request",
        ),
    ],
)
async def test_requested_model_uses_vision_fallback_for_image_tool_results(
    default_model: str, requested_model: str, image_model: str | None
) -> None:
    from agent.dashboard.options import default_vision_model_pair
    from agent.middleware.image_model_fallback import ImageModelFallbackMiddleware
    from agent.middleware.model_selection import ModelSelectionMiddleware, ModelSelectionState

    config = _base_config()
    config["configurable"].update(source="dashboard")
    captured = await _capture_create_deep_agent_kwargs(
        config,
        profile={"model_routing_enabled": True},
        workspace_settings=WorkspaceSettings(
            {**_MODEL_DEFAULTS, "default_agent_model": default_model}
        ),
        make_model=lambda model_id, **_: MagicMock(model_id=model_id),
    )
    middleware = cast(list[object], captured["middleware"])
    selection = next(item for item in middleware if isinstance(item, ModelSelectionMiddleware))
    fallback = next(
        (item for item in middleware if isinstance(item, ImageModelFallbackMiddleware)), None
    )
    state: ModelSelectionState = {
        "messages": [HumanMessage(content="Read the screenshot")],
        "requested_model": requested_model,
    }
    with patch(
        "agent.server.make_model", side_effect=lambda model_id, **_: MagicMock(model_id=model_id)
    ):
        state.update(await selection.abefore_model(state, MagicMock()))

    handler = AsyncMock(return_value=ModelResponse(result=[AIMessage(content="Done")]))

    async def handle_selected(request: ModelRequest) -> ModelResponse:
        if fallback is not None:
            return await fallback.awrap_model_call(request, handler)
        return await handler(request)

    screenshot = ToolMessage(
        name="read_file",
        tool_call_id="read-screenshot",
        content=[{"type": "image_url", "image_url": {"url": "https://example.com/image.png"}}],
    )
    for with_image in (False, True, False):
        request = ModelRequest(
            model=cast(BaseChatModel, captured["model"]),
            messages=[*state["messages"], screenshot] if with_image else state["messages"],
            state=state,
        )
        await selection.awrap_model_call(request, handle_selected)
        actual = handler.call_args.args[0]
        expected = (
            (image_model or default_vision_model_pair()[0]) if with_image else requested_model
        )
        assert actual.model.model_id == expected
        assert actual.messages == request.messages


@pytest.mark.asyncio
async def test_explicit_auto_selection_clears_pin_and_keeps_routing_on_followups() -> None:
    from agent.middleware.model_selection import ModelSelectionMiddleware
    from agent.server import PrepareAgentRunMiddleware

    config = _base_config()
    config["configurable"].update(
        source="dashboard", model_selection="auto", model_selection_changed=True
    )
    settings = {
        "model_id": "anthropic:claude-opus-5-5",
        "effort": "high",
        "requested_model": "anthropic:claude-opus-5-5",
        "model_handoff_complete": True,
        "model_routing_enabled": False,
    }
    with patch("agent.server.store_thread_settings", new_callable=AsyncMock) as store:
        captured = await _capture_create_deep_agent_kwargs(config, thread_settings=settings)
    snapshot = cast(dict[str, object], store.call_args.args[2])
    assert snapshot["requested_model"] is None
    assert snapshot["model_routing_enabled"] is True
    assert snapshot["model_handoff_complete"] is True

    followup = _base_config()
    followup["configurable"].update(source="dashboard", model_selection="auto")
    followup_agent = await _capture_create_deep_agent_kwargs(followup, thread_settings=snapshot)
    for agent in (captured, followup_agent):
        middleware = cast(list[object], agent["middleware"])
        assert any(isinstance(item, ModelSelectionMiddleware) for item in middleware)
        prepare = next(item for item in middleware if isinstance(item, PrepareAgentRunMiddleware))
        assert prepare._requested_models is None
