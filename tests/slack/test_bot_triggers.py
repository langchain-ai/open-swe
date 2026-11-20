"""Global bot-trigger admission preserves explicit and allowlisted routing."""

import asyncio
from unittest.mock import DEFAULT, AsyncMock

import pytest

from openswe import event_claims
from openswe.slack import bot_triggers
from openswe.slack.bot_triggers import BotTriggers


@pytest.mark.parametrize(
    ("mode", "mentioned", "listening", "reply", "expected"),
    [
        ("mentions", True, False, "", True),
        ("mentions", True, True, "1.1", True),
        ("mentions", False, True, "", False),
        ("false", True, True, "", False),
        ("false", False, True, "", False),
        ("true", False, True, "", True),
        ("true", False, True, "1.2", True),
        ("true", False, False, "", False),
        ("true", True, False, "", True),
        ("true", False, True, "1.1", False),
        ("true", True, True, "1.1", True),
        ("true", True, False, "1.1", True),
        ("false", True, True, "1.1", False),
    ],
)
async def test_modes(
    monkeypatch: pytest.MonkeyPatch,
    mode: str,
    mentioned: bool,
    listening: bool,
    reply: str,
    expected: bool,
) -> None:
    monkeypatch.setattr(
        bot_triggers,
        "get_instance_settings",
        AsyncMock(return_value={"slack_bot_trigger_mode": mode}),
    )
    monkeypatch.setattr(bot_triggers, "is_listening_channel", AsyncMock(return_value=listening))
    assert (
        await BotTriggers.accepts(
            user_id="UBOT",
            app_id="A2",
            own_user_id="ROBIN",
            own_app_id="A1",
            channel_id="C1",
            subtype="bot_message",
            explicit_mention=mentioned,
            reply_thread_ts=reply,
            message_ts="1.2",
        )
        is expected
    )


@pytest.mark.parametrize(
    ("user", "app", "subtype", "dm"),
    [
        ("ROBIN", "A2", "", False),
        ("UBOT", "A1", "", False),
        ("UBOT", "A2", "message_changed", False),
        ("UBOT", "A2", "file_share", False),
        ("UBOT", "A2", "thread_broadcast", False),
        ("UBOT", "A2", "", True),
    ],
)
async def test_self_subtypes_and_unmentioned_dm_never_start(
    monkeypatch: pytest.MonkeyPatch,
    user: str,
    app: str,
    subtype: str,
    dm: bool,
) -> None:
    monkeypatch.setattr(BotTriggers, "mode", AsyncMock(return_value="true"))
    monkeypatch.setattr(bot_triggers, "is_listening_channel", AsyncMock(return_value=True))
    assert not await BotTriggers.accepts(
        user_id=user,
        app_id=app,
        own_user_id="ROBIN",
        own_app_id="A1",
        channel_id="C1",
        subtype=subtype,
        explicit_mention=False,
        reply_thread_ts="",
        message_ts="1.2",
        dm=dm,
    )


@pytest.mark.usefixtures("registry_db")
async def test_cooldown_is_persistent_and_message_admission_is_reusable() -> None:
    args = {"team_id": "T1", "channel_id": "C1", "bot_id": "B1", "explicit_mention": False}
    assert await BotTriggers.claim(**args, message_ts="1.2")
    assert await BotTriggers.claim(**args, message_ts="1.2")
    assert not await BotTriggers.claim(**args, message_ts="1.3")
    assert await BotTriggers.claim(**(args | {"channel_id": "C2"}), message_ts="1.3")
    assert await event_claims.held("slack_bot_cooldown", "T1:C1:B1")
    assert await event_claims.held("slack_bot_admitted", "T1:C1:B1:1.2")


@pytest.mark.usefixtures("registry_db")
async def test_admission_failure_rolls_back_cooldown(monkeypatch: pytest.MonkeyPatch) -> None:
    original = event_claims.claim
    claim = AsyncMock(wraps=original, side_effect=[DEFAULT, RuntimeError("Admission unavailable")])
    monkeypatch.setattr(event_claims, "claim", claim)
    args = {"team_id": "T1", "channel_id": "C1", "bot_id": "B1", "explicit_mention": False}
    with pytest.raises(RuntimeError, match="Admission unavailable"):
        await BotTriggers.claim(**args, message_ts="1.2")
    assert not await event_claims.held("slack_bot_cooldown", "T1:C1:B1")
    assert not await event_claims.held("slack_bot_admitted", "T1:C1:B1:1.2")
    monkeypatch.setattr(event_claims, "claim", original)
    assert await BotTriggers.claim(**args, message_ts="1.2")


@pytest.mark.usefixtures("registry_db")
@pytest.mark.parametrize("same_message", [True, False])
async def test_concurrent_admission_preserves_retries_and_cooldown(same_message: bool) -> None:
    args = {"team_id": "T1", "channel_id": "C1", "bot_id": "B1", "explicit_mention": False}
    results = await asyncio.gather(
        *(
            BotTriggers.claim(**args, message_ts="1.2" if same_message else f"1.{i}")
            for i in range(8)
        )
    )
    assert sum(results) == (8 if same_message else 1)


async def test_explicit_mentions_do_not_consume_unmentioned_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    claim = AsyncMock()
    monkeypatch.setattr(event_claims, "claim", claim)
    assert await BotTriggers.claim(
        team_id="T1",
        channel_id="C1",
        bot_id="B1",
        message_ts="1.2",
        explicit_mention=True,
    )
    claim.assert_not_awaited()
