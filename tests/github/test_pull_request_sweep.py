"""Which stored pull requests a stale sweep picks up, and how it groups them."""

from collections.abc import Sequence
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import update

from agent.database import postgres
from agent.github import pull_request_sweep
from agent.github.comments import PrState
from agent.github.pull_request_sweep import sweep_stale_pull_requests
from agent.github.pull_requests import PullRequest

pytestmark = pytest.mark.usefixtures("registry_db")

MAX_AGE = timedelta(hours=1)


class _Github:
    """Records what the sweep asked GitHub for, standing in for both calls."""

    def __init__(self, *, token: str | None = "token", failing: Sequence[int] = ()) -> None:
        self.token = token
        self.failing = set(failing)
        self.token_requests: list[list[str]] = []
        self.synced: list[tuple[str, str, int, str]] = []

    async def installation_token(self, *, repositories: Sequence[str]) -> str | None:
        self.token_requests.append(list(repositories))
        return self.token

    async def sync(self, owner: str, repo: str, number: int, *, token: str) -> object | None:
        self.synced.append((owner, repo, number, token))
        if number in self.failing:
            raise RuntimeError("github said no")
        return object()

    def install(self, monkeypatch: pytest.MonkeyPatch) -> _Github:
        monkeypatch.setattr(
            pull_request_sweep, "get_github_app_installation_token", self.installation_token
        )
        monkeypatch.setattr(pull_request_sweep, "sync_pull_request", self.sync)
        return self

    @property
    def numbers(self) -> set[tuple[str, str, int]]:
        return {(owner, repo, number) for owner, repo, number, _ in self.synced}


async def _store(
    repo: str, number: int, *, state: PrState = "open", synced_at: datetime | None = None
) -> None:
    await PullRequest(owner="lc", repo=repo, number=number, state=state).save(
        synced=synced_at is not None
    )
    if synced_at is None:
        return
    async with postgres.session() as session:
        await session.execute(
            update(PullRequest)
            .where(PullRequest.repo == repo, PullRequest.number == number)
            .values(last_synced_at=synced_at)
        )


def _ago(hours: int) -> datetime:
    return datetime.now(UTC) - timedelta(hours=hours)


async def test_sweep_takes_only_stale_open_pull_requests_grouped_by_repository(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _store("alpha", 1)
    await _store("alpha", 2, synced_at=_ago(2))
    await _store("beta", 3)
    await _store("alpha", 4, synced_at=_ago(0))
    await _store("alpha", 5, state="closed")
    github = _Github().install(monkeypatch)

    report = await sweep_stale_pull_requests(max_age=MAX_AGE, limit=10)

    assert github.numbers == {("lc", "alpha", 1), ("lc", "alpha", 2), ("lc", "beta", 3)}
    assert sorted(github.token_requests) == [["lc/alpha"], ["lc/beta"]]
    assert (report.synced, report.failed, report.skipped) == (3, 0, 0)
    assert {(detail.repo_full_name, detail.synced) for detail in report.repositories} == {
        ("lc/alpha", 2),
        ("lc/beta", 1),
    }


async def test_sweep_limit_takes_the_least_recently_synced_first(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _store("alpha", 1, synced_at=_ago(3))
    await _store("alpha", 2, synced_at=_ago(2))
    await _store("alpha", 3)
    github = _Github().install(monkeypatch)

    await sweep_stale_pull_requests(max_age=MAX_AGE, limit=2)

    assert github.numbers == {("lc", "alpha", 3), ("lc", "alpha", 1)}


async def test_repository_without_an_installation_is_skipped_not_failed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _store("alpha", 1)
    await _store("alpha", 2)
    github = _Github(token=None).install(monkeypatch)

    report = await sweep_stale_pull_requests(max_age=MAX_AGE, limit=10)

    assert github.synced == []
    assert (report.synced, report.failed, report.skipped) == (0, 0, 2)


async def test_one_failing_pull_request_does_not_stop_the_sweep(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _store("alpha", 1)
    await _store("beta", 2)
    github = _Github(failing=[1]).install(monkeypatch)

    report = await sweep_stale_pull_requests(max_age=MAX_AGE, limit=10)

    assert github.numbers == {("lc", "alpha", 1), ("lc", "beta", 2)}
    assert (report.synced, report.failed, report.skipped) == (1, 1, 0)
