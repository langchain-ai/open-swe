import json
from typing import Any

import pytest

from agent import webapp
from agent.utils import slack_feedback
from agent.utils.slack_feedback import (
    process_slack_reaction_added,
    process_slack_reaction_removed,
)


class _FakeStore:
    def __init__(self) -> None:
        self.items: dict[tuple[tuple[str, ...], str], dict[str, Any]] = {}

    async def get_item(self, namespace: tuple[str, ...], key: str) -> dict[str, Any] | None:
        return self.items.get((namespace, key))

    async def put_item(self, namespace: tuple[str, ...], key: str, value: dict[str, Any]) -> None:
        self.items[(namespace, key)] = {"value": value}


class _FakeClient:
    def __init__(self) -> None:
        self.store = _FakeStore()


class _FakeBackgroundTasks:
    def __init__(self) -> None:
        self.tasks: list[tuple[Any, tuple[Any, ...]]] = []

    def add_task(self, func: Any, *args: Any) -> None:
        self.tasks.append((func, args))


class _FakeRequest:
    def __init__(self, payload: dict[str, Any]) -> None:
        self.headers: dict[str, str] = {}
        self._body = json.dumps(payload).encode()

    async def body(self) -> bytes:
        return self._body


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
async def test_reaction_added_creates_feedback(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _FakeClient()
    _store_message_mapping(client, "C123", "2.000")
    created: dict[str, Any] = {}

    def fake_create_feedback(
        run_id: str,
        key: str,
        *,
        score: float,
        comment: str | None = None,
        source_info: dict[str, Any] | None = None,
    ) -> bool:
        created.update(
            {
                "run_id": run_id,
                "key": key,
                "score": score,
                "comment": comment,
                "source_info": source_info,
            }
        )
        return True

    monkeypatch.setattr(slack_feedback, "get_client", lambda url: client)
    monkeypatch.setattr(slack_feedback, "create_langsmith_feedback", fake_create_feedback)

    await process_slack_reaction_added(_reaction_event(), event_id="Ev1")

    assert created["run_id"] == "run-1"
    assert created["key"] == "slack_reaction:C123:U123:2.000"
    assert created["score"] == 1.0
    assert created["source_info"]["reactions"] == ["thumbsup"]
    assert (("slack_reaction_events", "C123"), "Ev1") in client.store.items


@pytest.mark.asyncio
async def test_reaction_added_skips_duplicate_event(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _FakeClient()
    _store_message_mapping(client, "C123", "2.000")
    client.store.items[(("slack_reaction_events", "C123"), "Ev1")] = {"value": {"event_id": "Ev1"}}

    def fail_create_feedback(*args: Any, **kwargs: Any) -> bool:
        raise AssertionError("duplicate event should not create feedback")

    monkeypatch.setattr(slack_feedback, "get_client", lambda url: client)
    monkeypatch.setattr(slack_feedback, "create_langsmith_feedback", fail_create_feedback)

    await process_slack_reaction_added(_reaction_event(), event_id="Ev1")


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

    def fake_delete_feedback(run_id: str, key: str) -> bool:
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
async def test_reaction_without_message_mapping_is_ignored(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _FakeClient()

    def fail_create_feedback(*args: Any, **kwargs: Any) -> bool:
        raise AssertionError("unmapped message should not create feedback")

    monkeypatch.setattr(slack_feedback, "get_client", lambda url: client)
    monkeypatch.setattr(slack_feedback, "create_langsmith_feedback", fail_create_feedback)

    await process_slack_reaction_added(_reaction_event(), event_id="Ev1")


@pytest.mark.asyncio
async def test_reaction_from_non_triggering_user_is_ignored(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _FakeClient()
    _store_message_mapping(client, "C123", "2.000", triggering_user_id="UTRIGGER")

    def fail_create_feedback(*args: Any, **kwargs: Any) -> bool:
        raise AssertionError("non-triggering user should not create feedback")

    monkeypatch.setattr(slack_feedback, "get_client", lambda url: client)
    monkeypatch.setattr(slack_feedback, "create_langsmith_feedback", fail_create_feedback)

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

    def fail_create_feedback(*args: Any, **kwargs: Any) -> bool:
        raise AssertionError("conflicting reactions must not record a numeric score")

    def fake_delete_feedback(run_id: str, key: str) -> bool:
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


@pytest.mark.asyncio
async def test_slack_webhook_queues_reaction_added(monkeypatch: pytest.MonkeyPatch) -> None:
    event = _reaction_event("+1")
    payload = {"type": "event_callback", "event_id": "Ev1", "event": event}
    background_tasks = _FakeBackgroundTasks()

    monkeypatch.setattr(webapp, "verify_slack_signature", lambda **kwargs: True)

    response = await webapp.slack_webhook(_FakeRequest(payload), background_tasks)

    assert response == {"status": "accepted", "message": "Reaction feedback queued"}
    assert background_tasks.tasks == [(webapp.process_slack_reaction_added, (event, "Ev1"))]


@pytest.mark.asyncio
async def test_slack_webhook_queues_reaction_removed(monkeypatch: pytest.MonkeyPatch) -> None:
    event = {**_reaction_event("-1"), "type": "reaction_removed"}
    payload = {"type": "event_callback", "event_id": "Ev2", "event": event}
    background_tasks = _FakeBackgroundTasks()

    monkeypatch.setattr(webapp, "verify_slack_signature", lambda **kwargs: True)

    response = await webapp.slack_webhook(_FakeRequest(payload), background_tasks)

    assert response == {"status": "accepted", "message": "Reaction removal queued"}
    assert background_tasks.tasks == [(webapp.process_slack_reaction_removed, (event, "Ev2"))]


@pytest.mark.asyncio
async def test_slack_webhook_ignores_untracked_reaction(monkeypatch: pytest.MonkeyPatch) -> None:
    event = _reaction_event("eyes")
    payload = {"type": "event_callback", "event_id": "Ev3", "event": event}
    background_tasks = _FakeBackgroundTasks()

    monkeypatch.setattr(webapp, "verify_slack_signature", lambda **kwargs: True)

    response = await webapp.slack_webhook(_FakeRequest(payload), background_tasks)

    assert response == {"status": "ignored", "reason": "Reaction not tracked for feedback"}
    assert background_tasks.tasks == []
