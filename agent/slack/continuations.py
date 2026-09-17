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

from sqlalchemy import Boolean, Text, case, or_, select, update
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
    # Slack's message timestamp is unknown until the message is posted, and an
    # ephemeral message never gets one, so exclusivity is keyed on a group the
    # reply path mints instead.
    group_id: Mapped[UUID] = mapped_column(default_factory=uuid7)
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


async def peek(token: UUID) -> SlackContinuation | None:
    """The open, unexpired row for `token`, consuming nothing.

    Authorization needs the thread the row names, and an unauthorized click
    must not spend the answer the owner is still expected to give, so the row
    is read before it is claimed.
    """
    async with postgres.session() as session:
        return await session.scalar(
            select(SlackContinuation).where(
                SlackContinuation.id == token,
                SlackContinuation.state == "open",
                SlackContinuation.expires_at > datetime.now(UTC),
            )
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
        # The clicked element and the alternatives it settles are closed by one
        # statement, over rows locked in id order. Two people clicking different
        # buttons on the same message would otherwise each hold their own row and
        # wait for the other's, which Postgres breaks by killing one as a
        # deadlock victim; a single ordered lock cannot deadlock against itself.
        locked = (
            select(SlackContinuation.id)
            .where(
                SlackContinuation.state == "open",
                SlackContinuation.single_use.is_(True),
                or_(
                    SlackContinuation.id == token,
                    SlackContinuation.group_id == row.group_id,
                ),
            )
            .order_by(SlackContinuation.id)
            .with_for_update()
            .subquery()
        )
        spent = list(
            await session.scalars(
                update(SlackContinuation)
                .where(SlackContinuation.id.in_(select(locked.c.id)))
                .values(
                    state=case((SlackContinuation.id == token, "used"), else_="revoked"),
                    used_by=case((SlackContinuation.id == token, slack_user_id), else_=""),
                    used_at=case((SlackContinuation.id == token, now), else_=None),
                    updated_at=now,
                )
                .returning(SlackContinuation.id)
            )
        )
        if token not in spent:
            return None
        return Claim(
            row=row,
            spent_action_ids=frozenset(action_id_for(item) for item in spent),
        )
