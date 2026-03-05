from __future__ import annotations

import asyncio

import pytest

from agent.utils import auth


def test_leave_failure_comment_posts_to_slack_thread(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    called: dict[str, str] = {}

    async def fake_post_slack_thread_reply(channel_id: str, thread_ts: str, message: str) -> bool:
        called["channel_id"] = channel_id
        called["thread_ts"] = thread_ts
        called["message"] = message
        return True

    monkeypatch.setattr(auth, "post_slack_thread_reply", fake_post_slack_thread_reply)
    monkeypatch.setattr(
        auth,
        "get_config",
        lambda: {"configurable": {"slack_thread": {"channel_id": "C123", "thread_ts": "1.2"}}},
    )

    asyncio.run(auth.leave_failure_comment("slack", "auth failed"))

    assert called == {"channel_id": "C123", "thread_ts": "1.2", "message": "auth failed"}


def test_leave_failure_comment_unknown_source_raises() -> None:
    with pytest.raises(ValueError, match="Unknown source"):
        asyncio.run(auth.leave_failure_comment("unknown", "auth failed"))
