import importlib
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock
from uuid import uuid7
from xml.etree import ElementTree

import pytest

from openswe import thread_feedback
from openswe.slack import thinking as slack_thinking
from openswe.users import User

dispatch = importlib.import_module("openswe.dispatch")

_ABSOLUTE = "https://open-swe-v3-abc.us.langgraph.app/webhooks/run-complete"


class _FakeRuns:
    def __init__(self) -> None:
        self.created: list[dict[str, Any]] = []
        self.fail_next = False

    async def create(self, thread_id: str, assistant_id: str, **kwargs: Any) -> dict[str, str]:
        self.created.append({"thread_id": thread_id, "assistant_id": assistant_id, **kwargs})
        if self.fail_next:
            self.fail_next = False
            raise RuntimeError("dispatch failed")
        return {"run_id": "run-1"}


class _FakeThreads:
    def __init__(self) -> None:
        self.metadata: dict[str, Any] = {}
        self.messages: list[dict[str, Any]] = []

    async def get(self, thread_id: str) -> dict[str, Any]:
        return {"thread_id": thread_id, "metadata": self.metadata}

    async def get_state(self, thread_id: str) -> dict[str, Any]:
        return {"values": {"messages": self.messages}}

    async def update(self, *, thread_id: str, metadata: dict[str, Any]) -> None:
        self.metadata = metadata


class _FakeClient:
    def __init__(self) -> None:
        self.runs = _FakeRuns()
        self.threads = _FakeThreads()


@pytest.mark.asyncio
async def test_create_durable_run_applies_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _FakeClient()
    monkeypatch.setattr(dispatch, "COMPLETION_WEBHOOK_URL", "https://app/webhooks/run-complete")

    run = await dispatch.create_durable_run(
        "thread-1",
        "agent",
        input={"messages": [{"role": "user", "content": "hi"}]},
        source="test",
        thread_title=None,
        config={
            "configurable": {
                "thread_id": "thread-1",
                "slack_thread": {
                    "channel_id": "C123",
                    "channel_context": {"name": "team-openswe"},
                },
            },
            "metadata": {"kind": "test"},
        },
        client=client,
    )

    assert run == {"run_id": "run-1"}
    created = client.runs.created[0]
    assert created["durability"] == "sync"
    assert created["multitask_strategy"] == "interrupt"
    assert created["if_not_exists"] == "create"
    assert created["webhook"] == "https://app/webhooks/run-complete"
    # Resumable by default so the dashboard can join (and stop) a run it did not start.
    assert created["stream_resumable"] is True
    # The v3 run shape, so the dashboard gets `tools` events and subagent
    # namespaces from runs it did not start — exactly what its own `run.start` sends.
    assert created["stream_mode"] == [
        "values",
        "updates",
        "messages",
        "custom",
        "tasks",
        "checkpoints",
    ]
    assert created["stream_subgraphs"] is True
    assert created["config"]["configurable"]["__event_streaming_v2"] is True
    invocation_id = created["config"]["configurable"]["invocation_id"]
    assert created["config"]["metadata"] == {
        "kind": "test",
        "slack_channel_id": "C123",
        "slack_channel_name": "team-openswe",
        "invocation_id": invocation_id,
        "prepare_run_id": invocation_id,
        "invocation_started_at": created["config"]["configurable"]["invocation_started_at"],
    }
    assert created["metadata"] == created["config"]["metadata"]
    assert created["config"]["configurable"]["thread_id"] == "thread-1"
    assert created["config"]["configurable"]["prepare_run_id"] == invocation_id
    assert isinstance(invocation_id, str)


