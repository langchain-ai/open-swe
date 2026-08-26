import pytest
from pydantic import ValidationError

from agent.dashboard.team_settings import TeamSettingsUpdate, get_team_firehose_channel_id


def test_channel_id_is_normalized():
    assert (
        TeamSettingsUpdate(slack_firehose_channel_id=" #C0123ABCDEF ").slack_firehose_channel_id
        == "C0123ABCDEF"
    )


def test_blank_channel_disables_the_firehose():
    assert TeamSettingsUpdate(slack_firehose_channel_id="  ").slack_firehose_channel_id is None
    assert TeamSettingsUpdate().slack_firehose_channel_id is None


def test_channel_name_is_rejected():
    with pytest.raises(ValidationError):
        TeamSettingsUpdate(slack_firehose_channel_id="not a channel")


async def test_getter_reads_the_stored_channel(monkeypatch):
    async def settings() -> dict[str, str]:
        return {"slack_firehose_channel_id": "C0123ABCDEF"}

    monkeypatch.setattr("agent.dashboard.team_settings.get_team_settings", settings)
    assert await get_team_firehose_channel_id() == "C0123ABCDEF"


async def test_getter_returns_none_when_unset(monkeypatch):
    async def settings() -> dict[str, None]:
        return {"slack_firehose_channel_id": None}

    monkeypatch.setattr("agent.dashboard.team_settings.get_team_settings", settings)
    assert await get_team_firehose_channel_id() is None
