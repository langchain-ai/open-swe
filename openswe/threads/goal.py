"""A Goal mode task's goal, as seen from one of its threads.

The person who turned on Goal mode wants the agent to keep going until the work is
blocked on them or ready. The agent's run (MDA) decides when the goal moves: it sends
the agent back to work, lets a turn rest on a wait, and pauses the goal after a few
turns without progress. The goal is kept on the thread's task (``Task.goal_state``),
which the dashboard shows; the backend reads what will wake the thread from where
each wait lives, and pauses the goal when the person stops the thread.
"""

import logging
from collections.abc import Mapping
from datetime import datetime
from typing import Final, Literal, TypedDict, Unpack
from uuid import UUID

from fastapi import APIRouter
from pydantic import BaseModel, JsonValue, ValidationError
from runtime_api.loop import GoalState

from openswe.dashboard.deps import SESSION_DEP
from openswe.jobs import queue
from openswe.prompts import prompt
from openswe.run_config import RunConfig
from openswe.tasks.store import PauseReason, Task, UserWaitReason
from openswe.threads.access import _readable_thread_metadata
from openswe.threads.store import THREADS
from openswe.ui_invalidations.topics import Topic
from openswe.utils.background_task_state import RUNNING_BACKGROUND_TASKS_KEY, RUNNING_MONITORS_KEY

logger = logging.getLogger(__name__)

router = APIRouter(tags=["threads"])

# The draft PR whose Goal mode review this thread waits on, until its findings arrive.
REVIEW_PENDING_KEY: Final = "goal_review_pending"
_MAX_ASK_CHARS: Final = 1000


class GoalWait(BaseModel):
    """One thing that wakes the thread again."""

    kind: Literal["wakeup", "command", "monitor", "event", "review"]
    # When it fires, for a wakeup; when it stops waiting, for an event subscription.
    until: datetime | None = None
    # What it waits for: an event subscription's event types and target, or the PR
    # under review.
    detail: str | None = None


class _GoalChange(TypedDict, total=False):
    state: GoalState | None
    reason: UserWaitReason | PauseReason | None
    ask: str | None
    objective: str | None
    waits: list[dict[str, JsonValue]] | None
    by: UUID | None
    over_stop: bool


class Goal(BaseModel):
    """What the thread's task keeps of its goal."""

    state: GoalState = "working"
    reason: UserWaitReason | PauseReason | None = None
    # What the goal is for: the person's request, in the shape the work should take.
    objective: str | None = None
    # What the agent waits on its person for, in its own words, while it does.
    ask: str | None = None
    waits: list[GoalWait] = []
    updated_at_ms: int = 0


