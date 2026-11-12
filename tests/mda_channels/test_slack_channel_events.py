"""MDA hands a Slack channel's own changes to ``/mda/slack/channel-events``."""

import json
from datetime import UTC, datetime
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi import FastAPI
from pydantic import JsonValue
from standardwebhooks import Webhook

from openswe.mda_channels import slack_channel_events
from openswe.mda_channels.slack_channel_events import router

_SERVICE_SECRET = "5e" * 32  # noqa: S105 - test value


@pytest.fixture(autouse=True)
def _setup(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MDA_SERVICE_SECRET", _SERVICE_SECRET)
    monkeypatch.setenv("SLACK_APP_ID", "A1")


def _call(kind: str, channel: JsonValue) -> dict[str, JsonValue]:
    return {
        "event": {
            "key": "channel",
            "deliveryId": "delivery-1",
            "payload": {"type": kind, "channel": channel},
            "channelId": "C9",
            "teamId": "T1",
        },
        "thread": {"id": "thread-1", "channel": {"name": "slack"}},
    }


async def _post(body: dict[str, JsonValue], *, signed: bool = True) -> httpx.Response:
    app = FastAPI()
    app.include_router(router)
    content = json.dumps(body).encode()
    headers = {"content-type": "application/json"}
    if signed:
        now = datetime.now(tz=UTC)
        headers |= {
            "webhook-id": "msg_delivery-1_channel",
            "webhook-timestamp": str(int(now.timestamp())),
            "webhook-signature": Webhook(_SERVICE_SECRET).sign(
                "msg_delivery-1_channel", now, content.decode()
            ),
        }
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        return await client.post("/mda/slack/channel-events", content=content, headers=headers)


async def test_only_mda_may_hand_one_over() -> None:
    response = await _post(_call("channel_archive", "C9"), signed=False)

    assert response.status_code == 401


@pytest.mark.parametrize(
    ("kind", "channel", "directory"),
    [
        ("channel_created", {"id": "C9", "name": "inc-1"}, True),
        ("channel_archive", "C9", True),
        ("group_rename", {"id": "C9", "name": "secret"}, True),
    ],
)
async def test_a_channel_change_keeps_the_backends_records(
    monkeypatch: pytest.MonkeyPatch, kind: str, channel: JsonValue, directory: bool
) -> None:
    observe = AsyncMock()
    incident = AsyncMock(return_value=None)
    monkeypatch.setattr(slack_channel_events.SlackChannel, "observe", observe)
    monkeypatch.setattr(slack_channel_events.incidents, "handle_slack_event", incident)

    response = await _post(_call(kind, channel))

    assert response.status_code == 200
    assert response.json() is None, "it starts no run"
    assert observe.await_count == int(directory)
    # Incidents sees Slack's envelope, with the workspace MDA verified and this app.
    envelope = incident.await_args.args[0]
    assert envelope["event"] == {"type": kind, "channel": channel}
    assert (envelope["team_id"], envelope["api_app_id"]) == ("T1", "A1")
