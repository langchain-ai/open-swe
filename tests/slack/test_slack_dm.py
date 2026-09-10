import asyncio

import pytest

from agent.slack import client as slack_client


class _Response:
    status_code = 200
    headers: dict[str, str] = {}

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict[str, object]:
        return {"ok": True, "channel": {"id": "D12345678"}}


class _Client:
    def __init__(self, **kwargs: object) -> None:
        pass

    async def __aenter__(self) -> _Client:
        return self

    async def __aexit__(self, *args: object) -> None:
        return None

    async def post(self, url: str, **kwargs: object) -> _Response:
        assert url.endswith("/conversations.open")
        assert kwargs["json"] == {"users": "U12345678"}
        return _Response()


def test_post_slack_dm_opens_conversation_before_posting(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(slack_client, "SLACK_BOT_TOKEN", "token")
    monkeypatch.setattr(slack_client.httpx2, "AsyncClient", _Client)
    posted: list[tuple[str, str]] = []

    async def post(channel_id: str, text: str) -> tuple[str | None, str | None]:
        posted.append((channel_id, text))
        return "1.0", None

    monkeypatch.setattr(slack_client, "post_slack_top_level_message_with_ts", post)

    result = asyncio.run(slack_client.post_slack_dm("U12345678", "Request approved"))

    assert result == ("1.0", None)
    assert posted == [("D12345678", "Request approved")]
