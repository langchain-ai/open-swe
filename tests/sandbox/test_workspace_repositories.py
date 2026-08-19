import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from deepagents.backends.protocol import ExecuteResponse, LsResult

from agent.middleware.prepare_run import PrepareRunState
from agent.prompt import construct_system_prompt
from agent.server import _initial_workspace_repositories
from agent.utils.workspace_repositories import WorkspaceRepository, discover_workspace_repositories


@pytest.mark.asyncio
async def test_discovers_repositories_and_sanitizes_remote_credentials() -> None:
    backend = MagicMock()
    backend.als = AsyncMock(
        return_value=LsResult(
            entries=[
                {"path": "/workspace/not-a-repo/", "is_dir": True},
                {"path": "/workspace/open-swe/", "is_dir": True},
            ]
        )
    )
    backend.aexecute = AsyncMock(
        side_effect=[
            ExecuteResponse(output="", exit_code=128),
            ExecuteResponse(
                output=(
                    "/workspace/open-swe\n"
                    "origin\thttps://user:secret@github.com/langchain-ai/open-swe.git?token=x "
                    "(fetch)\n"
                    "origin\thttps://github.com/langchain-ai/open-swe.git (push)\n"
                ),
                exit_code=0,
            ),
        ]
    )

    repositories = await discover_workspace_repositories(backend, "/workspace")

    assert repositories == [
        {
            "path": "/workspace/open-swe",
            "remotes": {"origin": "https://github.com/langchain-ai/open-swe.git"},
        }
    ]


@pytest.mark.asyncio
async def test_initial_workspace_inventory_is_reused_from_state() -> None:
    repository: WorkspaceRepository = {
        "path": "/workspace/open-swe",
        "remotes": {"origin": "https://github.com/langchain-ai/open-swe.git"},
    }
    state = PrepareRunState(messages=[])
    backend = MagicMock()

    with patch(
        "agent.server.discover_workspace_repositories",
        new_callable=AsyncMock,
        return_value=[repository],
    ) as discover:
        initial = await _initial_workspace_repositories(state, backend, "/workspace")
        state["workspace_repositories"] = initial
        reused = await _initial_workspace_repositories(state, backend, "/workspace")

    assert reused is initial
    discover.assert_awaited_once_with(backend, "/workspace")


def test_system_prompt_includes_initial_workspace_inventory() -> None:
    repository: WorkspaceRepository = {
        "path": "/workspace/open-swe",
        "remotes": {"origin": "https://github.com/langchain-ai/open-swe.git"},
    }

    prompt = construct_system_prompt(
        working_dir="/workspace",
        workspace_repositories=[repository],
    )

    assert json.dumps(repository, sort_keys=True) in prompt
