"""Tests for the Slack Assistants API integration (feature-flagged)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agent.utils import slack as slack_utils


def _ok_response() -> MagicMock:
    response = MagicMock()
    response.json.return_value = {"ok": True}
    response.raise_for_status.return_value = None
    return response


def _err_response(error: str = "channel_not_found") -> MagicMock:
    response = MagicMock()
    response.json.return_value = {"ok": False, "error": error}
    response.raise_for_status.return_value = None
    return response


def _async_client_cm(post_response: MagicMock) -> AsyncMock:
    client_cm = AsyncMock()
    client_cm.__aenter__.return_value = client_cm
    client_cm.post = AsyncMock(return_value=post_response)
    return client_cm


@pytest.mark.asyncio
async def test_set_slack_assistant_status_noop_when_flag_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("SLACK_ASSISTANTS_API_ENABLED", raising=False)
    monkeypatch.setattr(slack_utils, "SLACK_BOT_TOKEN", "xoxb-test")

    client_cm = _async_client_cm(_ok_response())
    with patch.object(slack_utils.httpx, "AsyncClient", return_value=client_cm):
        ok = await slack_utils.set_slack_assistant_status("C1", "1.0", "thinking…")

    assert ok is False
    client_cm.post.assert_not_called()


@pytest.mark.asyncio
async def test_set_slack_assistant_status_noop_when_no_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SLACK_ASSISTANTS_API_ENABLED", "true")
    monkeypatch.setattr(slack_utils, "SLACK_BOT_TOKEN", "")

    client_cm = _async_client_cm(_ok_response())
    with patch.object(slack_utils.httpx, "AsyncClient", return_value=client_cm):
        ok = await slack_utils.set_slack_assistant_status("C1", "1.0", "thinking…")

    assert ok is False
    client_cm.post.assert_not_called()


@pytest.mark.asyncio
async def test_set_slack_assistant_status_calls_correct_endpoint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SLACK_ASSISTANTS_API_ENABLED", "true")
    monkeypatch.setattr(slack_utils, "SLACK_BOT_TOKEN", "xoxb-test")

    client_cm = _async_client_cm(_ok_response())
    with patch.object(slack_utils.httpx, "AsyncClient", return_value=client_cm):
        ok = await slack_utils.set_slack_assistant_status("C1", "1.0", "thinking…")

    assert ok is True
    client_cm.post.assert_awaited_once()
    args, kwargs = client_cm.post.call_args
    assert args[0].endswith("/assistants.threads.setStatus")
    assert kwargs["json"] == {
        "channel_id": "C1",
        "thread_ts": "1.0",
        "status": "thinking…",
    }


@pytest.mark.asyncio
async def test_set_slack_assistant_status_returns_false_on_slack_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SLACK_ASSISTANTS_API_ENABLED", "true")
    monkeypatch.setattr(slack_utils, "SLACK_BOT_TOKEN", "xoxb-test")

    client_cm = _async_client_cm(_err_response("invalid_thread"))
    with patch.object(slack_utils.httpx, "AsyncClient", return_value=client_cm):
        ok = await slack_utils.set_slack_assistant_status("C1", "1.0", "thinking…")

    assert ok is False


@pytest.mark.asyncio
async def test_clear_slack_assistant_status_sends_empty_status(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SLACK_ASSISTANTS_API_ENABLED", "true")
    monkeypatch.setattr(slack_utils, "SLACK_BOT_TOKEN", "xoxb-test")

    client_cm = _async_client_cm(_ok_response())
    with patch.object(slack_utils.httpx, "AsyncClient", return_value=client_cm):
        ok = await slack_utils.clear_slack_assistant_status("C1", "1.0")

    assert ok is True
    _, kwargs = client_cm.post.call_args
    assert kwargs["json"]["status"] == ""


@pytest.mark.asyncio
async def test_post_slack_thread_reply_clears_status_when_flag_enabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SLACK_ASSISTANTS_API_ENABLED", "true")
    monkeypatch.setattr(slack_utils, "SLACK_BOT_TOKEN", "xoxb-test")

    client_cm = _async_client_cm(_ok_response())
    with patch.object(slack_utils.httpx, "AsyncClient", return_value=client_cm):
        ok = await slack_utils.post_slack_thread_reply("C1", "1.0", "hello")

    assert ok is True
    # 1 call for chat.postMessage, 1 for assistants.threads.setStatus (clear)
    assert client_cm.post.await_count == 2
    endpoints = [call.args[0] for call in client_cm.post.await_args_list]
    assert any(url.endswith("/chat.postMessage") for url in endpoints)
    assert any(url.endswith("/assistants.threads.setStatus") for url in endpoints)


@pytest.mark.asyncio
async def test_post_slack_thread_reply_skips_clear_when_flag_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("SLACK_ASSISTANTS_API_ENABLED", raising=False)
    monkeypatch.setattr(slack_utils, "SLACK_BOT_TOKEN", "xoxb-test")

    client_cm = _async_client_cm(_ok_response())
    with patch.object(slack_utils.httpx, "AsyncClient", return_value=client_cm):
        ok = await slack_utils.post_slack_thread_reply("C1", "1.0", "hello")

    assert ok is True
    assert client_cm.post.await_count == 1
    assert client_cm.post.call_args.args[0].endswith("/chat.postMessage")


@pytest.mark.asyncio
async def test_post_slack_thread_reply_does_not_clear_on_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SLACK_ASSISTANTS_API_ENABLED", "true")
    monkeypatch.setattr(slack_utils, "SLACK_BOT_TOKEN", "xoxb-test")

    client_cm = _async_client_cm(_err_response("channel_not_found"))
    with patch.object(slack_utils.httpx, "AsyncClient", return_value=client_cm):
        ok = await slack_utils.post_slack_thread_reply("C1", "1.0", "hello")

    assert ok is False
    assert client_cm.post.await_count == 1


def test_is_slack_assistants_api_enabled_truthy_values(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for truthy in ("1", "true", "TRUE", "yes", "Yes"):
        monkeypatch.setenv("SLACK_ASSISTANTS_API_ENABLED", truthy)
        assert slack_utils._is_slack_assistants_api_enabled() is True

    for falsy in ("0", "false", "no", "", "off"):
        monkeypatch.setenv("SLACK_ASSISTANTS_API_ENABLED", falsy)
        assert slack_utils._is_slack_assistants_api_enabled() is False
