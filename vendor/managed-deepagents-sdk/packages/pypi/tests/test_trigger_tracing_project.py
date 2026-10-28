"""The tracing project id sent on channel runtime events."""

from __future__ import annotations

import asyncio
import threading
from collections.abc import Iterator

import pytest
from langsmith import utils as ls_utils

from managed_deepagents._channels.trigger import tracing_project


@pytest.fixture(autouse=True)
def lookups(monkeypatch: pytest.MonkeyPatch) -> Iterator[list[str]]:
    """Tracing on, writing to ``agent-project``; records each lookup by name."""
    lookups: list[str] = []

    def read_project_id(name: str) -> str:
        lookups.append(name)
        return "session-1"

    monkeypatch.setattr(ls_utils, "tracing_is_enabled", lambda: True)
    monkeypatch.setattr(ls_utils, "get_tracer_project", lambda: "agent-project")
    monkeypatch.setattr(tracing_project, "_read_project_id", read_project_id)
    tracing_project._project_ids.clear()
    tracing_project._next_lookup_at.clear()
    yield lookups
    tracing_project._project_ids.clear()
    tracing_project._next_lookup_at.clear()


def test_looks_the_project_up_by_the_name_the_tracer_writes_to(
    lookups: list[str],
) -> None:
    assert asyncio.run(tracing_project.tracer_session_id()) == "session-1"
    assert lookups == ["agent-project"]


def test_caches_a_found_project_for_the_process(lookups: list[str]) -> None:
    asyncio.run(tracing_project.tracer_session_id())
    assert asyncio.run(tracing_project.tracer_session_id()) == "session-1"
    assert lookups == ["agent-project"]


@pytest.mark.parametrize("enabled", [False, "local"])
def test_sends_nothing_when_traces_never_reach_langsmith(
    monkeypatch: pytest.MonkeyPatch,
    lookups: list[str],
    enabled: object,
) -> None:
    """A link to a trace that was never uploaded would always be dead."""
    monkeypatch.setattr(ls_utils, "tracing_is_enabled", lambda: enabled)
    assert asyncio.run(tracing_project.tracer_session_id()) is None
    assert lookups == []


def test_a_failed_lookup_waits_out_the_retry_delay(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A failing lookup must not add a request to every event."""
    attempts: list[str] = []
    clock = [1_000.0]

    def read_project_id(name: str) -> str:
        attempts.append(name)
        if len(attempts) == 1:
            raise RuntimeError("LangSmith unavailable")
        return "session-1"

    monkeypatch.setattr(tracing_project, "_read_project_id", read_project_id)
    monkeypatch.setattr(tracing_project, "monotonic", lambda: clock[0])

    assert asyncio.run(tracing_project.tracer_session_id()) is None
    clock[0] += tracing_project._RETRY_AFTER_SECONDS - 1
    assert asyncio.run(tracing_project.tracer_session_id()) is None
    assert attempts == ["agent-project"]

    clock[0] += 1
    assert asyncio.run(tracing_project.tracer_session_id()) == "session-1"
    assert attempts == ["agent-project", "agent-project"]


def test_a_caller_during_a_lookup_does_not_wait_or_start_another(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    attempts: list[str] = []
    release = threading.Event()

    def read_project_id(name: str) -> str:
        attempts.append(name)
        release.wait(timeout=1)
        return "session-1"

    monkeypatch.setattr(tracing_project, "_read_project_id", read_project_id)

    async def both() -> tuple[str | None, str | None]:
        first = asyncio.create_task(tracing_project.tracer_session_id())
        await asyncio.sleep(0)
        second = await tracing_project.tracer_session_id()
        release.set()
        return await first, second

    assert asyncio.run(both()) == ("session-1", None)
    assert attempts == ["agent-project"]
