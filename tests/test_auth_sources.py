from __future__ import annotations

import asyncio
import json

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


def test_resolve_github_token_prefers_explicit_configurable_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fail_if_called(thread_id: str) -> tuple[str | None, str | None]:
        raise AssertionError

    monkeypatch.setattr(auth, "get_github_token_from_thread", fail_if_called)
    monkeypatch.setattr(auth, "is_bot_token_only_mode", lambda: False)

    token, encrypted = asyncio.run(
        auth.resolve_github_token(
            {"configurable": {"source": "acp", "github_token": "ghu_direct"}},
            "thread-1",
        )
    )

    assert token == "ghu_direct"
    assert encrypted == ""


def test_get_secret_key_for_user_prefers_user_id_api_key_map(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        auth,
        "USER_ID_API_KEY_MAP",
        json.dumps({"user-123": {"api_key": "lsv2_user_key"}}),
    )
    monkeypatch.setattr(auth, "X_SERVICE_AUTH_JWT_SECRET", "")

    secret, secret_type = auth.get_secret_key_for_user("user-123", "tenant-123")

    assert secret == "lsv2_user_key"
    assert secret_type == "api_key"


def test_resolve_github_token_allows_acp_github_app_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_get_github_token_from_thread(thread_id: str) -> tuple[str | None, str | None]:
        return None, None

    async def fake_resolve_bot_installation_token(thread_id: str) -> tuple[str, str]:
        assert thread_id == "thread-1"
        return "ghs_bot", "encrypted"

    monkeypatch.setattr(auth, "get_github_token_from_thread", fake_get_github_token_from_thread)
    monkeypatch.setattr(auth, "is_bot_token_only_mode", lambda: False)
    monkeypatch.setattr(auth, "_resolve_bot_installation_token", fake_resolve_bot_installation_token)

    token, encrypted = asyncio.run(
        auth.resolve_github_token(
            {
                "configurable": {
                    "source": "acp",
                    "allow_github_app_fallback": True,
                }
            },
            "thread-1",
        )
    )

    assert token == "ghs_bot"
    assert encrypted == "encrypted"


def test_get_github_token_for_langsmith_api_key_uses_api_key_header(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    class _FakeResponse:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, str]:
            return {"token": "ghu_ls"}

    class _FakeAsyncClient:
        async def __aenter__(self) -> _FakeAsyncClient:
            return self

        async def __aexit__(self, exc_type, exc, tb) -> None:
            return None

        async def post(self, url: str, json: dict[str, object], headers: dict[str, str]) -> _FakeResponse:
            captured["url"] = url
            captured["json"] = json
            captured["headers"] = headers
            return _FakeResponse()

    monkeypatch.setattr(auth, "GITHUB_OAUTH_PROVIDER_ID", "github-oauth")
    monkeypatch.setattr(auth, "LANGSMITH_HOST_API_URL", "https://host.example.com")
    monkeypatch.setattr(auth.httpx, "AsyncClient", _FakeAsyncClient)

    result = asyncio.run(auth.get_github_token_for_langsmith_api_key("lsv2_user", "tenant-123"))

    assert result == {"token": "ghu_ls"}
    assert captured["url"] == "https://host.example.com/v2/auth/authenticate"
    assert captured["json"] == {"provider": "github-oauth", "scopes": ["repo"]}
    assert captured["headers"] == {
        "X-API-Key": "lsv2_user",
        "X-Tenant-Id": "tenant-123",
    }
