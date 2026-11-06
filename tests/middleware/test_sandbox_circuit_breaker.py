from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from openswe.github import thread_token
from openswe.github.comments import AGENT_NOTICE_MARKER
from openswe.middleware import sandbox_circuit_breaker as notices


@pytest.mark.parametrize("posted", [True, False])
@pytest.mark.parametrize("retry", ["same", "different", "expired"])
async def test_github_sandbox_notice_uses_app_and_suppresses_successful_duplicates(
    monkeypatch: pytest.MonkeyPatch, posted: bool, retry: str
) -> None:
    monkeypatch.setattr(thread_token, "get_github_token", Mock(return_value="user-token"))
    thread = SimpleNamespace(thread_id="thread-1", metadata={})
    monkeypatch.setattr(notices, "get_slack_target", AsyncMock(return_value=None))
    monkeypatch.setattr(notices.THREADS, "get", AsyncMock(return_value=thread))
    update = AsyncMock()
    monkeypatch.setattr(notices.THREADS, "update", update)
    monkeypatch.setattr(
        notices, "get_github_app_installation_token", AsyncMock(return_value="app-token")
    )
    post = AsyncMock(return_value=posted)
    monkeypatch.setattr(notices, "post_github_comment", post)
    config = {
        "configurable": {
            "thread_id": "thread-1",
            "repo": {"owner": "org", "name": "repo"},
            "pr_number": 42,
        }
    }

    await notices.post_sandbox_unreachable_notification(config, sandbox_id="sb-dead")

    post.assert_awaited_once_with(
        {"owner": "org", "name": "repo"},
        42,
        f"{notices.sandbox_unreachable_message(sandbox_id='sb-dead')}\n\n{AGENT_NOTICE_MARKER}",
        token="app-token",
    )
    if posted:
        thread.metadata.update(update.call_args.args[1])
        if retry == "expired":
            thread.metadata["github_sandbox_unreachable_notice"]["sent_at"] = (
                datetime.now(UTC) - timedelta(hours=1)
            ).timestamp()
    else:
        update.assert_not_awaited()
    await notices.post_sandbox_unreachable_notification(
        config, sandbox_id="sb-other" if retry == "different" else "sb-dead"
    )
    assert post.await_count == (1 if posted and retry == "same" else 2)


async def test_slack_sandbox_notice_keeps_slack_delivery(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        notices, "get_slack_target", AsyncMock(return_value=("channel", "timestamp"))
    )
    reply = AsyncMock()
    monkeypatch.setattr(notices, "post_slack_thread_reply", reply)
    github = AsyncMock()
    monkeypatch.setattr(notices, "post_github_comment", github)

    await notices.post_sandbox_unreachable_notification(
        {"configurable": {"thread_id": "thread-1"}}, sandbox_id="sb-dead"
    )

    message = notices.sandbox_unreachable_message(sandbox_id="sb-dead")
    reply.assert_awaited_once_with(
        "channel",
        "timestamp",
        message,
        agent_thread_id="thread-1",
        notice=notices.SlackNotice(notices.SANDBOX_UNREACHABLE_NOTICE, message),
    )
    github.assert_not_awaited()
