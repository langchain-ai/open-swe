"""Assembly contract for the main agent's context-management + middleware wiring.

Locks in that `get_agent` hands a sandbox `backend` to `create_deep_agent` (which
is what makes deepagents auto-wire `FilesystemMiddleware` tool-result eviction and
`SummarizationMiddleware` history offloading), and that the redundant custom
`RepairOrphanedToolCallsMiddleware` is no longer added explicitly — the built-in
`PatchToolCallsMiddleware` that `create_deep_agent` adds covers it.
"""

import asyncio
from types import SimpleNamespace
from typing import cast
from unittest.mock import AsyncMock, MagicMock, patch

import langgraph_sdk
import pytest
from deepagents.backends.composite import CompositeBackend
from deepagents.backends.state import StateBackend
from langgraph.graph.state import RunnableConfig

from agent.dashboard.workspace_settings import WorkspaceSettings
from agent.run_config import RunConfig
from agent.sandboxes.read_only_backend import ReadOnlyBackend
from agent.sandboxes.state import SANDBOX_BACKENDS, SandboxBackendProxy
from agent.server import DesktopAgentState, _registered_tool_name, get_agent, workspace_slug

_MODEL_DEFAULTS = {
    "default_agent_model": "openai:gpt-5.6-sol",
    "default_agent_reasoning_effort": "medium",
    "default_agent_subagent_model": "openai:gpt-5.6-sol",
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
    with patch("agent.server._notion_tools_for", new_callable=AsyncMock, return_value=[]) as notion:
        captured = await _capture_create_deep_agent_kwargs()
    assert captured["skills"] == ["/organization-skills/", "/bundled-skills/"]
    assert "/skills/" not in captured["backend"].routes
    tools = captured["tools"]
    assert isinstance(tools, list)
    tool_names = {_registered_tool_name(tool) for tool in tools}
    assert not tool_names.intersection(
        {"save_user_instructions", "save_user_skill", "delete_user_skill", "read_user_settings"}
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
    private_thread: bool = False,
) -> dict[str, object]:
    captured: dict[str, object] = {}
    make_model_calls: list[tuple[str, dict[str, object]]] = []
    config = config or _base_config()
    thread_id = str((config.get("configurable") or {}).get("thread_id"))

    def fake_create_deep_agent(**kwargs: object) -> _DummyAgent:
        captured.update(kwargs)
        return _DummyAgent()

    def fake_make_model(model_id: str, **kwargs: object) -> MagicMock:
        make_model_calls.append((model_id, kwargs))
        return MagicMock()

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
            return_value=WorkspaceSettings(
                {
                    **_MODEL_DEFAULTS,
                    "default_agent_routing_fast_model": "google_genai:gemini-3.8-flash",
                    "default_agent_routing_fast_reasoning_effort": "low",
                    "default_agent_routing_balanced_model": "openai:gpt-5.6-sol",
                    "default_agent_routing_balanced_reasoning_effort": "medium",
                    "default_agent_routing_performance_model": "anthropic:claude-opus-5",
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

    async def load_defaults(*args: object) -> WorkspaceSettings:
        del args
        await started.wait()
        return WorkspaceSettings(
            {
                **_MODEL_DEFAULTS,
                "default_agent_routing_fast_model": "openai:gpt-5.6-sol",
                "default_agent_routing_fast_reasoning_effort": "low",
                "default_agent_routing_balanced_model": "openai:gpt-5.6-sol",
                "default_agent_routing_balanced_reasoning_effort": "medium",
                "default_agent_routing_performance_model": "openai:gpt-5.6-sol",
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
@pytest.mark.parametrize(
    ("configurable_update", "profile", "thread_settings", "expected"),
    [
        ({}, None, None, "openai:gpt-5.6-sol"),
        (
            {"agent_model_id": "anthropic:claude-opus-5", "agent_effort": "high"},
            None,
            None,
            "anthropic:claude-opus-5",
        ),
        (
            {},
            {"default_model": "google_genai:gemini-3.8-flash", "reasoning_effort": "low"},
            None,
            "google_genai:gemini-3.8-flash",
        ),
        (
            {},
            None,
            {"model_id": "anthropic:claude-opus-5", "effort": "high"},
            "anthropic:claude-opus-5",
        ),
    ],
)
async def test_resolved_configured_model_is_available_to_tools(
    configurable_update: dict[str, object],
    profile: dict[str, object] | None,
    thread_settings: dict[str, object] | None,
    expected: str,
) -> None:
    config = _base_config()
    config["configurable"].update(configurable_update)
    await _capture_create_deep_agent_kwargs(
        config, profile=profile, thread_settings=thread_settings
    )

    assert config["configurable"]["resolved_agent_model_id"] == expected


@pytest.mark.asyncio
async def test_model_routing_is_applied_when_enabled() -> None:
    config = _base_config()
    config["configurable"]["thread_id"] = "thread-1"
    agent = await _capture_create_deep_agent_kwargs(config, profile={"model_routing_enabled": True})

    assert config["configurable"]["resolved_agent_model_id"] == "openai:gpt-5.6-sol"
    middleware_names = [
        type(middleware).__name__ for middleware in cast(list[object], agent["middleware"])
    ]
    assert "ModelSelectionMiddleware" in middleware_names
    assert "model_routing_mode" not in config["configurable"]
    assert config["metadata"]["model_routing_mode"] == "auto"
    assert config["metadata"]["model_routing_applied"] is True
    calls = cast(list[tuple[str, dict[str, object]]], agent["make_model_calls"])
    assert [model for model, _ in calls[1:4]] == [
        "google_genai:gemini-3.8-flash",
        "openai:gpt-5.6-sol",
        "anthropic:claude-opus-5",
    ]


@pytest.mark.asyncio
async def test_model_routing_control_uses_performance_model() -> None:
    config = _base_config()
    agent = await _capture_create_deep_agent_kwargs(config, profile={"model_routing_enabled": True})

    middleware_names = [
        type(middleware).__name__ for middleware in cast(list[object], agent["middleware"])
    ]
    assert "ModelSelectionMiddleware" in middleware_names
    assert "model_routing_mode" not in config["configurable"]
    assert config["metadata"]["model_routing_mode"] == "performance"
    assert config["metadata"]["model_routing_applied"] is True
    calls = cast(list[tuple[str, dict[str, object]]], agent["make_model_calls"])
    assert [model for model, _ in calls[1:4]] == [
        "google_genai:gemini-3.8-flash",
        "openai:gpt-5.6-sol",
        "anthropic:claude-opus-5",
    ]


@pytest.mark.asyncio
async def test_model_routing_is_disabled_by_default() -> None:
    config = _base_config()
    agent = await _capture_create_deep_agent_kwargs(config)

    assert config["configurable"]["resolved_agent_model_id"] == "openai:gpt-5.6-sol"
    middleware_names = [
        type(middleware).__name__ for middleware in cast(list[object], agent["middleware"])
    ]
    assert "ModelSelectionMiddleware" not in middleware_names
    assert config["metadata"]["model_routing_applied"] is False
    assert "model_routing_mode" not in config["metadata"]
    calls = cast(list[tuple[str, dict[str, object]]], agent["make_model_calls"])
    assert [model for model, _ in calls] == [
        "openai:gpt-5.6-sol",
        "openai:gpt-5.6-sol",
        "openai:gpt-5.6-luna",
    ]


@pytest.mark.asyncio
async def test_model_routing_preference_is_snapshotted_for_existing_thread() -> None:
    config = _base_config()
    agent = await _capture_create_deep_agent_kwargs(
        config,
        profile={"model_routing_enabled": True},
        thread_settings={
            "model_id": "openai:gpt-5.6-sol",
            "effort": "medium",
            "subagent_model_id": "openai:gpt-5.6-sol",
            "subagent_effort": "low",
            "model_routing_enabled": False,
        },
    )

    middleware_names = [
        type(middleware).__name__ for middleware in cast(list[object], agent["middleware"])
    ]
    assert "ModelSelectionMiddleware" not in middleware_names
    assert config["metadata"]["model_routing_applied"] is False
    assert "model_routing_mode" not in config["metadata"]


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
    assert captured["state_schema"] is None


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
    artifacts = await backend.aread("/bundled-skills/html-artifacts/SKILL.md")
    assert artifacts.file_data and "name: html-artifacts" in artifacts.file_data["content"]
    environments = await backend.aread("/bundled-skills/workspaces/SKILL.md")
    assert environments.file_data and "name: workspaces" in environments.file_data["content"]
    subagents = captured["subagents"]
    assert isinstance(subagents, list)
    gp = next(s for s in subagents if s["name"] == "general-purpose")
    assert gp["skills"] == sources


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
    assert captured["state_schema"] is DesktopAgentState
    assert "files" in DesktopAgentState.__annotations__


@pytest.mark.asyncio
async def test_desktop_agent_honors_gateway_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LANGSMITH_GATEWAY_ENABLED", "true")
    config = _base_config()
    config.setdefault("configurable", {}).update(
        {"source": "desktop", "local_project_path": "/tmp"}
    )
    with patch("agent.server.create_desktop_backend", return_value=MagicMock()):
        captured = await _capture_create_deep_agent_kwargs(config)

    calls = captured["make_model_calls"]
    assert isinstance(calls, list)
    assert calls
    assert all(kwargs["use_gateway"] is True for _, kwargs in calls)


@pytest.mark.asyncio
async def test_agent_defaults_missing_run_source_before_prepare() -> None:
    config = _base_config()

    await _capture_create_deep_agent_kwargs(config)

    configurable = config.get("configurable")
    assert isinstance(configurable, dict)
    assert configurable["source"] == "dashboard"


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
async def test_agent_includes_sql_only_on_private_admin_surfaces(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agent.server import ADMIN_TOOLS
    from agent.tools import read_only_sql

    captured = await _capture_create_deep_agent_kwargs()
    tools = captured["tools"]
    assert isinstance(tools, list)
    assert read_only_sql not in tools

    monkeypatch.setenv("CONFIGURED_ADMINS", "octocat")
    config = _base_config()
    configurable = config.get("configurable")
    assert isinstance(configurable, dict)
    configurable["admin_thread"] = True
    captured = await _capture_create_deep_agent_kwargs(config)
    tools = captured["tools"]
    assert isinstance(tools, list)
    assert read_only_sql in tools
    subagents = captured["subagents"]
    assert isinstance(subagents, list)
    general_purpose = next(item for item in subagents if item["name"] == "general-purpose")
    assert read_only_sql not in general_purpose["tools"]

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

    assert all(tool in tools for tool in ADMIN_TOOLS)

    configurable["github_login"] = "not-an-admin"
    captured = await _capture_create_deep_agent_kwargs(config)
    tools = captured["tools"]
    assert isinstance(tools, list)
    assert read_only_sql not in tools
    assert all(tool not in tools for tool in ADMIN_TOOLS)

    configurable["github_login"] = "octocat"
    configurable["source"] = "schedule"
    captured = await _capture_create_deep_agent_kwargs(config)
    tools = captured["tools"]
    assert isinstance(tools, list)
    assert read_only_sql not in tools


@pytest.mark.asyncio
async def test_agent_includes_sandbox_file_download_url_tools() -> None:
    from agent.tools import (
        create_sandbox_file_download_url,
        expose_port,
        output_iframe,
    )

    captured = await _capture_create_deep_agent_kwargs()
    tools = captured["tools"]
    assert isinstance(tools, list)
    assert create_sandbox_file_download_url in tools
    assert expose_port in tools
    assert output_iframe in tools


@pytest.mark.asyncio
async def test_agent_excludes_sandbox_file_downloads_for_other_providers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from deepagents.middleware.subagents import GENERAL_PURPOSE_SUBAGENT

    from agent.prompt import OPEN_SWE_SHARED_BASE
    from agent.tools import (
        create_sandbox_file_download_url,
        expose_port,
        output_iframe,
    )

    monkeypatch.setenv("SANDBOX_TYPE", "modal")
    captured = await _capture_create_deep_agent_kwargs()
    tools = captured["tools"]
    subagents = captured["subagents"]
    assert isinstance(tools, list)
    assert isinstance(subagents, list)
    assert create_sandbox_file_download_url not in tools
    assert expose_port not in tools
    assert output_iframe not in tools
    general_purpose = next(item for item in subagents if item["name"] == "general-purpose")
    assert create_sandbox_file_download_url not in general_purpose["tools"]
    assert expose_port not in general_purpose["tools"]
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
async def test_general_purpose_subagent_gets_shell_guards() -> None:
    captured = await _capture_create_deep_agent_kwargs()
    subagents = captured["subagents"]
    assert isinstance(subagents, list)
    gp = next(s for s in subagents if s["name"] == "general-purpose")
    names = [type(m).__name__ for m in gp["middleware"]]
    assert "WorkflowPushGuardMiddleware" in names
    assert "PullRequestCreationGuardMiddleware" in names


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
        "submit_thread_feedback",
    }
    assert parent_only_names <= parent_names
    assert parent_only_names.isdisjoint(subagent_names)
    assert subagent_names == parent_names - parent_only_names


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


def test_workspace_slug_reads_workspace_then_environment() -> None:
    assert workspace_slug(RunConfig(workspace="oss")) == "oss"
    assert workspace_slug(RunConfig(environment="legacy")) == "legacy"
    assert workspace_slug(RunConfig()) is None
