"""A Goal mode thread's goal: where its work stands, and what it waits on.

The person who turned on Goal mode wants the agent to keep going until the work is
blocked on them or ready. The agent's run (MDA) decides when the goal moves: it
sends the agent back to work, lets a turn rest on a wait, and pauses the goal after
a few turns without progress. The backend keeps the goal in the thread's metadata,
where the dashboard shows it, reads what will wake the thread from where each wait
lives, and pauses it when the person stops the thread.
"""

import logging
import time
from collections.abc import Mapping
from datetime import datetime
from typing import Final, Literal, Self

from fastapi import APIRouter
from pydantic import BaseModel, JsonValue, ValidationError
from runtime_api.loop import GoalState

from openswe.dashboard.deps import SESSION_DEP
from openswe.database import postgres
from openswe.jobs import queue
from openswe.run_config import RunConfig
from openswe.threads.access import _readable_thread_metadata
from openswe.threads.store import THREADS
from openswe.ui_invalidations.topics import Topic
from openswe.utils.background_task_state import RUNNING_BACKGROUND_TASKS_KEY, RUNNING_MONITORS_KEY
from openswe.utils.thread_pr_state import agent_thread_pr_state_lock

logger = logging.getLogger(__name__)

router = APIRouter(tags=["threads"])

GOAL_KEY: Final = "goal"
# The draft PR whose Goal mode review this thread waits on, until its findings arrive.
REVIEW_PENDING_KEY: Final = "goal_review_pending"
_MAX_ASK_CHARS: Final = 1000

# Why the goal waits on its person: the agent needs something only they can give,
# or the work is ready for them, such as a reviewed draft to mark ready.
UserWaitReason = Literal["blocked", "ready"]
# Why a goal paused: the person stopped the run, or the agent kept ending turns
# without saying where the work stands or waiting on anything.
PauseReason = Literal["stopped", "no_progress"]


class GoalWait(BaseModel):
    """One thing that wakes the thread again."""

    kind: Literal["wakeup", "command", "monitor", "event", "review"]
    # When it fires, for a wakeup; when it stops waiting, for an event subscription.
    until: datetime | None = None
    # What it waits for: an event subscription's event types and target, or the PR
    # under review.
    detail: str | None = None


class Goal(BaseModel):
    """What the thread's metadata keeps of its goal."""

    state: GoalState = "working"
    reason: UserWaitReason | PauseReason | None = None
    # What the agent waits on its person for, in its own words, while it does.
    ask: str | None = None
    waits: list[GoalWait] = []
    updated_at_ms: int = 0


class ThreadGoal:
    """One thread's goal, as its run moves it and its person steps in."""

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
        return self._of(await self._metadata())

    async def change(self, state: GoalState | None, reason: PauseReason | None = None) -> None:
        """Move the goal as the run decided: working again, resting on what will wake the
        thread (whose waits are recorded), paused, or gone once Goal mode is off."""
        if state is None and await self.current() is None:
            return
        async with agent_thread_pr_state_lock(self.thread_id):
            if state is None:
                await self._write(None)
            elif state == "paused":
                await self._pause(reason or "no_progress")
            elif state == "waiting":
                await self._write(Goal(state="waiting", waits=await self.waits()))
            else:
                await self._write(Goal(state=state))

    async def reported(self, state: Literal["blocked", "complete"], message: str) -> None:
        """The agent said the work is blocked on the person, or complete: it waits on them
        for what ``message``, its reply to them, asks."""
        reason: UserWaitReason = "blocked" if state == "blocked" else "ready"
        ask = message.strip()
        if len(ask) > _MAX_ASK_CHARS:
            ask = ask[: _MAX_ASK_CHARS - 1].rstrip() + "…"
        async with agent_thread_pr_state_lock(self.thread_id):
            await self._write(Goal(state="waiting_on_user", reason=reason, ask=ask))

    async def stopped(self) -> None:
        """The person stopped the thread's run: its goal pauses until they write again.

        The stop itself has happened already, so a goal that cannot be paused is logged
        rather than failing it.
        """
        try:
            async with agent_thread_pr_state_lock(self.thread_id):
                goal = await self.current()
                if goal is not None and (goal.state, goal.reason) != ("paused", "stopped"):
                    await self._pause("stopped")
        except Exception:
            logger.exception(
                "Could not pause the goal of a stopped thread",
                extra={"agent_thread_id": self.thread_id},
            )

    async def review_requested(self, pr_url: str) -> None:
        """The agent asked for a Goal mode review of its draft: its findings will wake it."""
        await THREADS.update(self.thread_id, {REVIEW_PENDING_KEY: pr_url})

    async def review_returned(self) -> None:
        await THREADS.update(self.thread_id, {REVIEW_PENDING_KEY: None})

    async def waits(self) -> list[GoalWait]:
        """What will wake the thread again: wakeups, commands, monitors, event
        subscriptions, and a review whose findings have not arrived."""
        from openswe.tools.schedule_thread_wakeup import WAKEUP_KIND
        from openswe.webhooks.event_subscriptions import EventSubscription

        metadata = await self._metadata()
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
        if isinstance(pr_url := metadata.get(REVIEW_PENDING_KEY), str):
            waits.append(GoalWait(kind="review", detail=pr_url))
        return waits

    async def shown(self) -> Goal | None:
        """The goal as the dashboard shows it: a goal resting on waits that have all
        lapsed, with nothing left to wake the thread, has stalled and pauses."""
        goal = await self.current()
        if goal is None or goal.state != "waiting":
            return goal
        if waits := await self.waits():
            return goal.model_copy(update={"waits": waits})
        await self.change("paused", "no_progress")
        return await self.current()

    async def _pause(self, reason: PauseReason) -> None:
        """Stop pushing the work on; what it scheduled to wake it is dropped."""
        from openswe.tools.schedule_thread_wakeup import cancel_thread_wakeups

        await self._write(Goal(state="paused", reason=reason))
        await cancel_thread_wakeups(self.thread_id)

    async def _metadata(self) -> Mapping[str, JsonValue]:
        thread = await THREADS.get(self.thread_id)
        return {} if thread is None else thread.metadata

    def _of(self, metadata: Mapping[str, JsonValue]) -> Goal | None:
        raw = metadata.get(GOAL_KEY)
        if raw is None:
            return None
        try:
            return Goal.model_validate(raw)
        except ValidationError:
            logger.warning("Ignored an unreadable goal", extra={"agent_thread_id": self.thread_id})
            return None

    async def _write(self, goal: Goal | None) -> None:
        if goal is not None:
            goal.updated_at_ms = time.time_ns() // 1_000_000
        await THREADS.update(
            self.thread_id, {GOAL_KEY: None if goal is None else goal.model_dump(mode="json")}
        )
        await Topic.THREAD_GOALS.invalidate(key=self.thread_id)


@router.get("/threads/{thread_id}/goal")
async def api_get_thread_goal(thread_id: str, session: dict[str, str] = SESSION_DEP) -> Goal | None:
    """The thread's goal, for someone allowed to read the thread; None without Goal mode."""
    await _readable_thread_metadata(thread_id, login=session["sub"], email=session.get("email"))
    return await ThreadGoal(thread_id).shown()
