"""What the main agent's planner hands an MDA run: its tools, middleware, models and subagents."""

import json
from collections.abc import Callable
from types import SimpleNamespace
from typing import Literal, cast
from unittest.mock import ANY, AsyncMock, MagicMock, patch

import langgraph_sdk
import pytest
from langchain_core.language_models import BaseChatModel
from langgraph.graph.state import RunnableConfig

from openswe.agent_api.loop import ModelChoices
from openswe.dashboard.workspace_settings import WorkspaceSettings
from openswe.middleware.model_selection import RouteSelection
from openswe.sandboxes.state import SANDBOX_BACKENDS
from openswe.sandboxes.tool_runtime import ToolSurface
from openswe.server import _registered_tool_name, plan_agent
from openswe.threads.inbox import ThreadInbox

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
    with patch("openswe.server._mcp_tools_for", new_callable=AsyncMock, return_value=[]) as mcps:
        captured = await _plan_agent(config)
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
    mcps.assert_awaited_once_with(None, ANY, None)


async def test_remote_agent_lists_personal_skills(monkeypatch: pytest.MonkeyPatch) -> None:
    from runtime_api.operations import RunRequest
    from runtime_api.skills import RunSkills

    from openswe.agent_api.agent import AGENT
    from openswe.agent_api.tokens import RemoteRun
    from openswe.skill_store.store import _record

    monkeypatch.setattr(
        "openswe.skill_store.backend.agent_skills",
        AsyncMock(
            side_effect=lambda login: (
                [_record("personal-workflow", "A workflow", "Do it.")] if login else []
            )
        ),
    )
    captured = await _plan_agent()
    surface = cast(ToolSurface, captured["surface"])
    configurable = dict(_base_config()["configurable"])
    run = RemoteRun(thread_id="thread-ctx", assistant_id="agent", configurable=configurable)

    # Listed from the run's token alone: no plan of the run is made for it.
    listed = RunSkills.model_validate(await AGENT.api(run, "skills", RunRequest().model_dump()))

    personal = next(f for f in listed.files if f.path == "/skills/personal-workflow/SKILL.md")
    assert "Do it." in personal.content
    # The runtime answers reads of skills itself; the files API serves none of them.
    assert not set(surface.skill_sources) & set(surface.backend_paths)


@pytest.mark.parametrize("profile", [None, {"experimental_monitor": False}])
async def test_monitor_is_available_without_opt_in(profile: dict[str, object] | None) -> None:
    captured = await _plan_agent(profile=profile)
    tools = captured["tools"]
    assert isinstance(tools, list)
    assert "monitor" in {_registered_tool_name(tool) for tool in tools}


async def test_offloaded_images_are_served_from_a_thread_scoped_store() -> None:
    from deepagents.backends.store import StoreBackend

    captured = await _plan_agent()
    blobs = captured["backend"].routes["/blobs/"]
    assert isinstance(blobs, StoreBackend)
    assert blobs._namespace(MagicMock()) == ("thread_blobs", "thread-ctx")
    surface = cast(ToolSurface, captured["surface"])
    assert "/blobs/" in surface.backend_paths


@pytest.mark.parametrize(
    ("binding", "connected", "available"),
    [
        ({}, False, True),
        ({"sandbox_id": "bridge:desktop", "sandbox_bridge_client": "desktop"}, True, True),
        ({"sandbox_id": "bridge:cli", "sandbox_bridge_client": "cli"}, True, True),
        ({"sandbox_id": "bridge:cli", "sandbox_bridge_client": "cli"}, False, False),
    ],
)
async def test_task_tools_require_a_served_sandbox(
    saved_thread_scope: dict[str, object],
    binding: dict[str, str],
    connected: bool,
    available: bool,
) -> None:
    saved_thread_scope.update(owner_type="user", owner_login="owner")
    saved_thread_scope.update(binding)

    with patch("openswe.tasks.schemas.BridgeStore.is_connected", return_value=connected):
        captured = await _plan_agent()
    tools = captured["tools"]
    assert isinstance(tools, list)
    names = {_registered_tool_name(tool) for tool in tools}
    assert ("control_worker" in names) is available


