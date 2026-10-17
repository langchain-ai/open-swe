"""Trusted channel loopback authentication tests."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any, get_type_hints
from unittest.mock import AsyncMock

import pytest

from managed_deepagents import _ingress
from managed_deepagents._channels.http import ingress as http_ingress_runtime
from managed_deepagents._channels.trigger import ingress as trigger_ingress
from managed_deepagents._channels.types import ChannelError


def test_trigger_loopback_client_factory_does_not_return_any() -> None:
    return_type = get_type_hints(trigger_ingress._create_trigger_ingress_loopback_client)["return"]

    assert return_type is not Any


CHANNEL_BINDING = {
    "name": "support",
    "provider": "slack",
    "source_thread_key": "source",
    "delivery_id": "delivery",
    "transport": "direct",
    "target": {},
    "event": {"type": "message", "messages": []},
}
CHANNEL_CONTEXT = {
    "name": "support",
    "provider": "slack",
    "sourceThreadKey": "source",
    "deliveryId": "delivery",
    "transport": "direct",
    "target": {},
    "event": {"type": "message", "messages": []},
}


class _Threads:
    def __init__(self, thread: object | None = None, error: Exception | None = None) -> None:
        self.calls: list[dict[str, object]] = []
        self._thread = thread
        self._error = error

    async def create(self, **kwargs: object) -> None:
        self.calls.append(kwargs)

    async def get(self, thread_id: str) -> object:
        self.calls.append({"get": thread_id})
        if self._error is not None:
            raise self._error
        return self._thread


class _Runs:
    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple[object, ...], dict[str, object]]] = []

    async def create(self, *args: object, **kwargs: object) -> dict[str, object]:
        self.calls.append(("create", args, kwargs))
        return {"run_id": "run-1"}

    async def join(self, *args: object, **kwargs: object) -> dict[str, object]:
        self.calls.append(("join", args, kwargs))
        return {"messages": [{"type": "ai", "content": "done"}]}

    async def get(self, *args: object, **kwargs: object) -> dict[str, object]:
        self.calls.append(("get", args, kwargs))
        return {"status": "success"}


class _Client:
    def __init__(self, thread: object | None = None, error: Exception | None = None) -> None:
        self.threads = _Threads(thread, error)
        self.runs = _Runs()


def _capture_client(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    captured: dict[str, Any] = {}

    def get_client(**kwargs: object) -> _Client:
        captured.update(kwargs)
        return _Client()

    monkeypatch.setattr(_ingress, "get_client", get_client)
    return captured


def test_server_api_key_loopback_omits_ingress_secret(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LANGSMITH_API_KEY", "server-key")
    monkeypatch.delenv("MDA_INGRESS_SECRET", raising=False)
    captured = _capture_client(monkeypatch)

    assert _ingress._has_loopback_credential()
    _ingress._create_ingress_loopback_client(
        user_id="slack-user-1", source_thread_key="slack:T1:C1:1"
    )

    assert captured["api_key"] == "server-key"
    assert captured["headers"] == {
        "x-mda-user-id": "slack-user-1",
        "x-mda-source-provider": "slack",
        "x-mda-source-thread-id": "slack:T1:C1:1",
    }


def test_ingress_secret_remains_a_loopback_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("LANGSMITH_API_KEY", raising=False)
    monkeypatch.setenv("MDA_INGRESS_SECRET", "legacy-ingress-secret")
    captured = _capture_client(monkeypatch)

    assert _ingress._has_loopback_credential()
    _ingress._create_ingress_loopback_client(
        user_id="slack-user-1", source_thread_key="slack:T1:C1:1"
    )

    assert captured["headers"]["x-mda-ingress-secret"] == "legacy-ingress-secret"


def test_deploy_owned_handoff_secret_does_not_require_server_api_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("LANGSMITH_API_KEY", raising=False)
    monkeypatch.setenv("MDA_HANDOFF_SECRET", "generated-handoff-secret")
    captured = _capture_client(monkeypatch)

    assert _ingress._has_loopback_credential()
    _ingress._create_ingress_loopback_client(
        user_id="slack-user-1", source_thread_key="slack:T1:C1:1"
    )

    assert captured["api_key"] == "local"
    assert captured["headers"]["x-mda-handoff-secret"] == "generated-handoff-secret"


def test_handoff_secret_is_not_sent_to_configured_remote_origin(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("LANGSMITH_API_KEY", raising=False)
    monkeypatch.setenv("MDA_HANDOFF_SECRET", "generated-handoff-secret")
    monkeypatch.setenv("MDA_CHANNEL_API_URL", "https://proxy.example")
    captured = _capture_client(monkeypatch)

    assert not _ingress._has_loopback_credential()
    _ingress._create_ingress_loopback_client(
        user_id="slack-user-1", source_thread_key="slack:T1:C1:1"
    )

    assert "x-mda-handoff-secret" not in captured["headers"]


def test_start_ingress_agent_enqueues_opaque_messages_with_langchain_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LANGCHAIN_API_KEY", "hosted-server-key")
    monkeypatch.setenv("LANGSMITH_API_KEY", "must-not-be-used")
    client = _Client()
    captured: dict[str, object] = {}

    def get_client(**kwargs: object) -> _Client:
        captured.update(kwargs)
        return client

    monkeypatch.setattr(trigger_ingress, "get_client", get_client)
    messages = [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "describe"},
                {"type": "image_url", "image_url": {"url": "data:image/png;base64,AA=="}},
            ],
        }
    ]

    assert asyncio.run(
        trigger_ingress.start_ingress_agent(
            binding=CHANNEL_BINDING,
            thread_id="thread-1",
            assistant_id="agent",
            messages=messages,
            source_provider="slack",
            source_thread_key="slack:v1:A1:T1:C1:171.1",
            user_id="11111111-1111-4111-8111-111111111111",
        )
    ) == {"thread_id": "thread-1", "run_id": "run-1"}

    assert captured == {
        "url": "http://127.0.0.1:8000",
        "api_key": "hosted-server-key",
        "headers": {
            "x-mda-user-id": "11111111-1111-4111-8111-111111111111",
            "x-mda-agent-auth-principal-id": "11111111-1111-4111-8111-111111111111",
            "x-mda-source-provider": "slack",
            "x-mda-source-thread-id": "slack:v1:A1:T1:C1:171.1",
        },
    }
    assert client.threads.calls == [
        {
            "thread_id": "thread-1",
            "if_exists": "do_nothing",
        }
    ]
    assert client.runs.calls == [
        (
            "create",
            ("thread-1", "agent"),
            {
                "input": {"messages": messages},
                "multitask_strategy": "enqueue",
                "context": {"mda_channel": CHANNEL_CONTEXT},
            },
        )
    ]
    assert client.runs.calls[0][2]["input"]["messages"] is messages  # type: ignore[index]


def test_start_ingress_agent_stamps_identity_context_principal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LANGCHAIN_API_KEY", "hosted-server-key")
    client = _Client()
    captured: dict[str, object] = {}

    def get_client(**kwargs: object) -> _Client:
        captured.update(kwargs)
        return client

    monkeypatch.setattr(trigger_ingress, "get_client", get_client)

    assert asyncio.run(
        trigger_ingress.start_ingress_agent(
            binding=CHANNEL_BINDING,
            thread_id="thread-1",
            assistant_id="agent",
            messages=[],
            source_provider="slack",
            source_thread_key="slack:v1:A1:T1:C1:171.1",
            user_id="11111111-1111-4111-8111-111111111111",
        )
    ) == {"thread_id": "thread-1", "run_id": "run-1"}

    assert captured["headers"] == {
        "x-mda-user-id": "11111111-1111-4111-8111-111111111111",
        "x-mda-agent-auth-principal-id": "11111111-1111-4111-8111-111111111111",
        "x-mda-source-provider": "slack",
        "x-mda-source-thread-id": "slack:v1:A1:T1:C1:171.1",
    }


@pytest.mark.parametrize("status", ["success", "error", "timeout", "interrupted"])
def test_observe_ingress_agent_joins_then_gets_terminal_status(
    monkeypatch: pytest.MonkeyPatch, status: str
) -> None:
    monkeypatch.setenv("LANGCHAIN_API_KEY", "hosted-server-key")
    client = _Client()
    client.runs.get = AsyncMock(return_value={"status": status})
    captured: dict[str, object] = {}

    def get_client(**kwargs: object) -> _Client:
        captured.update(kwargs)
        return client

    monkeypatch.setattr(trigger_ingress, "get_client", get_client)

    observed = asyncio.run(
        trigger_ingress.observe_ingress_agent(
            thread_id="thread-1",
            run_id="run-1",
            source_provider="slack",
            source_thread_key="slack:v1:A1:T1:C1:171.1",
            user_id="11111111-1111-4111-8111-111111111111",
        )
    )

    assert observed == {
        "values": {"messages": [{"type": "ai", "content": "done"}]},
        "status": status,
    }
    assert captured == {
        "url": "http://127.0.0.1:8000",
        "api_key": "hosted-server-key",
        "headers": {
            "x-mda-user-id": "11111111-1111-4111-8111-111111111111",
            "x-mda-agent-auth-principal-id": "11111111-1111-4111-8111-111111111111",
            "x-mda-source-provider": "slack",
            "x-mda-source-thread-id": "slack:v1:A1:T1:C1:171.1",
        },
    }
    assert client.runs.calls == [
        ("join", ("thread-1", "run-1"), {}),
    ]
    client.runs.get.assert_awaited_once_with("thread-1", "run-1")


def test_resume_ingress_agent_sends_keyed_interrupt_command(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LANGCHAIN_API_KEY", "hosted-server-key")
    client = _Client()

    monkeypatch.setattr(trigger_ingress, "get_client", lambda **_kwargs: client)
    resume = {
        "interrupt-1": {
            "decisions": [{"type": "approve"}],
        }
    }

    assert asyncio.run(
        trigger_ingress.resume_ingress_agent(
            binding=CHANNEL_BINDING,
            thread_id="thread-1",
            assistant_id="agent",
            resume=resume,
            source_provider="slack",
            source_thread_key="slack:v1:A1:T1:C1:171.1",
            user_id="11111111-1111-4111-8111-111111111111",
        )
    ) == {"thread_id": "thread-1", "run_id": "run-1"}
    assert client.threads.calls == []
    assert client.runs.calls == [
        (
            "create",
            ("thread-1", "agent"),
            {
                "command": {"resume": resume},
                "multitask_strategy": "enqueue",
                "context": {"mda_channel": CHANNEL_CONTEXT},
            },
        )
    ]


@pytest.fixture
def http_ingress(monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    monkeypatch.setenv("MDA_HANDOFF_SECRET", "generated-handoff-secret")
    client = _Client()
    captured = {}

    def get_client(**kwargs: object) -> _Client:
        captured.update(kwargs)
        return client

    monkeypatch.setattr(_ingress, "get_client", get_client)
    args = {
        "user_id": "user-1",
        "source_thread_key": "http:thread-1",
        "source_provider": "photon",
        "thread_id": "11111111-1111-4111-8111-111111111111",
        "assistant_id": "agent",
        "binding": {
            "name": "orders",
            "provider": "photon",
            "delivery_id": "delivery-1",
            "source_thread_key": "http:thread-1",
            "transport": "direct",
            "target": {},
            "event": {"type": "message", "messages": []},
        },
        "input": [{"type": "image", "mimeType": "image/png", "data": "abc"}],
    }
    return SimpleNamespace(client=client, captured=captured, args=args)


@pytest.mark.parametrize("event", [None, False, "event", ["one", "two"], {"order": "one"}])
def test_http_run_acceptance_preserves_content_and_identity(
    http_ingress: SimpleNamespace, event: object
) -> None:
    args = {**http_ingress.args, "binding": {**http_ingress.args["binding"], "raw_event": event}}
    started = asyncio.run(http_ingress_runtime.start_http_ingress_agent(**args))
    assert started == {"thread_id": args["thread_id"], "run_id": "run-1"}
    headers = http_ingress.captured["headers"]
    assert headers["x-mda-source-provider"] == "photon"
    assert headers["x-mda-user-id"] == "user-1"
    assert len(http_ingress.client.runs.calls) == 1
    method, positional, payload = http_ingress.client.runs.calls[0]
    assert (method, positional) == ("create", (args["thread_id"], "agent"))
    assert payload == {
        "input": {
            "messages": [
                {
                    "role": "human",
                    "content": args["input"],
                    "response_metadata": {"output_version": "v1"},
                }
            ]
        },
        "context": {
            "mda_channel": {
                "name": "orders",
                "provider": "photon",
                "deliveryId": "delivery-1",
                "sourceThreadKey": "http:thread-1",
                "transport": "direct",
                "target": {},
                "event": {"type": "message", "messages": []},
                "rawEvent": event,
            }
        },
        "multitask_strategy": "enqueue",
    }


@pytest.mark.parametrize("status", ["success", "error", "timeout", "interrupted", "pending", None])
def test_http_run_observation_checks_status(http_ingress: SimpleNamespace, status: object) -> None:
    http_ingress.client.runs.get = AsyncMock(return_value={"status": status})
    pending = http_ingress_runtime.observe_http_ingress_agent(
        user_id="user-1",
        source_thread_key="http:thread-1",
        source_provider="photon",
        thread_id="thread-1",
        run_id="run-1",
    )
    if status == "success":
        assert asyncio.run(pending)["reply_content"] == "done"
    elif status == "interrupted":
        assert asyncio.run(pending)["reply_content"] is None
    else:
        with pytest.raises(ChannelError, match="did not complete successfully"):
            asyncio.run(pending)


@pytest.mark.parametrize("resume", [False, True])
def test_ingress_forwards_run_context(monkeypatch: pytest.MonkeyPatch, resume: bool) -> None:
    client = _Client()
    monkeypatch.setattr(
        trigger_ingress, "_create_trigger_ingress_loopback_client", lambda scope: client
    )
    context = {"provider": "slack", "raw_event": {"channel_type": "im"}, "custom": True}
    args = {
        "thread_id": "thread",
        "assistant_id": "agent",
        "source_provider": "slack",
        "source_thread_key": "source",
        "user_id": "alice",
        "context": context,
        "binding": CHANNEL_BINDING,
    }
    call = trigger_ingress.resume_ingress_agent if resume else trigger_ingress.start_ingress_agent
    args.update({"resume": {"interrupt": "yes"}} if resume else {"messages": []})
    asyncio.run(call(**args))
    assert client.runs.calls[0][2]["context"] == {**context, "mda_channel": CHANNEL_CONTEXT}


def test_http_does_not_run_when_thread_access_is_rejected(http_ingress: SimpleNamespace) -> None:
    http_ingress.client.threads.create = AsyncMock(side_effect=RuntimeError("thread access denied"))
    with pytest.raises(RuntimeError, match="thread access denied"):
        asyncio.run(http_ingress_runtime.start_http_ingress_agent(**http_ingress.args))
    assert not http_ingress.client.runs.calls


@pytest.mark.parametrize("status", ["pending", None, []])
def test_trigger_observation_rejects_unknown_status(
    monkeypatch: pytest.MonkeyPatch, status: object
) -> None:
    client = _Client()
    client.runs.get = AsyncMock(return_value={"status": status})
    monkeypatch.setattr(
        trigger_ingress, "_create_trigger_ingress_loopback_client", lambda _source: client
    )
    with pytest.raises(ChannelError, match="unknown run status"):
        asyncio.run(
            trigger_ingress.observe_ingress_agent(
                thread_id="thread-1",
                run_id="run-1",
                source_provider="slack",
                source_thread_key="source",
                user_id="alice",
            )
        )


@pytest.mark.parametrize("interrupts", [[{"id": "interrupt-1"}], {"id": "interrupt-1"}])
def test_http_observation_suppresses_interrupted_reply(
    http_ingress: SimpleNamespace, interrupts: object
) -> None:
    http_ingress.client.runs.join = AsyncMock(
        return_value={
            "messages": [{"type": "ai", "content": "do not send"}],
            "__interrupt__": interrupts,
        }
    )
    observed = asyncio.run(
        http_ingress_runtime.observe_http_ingress_agent(
            user_id="user-1",
            source_thread_key="http:thread-1",
            source_provider="photon",
            thread_id="thread-1",
            run_id="run-1",
        )
    )
    assert observed == {"thread_id": "thread-1", "run_id": "run-1", "reply_content": None}


def _thread_interrupts(monkeypatch: pytest.MonkeyPatch, error: Exception) -> list[object]:
    monkeypatch.setattr(
        trigger_ingress,
        "_create_trigger_ingress_loopback_client",
        lambda _scope: _Client(error=error),
    )
    return asyncio.run(
        trigger_ingress.ingress_thread_interrupts(
            thread_id="thread-1",
            source_provider="slack",
            source_thread_key="slack:v1:A1:T1:C1:171.1",
            user_id="11111111-1111-4111-8111-111111111111",
        )
    )


def _status_error(status: int, *, nested: bool) -> Exception:
    error = Exception("thread lookup failed")
    if nested:
        error.response = SimpleNamespace(status_code=status)  # type: ignore[attr-defined]
    else:
        error.status_code = status  # type: ignore[attr-defined]
    return error


@pytest.mark.parametrize("nested", [True, False])
def test_thread_interrupts_treats_a_missing_thread_as_nothing_pending(
    monkeypatch: pytest.MonkeyPatch, nested: bool
) -> None:
    assert _thread_interrupts(monkeypatch, _status_error(404, nested=nested)) == []


@pytest.mark.parametrize(
    "error",
    [_status_error(500, nested=True), Exception("network timeout")],
    ids=["server_error", "no_status"],
)
def test_thread_interrupts_propagates_other_lookup_failures(
    monkeypatch: pytest.MonkeyPatch, error: Exception
) -> None:
    # Reporting nothing pending would start a fresh turn and abandon the
    # checkpoint the answer was meant to resume.
    with pytest.raises(Exception) as raised:  # noqa: PT011
        _thread_interrupts(monkeypatch, error)
    assert raised.value is error
