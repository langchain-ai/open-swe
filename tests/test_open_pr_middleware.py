from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from langchain_core.messages import HumanMessage

from agent.middleware.open_pr import _create_or_update_pr, _run_gh, open_pr_if_needed


def _result(exit_code: int = 0, output: str = "") -> MagicMock:
    return MagicMock(exit_code=exit_code, output=output)


def test_run_gh_uses_dummy_token_and_repo_dir() -> None:
    sandbox = MagicMock()
    sandbox.execute.return_value = _result()

    _run_gh(sandbox, "/workspace/repo", "pr view --json url")

    sandbox.execute.assert_called_once_with(
        "cd /workspace/repo && GH_TOKEN=dummy gh pr view --json url"
    )


def test_create_or_update_pr_updates_existing_pr() -> None:
    sandbox = MagicMock()
    sandbox.execute.side_effect = [
        _result(output="https://github.com/org/repo/pull/1\n"),
        _result(),
    ]

    pr_url = _create_or_update_pr(
        sandbox,
        "/workspace/repo",
        "feat: title",
        "body",
        "main",
        "open-swe/thread",
    )

    assert pr_url == "https://github.com/org/repo/pull/1"
    assert "GH_TOKEN=dummy gh pr edit" in sandbox.execute.call_args_list[1].args[0]


def test_create_or_update_pr_creates_draft_pr() -> None:
    sandbox = MagicMock()
    sandbox.execute.side_effect = [
        _result(exit_code=1, output="no pr"),
        _result(output="https://github.com/org/repo/pull/2\n"),
    ]

    pr_url = _create_or_update_pr(
        sandbox,
        "/workspace/repo",
        "feat: title",
        "body",
        "main",
        "open-swe/thread",
    )

    assert pr_url == "https://github.com/org/repo/pull/2"
    assert "GH_TOKEN=dummy gh pr create --draft" in sandbox.execute.call_args_list[1].args[0]


@pytest.mark.asyncio
async def test_open_pr_if_needed_skips_without_changes() -> None:
    state = {"messages": [HumanMessage(content="done")]}

    with (
        patch(
            "agent.middleware.open_pr.get_config",
            return_value={
                "configurable": {
                    "thread_id": "thread-1",
                    "repo": {"owner": "org", "name": "repo"},
                },
                "metadata": {},
            },
        ),
        patch("agent.middleware.open_pr.get_github_token", return_value=None),
        patch("agent.middleware.open_pr.resolve_triggering_user_identity", return_value=None),
        patch("agent.middleware.open_pr.get_sandbox_backend", new_callable=AsyncMock) as backend,
        patch("agent.middleware.open_pr.aresolve_repo_dir", new_callable=AsyncMock) as repo_dir,
        patch("agent.middleware.open_pr.git_has_uncommitted_changes", return_value=False),
        patch("agent.middleware.open_pr.git_fetch_origin"),
        patch("agent.middleware.open_pr.git_has_unpushed_commits", return_value=False),
    ):
        backend.return_value = MagicMock()
        repo_dir.return_value = "/workspace/repo"

        result = await open_pr_if_needed.aafter_agent(state, MagicMock())

    assert result is None
