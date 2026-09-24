"""The author's steering of an Open SWE pull request, and what the review scout made of it.

Open SWE already records every human turn: a thread's checkpointed ``messages``
hold the text and the ``<input-message>`` envelope that says whether a person
typed it or the platform generated it, and ``pull_request_thread`` maps a PR
back to the agent threads that produced it. :class:`SteeringHistory` reads those
turns so they can be put in front of the review scout.

Reading them through ``threads.get_state`` rather than the transcript tables is
deliberate: the checkpoint is the stable record. Compaction never costs a turn,
because deepagents keeps ``state["messages"]`` intact and tracks the summary
beside it.

Deciding which of them *changed the pull request* needs the code, not the
conversation — a reply saying "drop the retry wrapper" only counts if the
wrapper is actually gone. The review scout reads the whole change before the
reviewer does, so it makes that call through ``record_guidance``, each verdict
becomes a :class:`GuidancePoint` row, and the reviewer checks the recorded
points are carried through.

The quote is a point's identity and the scouted head is its scope. A scout on a
later push re-reads the same messages and re-derives points it already
recorded, so writing by quote keeps the newest verdict instead of accumulating a
row per push, and carries that row's head forward. Reads then show only the
head the newest finished scout stands behind, so a point it no longer
recognises stops being shown without anything having to delete it.
"""

import hashlib
import html
import logging
import re
from collections.abc import Mapping
from datetime import datetime
from typing import Any, Self
from uuid import UUID, uuid7

from pydantic import BaseModel, ConfigDict
from sqlalchemy import ForeignKey, delete, func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column, relationship

from agent.database import postgres
from agent.database.orm import NOW, Base
from agent.github.pull_requests import PullRequest
from agent.github.repositories import Repository
from agent.input_messages import input_message_text, message_sender_id
from agent.utils import ttl_cache
from agent.utils.thread_ops import langgraph_client

logger = logging.getLogger(__name__)

GUIDANCE_CAP = 8
MAX_FOLLOW_UPS = 40
MAX_MESSAGE_CHARS = 4_000
# Long enough that one reviewer run reads each thread's state once, short enough
# that a later run in the same process sees turns added since.
_STEERING_CACHE_SECONDS = 300
# Keeps a message's own text from closing the block it is quoted in.
_CLOSING_MESSAGE_TAG_RE = re.compile(r"</\s*(author_messages|message)\s*>", re.IGNORECASE)


class _StateMessage(BaseModel):
    """One entry of a thread's checkpointed ``messages``, as the SDK returns it."""

    model_config = ConfigDict(extra="ignore")

    type: str = ""
    # Absent on a message written straight into state rather than through a run.
    id: str | None = None
    content: str | list[dict[str, Any]] = ""


class HumanTurn(BaseModel):
    """One message a person actually typed into a thread behind this PR."""

    thread_id: str
    message_id: str
    author: str
    text: str
    index: int = 0


class SteeringHistory(BaseModel):
    """The opening ask, and every human turn after it."""

    request: HumanTurn
    follow_ups: list[HumanTurn]

    @classmethod
    async def load(cls, owner: str, repo: str, pr_number: int) -> Self | None:
        """This PR's human turns, or ``None`` when Open SWE did not write it.

        A PR the agent produced in one pass has an opening request and nothing
        after it, which is no steering at all.

        Cached briefly: a reviewer run reads this once to build its prompt and
        again for every point it records, and each read is a whole thread state.
        """
        return await ttl_cache.cached(
            f"guidance:steering:{owner}/{repo}#{pr_number}".lower(),
            _STEERING_CACHE_SECONDS,
            lambda: cls._load(owner, repo, pr_number),
        )

    @classmethod
    async def _load(cls, owner: str, repo: str, pr_number: int) -> Self | None:
        pull_request = await PullRequest.get(owner, repo, pr_number)
        if pull_request is None:
            return None
        thread_ids = await pull_request.linked_threads()
        if not thread_ids:
            return None
        turns = [turn for thread_id in thread_ids for turn in await cls._human_turns(thread_id)]
        if len(turns) < 2:
            return None
        follow_ups = [
            turn.model_copy(update={"index": index})
            for index, turn in enumerate(turns[1:][-MAX_FOLLOW_UPS:])
        ]
        return cls(request=turns[0], follow_ups=follow_ups)

    @staticmethod
    async def _human_turns(thread_id: str) -> list[HumanTurn]:
        """Every message a person typed into one thread, oldest first.

        A ``human`` message covers platform-generated wake-ups too — they ride
        the same channel — so the envelope decides: only a ``kind="human"``
        ``<input-message>`` was typed by someone. The sender id names them
        (``github:<login>``), and the checkpoint keeps the messages in the order
        they arrived, which is the order the steering happened.
        """
        try:
            state = await langgraph_client().threads.get_state(thread_id)
        except Exception:
            logger.warning(
                "Could not read thread state for steering history",
                exc_info=True,
                extra={"steering_thread_id": thread_id},
            )
            return []
        values = state.get("values") if isinstance(state, Mapping) else None
        raw = values.get("messages") if isinstance(values, Mapping) else None
        turns: list[HumanTurn] = []
        for entry in raw if isinstance(raw, list) else []:
            message = _StateMessage.model_validate(entry)
            if message.type != "human":
                continue
            sender = message_sender_id(message.content, kind="human")
            if sender is None:
                continue
            body = (input_message_text(message.content) or "").strip()
            if not body:
                continue
            turns.append(
                HumanTurn(
                    thread_id=thread_id,
                    message_id=message.id or "",
                    author=sender.split(":", 1)[-1] or "unknown",
                    text=body[:MAX_MESSAGE_CHARS],
                )
            )
        return turns

    def messages_block(self) -> str:
        """The follow-up messages as ``<message author="...">`` entries, oldest first."""
        return "\n".join(
            f'<message author="{html.escape(turn.author)}">\n'
            f"{_CLOSING_MESSAGE_TAG_RE.sub(lambda m: f'</{m.group(1)}_>', turn.text)}\n"
            "</message>"
            for turn in self.follow_ups
        )

    def source_of(self, quote: str) -> HumanTurn | None:
        """The message a quote came from, or ``None`` when it matches nothing."""
        needle = quote.strip().strip('"').lower()
        if not needle:
            return None
        return next((turn for turn in self.follow_ups if needle in turn.text.lower()), None)


