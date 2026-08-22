import importlib
from xml.etree import ElementTree

import pytest
from support.langgraph_fakes import FakeLangGraphClient

dispatch = importlib.import_module("agent.dispatch")

_ABSOLUTE = "https://open-swe-v3-abc.us.langgraph.app/webhooks/run-complete"


def test_is_loopback_webhook_relative() -> None:
    assert dispatch._is_loopback_webhook("/webhooks/run-complete") is True


def test_is_loopback_webhook_localhost() -> None:
    assert dispatch._is_loopback_webhook("http://localhost:2024/webhooks/run-complete") is True
    assert dispatch._is_loopback_webhook("http://127.0.0.1:8000/webhooks/run-complete") is True


def test_is_loopback_webhook_absolute() -> None:
    assert dispatch._is_loopback_webhook(_ABSOLUTE) is False


def test_resolve_no_secret_attaches_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("COMPLETION_WEBHOOK_URL", _ABSOLUTE)
    monkeypatch.delenv("RUN_COMPLETE_WEBHOOK_SECRET", raising=False)
    assert dispatch.completion_webhook_url() is None
    monkeypatch.setenv("RUN_COMPLETE_WEBHOOK_SECRET", "")
    assert dispatch.completion_webhook_url() is None


def test_resolve_relative_url_degrades_to_none(monkeypatch: pytest.MonkeyPatch) -> None:
    # Secret set but a loopback URL would 422 every run — attach nothing instead.
    monkeypatch.delenv("COMPLETION_WEBHOOK_URL", raising=False)
    monkeypatch.setenv("RUN_COMPLETE_WEBHOOK_SECRET", "s3cret")
    assert dispatch.completion_webhook_url() is None


def test_resolve_localhost_url_degrades_to_none(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("COMPLETION_WEBHOOK_URL", "http://localhost/x")
    monkeypatch.setenv("RUN_COMPLETE_WEBHOOK_SECRET", "s3cret")
    assert dispatch.completion_webhook_url() is None


def test_resolve_absolute_url_appends_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("COMPLETION_WEBHOOK_URL", _ABSOLUTE)
    monkeypatch.setenv("RUN_COMPLETE_WEBHOOK_SECRET", "s3cret")
    assert dispatch.completion_webhook_url() == f"{_ABSOLUTE}?token=s3cret"


def test_resolve_absolute_url_with_existing_query_left_as_is(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    url = f"{_ABSOLUTE}?token=preset"
    monkeypatch.setenv("COMPLETION_WEBHOOK_URL", url)
    monkeypatch.setenv("RUN_COMPLETE_WEBHOOK_SECRET", "s3cret")
    assert dispatch.completion_webhook_url() == url


def _client() -> FakeLangGraphClient:
    return FakeLangGraphClient(thread_metadata={})


@pytest.mark.asyncio
async def test_create_durable_run_applies_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _client()
    monkeypatch.setenv("COMPLETION_WEBHOOK_URL", "https://app/webhooks/run-complete")
    monkeypatch.setenv("RUN_COMPLETE_WEBHOOK_SECRET", "s3cret")

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
    assert created["webhook"] == "https://app/webhooks/run-complete?token=s3cret"
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
    client = _client()
    monkeypatch.delenv("RUN_COMPLETE_WEBHOOK_SECRET", raising=False)

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
async def test_dispatch_accepts_prebuilt_input(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _client()
    run_input = {"messages": [{"role": "user", "content": "structured"}]}

    await dispatch.dispatch_agent_run(
        "thread-1",
        None,
        {},
        source="github",
        input=run_input,
        client=client,
    )

    assert client.runs.created[0]["input"] == run_input


def test_dispatch_slack_identity_includes_verified_context() -> None:
    run_input = dispatch._dispatch_input(
        "hello",
        "slack",
        {
            "github_login": "mason-gh",
            "user_email": "mason@example.com",
            "slack_thread": {
                "triggering_user_id": "U123",
                "triggering_user_name": "Mason",
                "triggering_user_timezone": "America/New_York",
                "channel_id": "C123",
                "thread_ts": "123.45",
                "channel_context": {
                    "name": "eng",
                    "topic": "Ship <safely>",
                    "purpose": "Engineering work",
                },
            },
        },
    )

    person = ElementTree.fromstring(run_input["messages"][0]["content"])
    channel = ElementTree.fromstring(run_input["messages"][1]["content"])
    assert person.findtext("display_name") == "Mason"
    assert person.findtext("timezone") == "America/New_York"
    assert channel.findtext("name") == "eng"
    assert channel.findtext("topic") == "Ship <safely>"
    topic = channel.find("topic")
    assert topic is not None
    assert topic.attrib["trust"] == "untrusted"
    assert channel.findtext("purpose") == "Engineering work"
