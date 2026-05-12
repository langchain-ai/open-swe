"""Tests for the Slack Assistants API integration."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agent.utils import slack as slack_utils


def _ok_response() -> MagicMock:
    response = MagicMock()
    response.json.return_value = {"ok": True, "ts": "1.0"}
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
async def test_set_slack_assistant_status_noop_when_no_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
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
    monkeypatch.setattr(slack_utils, "SLACK_BOT_TOKEN", "xoxb-test")

    client_cm = _async_client_cm(_ok_response())
    with patch.object(slack_utils.httpx, "AsyncClient", return_value=client_cm):
        ok = await slack_utils.set_slack_assistant_status("C1", "1.0", "thinking…")

    assert ok is True
    client_cm.post.assert_awaited_once()
    args, kwargs = client_cm.post.call_args
    assert args[0].endswith("/assistant.threads.setStatus")
    assert kwargs["json"] == {
        "channel_id": "C1",
        "thread_ts": "1.0",
        "status": "thinking…",
    }


@pytest.mark.asyncio
async def test_set_slack_assistant_status_passes_loading_messages(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(slack_utils, "SLACK_BOT_TOKEN", "xoxb-test")

    client_cm = _async_client_cm(_ok_response())
    with patch.object(slack_utils.httpx, "AsyncClient", return_value=client_cm):
        await slack_utils.set_slack_assistant_status(
            "C1", "1.0", "thinking…", loading_messages=["a", "b", "c"]
        )

    _, kwargs = client_cm.post.call_args
    assert kwargs["json"]["loading_messages"] == ["a", "b", "c"]


@pytest.mark.asyncio
async def test_set_slack_assistant_status_caps_loading_messages_at_10(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(slack_utils, "SLACK_BOT_TOKEN", "xoxb-test")

    client_cm = _async_client_cm(_ok_response())
    with patch.object(slack_utils.httpx, "AsyncClient", return_value=client_cm):
        await slack_utils.set_slack_assistant_status(
            "C1", "1.0", loading_messages=[f"m{i}" for i in range(15)]
        )

    _, kwargs = client_cm.post.call_args
    assert len(kwargs["json"]["loading_messages"]) == 10
    assert kwargs["json"]["loading_messages"][0] == "m0"
    assert kwargs["json"]["loading_messages"][-1] == "m9"


@pytest.mark.asyncio
async def test_set_slack_assistant_status_omits_loading_messages_when_unset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(slack_utils, "SLACK_BOT_TOKEN", "xoxb-test")

    client_cm = _async_client_cm(_ok_response())
    with patch.object(slack_utils.httpx, "AsyncClient", return_value=client_cm):
        await slack_utils.set_slack_assistant_status("C1", "1.0", "thinking…")

    _, kwargs = client_cm.post.call_args
    assert "loading_messages" not in kwargs["json"]


@pytest.mark.asyncio
async def test_set_slack_assistant_status_returns_false_on_slack_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(slack_utils, "SLACK_BOT_TOKEN", "xoxb-test")

    client_cm = _async_client_cm(_err_response("invalid_thread"))
    with patch.object(slack_utils.httpx, "AsyncClient", return_value=client_cm):
        ok = await slack_utils.set_slack_assistant_status("C1", "1.0", "thinking…")

    assert ok is False


@pytest.mark.asyncio
async def test_post_slack_thread_reply_does_not_call_set_status(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Slack auto-clears the indicator on post; no extra setStatus call needed."""
    monkeypatch.setattr(slack_utils, "SLACK_BOT_TOKEN", "xoxb-test")

    client_cm = _async_client_cm(_ok_response())
    with patch.object(slack_utils.httpx, "AsyncClient", return_value=client_cm):
        ok = await slack_utils.post_slack_thread_reply("C1", "1.0", "hello")

    assert ok is True
    assert client_cm.post.await_count == 1
    assert client_cm.post.call_args.args[0].endswith("/chat.postMessage")
