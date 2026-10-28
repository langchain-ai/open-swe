"""Slack Web API transport tests."""

from __future__ import annotations

import asyncio
import json
from typing import Any

import httpx
import pytest

from managed_deepagents._channels.slack.transport import create_slack_web_api_transport
from managed_deepagents._channels.types import ChannelError


def test_transport_rate_limited() -> None:
    def fake_fetch(url: str, headers: dict[str, str], body: bytes) -> dict[str, Any]:
        return {"ok": False, "error": "ratelimited"}

    transport = create_slack_web_api_transport(bot_token="xoxb-test", fetch=fake_fetch)

    async def _run() -> None:
        with pytest.raises(ChannelError) as err:
            await transport.post(
                message={"type": "content", "content": "hello"}, target={"conversation_id": "C1"}
            )
        assert err.value.code == "channel_provider_rate_limited"

    asyncio.run(_run())


@pytest.mark.parametrize("status", [200, 429])
def test_default_transport_posts_json(monkeypatch: pytest.MonkeyPatch, status: int) -> None:
    def respond(request: httpx.Request) -> httpx.Response:
        assert str(request.url) == "https://mock.test/api/chat.postMessage"
        assert request.headers["authorization"] == "Bearer xoxb-test"
        assert request.headers["content-type"].startswith("application/json")
        assert json.loads(request.content) == {
            "text": "hello",
            "channel": "C1",
            "thread_ts": "111.000",
        }
        if status == 429:
            return httpx.Response(status, text="Too many requests")
        return httpx.Response(status, json={"ok": True, "ts": "111.222", "channel": "C1"})

    client = httpx.AsyncClient
    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        lambda **kwargs: client(transport=httpx.MockTransport(respond), **kwargs),
    )
    transport = create_slack_web_api_transport(
        bot_token="xoxb-test", api_base="https://mock.test/api/"
    )
    post = transport.post(
        message={"type": "content", "content": "hello"},
        target={"conversation_id": "C1", "thread_id": "111.000"},
    )
    if status == 429:
        with pytest.raises(ChannelError) as error:
            asyncio.run(post)
        assert error.value.code == "channel_provider_rate_limited"
    else:
        assert asyncio.run(post) == {"id": "slack:C1:111.222", "provider_message_id": "111.222"}
