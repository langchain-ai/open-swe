"""Trace Flight's leaderboard API.

A run arrives as its input log; the server replays it on the season's course
and keeps the score it computes. Replay proves a run is legal, not that a
person played it, which is enough for an internal easter egg.
"""

import asyncio
import weakref
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from datetime import timedelta
from typing import Annotated, ClassVar, Self
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import AliasGenerator, BaseModel, ConfigDict, JsonValue, StrictInt, ValidationError
from pydantic.alias_generators import to_camel

from openswe import event_claims
from openswe.audit_logs.middleware import audit_endpoint
from openswe.dashboard.deps import SESSION_DEP
from openswe.dashboard.principal import DashboardPrincipal
from openswe.trace_flight.core.replay import Replayed, Run, RunRejected
from openswe.trace_flight.core.rules import RULES_VERSION
from openswe.trace_flight.store import BestRun, LeaderRow, NoOpenSeason, Season, SeasonClosed
from openswe.users.models import User

router = APIRouter(prefix="/trace-flight", tags=["trace-flight"])

MAX_BODY_BYTES = 256 * 1024
RUN_SPACING = timedelta(seconds=5)


async def require_player(session: dict[str, object] = SESSION_DEP) -> User:
    user = await DashboardPrincipal.from_session(session).user()
    if user is None:
        raise HTTPException(401, "sign in again to play")
    return user


Player = Annotated[User, Depends(require_player)]


class CamelModel(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)


class SeasonView(CamelModel):
    season_id: UUID
    rules_version: int
    seed: int

    @classmethod
    def of(cls, season: Season) -> Self:
        return cls(season_id=season.id, rules_version=season.rules_version, seed=season.seed)


class BestView(CamelModel):
    seed: int
    end_tick: int
    end_reason: str
    flap_deltas: list[int]
    score: int

    @classmethod
    def of(cls, best: BestRun) -> Self:
        run = best.run
        return cls(
            seed=run.seed,
            end_tick=run.end_tick,
            end_reason=run.end_reason,
            flap_deltas=list(run.flap_deltas),
            score=best.score,
        )


class LeaderRowView(CamelModel):
    rank: int
    name: str
    score: int
    you: bool

    @classmethod
    def of(cls, row: LeaderRow) -> Self:
        return cls(rank=row.rank, name=row.name, score=row.score, you=row.you)


class RunResultView(CamelModel):
    score: int
    previous_best: int | None
    new_best: bool
    board: list[LeaderRowView]


class RunSubmission(BaseModel):
    """A run as the client sends it. The run's numbers stay raw JSON so ``Run.parse``
    can judge them the way the TypeScript core does."""

    model_config = ConfigDict(
        alias_generator=AliasGenerator(validation_alias=to_camel),
        populate_by_name=True,
        extra="forbid",
    )

    season_id: UUID
    rules_version: StrictInt
    end_tick: JsonValue
    end_reason: JsonValue
    flap_deltas: JsonValue

    @classmethod
    async def read(cls, request: Request) -> Self:
        """The request body, refused past MAX_BODY_BYTES whether or not it declares a length."""
        declared = request.headers.get("content-length", "")
        if declared.isdecimal() and int(declared) > MAX_BODY_BYTES:
            raise HTTPException(413, "run too large")
        body = bytearray()
        async for chunk in request.stream():
            body += chunk
            if len(body) > MAX_BODY_BYTES:
                raise HTTPException(413, "run too large")
        try:
            return cls.model_validate_json(bytes(body))
        except ValidationError as error:
            raise HTTPException(422, {"reason": "malformed"}) from error


class ReplaysBusy(Exception):
    """Every replay slot in this process is taken."""


class Replays:
    """Replays are CPU work: they run off the event loop, a couple at a time per process."""

    SLOTS: ClassVar[int] = 2
    _slots: ClassVar[weakref.WeakValueDictionary[asyncio.AbstractEventLoop, asyncio.Semaphore]] = (
        weakref.WeakValueDictionary()
    )

    @classmethod
    @asynccontextmanager
    async def slot(cls) -> AsyncGenerator[None]:
        loop = asyncio.get_running_loop()
        slots = cls._slots.get(loop)
        if slots is None:
            slots = cls._slots[loop] = asyncio.Semaphore(cls.SLOTS)
        # Checked and taken with no await between, so a full house turns callers away.
        if slots.locked():
            raise ReplaysBusy
        async with slots:
            yield

    @staticmethod
    async def replay(run: Run) -> Replayed:
        return await asyncio.to_thread(run.replay)


async def require_open_season() -> Season:
    try:
        return await Season.current()
    except NoOpenSeason as error:
        raise HTTPException(503, "Trace Flight has no open season") from error


OpenSeason = Annotated[Season, Depends(require_open_season)]


@router.get("/season")
async def api_season(_player: Player, season: OpenSeason) -> SeasonView:
    return SeasonView.of(season)


@router.get("/best")
async def api_best(player: Player, season: OpenSeason) -> BestView | None:
    best = await season.best_of(player.id)
    return BestView.of(best) if best else None


@router.get("/leaderboard")
async def api_leaderboard(player: Player, season: OpenSeason) -> list[LeaderRowView]:
    return [LeaderRowView.of(row) for row in await season.board(player.id)]


@router.post("/runs")
@audit_endpoint
async def api_submit_run(request: Request, player: Player, season: OpenSeason) -> RunResultView:
    submission = await RunSubmission.read(request)
    if submission.rules_version != RULES_VERSION or season.rules_version != RULES_VERSION:
        raise HTTPException(409, {"reason": "stale_client"})
    if submission.season_id != season.id:
        raise HTTPException(409, {"reason": "stale_season"})
    try:
        run = Run.parse(
            season.seed, submission.end_tick, submission.end_reason, submission.flap_deltas
        )
    except RunRejected as error:
        raise HTTPException(422, {"reason": error.reason}) from error

    try:
        # A slot first, then the run allowance: a run turned away as busy keeps its allowance.
        async with Replays.slot():
            outcome = await event_claims.claim_within_limit(
                "trace-flight-run",
                f"{player.id}:{uuid4()}",
                ttl=RUN_SPACING,
                group=f"{player.id}:",
                limit=1,
                within=RUN_SPACING,
            )
            if outcome != "claimed":
                retry = str(int(RUN_SPACING.total_seconds()))
                raise HTTPException(
                    429, "one run every few seconds", headers={"Retry-After": retry}
                )
            replayed = await Replays.replay(run)
    except ReplaysBusy as error:
        raise HTTPException(503, "busy replaying runs", headers={"Retry-After": "2"}) from error
    except RunRejected as error:
        raise HTTPException(422, {"reason": error.reason}) from error

    score = replayed.state.score
    try:
        recorded = await season.record(player.id, run, score)
    except SeasonClosed as error:
        raise HTTPException(409, {"reason": "stale_season"}) from error
    board = await season.board(player.id)
    return RunResultView(
        score=score,
        previous_best=recorded.previous_best,
        new_best=recorded.new_best,
        board=[LeaderRowView.of(row) for row in board],
    )
