import importlib
from typing import Any
from unittest.mock import AsyncMock

import pytest

from openswe.schedules.delivery import AutomationThreadDelivery
from openswe.slack import feedback as slack_feedback
from openswe.slack.feedback import (
    process_slack_reaction_added,
    process_slack_reaction_removed,
)
from openswe.slack.records import (
    SlackMessage,
    lookup_slack_run_mapping,
    lookup_slack_thread_run_mapping,
    store_slack_run_mapping,
)

pytestmark = pytest.mark.usefixtures("registry_db")


async def _store_message_mapping(
    channel_id: str, message_ts: str, *, triggering_user_id: str | None = "U123"
) -> None:
    await store_slack_run_mapping(
        channel_id, "1.000", "run-1", message_ts=message_ts, triggering_user_id=triggering_user_id
    )


def _reaction_event(reaction: str = "thumbsup") -> dict[str, Any]:
    return {
        "type": "reaction_added",
        "reaction": reaction,
        "user": "U123",
        "item": {"type": "message", "channel": "C123", "ts": "2.000"},
    }


@pytest.mark.asyncio
async def test_reaction_removed_deletes_feedback_when_last_reaction_removed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _store_message_mapping("C123", "2.000")
    await SlackMessage.toggle_reaction("C123", "2.000", "U123", "thumbsup", added=True)
    deleted: dict[str, str] = {}

    async def fake_delete_feedback(run_id: str, key: str) -> bool:
        deleted["run_id"] = run_id
        deleted["key"] = key
        return True

    monkeypatch.setattr(slack_feedback, "delete_langsmith_feedback", fake_delete_feedback)

    await process_slack_reaction_removed(_reaction_event(), event_id="Ev2")

    assert deleted == {"run_id": "run-1", "key": "slack_reaction:C123:U123:2.000"}


@pytest.mark.asyncio
async def test_reaction_from_non_triggering_user_is_ignored(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _store_message_mapping("C123", "2.000", triggering_user_id="UTRIGGER")

    async def fail_create_feedback(*args: Any, **kwargs: Any) -> bool:
        raise AssertionError("non-triggering user should not create feedback")

    monkeypatch.setattr(slack_feedback, "create_langsmith_feedback", fail_create_feedback)

    await process_slack_reaction_added(_reaction_event(), event_id="Ev1")


@pytest.mark.asyncio
async def test_conflicting_reactions_clear_feedback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _store_message_mapping("C123", "2.000")
    await SlackMessage.toggle_reaction("C123", "2.000", "U123", "thumbsup", added=True)
    deleted: dict[str, str] = {}

    async def fail_create_feedback(*args: Any, **kwargs: Any) -> bool:
        raise AssertionError("conflicting reactions must not record a numeric score")

    async def fake_delete_feedback(run_id: str, key: str) -> bool:
        deleted["run_id"] = run_id
        deleted["key"] = key
        return True

    monkeypatch.setattr(slack_feedback, "create_langsmith_feedback", fail_create_feedback)
    monkeypatch.setattr(slack_feedback, "delete_langsmith_feedback", fake_delete_feedback)

    # User adds a thumbsdown alongside the existing thumbsup → conflicting.
    event = {**_reaction_event("thumbsdown"), "type": "reaction_added"}
    await process_slack_reaction_added(event, event_id="EvConflict")

    assert deleted == {"run_id": "run-1", "key": "slack_reaction:C123:U123:2.000"}


@pytest.mark.parametrize(
    ("triggering_user_id", "reactor"), [("U123", "U123"), ("U123", "UOTHER"), (None, "U123")]
)
async def test_automation_reply_feedback_uses_its_own_run_and_triggering_user(
    monkeypatch: pytest.MonkeyPatch, triggering_user_id: str | None, reactor: str
) -> None:
    reply = importlib.import_module("openswe.slack.tools.reply")
    route = AutomationThreadDelivery(
        channel_id="C0ALERTS01", thread_ts="1.000", triggering_user_id=triggering_user_id
    )
    monkeypatch.setattr(
        reply,
        "get_config",
        lambda: {
            "run_id": "run-1",
            "configurable": {
                "source": "schedule",
                "thread_id": "automation-thread",
                "automation_slack_reply": route.model_dump(),
            },
        },
    )
    monkeypatch.setattr(AutomationThreadDelivery, "for_run", AsyncMock(return_value=route))
    monkeypatch.setattr(reply, "post_slack_thread_reply_with_ts", AsyncMock(return_value="2.000"))
    monkeypatch.setattr(reply, "get_slack_permalink", AsyncMock(return_value=None))
    created, deleted = AsyncMock(return_value=True), AsyncMock(return_value=True)
    monkeypatch.setattr(slack_feedback, "create_langsmith_feedback", created)
    monkeypatch.setattr(slack_feedback, "delete_langsmith_feedback", deleted)
    monkeypatch.setattr(slack_feedback, "upsert_run_outcome", AsyncMock())
    await store_slack_run_mapping(
        route.channel_id, route.thread_ts, "other-run", triggering_user_id="UUNRELATED"
    )

    result = await reply.slack_reply("Answer", "final")

    assert result["success"] is True
    mapping = await lookup_slack_run_mapping(route.channel_id, "2.000")
    assert mapping is not None and mapping.run_id == "run-1"
    assert mapping.triggering_user_id == triggering_user_id
    current = await lookup_slack_thread_run_mapping(route.channel_id, route.thread_ts)
    assert current is not None and current.run_id == "other-run"
    event = {
        **_reaction_event(),
        "user": reactor,
        "item": {"type": "message", "channel": route.channel_id, "ts": "2.000"},
    }
    await process_slack_reaction_added(event, event_id="EvAutomationAdded")
    if triggering_user_id and reactor != triggering_user_id:
        created.assert_not_awaited()
        return
    key = f"slack_reaction:{route.channel_id}:{reactor}:2.000"
    created.assert_awaited_once_with(
        "run-1",
        key,
        score=1.0,
        comment=f"Slack reaction feedback from user {reactor}",
        source_info={
            "source": "slack_reaction",
            "channel_id": route.channel_id,
            "message_ts": "2.000",
            "user_id": reactor,
            "reactions": ["thumbsup"],
        },
    )
    await process_slack_reaction_removed(
        {**event, "type": "reaction_removed"}, event_id="EvAutomationRemoved"
    )
    deleted.assert_awaited_once_with("run-1", key)
