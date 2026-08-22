"""The diffs a thread can show: its live working tree, one run's changes, and its branch."""

from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from agent.dashboard.threads import sandbox as thread_sandbox

_EMPTY_DIFF = {
    "status": "missing",
    "files": [],
    "truncated": False,
    "summary": {"files": 0, "additions": 0, "deletions": 0},
}


async def test_turn_diff_prefers_persisted_run_artifact(monkeypatch, dashboard_client) -> None:
    dashboard_client(
        thread_metadata={
            "sandbox_id": "sandbox-1",
            "turn_checkpoints": [
                {"key": "msg-1", "ref": "refs/open-swe/turns/msg-1", "started_at": "t0"}
            ],
        },
    )
    stored = {
        "status": "ready",
        "files": [
            {"path": f"{index}.py", "originalContent": "before", "modifiedContent": "after"}
            for index in range(3)
        ],
        "truncated": False,
        "summary": {"files": 3, "additions": 3, "deletions": 0},
    }
    monkeypatch.setattr("agent.settings.run_diffs.get_run_diff", AsyncMock(return_value=stored))
    create_sandbox = AsyncMock()
    monkeypatch.setattr(thread_sandbox, "create_sandbox", create_sandbox)

    result = await thread_sandbox.get_dashboard_thread_run_diff(
        "thread-1", "owner", turn_key="msg-1", max_files=2, include_content=False
    )

    assert result == {
        **stored,
        "files": [
            {**file, "originalContent": None, "modifiedContent": None}
            for file in stored["files"][:2]
        ],
        "truncated": True,
    }
    create_sandbox.assert_not_awaited()


async def test_turn_diff_hides_plan_mode_checkpoint(monkeypatch, dashboard_client) -> None:
    dashboard_client(
        thread_metadata={
            "sandbox_id": "sandbox-1",
            "turn_checkpoints": [
                {
                    "key": "msg-1",
                    "ref": "refs/open-swe/turns/msg-1",
                    "started_at": "t0",
                    "repo_path": "/workspace/repo",
                    "plan_mode": True,
                    "plan_ref": "refs/open-swe/turns/msg-1",
                }
            ],
        },
    )
    create_sandbox = AsyncMock()
    monkeypatch.setattr(thread_sandbox, "create_sandbox", create_sandbox)

    result = await thread_sandbox.get_dashboard_thread_run_diff(
        "thread-1", "owner", turn_key="msg-1"
    )

    assert result == {**_EMPTY_DIFF, "status": "ready"}
    create_sandbox.assert_not_awaited()


async def test_turn_diff_preserves_changes_before_mid_run_plan_mode(
    monkeypatch, dashboard_client
) -> None:
    dashboard_client(
        thread_metadata={
            "sandbox_id": "sandbox-1",
            "turn_checkpoints": [
                {
                    "key": "msg-1",
                    "ref": "refs/open-swe/turns/msg-1",
                    "started_at": "t0",
                    "repo_path": "/workspace/repo",
                    "plan_mode": True,
                    "plan_ref": "refs/open-swe/turns/msg-1-plan",
                }
            ],
        },
    )
    sandbox = object()
    monkeypatch.setattr(thread_sandbox, "create_sandbox", AsyncMock(return_value=sandbox))
    read_diff = AsyncMock(return_value={"status": "ready", "files": [], "truncated": False})
    monkeypatch.setattr("agent.sandboxes.turn_checkpoint.read_turn_diff", read_diff)

    await thread_sandbox.get_dashboard_thread_run_diff("thread-1", "owner", turn_key="msg-1")

    read_diff.assert_awaited_once_with(
        sandbox,
        None,
        "refs/open-swe/turns/msg-1",
        "refs/open-swe/turns/msg-1-plan",
        max_files=200,
        include_content=True,
        repo_path="/workspace/repo",
    )


