"""Assembly contract for the main agent's context-management + middleware wiring.

Locks in that `get_agent` hands a sandbox `backend` to `create_deep_agent` (which
is what makes deepagents auto-wire `FilesystemMiddleware` tool-result eviction and
`SummarizationMiddleware` history offloading), and that the redundant custom
`RepairOrphanedToolCallsMiddleware` is no longer added explicitly — the built-in
`PatchToolCallsMiddleware` that `create_deep_agent` adds covers it.
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from deepagents.backends.composite import CompositeBackend
from deepagents.backends.state import StateBackend
from langgraph.graph.state import RunnableConfig

from agent.server import _registered_tool_name, get_agent
from agent.utils.read_only_backend import ReadOnlyBackend
from agent.utils.sandbox_state import SandboxBackendProxy, clear_sandbox_backend


class _DummyAgent:
    def with_config(self, config: RunnableConfig) -> "_DummyAgent":
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
) -> dict[str, object]:
    captured: dict[str, object] = {}
    config = config or _base_config()
    thread_id = "thread-ctx"

    def fake_create_deep_agent(**kwargs: object) -> _DummyAgent:
        captured.update(kwargs)
        return _DummyAgent()

    clear_sandbox_backend(thread_id)
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
            "agent.server.aresolve_sandbox_work_dir",
            new_callable=AsyncMock,
            return_value="/workspace",
        ),
        patch(
            "agent.server.get_team_default_model_pair",
            new_callable=AsyncMock,
            return_value=(("openai:gpt-5.6-sol", "medium"), ("openai:gpt-5.6-sol", "low")),
        ),
        patch("agent.server.load_profile", new_callable=AsyncMock, return_value=profile),
        patch(
            "agent.server.load_thread_settings",
            new_callable=AsyncMock,
            return_value=thread_settings or {},
        ),
        patch("agent.server.fallback_model_id_for", return_value=None),
        patch("agent.server.make_model", side_effect=[MagicMock(), MagicMock()]),
        patch("agent.server.construct_system_prompt", return_value="prompt"),
        patch("agent.server.create_deep_agent", side_effect=fake_create_deep_agent),
    ):
        await get_agent(config)

    clear_sandbox_backend(thread_id)
    return captured


@pytest.mark.asyncio
async def test_existing_thread_reloads_sender_draft_preference_into_run_config() -> None:
    config = _base_config()
    configurable = config.get("configurable")
    assert isinstance(configurable, dict)
    configurable["github_login"] = "draft-preference-owner"

    await _capture_create_deep_agent_kwargs(
        config,
        profile={"draft_prs": False},
        thread_settings={
            "owner_login": "draft-preference-owner",
            "model_id": "openai:gpt-5.6-sol",
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

    async def load_defaults(*args: object) -> tuple[tuple[str, str], tuple[str, str]]:
        del args
        await started.wait()
        return (("openai:gpt-5.6-sol", "medium"), ("openai:gpt-5.6-sol", "low"))

    clear_sandbox_backend("thread-ctx")
    with (
        patch("agent.server.ensure_sandbox_for_thread", side_effect=ensure_sandbox),
        patch("agent.server._cached_team_default_model_pair", side_effect=load_defaults),
        patch("agent.server._cached_gateway_enabled", new_callable=AsyncMock, return_value=False),
        patch("agent.server._cached_profile", new_callable=AsyncMock, return_value=None),
        patch("agent.server._cached_fable_enabled", new_callable=AsyncMock, return_value=True),
        patch("agent.server._observability_authorized", new_callable=AsyncMock, return_value=False),
        patch("agent.server._allowed_org_member", new_callable=AsyncMock, return_value=False),
        patch("agent.server._load_corridor_mcp_tools", new_callable=AsyncMock, return_value=[]),
        patch("agent.server.load_browser_tools", return_value=[]),
        patch("agent.server.make_model", return_value=MagicMock()),
        patch("agent.server.fallback_model_id_for", return_value=None),
        patch("agent.server.create_deep_agent", return_value=_DummyAgent()),
    ):
        agent_task = asyncio.create_task(get_agent(_base_config()))
        await asyncio.wait_for(started.wait(), timeout=1)
        assert not agent_task.done()
        release.set()
        await agent_task

    clear_sandbox_backend("thread-ctx")


@pytest.mark.asyncio
async def test_agent_is_built_with_a_backend_for_eviction_and_summarization() -> None:
    captured = await _capture_create_deep_agent_kwargs()
    # The backend is what enables deepagents' auto-wired FilesystemMiddleware
    # eviction + SummarizationMiddleware offloading. deepagents 0.7 requires an
    # initialized backend instance, not a factory callable.
    backend = captured["backend"]
    assert isinstance(backend, CompositeBackend)
    assert isinstance(backend.default, SandboxBackendProxy)
    assert not callable(backend.default)


@pytest.mark.asyncio
async def test_agent_wires_user_organization_and_bundled_skills_into_agents() -> None:
    captured = await _capture_create_deep_agent_kwargs()
    sources = ["/skills/", "/organization-skills/", "/bundled-skills/"]
    assert captured["skills"] == sources
    backend = captured["backend"]
    assert isinstance(backend, CompositeBackend)
    for route in sources:
        assert isinstance(backend.routes[route], ReadOnlyBackend)
        with pytest.raises(NotImplementedError):
            backend.write(f"{route}poison/SKILL.md", "malicious")
    skill = await backend.aread("/bundled-skills/baby-sit/SKILL.md")
    assert skill.file_data and "name: baby-sit" in skill.file_data["content"]
    subagents = captured["subagents"]
    assert isinstance(subagents, list)
    gp = next(s for s in subagents if s["name"] == "general-purpose")
    assert gp["skills"] == sources


@pytest.mark.asyncio
async def test_agent_adds_repository_skill_sources() -> None:
    config = _base_config()
    config["configurable"]["repo"] = {"owner": "acme", "name": "widget"}
    with patch(
        "agent.server._resolve_repo_skill_sources",
        new_callable=AsyncMock,
        return_value=["/workspace/widget/.agents/skills/"],
    ):
        captured = await _capture_create_deep_agent_kwargs(config)

    assert captured["skills"] == [
        "/skills/",
        "/organization-skills/",
        "/bundled-skills/",
        "/workspace/widget/.agents/skills/",
    ]


@pytest.mark.asyncio
async def test_desktop_agent_loads_snapshotted_and_bundled_skills() -> None:
    config = _base_config()
    config.setdefault("configurable", {}).update(
        {"source": "desktop", "local_project_path": "/tmp"}
    )
    with patch("agent.server.create_desktop_backend", return_value=MagicMock()):
        captured = await _capture_create_deep_agent_kwargs(config)

    assert captured["skills"] == ["/skills/", "/bundled-skills/"]
    backend = captured["backend"]
    assert isinstance(backend, CompositeBackend)
    assert isinstance(backend.routes["/skills/"], ReadOnlyBackend)
    assert isinstance(backend.routes["/skills/"]._backend, StateBackend)


@pytest.mark.asyncio
async def test_agent_does_not_add_custom_repair_middleware() -> None:
    captured = await _capture_create_deep_agent_kwargs()
    middleware = captured["middleware"]
    assert isinstance(middleware, list)
    names = {type(m).__name__ for m in middleware}
    # Built-in PatchToolCallsMiddleware (added by create_deep_agent) replaces it.
    assert "RepairOrphanedToolCallsMiddleware" not in names
    assert "SanitizeOpenAIResponsesMiddleware" in names


@pytest.mark.asyncio
async def test_agent_keeps_message_queue_and_step_limit_middleware() -> None:
    captured = await _capture_create_deep_agent_kwargs()
    middleware = captured["middleware"]
    assert isinstance(middleware, list)
    # The dashboard depends on check_message_queue_before_model; the step-limit
    # notifier must still fire when the lowered run budget is hit.
    present = {type(m).__name__ for m in middleware}
    assert "check_message_queue_before_model" in present
    assert "notify_step_limit_reached" in present


@pytest.mark.asyncio
async def test_agent_includes_report_platform_issue_tool() -> None:
    from agent.tools import report_platform_issue

    captured = await _capture_create_deep_agent_kwargs()
    tools = captured["tools"]
    assert isinstance(tools, list)
    assert report_platform_issue in tools


@pytest.mark.asyncio
async def test_agent_includes_read_user_settings_only_on_parent() -> None:
    from agent.tools import read_user_settings

    captured = await _capture_create_deep_agent_kwargs()
    tools = captured["tools"]
    subagents = captured["subagents"]
    assert isinstance(tools, list)
    assert isinstance(subagents, list)
    assert read_user_settings in tools
    general_purpose = next(item for item in subagents if item["name"] == "general-purpose")
    assert read_user_settings not in general_purpose["tools"]


@pytest.mark.asyncio
async def test_agent_includes_thread_tools_only_on_parent() -> None:
    from agent.tools import get_thread, list_threads, manage_thread

    captured = await _capture_create_deep_agent_kwargs()
    tools = captured["tools"]
    subagents = captured["subagents"]
    assert isinstance(tools, list)
    assert isinstance(subagents, list)
    thread_tools = (get_thread, list_threads, manage_thread)
    assert all(tool in tools for tool in thread_tools)
    general_purpose = next(item for item in subagents if item["name"] == "general-purpose")
    assert all(tool not in general_purpose["tools"] for tool in thread_tools)


@pytest.mark.asyncio
async def test_agent_includes_recreate_sandbox_tool() -> None:
    from agent.tools import recreate_sandbox

    captured = await _capture_create_deep_agent_kwargs()
    tools = captured["tools"]
    assert isinstance(tools, list)
    assert recreate_sandbox in tools


@pytest.mark.asyncio
async def test_agent_includes_sandbox_reset_only_in_admin_threads(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agent.tools import sandbox_reset

    captured = await _capture_create_deep_agent_kwargs()
    tools = captured["tools"]
    assert isinstance(tools, list)
    assert sandbox_reset not in tools

    monkeypatch.setenv("CONFIGURED_ADMINS", "octocat")
    config = _base_config()
    configurable = config.get("configurable")
    assert isinstance(configurable, dict)
    configurable["admin_thread"] = True
    captured = await _capture_create_deep_agent_kwargs(config)
    tools = captured["tools"]
    assert isinstance(tools, list)
    assert sandbox_reset in tools


@pytest.mark.asyncio
async def test_agent_includes_sandbox_file_download_url_tools() -> None:
    from agent.tools import create_sandbox_file_download_url, output_iframe

    captured = await _capture_create_deep_agent_kwargs()
    tools = captured["tools"]
    assert isinstance(tools, list)
    assert create_sandbox_file_download_url in tools
    assert output_iframe in tools


@pytest.mark.asyncio
async def test_agent_excludes_sandbox_file_downloads_for_other_providers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from deepagents.middleware.subagents import GENERAL_PURPOSE_SUBAGENT

    from agent.prompt import OPEN_SWE_SHARED_BASE
    from agent.tools import create_sandbox_file_download_url, output_iframe

    monkeypatch.setenv("SANDBOX_TYPE", "modal")
    captured = await _capture_create_deep_agent_kwargs()
    tools = captured["tools"]
    subagents = captured["subagents"]
    assert isinstance(tools, list)
    assert isinstance(subagents, list)
    assert create_sandbox_file_download_url not in tools
    assert output_iframe not in tools
    general_purpose = next(item for item in subagents if item["name"] == "general-purpose")
    assert create_sandbox_file_download_url not in general_purpose["tools"]
    assert output_iframe not in general_purpose["tools"]
    assert general_purpose["system_prompt"] == (
        f"{OPEN_SWE_SHARED_BASE}\n\n{GENERAL_PURPOSE_SUBAGENT['system_prompt']}"
    )


@pytest.mark.asyncio
async def test_dashboard_agent_excludes_slack_tools() -> None:
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
    assert tool_names.isdisjoint(
        {
            "slack_add_reaction",
            "slack_attach_html",
            "slack_move_thread",
            "slack_read_thread_messages",
            "slack_start_new_thread",
            "slack_thread_reply",
        }
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("source", ["slack", "schedule"])
async def test_slack_source_context_includes_slack_tools(source: str) -> None:
    config = _base_config()
    configurable = config.get("configurable")
    assert isinstance(configurable, dict)
    configurable.update(
        {
            "source": source,
            "slack_thread": {"channel_id": "C123", "thread_ts": "1700000000.000100"},
        }
    )

    captured = await _capture_create_deep_agent_kwargs(config)
    tools = captured["tools"]
    assert isinstance(tools, list)

    tool_names = {getattr(tool, "name", None) or getattr(tool, "__name__", None) for tool in tools}
    assert {
        "slack_add_reaction",
        "slack_attach_html",
        "slack_move_thread",
        "slack_read_thread_messages",
        "slack_start_new_thread",
        "slack_thread_reply",
    } <= tool_names


@pytest.mark.asyncio
async def test_agent_excludes_deepagents_grep_tool() -> None:
    captured = await _capture_create_deep_agent_kwargs()
    middleware = captured["middleware"]
    subagents = captured["subagents"]
    assert isinstance(middleware, list)
    assert isinstance(subagents, list)

    exclusion = next(item for item in middleware if type(item).__name__ == "ExcludeToolsMiddleware")
    assert exclusion._excluded == frozenset({"grep"})
    general_purpose = next(item for item in subagents if item["name"] == "general-purpose")
    subagent_exclusion = next(
        item
        for item in general_purpose["middleware"]
        if type(item).__name__ == "ExcludeToolsMiddleware"
    )
    assert subagent_exclusion._excluded == frozenset({"grep"})


@pytest.mark.asyncio
async def test_stop_summary_agent_is_read_only_and_slack_only() -> None:
    config = _base_config()
    configurable = config.get("configurable")
    assert isinstance(configurable, dict)
    configurable.update(
        {
            "source": "slack",
            "slack_thread": {"channel_id": "C123", "thread_ts": "1700000000.000100"},
            "stop_summary": True,
        }
    )

    captured = await _capture_create_deep_agent_kwargs(config)
    tools = captured["tools"]
    middleware = captured["middleware"]
    assert isinstance(tools, list)
    assert isinstance(middleware, list)

    tool_names = {getattr(tool, "name", None) or getattr(tool, "__name__", None) for tool in tools}
    assert tool_names == {"slack_read_thread_messages", "slack_thread_reply"}
    middleware_names = {type(item).__name__ for item in middleware}
    assert "ExcludeToolsMiddleware" in middleware_names
    assert "check_message_queue_before_model" not in middleware_names


@pytest.mark.asyncio
async def test_task_retry_wraps_inside_tool_error_middleware() -> None:
    captured = await _capture_create_deep_agent_kwargs()
    middleware = captured["middleware"]
    assert isinstance(middleware, list)
    names = [type(m).__name__ for m in middleware]

    assert names.index("ToolErrorMiddleware") < names.index("ToolRetryMiddleware")


@pytest.mark.asyncio
async def test_general_purpose_subagent_carries_open_swe_shared_base() -> None:
    from agent.prompt import OPEN_SWE_SHARED_BASE

    captured = await _capture_create_deep_agent_kwargs()
    subagents = captured["subagents"]
    assert isinstance(subagents, list)
    gp = next(s for s in subagents if s["name"] == "general-purpose")
    prompt = gp["system_prompt"]
    assert prompt.startswith(OPEN_SWE_SHARED_BASE)
    # GP task-mechanics guidance still trails the shared base.
    assert "calling agent only sees your final" in prompt


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
        "notify_automation_channel",
        "slack_add_reaction",
        "slack_attach_html",
        "slack_move_thread",
        "slack_read_thread_messages",
        "slack_start_new_thread",
        "slack_thread_reply",
    }

    parent_only_names = {
        *slack_names,
        "background_execute",
        "background_task",
        "get_thread",
        "list_threads",
        "manage_thread",
        "read_user_settings",
    }
    assert parent_only_names <= parent_names
    assert parent_only_names.isdisjoint(subagent_names)
    assert subagent_names == parent_names - parent_only_names