class ThreadGoals:
    """Read and move task-owned goals through their coordinating threads."""

    @staticmethod
    def run_thread_id() -> str:
        """Require the thread the running tool call works in."""
        thread_id = RunConfig.from_runtime().thread_id
        if not thread_id:
            raise RuntimeError("No thread_id in current run config")
        return thread_id

    @classmethod
    async def current(cls, thread_id: str) -> Goal | None:
        task = await cls._task(thread_id)
        return None if task is None else cls._of(task, thread_id=thread_id)

    @classmethod
    async def change(
        cls,
        thread_id: str,
        state: GoalState | None,
        reason: PauseReason | None = None,
        *,
        request: str | None = None,
    ) -> None:
        """Move the goal as the run decided: working again, resting on what will wake the
        thread (whose waits are recorded), paused, or gone once Goal mode is off. A goal
        that starts from the person's ``request`` takes its objective from it.

        A rest never overrides a stop: the person who stopped the goal resumes it.
        """
        task = await cls._task(thread_id)
        if task is None:
            if state is not None:
                logger.warning(
                    "A goal moved on a thread coordinating no task",
                    extra={"agent_thread_id": thread_id},
                )
            return
        if state is None:
            if task.goal_state is not None:
                await cls._move(thread_id, task.id, state=None)
        elif state == "paused":
            await cls._pause(thread_id, task.id, reason or "no_progress", over_stop=False)
        elif state == "waiting":
            waits = [wait.model_dump(mode="json") for wait in await cls.waits(thread_id)]
            await cls._move(thread_id, task.id, state="waiting", waits=waits, over_stop=False)
        elif task.goal_state is None and request:
            await cls._move(
                thread_id, task.id, state=state, objective=prompt("goal/objective", request=request)
            )
        else:
            await cls._move(thread_id, task.id, state=state)

    @classmethod
    async def rewritten(cls, thread_id: str, objective: str) -> None:
        """The agent rewrote its goal's objective."""
        task = await cls._task(thread_id)
        if task is None or task.goal_state is None:
            raise ValueError("This thread has no goal to rewrite; Goal mode sets one")
        await cls._move(
            thread_id,
            task.id,
            state=task.goal_state,
            reason=task.goal_reason,
            ask=task.goal_ask,
            waits=task.goal_waits,
            objective=objective,
            over_stop=False,
        )

    @classmethod
    async def failed(cls, thread_id: str) -> None:
        """A run on the thread failed: its goal pauses rather than retry into the same error.

        The failure is the run's; one the goal cannot record is logged, not raised.
        """
        try:
            task = await cls._task(thread_id)
            if task is not None and task.goal_state in {"working", "waiting"}:
                await cls._pause(thread_id, task.id, "error", over_stop=False)
        except Exception:
            logger.exception(
                "Could not pause the goal of a failed run", extra={"agent_thread_id": thread_id}
            )

    @classmethod
    async def paused_on_request(cls, thread_id: str) -> None:
        """The agent paused the goal because its person asked: only they resume it."""
        task = await cls._task(thread_id)
        if task is None or task.goal_state is None:
            raise ValueError("This thread has no goal to pause; Goal mode sets one")
        await cls._pause(thread_id, task.id, "requested")

    @classmethod
    async def reported(
        cls, thread_id: str, state: Literal["blocked", "complete"], message: str
    ) -> None:
        """The agent said the work is blocked on the person, or complete: it waits on them
        for what ``message``, its reply to them, asks."""
        task = await cls._task(thread_id)
        if task is None:
            logger.warning(
                "Work reported on a thread coordinating no task",
                extra={"agent_thread_id": thread_id},
            )
            return
        reason: UserWaitReason = "blocked" if state == "blocked" else "ready"
        ask = message.strip()
        if len(ask) > _MAX_ASK_CHARS:
            ask = ask[: _MAX_ASK_CHARS - 1].rstrip() + "…"
        await cls._move(
            thread_id, task.id, state="waiting_on_user", reason=reason, ask=ask, over_stop=False
        )

    @classmethod
    async def stopped(cls, thread_id: str, *, by: str | None = None) -> None:
        """The person logged in as ``by`` stopped the thread's run: its goal pauses until
        they write again, recorded as theirs.

        The stop itself has happened already, so a goal that cannot be paused is logged
        rather than failing it.
        """
        from openswe.tasks.schemas import user_for_login

        try:
            task = await cls._task(thread_id)
            if task is not None and task.goal_state is not None:
                person = await user_for_login(by) if by else None
                await cls._pause(
                    thread_id, task.id, "stopped", by=None if person is None else person.id
                )
        except Exception:
            logger.exception(
                "Could not pause the goal of a stopped thread", extra={"agent_thread_id": thread_id}
            )

    @classmethod
    async def review_requested(cls, thread_id: str, pr_url: str) -> None:
        """The agent asked for a Goal mode review of its draft: its findings will wake it."""
        await THREADS.update(thread_id, {REVIEW_PENDING_KEY: pr_url})

    @classmethod
    async def review_returned(cls, thread_id: str) -> None:
        await THREADS.update(thread_id, {REVIEW_PENDING_KEY: None})

    @classmethod
    async def waits(cls, thread_id: str) -> list[GoalWait]:
        """What will wake the thread again: wakeups, commands, monitors, event
        subscriptions, and a review whose findings have not arrived."""
        from openswe.tools.schedule_thread_wakeup import WAKEUP_KIND
        from openswe.webhooks.event_subscriptions import EventSubscription

        thread = await THREADS.get(thread_id)
        metadata: Mapping[str, JsonValue] = {} if thread is None else thread.metadata
        waits = [
            GoalWait(kind="wakeup", until=job.due_at)
            for job in await queue.find({"kind": WAKEUP_KIND, "thread_id": thread_id})
        ]
        for key, kind in (
            (RUNNING_BACKGROUND_TASKS_KEY, "command"),
            (RUNNING_MONITORS_KEY, "monitor"),
        ):
            running = metadata.get(key)
            if isinstance(running, list):
                waits.extend(GoalWait(kind=kind) for item in running if isinstance(item, str))
        waits.extend(
            GoalWait(
                kind="event",
                until=subscription.expires_at,
                detail=", ".join([*subscription.event_types, subscription.target]),
            )
            for subscription in await EventSubscription.for_thread(thread_id)
        )
        if isinstance(pr_url := metadata.get(REVIEW_PENDING_KEY), str):
            waits.append(GoalWait(kind="review", detail=pr_url))
        return waits

    @classmethod
    async def shown(cls, thread_id: str) -> Goal | None:
        """The goal as the dashboard shows it: a goal resting on waits that have all
        lapsed, with nothing left to wake the thread, has stalled and pauses."""
        goal = await cls.current(thread_id)
        if goal is None or goal.state != "waiting":
            return goal
        if waits := await cls.waits(thread_id):
            return goal.model_copy(update={"waits": waits})
        await cls.change(thread_id, "paused", "no_progress")
        return await cls.current(thread_id)

    @classmethod
    async def _task(cls, thread_id: str) -> Task | None:
        """The task whose goal this thread holds: the one it coordinates. A worker's runs
        never move its coordinator's goal, nor does stopping a worker."""
        task = await Task.of_thread(thread_id)
        return task if task is not None and task.coordinator_thread_id == thread_id else None

    @staticmethod
    def _of(task: Task, *, thread_id: str) -> Goal | None:
        if task.goal_state is None:
            return None
        try:
            return Goal.model_validate(
                {
                    "state": task.goal_state,
                    "reason": task.goal_reason,
                    "objective": task.goal_objective,
                    "ask": task.goal_ask,
                    "waits": task.goal_waits or [],
                    "updated_at_ms": int(task.goal_updated_at.timestamp() * 1000)
                    if task.goal_updated_at
                    else 0,
                }
            )
        except ValidationError:
            logger.warning(
                "Ignored an unreadable goal", extra={"agent_thread_id": thread_id}, exc_info=True
            )
            return None

    @classmethod
    async def _pause(
        cls,
        thread_id: str,
        task_id: UUID,
        reason: PauseReason,
        *,
        by: UUID | None = None,
        over_stop: bool = True,
    ) -> None:
        """Stop pushing the work on; what it scheduled to wake it is dropped."""
        from openswe.tools.schedule_thread_wakeup import cancel_thread_wakeups

        if await cls._move(
            thread_id, task_id, state="paused", reason=reason, by=by, over_stop=over_stop
        ):
            await cancel_thread_wakeups(thread_id)

    @classmethod
    async def _move(cls, thread_id: str, task_id: UUID, **change: Unpack[_GoalChange]) -> bool:
        moved = await Task.move_goal(task_id, **change)
        if moved:
            await Topic.THREAD_GOALS.invalidate(key=thread_id)
        return moved


@router.get("/threads/{thread_id}/goal")
async def api_get_thread_goal(thread_id: str, session: dict[str, str] = SESSION_DEP) -> Goal | None:
    """The thread's goal, for someone allowed to read the thread; None without Goal mode."""
    await _readable_thread_metadata(thread_id, login=session["sub"], email=session.get("email"))
    return await ThreadGoals.shown(thread_id)
