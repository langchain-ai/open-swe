from contextlib import asynccontextmanager
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from agent.analytics import queries


class _Result:
    def __init__(self, rows: list[dict[str, object]]) -> None:
        self._rows = rows

    def mappings(self) -> list[dict[str, object]]:
        return self._rows


class _Connection:
    def __init__(self, rows: list[dict[str, object]]) -> None:
        self._rows = rows
        self._scalars = iter([len(rows), None])

    async def execute(self, *_args: object, **_kwargs: object) -> _Result:
        return _Result(self._rows)

    async def scalar(self, *_args: object, **_kwargs: object) -> object:
        return next(self._scalars)


@pytest.mark.asyncio
async def test_usage_leaderboard_removes_internal_current_marker(monkeypatch) -> None:
    current_person = uuid4()
    rows = [
        {
            "rank": rank,
            "user_id": current_person if rank == 2 else uuid4(),
            "github_login": f"user-{rank}",
            "display_name": None,
            "email": None,
            "finished_runs": 0,
            "duration_seconds": 0,
            "agent_runs": 1,
            "completed_runs": 1,
            "failed_runs": 0,
            "canceled_runs": 0,
            "prs_opened": 0,
            "merged_prs": 0,
            "total_tokens": 0,
            "total_cost_usd": 0,
            "known_cost_runs": 0,
            "cost_run_count": 0,
        }
        for rank in range(1, 4)
    ]

    @asynccontextmanager
    async def connection():
        yield _Connection(rows)

    monkeypatch.setattr(queries, "connection", connection)
    monkeypatch.setattr(queries, "workspace_id", uuid4)
    monkeypatch.setattr(queries, "opaque_person", lambda *_args: current_person)
    monkeypatch.setattr(queries, "reviewer_stats", AsyncMock(return_value={}))

    result = await queries.usage_leaderboard(
        period="30d", limit=10, current_login="current", admin=True
    )

    assert result["current_user_rank"] == 2
    assert all("is_current" not in row for row in result["rows"])
