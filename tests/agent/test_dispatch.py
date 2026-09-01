import importlib
from typing import Any

import pytest

dispatch = importlib.import_module("agent.dispatch")

_ABSOLUTE = "https://open-swe-v3-abc.us.langgraph.app/webhooks/run-complete"


def test_is_loopback_webhook_relative() -> None:
    assert dispatch._is_loopback_webhook("/webhooks/run-complete") is True


def test_is_loopback_webhook_localhost() -> None:
    assert dispatch._is_loopback_webhook("http://localhost:2024/webhooks/run-complete") is True
    assert dispatch._is_loopback_webhook("http://127.0.0.1:8000/webhooks/run-complete") is True


def test_is_loopback_webhook_absolute() -> None:
    assert dispatch._is_loopback_webhook(_ABSOLUTE) is False


def test_resolve_no_secret_attaches_nothing() -> None:
    assert dispatch._resolve_completion_webhook_url(_ABSOLUTE, None) is None
    assert dispatch._resolve_completion_webhook_url(_ABSOLUTE, "") is None


def test_resolve_relative_url_degrades_to_none() -> None:
    # Secret set but a loopback URL would 422 every run — attach nothing instead.
    assert dispatch._resolve_completion_webhook_url("/webhooks/run-complete", "s3cret") is None


def test_resolve_localhost_url_degrades_to_none() -> None:
    assert dispatch._resolve_completion_webhook_url("http://localhost/x", "s3cret") is None


def test_resolve_absolute_url_appends_token() -> None:
    assert (
        dispatch._resolve_completion_webhook_url(_ABSOLUTE, "s3cret") == f"{_ABSOLUTE}?token=s3cret"
    )


def test_resolve_absolute_url_with_existing_query_left_as_is() -> None:
    url = f"{_ABSOLUTE}?token=preset"
    assert dispatch._resolve_completion_webhook_url(url, "s3cret") == url


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
        config={"configurable": {"thread_id": "thread-1"}, "metadata": {"kind": "test"}},
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
    # The Protocol v2 run shape, so the dashboard gets `tools` events and subagent
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
    prepare_run_id = created["config"]["configurable"]["prepare_run_id"]
    assert created["config"]["metadata"] == {
        "kind": "test",
        "prepare_run_id": prepare_run_id,
    }
    assert created["metadata"] == created["config"]["metadata"]
    assert created["config"]["configurable"]["thread_id"] == "thread-1"
    assert isinstance(prepare_run_id, str)


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
        config={"configurable": {"prepare_run_id": "existing"}},
        stream_resumable=False,
        client=client,
    )

    created = client.runs.created[0]
    assert "webhook" not in created
    assert created["stream_resumable"] is False
    assert created["config"]["configurable"]["prepare_run_id"] == "existing"
    assert created["config"]["configurable"]["__event_streaming_v2"] is True


def test_prepare_run_config_marks_every_run_as_protocol_v2() -> None:
    # The marker is fixed per run by the server: a caller cannot opt a run out of
    # v2 by passing its own `configurable`, or the dashboard silently loses `tools`.
    run_config = dispatch.prepare_run_config(
        {"configurable": {"__event_streaming_v2": False, "thread_id": "t"}}, None
    )

    assert run_config["configurable"]["__event_streaming_v2"] is True
    assert run_config["configurable"]["thread_id"] == "t"


@pytest.mark.asyncio
async def test_dispatch_requires_prebuilt_input() -> None:
    client = _FakeClient()

    with pytest.raises(TypeError):
        await dispatch.dispatch_agent_run(  # type: ignore[call-overload]
            "thread-1",
            "synthetic event",
            {},
            source="slack",
            client=client,
        )

    assert client.runs.created == []


@pytest.mark.asyncio
async def test_dispatch_accepts_prebuilt_input() -> None:
    client = _FakeClient()
    run_input = {"messages": [{"role": "user", "content": "structured"}]}

    await dispatch.dispatch_agent_run(
        "thread-1",
        configurable={},
        source="github",
        input=run_input,
        client=client,
    )

    assert client.runs.created[0]["input"] == run_input
