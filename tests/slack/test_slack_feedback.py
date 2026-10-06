from typing import Any

import pytest

from agent.slack import feedback as slack_feedback
from agent.slack.feedback import (
    process_slack_reaction_added,
    process_slack_reaction_removed,
)


class _FakeStore:
    def __init__(self) -> None:
        self.items: dict[tuple[tuple[str, ...], str], dict[str, Any]] = {}

    async def get_item(self, namespace: tuple[str, ...], key: str) -> dict[str, Any] | None:
        return self.items.get((tuple(namespace), key))

    async def put_item(self, namespace: tuple[str, ...], key: str, value: dict[str, Any]) -> None:
        self.items[(namespace, key)] = {"value": value}


class _FakeClient:
    def __init__(self) -> None:
        self.store = _FakeStore()


def _store_message_mapping(
    client: _FakeClient,
    channel_id: str,
    message_ts: str,
    *,
    triggering_user_id: str | None = "U123",
) -> None:
    value: dict[str, Any] = {"run_id": "run-1", "thread_ts": "1.000"}
    if triggering_user_id:
        value["triggering_user_id"] = triggering_user_id
    client.store.items[(("slack_run_map", channel_id), f"message:{message_ts}")] = {"value": value}


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
    client = _FakeClient()
    _store_message_mapping(client, "C123", "2.000")
    client.store.items[(("slack_reaction_state", "C123"), "run-1:U123:2.000")] = {
        "value": {
            "run_id": "run-1",
            "user_id": "U123",
            "message_ts": "2.000",
            "reactions": ["thumbsup"],
        }
    }
    deleted: dict[str, str] = {}

    async def fake_delete_feedback(run_id: str, key: str) -> bool:
        deleted["run_id"] = run_id
        deleted["key"] = key
        return True

    monkeypatch.setattr(slack_feedback, "get_client", lambda url: client)
    monkeypatch.setattr(slack_feedback, "delete_langsmith_feedback", fake_delete_feedback)

    await process_slack_reaction_removed(_reaction_event(), event_id="Ev2")

    assert deleted == {"run_id": "run-1", "key": "slack_reaction:C123:U123:2.000"}
    state = client.store.items[(("slack_reaction_state", "C123"), "run-1:U123:2.000")]
    assert state["value"]["reactions"] == []


@pytest.mark.asyncio
async def test_reaction_from_non_triggering_user_is_ignored(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _FakeClient()
    _store_message_mapping(client, "C123", "2.000", triggering_user_id="UTRIGGER")

    async def fail_create_feedback(*args: Any, **kwargs: Any) -> bool:
        raise AssertionError("non-triggering user should not create feedback")

    monkeypatch.setattr(slack_feedback, "get_client", lambda url: client)
    monkeypatch.setattr(slack_feedback, "create_langsmith_feedback", fail_create_feedback)
    monkeypatch.setattr("agent.store.store_client", lambda: client)

    await process_slack_reaction_added(_reaction_event(), event_id="Ev1")


@pytest.mark.asyncio
async def test_conflicting_reactions_clear_feedback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _FakeClient()
    _store_message_mapping(client, "C123", "2.000")
    client.store.items[(("slack_reaction_state", "C123"), "run-1:U123:2.000")] = {
        "value": {
            "run_id": "run-1",
            "user_id": "U123",
            "message_ts": "2.000",
            "reactions": ["thumbsup"],
        }
    }
    deleted: dict[str, str] = {}

    async def fail_create_feedback(*args: Any, **kwargs: Any) -> bool:
        raise AssertionError("conflicting reactions must not record a numeric score")

    async def fake_delete_feedback(run_id: str, key: str) -> bool:
        deleted["run_id"] = run_id
        deleted["key"] = key
        return True

    monkeypatch.setattr(slack_feedback, "get_client", lambda url: client)
    monkeypatch.setattr(slack_feedback, "create_langsmith_feedback", fail_create_feedback)
    monkeypatch.setattr(slack_feedback, "delete_langsmith_feedback", fake_delete_feedback)

    # User adds a thumbsdown alongside the existing thumbsup → conflicting.
    event = {**_reaction_event("thumbsdown"), "type": "reaction_added"}
    await process_slack_reaction_added(event, event_id="EvConflict")

    assert deleted == {"run_id": "run-1", "key": "slack_reaction:C123:U123:2.000"}
