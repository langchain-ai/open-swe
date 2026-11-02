import importlib
from collections.abc import Mapping
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from pydantic import JsonValue

from openswe.database import postgres
from openswe.tasks.store import Task
from openswe.threads import goal as goal_module
from openswe.threads import index
from openswe.threads.goal import ThreadGoal
from openswe.workspaces.rows import WorkspaceRow

_THREAD = "thread-1"


@pytest.fixture
async def goal(registry_db: None, monkeypatch: pytest.MonkeyPatch) -> ThreadGoal:
    """An agent thread working alone on its own task."""

    async def invalidate(**_key: str) -> None:
        return None

    monkeypatch.setattr(goal_module.Topic.THREAD_GOALS, "invalidate", invalidate)
    workspace = WorkspaceRow(slug="goals", name="Goals")
    async with postgres.session() as session:
        session.add(workspace)
    await index.create(_THREAD, runtime="mda", metadata={})
    await Task.for_thread(_THREAD, title="Fix login", workspace=workspace.slug)
    return ThreadGoal(_THREAD)


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


async def test_a_goal_rests_on_what_will_wake_it_and_stalls_once_that_lapses(
    goal: ThreadGoal, wakeups: list[SimpleNamespace]
) -> None:
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


async def test_a_stop_holds_until_its_person_writes_and_drops_the_wakeups(
    goal: ThreadGoal, wakeups: list[SimpleNamespace]
) -> None:
    await goal.stopped()
    assert await goal.current() is None

    await goal.change("working")
    wakeups.append(SimpleNamespace(due_at=datetime(2026, 11, 1, 23, 28, tzinfo=UTC)))
    await goal.stopped()
    # A run still finishing cannot rest or report over the stop.
    await goal.change("waiting")
    await goal.reported("complete", "Ready for review.")
    stopped = await goal.current()

    assert stopped is not None and (stopped.state, stopped.reason) == ("paused", "stopped")
    assert not wakeups
    await goal.change("working")
    await goal.reported("blocked", "Which Slack channel should review it?")
    waiting = await goal.current()
    assert waiting is not None
    assert (waiting.state, waiting.reason, waiting.ask) == (
        "waiting_on_user",
        "blocked",
        "Which Slack channel should review it?",
    )
    await goal.change(None)
    assert await goal.current() is None