@pytest.mark.parametrize("role", ["coordinator", "worker"])
async def test_existing_task_keeps_controls_without_delegation(
    saved_thread_scope, role: Literal["coordinator", "worker"]
):
    from openswe.tasks.store import Task, TaskContext, TaskMembership
    from openswe.workspaces.rows import WorkspaceRow

    saved_thread_scope.update(
        owner_type="user", owner_login="owner", sandbox_id="bridge:cli", sandbox_bridge_client="cli"
    )
    workspace = WorkspaceRow(slug="default", name="Default")
    task = Task(
        coordinator_thread_id="coordinator", title="Existing task", workspace_id=workspace.id
    )
    task.workspace = workspace
    context = TaskContext(task, TaskMembership(thread_id="thread-ctx", task_id=task.id, role=role))
    with patch(
        "openswe.middleware.task_coordination.TaskMembership.context_for_thread",
        return_value=context,
    ):
        captured = await _plan_agent()
    tools = captured["tools"]
    assert isinstance(tools, list)
    names = {_registered_tool_name(tool) for tool in tools}
    assert {"control_worker", "message_task_thread", "task_status"} <= names


@pytest.mark.asyncio
async def test_unknown_scope_omits_workspace_and_personal_mcps():
    with (
        patch("openswe.server.private_credential_login", side_effect=TimeoutError),
        patch("openswe.server._mcp_tools_for", new_callable=AsyncMock) as mcps,
    ):
        await _plan_agent()
    mcps.assert_not_awaited()


def _base_config() -> RunnableConfig:
    return {
        "configurable": {
            "__is_for_execution__": True,
            "thread_id": "thread-ctx",
            "github_login": "octocat",
        },
        "metadata": {},
    }


async def _plan_agent(
    config: RunnableConfig | None = None,
    *,
    profile: dict[str, object] | None = None,
    thread_settings: dict[str, object] | None = None,
    workspace_settings: WorkspaceSettings | None = None,
    make_model: Callable[..., BaseChatModel] | None = None,
    surface: ToolSurface | None = None,
) -> dict[str, object]:
    """The run ``config`` plans, with the parts tests read pulled out of its middleware."""

    surface = surface or ToolSurface()
    make_model_calls: list[tuple[str, dict[str, object]]] = []
    config = config or _base_config()
    thread_id = str((config.get("configurable") or {}).get("thread_id"))

    def fake_make_model(model_id: str, **kwargs: object) -> MagicMock | BaseChatModel:
        make_model_calls.append((model_id, kwargs))
        return MagicMock() if make_model is None else make_model(model_id, **kwargs)

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
        patch("openswe.server.load_profile", new_callable=AsyncMock, return_value=profile),
        patch(
            "openswe.server.load_thread_settings",
            new_callable=AsyncMock,
            return_value=thread_settings or {},
        ),
        patch("openswe.server.fallback_model_id_for", return_value=None),
        patch("openswe.server.make_model", side_effect=fake_make_model),
        patch("openswe.server.construct_system_prompt", return_value="prompt"),
    ):
        await plan_agent(config, surface)

    SANDBOX_BACKENDS.pop(thread_id, None)
    middleware = list(surface.middleware)
    return {
        "surface": surface,
        "tools": list(surface.base_tools),
        "middleware": middleware,
        "subagents": list(surface.subagents),
        "backend": surface.files,
        "skills": list(surface.skill_sources),
        "make_model_calls": make_model_calls,
    }


@pytest.mark.asyncio
async def test_existing_thread_reloads_sender_draft_preference_into_run_config(
    saved_thread_scope,
) -> None:
    saved_thread_scope["owner_login"] = "draft-preference-owner"
    config = _base_config()
    configurable = config.get("configurable")
    assert isinstance(configurable, dict)
    configurable["github_login"] = "draft-preference-owner"

    await _plan_agent(
        config,
        profile={"draft_prs": False},
        thread_settings={
            "owner_login": "draft-preference-owner",
            "model_id": "openai:gpt-6.1-sol",
        },
    )

    assert configurable["draft_prs"] is False


