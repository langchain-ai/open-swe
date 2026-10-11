import builtins
from typing import Any
from unittest.mock import AsyncMock

import pytest

from openswe.slack import client as slack_client
from openswe.slack import http as slack_http
from openswe.slack import stop as slack_stop
from openswe.slack import thinking
from openswe.slack.stop import process_slack_stop_reaction
from openswe.threads import handlers
from openswe.users.models import User, UserIdentity


class FakeStore:
    def __init__(self) -> None:
        self.items: dict[tuple[tuple[str, ...], str], dict[str, Any]] = {}

    async def get_item(self, namespace: tuple[str, ...], key: str) -> dict[str, Any] | None:
        return self.items.get((namespace, key))

    async def put_item(self, namespace: tuple[str, ...], key: str, value: dict[str, Any]) -> None:
        self.items[(namespace, key)] = {"value": value}


class FakeThreads:
    def __init__(self) -> None:
        self.values: dict[str, dict[str, Any]] = {}
        self.updates: list[tuple[str, dict[str, Any]]] = []

    async def get(self, thread_id: str) -> dict[str, Any]:
        if thread_id not in self.values:
            raise RuntimeError("not found")
        return self.values[thread_id]

    async def update(self, *, thread_id: str, metadata: dict[str, Any]) -> None:
        self.updates.append((thread_id, metadata))
        current = self.values[thread_id].setdefault("metadata", {})
        current.update(metadata)


class FakeRuns:
    def __init__(self) -> None:
        self.by_status: dict[str, list[dict[str, object]]] = {"pending": [], "running": []}
        self.cancelled: list[dict[str, Any]] = []
        self.fail_cancel = False

    async def list(
        self, thread_id: str, *, status: str, limit: int, offset: int
    ) -> list[dict[str, object]]:
        del thread_id
        return self.by_status[status][offset : offset + limit]

    async def cancel_many(
        self, *, thread_id: str, run_ids: builtins.list[str], action: str
    ) -> None:
        if self.fail_cancel:
            raise RuntimeError("cancel failed")
        self.cancelled.append({"thread_id": thread_id, "run_ids": run_ids, "action": action})


class FakeClient:
    def __init__(self) -> None:
        self.store = FakeStore()
        self.threads = FakeThreads()
        self.runs = FakeRuns()
        self.cleared_queues: list[str] = []
        self.fail_clear = False


def _event(message_ts: str, *, user_id: str = "UOTHER") -> dict[str, Any]:
    return {
        "type": "reaction_added",
        "reaction": "x",
        "user": user_id,
        "item": {"type": "message", "channel": "C123", "ts": message_ts},
    }


def _add_thread(client: FakeClient, thread_ts: str = "1.000") -> str:
    thread_id = f"thread-{thread_ts}"
    client.store.items[(("slack_thread_map", "C123"), thread_ts)] = {
        "value": {"thread_id": thread_id, "channel_id": "C123", "thread_ts": thread_ts}
    }
    client.threads.values[thread_id] = {
        "thread_id": thread_id,
        "metadata": {
            "source": "slack",
            "github_login": "owner",
            "triggering_user_email": "owner@example.com",
            "repo": {"owner": "langchain-ai", "name": "open-swe"},
            "environment": "default",
            "source_context": {
                "slack_thread": {
                    "channel_id": "C123",
                    "thread_ts": thread_ts,
                    "triggering_user_id": "UOWNER",
                    "triggering_user_email": "owner@example.com",
                }
            },
        },
    }
    return thread_id


def _map_reply(client: FakeClient, message_ts: str, thread_ts: str = "1.000") -> None:
    client.store.items[(("slack_run_map", "C123"), f"message:{message_ts}")] = {
        "value": {"run_id": "run-old", "thread_ts": thread_ts}
    }


def _patch_handler(
    monkeypatch: pytest.MonkeyPatch, client: FakeClient
) -> tuple[list[dict[str, Any]], list[str]]:
    dispatched: list[dict[str, Any]] = []
    claimed: list[str] = []

    async def fake_claim(event_id: str) -> bool:
        claimed.append(event_id)
        return True

    async def fake_dispatch(
        thread_id: str,
        content: str,
        configurable: dict[str, Any],
        *,
        source: str,
        thread_title: str | None,
        metadata: dict[str, Any],
        client: FakeClient,
    ) -> dict[str, str]:
        dispatched.append(
            {
                "thread_id": thread_id,
                "content": content,
                "configurable": configurable,
                "source": source,
                "metadata": metadata,
                "client": client,
            }
        )
        return {"run_id": "run-summary"}

    class FakeQueuedMessage:
        @staticmethod
        async def clear(thread_id: str) -> None:
            if client.fail_clear:
                raise RuntimeError("database unavailable")
            client.cleared_queues.append(thread_id)

    monkeypatch.setattr(slack_stop, "get_client", lambda url: client)
    monkeypatch.setattr(slack_stop, "QueuedMessage", FakeQueuedMessage)
    monkeypatch.setattr(slack_stop, "claim_slack_event", fake_claim)
    monkeypatch.setattr(slack_stop, "dispatch_agent_run", fake_dispatch)
    return dispatched, claimed


