"""What the main agent's planner hands an MDA run: its tools, loop rules, models and subagents."""

import json
from types import SimpleNamespace
from typing import Literal, cast
from unittest.mock import ANY, AsyncMock, MagicMock, patch

import langgraph_sdk
import pytest
from langgraph.graph.state import RunnableConfig
from runtime_api.catalog import McpServerSpec
from runtime_api.connections import ConnectionRef

from openswe.agent_api.connections import ACTING_USER_KEY
from openswe.agent_api.tokens import AGENT_TURN_KEY, AgentTurnSettings
from openswe.dashboard.workspace_settings import WorkspaceSettings
from openswe.mcp.runtime import MCPTools
from openswe.sandboxes.state import SANDBOX_BACKENDS
from openswe.sandboxes.tool_runtime import ModelChoice, SurfaceModels, ToolSurface
from openswe.server import PrepareAgentRunMiddleware, _registered_tool_name, agent_catalog
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
    with patch(
        "openswe.server._mcp_tools_for",
        new_callable=AsyncMock,
        return_value=MCPTools(),
    ) as mcps:
        captured = await _agent_catalog(config)
    assert captured["skills"] == ["/organization-skills/", "/bundled-skills/"]
    tools = captured["tools"]
    assert isinstance(tools, list)
    tool_names = {_registered_tool_name(tool) for tool in tools}
    assert not tool_names.intersection(
        {
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

    from openswe.agent_api.agent import AgentKind
    from openswe.agent_api.run_calls import TokenRun
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
    captured = await _agent_catalog()
    surface = cast(ToolSurface, captured["surface"])
    configurable = dict(_base_config()["configurable"])
    run = RemoteRun(thread_id="thread-ctx", assistant_id="agent", configurable=configurable)

    # Listed from the run's token alone: no plan of the run is made for it.
    listed = RunSkills.model_validate(
        await TokenRun.answer(AgentKind(), run, "skills", RunRequest().model_dump())
    )

    personal = next(f for f in listed.files if f.path == "/skills/personal-workflow/SKILL.md")
    assert "Do it." in personal.content
    # The runtime answers reads of skills itself; the files API serves none of them.
    assert not set(surface.skill_sources) & set(surface.backend_paths)


@pytest.mark.parametrize("profile", [None, {"experimental_monitor": False}])
async def test_monitor_is_available_without_opt_in(profile: dict[str, object] | None) -> None:
    captured = await _agent_catalog(profile=profile)
    tools = captured["tools"]
    assert isinstance(tools, list)
    assert "monitor" in {_registered_tool_name(tool) for tool in tools}


async def test_offloaded_images_are_served_from_a_thread_scoped_store() -> None:
    from deepagents.backends.store import StoreBackend

    from openswe.run_config import RunConfig
    from openswe.server import ThreadFiles
    from openswe.skill_store.run_skills import RunSkillStores

    config = _base_config()
    captured = await _agent_catalog(config)
    files = ThreadFiles(
        "thread-ctx", RunConfig.from_config(config), skills=RunSkillStores(None), bridged=False
    )
    blobs = files.routes["/blobs/"]
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
        captured = await _agent_catalog()
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
        coordinator_thread_id="coordinator",
        title="Existing task",
        workspace_id=workspace.id,
        delegated=True,
    )
    task.workspace = workspace
    context = TaskContext(task, TaskMembership(thread_id="thread-ctx", task_id=task.id, role=role))
    with patch(
        "openswe.middleware.task_coordination.TaskMembership.context_for_thread",
        return_value=context,
    ):
        captured = await _agent_catalog()
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
        await _agent_catalog()
    mcps.assert_not_awaited()


@pytest.mark.parametrize(
    ("background", "attributed", "acting", "personal"),
    [
        (False, "OctoCat", True, True),
        # A background task's completion runs for no person: on a channel's thread as MDA's
        # channel service, which reads no user connections, and elsewhere as the backend's
        # own key, which every such run shares: one person's grant would reach them all.
        (True, None, False, False),
        (False, None, False, False),
        (False, "someone-else", True, False),
        # A wakeup attributed to its owner that they didn't start acts as no one.
        (False, "OctoCat", False, False),
    ],
)
async def test_a_personal_mda_mcp_server_goes_only_to_a_run_that_acts_for_its_owner(
    background: bool, attributed: str | None, acting: bool, personal: bool
) -> None:
    def server(name: str, owner: Literal["agent", "user"]) -> McpServerSpec:
        return McpServerSpec(
            name=name,
            url=f"https://{name}.example/mcp",
            transport="streamable_http",
            tools={"search": f"mcp_{name}_search"},
            connection=ConnectionRef(slug=f"open-swe-mcp-{name}", owner=owner),
        )

    config = _base_config()
    config["configurable"]["background_task_completion"] = background
    if attributed is not None:
        config["metadata"] = {"ls_attribution_identifier": attributed}
    if acting:
        config["metadata"] = {**config["metadata"], ACTING_USER_KEY: "user-1"}
    loaded = MCPTools(runtime_servers=[server("shared", "agent"), server("own", "user")])
    with patch(
        "openswe.server._mcp_tools_for", new_callable=AsyncMock, return_value=loaded
    ) as mcps:
        captured = await _agent_catalog(config)
    # The private thread's owner started it, so the personal tier loads either way.
    mcps.assert_awaited_once_with("octocat", ANY, ANY)
    surface = cast(ToolSurface, captured["surface"])
    expected = ["shared", "own"] if personal else ["shared"]
    assert [server.name for server in surface.mcp_servers] == expected


def _base_config() -> RunnableConfig:
    return {
        "configurable": {
            "__is_for_execution__": True,
            "thread_id": "thread-ctx",
            "github_login": "octocat",
        },
        "metadata": {},
    }


async def _agent_catalog(
    config: RunnableConfig | None = None,
    *,
    profile: dict[str, object] | None = None,
    thread_settings: dict[str, object] | None = None,
    workspace_settings: WorkspaceSettings | None = None,
) -> dict[str, object]:
    """The run ``config`` plans, with the parts tests read pulled out of its surface."""

    surface = ToolSurface()
    config = config or _base_config()
    thread_id = str((config.get("configurable") or {}).get("thread_id"))

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
        patch("openswe.server.construct_system_prompt", return_value="prompt"),
    ):
        await agent_catalog(config, surface)

    SANDBOX_BACKENDS.pop(thread_id, None)
    return {
        "surface": surface,
        "tools": list(surface.base_tools),
        "subagents": list(surface.subagents),
        "skills": list(surface.skill_sources),
        "turn": AgentTurnSettings.model_validate(config["configurable"][AGENT_TURN_KEY]),
    }


