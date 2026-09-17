"""Continuations: a Block Kit element whose click resumes the thread that posted it.

Slack sends a click to one endpoint with nothing but an ``action_id`` and the
message it came from, so an element is only answerable if the run that drew it
left behind what to do about it. A continuation is that record: one row per
interactive element, keyed by the opaque token the element carries as its
``action_id``, holding the agent thread to resume and the configurable to
resume it with.

Rows are claimed, not merely read. Claiming a single-use element flips it to
``used`` in one statement and revokes the elements posted beside it, so a
double-click, two people racing, or a redelivered interaction resumes the
thread exactly once. Everything expires: a button on a month-old message must
not wake a thread whose sandbox and branch are long gone.
"""

import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Literal
from uuid import UUID, uuid7

from sqlalchemy import Boolean, Text, select, update
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from agent.database import postgres
from agent.database.orm import NOW, Base
from agent.utils.json_types import JsonObject

logger = logging.getLogger(__name__)

ContinuationState = Literal["open", "used", "revoked"]

ACTION_ID_PREFIX = "open_swe_continuation_"
DEFAULT_TTL = timedelta(days=7)


def action_id_for(token: UUID) -> str:
    """The ``action_id`` an element carries so a click can find its row."""
    return f"{ACTION_ID_PREFIX}{token.hex}"


def token_in(action_id: object) -> UUID | None:
    """The continuation token in ``action_id``, or None when it carries none."""
    if not isinstance(action_id, str) or not action_id.startswith(ACTION_ID_PREFIX):
        return None
    try:
        return UUID(hex=action_id.removeprefix(ACTION_ID_PREFIX))
    except ValueError:
        return None


class SlackContinuation(Base):
    __tablename__ = "slack_continuation"

    thread_id: Mapped[str]
    action_id: Mapped[str]
    element_type: Mapped[str]
    channel_id: Mapped[str]
    id: Mapped[UUID] = mapped_column(primary_key=True, default_factory=uuid7)
    label: Mapped[str] = mapped_column(server_default="", default="")
    thread_ts: Mapped[str] = mapped_column(server_default="", default="")
    message_ts: Mapped[str] = mapped_column(server_default="", default="")
    run_config: Mapped[JsonObject] = mapped_column(JSONB, default_factory=dict)
    single_use: Mapped[bool] = mapped_column(Boolean, server_default="true", default=True)
    state: Mapped[ContinuationState] = mapped_column(Text, default="open")
    used_by: Mapped[str] = mapped_column(server_default="", default="")
    used_at: Mapped[datetime | None] = mapped_column(default=None)
    expires_at: Mapped[datetime] = mapped_column(
        default_factory=lambda: datetime.now(UTC) + DEFAULT_TTL
    )
    created_at: Mapped[datetime | None] = mapped_column(server_default=NOW, init=False)
    updated_at: Mapped[datetime | None] = mapped_column(server_default=NOW, init=False)


async def save(rows: list[SlackContinuation]) -> None:
    """Persist freshly built continuations."""
    if not rows:
        return
    async with postgres.session() as session:
        session.add_all(rows)


async def attach_message(tokens: list[UUID], channel_id: str, message_ts: str) -> None:
    """Record which message the elements landed on, known only after posting."""
    if not tokens or not message_ts:
        return
    async with postgres.session() as session:
        await session.execute(
            update(SlackContinuation)
            .where(SlackContinuation.id.in_(tokens))
            .values(channel_id=channel_id, message_ts=message_ts, updated_at=datetime.now(UTC))
        )


@dataclass(frozen=True)
class Claim:
    """A taken click, and the ``action_id`` values it just spent."""

    row: SlackContinuation
    spent_action_ids: frozenset[str]


async def claim(token: UUID, *, slack_user_id: str) -> Claim | None:
    """Take ownership of one click, or None when there is nothing left to take.

    A single-use element is flipped to ``used`` by the same statement that
    selects it, so only the first of two concurrent clicks gets a row back.
    """
    now = datetime.now(UTC)
    async with postgres.session() as session:
        row = await session.scalar(
            select(SlackContinuation).where(
                SlackContinuation.id == token,
                SlackContinuation.state == "open",
                SlackContinuation.expires_at > now,
            )
        )
        if row is None:
            return None
        if not row.single_use:
            return Claim(row=row, spent_action_ids=frozenset())
        # `state = 'open'` in the predicate is the gate, and RETURNING says
        # whether this statement is the one that closed it.
        claimed = await session.scalar(
            update(SlackContinuation)
            .where(SlackContinuation.id == token, SlackContinuation.state == "open")
            .values(state="used", used_by=slack_user_id, used_at=now, updated_at=now)
            .returning(SlackContinuation.id)
        )
        if claimed is None:
            return None
        # The other single-use elements on that message offered alternatives to
        # this one answer, so they stop being clickable with it. A reusable one
        # is left alone, in the row and in the message.
        revoked: list[UUID] = []
        if row.message_ts:
            revoked = list(
                await session.scalars(
                    update(SlackContinuation)
                    .where(
                        SlackContinuation.channel_id == row.channel_id,
                        SlackContinuation.message_ts == row.message_ts,
                        SlackContinuation.id != token,
                        SlackContinuation.single_use.is_(True),
                        SlackContinuation.state == "open",
                    )
                    .values(state="revoked", updated_at=now)
                    .returning(SlackContinuation.id)
                )
            )
        return Claim(
            row=row,
            spent_action_ids=frozenset(action_id_for(spent) for spent in [token, *revoked]),
        )
