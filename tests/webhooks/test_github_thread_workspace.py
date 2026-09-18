"""A GitHub PR comment keeps an existing thread in the workspace it started in."""

from unittest.mock import AsyncMock

import pytest

from agent.webhooks import common as webhook_common


@pytest.mark.parametrize(
    ("saved", "expected"),
    [("oss", "oss"), (None, "moved-to")],
    ids=["saved workspace wins", "new thread follows the repository"],
)
async def test_trigger_or_queue_run_prefers_the_threads_saved_workspace(
    monkeypatch: pytest.MonkeyPatch, saved: str | None, expected: str
) -> None:
    monkeypatch.setattr(webhook_common, "authorize_github_thread", AsyncMock())
    monkeypatch.setattr(webhook_common, "get_thread_workspace", AsyncMock(return_value=saved))
    monkeypatch.setattr(
        webhook_common, "workspace_for_repo_config", AsyncMock(return_value="moved-to")
    )
    upsert = AsyncMock()
    dispatch = AsyncMock()
    monkeypatch.setattr(webhook_common, "upsert_agent_thread_metadata", upsert)
    monkeypatch.setattr(webhook_common, "dispatch_agent_run", dispatch)

    await webhook_common.trigger_or_queue_run(
        "thread-1",
        "please continue",
        github_login="bob",
        github_user_id=2,
        repo_config={"owner": "acme", "name": "api"},
        pr_number=7,
    )

    assert upsert.await_args.kwargs["workspace"] == expected
    configurable = dispatch.await_args.args[2]
    assert configurable["workspace"] == expected
    assert configurable["environment"] == expected