async def test_stop_reaction_on_mapped_reply_interrupts_all_runs_and_dispatches_agent_summary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = FakeClient()
    thread_id = _add_thread(client)
    _map_reply(client, "2.000")
    client.runs.by_status["pending"] = [{"run_id": "run-pending"}]
    client.runs.by_status["running"] = [{"run_id": "run-running"}]
    dispatched, claimed = _patch_handler(monkeypatch, client)

    await process_slack_stop_reaction(_event("2.000"), event_id="EvStop")

    assert claimed == ["EvStop"]
    assert client.runs.cancelled == [
        {
            "thread_id": thread_id,
            "run_ids": ["run-pending", "run-running"],
            "action": "interrupt",
        }
    ]
    assert client.cleared_queues == [thread_id]
    assert client.threads.updates[0][1]["latest_run_status"] == "interrupted"
    assert len(dispatched) == 1
    assert dispatched[0]["thread_id"] == thread_id
    assert dispatched[0]["source"] == "slack"
    assert dispatched[0]["configurable"]["github_login"] == "owner"
    assert dispatched[0]["configurable"]["stop_summary"] is True
    assert dispatched[0]["configurable"]["slack_thread"]["triggering_user_id"] == "UOWNER"
    thread_mapping = client.store.items[(("slack_run_map", "C123"), "thread:1.000")]
    assert thread_mapping["value"]["run_id"] == "run-summary"


