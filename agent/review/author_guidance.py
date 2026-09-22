"""The author's steering of an Open SWE pull request, and what the reviewer made of it.

Open SWE already records every human turn: ``thread_message`` holds the text and
the ``<input-message>`` envelope that says whether a person typed it or the
platform generated it, and ``pull_request_thread`` maps a PR back to the agent
threads that produced it. :class:`SteeringHistory` reads those turns so they can
be put in front of the reviewer.

Deciding which of them *changed the pull request* needs the code, not the
conversation — a reply saying "drop the retry wrapper" only counts if the
wrapper is actually gone. The reviewer is the one process holding the diff and
the checked-out repo, so it makes that call itself through ``record_guidance``
and each verdict becomes a :class:`GuidancePoint` row.

The quote is a point's identity and the reviewed head is its scope. A re-review
re-reads the same messages and re-derives points it already recorded, so writing
by quote keeps the newest verdict instead of accumulating a row per push, and
carries that row's head forward. Reads then show only the head the newest
published review stands behind, so a point the latest review no longer
recognises stops being shown without anything having to delete it.
"""

import hashlib
import logging
from datetime import datetime
from typing import Self
from uuid import UUID, uuid7

from pydantic import BaseModel
from sqlalchemy import ARRAY, ForeignKey, Text, bindparam, delete, func, select, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column, relationship

from agent.database import postgres
from agent.database.orm import NOW, Base
from agent.github.pull_requests import PullRequest
from agent.github.repositories import Repository
from agent.input_messages import input_message_text, message_sender_id

logger = logging.getLogger(__name__)

GUIDANCE_CAP = 8
MAX_FOLLOW_UPS = 40
MAX_MESSAGE_CHARS = 4_000


class HumanTurn(BaseModel):
    """One message a person actually typed into a thread behind this PR."""

    thread_id: str
    message_id: str
    author: str
    text: str
    created_at: datetime


class SteeringHistory(BaseModel):
    """The opening ask, and every human turn after it."""

    request: HumanTurn
    follow_ups: list[HumanTurn]

    @classmethod
    async def load(cls, owner: str, repo: str, pr_number: int) -> Self | None:
        """This PR's human turns, or ``None`` when Open SWE did not write it.

        A PR the agent produced in one pass has an opening request and nothing
        after it, which is no steering at all.
        """
        pull_request = await PullRequest.get(owner, repo, pr_number)
        if pull_request is None:
            return None
        thread_ids = await pull_request.linked_threads()
        if not thread_ids:
            return None
        turns = await cls._human_turns(thread_ids)
        if len(turns) < 2:
            return None
        return cls(request=turns[0], follow_ups=turns[1:][-MAX_FOLLOW_UPS:])

    @staticmethod
    async def _human_turns(thread_ids: list[str]) -> list[HumanTurn]:
        """Every message a person typed across these threads, oldest first.

        ``role = 'human'`` covers platform-generated wake-ups too — they ride
        the same channel — so the envelope decides: only a ``kind="human"``
        ``<input-message>`` was typed by someone.
        """
        statement = text(
            """
            SELECT thread_id, message_id, text, sender, created_at
            FROM thread_message
            WHERE thread_id = ANY(:thread_ids) AND role = 'human'
            ORDER BY created_at, message_id
            """
        ).bindparams(bindparam("thread_ids", type_=ARRAY(Text)))
        async with postgres.snapshot_transaction() as conn:
            rows = (await conn.execute(statement, {"thread_ids": thread_ids})).mappings().all()
        turns: list[HumanTurn] = []
        for row in rows:
            raw = row["text"]
            if not isinstance(raw, str) or message_sender_id(raw, kind="human") is None:
                continue
            body = (input_message_text(raw) or "").strip()
            if not body:
                continue
            sender = row["sender"] if isinstance(row["sender"], dict) else {}
            login = sender.get("login")
            turns.append(
                HumanTurn(
                    thread_id=row["thread_id"],
                    message_id=row["message_id"],
                    author=login if isinstance(login, str) and login else "unknown",
                    text=body[:MAX_MESSAGE_CHARS],
                    created_at=row["created_at"],
                )
            )
        return turns

    def source_of(self, quote: str) -> HumanTurn | None:
        """The message a quote came from, or ``None`` when it matches nothing."""
        needle = quote.strip().strip('"').lower()
        if not needle:
            return None
        return next((turn for turn in self.follow_ups if needle in turn.text.lower()), None)


class GuidanceReview(Base):
    """The commit the last completed review of a pull request stands behind."""

    __tablename__ = "pull_request_guidance_review"

    pull_request_id: Mapped[UUID] = mapped_column(
        ForeignKey("pull_request.id", ondelete="CASCADE"), primary_key=True
    )
    head_sha: Mapped[str]
    completed_at: Mapped[datetime | None] = mapped_column(server_default=NOW, init=False)

    @classmethod
    async def complete(cls, owner: str, repo: str, pr_number: int, head_sha: str) -> None:
        """Mark a review of this head finished, whatever it decided.

        Called on every path that finishes a review, including the one that
        publishes nothing: a review that recognises no guidance still settles
        the question of what is true at this commit, and only this row can say
        so, because that outcome writes no point to infer it from.

        A failure here is logged rather than raised. The review has already
        been published to GitHub by the time this runs, and turning a
        bookkeeping write into a tool error would have the agent retry the
        publish.
        """
        if not head_sha:
            return
        try:
            pull_request = await PullRequest.get(owner, repo, pr_number)
            if pull_request is None or pull_request.id is None:
                return
            statement = insert(cls).values(pull_request_id=pull_request.id, head_sha=head_sha)
            async with postgres.session() as session:
                await session.execute(
                    statement.on_conflict_do_update(
                        index_elements=[cls.pull_request_id],
                        set_={"head_sha": head_sha, "completed_at": func.clock_timestamp()},
                    )
                )
                await session.commit()
        except Exception:
            logger.warning(
                "Could not advance the guidance review head",
                exc_info=True,
                extra={"pr_repo_full_name": f"{owner}/{repo}", "pr_number": pr_number},
            )


