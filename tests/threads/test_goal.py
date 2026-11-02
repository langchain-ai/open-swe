import importlib
from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from pydantic import JsonValue

from openswe.threads import goal as goal_module
from openswe.threads.goal import ThreadGoal

_THREAD = "thread-1"


class _Threads:
    """The thread index, holding one thread's metadata."""

    def __init__(self) -> None:
        self.metadata: dict[str, JsonValue] = {}

    async def get(self, thread_id: str) -> SimpleNamespace:
        return SimpleNamespace(metadata=self.metadata)

    async def update(self, thread_id: str, metadata: Mapping[str, JsonValue]) -> None:
        self.metadata.update(metadata)


@pytest.fixture
def threads(monkeypatch: pytest.MonkeyPatch) -> _Threads:
    store = _Threads()
    monkeypatch.setattr(goal_module, "THREADS", store)
    monkeypatch.setattr(goal_module.postgres, "configured", lambda: True)

    async def invalidate(**_key: str) -> None:
        return None

    monkeypatch.setattr(goal_module.Topic.THREAD_GOALS, "invalidate", invalidate)
    return store


@pytest.fixture
def wakeups(monkeypatch: pytest.MonkeyPatch) -> list[SimpleNamespace]:
    """The thread's queued wakeups; cancelling them empties the list."""
    jobs: list[SimpleNamespace] = []

    async def find(metadata: Mapping[str, JsonValue]) -> list[SimpleNamespace]:
        return list(jobs)

    async def cancel(thread_id: str) -> None:
        jobs.clear()

    async def no_subscriptions(thread_id: str) -> list[object]:
        return []

    monkeypatch.setattr(goal_module.queue, "find", find)
    # The package re-exports the tool function under the module's name.
    wakeup_module = importlib.import_module("openswe.tools.schedule_thread_wakeup")
    monkeypatch.setattr(wakeup_module, "cancel_thread_wakeups", cancel)
    monkeypatch.setattr(
        "openswe.webhooks.event_subscriptions.EventSubscription.for_thread", no_subscriptions
    )
    return jobs


@pytest.fixture(autouse=True)
def no_lock(monkeypatch: pytest.MonkeyPatch) -> None:
    @asynccontextmanager
    async def lock(thread_id: str) -> AsyncIterator[None]:
        yield

    monkeypatch.setattr(goal_module, "agent_thread_pr_state_lock", lock)


async def test_a_goal_rests_on_what_will_wake_it_and_stalls_once_that_lapses(
    threads: _Threads, wakeups: list[SimpleNamespace]
) -> None:
    goal = ThreadGoal(_THREAD)
    due = datetime(2026, 11, 1, 23, 28, tzinfo=UTC)
    wakeups.append(SimpleNamespace(due_at=due))
    await goal.review_requested("https://github.com/o/r/pull/1")

    await goal.change("waiting")
    waiting = await goal.current()

    assert waiting is not None and waiting.state == "waiting"
    assert [(wait.kind, wait.until) for wait in waiting.waits] == [
        ("wakeup", due),
        ("review", None),
    ]
    wakeups.clear()
    await goal.review_returned()
    stalled = await goal.shown()
    assert stalled is not None and (stalled.state, stalled.reason) == ("paused", "no_progress")


async def test_waiting_on_the_person_keeps_their_ask(
    threads: _Threads, wakeups: list[SimpleNamespace]
) -> None:
    goal = ThreadGoal(_THREAD)

    await goal.reported("blocked", "Which Slack channel should review it?")
    waiting = await goal.current()

    assert waiting is not None
    assert (waiting.state, waiting.reason) == ("waiting_on_user", "blocked")
    assert waiting.ask == "Which Slack channel should review it?"


async def test_a_stop_pauses_a_goal_and_drops_its_wakeups_but_leaves_other_threads_alone(
    threads: _Threads, wakeups: list[SimpleNamespace]
) -> None:
    goal = ThreadGoal(_THREAD)
    wakeups.append(SimpleNamespace(due_at=datetime(2026, 11, 1, 23, 28, tzinfo=UTC)))

    await goal.stopped()
    assert goal_module.GOAL_KEY not in threads.metadata
    assert wakeups

    await goal.change("working")
    await goal.stopped()
    stopped = await goal.current()
    assert stopped is not None and (stopped.state, stopped.reason) == ("paused", "stopped")
    assert not wakeups
    await goal.change(None)
    assert await goal.current() is None
