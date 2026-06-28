"""Assembly contract for the main agent's context-management + middleware wiring.

Locks in that `get_agent` hands a sandbox `backend` to `create_deep_agent` (which
is what makes deepagents auto-wire `FilesystemMiddleware` tool-result eviction and
`SummarizationMiddleware` history offloading), and that the redundant custom
`RepairOrphanedToolCallsMiddleware` is no longer added explicitly — the built-in
`PatchToolCallsMiddleware` that `create_deep_agent` adds covers it.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from langgraph.graph.state import RunnableConfig

from agent.server import get_agent


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
            return_value=(("openai:gpt-5.5", "medium"), ("openai:gpt-5.5", "low")),
        ),
        patch("agent.server.load_profile", new_callable=AsyncMock, return_value=None),
        patch("agent.server.fallback_model_id_for", return_value=None),
        patch("agent.server.make_model", side_effect=[MagicMock(), MagicMock()]),
        patch("agent.server.construct_system_prompt", return_value="prompt"),
        patch("agent.server.create_deep_agent", side_effect=fake_create_deep_agent),
    ):
        await get_agent(_base_config())

    return captured


@pytest.mark.asyncio
async def test_agent_is_built_with_a_backend_for_eviction_and_summarization() -> None:
    captured = await _capture_create_deep_agent_kwargs()
    # The backend is what enables deepagents' auto-wired FilesystemMiddleware
    # eviction + SummarizationMiddleware offloading.
    assert callable(captured["backend"])


@pytest.mark.asyncio
async def test_agent_does_not_add_custom_repair_middleware() -> None:
    captured = await _capture_create_deep_agent_kwargs()
    middleware = captured["middleware"]
    assert isinstance(middleware, list)
    names = {type(m).__name__ for m in middleware}
    # Built-in PatchToolCallsMiddleware (added by create_deep_agent) replaces it.
    assert "RepairOrphanedToolCallsMiddleware" not in names


@pytest.mark.asyncio
async def test_agent_keeps_message_queue_and_step_limit_middleware() -> None:
    captured = await _capture_create_deep_agent_kwargs()
    middleware = captured["middleware"]
    # The dashboard depends on check_message_queue_before_model; the step-limit
    # notifier must still fire when the lowered run budget is hit.
    present = {type(m).__name__ for m in middleware}
    assert "check_message_queue_before_model" in present
    assert "notify_step_limit_reached" in present


@pytest.mark.asyncio
async def test_delivery_agent_provisions_workspace_before_first_model_call() -> None:
    config = _base_config()
    worker_input = {
        "issue_context": {
            "repository": {"owner": "example", "name": "sports-cms"},
            "branch": "delivery/sports-cms/eng-123",
            "base_branch": "main",
        },
        "sandbox_profile": {
            "worktree": {"path": "/workspace/worktrees/delivery-sports-cms-eng-123"}
        },
    }
    config["configurable"].update(
        {"source": "delivery_queue", "delivery_worker_input": worker_input}
    )
    sandbox_backend = MagicMock()
    prompt_kwargs: dict[str, Any] = {}

    def fake_construct_system_prompt(**kwargs: Any) -> str:
        prompt_kwargs.update(kwargs)
        return "prompt"

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
            return_value=sandbox_backend,
        ),
        patch(
            "agent.server.aresolve_sandbox_work_dir",
            new_callable=AsyncMock,
            return_value="/workspace",
        ),
        patch(
            "agent.server.get_team_default_model_pair",
            new_callable=AsyncMock,
            return_value=(("openai:gpt-5.5", "medium"), ("openai:gpt-5.5", "low")),
        ),
        patch("agent.server.load_profile", new_callable=AsyncMock, return_value=None),
        patch("agent.server.fallback_model_id_for", return_value=None),
        patch("agent.server.make_model", side_effect=[MagicMock(), MagicMock()]),
        patch("agent.server.construct_system_prompt", side_effect=fake_construct_system_prompt),
        patch("agent.server.create_deep_agent", return_value=_DummyAgent()),
        patch(
            "agent.server.provision_delivery_workspace",
            new_callable=AsyncMock,
            return_value={
                "status": "ready",
                "path": "/workspace/worktrees/delivery-sports-cms-eng-123",
            },
        ) as provision,
    ):
        await get_agent(config)

    provision.assert_awaited_once_with(
        sandbox_backend,
        worker_input=worker_input,
        default_work_dir="/workspace",
    )
    assert prompt_kwargs["working_dir"] == "/workspace/worktrees/delivery-sports-cms-eng-123"


@pytest.mark.asyncio
async def test_delivery_agent_stops_when_workspace_provisioning_fails() -> None:
    config = _base_config()
    worker_input = {
        "issue_context": {
            "repository": {"owner": "example", "name": "sports-cms"},
            "branch": "delivery/sports-cms/eng-123",
            "base_branch": "main",
        },
        "sandbox_profile": {
            "worktree": {"path": "/workspace/worktrees/delivery-sports-cms-eng-123"}
        },
    }
    config["configurable"].update(
        {"source": "delivery_queue", "delivery_worker_input": worker_input}
    )

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
            return_value=(("openai:gpt-5.5", "medium"), ("openai:gpt-5.5", "low")),
        ),
        patch("agent.server.load_profile", new_callable=AsyncMock, return_value=None),
        patch(
            "agent.server.provision_delivery_workspace",
            new_callable=AsyncMock,
            return_value={"status": "failed", "reason": "checkout_failed"},
        ),
        patch("agent.server.create_deep_agent", return_value=_DummyAgent()) as create_agent,
    ):
        with pytest.raises(RuntimeError, match="checkout_failed"):
            await get_agent(config)

    create_agent.assert_not_called()