async def test_stop_reaction_ignores_mismatched_thread_metadata(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = FakeClient()
    thread_id = _add_thread(client)
    client.threads.values[thread_id]["metadata"]["source_context"]["slack_thread"]["channel_id"] = (
        "COTHER"
    )
    _map_reply(client, "2.000")
    dispatched, claimed = _patch_handler(monkeypatch, client)

    await process_slack_stop_reaction(_event("2.000"), event_id="EvMismatch")

    assert dispatched == []
    assert claimed == []


async def test_duplicate_stop_reaction_has_no_side_effects(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = FakeClient()
    _add_thread(client)
    _map_reply(client, "2.000")
    dispatched, _ = _patch_handler(monkeypatch, client)

    async def duplicate_claim(event_id: str) -> bool:
        assert event_id == "EvDuplicate"
        return False

    monkeypatch.setattr(slack_stop, "claim_slack_event", duplicate_claim)

    await process_slack_stop_reaction(_event("2.000"), event_id="EvDuplicate")

    assert dispatched == []
    assert client.runs.cancelled == []
    assert client.cleared_queues == []


async def test_failed_cancellation_does_not_dispatch_success_summary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = FakeClient()
    _add_thread(client)
    _map_reply(client, "2.000")
    client.runs.by_status["running"] = [{"run_id": "run-running"}]
    client.runs.fail_cancel = True
    dispatched, _ = _patch_handler(monkeypatch, client)

    await process_slack_stop_reaction(_event("2.000"), event_id="EvFailure")

    assert dispatched == []
    assert client.cleared_queues == []
    assert client.threads.updates == []


async def test_failed_queue_cleanup_does_not_dispatch_summary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = FakeClient()
    _add_thread(client)
    _map_reply(client, "2.000")
    client.fail_clear = True
    dispatched, _ = _patch_handler(monkeypatch, client)

    await process_slack_stop_reaction(_event("2.000"), event_id="EvStoreFailure")

    assert dispatched == []
    assert client.threads.updates == []


def _patch_native_stop(monkeypatch: pytest.MonkeyPatch, client: FakeClient) -> AsyncMock:
    _patch_handler(monkeypatch, client)
    user = User(identities=[UserIdentity(provider="github", external_id="123", login="owner")])
    monkeypatch.setattr(User, "for_identity", AsyncMock(return_value=user))
    monkeypatch.setattr(handlers, "langgraph_client", lambda: client)
    monkeypatch.setattr(handlers, "_thread_summary", AsyncMock(return_value={}))
    monkeypatch.setattr(handlers, "settle_run_turn", AsyncMock())
    monkeypatch.setattr(slack_client, "post_slack_thread_reply", AsyncMock())
    monkeypatch.setattr(slack_http.SlackClient, "bot", lambda: AsyncMock())
    monkeypatch.setattr(slack_http, "slack_identity", AsyncMock(return_value={"team_id": "T123"}))
    status = AsyncMock(return_value=True)
    monkeypatch.setattr(thinking, "set_slack_thread_status", status)
    return status


async def test_native_stop_targets_ordinary_thread_preserves_queued_work_and_deduplicates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = FakeClient()
    thread_id = _add_thread(client)
    code_thread_id = _add_thread(client, "0")
    client.runs.by_status["running"] = [{"run_id": "running"}]
    client.runs.by_status["pending"] = [
        {"run_id": "other-follow-up", "metadata": {"queued_by": "other"}}
    ]
    queued_key = (("queue", thread_id), "pending_messages")
    client.store.items[queued_key] = {"value": {"messages": [{"text": "later"}]}}
    status = _patch_native_stop(monkeypatch, client)
    monkeypatch.setattr(slack_stop, "claim_slack_event", AsyncMock(side_effect=[True, False]))
    event: dict[str, object] = {"channel": "C123", "thread_ts": "1.000", "user": "UOWNER"}

    await slack_stop.process_agent_session_stopped(event, "EvStop", "T123")
    await slack_stop.process_agent_session_stopped(event, "EvStop", "T123")

    assert client.runs.cancelled == [
        {"thread_id": thread_id, "run_ids": ["running"], "action": "interrupt"}
    ]
    assert client.store.items[queued_key]["value"]["messages"] == [{"text": "later"}]
    assert client.store.deleted == []
    assert "latest_run_status" not in client.threads.values[code_thread_id]["metadata"]
    status.assert_awaited_once_with("C123", "1.000", "")


@pytest.mark.parametrize("rejection", ["workspace", "moved", "unlinked", "private"])
async def test_native_stop_rejects_wrong_location_or_unauthorized_user(
    monkeypatch: pytest.MonkeyPatch, rejection: str
) -> None:
    client = FakeClient()
    thread_id = _add_thread(client)
    metadata = client.threads.values[thread_id]["metadata"]
    slack_thread = metadata["source_context"]["slack_thread"]
    slack_thread["team_id"] = "T123"
    client.runs.by_status["running"] = [{"run_id": "running"}]
    status = _patch_native_stop(monkeypatch, client)
    if rejection == "workspace":
        slack_thread["team_id"] = "TOTHER"
    elif rejection == "moved":
        slack_thread["thread_ts"] = "2.000"
    elif rejection == "unlinked":
        monkeypatch.setattr(User, "for_identity", AsyncMock(return_value=None))
    else:
        metadata.update(visibility="private", owner_login="someone-else")

    await slack_stop.process_agent_session_stopped(
        {"channel": "C123", "thread_ts": "1.000", "user": "UOWNER"}, "EvStop", "T123"
    )

    assert client.runs.cancelled == []
    assert client.threads.updates == []
    assert client.store.deleted == []
    status.assert_not_awaited()


@pytest.mark.parametrize("cancel_fails", [False, True])
async def test_native_stop_clears_status_after_interruption_even_if_follow_up_fails(
    monkeypatch: pytest.MonkeyPatch, cancel_fails: bool
) -> None:
    client = FakeClient()
    thread_id = _add_thread(client)
    client.runs.by_status["running"] = [{"run_id": "running"}]
    client.runs.fail_cancel = cancel_fails
    status = _patch_native_stop(monkeypatch, client)
    follow_up = AsyncMock(side_effect=RuntimeError("follow-up dispatch failed"))
    monkeypatch.setattr(handlers, "dispatch_pending_follow_ups", follow_up)

    await slack_stop.process_agent_session_stopped(
        {"channel": "C123", "thread_ts": "1.000", "user": "UOWNER"}, "EvStop", "T123"
    )

    if cancel_fails:
        assert client.runs.cancelled == []
        status.assert_not_awaited()
        follow_up.assert_not_awaited()
    else:
        assert client.runs.cancelled == [
            {"thread_id": thread_id, "run_ids": ["running"], "action": "interrupt"}
        ]
        status.assert_awaited_once_with("C123", "1.000", "")
        follow_up.assert_awaited_once()


@pytest.mark.parametrize("thread_ts", ["0", None])
async def test_native_stop_preserves_code_channel_routing(
    monkeypatch: pytest.MonkeyPatch, thread_ts: str | None
) -> None:
    client = FakeClient()
    thread_id = _add_thread(client, "0")
    client.runs.by_status["running"] = [{"run_id": "running"}]
    _patch_handler(monkeypatch, client)
    status = AsyncMock()
    monkeypatch.setattr(slack_stop, "set_session_status", status)
    event: dict[str, object] = {"channel": "C123"}
    if thread_ts is not None:
        event["thread_ts"] = thread_ts

    await slack_stop.process_agent_session_stopped(event, "EvStop")

    assert client.runs.cancelled == [
        {"thread_id": thread_id, "run_ids": ["running"], "action": "interrupt"}
    ]
    assert (("queue", thread_id), "pending_messages") in client.store.deleted
    status.assert_awaited_once_with("C123", "active")
