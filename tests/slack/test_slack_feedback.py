from typing import Any

import pytest

from openswe.slack import feedback as slack_feedback
from openswe.slack.feedback import (
    process_slack_reaction_added,
    process_slack_reaction_removed,
)
from openswe.slack.records import SlackMessage, store_slack_run_mapping

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
