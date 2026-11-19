"""Slack-started threads keep repository and global model defaults."""

from typing import Any
from unittest.mock import AsyncMock

import pytest

from openswe.dashboard.settings import SettingsUpdate, upsert_instance_settings
from openswe.run_config import Repo
from openswe.slack import webhook as slack_webhooks
from openswe.slack.request import SlackRequest
from openswe.webhooks import common as webhook_common
from tests.conftest import FakeUserRecords
from tests.slack.test_slack_context import _setup_slack_mention_fakes

_needs_settings_rows = pytest.mark.usefixtures("registry_db")


async def test_the_personal_repository_default_is_global(user_records: FakeUserRecords) -> None:
    user_records.seed("profile", "alice", {"default_repo": "acme/global"})
    assert await webhook_common.get_profile_default_repo("alice") == {
        "owner": "acme",
        "name": "global",
    }


@_needs_settings_rows
async def test_the_vision_fallback_reads_the_global_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An image falls back when the globally configured model cannot read it."""
    captured: dict[str, Any] = {}
    _setup_slack_mention_fakes(monkeypatch, captured)

    async def fake_thread_exists(thread_id: str) -> bool:
        return False

    async def fake_thread_messages(channel_id: str, thread_ts: str) -> list[dict[str, Any]]:
        return [
            {
                "ts": "1700000000.000200",
                "text": "<@UBOT> look at https://example.com/shot.png",
                "user": "U123",
            }
        ]

    async def no_image_block(*args: object, **kwargs: object) -> None:
        return None

    monkeypatch.setattr(webhook_common, "thread_exists", fake_thread_exists)
    monkeypatch.setattr(
        webhook_common.ThreadRecord,
        "read",
        AsyncMock(return_value=webhook_common.ThreadRecord(exists=False, metadata={})),
    )
    monkeypatch.setattr(webhook_common, "fetch_slack_thread_messages", fake_thread_messages)
    monkeypatch.setattr(webhook_common, "fetch_image_block", no_image_block)

    await upsert_instance_settings(
        SettingsUpdate(
            default_agent_model="fireworks:accounts/fireworks/models/kimi-k3",
            default_agent_reasoning_effort="high",
        )
    )

    request = SlackRequest.model_validate(
        {
            "channel_id": "C0SS",
            "thread_ts": "1700000000.000100",
            "event_ts": "1700000000.000200",
            "user_id": "U123",
            "text": "<@UBOT> look at https://example.com/shot.png",
            "bot_user_id": "UBOT",
        }
    )

    await slack_webhooks._process_slack_mention_impl(request, None)

    configurable = captured["run_create"]["kwargs"]["config"]["configurable"]
    assert "workspace" not in configurable
    assert (configurable["agent_model_id"], configurable["agent_effort"]) == (
        webhook_common.default_vision_model_pair()
    )


@_needs_settings_rows
@pytest.mark.parametrize("text", ["<@UBOT> /workspace:internal hello", "<@UBOT> hello"])
async def test_a_named_repository_is_not_changed_by_a_removed_selector(
    monkeypatch: pytest.MonkeyPatch,
    text: str,
) -> None:
    captured: dict[str, Any] = {}
    _setup_slack_mention_fakes(monkeypatch, captured)

    async def fake_thread_exists(thread_id: str) -> bool:
        return False

    monkeypatch.setattr(webhook_common, "thread_exists", fake_thread_exists)
    monkeypatch.setattr(
        webhook_common.ThreadRecord,
        "read",
        AsyncMock(return_value=webhook_common.ThreadRecord(exists=False, metadata={})),
    )

    request = SlackRequest.model_validate(
        {
            "channel_id": "C0SS",
            "thread_ts": "1700000000.000100",
            "event_ts": "1700000000.000200",
            "user_id": "U123",
            "text": text,
            "bot_user_id": "UBOT",
        }
    )

    await slack_webhooks._process_slack_mention_impl(
        request,
        webhook_common.SlackRepoResolution(Repo(owner="acme", name="internal"), explicit=True),
    )

    configurable = captured["run_create"]["kwargs"]["config"]["configurable"]
    assert "workspace" not in configurable
    assert configurable["repo"] == {"owner": "acme", "name": "internal"}
