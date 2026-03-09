from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
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


def test_get_github_token_for_user_returns_auth_url_on_http_error_with_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When the auth API responds with a non-2xx status that still carries a
    "url" field in the JSON body (e.g. first-time OAuth authorisation required),
    get_github_token_for_user should return {"auth_url": ...} rather than a
    generic error dict so callers can surface the proper auth link to the user.
    """

    # Build a fake httpx response that raises HTTPStatusError but contains a URL
    fake_url = "https://github.com/login/oauth/authorize?client_id=test"
    fake_response = MagicMock(spec=httpx.Response)
    fake_response.status_code = 401
    fake_response.text = f'{{"url": "{fake_url}"}}'
    fake_response.json.return_value = {"url": fake_url}

    http_error = httpx.HTTPStatusError(
        "401 Unauthorized", request=MagicMock(), response=fake_response
    )

    async def fake_post(*args, **kwargs):  # noqa: ANN001, ANN002, ANN003
        raise http_error

    monkeypatch.setattr(auth, "GITHUB_OAUTH_PROVIDER_ID", "test-provider")
    monkeypatch.setattr(auth, "X_SERVICE_AUTH_JWT_SECRET", "test-secret")

    with patch.object(auth, "get_service_jwt_token_for_user", return_value="fake-jwt"):
        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client.post = AsyncMock(side_effect=http_error)
            mock_client_cls.return_value = mock_client

            result = asyncio.run(auth.get_github_token_for_user("user-123", "tenant-456"))

    assert result == {"auth_url": fake_url}, (
        f"Expected auth_url to be extracted from error response body, got: {result}"
    )
