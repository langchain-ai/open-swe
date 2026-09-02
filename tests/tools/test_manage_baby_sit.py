import importlib
from unittest.mock import AsyncMock

import pytest

from agent.baby_sit import BabySitWatch

manage_tool = importlib.import_module("agent.tools.manage_baby_sit")


async def test_manage_baby_sit_starts_watch_from_current_thread(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    configurable = {
        "thread_id": "thread-1",
        "source": "slack",
        "github_login": "octocat",
        "repo": {"owner": "acme", "name": "repo"},
        "slack_thread": {"channel_id": "C1", "thread_ts": "1.2"},
    }
    monkeypatch.setattr(manage_tool, "get_config", lambda: {"configurable": configurable})
    monkeypatch.setattr(
        manage_tool, "resolve_github_token", AsyncMock(return_value=("token", None))
    )
    monkeypatch.setattr(
        manage_tool,
        "get_github_app_installation_id_for_repo",
        AsyncMock(return_value=42),
    )
    monkeypatch.setattr(
        manage_tool,
        "fetch_pr",
        AsyncMock(return_value={"state": "open", "head": {"sha": "head-1", "ref": "feature"}}),
    )
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
    assert start.await_args.kwargs["source_context"].dump() == {
        "slack_thread": {"channel_id": "C1", "thread_ts": "1.2"}
    }


async def test_manage_baby_sit_rejects_other_repository(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        manage_tool,
        "get_config",
        lambda: {
            "configurable": {
                "thread_id": "thread-1",
                "repo": {"owner": "acme", "name": "repo"},
            }
        },
    )

    result = await manage_tool.manage_baby_sit("https://github.com/other/repo/pull/7")

    assert result == {
        "success": False,
        "error": "Pull request does not match this thread's repository",
    }