async def test_turn_diff_reads_the_checkpoint_repository(monkeypatch, dashboard_client) -> None:
    dashboard_client(
        thread_metadata={
            "sandbox_id": "sandbox-1",
            "turn_checkpoints": [
                {
                    "key": "msg-1",
                    "ref": "refs/open-swe/turns/msg-1",
                    "started_at": "t0",
                    "repo_path": "/workspace/repo",
                },
                {
                    "key": "msg-2",
                    "ref": "refs/open-swe/turns/msg-2",
                    "started_at": "t1",
                    "repo_path": "/workspace/repo",
                },
            ],
        },
    )
    sandbox = object()
    monkeypatch.setattr(thread_sandbox, "create_sandbox", AsyncMock(return_value=sandbox))
    read_diff = AsyncMock(return_value={"status": "ready", "files": [], "truncated": False})
    monkeypatch.setattr("agent.sandboxes.turn_checkpoint.read_turn_diff", read_diff)

    await thread_sandbox.get_dashboard_thread_run_diff("thread-1", "owner", turn_key="msg-1")

    read_diff.assert_awaited_once_with(
        sandbox,
        None,
        "refs/open-swe/turns/msg-1",
        "refs/open-swe/turns/msg-2",
        max_files=200,
        include_content=True,
        repo_path="/workspace/repo",
    )


async def test_turn_diff_rejects_checkpoints_from_different_repositories(
    monkeypatch, dashboard_client
) -> None:
    dashboard_client(
        thread_metadata={
            "sandbox_id": "sandbox-1",
            "turn_checkpoints": [
                {
                    "key": "msg-1",
                    "ref": "refs/open-swe/turns/msg-1",
                    "started_at": "t0",
                    "repo_path": "/workspace/one",
                },
                {
                    "key": "msg-2",
                    "ref": "refs/open-swe/turns/msg-2",
                    "started_at": "t1",
                    "repo_path": "/workspace/two",
                },
            ],
        },
    )
    create_sandbox = AsyncMock()
    monkeypatch.setattr(thread_sandbox, "create_sandbox", create_sandbox)

    result = await thread_sandbox.get_dashboard_thread_run_diff(
        "thread-1", "owner", turn_key="msg-1"
    )

    assert result == _EMPTY_DIFF
    create_sandbox.assert_not_awaited()


async def test_working_tree_diff_reads_live_sandbox_against_head(
    monkeypatch, dashboard_client
) -> None:
    dashboard_client(
        thread_metadata={
            "sandbox_id": "sandbox-1",
            "turn_checkpoints": [{"ref": "refs/open-swe/turns/msg-1", "repo_path": "/work/repo"}],
        },
    )
    live = {
        "status": "ready",
        "files": [{"path": "new.py", "additions": 1, "deletions": 0}],
        "truncated": False,
        "summary": {"files": 1, "additions": 1, "deletions": 0},
    }
    sandbox = object()
    monkeypatch.setattr(thread_sandbox, "create_sandbox", AsyncMock(return_value=sandbox))
    monkeypatch.setattr(
        "agent.sandboxes.paths.aresolve_sandbox_work_dir", AsyncMock(return_value="/work")
    )
    read_diff = AsyncMock(return_value=live)
    monkeypatch.setattr("agent.sandboxes.turn_checkpoint.read_turn_diff", read_diff)

    result = await thread_sandbox.get_dashboard_thread_working_tree_diff("thread-1", "owner")

    assert result == live
    read_diff.assert_awaited_once_with(sandbox, "/work", "HEAD", None, repo_path="/work/repo")


async def test_working_tree_diff_derives_the_repo_path_from_the_thread_repo(
    monkeypatch, dashboard_client
) -> None:
    dashboard_client(
        thread_metadata={
            "sandbox_id": "sandbox-1",
            "repo_owner": "langchain-ai",
            "repo_name": "open-swe",
        }
    )
    sandbox = object()
    monkeypatch.setattr(thread_sandbox, "create_sandbox", AsyncMock(return_value=sandbox))
    monkeypatch.setattr(
        "agent.sandboxes.paths.aresolve_sandbox_work_dir", AsyncMock(return_value="/work")
    )
    read_diff = AsyncMock(return_value={**_EMPTY_DIFF, "status": "ready"})
    monkeypatch.setattr("agent.sandboxes.turn_checkpoint.read_turn_diff", read_diff)

    await thread_sandbox.get_dashboard_thread_working_tree_diff("thread-1", "owner")

    read_diff.assert_awaited_once_with(sandbox, "/work", "HEAD", None, repo_path="/work/open-swe")


