from contextlib import asynccontextmanager
from datetime import timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy import text

from openswe.database.postgres import transaction
from openswe.github.ci import RequiredCheck
from openswe.github.http import GitHubClient
from openswe.github.required_check_cache import (
    CACHE_TTL,
    REFRESH_JOB,
    RequiredCheckCache,
    refresh_required_checks,
)
from openswe.jobs import queue
from openswe.jobs.worker import JobWorker

pytestmark = pytest.mark.usefixtures("registry_db")


async def test_required_checks_refresh_off_request_and_expire_per_branch(monkeypatch):
    @asynccontextmanager
    async def app(*args):
        yield GitHubClient(MagicMock())

    monkeypatch.setattr(GitHubClient, "as_app", app)
    fetch = AsyncMock(return_value={RequiredCheck("lint", 42)})
    monkeypatch.setattr(RequiredCheck, "for_branch", fetch)
    cache = RequiredCheckCache("Acme/App", "main")
    assert await cache.read() is None
    assert await RequiredCheckCache("acme/app", "main").read() is None
    assert fetch.await_count == 0
    assert len(await queue.find({})) == 1

    worker = JobWorker()
    worker.register(REFRESH_JOB)(refresh_required_checks)
    assert await worker.run_due() == 1
    assert await cache.read() == {RequiredCheck("lint", 42)}
    assert await RequiredCheckCache("acme/app", "release").read() is None
    assert fetch.await_count == 1

    async with transaction() as conn:
        await conn.execute(
            text(
                "UPDATE required_check_cache SET updated_at = clock_timestamp() - CAST(:age AS interval)"
            ),
            {"age": CACHE_TTL + timedelta(seconds=1)},
        )
    assert await cache.read() is None
    fetch.return_value = set()
    assert await worker.run_due() == 2
    assert await cache.read() == set()
    assert await RequiredCheckCache("acme/app", "release").read() == set()


async def test_failed_refresh_does_not_turn_unknown_rules_into_no_checks(monkeypatch):
    @asynccontextmanager
    async def app(*args):
        yield GitHubClient(MagicMock())

    monkeypatch.setattr(GitHubClient, "as_app", app)
    monkeypatch.setattr(RequiredCheck, "for_branch", AsyncMock(return_value=None))
    cache = RequiredCheckCache("acme/app", "main")
    assert await cache.read() is None
    worker = JobWorker()
    worker.register(REFRESH_JOB)(refresh_required_checks)
    await worker.run_due()
    assert await cache.read() is None
    assert (await queue.find({}))[0].attempts == 1