@pytest.mark.asyncio
async def test_router_failure_uses_fast_tier_not_profile_default() -> None:
    config = _base_config()
    config["configurable"].update(thread_id="thread-1", source="dashboard", model_selection="auto")
    profile = {
        "default_model": "anthropic:claude-opus-5-5",
        "reasoning_effort": "high",
        "model_routing_enabled": True,
    }
    agent = await _plan_agent(config, profile=profile)
    model_selection = next(
        item
        for item in cast(list[object], agent["middleware"])
        if type(item).__name__ == "ModelSelectionMiddleware"
    )
    route = await model_selection.select_route({"messages": []})

    assert route.route == "default"
    assert agent["make_model_calls"][0][0] == "google_genai:gemini-3.8-flash"


@pytest.mark.asyncio
@pytest.mark.parametrize("legacy_thread", [False, True])
async def test_admin_model_changes_only_affect_new_threads(legacy_thread: bool) -> None:
    initial_settings = (
        {"model_id": "openai:gpt-6.1-sol", "effort": "medium", "model_routing_enabled": True}
        if legacy_thread
        else {}
    )
    with patch("openswe.server.store_thread_settings", new_callable=AsyncMock) as store:
        original = await _plan_agent(
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
    existing = await _plan_agent(thread_settings=snapshot, workspace_settings=changed_defaults)
    fresh_config = _base_config()
    fresh_config["configurable"]["thread_id"] = "new-thread"
    fresh = await _plan_agent(fresh_config, workspace_settings=changed_defaults)

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
    captured = await _plan_agent(config)
    tools = captured["tools"]
    assert isinstance(tools, list)
    assert "save_user_settings" not in {_registered_tool_name(tool) for tool in tools}


def _tool_names(tools: object) -> set[str]:
    assert isinstance(tools, list)
    return {_registered_tool_name(tool) for tool in tools}


@pytest.mark.asyncio
async def test_agent_includes_sql_only_on_private_admin_surfaces(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from openswe.server import ADMIN_TOOLS
    from openswe.tools import execute_database_sql, read_only_sql

    monkeypatch.setenv("OPENSWE_ENV", "preview")
    from openswe.tools.manage_feature_flags import manage_feature_flags

    captured = await _plan_agent()
    tools = _tool_names(captured["tools"])
    assert read_only_sql.__name__ not in tools
    assert execute_database_sql.__name__ not in tools
    assert manage_feature_flags.__name__ not in tools

    monkeypatch.setenv("CONFIGURED_ADMINS", "octocat")
    config = _base_config()
    configurable = config.get("configurable")
    assert isinstance(configurable, dict)
    configurable["admin_thread"] = True
    captured = await _plan_agent(config)
    tools = _tool_names(captured["tools"])
    assert read_only_sql.__name__ in tools
    assert execute_database_sql.__name__ in tools
    assert manage_feature_flags.__name__ in tools
    subagents = captured["subagents"]
    assert isinstance(subagents, list)
    general_purpose = next(item for item in subagents if item.name == "general-purpose")
    assert general_purpose.tools is not None
    # SQL needs the parent; the subagent keeps the rest.
    assert read_only_sql.__name__ not in general_purpose.tools
    assert execute_database_sql.__name__ not in general_purpose.tools
    assert manage_feature_flags.__name__ in general_purpose.tools

    configurable["source"] = "slack"
    configurable["slack_thread"] = {
        "channel_id": "D123",
        "thread_ts": "1700000000.000100",
        "triggering_user_id": "U123",
        "channel_context": {"is_im": True},
    }
    captured = await _plan_agent(config)
    tools = _tool_names(captured["tools"])
    assert read_only_sql.__name__ in tools
    assert execute_database_sql.__name__ in tools
    assert manage_feature_flags.__name__ in tools

    assert all(tool.__name__ in tools for tool in ADMIN_TOOLS)

    configurable["github_login"] = "not-an-admin"
    captured = await _plan_agent(config)
    tools = _tool_names(captured["tools"])
    assert read_only_sql.__name__ not in tools
    assert execute_database_sql.__name__ not in tools
    assert manage_feature_flags.__name__ not in tools
    assert all(tool.__name__ not in tools for tool in ADMIN_TOOLS)

    configurable["github_login"] = "octocat"
    configurable["source"] = "schedule"
    captured = await _plan_agent(config)
    tools = _tool_names(captured["tools"])
    assert read_only_sql.__name__ not in tools
    assert execute_database_sql.__name__ not in tools
    assert manage_feature_flags.__name__ not in tools


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
@pytest.mark.parametrize(
    ("source", "slack_thread", "slack_enabled"),
    [
        ("dashboard", {"channel_id": "C123", "thread_ts": "1700000000.000100"}, True),
        ("github", {"channel_id": "C123", "thread_ts": "1700000000.000100"}, True),
        ("github", None, False),
        ("github", {}, False),
        ("github", {"channel_id": "C123"}, False),
        ("github", {"thread_ts": "1700000000.000100"}, False),
    ],
)
async def test_web_reply_turns_require_slack_thread_context_for_slack_tools(
    source: str, slack_thread: dict[str, str] | None, slack_enabled: bool
) -> None:
    """The tool set cannot move with the surface: that invalidates the cached prefix."""
    config = _base_config()
    configurable = config.get("configurable")
    assert isinstance(configurable, dict)
    configurable.update(
        {
            "source": source,
            "slack_thread": slack_thread,
        }
    )

    captured = await _plan_agent(config)
    tools = captured["tools"]
    assert isinstance(tools, list)

    tool_names = {getattr(tool, "name", None) or getattr(tool, "__name__", None) for tool in tools}
    assert tool_names & SLACK_TOOL_NAMES == (SLACK_TOOL_NAMES if slack_enabled else set())
    middleware = captured["middleware"]
    assert isinstance(middleware, list)
    require_reply = next(
        item for item in middleware if type(item).__name__ == "RequireUserReplyMiddleware"
    )
    prepared = await require_reply.abefore_agent({}, MagicMock())
    assert prepared["reply_surface"] == "web"


@pytest.mark.asyncio
async def test_an_automation_run_can_post_to_a_channel_without_a_slack_thread() -> None:
    config = _base_config()
    configurable = config.get("configurable")
    assert isinstance(configurable, dict)
    configurable.update({"source": "schedule", "slack_thread": None})

    captured = await _plan_agent(config)
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
    captured = await _plan_agent(config)
    parent_tools = captured["tools"]
    subagents = captured["subagents"]
    assert isinstance(parent_tools, list)
    assert isinstance(subagents, list)

    gp = next(item for item in subagents if item.name == "general-purpose")
    assert "cannot access Slack tools" in gp.description
    assert gp.tools is not None
    parent_names = {_registered_tool_name(tool) for tool in parent_tools}
    subagent_names = set(gp.tools)
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
    assert not subagent_names & parent_only_names


@pytest.mark.asyncio
@pytest.mark.parametrize("thread_scope", ["private", "public", "bot"])
async def test_channel_reads_need_a_private_thread_or_its_triggering_bot(
    thread_scope: str, saved_thread_scope: dict[str, object]
) -> None:
    saved_thread_scope["visibility"] = "private" if thread_scope == "private" else "public"
    config = _base_config()
    configurable = config.get("configurable")
    assert isinstance(configurable, dict)
    configurable.update(
        {
            "source": "slack",
            "slack_thread": {"channel_id": "C123", "thread_ts": "1700000000.000100"},
        }
    )
    if thread_scope == "bot":
        slack_thread = {
            "channel_id": "C123",
            "thread_ts": "1700000000.000100",
            "triggering_bot_id": "B1",
        }
        configurable.update(slack_thread=slack_thread, github_login=None)
        saved_thread_scope.update(
            owner_type="system", source_context={"slack_thread": slack_thread}
        )

    captured = await _plan_agent(config)
    tools = captured["tools"]
    subagents = captured["subagents"]
    assert isinstance(tools, list)
    assert isinstance(subagents, list)

    tool_names = {_registered_tool_name(tool) for tool in tools}
    assert ("slack_read_channel_messages" in tool_names) is (thread_scope != "public")
    # A thread-bound Slack read is unaffected either way.
    assert "slack_read_thread_messages" in tool_names

    gp = next(item for item in subagents if item.name == "general-purpose")
    assert ("slack_read_channel_messages" in (gp.tools or ())) is (thread_scope != "public")


@pytest.fixture
def pinned_settings() -> dict[str, object]:
    return {
        "model_id": "anthropic:claude-opus-5-5",
        "effort": "high",
        "requested_model": "anthropic:claude-opus-5-5",
        "model_handoff_complete": True,
        "model_routing_enabled": False,
    }


@pytest.mark.parametrize("followup_source", ["slack", "dashboard"])
async def test_explicit_breakout_model_survives_startup_and_followup(
    followup_source: str,
) -> None:
    from openswe.server import PrepareAgentRunMiddleware

    config = _base_config()
    config["configurable"].update(
        source="slack",
        slack_breakout=True,
        model_selection="explicit",
        agent_model_id="anthropic:claude-opus-5-5",
        agent_effort="max",
    )
    with patch("openswe.server.store_thread_settings", new_callable=AsyncMock) as store:
        initial = await _plan_agent(config)
    snapshot = cast(dict[str, object], store.call_args.args[2])
    assert snapshot["model_handoff_complete"] is True
    assert snapshot["model_routing_enabled"] is False
    followup_config = _base_config()
    followup_config["configurable"].update(source=followup_source, model_selection="auto")
    followup = await _plan_agent(followup_config, thread_settings=snapshot)
    for agent in (initial, followup):
        calls = cast(list[tuple[str, dict[str, object]]], agent["make_model_calls"])
        assert calls[0][0] == "anthropic:claude-opus-5-5"
        assert calls[0][1]["effort"] == "max"
        prepare = next(
            item
            for item in cast(list[object], agent["middleware"])
            if isinstance(item, PrepareAgentRunMiddleware)
        )
        assert prepare._requested_models is None


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
    await _plan_agent(config, thread_settings=pinned_settings)
    assert configurable["resolved_agent_model_id"] == "anthropic:claude-opus-5-5"
    configurable.update(
        model_selection="explicit", agent_model_id="openai:gpt-6.1-sol", agent_effort="low"
    )
    await _plan_agent(config, thread_settings=pinned_settings)
    assert configurable["resolved_agent_model_id"] == "openai:gpt-6.1-sol"


@pytest.mark.parametrize("route", ["fast", "balanced", "performance"])
async def test_text_only_adaptive_route_names_a_vision_model_after_handoff(
    route: Literal["fast", "balanced", "performance"],
) -> None:
    from openswe.agent_api.graph import RunSession

    model_id = "fireworks:accounts/fireworks/models/kimi-k3"
    config = _base_config()
    config["configurable"].update(source="dashboard", model_selection="auto")
    planned = await _plan_agent(
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
    )
    session = RunSession(cast(ToolSurface, planned["surface"]), {}, None, started_here=True)
    choices = ModelChoices(session.start_models())

    routed = choices.model_for({"model_route": route})
    vision = choices.vision_for(routed)

    assert routed.model_id == model_id
    assert vision is not None and vision.model_id == "openai:gpt-6.1-sol"
    assert choices.vision_for(vision) is None


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
    from openswe.middleware.model_selection import ModelSelectionMiddleware
    from openswe.server import PrepareAgentRunMiddleware

    monkeypatch.setattr("openswe.server._model_routing_mode", lambda _: "auto")
    monkeypatch.setattr(
        "openswe.middleware.model_selection._select_jev_route",
        AsyncMock(return_value=RouteSelection("default")),
    )
    config = _base_config()
    config["configurable"].update(
        source="dashboard", model_selection="auto", model_selection_changed=True
    )
    with patch("openswe.server.store_thread_settings", new_callable=AsyncMock) as store:
        captured = await _plan_agent(config, thread_settings=pinned_settings, profile=profile)
    snapshot = cast(dict[str, object], store.call_args.args[2])
    assert snapshot["requested_model"] is None
    assert snapshot["model_routing_enabled"] is True
    assert snapshot["model_handoff_complete"] is True
    assert snapshot["model_id"] == expected_model
    assert snapshot["effort"] == expected_effort
    assert snapshot["subagent_model_id"] == "openai:gpt-6.1-sol"
    assert snapshot["subagent_effort"] == "low"

    followup = _base_config()
    followup["configurable"].update(source="dashboard", model_selection="auto")
    followup_agent = await _plan_agent(followup, thread_settings=snapshot)
    for agent in (captured, followup_agent):
        middleware = cast(list[object], agent["middleware"])
        selection = next(item for item in middleware if isinstance(item, ModelSelectionMiddleware))
        assert (await selection.select_route({"messages": []})).route == "default"
        assert agent["make_model_calls"][0][0] == expected_model
        prepare = next(item for item in middleware if isinstance(item, PrepareAgentRunMiddleware))
        assert prepare._requested_models is None


@pytest.mark.parametrize("reply_thread_ts", ["", "123.456"])
async def test_slack_question_allows_auto_routing_after_dashboard_handoff(
    monkeypatch: pytest.MonkeyPatch, reply_thread_ts: str
) -> None:
    from openswe.middleware.model_selection import ModelSelectionMiddleware
    from openswe.threads.runs import _build_dashboard_configurable

    monkeypatch.setattr("openswe.threads.runs.resolve_run_email", AsyncMock(return_value=None))
    monkeypatch.setattr("openswe.server._model_routing_mode", lambda _: "fast")
    source_context = {
        "slack_ask": True,
        "slack_thread": {"channel_id": "C1", "triggering_user_id": "U1"},
        "slack_by_the_way_thread_ts": reply_thread_ts,
    }
    for source in ("slack", "dashboard"):
        config = _base_config()
        config["configurable"].update(
            await _build_dashboard_configurable(
                "thread-ctx",
                "octocat",
                {"source": source, "source_context": source_context, "model_selection": "auto"},
                profile={},
            )
        )
        captured = await _plan_agent(config, profile={"model_routing_enabled": True})
        selection = next(
            item
            for item in cast(list[object], captured["middleware"])
            if isinstance(item, ModelSelectionMiddleware)
        )
        assert (await selection.select_route({"messages": []})).route == (
            "fast" if source == "dashboard" else "default"
        )


async def test_queued_images_reach_vision_fallback_for_text_only_main_model(
    registry_db: None,
) -> None:
    from langchain_core.messages import convert_to_messages

    from openswe.agent_api.graph import RunSession
    from openswe.message_queue import QueuedMessage

    config = _base_config()
    planned = await _plan_agent(
        config,
        thread_settings={"model_id": "fireworks:accounts/fireworks/models/kimi-k3"},
    )
    url = "https://example.com/image.png"
    image = {"type": "image_url", "image_url": {"url": url}}
    await QueuedMessage.put("thread-ctx", {"text": "Explain this", "image_urls": [url]})
    surface = cast(ToolSurface, planned["surface"])
    assert surface.inbox is not None
    with patch("openswe.threads.inbox.fetch_image_block", return_value=image):
        taken = await ThreadInbox("thread-ctx").read("run-1", surface.inbox)
    messages = convert_to_messages(taken.messages)
    content = messages[-1].content
    assert isinstance(content, list) and image in content
    models = RunSession(surface, {}, None, started_here=True).start_models()
    vision = ModelChoices(models).vision_for(models.model)
    assert vision is not None and vision.model_id != models.model.model_id


@pytest.mark.asyncio
async def test_historical_personal_models_and_routing_do_not_override_workspace() -> None:
    captured = await _plan_agent(
        profile={
            "default_model": "anthropic:claude-opus-5-5",
            "reasoning_effort": "high",
            "default_subagent_model": "anthropic:claude-opus-5-5",
            "subagent_reasoning_effort": "high",
            "model_routing_enabled": True,
        }
    )
    assert captured["make_model_calls"][0][0] == "openai:gpt-6.1-sol"
    assert captured["make_model_calls"][0][1]["reasoning"]["effort"] == "medium"


async def test_anthropic_title_model_omits_thinking_without_affecting_agent() -> None:
    captured = await _plan_agent(
        workspace_settings=WorkspaceSettings(
            {
                **_MODEL_DEFAULTS,
                "default_agent_model": "anthropic:claude-opus-5-5",
                "default_agent_reasoning_effort": "high",
                "default_thread_title_model": "anthropic:claude-haiku-5-5",
                "default_thread_title_reasoning_effort": "high",
            }
        )
    )
    calls = cast(list[tuple[str, dict[str, object]]], captured["make_model_calls"])
    title_kwargs = next(kwargs for model, kwargs in calls if model == "anthropic:claude-haiku-5-5")
    assert "thinking" not in title_kwargs
    assert title_kwargs["effort"] == "high"
    assert title_kwargs["max_tokens"] == 1024
    agent_kwargs = next(kwargs for model, kwargs in calls if model == "anthropic:claude-opus-5-5")
    assert agent_kwargs["thinking"] == {"type": "adaptive", "display": "summarized"}


@pytest.mark.parametrize("mode", ["modern", "oversized", "grantless"])
async def test_remote_preparation_reuses_signed_inputs_but_rechecks_access(
    monkeypatch: pytest.MonkeyPatch, mode: str
) -> None:
    from langchain.agents.middleware.types import AgentState
    from langgraph.runtime import Runtime
    from runtime_api.loop import PhaseResult, Prepare

    import openswe.server as server
    from openswe.agent_api.agent import AgentParts
    from openswe.agent_api.graph import RemoteGraph, RunSession
    from openswe.agent_api.tokens import (
        RemoteRun,
        sign_runtime_token,
        verify_runtime_token,
    )
    from openswe.tools.access import Access

    monkeypatch.setenv("REMOTE_RUNTIME_TOKEN_SECRET", "preparation-regression-secret-0123456789")
    monkeypatch.setattr("openswe.agent_api.graph.ToolSet.keep", AsyncMock(return_value="tools"))
    monkeypatch.setattr(
        "openswe.agent_api.graph.ManagedSandbox.for_thread", AsyncMock(return_value=None)
    )
    oversized = mode == "oversized"
    instructions = "repository rules" if not oversized else "x" * 8192
    pinned = {
        "model_id": "openai:gpt-6.1-sol",
        "effort": "medium",
        "model_handoff_complete": True,
        "repo_instructions": instructions,
    }
    planned = 0

    async def build(config: RunnableConfig, surface: ToolSurface) -> None:
        nonlocal planned
        planned += 1
        await _plan_agent(config, thread_settings=pinned, surface=surface)

    parts = AgentParts()
    dispatch = RemoteGraph("agent", build, parts)
    run = RemoteRun(
        thread_id="thread-ctx", assistant_id="agent", configurable={"github_login": "octocat"}
    ).granting()
    started = await dispatch.start_run(run)
    if mode == "grantless":
        run = run.model_copy(update={"grant_id": None})
    run = verify_runtime_token(
        sign_runtime_token(run.model_copy(update={"settings": started.settings}))
    )
    assert (run.settings.preparation is None) is oversized
    assert "ghp" not in run.settings.model_dump_json()
    if not oversized:
        assert run.settings.preparation is not None
        assert run.settings.preparation.repo_instructions == instructions

    async def elsewhere(config: RunnableConfig, surface: ToolSurface) -> None:
        if mode == "modern":
            pytest.fail("A replica repeated immutable startup planning")
        await build(config, surface)

    prepared: list[tuple[str | None, bool, str | None]] = []

    async def per_turn(
        self: server.PrepareAgentRunMiddleware, state: AgentState, runtime: Runtime
    ) -> dict[str, object]:
        prepared.append((self._credential_login, self._sole_writer, self._repo_instructions))
        return {"rendered_system_prompt": self._model_id}

    monkeypatch.setattr(server.PrepareAgentRunMiddleware, "_prepare", per_turn)
    monkeypatch.setattr(server, "make_model", MagicMock())
    monkeypatch.setattr(server, "private_credential_login", AsyncMock(return_value="live-owner"))
    monkeypatch.setattr(server, "_admin_thread", AsyncMock(return_value=False))
    monkeypatch.setattr(server, "resolve_access", AsyncMock(return_value=Access(sole=False)))
    bound = AsyncMock()
    monkeypatch.setattr(RunSession, "bind_sandbox", bound)
    monkeypatch.setattr("openswe.agent_api.graph.TurnGitMetadata.begin", AsyncMock(return_value={}))
    replica = RemoteGraph("agent", elsewhere, parts)
    for turn in range(2):
        result = PhaseResult.model_validate(
            await replica.api(
                run, "prepare", Prepare(run_id=f"run-{turn}", sandbox_id="mda-box").model_dump()
            )
        )
        assert result.update["rendered_system_prompt"] == "openai:gpt-6.1-sol"
        assert result.update["run_prepared"] is True
        assert result.update["reply_surface"] == "web"
    assert prepared == [("live-owner", False, instructions)] * 2
    assert bound.await_count == 2
    assert all(call.args[0].sandbox_id == "mda-box" for call in bound.await_args_list)
    assert planned == (1 if mode == "modern" else 3)
