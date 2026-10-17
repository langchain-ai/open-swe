"""Transport-neutral run lifecycle with an injected Agent Server client."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from managed_deepagents import _ingress_runs as ingress_runs
from managed_deepagents._channels.types import ChannelError


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    for name in (
        "LANGCHAIN_API_KEY",
        "LANGSMITH_API_KEY",
        "MDA_INGRESS_SECRET",
        "MDA_HANDOFF_SECRET",
    ):
        monkeypatch.delenv(name, raising=False)
    return SimpleNamespace(
        threads=SimpleNamespace(create=AsyncMock()),
        runs=SimpleNamespace(
            create=AsyncMock(return_value={"run_id": "run-1"}),
            join=AsyncMock(return_value={"messages": []}),
            get=AsyncMock(return_value={"status": "success"}),
        ),
    )


@pytest.mark.parametrize("resume", [False, True])
@pytest.mark.parametrize("with_options", [False, True])
def test_injected_client_preserves_payload_and_options(
    client: SimpleNamespace, resume: bool, with_options: bool
) -> None:
    messages = [{"role": "user", "content": [{"type": "text", "text": "hello"}]}]
    command = {"interrupt-1": {"decisions": [{"type": "approve"}]}}
    context = {"provider": "custom", "event": {"kind": "message"}, "custom": True}
    config = {"configurable": {"custom": "value"}}
    options = {"context": context, "config": config} if with_options else {}
    if resume:
        pending = ingress_runs.resume_ingress_run(
            client=client, thread_id="thread-1", assistant_id="agent", resume=command, **options
        )
    else:
        pending = ingress_runs.start_ingress_run(
            client=client, thread_id="thread-1", assistant_id="agent", messages=messages, **options
        )

    assert asyncio.run(pending) == {"thread_id": "thread-1", "run_id": "run-1"}
    if resume:
        client.threads.create.assert_not_awaited()
    else:
        client.threads.create.assert_awaited_once_with(thread_id="thread-1", if_exists="do_nothing")
    payload = {"command": {"resume": command}} if resume else {"input": {"messages": messages}}
    client.runs.create.assert_awaited_once_with(
        "thread-1", "agent", **payload, **options, multitask_strategy="enqueue"
    )
    if with_options:
        assert client.runs.create.call_args.kwargs["context"] is context
        assert client.runs.create.call_args.kwargs["config"] is config


def test_thread_rejection_stops_run_creation(client: SimpleNamespace) -> None:
    client.threads.create.side_effect = RuntimeError("thread access denied")
    with pytest.raises(RuntimeError, match="thread access denied"):
        asyncio.run(
            ingress_runs.start_ingress_run(
                client=client, thread_id="thread-1", assistant_id="agent", messages=[]
            )
        )
    client.runs.create.assert_not_awaited()


@pytest.mark.parametrize("resume", [False, True])
@pytest.mark.parametrize("response", [{}, {"run_id": " "}, {"run_id": 5}, None])
def test_run_creation_requires_run_id(
    client: SimpleNamespace, resume: bool, response: object
) -> None:
    client.runs.create.return_value = response
    if resume:
        pending = ingress_runs.resume_ingress_run(
            client=client, thread_id="thread-1", assistant_id="agent", resume={}
        )
    else:
        pending = ingress_runs.start_ingress_run(
            client=client, thread_id="thread-1", assistant_id="agent", messages=[]
        )
    with pytest.raises(ChannelError, match="did not return run_id"):
        asyncio.run(pending)


@pytest.mark.parametrize("status", ["success", "error", "timeout", "interrupted"])
def test_observe_joins_before_reading_terminal_status(client: SimpleNamespace, status: str) -> None:
    values = {"messages": [{"role": "assistant", "content": "done"}]}

    async def get(_thread_id: str, _run_id: str) -> dict[str, str]:
        client.runs.join.assert_awaited_once_with("thread-1", "run-1")
        return {"status": status}

    client.runs.join.return_value = values
    client.runs.get.side_effect = get
    observed = asyncio.run(
        ingress_runs.observe_ingress_run(client=client, thread_id="thread-1", run_id="run-1")
    )
    assert observed == {"values": values, "status": status}
    assert observed["values"] is values
    client.runs.get.assert_awaited_once_with("thread-1", "run-1")


@pytest.mark.parametrize("response", [{"status": "pending"}, {}, None])
def test_observe_leaves_status_policy_to_transport(
    client: SimpleNamespace, response: object
) -> None:
    client.runs.get.return_value = response
    observed = asyncio.run(
        ingress_runs.observe_ingress_run(client=client, thread_id="thread-1", run_id="run-1")
    )
    assert observed["status"] == (response.get("status") if isinstance(response, dict) else None)
