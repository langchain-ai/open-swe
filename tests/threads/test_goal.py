import importlib
from collections.abc import Mapping
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from pydantic import JsonValue

from openswe.database import postgres
from openswe.tasks.store import Task, TaskMembership
from openswe.threads import goal as goal_module
from openswe.threads import index
from openswe.threads.goal import ThreadGoals
from openswe.threads.store import THREADS
from openswe.users import User
from openswe.users.models import UserIdentity
from openswe.workspaces.rows import WorkspaceRow

_THREAD = "thread-1"


@pytest.fixture
async def goal(registry_db: None, monkeypatch: pytest.MonkeyPatch) -> str:
    """An agent thread working alone on its own task."""

    async def invalidate(**_key: str) -> None:
        return None

    monkeypatch.setattr(goal_module.Topic.THREAD_GOALS, "invalidate", invalidate)
    workspace = WorkspaceRow(slug="goals", name="Goals")
    async with postgres.session() as session:
        session.add(workspace)
    await index.create(_THREAD, runtime="mda", metadata={})
    await Task.for_thread(_THREAD, title="Fix login", workspace=workspace.slug)
    return _THREAD


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
    wakeup_module = importlib.import_module("openswe.tools.schedule_thread_wakeup")
    monkeypatch.setattr(wakeup_module, "cancel_thread_wakeups", cancel)
    monkeypatch.setattr(
        "openswe.webhooks.event_subscriptions.EventSubscription.for_thread", no_subscriptions
    )
    return jobs


async def test_a_goal_rests_on_what_will_wake_it_and_stalls_once_that_lapses(
    goal: str, wakeups: list[SimpleNamespace]
) -> None:
    due = datetime(2026, 11, 1, 23, 28, tzinfo=UTC)
    wakeups.append(SimpleNamespace(due_at=due))
    await ThreadGoals.review_requested(goal, "https://github.com/o/r/pull/1")

    await ThreadGoals.change(goal, "waiting")
    waiting = await ThreadGoals.current(goal)

    assert waiting is not None and waiting.state == "waiting"
    assert [(wait.kind, wait.until) for wait in waiting.waits] == [
        ("wakeup", due),
        ("review", None),
    ]
    wakeups.clear()
    await ThreadGoals.review_returned(goal)
    stalled = await ThreadGoals.shown(goal)
    assert stalled is not None and (stalled.state, stalled.reason) == ("paused", "no_progress")
    kept = await ThreadGoals.current(goal)
    assert kept is not None and kept.state == "waiting"


async def test_only_its_owner_turning_goal_mode_off_clears_the_goal(
    goal: str, wakeups: list[SimpleNamespace], monkeypatch: pytest.MonkeyPatch
) -> None:
    owner = User(identities=[UserIdentity(provider="github", external_id="1", login="owner")])
    other = User(identities=[UserIdentity(provider="github", external_id="2", login="other")])
    people = {"1": owner, "2": other}
    by_login = {"owner": owner, "other": other}
    monkeypatch.setattr(
        User, "for_login", AsyncMock(side_effect=lambda provider, login: by_login.get(login))
    )
    monkeypatch.setattr(
        User,
        "for_identity",
        AsyncMock(side_effect=lambda provider, external_id: people[external_id]),
    )
    monkeypatch.setattr(
        User,
        "get",
        AsyncMock(side_effect=lambda user_id: owner if user_id == owner.id else other),
    )
    await THREADS.update(goal, {"owner_type": "user", "owner_user_id": str(owner.id)})
    await ThreadGoals.change(goal, "working")

    assert await ThreadGoals.for_run(goal, sender=None, sender_goal_mode=False, worker=False)
    assert not await ThreadGoals.for_run(goal, sender=None, sender_goal_mode=False, worker=True)
    assert not await ThreadGoals.for_run(goal, sender="other", sender_goal_mode=False, worker=False)
    assert await ThreadGoals.current(goal) is not None
    assert not await ThreadGoals.for_run(goal, sender="owner", sender_goal_mode=False, worker=False)
    assert await ThreadGoals.current(goal) is None
    assert not await ThreadGoals.for_run(goal, sender=None, sender_goal_mode=False, worker=False)


async def test_a_stop_holds_until_its_person_writes_and_drops_the_wakeups(
    goal: str, wakeups: list[SimpleNamespace]
) -> None:
    await ThreadGoals.stopped(goal)
    assert await ThreadGoals.current(goal) is None

    await ThreadGoals.change(goal, "working")
    wakeups.append(SimpleNamespace(due_at=datetime(2026, 11, 1, 23, 28, tzinfo=UTC)))
    await ThreadGoals.stopped(goal)
    await ThreadGoals.change(goal, "waiting")
    await ThreadGoals.reported(goal, "complete", "Ready for review.")
    stopped = await ThreadGoals.current(goal)

    assert stopped is not None and (stopped.state, stopped.reason) == ("paused", "stopped")
    assert not wakeups
    await ThreadGoals.change(goal, "working")
    await ThreadGoals.reported(goal, "blocked", "Which Slack channel should review it?")
    waiting = await ThreadGoals.current(goal)
    assert waiting is not None
    assert (waiting.state, waiting.reason, waiting.ask) == (
        "waiting_on_user",
        "blocked",
        "Which Slack channel should review it?",
    )
    await ThreadGoals.unreported(goal)
    unreported = await ThreadGoals.current(goal)
    assert unreported is not None and unreported.state == "working"
    await ThreadGoals.stopped(goal)
    await ThreadGoals.unreported(goal)
    still_stopped = await ThreadGoals.current(goal)
    assert still_stopped is not None and still_stopped.state == "paused"
    await ThreadGoals.change(goal, None)
    assert await ThreadGoals.current(goal) is None


async def test_a_workers_runs_leave_its_coordinators_goal_alone(
    goal: str, wakeups: list[SimpleNamespace]
) -> None:
    await ThreadGoals.change(goal, "working")
    await ThreadGoals.stopped(goal)
    task = await Task.of_thread(_THREAD)
    assert task is not None
    async with postgres.session() as session:
        session.add(TaskMembership(thread_id="worker-1", task_id=task.id, role="worker"))
    worker = "worker-1"

    await ThreadGoals.change(worker, None)
    await ThreadGoals.stopped(worker)

    kept = await ThreadGoals.current(goal)
    assert kept is not None and (kept.state, kept.reason) == ("paused", "stopped")
    assert await ThreadGoals.current(worker) is None


async def test_a_goal_starts_from_the_request_and_the_agent_can_rewrite_or_pause_it(
    goal: str, wakeups: list[SimpleNamespace]
) -> None:
    await ThreadGoals.change(goal, "working", request="Fix the login redirect")
    started = await ThreadGoals.current(goal)
    assert started is not None and started.objective is not None
    assert started.objective.startswith("Fix the login redirect")

    await ThreadGoals.rewritten(goal, "Fix the login redirect and the logout one")
    await ThreadGoals.paused_on_request(goal)
    await ThreadGoals.change(goal, "waiting")
    await ThreadGoals.failed(goal)
    paused = await ThreadGoals.current(goal)

    assert paused is not None
    assert (paused.state, paused.reason) == ("paused", "requested")
    assert paused.objective == "Fix the login redirect and the logout one"
    await ThreadGoals.change(goal, "working")
    await ThreadGoals.failed(goal)
    errored = await ThreadGoals.current(goal)
    assert errored is not None and (errored.state, errored.reason) == ("paused", "error")
