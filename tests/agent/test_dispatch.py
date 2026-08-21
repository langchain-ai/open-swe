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


def _client() -> FakeLangGraphClient:
    return FakeLangGraphClient(thread_metadata={})


@pytest.mark.asyncio
async def test_create_durable_run_applies_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _client()
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
    prepare_run_id = created["config"]["configurable"]["prepare_run_id"]
    assert created["config"]["metadata"] == {
        "kind": "test",
        "prepare_run_id": prepare_run_id,
    }
    assert created["metadata"] == created["config"]["metadata"]
    assert created["config"]["configurable"]["thread_id"] == "thread-1"
    assert isinstance(prepare_run_id, str)


@pytest.mark.asyncio
async def test_create_durable_run_preserves_existing_prepare_id_and_stream_kwargs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _client()
    monkeypatch.setattr(dispatch, "COMPLETION_WEBHOOK_URL", None)

    await dispatch.create_durable_run(
        "thread-1",
        "agent",
        input={"messages": []},
        source="schedule",
        config={"configurable": {"prepare_run_id": "existing"}},
        stream_mode=["values"],
        stream_resumable=False,
        client=client,
    )

    created = client.runs.created[0]
    assert "webhook" not in created
    assert created["stream_mode"] == ["values"]
    assert created["stream_resumable"] is False
    assert created["config"]["configurable"]["prepare_run_id"] == "existing"


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