class GuidanceReview(Base):
    """The commit the last finished review scout of a pull request stands behind."""

    __tablename__ = "pull_request_guidance_review"

    pull_request_id: Mapped[UUID] = mapped_column(
        ForeignKey("pull_request.id", ondelete="CASCADE"), primary_key=True
    )
    head_sha: Mapped[str]
    completed_at: Mapped[datetime | None] = mapped_column(server_default=NOW, init=False)

    @classmethod
    async def carry_forward(
        cls, owner: str, repo: str, pr_number: int, *, from_sha: str, to_sha: str
    ) -> None:
        """Re-pin the points and the card to a new head whose diff is identical to the old one."""
        pull_request = await PullRequest.get(owner, repo, pr_number)
        if pull_request is None or pull_request.id is None:
            return
        async with postgres.session() as session:
            for table in (cls, GuidancePoint):
                await session.execute(
                    update(table)
                    .where(table.pull_request_id == pull_request.id, table.head_sha == from_sha)
                    .values(head_sha=to_sha)
                )
            await session.commit()

    @classmethod
    async def complete(cls, owner: str, repo: str, pr_number: int, head_sha: str) -> None:
        """Mark a scout of this head finished, whatever it decided.

        A scout that recognises no guidance still settles the question of what
        is true at this commit, and only this row can say so, because that
        outcome writes no point to infer it from.

        A failure here is logged rather than raised: it is bookkeeping for the
        guidance card, and must not cost the scout its walkthrough.
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
    turn_index: Mapped[int | None] = mapped_column(default=None)
    reviewer_thread_id: Mapped[str] = mapped_column(server_default="", default="")
    head_sha: Mapped[str] = mapped_column(server_default="", default="")
    recorded_at: Mapped[datetime | None] = mapped_column(server_default=NOW, init=False)
    pull_request: Mapped[PullRequest] = relationship(init=False)

    @staticmethod
    def hash_quote(quote: str) -> str:
        return hashlib.sha256(quote.strip().lower().encode()).hexdigest()

    @classmethod
    async def for_pull_request(cls, owner: str, repo: str, pr_number: int) -> list[Self]:
        """Points the last finished scout stands behind, oldest steering first.

        Scoped to that scout's head rather than to the pull request, because a
        point is only ever a claim about one commit. A later scout that no
        longer recognises a point does not re-record it, so the row keeps the
        older head and drops out here — including when the new scout recognises
        nothing at all, which no reconciliation triggered by a write could
        cover. A re-recorded point carries its row forward to the new head, so
        the visible set is always exactly what the last scout would say.
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
                .order_by(cls.turn_index, cls.id)
            )
            return list(rows)

    @classmethod
    async def for_head(cls, owner: str, repo: str, pr_number: int, head_sha: str) -> list[Self]:
        """Points recorded against ``head_sha`` that trace to a stored message, oldest first."""
        async with postgres.session() as session:
            rows = await session.scalars(
                select(cls)
                .join(cls.pull_request)
                .join(PullRequest.repository)
                .where(
                    Repository.key == f"{owner}/{repo}".lower(),
                    PullRequest.number == pr_number,
                    cls.head_sha == head_sha,
                    cls.turn_index.is_not(None),
                )
                .order_by(cls.turn_index, cls.id)
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
        turn_index: int | None,
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
            "turn_index": turn_index,
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
                        "turn_index": func.coalesce(statement.excluded.turn_index, cls.turn_index),
                        # Reaffirming a point is recording it. Without this the
                        # row keeps the timestamp of the review that first saw
                        # it, and the cap can drop something this very run
                        # confirmed in favour of something it recorded a moment
                        # later.
                        "recorded_at": func.clock_timestamp(),
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

    @classmethod
    async def for_head(cls, owner: str, repo: str, pr_number: int, head_sha: str) -> list[Self]:
        """The points the review scout recorded for ``head_sha``."""
        if not head_sha or not postgres.configured():
            return []
        return [
            cls.of(point)
            for point in await GuidancePoint.for_head(owner, repo, pr_number, head_sha)
        ]
