import builtins
from typing import Any
from unittest.mock import AsyncMock

import pytest

from openswe.slack import client as slack_client
from openswe.slack import http as slack_http
from openswe.slack import stop as slack_stop
from openswe.slack import thinking
from openswe.slack.records import bind_slack_thread_id
from openswe.threads import handlers
from openswe.users.models import User, UserIdentity

pytestmark = pytest.mark.usefixtures("registry_db")


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
        self.threads = FakeThreads()
        self.runs = FakeRuns()
        self.cleared_queues: list[str] = []


async def _add_thread(client: FakeClient, thread_ts: str = "1.000") -> str:
    thread_id = f"thread-{thread_ts}"
    await bind_slack_thread_id("C123", thread_ts, thread_id)
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


def _patch_handler(monkeypatch: pytest.MonkeyPatch, client: FakeClient) -> None:
    class FakeQueuedMessage:
        @staticmethod
        async def clear(thread_id: str) -> None:
            client.cleared_queues.append(thread_id)

    monkeypatch.setattr(slack_stop, "get_client", lambda url: client)
    monkeypatch.setattr(slack_stop, "QueuedMessage", FakeQueuedMessage)
    monkeypatch.setattr(slack_stop, "claim_slack_event", AsyncMock(return_value=True))


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


@pytest.mark.parametrize("has_follow_up", [False, True])
async def test_native_stop_targets_ordinary_thread_preserves_queued_work_and_deduplicates(
    monkeypatch: pytest.MonkeyPatch, has_follow_up: bool
) -> None:
    client = FakeClient()
    thread_id = await _add_thread(client)
    code_thread_id = await _add_thread(client, "0")
    client.runs.by_status["running"] = [{"run_id": "running"}]
    if has_follow_up:
        client.runs.by_status["pending"] = [
            {"run_id": "other-follow-up", "metadata": {"queued_by": "other"}}
        ]
    status = _patch_native_stop(monkeypatch, client)
    monkeypatch.setattr(slack_stop, "claim_slack_event", AsyncMock(side_effect=[True, False]))
    event: dict[str, object] = {"channel": "C123", "thread_ts": "1.000", "user": "UOWNER"}

    await slack_stop.process_agent_session_stopped(event, "EvStop", "T123")
    await slack_stop.process_agent_session_stopped(event, "EvStop", "T123")

    assert client.runs.cancelled == [
        {"thread_id": thread_id, "run_ids": ["running"], "action": "interrupt"}
    ]
    assert client.cleared_queues == []
    assert "latest_run_status" not in client.threads.values[code_thread_id]["metadata"]
    status.assert_awaited_once_with("C123", "1.000", "")
    slack_client.post_slack_thread_reply.assert_not_awaited()


async def test_native_stop_reports_cancellation_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    client = FakeClient()
    thread_id = await _add_thread(client)
    client.runs.by_status["running"] = [{"run_id": "running"}]
    client.runs.fail_cancel = True
    status = _patch_native_stop(monkeypatch, client)

    await slack_stop.process_agent_session_stopped(
        {"channel": "C123", "thread_ts": "1.000", "user": "UOWNER"}, "EvStop", "T123"
    )

    assert client.threads.updates == []
    status.assert_not_awaited()
    slack_client.post_slack_thread_reply.assert_awaited_once_with(
        "C123",
        "1.000",
        "I couldn't stop this thread. Please try Stop again.",
        agent_thread_id=thread_id,
    )


@pytest.mark.parametrize("rejection", ["workspace", "moved", "unlinked", "private"])
async def test_native_stop_rejects_wrong_location_or_unauthorized_user(
    monkeypatch: pytest.MonkeyPatch, rejection: str
) -> None:
    client = FakeClient()
    thread_id = await _add_thread(client)
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
    assert client.cleared_queues == []
    status.assert_not_awaited()
    slack_client.post_slack_thread_reply.assert_not_awaited()


@pytest.mark.parametrize("thread_ts", ["0", None])
async def test_native_stop_preserves_code_channel_routing(
    monkeypatch: pytest.MonkeyPatch, thread_ts: str | None
) -> None:
    client = FakeClient()
    thread_id = await _add_thread(client, "0")
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
    assert client.cleared_queues == [thread_id]
    status.assert_awaited_once_with("C123", "active")
