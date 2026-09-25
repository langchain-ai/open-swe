import importlib
from unittest.mock import AsyncMock

import pytest

from agent.baby_sit import BabySitWatch

manage_tool = importlib.import_module("agent.tools.manage_baby_sit")


async def test_manage_baby_sit_starts_cross_repo_watch_from_github_issue(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    configurable = {
        "thread_id": "thread-1",
        "source": "github",
        "github_login": "octocat",
        "repo": {"owner": "acme", "name": "default"},
        "github_issue": {"number": 12, "url": "https://github.com/acme/default/issues/12"},
        "workspace": "oss",
    }
    monkeypatch.setattr(manage_tool, "get_config", lambda: {"configurable": configurable})
    monkeypatch.setattr(
        manage_tool, "resolve_github_token", AsyncMock(return_value=("token", None))
    )
    installation = AsyncMock(side_effect=[42, 84])
    monkeypatch.setattr(
        manage_tool,
        "get_github_app_installation_id_for_repo",
        installation,
    )
    monkeypatch.setattr(
        manage_tool,
        "fetch_pr",
        AsyncMock(return_value={"state": "open", "head": {"sha": "head-1", "ref": "feature"}}),
    )
    monkeypatch.setattr(manage_tool, "list_check_runs", AsyncMock(return_value=[]))
    monkeypatch.setattr(manage_tool, "list_commit_statuses", AsyncMock(return_value=[]))
    monkeypatch.setattr(manage_tool, "ready_wakeup_head", AsyncMock(return_value=None))
    start = AsyncMock(
        return_value=BabySitWatch(
            key="acme/repo#7",
            pr_url="https://github.com/acme/repo/pull/7",
            head_sha="head-1",
        )
    )
    monkeypatch.setattr(manage_tool, "start_watch", start)

    result = await manage_tool.manage_baby_sit("https://github.com/acme/repo/pull/7")

    assert result["success"] is True
    assert result["poll_schedule"] == "every 10 minutes"
    assert start.await_args is not None
    assert start.await_args.kwargs["thread_id"] == "thread-1"
    assert start.await_args.kwargs["installation_id"] == 42
    assert start.await_args.kwargs["pr_ref"].owner == "acme"
    assert start.await_args.kwargs["pr_ref"].repo == "repo"
    assert start.await_args.kwargs["run_config"]["source_repo"] == {
        "owner": "acme",
        "name": "default",
    }
    assert start.await_args.kwargs["run_config"]["source_installation_id"] == 84
    # The watch's own runs must boot from the same workspace as the thread.
    assert start.await_args.kwargs["run_config"]["workspace"] == "oss"
    assert start.await_args.kwargs["source_context"].dump() == {
        "github_issue": {
            "number": 12,
            "url": "https://github.com/acme/default/issues/12",
        }
    }
    assert installation.await_args_list[0].args == ("acme", "repo")
    assert installation.await_args_list[1].args == ("acme", "default")


@pytest.mark.parametrize(
    ("check_runs", "statuses", "state"),
    [
        ([{"status": "completed", "conclusion": "success"}], [], "success"),
        ([{"status": "completed", "conclusion": "cancelled"}], [], "blocked"),
    ],
)
async def test_manage_baby_sit_refuses_terminal_non_failing_checks(
    monkeypatch: pytest.MonkeyPatch,
    check_runs: list[dict[str, str]],
    statuses: list[dict[str, str]],
    state: str,
) -> None:
    monkeypatch.setattr(
        manage_tool,
        "get_config",
        lambda: {"configurable": {"thread_id": "thread-1"}},
    )
    monkeypatch.setattr(
        manage_tool, "resolve_github_token", AsyncMock(return_value=("token", None))
    )
    monkeypatch.setattr(
        manage_tool,
        "fetch_pr",
        AsyncMock(return_value={"state": "open", "head": {"sha": "head-1", "ref": "feature"}}),
    )
    monkeypatch.setattr(manage_tool, "list_check_runs", AsyncMock(return_value=check_runs))
    monkeypatch.setattr(manage_tool, "list_commit_statuses", AsyncMock(return_value=statuses))
    start = AsyncMock()
    monkeypatch.setattr(manage_tool, "start_watch", start)

    result = await manage_tool.manage_baby_sit("https://github.com/acme/repo/pull/7")

    assert result == {
        "success": False,
        "already_green": True,
        "state": state,
        "error": "All checks on this pull request are already terminal and non-failing; "
        "there is nothing to watch. If the merge is blocked on a human action, report it "
        "and stop.",
    }
    start.assert_not_awaited()


async def test_manage_baby_sit_refuses_rearming_same_ready_head(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        manage_tool,
        "get_config",
        lambda: {"configurable": {"thread_id": "thread-1"}},
    )
    monkeypatch.setattr(
        manage_tool, "resolve_github_token", AsyncMock(return_value=("token", None))
    )
    monkeypatch.setattr(
        manage_tool,
        "fetch_pr",
        AsyncMock(return_value={"state": "open", "head": {"sha": "head-1", "ref": "feature"}}),
    )
    monkeypatch.setattr(
        manage_tool,
        "list_check_runs",
        AsyncMock(return_value=[{"status": "in_progress", "name": "tests"}]),
    )
    monkeypatch.setattr(manage_tool, "list_commit_statuses", AsyncMock(return_value=[]))
    monkeypatch.setattr(manage_tool, "ready_wakeup_head", AsyncMock(return_value="head-1"))
    start = AsyncMock()
    monkeypatch.setattr(manage_tool, "start_watch", start)

    result = await manage_tool.manage_baby_sit("https://github.com/acme/repo/pull/7")

    assert result["success"] is False
    assert result["already_ready"] is True
    assert result["state"] == "pending"
    start.assert_not_awaited()


@pytest.mark.parametrize(
    ("check_runs", "ready_head"),
    [
        ([{"status": "in_progress", "name": "tests"}], None),
        ([{"status": "completed", "name": "tests", "conclusion": "failure"}], "head-1"),
    ],
)
async def test_manage_baby_sit_starts_for_pending_or_failing_checks(
    monkeypatch: pytest.MonkeyPatch,
    check_runs: list[dict[str, str]],
    ready_head: str | None,
) -> None:
    configurable = {"thread_id": "thread-1"}
    monkeypatch.setattr(manage_tool, "get_config", lambda: {"configurable": configurable})
    monkeypatch.setattr(
        manage_tool, "resolve_github_token", AsyncMock(return_value=("token", None))
    )
    monkeypatch.setattr(
        manage_tool,
        "fetch_pr",
        AsyncMock(return_value={"state": "open", "head": {"sha": "head-1", "ref": "feature"}}),
    )
    monkeypatch.setattr(manage_tool, "list_check_runs", AsyncMock(return_value=check_runs))
    monkeypatch.setattr(manage_tool, "list_commit_statuses", AsyncMock(return_value=[]))
    monkeypatch.setattr(manage_tool, "ready_wakeup_head", AsyncMock(return_value=ready_head))
    monkeypatch.setattr(
        manage_tool, "get_github_app_installation_id_for_repo", AsyncMock(return_value=42)
    )
    start = AsyncMock(
        return_value=BabySitWatch(
            key="acme/repo#7", pr_url="https://github.com/acme/repo/pull/7", head_sha="head-1"
        )
    )
    monkeypatch.setattr(manage_tool, "start_watch", start)

    result = await manage_tool.manage_baby_sit("https://github.com/acme/repo/pull/7")

    assert result["success"] is True
    start.assert_awaited_once()
