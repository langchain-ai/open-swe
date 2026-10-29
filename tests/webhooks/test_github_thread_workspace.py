"""A GitHub PR comment keeps an existing thread in the workspace it started in."""

from unittest.mock import AsyncMock

import pytest

from openswe.webhooks import common as webhook_common


@pytest.mark.parametrize(
    ("saved", "expected", "slack_thread"),
    [
        ("oss", "oss", {"channel_id": "C123", "thread_ts": "1700000000.000100"}),
        ("oss", "oss", None),
        (None, "moved-to", None),
    ],
    ids=["saved Slack thread", "saved web thread", "new thread follows the repository"],
)
async def test_trigger_or_queue_run_prefers_the_threads_saved_workspace(
    monkeypatch: pytest.MonkeyPatch,
    saved: str | None,
    expected: str,
    slack_thread: dict[str, str] | None,
) -> None:
    record = webhook_common.ThreadRecord(
        exists=saved is not None,
        metadata={
            "workspace": saved,
            "source_context": {"slack_thread": slack_thread} if slack_thread else {},
        }
        if saved
        else {},
    )
    monkeypatch.setattr(webhook_common.ThreadRecord, "read", AsyncMock(return_value=record))
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
    assert configurable["source"] == "github"
    if slack_thread:
        assert configurable["slack_thread"] == slack_thread
    else:
        assert "slack_thread" not in configurable
