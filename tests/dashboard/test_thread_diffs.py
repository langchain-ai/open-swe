"""The two diffs a thread can show: one turn's changes, and its pull request."""

from typing import Any
from unittest.mock import AsyncMock

from support.langgraph_fakes import FakeLangGraphClient

from agent.dashboard import thread_api


def _install_thread(monkeypatch, metadata: dict[str, Any]) -> FakeLangGraphClient:
    client = FakeLangGraphClient(thread_metadata=metadata)
    monkeypatch.setattr(thread_api, "langgraph_client", lambda: client)
    return client


_EMPTY_DIFF = {
    "status": "missing",
    "files": [],
    "truncated": False,
    "summary": {"files": 0, "additions": 0, "deletions": 0},
}


async def test_turn_diff_prefers_persisted_run_artifact(monkeypatch) -> None:
    _install_thread(
        monkeypatch,
        {
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
    monkeypatch.setattr("agent.dashboard.run_diffs.get_run_diff", AsyncMock(return_value=stored))
    create_sandbox = AsyncMock()
    monkeypatch.setattr(thread_api, "create_sandbox", create_sandbox)

    result = await thread_api.get_dashboard_thread_turn_diff(
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


async def test_turn_diff_hides_plan_mode_checkpoint(monkeypatch) -> None:
    _install_thread(
        monkeypatch,
        {
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
    monkeypatch.setattr(thread_api, "create_sandbox", create_sandbox)

    result = await thread_api.get_dashboard_thread_turn_diff("thread-1", "owner", turn_key="msg-1")

    assert result == {**_EMPTY_DIFF, "status": "ready"}
    create_sandbox.assert_not_awaited()


async def test_turn_diff_preserves_changes_before_mid_run_plan_mode(monkeypatch) -> None:
    _install_thread(
        monkeypatch,
        {
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
    monkeypatch.setattr(thread_api, "create_sandbox", AsyncMock(return_value=sandbox))
    read_diff = AsyncMock(return_value={"status": "ready", "files": [], "truncated": False})
    monkeypatch.setattr("agent.utils.turn_checkpoint.read_turn_diff", read_diff)

    await thread_api.get_dashboard_thread_turn_diff("thread-1", "owner", turn_key="msg-1")

    read_diff.assert_awaited_once_with(
        sandbox,
        None,
        "refs/open-swe/turns/msg-1",
        "refs/open-swe/turns/msg-1-plan",
        max_files=200,
        include_content=True,
        repo_path="/workspace/repo",
    )


async def test_turn_diff_reads_the_checkpoint_repository(monkeypatch) -> None:
    _install_thread(
        monkeypatch,
        {
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
    monkeypatch.setattr(thread_api, "create_sandbox", AsyncMock(return_value=sandbox))
    read_diff = AsyncMock(return_value={"status": "ready", "files": [], "truncated": False})
    monkeypatch.setattr("agent.utils.turn_checkpoint.read_turn_diff", read_diff)

    await thread_api.get_dashboard_thread_turn_diff("thread-1", "owner", turn_key="msg-1")

    read_diff.assert_awaited_once_with(
        sandbox,
        None,
        "refs/open-swe/turns/msg-1",
        "refs/open-swe/turns/msg-2",
        max_files=200,
        include_content=True,
        repo_path="/workspace/repo",
    )


async def test_turn_diff_rejects_checkpoints_from_different_repositories(monkeypatch) -> None:
    _install_thread(
        monkeypatch,
        {
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
    monkeypatch.setattr(thread_api, "create_sandbox", create_sandbox)

    result = await thread_api.get_dashboard_thread_turn_diff("thread-1", "owner", turn_key="msg-1")

    assert result == _EMPTY_DIFF
    create_sandbox.assert_not_awaited()


async def test_pr_diff_uses_repository_from_pr_url(monkeypatch) -> None:
    _install_thread(
        monkeypatch,
        {
            "repo_owner": "langchain-ai",
            "repo_name": "deepagents",
            "pr_number": 1925,
            "pr_url": "https://github.com/langchain-ai/open-swe/pull/1925",
        },
    )
    monkeypatch.setattr(thread_api, "get_valid_access_token", AsyncMock(return_value="token"))
    build_diff = AsyncMock(
        return_value={"base_sha": "base", "head_sha": "head", "truncated": False, "files": []}
    )
    monkeypatch.setattr(thread_api, "build_pr_diff_files", build_diff)

    await thread_api.get_dashboard_thread_pr_diff("thread-1", "owner")

    assert build_diff.await_args is not None
    assert build_diff.await_args.args[1:] == ("langchain-ai/open-swe", 1925)
