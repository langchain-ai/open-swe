"""The leaderboard API: scores come from the server's replay, per season, one run every few seconds."""

import json
from collections.abc import AsyncIterator
from datetime import timedelta
from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import func, select, update

from openswe.dashboard import oauth, routes
from openswe.database import postgres
from openswe.trace_flight import routes as trace_flight
from openswe.trace_flight.core.replay import Replayed, Run
from openswe.trace_flight.core.rules import RULES_VERSION
from openswe.trace_flight.store import BestRow, LeaderRow, Season, SeasonRow
from openswe.users.models import User, UserIdentity

GOLDEN = Path(__file__).parent / "golden"


async def player(external_id: str, login: str, display_name: str = "") -> User:
    user = User(
        display_name=display_name,
        identities=[UserIdentity(provider="github", external_id=external_id, login=login)],
    )
    async with postgres.session() as session:
        session.add(user)
        await session.flush()
    return user


def client_for(user: User) -> httpx.AsyncClient:
    app = FastAPI()
    app.include_router(routes.router)
    session = {"sub": user.github_login, "user_id": str(user.id)}
    app.dependency_overrides[oauth.require_session] = lambda: session
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
        headers={"Origin": "http://test"},
    )


@pytest.fixture
async def season(registry_db: None, monkeypatch: pytest.MonkeyPatch) -> Season:
    """A season on seed 1, so the golden runs recorded on that seed are legal in it."""
    monkeypatch.setenv("DASHBOARD_BASE_URL", "http://test")
    async with postgres.session() as session:
        await session.execute(update(SeasonRow).values(closed_at=func.clock_timestamp()))
        row = SeasonRow(rules_version=RULES_VERSION, seed=1)
        session.add(row)
        await session.flush()
        return Season(row.id, row.rules_version, row.seed)


@pytest.fixture
async def pilot(season: Season) -> AsyncIterator[httpx.AsyncClient]:
    user = await player("1", "pilot", "Pilot")
    async with client_for(user) as client:
        yield client


def submission(season: Season, golden: str | None = None, **changes: object) -> dict[str, object]:
    run = (
        json.loads((GOLDEN / f"{golden}.json").read_text())
        if golden
        else {"endTick": 10, "endReason": "quit", "flapDeltas": [0]}
    )
    body = {
        "seasonId": str(season.id),
        "rulesVersion": RULES_VERSION,
        "endTick": run["endTick"],
        "endReason": run["endReason"],
        "flapDeltas": run["flapDeltas"],
    }
    return {**body, **changes}


async def test_a_run_scores_what_the_server_replays_and_only_a_higher_one_replaces_it(
    season: Season, pilot: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(trace_flight, "RUN_SPACING", timedelta(milliseconds=1))
    claimed = await pilot.post(
        "/dashboard/api/trace-flight/runs",
        json=submission(season, "autopilot-takeover-seed-1", score=999_999),
    )
    assert claimed.status_code == 422

    first = await pilot.post(
        "/dashboard/api/trace-flight/runs", json=submission(season, "autopilot-takeover-seed-1")
    )
    assert first.status_code == 200, first.text
    assert first.json() == {
        "score": 48,
        "previousBest": None,
        "newBest": True,
        "board": [{"rank": 1, "name": "Pilot", "score": 48, "you": True}],
    }

    lower = await pilot.post("/dashboard/api/trace-flight/runs", json=submission(season))
    assert lower.json()["score"] == 0
    assert (lower.json()["previousBest"], lower.json()["newBest"]) == (48, False)
    best = (await pilot.get("/dashboard/api/trace-flight/best")).json()
    assert (best["score"], best["endTick"], best["seed"]) == (48, 3000, 1)


async def test_the_board_ranks_everyone_but_each_player_sees_only_their_own_run(
    season: Season, pilot: httpx.AsyncClient
) -> None:
    await pilot.post(
        "/dashboard/api/trace-flight/runs", json=submission(season, "autopilot-takeover-seed-1")
    )
    rival = await player("2", "wingman")
    async with client_for(rival) as wingman:
        await wingman.post("/dashboard/api/trace-flight/runs", json=submission(season))
        assert (await wingman.get("/dashboard/api/trace-flight/best")).json()["score"] == 0
        board = (await wingman.get("/dashboard/api/trace-flight/leaderboard")).json()
    assert board == [
        {"rank": 1, "name": "Pilot", "score": 48, "you": False},
        {"rank": 2, "name": "wingman", "score": 0, "you": True},
    ]


@pytest.mark.parametrize(
    ("changes", "status", "reason"),
    [
        ({"rulesVersion": RULES_VERSION + 1}, 409, "stale_client"),
        ({"seasonId": "01a1c82c-98e0-7320-b60a-965dbead23be"}, 409, "stale_season"),
        ({"flapDeltas": [1]}, 422, "bad_flaps"),
        ({"endTick": 216_001}, 422, "bad_end_tick"),
        ({"endReason": "death"}, 422, "wrong_end_reason"),
    ],
)
async def test_stale_and_illegal_runs_are_refused(
    season: Season, pilot: httpx.AsyncClient, changes: dict[str, object], status: int, reason: str
) -> None:
    response = await pilot.post(
        "/dashboard/api/trace-flight/runs", json=submission(season, **changes)
    )
    assert (response.status_code, response.json()["detail"]["reason"]) == (status, reason)


async def test_oversized_runs_and_rapid_resubmissions_are_turned_away(
    season: Season, pilot: httpx.AsyncClient
) -> None:
    huge = await pilot.post(
        "/dashboard/api/trace-flight/runs", json=submission(season, flapDeltas=[1] * 140_000)
    )
    assert huge.status_code == 413
    assert (
        await pilot.post("/dashboard/api/trace-flight/runs", json=submission(season))
    ).is_success
    again = await pilot.post("/dashboard/api/trace-flight/runs", json=submission(season))
    assert again.status_code == 429
    assert again.headers["Retry-After"] == "5"


async def test_a_season_that_closes_during_the_replay_keeps_the_run_off_its_board(
    season: Season, pilot: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def close_then_replay(run: Run) -> Replayed:
        async with postgres.session() as session:
            await session.execute(
                update(SeasonRow)
                .where(SeasonRow.id == season.id)
                .values(closed_at=func.clock_timestamp())
            )
        return run.replay()

    monkeypatch.setattr(trace_flight.Replays, "replay", close_then_replay)
    response = await pilot.post("/dashboard/api/trace-flight/runs", json=submission(season))
    assert (response.status_code, response.json()["detail"]["reason"]) == (409, "stale_season")
    async with postgres.session() as session:
        assert await session.scalar(select(func.count()).select_from(BestRow)) == 0


async def test_the_board_breaks_ties_by_who_got_there_first_and_ranks_you_below_the_top_five(
    season: Season,
) -> None:
    quick = Run(season.seed, 10, "quit", (0,))
    leader = await player("10", "leader")
    await season.record(leader.id, quick, 20)
    rivals = [await player(str(11 + i), f"rival{i}") for i in range(5)]
    for rival in rivals:
        await season.record(rival.id, quick, 10)
    you = await player("20", "you")
    await season.record(you.id, quick, 10)

    board = await season.board(you.id)
    assert board == [
        LeaderRow(1, "leader", 20, False),
        *(LeaderRow(i + 2, f"rival{i}", 10, False) for i in range(4)),
        LeaderRow(7, "you", 10, True),
    ]