def _models(agent: dict[str, object]) -> SurfaceModels:
    models = cast(ToolSurface, agent["surface"]).models
    assert models is not None
    return models


def _main_model(agent: dict[str, object]) -> ModelChoice:
    return _models(agent).main


@pytest.mark.asyncio
async def test_existing_thread_reloads_sender_draft_preference_into_run_config(
    saved_thread_scope,
) -> None:
    saved_thread_scope["owner_login"] = "draft-preference-owner"
    config = _base_config()
    configurable = config.get("configurable")
    assert isinstance(configurable, dict)
    configurable["github_login"] = "draft-preference-owner"

    await _agent_catalog(
        config,
        profile={"draft_prs": False},
        thread_settings={
            "owner_login": "draft-preference-owner",
            "model_id": "openai:gpt-6.1-sol",
        },
    )

    assert configurable["draft_prs"] is False


@pytest.mark.asyncio
async def test_routed_default_route_uses_fast_tier_not_profile_default() -> None:
    config = _base_config()
    config["configurable"].update(thread_id="thread-1", source="dashboard", model_selection="auto")
    profile = {
        "default_model": "anthropic:claude-opus-5-5",
        "reasoning_effort": "high",
        "model_routing_enabled": True,
    }
    agent = await _agent_catalog(config, profile=profile)

    assert _main_model(agent).model_id == "google_genai:gemini-3.8-flash"


