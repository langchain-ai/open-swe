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

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field, JsonValue, ValidationError
from runtime_api.loop import GoalState

from openswe.audit_logs.middleware import audit_endpoint
from openswe.dashboard.deps import SESSION_DEP
from openswe.dispatch import dispatch_agent_run, follow_up_configurable
from openswe.jobs import queue
from openswe.prompts import prompt
from openswe.run_config import RunConfig
from openswe.source_context import SourceContext
from openswe.tasks.store import GoalCheck, PauseReason, Task, UserWaitReason
from openswe.threads.access import _readable_thread_metadata
from openswe.threads.store import THREADS
from openswe.ui_invalidations.topics import Topic
from openswe.users import User
from openswe.utils.background_task_state import RUNNING_BACKGROUND_TASKS_KEY, RUNNING_MONITORS_KEY

logger = logging.getLogger(__name__)

router = APIRouter(tags=["threads"])

REVIEW_PENDING_KEY: Final = "goal_review_pending"
_EDIT_SENDER: Final = "system:goal-edit"
_MAX_ASK_CHARS: Final = 1000
type GoalStateOutcome = Literal["moved", "stale", "check"]


class GoalWait(BaseModel):
    """One thing that wakes the thread again."""

    kind: Literal["wakeup", "command", "monitor", "event", "review"]
    until: datetime | None = None
    detail: str | None = None


class _GoalChange(TypedDict, total=False):
    state: GoalState | None
    reason: UserWaitReason | PauseReason | None
    ask: str | None
    objective: str | None
    check: GoalCheck | None
    waits: list[dict[str, JsonValue]] | None
    by: UUID | None
    over_stop: bool
    from_state: GoalState | None
    checked: bool


class GoalNotDelivered(Exception):
    """The edited goal was saved, but no run could carry it to the agent."""


class GoalEdit(BaseModel):
    """The person's own rewrite of their goal's objective."""

    objective: str = Field(min_length=1, max_length=20_000)


