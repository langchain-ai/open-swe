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


async def test_an_asked_for_goal_holds_goal_mode_until_it_is_complete(
    goal: str, wakeups: list[SimpleNamespace]
) -> None:
    assert not await ThreadGoals.for_run(goal, sender="other", sender_goal_mode=False, worker=False)
    assert await ThreadGoals.for_run(goal, sender="owner", sender_goal_mode=True, worker=False)

    assert await ThreadGoals.created(goal, "Consolidate the desktop icons")
    created = await ThreadGoals.current(goal)
    assert created is not None and created.state == "working" and created.objective is not None
    assert created.objective.startswith("Consolidate the desktop icons")
    assert not await ThreadGoals.created(goal, "Something else")
    assert await ThreadGoals.for_run(goal, sender="other", sender_goal_mode=False, worker=False)
    assert not await ThreadGoals.for_run(goal, sender=None, sender_goal_mode=False, worker=True)

    assert await ThreadGoals.set_state(goal, "working", "waiting_on_user", "blocked") == "moved"
    assert await ThreadGoals.for_run(goal, sender="other", sender_goal_mode=False, worker=False)
    assert (
        await ThreadGoals.set_state(goal, "waiting_on_user", "waiting_on_user", "ready") == "moved"
    )
    assert await ThreadGoals.for_run(goal, sender="other", sender_goal_mode=False, worker=False)
    assert await ThreadGoals.set_state(goal, "waiting_on_user", "complete") == "check"
    assert await ThreadGoals.set_state(goal, "waiting_on_user", "complete") == "moved"
    assert not await ThreadGoals.for_run(goal, sender="other", sender_goal_mode=False, worker=False)
    assert not await ThreadGoals.for_run(goal, sender=None, sender_goal_mode=False, worker=False)
    await ThreadGoals.change(goal, "working")
    kept = await ThreadGoals.current(goal)
    assert kept is not None and kept.state == "complete"

    assert await ThreadGoals.created(goal, "Ship the icon release")
    renewed = await ThreadGoals.current(goal)
    assert renewed is not None and (renewed.state, renewed.reason) == ("working", None)
    await ThreadGoals.stopped(goal)
    assert await ThreadGoals.for_run(goal, sender="other", sender_goal_mode=False, worker=False)
    assert await ThreadGoals.created(goal, "Ship the icon release on Windows too")
    replaced = await ThreadGoals.current(goal)
    assert replaced is not None and replaced.state == "working"
    assert await ThreadGoals.set_state(goal, "working", "complete") == "check"
    assert await ThreadGoals.set_state(goal, "working", "complete") == "moved"


async def test_a_stop_holds_until_its_person_writes_and_drops_the_wakeups(
    goal: str, wakeups: list[SimpleNamespace]
) -> None:
    await ThreadGoals.stopped(goal)
    assert await ThreadGoals.current(goal) is None

    await ThreadGoals.change(goal, "working")
    wakeups.append(SimpleNamespace(due_at=datetime(2026, 11, 1, 23, 28, tzinfo=UTC)))
    await ThreadGoals.stopped(goal)
    await ThreadGoals.change(goal, "waiting")
    assert await ThreadGoals.set_state(goal, "paused", "waiting_on_user", "ready") == "stale"
    assert await ThreadGoals.set_state(goal, "paused", "paused") == "stale"
    stopped = await ThreadGoals.current(goal)

    assert stopped is not None and (stopped.state, stopped.reason) == ("paused", "stopped")
    assert not wakeups
    await ThreadGoals.change(goal, "working")
    assert await ThreadGoals.set_state(goal, "waiting", "waiting_on_user", "blocked") == "stale"
    assert await ThreadGoals.set_state(goal, "working", "waiting_on_user", "blocked") == "moved"
    waiting = await ThreadGoals.current(goal)
    assert waiting is not None and (waiting.state, waiting.reason) == ("waiting_on_user", "blocked")
    assert (
        await ThreadGoals.set_state(goal, "waiting_on_user", "waiting_on_user", "ready") == "moved"
    )
    ready = await ThreadGoals.current(goal)
    assert ready is not None and (ready.state, ready.reason) == ("waiting_on_user", "ready")
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
    assert await ThreadGoals.set_state(goal, "working", "paused") == "moved"
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


async def test_only_the_goals_own_slack_thread_stays_quiet_while_the_goal_is_underway(
    goal: str, wakeups: list[SimpleNamespace], monkeypatch: pytest.MonkeyPatch
) -> None:
    from openswe.github.pull_requests import PullRequest

    owned = SimpleNamespace(primary_thread_id=goal, agent_thread_id=None)
    monkeypatch.setattr(PullRequest, "get", AsyncMock(return_value=owned))
    await THREADS.update(
        goal, {"source_context": {"slack_thread": {"channel_id": "C1", "thread_ts": "1.0"}}}
    )
    own, other = ("C1", "1.0"), ("C2", "2.0")
    assert not await ThreadGoals.quiets_slack_thread("o", "r", 1, own)

    await ThreadGoals.change(goal, "working")
    assert await ThreadGoals.quiets_slack_thread("o", "r", 1, own)
    assert not await ThreadGoals.quiets_slack_thread("o", "r", 1, other)
    assert await ThreadGoals.set_state(goal, "working", "waiting_on_user", "ready") == "moved"
    assert await ThreadGoals.quiets_slack_thread("o", "r", 1, own)
    await ThreadGoals.stopped(goal)
    assert not await ThreadGoals.quiets_slack_thread("o", "r", 1, own)
    assert await ThreadGoals.set_state(goal, "paused", "working") == "moved"
    assert await ThreadGoals.set_state(goal, "working", "complete") == "moved"
    assert not await ThreadGoals.quiets_slack_thread("o", "r", 1, own)


