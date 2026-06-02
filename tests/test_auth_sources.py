from __future__ import annotations

import asyncio

import pytest

from agent.utils import auth


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


def _slack_config(github_login: str | None = "mason-gh") -> dict:
    configurable: dict = {
        "source": "slack",
        "user_email": "mason@example.com",
        "thread_id": "t1",
    }
    if github_login is not None:
        configurable["github_login"] = github_login
    return {"configurable": configurable}


def _stub_dashboard_store(
    monkeypatch: pytest.MonkeyPatch,
    *,
    token: str | None,
    expires_at: str | None = "2099-01-01T00:00:00Z",
    cached: tuple[str | None, str | None, str | None] = (None, None, None),
) -> None:
    from agent.dashboard import profiles

    async def fake_get_from_thread(thread_id: str):
        return cached

    async def fake_get_valid(login: str):
        return token

    async def fake_get_value(namespace, key):
        return {"token_expires_at": expires_at}

    async def fake_persist(thread_id: str, tok: str, expires_at: str | None = None):
        return "enc"

    monkeypatch.setattr(auth, "get_github_token_from_thread", fake_get_from_thread)
    monkeypatch.setattr(auth, "persist_encrypted_github_token", fake_persist)
    monkeypatch.setattr(profiles, "get_valid_access_token", fake_get_valid)
    monkeypatch.setattr(profiles, "_get_value", fake_get_value)


def test_resolve_github_token_slack_uses_dashboard_store(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stub_dashboard_store(monkeypatch, token="user-tok")
    monkeypatch.setattr(auth, "is_bot_token_only_mode", lambda: False)

    token, encrypted, expires_at = asyncio.run(auth.resolve_github_token(_slack_config(), "t1"))

    assert token == "user-tok"
    assert encrypted == "enc"
    assert expires_at == "2099-01-01T00:00:00Z"


def test_resolve_github_token_slack_returns_cached_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fail_get_valid(login: str):
        raise AssertionError("dashboard store should not be hit when cache is warm")

    from agent.dashboard import profiles

    _stub_dashboard_store(
        monkeypatch, token=None, cached=("cached-tok", "cached-enc", "2099-01-01T00:00:00Z")
    )
    monkeypatch.setattr(profiles, "get_valid_access_token", fail_get_valid)
    monkeypatch.setattr(auth, "is_bot_token_only_mode", lambda: False)

    token, encrypted, expires_at = asyncio.run(auth.resolve_github_token(_slack_config(), "t1"))

    assert (token, encrypted, expires_at) == (
        "cached-tok",
        "cached-enc",
        "2099-01-01T00:00:00Z",
    )


def test_resolve_github_token_slack_no_token_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stub_dashboard_store(monkeypatch, token=None)
    monkeypatch.setattr(auth, "is_bot_token_only_mode", lambda: False)

    with pytest.raises(auth.GitHubUserAuthRequired):
        asyncio.run(auth.resolve_github_token(_slack_config(), "t1"))


def test_resolve_github_token_per_user_wins_over_bot_only_mode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stub_dashboard_store(monkeypatch, token="user-tok")
    monkeypatch.setattr(auth, "is_bot_token_only_mode", lambda: True)

    async def fail_bot(thread_id: str):
        raise AssertionError("bot token must not be used when a user token exists")

    monkeypatch.setattr(auth, "_resolve_bot_installation_token", fail_bot)

    token, _, _ = asyncio.run(auth.resolve_github_token(_slack_config(), "t1"))
    assert token == "user-tok"


def test_resolve_github_token_slack_no_token_falls_back_to_bot_in_bot_only_mode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stub_dashboard_store(monkeypatch, token=None)
    monkeypatch.setattr(auth, "is_bot_token_only_mode", lambda: True)

    async def fake_bot(thread_id: str):
        return ("bot-tok", "bot-enc", None)

    monkeypatch.setattr(auth, "_resolve_bot_installation_token", fake_bot)

    token, encrypted, expires_at = asyncio.run(auth.resolve_github_token(_slack_config(), "t1"))
    assert (token, encrypted, expires_at) == ("bot-tok", "bot-enc", None)


@pytest.mark.parametrize("source", ["github", "linear"])
def test_resolve_github_token_bot_only_mode_non_slack_uses_bot(
    monkeypatch: pytest.MonkeyPatch, source: str
) -> None:
    monkeypatch.setattr(auth, "is_bot_token_only_mode", lambda: True)

    async def fake_bot(thread_id: str):
        return ("bot-tok", "bot-enc", None)

    monkeypatch.setattr(auth, "_resolve_bot_installation_token", fake_bot)

    config = {"configurable": {"source": source, "github_login": "octo", "thread_id": "t1"}}
    token, _, _ = asyncio.run(auth.resolve_github_token(config, "t1"))
    assert token == "bot-tok"
