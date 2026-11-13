"""A person's message to an agent thread, recorded the moment a run takes it.

Each step-in on the thread's work writes one ``human_interjection`` feedback row on the
thread's traces, valued by how it arrived:

- ``superseded``: the person's message cut off a run that had been working a while.
- ``steered``: a model call took the person's message once the run's model had answered.
- ``model_pick``: the person picked a model, or Auto, for a follow-up.

A message that starts a run after the previous turn completed writes one
``human_follow_up`` row, naming the run whose reply it answers.

Only what a person typed on the web, in Slack or on GitHub counts. Nothing here reads
the routing experiment's arm, so both arms record the same rows.
"""

import asyncio
import logging
from collections.abc import Coroutine, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import ClassVar, Final, Literal
from uuid import UUID

from langchain_core.messages import BaseMessage
from runtime_api.provenance import Provenance
from sqlalchemy import RowMapping, text

from openswe.database import postgres
from openswe.run_config import RunConfig
from openswe.threads.stalls import STALLED_AFTER
from openswe.threads.store import THREADS
from openswe.utils.langsmith import create_langsmith_thread_feedback

logger = logging.getLogger(__name__)

FEEDBACK_KEY: Final = "human_interjection"
FOLLOW_UP_KEY: Final = "human_follow_up"
# A message that cuts off a run sooner than this is a double send, not a step-in.
SUPERSEDE_AFTER: Final = timedelta(seconds=10)
_TYPED_SURFACES: Final = frozenset({"web", "slack", "github"})

type Arrival = Literal["superseded", "steered", "model_pick"]


@dataclass(frozen=True)
class RunInterjections:
    """How a person's message reached one run of an agent thread."""

    thread_id: str
    run_id: str
    _pending: ClassVar[set[asyncio.Task[None]]] = set()

    @staticmethod
    def typed_surface(message: BaseMessage | Mapping[str, object]) -> str | None:
        """Where a person typed ``message`` to Robin; None for anything else."""
        provenance = Provenance.of(message)
        if provenance is None or not provenance.human or provenance.surface not in _TYPED_SURFACES:
            return None
        return provenance.surface

    def steered(self, messages: Sequence[BaseMessage]) -> None:
        """Record each typed message a model call took after the run's model answered."""
        for message in messages:
            surface = self.typed_surface(message)
            if surface is not None and message.id:
                self._spawn(
                    self._write("steered", surface, message.id, self.run_id, event_id=message.id)
                )

    def started(self, humans: Sequence[BaseMessage], cfg: RunConfig) -> None:
        """Record whether the person who started the run cut off the previous turn or
        followed up its reply, and whether they picked its model: the dashboard's picker
        and Slack's ``/model:perf`` flag that, unlike the model they send with every
        message."""
        latest = next(
            (m for m in reversed(humans) if (p := Provenance.of(m)) is None or p.kind != "context"),
            None,
        )
        if latest is None or (surface := self.typed_surface(latest)) is None:
            return
        self._spawn(
            self._started(
                surface, latest.id, self._requested_at(), picked=cfg.model_selection_changed
            )
        )

    def _requested_at(self) -> datetime:
        """When the run was asked for: the Agent Server's run ids are UUIDv7s minted then."""
        try:
            run = UUID(self.run_id)
        except ValueError:
            return datetime.now(UTC)
        return (
            datetime.fromtimestamp(run.time / 1000, UTC) if run.version == 7 else datetime.now(UTC)
        )

    async def _started(
        self, surface: str, message_id: str | None, at: datetime, *, picked: bool
    ) -> None:
        previous = await self._previous_turn(at)
        if previous is None:
            # An opening message steps in on nothing.
            return
        cut_off = await self._cut_off(previous, at)
        interrupted = previous["run_id"] if cut_off else None
        if picked:
            self._spawn(
                self._write("model_pick", surface, message_id, interrupted, event_id=self.run_id)
            )
        if cut_off:
            self._spawn(
                self._write("superseded", surface, message_id, interrupted, event_id=self.run_id)
            )
        # A message queued while the turn worked was written before its reply ended.
        elif message_id and previous["state"] == "completed" and previous["completed_at"] <= at:
            self._spawn(self._follow_up(surface, message_id, previous, at))

    async def _previous_turn(self, at: datetime) -> RowMapping | None:
        """The thread's latest turn started before ``at``, with when it last did anything
        before then."""
        async with postgres.read_only_transaction() as conn:
            result = await conn.execute(
                text(
                    """
                    SELECT turn.run_id, turn.state, turn.started_at, turn.completed_at, (
                        SELECT max(event.occurred_at) FROM thread_event event
                        WHERE event.thread_id = turn.thread_id AND event.run_id = turn.run_id
                          AND event.occurred_at < :at
                    ) AS active_at
                    FROM thread_turn turn
                    WHERE turn.thread_id = :thread_id AND turn.run_id IS DISTINCT FROM :run_id
                      AND turn.started_at < :at
                    ORDER BY turn.started_at DESC
                    LIMIT 1
                    """
                ),
                {"thread_id": self.thread_id, "run_id": self.run_id, "at": at},
            )
            return result.mappings().one_or_none()

    async def _cut_off(self, previous: RowMapping, at: datetime) -> bool:
        """Whether a request at ``at`` cut off ``previous`` while it worked: it had worked
        a while and had not stalled, never ended or ended interrupted, and no one
        stopped it."""
        if previous["state"] not in {"running", "interrupted"}:
            return False
        started_at: datetime = previous["started_at"]
        if at - started_at < SUPERSEDE_AFTER:
            return False
        if at - (previous["active_at"] or started_at) > STALLED_AFTER:
            return False
        thread = await THREADS.get(self.thread_id)
        return thread is not None and not thread.stopped_since(int(started_at.timestamp() * 1000))

    async def _write(
        self,
        arrival: Arrival,
        surface: str,
        message_id: str | None,
        interrupted_run_id: str | None,
        *,
        event_id: str,
    ) -> None:
        await create_langsmith_thread_feedback(
            self.thread_id,
            FEEDBACK_KEY,
            score=1.0,
            value=arrival,
            event_id=f"{arrival}:{event_id}",
            source_info={
                "thread_id": self.thread_id,
                "run_id": self.run_id,
                "surface": surface,
                "message_id": message_id,
                "interrupted_run_id": interrupted_run_id,
            },
        )

    async def _follow_up(
        self, surface: str, message_id: str, answered: RowMapping, at: datetime
    ) -> None:
        await create_langsmith_thread_feedback(
            self.thread_id,
            FOLLOW_UP_KEY,
            score=1.0,
            event_id=message_id,
            source_info={
                "thread_id": self.thread_id,
                "run_id": self.run_id,
                "surface": surface,
                "message_id": message_id,
                "answered_run_id": answered["run_id"],
                "seconds_since_reply": (at - answered["completed_at"]).total_seconds(),
            },
        )

    def _spawn(self, work: Coroutine[object, object, None]) -> None:
        """Run ``work`` beside the run: a failed write never fails or slows it."""

        async def guarded() -> None:
            try:
                await work
            except Exception:
                logger.exception(
                    "Could not record feedback on a person's message",
                    extra={"thread_id": self.thread_id, "run_id": self.run_id},
                )

        task = asyncio.create_task(guarded())
        self._pending.add(task)
        task.add_done_callback(self._pending.discard)
