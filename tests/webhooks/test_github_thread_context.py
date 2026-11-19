"""GitHub follow-ups retain the thread's Slack delivery destination."""

from unittest.mock import AsyncMock

import pytest

from openswe.webhooks import common as webhook_common


@pytest.mark.parametrize(
    "slack_thread", [{"channel_id": "C123", "thread_ts": "1700000000.000100"}, None]
)
async def test_github_followup_uses_repository_and_existing_slack_destination(
    monkeypatch: pytest.MonkeyPatch, slack_thread: dict[str, str] | None
) -> None:
    record = webhook_common.ThreadRecord(
        exists=True,
        metadata={
            "source_context": {"slack_thread": slack_thread} if slack_thread else {},
        },
    )
    monkeypatch.setattr(webhook_common.ThreadRecord, "read", AsyncMock(return_value=record))
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

    assert upsert.await_args.kwargs["repo_config"] == {"owner": "acme", "name": "api"}
    configurable = dispatch.await_args.args[2]
    assert configurable["repo"] == {"owner": "acme", "name": "api"}
    assert configurable["source"] == "github"
    if slack_thread:
        assert configurable["slack_thread"] == slack_thread
    else:
        assert "slack_thread" not in configurable
