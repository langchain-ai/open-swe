from unittest.mock import AsyncMock

from agent.slack import channels
from agent.slack.channels import SlackChannel


def _record(**overrides: object) -> SlackChannel:
    values = {
        "id": "C123",
        "team_id": "T123",
        "name": "old-name",
        "first_seen_at_ms": 10,
        "updated_at_ms": 20,
        **overrides,
    }
    return SlackChannel.model_validate(values)


async def test_upsert_slack_channel_preserves_identity_and_refreshes_name(monkeypatch) -> None:
    store = AsyncMock()
    store.get.return_value = _record()
    store.put.side_effect = lambda _key, record: record
    monkeypatch.setattr(channels, "_store", lambda _team_id: store)
    monkeypatch.setattr(channels, "now_ms", lambda: 30)

    record = await channels.upsert_slack_channel(
        "C123",
        {"name": "new-name", "is_private": True},
        team_id="T123",
    )

    assert record == _record(name="new-name", is_private=True, updated_at_ms=30)
    store.put.assert_awaited_once_with("C123", record)


async def test_get_slack_channel_falls_back_to_legacy_workspace(monkeypatch) -> None:
    current = AsyncMock()
    current.get.return_value = None
    legacy = AsyncMock()
    legacy.get.return_value = _record(team_id="")
    monkeypatch.setattr(
        channels,
        "_store",
        lambda team_id: current if team_id == "T123" else legacy,
    )

    record = await channels.get_slack_channel("C123", team_id="T123")

    assert record == _record(team_id="")