async def test_run_metadata_uses_users_id_across_slack_and_github(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user_id = uuid7()

    async def by_identity(provider: str, external_id: str) -> SimpleNamespace | None:
        if (provider, external_id) in {("slack", "U123"), ("github", "2001")}:
            return SimpleNamespace(id=user_id)
        return None

    async def by_login(provider: str, login: str) -> SimpleNamespace | None:
        return SimpleNamespace(id=user_id) if (provider, login) == ("github", "mason-gh") else None

    monkeypatch.setattr(User, "for_identity", by_identity)
    monkeypatch.setattr(User, "for_login", by_login)
    client = _FakeClient()

    for source, configurable in (
        ("slack", {"slack_thread": {"triggering_user_id": "U123"}}),
        ("github", {"github_user_id": "2001", "github_login": "mason-gh"}),
        ("dashboard", {"github_login": "mason-gh"}),
    ):
        await dispatch.create_durable_run(
            "thread-1",
            "agent",
            input={"messages": []},
            source=source,
            thread_title=None,
            config={"configurable": configurable},
            client=client,
        )
        created = client.runs.created[-1]
        assert created["metadata"]["user_id"] == str(user_id)
        assert created["config"]["metadata"]["user_id"] == str(user_id)

    await dispatch.create_durable_run(
        "thread-1",
        "agent",
        input={"messages": []},
        source="slack",
        thread_title=None,
        config={"configurable": {"slack_thread": {"triggering_user_id": "unknown"}}},
        metadata={"user_id": str(user_id)},
        client=client,
    )
    assert "user_id" not in client.runs.created[-1]["metadata"]

    await dispatch.create_durable_run(
        "thread-1",
        "agent",
        input={"messages": []},
        source="slack",
        thread_title=None,
        config={
            "configurable": {
                "background_task_completion": True,
                "github_login": "mason-gh",
                "slack_thread": {"triggering_user_id": "U123"},
            },
            "metadata": {"user_id": str(user_id)},
        },
        client=client,
    )
    completion = client.runs.created[-1]
    assert "user_id" not in completion["metadata"]
    assert "user_id" not in completion["config"]["configurable"]


@pytest.mark.asyncio
async def test_create_durable_run_preserves_existing_prepare_id_and_resumable_opt_out(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _FakeClient()
    monkeypatch.setattr(dispatch, "COMPLETION_WEBHOOK_URL", None)

    await dispatch.create_durable_run(
        "thread-1",
        "agent",
        input={"messages": []},
        source="schedule",
        thread_title=None,
        config={"configurable": {"prepare_run_id": "existing"}},
        stream_resumable=False,
        client=client,
    )

    created = client.runs.created[0]
    assert "webhook" not in created
    assert created["stream_resumable"] is False
    assert created["config"]["configurable"]["invocation_id"] == "existing"
    assert created["config"]["configurable"]["prepare_run_id"] == "existing"
    assert created["config"]["configurable"]["__event_streaming_v2"] is True


def test_prepare_run_config_rejects_conflicting_invocation_ids() -> None:
    with pytest.raises(ValueError, match="conflicts"):
        dispatch.prepare_run_config(
            {
                "configurable": {
                    "invocation_id": "invocation-1",
                    "prepare_run_id": "invocation-2",
                }
            },
            None,
        )


@pytest.mark.asyncio
async def test_dashboard_followup_records_activity_even_if_dispatch_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _FakeClient()
    client.runs.fail_next = True
    monkeypatch.setattr(thread_feedback, "now_ms", lambda: 123000)

    with pytest.raises(RuntimeError, match="dispatch failed"):
        await dispatch.dispatch_agent_run(
            "thread-1",
            "Please revise the plan.",
            {},
            source="dashboard",
            thread_title=None,
            client=client,
        )

    assert client.threads.metadata[thread_feedback.ACTIVITY_KEY] == 123000


@pytest.mark.usefixtures("registry_db")
async def test_dispatch_keys_a_linked_slack_sender_on_their_person(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ALLOWED_GITHUB_USERS", "mason-gh")
    user = await User.sign_in("github", "2001", login="mason-gh")
    await user.link("slack", "U123", team_id="T1")

    run_input = await dispatch._dispatch_input(
        "hello",
        "slack",
        {"slack_thread": {"triggering_user_id": "U123", "channel_id": "C123"}},
    )

    envelope = ElementTree.fromstring(run_input["messages"][-1]["content"])
    assert envelope.attrib["sender"] == f"user:{user.id}"


async def test_dispatch_uses_moved_slack_destination_from_metadata(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = AsyncMock()
    client.runs.create.return_value = {"run_id": "run-1"}
    client.runs.list.return_value = [{"run_id": "run-1"}]
    client.threads.get.return_value = {
        "metadata": {"source_context": {"slack_thread": {"channel_id": "C2", "thread_ts": "2.0"}}}
    }
    set_status = AsyncMock(return_value=True)
    monkeypatch.setattr(slack_thinking, "set_slack_thread_status", set_status)
    await dispatch.create_durable_run(
        "thread-1",
        "agent",
        input={"messages": []},
        source="slack",
        thread_title=None,
        client=client,
        config={"configurable": {"slack_thread": {"channel_id": "C1", "thread_ts": "1.0"}}},
    )
    client.threads.get.assert_awaited_once_with("thread-1")
    set_status.assert_awaited_once_with("C2", "2.0", "Thinking...")


async def test_dispatch_reads_task_state_if_run_finishes_before_status_sync(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = AsyncMock()
    client.runs.create.return_value = {"run_id": "run-1"}
    client.runs.list.return_value = []
    client.threads.get.return_value = {
        "metadata": {
            "running_background_tasks": ["cmd-1"],
            "source_context": {"slack_thread": {"channel_id": "C1", "thread_ts": "1.0"}},
        }
    }
    set_status = AsyncMock()
    monkeypatch.setattr(slack_thinking, "set_slack_thread_status", set_status)
    await dispatch.create_durable_run(
        "thread-1",
        "agent",
        input={"messages": []},
        source="slack",
        thread_title=None,
        client=client,
        config={"configurable": {"slack_thread": {"channel_id": "C1", "thread_ts": "1.0"}}},
    )
    client.threads.get.assert_awaited_once_with("thread-1")
    set_status.assert_awaited_once_with("C1", "1.0", "Waiting for background tasks…")