async def test_a_goal_that_edited_files_is_checked_once_before_it_completes(
    goal: str, wakeups: list[SimpleNamespace]
) -> None:
    await ThreadGoals.change(goal, "working", request="Fix the login redirect")
    await ThreadGoals.edited(goal)

    assert await ThreadGoals.set_state(goal, "working", "complete") == "check"
    unchecked = await ThreadGoals.current(goal)
    assert unchecked is not None and unchecked.state == "working"
    await ThreadGoals.edited(goal)
    assert await ThreadGoals.set_state(goal, "working", "complete") == "check"
    assert await ThreadGoals.set_state(goal, "working", "complete") == "moved"
    done = await ThreadGoals.current(goal)
    assert done is not None and done.state == "complete" and not done.active

    await ThreadGoals.change(goal, "working", request="Fix the logout redirect")
    renewed = await ThreadGoals.current(goal)
    assert renewed is not None and renewed.state == "working" and renewed.objective is not None
    assert renewed.objective.startswith("Fix the logout redirect")
    assert await ThreadGoals.set_state(goal, "working", "complete") == "moved"

    assert await ThreadGoals.created(goal, "Investigate the login redirect")
    assert await ThreadGoals.set_state(goal, "working", "complete") == "check"
    await ThreadGoals.rewritten(goal, "Investigate the logout redirect too")
    assert await ThreadGoals.set_state(goal, "working", "complete") == "check"
    assert await ThreadGoals.set_state(goal, "working", "complete") == "moved"


async def test_a_paused_goal_resumes_only_from_its_pause(
    goal: str, wakeups: list[SimpleNamespace]
) -> None:
    await ThreadGoals.change(goal, "working")
    with pytest.raises(ValueError, match="Only a paused goal resumes"):
        await ThreadGoals.set_state(goal, "working", "working")
    await ThreadGoals.stopped(goal)

    assert await ThreadGoals.set_state(goal, "paused", "working") == "moved"
    resumed = await ThreadGoals.current(goal)
    assert resumed is not None and (resumed.state, resumed.reason) == ("working", None)


async def test_a_completion_that_lost_the_check_to_another_does_not_complete(
    goal: str, wakeups: list[SimpleNamespace], monkeypatch: pytest.MonkeyPatch
) -> None:
    await ThreadGoals.change(goal, "working", request="Fix the login redirect")
    await ThreadGoals.edited(goal)
    # Both calls read the goal before either moved its check.
    due = await Task.of_thread(goal)
    assert due is not None and due.goal_check == "due"
    monkeypatch.setattr(ThreadGoals, "_task", classmethod(lambda cls, thread_id: _returning(due)))

    first = await ThreadGoals.set_state(goal, "working", "complete")
    second = await ThreadGoals.set_state(goal, "working", "complete")

    assert (first, second) == ("check", "stale")
    unchecked = await Task.of_thread(goal)
    assert unchecked is not None
    assert (unchecked.goal_state, unchecked.goal_check) == ("working", "asked")


async def _returning[T](value: T) -> T:
    return value


@pytest.mark.parametrize("dispatch_fails", [False, True])
async def test_a_goal_created_outside_goal_mode_hands_its_run_on_as_its_creator(
    goal: str, wakeups: list[SimpleNamespace], monkeypatch: pytest.MonkeyPatch, dispatch_fails: bool
) -> None:
    from openswe.run_config import RunConfig
    from openswe.tools import goal as goal_tools

    config = RunConfig(thread_id=goal, auth_provider="langsmith", user_id="u-1", source="slack")
    dispatch = AsyncMock(side_effect=RuntimeError("down") if dispatch_fails else None)
    monkeypatch.setattr(RunConfig, "from_runtime", classmethod(lambda cls: config))
    monkeypatch.setattr(goal_tools, "get_config", lambda: {"configurable": {}})
    monkeypatch.setattr(goal_tools, "dispatch_agent_run", dispatch)

    result = await goal_tools.goal_create("Consolidate the desktop icons")

    assert result["success"] is not dispatch_fails
    queued = dispatch.await_args
    assert queued is not None
    assert {key: queued.args[2].get(key) for key in ("auth_provider", "user_id")} == {
        "auth_provider": "langsmith",
        "user_id": "u-1",
    }
    left = await ThreadGoals.current(goal)
    assert left is not None
    assert (left.state, left.reason) == (
        ("paused", "error") if dispatch_fails else ("working", None)
    )
    if dispatch_fails:
        assert await ThreadGoals.created(goal, "Consolidate the desktop icons")
