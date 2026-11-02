"""A goal-mode thread's goal: where its work stands, and what it waits on.

The person who turned on Goal mode wants the agent to keep going until the work
is blocked on them or complete. Each turn that ends without saying so must rest
on something that wakes the thread again; otherwise the agent is sent back to
work, and after a few tries the goal pauses rather than prodding it forever.
The goal is kept in the thread's metadata, so the dashboard can show it and a
stop can pause it, and what it waits on is read from where each wait lives.
"""

import logging
import time
from datetime import datetime
from typing import Final, Literal, Self

from fastapi import APIRouter
from pydantic import BaseModel, ValidationError

from openswe.dashboard.deps import SESSION_DEP
from openswe.database import postgres
from openswe.jobs import queue
from openswe.prompts import prompt
from openswe.run_config import RunConfig
from openswe.threads.access import _readable_thread_metadata
from openswe.threads.store import THREADS
from openswe.ui_invalidations.topics import Topic
from openswe.utils.background_task_state import RUNNING_BACKGROUND_TASKS_KEY, RUNNING_MONITORS_KEY

logger = logging.getLogger(__name__)

router = APIRouter(tags=["threads"])

GOAL_KEY: Final = "goal"
_MAX_ASK_CHARS: Final = 1000

GoalState = Literal["working", "waiting", "waiting_on_user", "paused"]
# Why the goal waits on its person: the agent needs something only they can give,
# or the work is ready for them, such as a reviewed draft to mark ready.
UserWaitReason = Literal["blocked", "ready"]
# Why a goal paused: the person stopped the run, or the agent kept ending turns
# without saying where the work stands or waiting on anything.
PauseReason = Literal["stopped", "no_progress"]


class GoalWait(BaseModel):
    """One thing that wakes the thread again."""

    kind: Literal["wakeup", "command", "monitor", "event"]
    # When it fires, for a wakeup; when it stops waiting, for an event subscription.
    until: datetime | None = None
    # What it waits for, for an event subscription: its event types and target.
    detail: str | None = None


class Goal(BaseModel):
    """What the thread's metadata keeps of its goal."""

    state: GoalState = "working"
    reason: UserWaitReason | PauseReason | None = None
    # What the agent waits on its person for, in its own words, while it does.
    ask: str | None = None
    waits: list[GoalWait] = []
    updated_at_ms: int = 0

    @property
    def rests(self) -> bool:
        """Whether a turn may end here without being sent back to work."""
        return self.state in {"waiting_on_user", "paused"}


class ThreadGoal:
    """One thread's goal, changed as its agent works and its person steps in.

    A thread gets one when a Goal mode run starts, or from such a run's report or the
    end of its turn. Every other change leaves a thread without one alone.
    """

    def __init__(self, thread_id: str) -> None:
        self.thread_id = thread_id

    @classmethod
    def of_run(cls) -> Self:
        """The goal of the thread the running tool call works in."""
        thread_id = RunConfig.from_runtime().thread_id
        if not thread_id:
            raise RuntimeError("No thread_id in current run config")
        return cls(thread_id)

    async def current(self) -> Goal | None:
        if not postgres.configured():
            return None
        thread = await THREADS.get(self.thread_id)
        raw = None if thread is None else thread.metadata.get(GOAL_KEY)
        if raw is None:
            return None
        try:
            return Goal.model_validate(raw)
        except ValidationError:
            logger.warning("Ignored an unreadable goal", extra={"agent_thread_id": self.thread_id})
            return None

    async def reminder(self) -> str | None:
        """What to remind the agent of while its goal waits on its person: what for, and
        to settle it itself when it now can."""
        goal = await self.current()
        if goal is None or goal.state != "waiting_on_user":
            return None
        return prompt("runs/goal-waiting-on-user", reason=goal.reason, ask=goal.ask)

    async def started(self) -> None:
        """A Goal mode run started: the thread has a goal from now on."""
        if await self.current() is None:
            await self._write(Goal())

    async def resumed(self) -> None:
        """A person wrote: whatever the goal rested on, it is the agent's to work on again."""
        goal = await self.current()
        if goal is not None and goal.state != "working":
            await self._write(Goal())

    async def woke(self) -> None:
        """Something the goal waited on woke the thread."""
        goal = await self.current()
        if goal is not None and goal.state == "waiting":
            await self._write(Goal())

    async def reported(self, state: Literal["blocked", "complete"], message: str) -> None:
        """The agent said the work is blocked on the person, or complete: it waits on them
        for what ``message``, its reply to them, asks."""
        reason: UserWaitReason = "blocked" if state == "blocked" else "ready"
        ask = message.strip()
        if len(ask) > _MAX_ASK_CHARS:
            ask = ask[: _MAX_ASK_CHARS - 1].rstrip() + "…"
        await self._write(Goal(state="waiting_on_user", reason=reason, ask=ask))

    async def stopped(self) -> None:
        """The person stopped the thread's run: its goal pauses until they write again.

        The stop itself has happened already, so a goal that cannot be paused is logged
        rather than failing it.
        """
        try:
            goal = await self.current()
            if goal is not None and goal.state != "paused":
                await self._pause("stopped")
        except Exception:
            logger.exception(
                "Could not pause the goal of a stopped thread",
                extra={"agent_thread_id": self.thread_id},
            )

    async def turn_ended(self, *, last: bool) -> bool:
        """A turn ended without saying where the work stands: whether it may rest.

        It may while the goal waits on its person or is paused, or while something
        will wake the thread again. ``last`` says the agent was already sent back
        as often as it may be: the goal pauses instead.
        """
        goal = await self.current()
        if goal is not None and goal.rests:
            return True
        if waits := await self.waits():
            await self._write(Goal(state="waiting", waits=waits))
            return True
        if last:
            await self._pause("no_progress")
            return True
        if goal is None:
            await self._write(Goal())
        return False

    async def _pause(self, reason: PauseReason) -> None:
        """Stop pushing the work on; what it scheduled to wake it is dropped."""
        from openswe.tools.schedule_thread_wakeup import cancel_thread_wakeups

        await self._write(Goal(state="paused", reason=reason))
        await cancel_thread_wakeups(self.thread_id)

    async def waits(self) -> list[GoalWait]:
        """What will wake the thread again: wakeups, commands, monitors and subscriptions."""
        from openswe.tools.schedule_thread_wakeup import WAKEUP_KIND
        from openswe.webhooks.event_subscriptions import EventSubscription

        thread = await THREADS.get(self.thread_id)
        metadata = {} if thread is None else thread.metadata
        waits = [
            GoalWait(kind="wakeup", until=job.due_at)
            for job in await queue.find({"kind": WAKEUP_KIND, "thread_id": self.thread_id})
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
            for subscription in await EventSubscription.for_thread(self.thread_id)
        )
        return waits

    async def _write(self, goal: Goal) -> None:
        goal.updated_at_ms = time.time_ns() // 1_000_000
        await THREADS.update(self.thread_id, {GOAL_KEY: goal.model_dump(mode="json")})
        await Topic.THREAD_GOALS.invalidate(key=self.thread_id)


@router.get("/threads/{thread_id}/goal")
async def api_get_thread_goal(thread_id: str, session: dict[str, str] = SESSION_DEP) -> Goal | None:
    """The thread's goal, for someone allowed to read the thread; None without Goal mode."""
    await _readable_thread_metadata(thread_id, login=session["sub"], email=session.get("email"))
    return await ThreadGoal(thread_id).current()
