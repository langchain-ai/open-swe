from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from openswe.review_scout import launch

pytestmark = pytest.mark.usefixtures("no_local_threads")


@pytest.fixture
def pull_files(monkeypatch: pytest.MonkeyPatch) -> AsyncMock:
    files = AsyncMock(return_value=[{"filename": "api.py", "additions": 100}])

    @asynccontextmanager
    async def as_app(*args: object) -> AsyncIterator[SimpleNamespace]:
        yield SimpleNamespace(files=files)

    monkeypatch.setattr(launch.PullRequestClient, "as_app", as_app)
    return files


@pytest.mark.parametrize("active", [None, "active-run"])
async def test_scout_records_security_identity_before_starting_or_reusing_a_run(
    monkeypatch: pytest.MonkeyPatch, active: str | None, pull_files: AsyncMock
) -> None:
    stored: dict[str, object] = {"title": "Walkthrough", "title_locked": True}

    async def update(*, thread_id: str, metadata: dict[str, object]) -> None:
        stored.update(metadata)

    threads = SimpleNamespace(
        create=AsyncMock(return_value={"metadata": stored}),
        update=AsyncMock(side_effect=update),
    )
    client = SimpleNamespace(threads=threads)
    monkeypatch.setattr(launch, "dispatch_client", lambda: client)
    target = launch.ReviewScoutTarget(
        owner="acme",
        repo="api",
        pr_number=7,
        pr_title="Change",
        base_sha="base",
        head_sha="head",
        workspace_slug="workspace",
    )

    async def active_run(self: launch.ReviewScoutTarget) -> str | None:
        assert stored == {
            "title": "Walkthrough",
            "title_locked": True,
            "kind": "review-scout",
            "pr": {"owner": "acme", "name": "api", "number": 7},
        }
        return active

    monkeypatch.setattr(launch.ReviewScoutTarget, "active_run", active_run)
    monkeypatch.setattr(launch, "create_durable_run", AsyncMock(return_value={"run_id": "new-run"}))

    assert await target.start() == (active or "new-run")


@pytest.mark.parametrize("non_test_lines", [0, 99])
async def test_small_changes_skip_scout_and_reviewer_wait(
    monkeypatch: pytest.MonkeyPatch, pull_files: AsyncMock, non_test_lines: int
) -> None:
    pull_files.return_value = [
        {
            "filename": "api.py",
            "additions": non_test_lines // 2,
            "deletions": non_test_lines - non_test_lines // 2,
        },
        {"filename": "tests/test_api.py", "additions": 1000},
        {"filename": "ui/api.spec.ts", "additions": 1000},
    ]
    dispatch = MagicMock(side_effect=AssertionError("small changes must not start or poll a scout"))
    monkeypatch.setattr(launch, "dispatch_client", dispatch)
    monkeypatch.setattr(launch.postgres, "configured", lambda: True)
    monkeypatch.setattr(launch.ReviewScoutTarget, "walkthrough", AsyncMock(return_value=None))
    target = launch.ReviewScoutTarget(
        owner="acme",
        repo="api",
        pr_number=7,
        pr_title="Change",
        base_sha="base",
        head_sha="head",
        workspace_slug="workspace",
    )

    assert await target.start() is None
    assert await target.await_walkthrough() is None
    dispatch.assert_not_called()
