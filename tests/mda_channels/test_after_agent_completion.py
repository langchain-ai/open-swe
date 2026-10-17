"""An MDA channel's run has no run-complete webhook: its after_agent hook completes it."""

from typing import Any, cast
from unittest.mock import AsyncMock

import pytest
from langchain_core.messages import AIMessage, BaseMessage, messages_to_dict
from pydantic import JsonValue

from openswe import completion
from openswe.mda_channels import completion as channel_completion
from openswe.remote_runtime import graph
from openswe.remote_runtime.tokens import RemoteRun
from openswe.threads import index

pytestmark = pytest.mark.usefixtures("registry_db")

_RUN = RemoteRun(
    thread_id="00000000-0000-4000-8000-0000000000c1",
    assistant_id="agent",
    configurable={
        "invocation_id": "inv-1",
        "prepare_run_id": "inv-1",
        "invocation_started_at": "2026-10-16T00:00:00+00:00",
        "repo": {"owner": "acme", "name": "widgets"},
    },
)


def _messages() -> list[dict[str, JsonValue]]:
    return cast(
        list[dict[str, JsonValue]], messages_to_dict([AIMessage(content="Done.", id="ai-1")])
    )


@pytest.fixture
def completed(monkeypatch: pytest.MonkeyPatch) -> list[tuple[dict[str, Any], object]]:
    calls: list[tuple[dict[str, Any], object]] = []

    async def complete(payload: dict[str, Any], *, thread_status: object = None) -> dict[str, str]:
        calls.append((payload, thread_status))
        return {"status": "ok"}

    monkeypatch.setattr(completion, "complete_run_once", complete)
    return calls


async def test_a_channel_run_is_completed_when_the_agent_is_done(
    completed: list[tuple[dict[str, Any], object]],
) -> None:
    await index.create(_RUN.thread_id, runtime="mda", status="busy", metadata={"channel": "linear"})

    await channel_completion.complete_after_agent(_RUN, "run-1", _messages())

    [(payload, thread_status)] = completed
    # MDA still reports the run running while its after_agent hook answers.
    assert thread_status == "idle"
    assert {key: payload[key] for key in ("thread_id", "run_id", "status", "metadata")} == {
        "thread_id": _RUN.thread_id,
        "run_id": "run-1",
        "status": "success",
        "metadata": {
            "invocation_id": "inv-1",
            "prepare_run_id": "inv-1",
            "invocation_started_at": "2026-10-16T00:00:00+00:00",
            "channel": "linear",
        },
    }
    [message] = payload["values"]["messages"]
    assert isinstance(message, BaseMessage) and message.content == "Done."


async def test_the_run_token_names_the_channel(
    completed: list[tuple[dict[str, Any], object]],
) -> None:
    # Signed into the token by the channel's route, so the hook needs no index read.
    run = _RUN.model_copy(update={"configurable": {**_RUN.configurable, "channel": "github"}})

    await channel_completion.complete_after_agent(run, "run-2", _messages())

    [(payload, _)] = completed
    assert payload["metadata"]["channel"] == "github"


async def test_runs_the_backend_started_wait_for_their_webhook(
    completed: list[tuple[dict[str, Any], object]],
) -> None:
    await index.create(_RUN.thread_id, runtime="mda", status="busy")

    await channel_completion.complete_after_agent(_RUN, "run-1", _messages())
    await channel_completion.complete_after_agent(_RUN, None, _messages())

    assert completed == []


async def test_a_failed_completion_never_fails_the_run(monkeypatch: pytest.MonkeyPatch) -> None:
    await index.create(_RUN.thread_id, runtime="mda", status="busy", metadata={"channel": "linear"})
    monkeypatch.setattr(
        completion, "complete_run_once", AsyncMock(side_effect=RuntimeError("telemetry down"))
    )

    await channel_completion.complete_after_agent(_RUN, "run-1", _messages())


async def test_the_after_agent_hook_completes_the_run_after_its_phase(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    order: list[str] = []

    async def session(self: graph.RemoteGraph, run: RemoteRun, run_id: str | None) -> object:
        return object()

    async def phase(self: graph.RemoteGraph, session: object, phase: str, body: object) -> object:
        order.append(phase)
        return {"jump_to": None}

    async def complete(run: RemoteRun, run_id: str | None, messages: object) -> None:
        order.append(f"complete {run_id}")

    monkeypatch.setattr(graph.RemoteGraph, "session", session)
    monkeypatch.setattr(graph.RemoteGraph, "_phase", phase)
    monkeypatch.setattr(graph, "complete_after_agent", complete)
    remote = graph.RemoteGraph("agent", AsyncMock(), AsyncMock())

    reply = await remote.hook(_RUN, "after_agent", {"run_id": "run-1", "messages": []})

    assert reply == {"jump_to": None}
    assert order == ["after_agent", "complete run-1"]