async def test_working_tree_diff_does_not_fall_back_to_persisted_artifact(
    monkeypatch, dashboard_client
) -> None:
    dashboard_client(thread_metadata={"sandbox_id": "sandbox-1"})
    monkeypatch.setattr(thread_sandbox, "create_sandbox", AsyncMock(side_effect=RuntimeError))
    get_run_diff = AsyncMock()
    monkeypatch.setattr("agent.settings.run_diffs.get_run_diff", get_run_diff)

    result = await thread_sandbox.get_dashboard_thread_working_tree_diff("thread-1", "owner")

    assert result == _EMPTY_DIFF
    get_run_diff.assert_not_awaited()


async def test_branch_diff_uses_repository_from_pr_url(monkeypatch, dashboard_client) -> None:
    dashboard_client(
        thread_metadata={
            "repo_owner": "langchain-ai",
            "repo_name": "deepagents",
            "pr_number": 1925,
            "pr_url": "https://github.com/langchain-ai/open-swe/pull/1925",
            "base_branch": "main",
            "branch_name": "open-swe/feature",
        },
    )
    monkeypatch.setattr(thread_sandbox, "get_valid_access_token", AsyncMock(return_value="token"))
    build_diff = AsyncMock(
        return_value={"base_sha": "base", "head_sha": "head", "truncated": False, "files": []}
    )
    monkeypatch.setattr(thread_sandbox, "build_pr_diff_files", build_diff)

    result = await thread_sandbox.get_dashboard_thread_branch_diff("thread-1", "owner")

    assert build_diff.await_args is not None
    assert build_diff.await_args.args[1:] == ("langchain-ai/open-swe", 1925)
    assert result == {
        "prNumber": 1925,
        "baseRef": "main",
        "headRef": "open-swe/feature",
        "baseSha": "base",
        "headSha": "head",
        "truncated": False,
        "files": [],
    }


async def test_branch_diff_without_a_pull_request_compares_against_the_base(
    monkeypatch, dashboard_client
) -> None:
    dashboard_client(
        thread_metadata={
            "repo_owner": "langchain-ai",
            "repo_name": "open-swe",
            "base_branch": "main",
            "branch_name": "open-swe/feature",
        },
    )
    monkeypatch.setattr(thread_sandbox, "get_valid_access_token", AsyncMock(return_value="token"))
    build_compare = AsyncMock(
        return_value={"base_sha": "merge-base", "head_sha": "head", "truncated": False, "files": []}
    )
    monkeypatch.setattr(thread_sandbox, "build_compare_diff_files", build_compare)

    result = await thread_sandbox.get_dashboard_thread_branch_diff("thread-1", "owner")

    assert build_compare.await_args is not None
    assert build_compare.await_args.args[1:] == (
        "langchain-ai/open-swe",
        "main",
        "open-swe/feature",
    )
    assert result["prNumber"] is None
    assert result["baseSha"] == "merge-base"


async def test_branch_diff_rejects_an_unsafe_branch_name(monkeypatch, dashboard_client) -> None:
    dashboard_client(
        thread_metadata={
            "repo_owner": "langchain-ai",
            "repo_name": "open-swe",
            "base_branch": "main",
            "branch_name": "../../etc/passwd",
        },
    )
    monkeypatch.setattr(thread_sandbox, "get_valid_access_token", AsyncMock(return_value="token"))
    build_compare = AsyncMock()
    monkeypatch.setattr(thread_sandbox, "build_compare_diff_files", build_compare)

    with pytest.raises(HTTPException) as excinfo:
        await thread_sandbox.get_dashboard_thread_branch_diff("thread-1", "owner")

    assert excinfo.value.status_code == 404
    build_compare.assert_not_awaited()


async def test_branch_diff_needs_a_branch_that_left_the_base(monkeypatch, dashboard_client) -> None:
    dashboard_client(
        thread_metadata={
            "repo_owner": "langchain-ai",
            "repo_name": "open-swe",
            "base_branch": "main",
            "branch_name": "main",
        },
    )
    token = AsyncMock(return_value="token")
    monkeypatch.setattr(thread_sandbox, "get_valid_access_token", token)

    with pytest.raises(HTTPException) as excinfo:
        await thread_sandbox.get_dashboard_thread_branch_diff("thread-1", "owner")

    assert excinfo.value.status_code == 404
    assert excinfo.value.detail == "thread never branched off its base"
    token.assert_not_awaited()
