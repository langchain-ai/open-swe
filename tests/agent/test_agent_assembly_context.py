"""Assembly contract for the main agent's context-management + middleware wiring.

Locks in that `get_agent` hands a sandbox `backend` to `create_deep_agent` (which
is what makes deepagents auto-wire `FilesystemMiddleware` tool-result eviction and
`SummarizationMiddleware` history offloading), and that the redundant custom
`RepairOrphanedToolCallsMiddleware` is no longer added explicitly — the built-in
`PatchToolCallsMiddleware` that `create_deep_agent` adds covers it.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from deepagents.backends.composite import CompositeBackend
from langgraph.graph.state import RunnableConfig

from agent.server import get_agent
from agent.utils.read_only_backend import ReadOnlyBackend
from agent.utils.sandbox_state import SandboxBackendProxy, clear_sandbox_backend


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


async def _capture_create_deep_agent_kwargs() -> dict[str, object]:
    captured: dict[str, object] = {}

    def fake_create_deep_agent(**kwargs: object) -> _DummyAgent:
        captured.update(kwargs)
        return _DummyAgent()

    clear_sandbox_backend("thread-ctx")
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
        patch("agent.server.load_profile", new_callable=AsyncMock, return_value=None),
        patch("agent.server.fallback_model_id_for", return_value=None),
        patch("agent.server.make_model", side_effect=[MagicMock(), MagicMock()]),
        patch("agent.server.construct_system_prompt", return_value="prompt"),
        patch("agent.server.create_deep_agent", side_effect=fake_create_deep_agent),
    ):
        await get_agent(_base_config())

    clear_sandbox_backend("thread-ctx")
    return captured


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
async def test_agent_wires_user_skills_into_main_and_general_purpose_agents() -> None:
    captured = await _capture_create_deep_agent_kwargs()
    assert captured["skills"] == ["/skills/"]
    backend = captured["backend"]
    assert isinstance(backend, CompositeBackend)
    assert isinstance(backend.routes["/skills/"], ReadOnlyBackend)
    with pytest.raises(NotImplementedError):
        backend.write("/skills/poison/SKILL.md", "malicious")
    subagents = captured["subagents"]
    assert isinstance(subagents, list)
    gp = next(s for s in subagents if s["name"] == "general-purpose")
    assert gp["skills"] == ["/skills/"]


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
async def test_agent_includes_recreate_sandbox_tool() -> None:
    from agent.tools import recreate_sandbox

    captured = await _capture_create_deep_agent_kwargs()
    tools = captured["tools"]
    assert isinstance(tools, list)
    assert recreate_sandbox in tools


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
