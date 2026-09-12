from unittest.mock import AsyncMock

import pytest

from agent.incidents import slack
from agent.incidents.models import IncidentPolicy


async def test_publication_preserves_blocks_and_delivery_metadata(monkeypatch):
    request = AsyncMock(return_value={"ts": "1.2"})
    monkeypatch.setattr(slack, "request", request)
    blocks = [{"type": "section", "text": {"type": "mrkdwn", "text": "Update"}}]
    assert await slack.publish("C1", "Update", "1.1", "pub", blocks=blocks) == "1.2"
    payload = request.await_args.kwargs
    assert payload["blocks"] == blocks and payload["thread_ts"] == "1.1"
    assert payload["metadata"]["event_payload"] == {"publication_id": "pub"}
    assert payload["unfurl_links"] is False


async def test_session_status_uses_regular_channel_thread(monkeypatch):
    request = AsyncMock(return_value={"ok": True})
    monkeypatch.setattr(slack, "request", request)
    await slack.set_session_status("C1", "1.1", "processing", "Investigate API")
    request.assert_awaited_once_with(
        "agents.sessions.setStatus",
        write=True,
        channel_id="C1",
        thread_ts="1.1",
        status="processing",
        title="Investigate API",
    )


@pytest.mark.parametrize(
    "change",
    [
        {"is_private": True},
        {"is_ext_shared": True},
        {"is_pending_ext_shared": True},
        {"is_mpim": True},
        {"is_im": True},
        {"is_channel": False},
    ],
)
def test_public_internal_gate_cannot_be_bypassed_by_matching_name(change):
    channel = {
        "id": "C1",
        "name": "inc-api",
        "is_channel": True,
        "is_private": False,
        "is_im": False,
        "is_mpim": False,
        "is_ext_shared": False,
        "is_pending_ext_shared": False,
    }
    assert slack.channel_allowed(channel, IncidentPolicy())
    assert not slack.channel_allowed(channel | change, IncidentPolicy())


def test_channel_names_and_explicit_exclusions_are_enforced():
    channel = {
        "id": "C1",
        "name": "inc-api",
        "is_channel": True,
        "is_private": False,
        "is_im": False,
        "is_mpim": False,
        "is_ext_shared": False,
        "is_pending_ext_shared": False,
    }
    assert not slack.channel_allowed(channel, IncidentPolicy(excluded_channel_ids=["C1"]))
    assert not slack.channel_allowed(channel | {"name": "general"}, IncidentPolicy())
