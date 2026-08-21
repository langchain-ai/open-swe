import builtins
from typing import Any

import pytest

from agent.utils import slack_stop
from agent.utils.slack_stop import process_slack_stop_reaction


class FakeStore:
    def __init__(self) -> None:
        self.items: dict[tuple[tuple[str, ...], str], dict[str, Any]] = {}
        self.deleted: list[tuple[tuple[str, ...], str]] = []
        self.fail_delete = False

    async def get_item(self, namespace: tuple[str, ...], key: str) -> dict[str, Any] | None:
        return self.items.get((namespace, key))

    async def put_item(self, namespace: tuple[str, ...], key: str, value: dict[str, Any]) -> None:
        self.items[(namespace, key)] = {"value": value}

    async def delete_item(self, namespace: tuple[str, ...], key: str) -> None:
        if self.fail_delete:
            raise RuntimeError("store unavailable")
        self.deleted.append((namespace, key))
        self.items.pop((namespace, key), None)


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
        self.by_status: dict[str, list[dict[str, str]]] = {"pending": [], "running": []}
        self.cancelled: list[dict[str, Any]] = []
        self.fail_cancel = False

    async def list(
        self, thread_id: str, *, status: str, limit: int, offset: int
    ) -> list[dict[str, str]]:
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

    monkeypatch.setattr(slack_stop, "langgraph_client", lambda: client)
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
    client.store.items[(("queue", thread_id), "pending_messages")] = {
        "value": {"messages": [{"content": "later"}]}
    }
    client.store.items[(("autofix", thread_id), "pending_event")] = {"value": {"reason": "ci"}}
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
    assert (("queue", thread_id), "pending_messages") in client.store.deleted
    assert (("autofix", thread_id), "pending_event") in client.store.deleted
    assert client.threads.updates[0][1]["latest_run_status"] == "interrupted"
    assert len(dispatched) == 1
    assert dispatched[0]["thread_id"] == thread_id
    assert dispatched[0]["source"] == "slack"
    assert dispatched[0]["configurable"]["github_login"] == "owner"
    assert dispatched[0]["configurable"]["stop_summary"] is True
    assert dispatched[0]["configurable"]["slack_thread"]["triggering_user_id"] == "UOWNER"
    assert "first and only user-facing action" in dispatched[0]["content"]
    assert "active runs were interrupted" in dispatched[0]["content"]
    thread_mapping = client.store.items[(("slack_run_map", "C123"), "thread:1.000")]
    assert thread_mapping["value"]["run_id"] == "run-summary"


async def test_stop_reaction_on_root_dispatches_no_active_run_summary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = FakeClient()
    thread_id = _add_thread(client)
    dispatched, claimed = _patch_handler(monkeypatch, client)

    await process_slack_stop_reaction(_event("1.000"), event_id="EvRoot")

    assert claimed == ["EvRoot"]
    assert client.runs.cancelled == []
    assert dispatched[0]["thread_id"] == thread_id
    assert "No active run was present" in dispatched[0]["content"]


async def test_stop_reaction_from_non_owner_is_allowed(monkeypatch: pytest.MonkeyPatch) -> None:
    client = FakeClient()
    _add_thread(client)
    _map_reply(client, "2.000")
    client.runs.by_status["running"] = [{"run_id": "run-running"}]
    dispatched, _ = _patch_handler(monkeypatch, client)

    await process_slack_stop_reaction(_event("2.000", user_id="UNRELATED"), event_id="EvOtherUser")

    assert len(dispatched) == 1
    assert client.runs.cancelled[0]["run_ids"] == ["run-running"]


async def test_stop_reaction_ignores_unmapped_non_root_message(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = FakeClient()
    _add_thread(client)
    dispatched, claimed = _patch_handler(monkeypatch, client)

    await process_slack_stop_reaction(_event("9.000"), event_id="EvUnmapped")

    assert dispatched == []
    assert claimed == []
    assert client.runs.cancelled == []


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


async def test_stop_reaction_without_event_id_has_no_side_effects(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = FakeClient()
    _add_thread(client)
    _map_reply(client, "2.000")
    dispatched, claimed = _patch_handler(monkeypatch, client)

    await process_slack_stop_reaction(_event("2.000"))

    assert dispatched == []
    assert claimed == []
    assert client.runs.cancelled == []


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
    assert client.store.deleted == []


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
    assert client.store.deleted == []
    assert client.threads.updates == []


async def test_failed_queue_cleanup_does_not_dispatch_summary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = FakeClient()
    _add_thread(client)
    _map_reply(client, "2.000")
    client.store.fail_delete = True
    dispatched, _ = _patch_handler(monkeypatch, client)

    await process_slack_stop_reaction(_event("2.000"), event_id="EvStoreFailure")

    assert dispatched == []
    assert client.threads.updates == []
