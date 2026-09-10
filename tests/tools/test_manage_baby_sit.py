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
    assert start.await_args.kwargs["source_context"].dump() == {
        "github_issue": {
            "number": 12,
            "url": "https://github.com/acme/default/issues/12",
        }
    }
    assert installation.await_args_list[0].args == ("acme", "repo")
    assert installation.await_args_list[1].args == ("acme", "default")
