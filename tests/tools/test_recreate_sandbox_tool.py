from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from agent.tools.recreate_sandbox import recreate_sandbox


@pytest.mark.asyncio
async def test_recreate_sandbox_returns_old_and_new_ids() -> None:
    config = {
        "configurable": {
            "thread_id": "thread-1",
            "repo": {"owner": "langchain-ai", "name": "open-swe"},
        }
    }
    repo = {"owner": "langchain-ai", "name": "open-swe"}

    with (
        patch("agent.tools.recreate_sandbox.get_config", return_value=config),
        patch(
            "agent.server._resolve_prompt_default_repo",
            new_callable=AsyncMock,
            return_value=repo,
        ) as resolve_repo,
        patch(
            "agent.server.recreate_sandbox_for_thread",
            new_callable=AsyncMock,
            return_value=("sandbox-old", "sandbox-new"),
        ) as recreate,
    ):
        result = await recreate_sandbox()

    assert result == {
        "success": True,
        "old_sandbox_id": "sandbox-old",
        "new_sandbox_id": "sandbox-new",
    }
    resolve_repo.assert_awaited_once_with(config["configurable"])
    recreate.assert_awaited_once_with("thread-1", repo=repo)


@pytest.mark.asyncio
async def test_recreate_sandbox_reports_failure_without_ids() -> None:
    config = {"configurable": {"thread_id": "thread-1"}}

    with (
        patch("agent.tools.recreate_sandbox.get_config", return_value=config),
        patch(
            "agent.server._resolve_prompt_default_repo",
            new_callable=AsyncMock,
            return_value=None,
        ),
        patch(
            "agent.server.recreate_sandbox_for_thread",
            new_callable=AsyncMock,
            side_effect=RuntimeError("creation failed"),
        ),
    ):
        result = await recreate_sandbox()

    assert result == {"success": False, "error": "creation failed"}


def test_recreate_sandbox_description_explains_handoff() -> None:
    assert recreate_sandbox.__doc__ is not None
    assert "fresh sandbox has none of the thread's current files" in recreate_sandbox.__doc__
    assert "old sandbox is not deleted" in recreate_sandbox.__doc__
    assert "becomes inaccessible from this thread" in recreate_sandbox.__doc__


def test_recreate_sandbox_exported() -> None:
    from agent.tools import recreate_sandbox as exported

    assert exported is recreate_sandbox