class Goal(BaseModel):
    """What the thread's task keeps of its goal."""

    state: GoalState = "working"
    reason: UserWaitReason | PauseReason | None = None
    objective: str | None = None
    ask: str | None = None
    waits: list[GoalWait] = []
    updated_at_ms: int = 0

    @property
    def active(self) -> bool:
        """Whether the goal still holds its thread in Goal mode: until it is complete.
        A paused goal holds it too, so the person's next message resumes it."""
        return self.state != "complete"


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
        elif task.goal_state == "complete" and not request:
            # Only a new request starts a goal over once it is complete.
            return
        elif state == "paused":
            await cls._pause(thread_id, task.id, reason or "no_progress", over_stop=False)
        elif state == "waiting":
            waits = [wait.model_dump(mode="json") for wait in await cls.waits(thread_id)]
            await cls._move(thread_id, task.id, state="waiting", waits=waits, over_stop=False)
        elif task.goal_state in {None, "complete"} and request:
            await cls._move(
                thread_id, task.id, state=state, objective=prompt("goal/objective", request=request)
            )
        else:
            await cls._move(thread_id, task.id, state=state)

    @classmethod
    async def created(cls, thread_id: str, request: str) -> bool:
        """The agent set a goal from ``request`` because someone asked it to: the thread
        works toward it in Goal mode, whatever its people's own settings. It replaces a
        goal that is complete or paused; False, leaving the goal alone, while one is
        underway."""
        task = await cls._task(thread_id)
        if task is None:
            raise ValueError("Only a thread that coordinates its own task can hold a goal")
        current = cls._of(task, thread_id=thread_id)
        if current is not None and current.active and current.state != "paused":
            return False
        return await cls._move(
            thread_id,
            task.id,
            state="working",
            objective=prompt("goal/objective", request=request),
            check="due",
            from_state=None if current is None else current.state,
        )

    @classmethod
    async def rewritten(cls, thread_id: str, objective: str, *, by: UUID | None = None) -> None:
        """The agent, or the person ``by``, rewrote the goal's objective. The agent's rewrite
        changes only the objective, so a move that lands meanwhile, such as a stop or a
        report, stands; the person's puts the goal back to work, as a message from them would."""
        task = await cls._task(thread_id)
        if task is None or not await Task.rewrite_goal(
            task.id, objective, by=by, resume=by is not None
        ):
            raise ValueError("This thread has no goal to rewrite; Goal mode sets one")
        await Topic.THREAD_GOALS.invalidate(key=thread_id)

    @classmethod
    async def edited_by_person(cls, thread_id: str, objective: str, *, by: User) -> None:
        """The person ``by`` rewrote the goal: the agent stops what it is doing and is told
        the new goal at once. What the old goal scheduled to wake it is dropped."""
        from openswe.tools.schedule_thread_wakeup import cancel_thread_wakeups

        await cls.rewritten(thread_id, objective, by=by.id)
        try:
            await cancel_thread_wakeups(thread_id)
        except Exception:
            logger.exception(
                "Could not drop the old goal's wakeups", extra={"agent_thread_id": thread_id}
            )
        thread = await THREADS.get(thread_id)
        if thread is None:
            raise ValueError("This thread no longer exists")
        configurable = {
            **follow_up_configurable(thread.metadata, thread_id),
            "goal_continuation": True,
        }
        try:
            await dispatch_agent_run(
                thread_id,
                prompt("goal/edited", objective=objective),
                configurable,
                source=str(configurable.get("source") or "dashboard"),
                thread_title=None,
                context={"sender_id": _EDIT_SENDER, "surface": "automation", "kind": "system"},
                systems=[{"id": _EDIT_SENDER, "display_name": "Goal edit", "platform": "open-swe"}],
                multitask_strategy="interrupt",
                source_context=SourceContext.from_metadata(thread.metadata),
            )
        except Exception as exc:
            logger.exception(
                "Saved a goal edit but could not hand it to the agent",
                extra={"agent_thread_id": thread_id},
            )
            await cls.failed(thread_id)
            raise GoalNotDelivered from exc

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
    async def set_state(
        cls,
        thread_id: str,
        expected: GoalState,
        state: Literal["working", "waiting_on_user", "paused", "complete"],
        reason: UserWaitReason | None = None,
    ) -> GoalStateOutcome:
        """The agent moved its goal on from ``expected``: to wait on its person, blocked or
        with the work ready; paused because they asked, after which only they resume it;
        working again from a pause they asked to end; or complete.

        ``stale``, leaving the goal alone, once it is no longer ``expected`` or its person
        stopped it: what moved it since the agent last looked wins. ``check`` the first
        time an explicitly set goal or one whose work edited files is called complete:
        it stays as it is until the agent checked its work against the objective and
        calls it complete again.
        """
        task = await cls._task(thread_id)
        if task is None or task.goal_state is None:
            raise ValueError("This thread has no goal; goal_create sets one")
        if state == "working" and expected != "paused":
            raise ValueError("Only a paused goal resumes; any other keeps working on its own")
        if task.goal_state != expected:
            return "stale"
        if state == "complete" and task.goal_check == "due":
            asked = await Task.move_goal_check(thread_id, to="asked", from_checks=("due",))
            return "check" if asked else "stale"
        if state == "paused":
            moved = await cls._pause(
                thread_id, task.id, "requested", over_stop=False, from_state=expected
            )
        elif state == "working":
            moved = await cls._move(thread_id, task.id, state="working", from_state="paused")
        elif state == "complete":
            moved = await cls._settle(thread_id, task.id, from_state=expected)
        else:
            moved = await cls._move(
                thread_id,
                task.id,
                state="waiting_on_user",
                reason=reason,
                over_stop=False,
                from_state=expected,
            )
        return "moved" if moved else "stale"

    @classmethod
    async def edited(cls, thread_id: str) -> None:
        """The thread's agent edited a file or opened a pull request: a goal underway owes
        a completion check again before it completes."""
        await Task.move_goal_check(thread_id, to="due", from_checks=(None, "asked"))

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
            if task is not None and task.goal_state not in {None, "complete"}:
                person = await user_for_login(by) if by else None
                await cls._pause(
                    thread_id, task.id, "stopped", by=None if person is None else person.id
                )
        except Exception:
            logger.exception(
                "Could not pause the goal of a stopped thread", extra={"agent_thread_id": thread_id}
            )

    @classmethod
    async def quiets_slack_thread(
        cls, owner: str, repo: str, number: int, location: tuple[str, str]
    ) -> bool:
        """Whether the Slack thread at ``location`` belongs to the goal the pull request's
        thread works toward, so nothing else speaks there for the PR until the goal says
        so. A goal paused or complete leaves it to speak."""
        from openswe.github.pull_requests import PullRequest
        from openswe.source_context import SourceContext

        pull_request = await PullRequest.get(owner, repo, number)
        thread_id = (
            None
            if pull_request is None
            else pull_request.primary_thread_id or pull_request.agent_thread_id
        )
        goal = None if thread_id is None else await cls.current(thread_id)
        if thread_id is None or goal is None or goal.state in {"paused", "complete"}:
            return False
        thread = await THREADS.get(thread_id)
        return (
            thread is not None
            and SourceContext.from_metadata(thread.metadata).slack_location == location
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
        """The goal as the dashboard shows it, without changing it: one resting on waits
        that have all lapsed shows as stalled until the person writes again."""
        goal = await cls.current(thread_id)
        if goal is None or goal.state != "waiting":
            return goal
        if waits := await cls.waits(thread_id):
            return goal.model_copy(update={"waits": waits})
        return goal.model_copy(update={"state": "paused", "reason": "no_progress", "waits": []})

    @classmethod
    async def for_run(
        cls,
        thread_id: str,
        *,
        sender: str | None,
        sender_goal_mode: bool,
        worker: bool,
        continues_goal: bool = False,
    ) -> bool:
        """Whether a run works in Goal mode: for its sender's Goal mode by default, or for
        a goal that is not yet complete (a paused one resumes on the person's message). A
        wake no person sent, or a run that continues the goal, follows the goal alone."""
        if worker:
            return False
        goal = await cls.current(thread_id)
        active = goal is not None and goal.active
        return active if not sender or continues_goal else sender_goal_mode or active

    @staticmethod
    async def owner_as(thread_id: str, login: str) -> User | None:
        """The person logged in as ``login``, when they own the thread."""
        from openswe.tasks.schemas import ThreadMetadata, user_for_login

        thread = await THREADS.get(thread_id)
        if thread is None:
            return None
        try:
            owner = await ThreadMetadata.model_validate(thread.metadata).owner()
        except PermissionError:
            return None
        person = await user_for_login(login)
        return person if person is not None and person.id == owner.id else None

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
        from_state: GoalState | None = None,
    ) -> bool:
        """Stop pushing the work on; what it scheduled to wake it is dropped."""
        from openswe.tools.schedule_thread_wakeup import cancel_thread_wakeups

        moved = await cls._move(
            thread_id,
            task_id,
            state="paused",
            reason=reason,
            by=by,
            over_stop=over_stop,
            from_state=from_state,
        )
        if moved:
            await cancel_thread_wakeups(thread_id)
        return moved

    @classmethod
    async def _settle(cls, thread_id: str, task_id: UUID, *, from_state: GoalState) -> bool:
        """The work is done; what it scheduled to wake it is dropped."""
        from openswe.tools.schedule_thread_wakeup import cancel_thread_wakeups

        moved = await cls._move(
            thread_id,
            task_id,
            state="complete",
            over_stop=False,
            from_state=from_state,
            checked=True,
        )
        if moved:
            await cancel_thread_wakeups(thread_id)
        return moved

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


@router.patch("/threads/{thread_id}/goal")
@audit_endpoint
async def api_edit_thread_goal(
    thread_id: str, body: GoalEdit, session: dict[str, str] = SESSION_DEP
) -> Goal | None:
    """Rewrite the goal's objective, for the thread's owner: the agent is interrupted and told."""
    await _readable_thread_metadata(thread_id, login=session["sub"], email=session.get("email"))
    person = await ThreadGoals.owner_as(thread_id, session["sub"])
    if person is None:
        raise HTTPException(403, "Only the thread's owner can edit its goal")
    objective = body.objective.strip()
    if not objective:
        raise HTTPException(422, "The goal must not be empty")
    try:
        await ThreadGoals.edited_by_person(thread_id, objective, by=person)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    except GoalNotDelivered as exc:
        raise HTTPException(
            502,
            "Saved the goal, but couldn't start the agent on it; send a message to resume",
        ) from exc
    return await ThreadGoals.shown(thread_id)
