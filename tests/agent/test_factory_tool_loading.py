"""The graph factory's remaining tool loaders must overlap, not run back-to-back.

Each is a cold-cache network round trip (an MCP handshake, credential store
reads) sitting on the critical path before the run's first model call. Corridor
no longer appears here: its catalog is static, so it loads on demand instead.
"""

import asyncio
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from langgraph.graph.state import RunnableConfig

from agent.sandboxes.state import SANDBOX_BACKENDS
from agent.server import get_agent

_START_TIMEOUT_SECONDS = 2.0


class _DummyAgent:
    def with_config(self, config: RunnableConfig) -> "_DummyAgent":
        return self


def _config() -> RunnableConfig:
    return {
        "configurable": {
            "__is_for_execution__": True,
            "thread_id": "thread-parallel-tools",
            "github_login": "octocat",
        },
        "metadata": {},
    }


@pytest.mark.asyncio
async def test_tool_loaders_run_concurrently() -> None:
    barrier = asyncio.Barrier(2)

    def rendezvous(result: Any) -> Any:
        # Serial loaders never all reach the barrier, so a regression times out
        # here instead of quietly costing a few seconds per run.
        async def loader(*_args: Any) -> Any:
            await asyncio.wait_for(barrier.wait(), timeout=_START_TIMEOUT_SECONDS)
            return result

        return loader

    thread_id = "thread-parallel-tools"
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
            "agent.server.get_team_default_model_pair",
            new_callable=AsyncMock,
            return_value=(("openai:gpt-5.6-sol", "medium"), ("openai:gpt-5.6-sol", "low")),
        ),
        patch("agent.server.load_profile", new_callable=AsyncMock, return_value=None),
        patch("agent.server.load_thread_settings", new_callable=AsyncMock, return_value={}),
        patch("agent.server.fallback_model_id_for", return_value=None),
        patch("agent.server.make_model", side_effect=[MagicMock(), MagicMock()]),
        patch("agent.server.construct_system_prompt", return_value="prompt"),
        patch("agent.server.create_deep_agent", return_value=_DummyAgent()),
        patch("agent.server._observability_tools_for", side_effect=rendezvous([])),
        patch("agent.server._load_integration_tools", side_effect=rendezvous(([], []))),
    ):
        await get_agent(_config())

    SANDBOX_BACKENDS.pop(thread_id, None)