class GuidancePoint(Base):
    """One steering turn the reviewer located in the final change."""

    __tablename__ = "pull_request_guidance"

    pull_request_id: Mapped[UUID] = mapped_column(ForeignKey("pull_request.id", ondelete="CASCADE"))
    quote: Mapped[str]
    summary: Mapped[str]
    id: Mapped[UUID] = mapped_column(primary_key=True, default_factory=uuid7)
    quote_hash: Mapped[str] = mapped_column(default="")
    author: Mapped[str] = mapped_column(server_default="", default="")
    occurred_at: Mapped[datetime | None] = mapped_column(default=None)
    reviewer_thread_id: Mapped[str] = mapped_column(server_default="", default="")
    head_sha: Mapped[str] = mapped_column(server_default="", default="")
    recorded_at: Mapped[datetime | None] = mapped_column(server_default=NOW, init=False)
    pull_request: Mapped[PullRequest] = relationship(init=False)

    @staticmethod
    def hash_quote(quote: str) -> str:
        return hashlib.sha256(quote.strip().lower().encode()).hexdigest()

    @classmethod
    async def for_pull_request(cls, owner: str, repo: str, pr_number: int) -> list[Self]:
        """Points the last completed review stands behind, oldest steering first.

        Scoped to that review's head rather than to the pull request, because a
        point is only ever a claim about one commit. A re-review that no longer
        recognises a point does not re-record it, so the row keeps the older
        head and drops out here — including when the new review recognises
        nothing at all, which no reconciliation triggered by a write could
        cover. A re-recorded point carries its row forward to the new head, so
        the visible set is always exactly what the last review would say.
        """
        latest_head = (
            select(GuidanceReview.head_sha)
            .where(GuidanceReview.pull_request_id == cls.pull_request_id)
            .scalar_subquery()
        )
        async with postgres.session() as session:
            rows = await session.scalars(
                select(cls)
                .join(cls.pull_request)
                .join(PullRequest.repository)
                .where(
                    Repository.key == f"{owner}/{repo}".lower(),
                    PullRequest.number == pr_number,
                    cls.head_sha == latest_head,
                )
                .order_by(cls.occurred_at, cls.id)
            )
            return list(rows)

    @classmethod
    async def record(
        cls,
        pull_request: PullRequest,
        *,
        summary: str,
        quote: str,
        author: str,
        occurred_at: datetime | None,
        reviewer_thread_id: str,
        head_sha: str,
    ) -> int:
        """Write one point, replacing any earlier record of the same quote.

        Returns how many points this pull request now carries, so the caller can
        tell the reviewer it is approaching the cap.
        """
        if pull_request.id is None:
            raise ValueError("pull request must be saved before guidance is recorded")
        values = {
            "id": uuid7(),
            "pull_request_id": pull_request.id,
            "quote_hash": cls.hash_quote(quote),
            "quote": quote,
            "summary": summary,
            "author": author,
            "occurred_at": occurred_at,
            "reviewer_thread_id": reviewer_thread_id,
            "head_sha": head_sha,
        }
        statement = insert(cls).values(**values)
        async with postgres.session() as session:
            await session.execute(
                statement.on_conflict_do_update(
                    index_elements=[cls.pull_request_id, cls.quote_hash],
                    set_={
                        **{
                            key: values[key]
                            for key in ("summary", "reviewer_thread_id", "head_sha")
                        },
                        # Attribution is matched against the stored turns, which
                        # a later run may fail to reach. Keeping the earlier
                        # answer beats replacing a name with a blank.
                        "author": func.coalesce(
                            func.nullif(statement.excluded.author, ""), cls.author
                        ),
                        "occurred_at": func.coalesce(
                            statement.excluded.occurred_at, cls.occurred_at
                        ),
                    },
                )
            )
            await cls._trim(session, pull_request.id)
            count = len(
                (
                    await session.scalars(
                        select(cls.id).where(cls.pull_request_id == pull_request.id)
                    )
                ).all()
            )
            await session.commit()
            return count

    @classmethod
    async def _trim(cls, session: AsyncSession, pull_request_id: UUID) -> None:
        """Keep the newest ``GUIDANCE_CAP`` records, so one run cannot flood the card."""
        keep = select(cls.id).where(cls.pull_request_id == pull_request_id)
        keep = keep.order_by(cls.recorded_at.desc(), cls.id.desc()).limit(GUIDANCE_CAP)
        await session.execute(
            delete(cls).where(
                cls.pull_request_id == pull_request_id,
                cls.id.not_in(keep.scalar_subquery()),
            )
        )


class GuidanceView(BaseModel):
    """One recorded point, as the dashboard reads it."""

    summary: str
    quote: str
    author: str

    @classmethod
    def of(cls, point: GuidancePoint) -> Self:
        return cls(summary=point.summary, quote=point.quote, author=point.author)

    @classmethod
    async def for_pull_request(cls, owner: str, repo: str, pr_number: int) -> list[Self]:
        """The recorded points, or none at all where there is no database to hold them.

        The review page renders for deployments with no ``POSTGRES_URI`` and for
        pull requests no reviewer has touched; neither is a failure to report.
        """
        if not postgres.configured():
            return []
        return [
            cls.of(point) for point in await GuidancePoint.for_pull_request(owner, repo, pr_number)
        ]
