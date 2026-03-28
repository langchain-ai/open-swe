from __future__ import annotations

import asyncio

import pytest

from agent.utils import auth


def test_is_bot_token_only_mode_when_github_oauth_is_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(auth, "GITHUB_OAUTH_PROVIDER_ID", "")
    monkeypatch.setattr(auth, "LANGSMITH_API_KEY", "")
    monkeypatch.setattr(auth, "X_SERVICE_AUTH_JWT_SECRET", "")
    monkeypatch.setattr(auth, "USER_ID_API_KEY_MAP", "")

    assert auth.is_bot_token_only_mode() is True


def test_leave_failure_comment_posts_to_slack_thread(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    called: dict[str, str] = {}

    async def fake_post_slack_ephemeral_message(
        channel_id: str, user_id: str, text: str, thread_ts: str | None = None
    ) -> bool:
        called["channel_id"] = channel_id
        called["user_id"] = user_id
        called["thread_ts"] = thread_ts
        called["message"] = text
        return True

    async def fake_post_slack_thread_reply(channel_id: str, thread_ts: str, message: str) -> bool:
        raise AssertionError("post_slack_thread_reply should not be called when ephemeral succeeds")

    monkeypatch.setattr(auth, "post_slack_ephemeral_message", fake_post_slack_ephemeral_message)
    monkeypatch.setattr(auth, "post_slack_thread_reply", fake_post_slack_thread_reply)
    monkeypatch.setattr(
        auth,
        "get_config",
        lambda: {
            "configurable": {
                "slack_thread": {
                    "channel_id": "C123",
                    "thread_ts": "1.2",
                    "triggering_user_id": "U123",
                }
            }
        },
    )

    asyncio.run(auth.leave_failure_comment("slack", "auth failed"))

    assert called == {
        "channel_id": "C123",
        "user_id": "U123",
        "thread_ts": "1.2",
        "message": "auth failed",
    }


def test_leave_failure_comment_falls_back_to_slack_thread_when_ephemeral_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    thread_called: dict[str, str] = {}

    async def fake_post_slack_ephemeral_message(
        channel_id: str, user_id: str, text: str, thread_ts: str | None = None
    ) -> bool:
        return False

    async def fake_post_slack_thread_reply(channel_id: str, thread_ts: str, message: str) -> bool:
        thread_called["channel_id"] = channel_id
        thread_called["thread_ts"] = thread_ts
        thread_called["message"] = message
        return True

    monkeypatch.setattr(auth, "post_slack_ephemeral_message", fake_post_slack_ephemeral_message)
    monkeypatch.setattr(auth, "post_slack_thread_reply", fake_post_slack_thread_reply)
    monkeypatch.setattr(
        auth,
        "get_config",
        lambda: {
            "configurable": {
                "slack_thread": {
                    "channel_id": "C123",
                    "thread_ts": "1.2",
                    "triggering_user_id": "U123",
                }
            }
        },
    )

    asyncio.run(auth.leave_failure_comment("slack", "auth failed"))

    assert thread_called == {"channel_id": "C123", "thread_ts": "1.2", "message": "auth failed"}


def test_resolve_bot_installation_token_does_not_persist_thread_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_get_github_app_installation_token() -> str | None:
        return "bot-token"

    async def fake_persist_encrypted_github_token(thread_id: str, token: str) -> str:
        raise AssertionError("bot-token-only mode should not persist installation tokens")

    monkeypatch.setattr(
        auth, "get_github_app_installation_token", fake_get_github_app_installation_token
    )
    monkeypatch.setattr(
        auth, "persist_encrypted_github_token", fake_persist_encrypted_github_token
    )

    token, encrypted = asyncio.run(auth._resolve_bot_installation_token("thread-123"))

    assert token == "bot-token"
    assert encrypted == ""
