"""Trace Flight seasons and each person's best run in one (migration 0102)."""

from dataclasses import dataclass
from datetime import datetime
from typing import Self
from uuid import UUID, uuid7

from sqlalchemy import ARRAY, BigInteger, Integer, and_, func, or_, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Mapped, mapped_column

from openswe.database import postgres
from openswe.database.orm import NOW, Base
from openswe.trace_flight.core.replay import Run
from openswe.users.models import User

BOARD_SIZE = 5


class SeasonRow(Base):
    __tablename__ = "trace_flight_season"

    rules_version: Mapped[int]
    # uint32 courses outgrow integer.
    seed: Mapped[int] = mapped_column(BigInteger)
    id: Mapped[UUID] = mapped_column(primary_key=True, default_factory=uuid7)
    closed_at: Mapped[datetime | None] = mapped_column(default=None)
    created_at: Mapped[datetime | None] = mapped_column(server_default=NOW, init=False)
    updated_at: Mapped[datetime | None] = mapped_column(server_default=NOW, init=False)
    created_by: Mapped[UUID | None] = mapped_column(default=None)
    updated_by: Mapped[UUID | None] = mapped_column(default=None)


class BestRow(Base):
    __tablename__ = "trace_flight_best"

    season_id: Mapped[UUID]
    user_id: Mapped[UUID]
    score: Mapped[int]
    end_tick: Mapped[int]
    end_reason: Mapped[str]
    flap_deltas: Mapped[list[int]] = mapped_column(ARRAY(Integer))
    id: Mapped[UUID] = mapped_column(primary_key=True, default_factory=uuid7)
    created_at: Mapped[datetime | None] = mapped_column(server_default=NOW, init=False)
    # Rewritten only when the score improves, so it is also when the best was set.
    updated_at: Mapped[datetime | None] = mapped_column(server_default=NOW, init=False)
    created_by: Mapped[UUID | None] = mapped_column(default=None)
    updated_by: Mapped[UUID | None] = mapped_column(default=None)


class SeasonClosed(Exception):
    """The season a run was played in closed before the run could count."""


class NoOpenSeason(Exception):
    """No season is open, so no run can count."""


@dataclass(frozen=True, slots=True)
class BestRun:
    run: Run
    score: int


@dataclass(frozen=True, slots=True)
class LeaderRow:
    rank: int
    name: str
    score: int
    you: bool


@dataclass(frozen=True, slots=True)
class Recorded:
    previous_best: int | None
    new_best: bool


@dataclass(frozen=True, slots=True)
class Season:
    id: UUID
    rules_version: int
    seed: int

    @classmethod
    async def current(cls) -> Self:
        async with postgres.session() as session:
            row = await session.scalar(select(SeasonRow).where(SeasonRow.closed_at.is_(None)))
        if row is None:
            raise NoOpenSeason
        return cls(row.id, row.rules_version, row.seed)

    async def best_of(self, user_id: UUID) -> BestRun | None:
        async with postgres.session() as session:
            row = await session.scalar(
                select(BestRow).where(BestRow.season_id == self.id, BestRow.user_id == user_id)
            )
        if row is None:
            return None
        run = Run.parse(self.seed, row.end_tick, row.end_reason, list(row.flap_deltas))
        return BestRun(run, row.score)

    async def record(self, user_id: UUID, run: Run, score: int) -> Recorded:
        """Keeps ``run`` as this person's best if it scores higher, unless the season closed.

        The share lock on the season row makes this wait for, or fail after, a season
        closing under ``FOR UPDATE``, so a score never lands on a closed board.
        """
        async with postgres.session() as session:
            open_season = await session.scalar(
                select(SeasonRow.id)
                .where(SeasonRow.id == self.id, SeasonRow.closed_at.is_(None))
                .with_for_update(read=True)
            )
            if open_season is None:
                raise SeasonClosed
            previous = await session.scalar(
                select(BestRow.score).where(
                    BestRow.season_id == self.id, BestRow.user_id == user_id
                )
            )
            row = insert(BestRow).values(
                id=uuid7(),
                season_id=self.id,
                user_id=user_id,
                score=score,
                end_tick=run.end_tick,
                end_reason=run.end_reason,
                flap_deltas=list(run.flap_deltas),
                created_by=user_id,
                updated_by=user_id,
            )
            written = await session.scalar(
                row.on_conflict_do_update(
                    index_elements=[BestRow.season_id, BestRow.user_id],
                    set_={
                        "score": row.excluded.score,
                        "end_tick": row.excluded.end_tick,
                        "end_reason": row.excluded.end_reason,
                        "flap_deltas": row.excluded.flap_deltas,
                        "updated_at": func.clock_timestamp(),
                        "updated_by": user_id,
                    },
                    where=BestRow.score < row.excluded.score,
                ).returning(BestRow.id)
            )
        # Whether this run wrote the row, not a comparison with a score another submission may have changed.
        return Recorded(previous, written is not None)

    async def board(self, user_id: UUID) -> list[LeaderRow]:
        """The top scores, then your own row if you're further down; ties go to whoever set theirs first."""
        async with postgres.session() as session:
            top = list(
                await session.scalars(
                    select(BestRow)
                    .where(BestRow.season_id == self.id)
                    .order_by(BestRow.score.desc(), BestRow.updated_at)
                    .limit(BOARD_SIZE)
                )
            )
            mine = await session.scalar(
                select(BestRow).where(BestRow.season_id == self.id, BestRow.user_id == user_id)
            )
            my_rank = None
            if mine is not None and all(row.user_id != user_id for row in top):
                ahead = await session.scalar(
                    select(func.count())
                    .select_from(BestRow)
                    .where(
                        BestRow.season_id == self.id,
                        or_(
                            BestRow.score > mine.score,
                            and_(BestRow.score == mine.score, BestRow.updated_at < mine.updated_at),
                        ),
                    )
                )
                my_rank = (ahead or 0) + 1
        ranked = [(i + 1, row) for i, row in enumerate(top)]
        if mine is not None and my_rank is not None:
            ranked.append((my_rank, mine))
        people = await User.for_ids({row.user_id for _, row in ranked})
        return [
            LeaderRow(
                rank, Season.name_of(people.get(row.user_id)), row.score, row.user_id == user_id
            )
            for rank, row in ranked
        ]

    @staticmethod
    def name_of(user: User | None) -> str:
        """The name the board shows: a display name first, else the GitHub handle."""
        if user is None:
            return "someone"
        return user.display_name or user.login_for("github")
