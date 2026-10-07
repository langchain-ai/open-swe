from contextlib import asynccontextmanager
from datetime import UTC, datetime
from unittest.mock import AsyncMock
from zoneinfo import ZoneInfo

import httpx2
import pytest

from agent.github.codeowners import CodeOwners
from agent.human_review.picking import WorkHours

_CODEOWNERS = CodeOwners.parse(
    """
* @default
*.js @js
/docs/ @docs  # nested docs
docs/* @flat
apps/ @apps
/smith-frontend/src/design-system/ @langchain-ai/design-system
/unowned.txt
"""
)


@pytest.mark.parametrize(
    ("path", "owners"),
    [
        ("README.md", ("@default",)),
        ("src/app.js", ("@js",)),
        ("docs/intro.md", ("@flat",)),
        ("docs/guides/setup.md", ("@docs",)),
        ("services/apps/main.py", ("@apps",)),
        ("smith-frontend/src/design-system/Button.tsx", ("@langchain-ai/design-system",)),
        ("unowned.txt", ()),
    ],
)
def test_the_last_matching_codeowners_rule_owns_a_path(path: str, owners: tuple[str, ...]) -> None:
    assert _CODEOWNERS.owners_for(path) == owners


@pytest.mark.parametrize("status", [403, 429, 500, 404, 200])
async def test_strict_codeowners_fetch_distinguishes_missing_from_unreadable(
    status: int,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agent.github import repo_files

    @asynccontextmanager
    async def client(**kwargs: object):
        yield object()

    monkeypatch.setattr(repo_files, "github_client", client)
    monkeypatch.setattr(
        repo_files, "github_request", AsyncMock(return_value=httpx2.Response(status, text=""))
    )
    if status in {403, 429, 500}:
        with pytest.raises(repo_files.RepoFileUnreadableError):
            await CodeOwners.fetch("lc", "repo", "main", token="token", strict=True)
    else:
        owners = await CodeOwners.fetch("lc", "repo", "main", token="token", strict=True)
        assert (owners is None) == (status == 404)


def test_off_shift_on_friday_evening_waits_for_monday_morning() -> None:
    hours = WorkHours(ZoneInfo("America/New_York"))
    friday_evening = datetime(2026, 10, 2, 23, tzinfo=UTC)
    assert not hours.on_shift(friday_evening)
    assert hours.next_start(friday_evening) == datetime(2026, 10, 5, 13, tzinfo=UTC)