@pytest.mark.asyncio
@pytest.mark.parametrize("legacy_thread", [False, True])
async def test_admin_model_changes_only_affect_new_threads(legacy_thread: bool) -> None:
    initial_settings = (
        {"model_id": "openai:gpt-6.1-sol", "effort": "medium", "model_routing_enabled": True}
        if legacy_thread
        else {}
    )
    with patch("openswe.server.store_thread_settings", new_callable=AsyncMock) as store:
        original = await _agent_catalog(
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
    existing = await _agent_catalog(thread_settings=snapshot, workspace_settings=changed_defaults)
    fresh_config = _base_config()
    fresh_config["configurable"]["thread_id"] = "new-thread"
    fresh = await _agent_catalog(fresh_config, workspace_settings=changed_defaults)

    original_models, existing_models, fresh_models = (
        _models(agent) for agent in (original, existing, fresh)
    )
    titles = [cast(AgentTurnSettings, agent["turn"]) for agent in (original, existing, fresh)]
    assert existing_models == original_models
    # The title model is no thread setting: it follows the workspace's default.
    assert titles[1].title_model_id == titles[2].title_model_id != titles[0].title_model_id
    assert fresh_models != original_models
    assert {
        choice.model_id
        for choice in (fresh_models.main, fresh_models.subagent, *fresh_models.routes.values())
    } == {"google_genai:gemini-3.8-flash"}


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
    captured = await _agent_catalog(config)
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

    captured = await _agent_catalog()
    tools = _tool_names(captured["tools"])
    assert read_only_sql.__name__ not in tools
    assert execute_database_sql.__name__ not in tools
    assert manage_feature_flags.__name__ not in tools

    monkeypatch.setenv("CONFIGURED_ADMINS", "octocat")
    config = _base_config()
    configurable = config.get("configurable")
    assert isinstance(configurable, dict)
    configurable["admin_thread"] = True
    captured = await _agent_catalog(config)
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
    captured = await _agent_catalog(config)
    tools = _tool_names(captured["tools"])
    assert read_only_sql.__name__ in tools
    assert execute_database_sql.__name__ in tools
    assert manage_feature_flags.__name__ in tools

    assert all(tool.__name__ in tools for tool in ADMIN_TOOLS)

    configurable["github_login"] = "not-an-admin"
    captured = await _agent_catalog(config)
    tools = _tool_names(captured["tools"])
    assert read_only_sql.__name__ not in tools
    assert execute_database_sql.__name__ not in tools
    assert manage_feature_flags.__name__ not in tools
    assert all(tool.__name__ not in tools for tool in ADMIN_TOOLS)

    configurable["github_login"] = "octocat"
    configurable["source"] = "schedule"
    captured = await _agent_catalog(config)
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

    captured = await _agent_catalog(config)
    tools = captured["tools"]
    assert isinstance(tools, list)

    tool_names = {getattr(tool, "name", None) or getattr(tool, "__name__", None) for tool in tools}
    assert tool_names & SLACK_TOOL_NAMES == (SLACK_TOOL_NAMES if slack_enabled else set())
    reply = cast(ToolSurface, captured["surface"]).loop.reply
    assert reply is not None and reply.surface == "web"


@pytest.mark.asyncio
async def test_an_automation_run_can_post_to_a_channel_without_a_slack_thread() -> None:
    config = _base_config()
    configurable = config.get("configurable")
    assert isinstance(configurable, dict)
    configurable.update({"source": "schedule", "slack_thread": None})

    captured = await _agent_catalog(config)
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
    captured = await _agent_catalog(config)
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
@pytest.mark.parametrize(
    ("thread_scope", "source", "channel_reads", "thread_reads"),
    [
        ("private", "slack", True, True),
        ("public", "slack", False, True),
        ("bot", "slack", True, True),
        ("private", "dashboard", True, True),
        ("public", "dashboard", False, False),
    ],
)
async def test_channel_reads_need_a_private_thread_or_its_triggering_bot(
    thread_scope: str,
    source: str,
    channel_reads: bool,
    thread_reads: bool,
    saved_thread_scope: dict[str, object],
) -> None:
    saved_thread_scope["visibility"] = "private" if thread_scope == "private" else "public"
    config = _base_config()
    configurable = config.get("configurable")
    assert isinstance(configurable, dict)
    configurable["source"] = source
    if source == "slack":
        configurable["slack_thread"] = {"channel_id": "C123", "thread_ts": "1700000000.000100"}
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

    captured = await _agent_catalog(config)
    tools = captured["tools"]
    subagents = captured["subagents"]
    assert isinstance(tools, list)
    assert isinstance(subagents, list)

    tool_names = {_registered_tool_name(tool) for tool in tools}
    assert ("slack_read_channel_messages" in tool_names) is channel_reads
    # A run started from Slack reads threads with the bot's own reach; a web one as channel history.
    assert ("slack_read_thread_messages" in tool_names) is thread_reads

    gp = next(item for item in subagents if item.name == "general-purpose")
    assert ("slack_read_channel_messages" in (gp.tools or ())) is channel_reads
    assert ("slack_read_thread_messages" in (gp.tools or ())) is thread_reads


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
        initial = await _agent_catalog(config)
    snapshot = cast(dict[str, object], store.call_args.args[2])
    assert snapshot["model_handoff_complete"] is True
    assert snapshot["model_routing_enabled"] is False
    followup_config = _base_config()
    followup_config["configurable"].update(source=followup_source, model_selection="auto")
    followup = await _agent_catalog(followup_config, thread_settings=snapshot)
    for agent in (initial, followup):
        main = _main_model(agent)
        assert main.model_id == "anthropic:claude-opus-5-5"
        assert main.kwargs["effort"] == "max"
        prepare = PrepareAgentRunMiddleware(
            _base_config(), cast(AgentTurnSettings, agent["turn"]), repo_instructions=None
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
    await _agent_catalog(config, thread_settings=pinned_settings)
    assert configurable["resolved_agent_model_id"] == "anthropic:claude-opus-5-5"
    configurable.update(
        model_selection="explicit", agent_model_id="openai:gpt-6.1-sol", agent_effort="low"
    )
    await _agent_catalog(config, thread_settings=pinned_settings)
    assert configurable["resolved_agent_model_id"] == "openai:gpt-6.1-sol"


@pytest.mark.parametrize("route", ["fast", "balanced", "performance"])
async def test_text_only_adaptive_route_names_a_vision_model_after_handoff(
    route: Literal["fast", "balanced", "performance"],
) -> None:
    model_id = "fireworks:accounts/fireworks/models/kimi-k3"
    config = _base_config()
    config["configurable"].update(source="dashboard", model_selection="auto")
    planned = await _agent_catalog(
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
    models = _models(planned)

    assert models.routes[route].model_id == model_id
    assert models.vision is not None and models.vision.model_id == "openai:gpt-6.1-sol"


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
    from openswe.server import PrepareAgentRunMiddleware

    monkeypatch.setattr("openswe.server._model_routing_mode", lambda _: "auto")
    config = _base_config()
    config["configurable"].update(
        source="dashboard", model_selection="auto", model_selection_changed=True
    )
    with patch("openswe.server.store_thread_settings", new_callable=AsyncMock) as store:
        captured = await _agent_catalog(config, thread_settings=pinned_settings, profile=profile)
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
    followup_agent = await _agent_catalog(followup, thread_settings=snapshot)
    for agent in (captured, followup_agent):
        routing = cast(ToolSurface, agent["surface"]).loop.routing
        assert routing is not None and routing.mode == "auto"
        assert _main_model(agent).model_id == expected_model
        prepare = PrepareAgentRunMiddleware(
            _base_config(), cast(AgentTurnSettings, agent["turn"]), repo_instructions=None
        )
        assert prepare._requested_models is None


@pytest.mark.parametrize("reply_thread_ts", ["", "123.456"])
async def test_slack_question_allows_auto_routing_after_dashboard_handoff(
    monkeypatch: pytest.MonkeyPatch, reply_thread_ts: str
) -> None:
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
        captured = await _agent_catalog(config, profile={"model_routing_enabled": True})
        routing = cast(ToolSurface, captured["surface"]).loop.routing
        # A Slack question keeps the default route; the dashboard's routes to the fast tier.
        assert routing is not None
        assert routing.mode == ("fast" if source == "dashboard" else None)


async def test_queued_images_reach_vision_fallback_for_text_only_main_model(
    registry_db: None,
) -> None:
    from langchain_core.messages import convert_to_messages

    from openswe.message_queue import QueuedMessage

    config = _base_config()
    planned = await _agent_catalog(
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
    models = _models(planned)
    assert models.vision is not None and models.vision.model_id != models.main.model_id


@pytest.mark.asyncio
async def test_historical_personal_models_and_routing_do_not_override_workspace() -> None:
    captured = await _agent_catalog(
        profile={
            "default_model": "anthropic:claude-opus-5-5",
            "reasoning_effort": "high",
            "default_subagent_model": "anthropic:claude-opus-5-5",
            "subagent_reasoning_effort": "high",
            "model_routing_enabled": True,
        }
    )
    main = _main_model(captured)
    assert main.model_id == "openai:gpt-6.1-sol"
    assert main.kwargs["reasoning"]["effort"] == "medium"


async def test_anthropic_title_model_omits_thinking_without_affecting_agent() -> None:
    captured = await _agent_catalog(
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
    titled: list[tuple[str, dict[str, object]]] = []

    def make_title_model(model_id: str, **kwargs: object) -> MagicMock:
        titled.append((model_id, kwargs))
        return MagicMock()

    # The title model is built where it is used: as the run's turn is prepared.
    turn = cast(AgentTurnSettings, captured["turn"])
    with patch("openswe.server.make_model", side_effect=make_title_model):
        PrepareAgentRunMiddleware(_base_config(), turn, repo_instructions=None)._title_model  # noqa: B018
    [(title_model, title_kwargs)] = titled
    assert title_model == "anthropic:claude-haiku-5-5"
    assert "thinking" not in title_kwargs
    assert title_kwargs["effort"] == "high"
    assert title_kwargs["max_tokens"] == 1024
    main = _main_model(captured)
    assert main.model_id == "anthropic:claude-opus-5-5"
    assert main.kwargs["thinking"] == {"type": "adaptive", "display": "summarized"}
