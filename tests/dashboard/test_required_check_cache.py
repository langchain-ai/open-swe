from contextlib import asynccontextmanager
from datetime import timedelta
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from sqlalchemy import text

from openswe.database.postgres import transaction
from openswe.github.ci import RequiredCheck
from openswe.github.http import GitHubAppUnavailable, GitHubClient
from openswe.github.required_check_cache import (
    CACHE_TTL,
    REFRESH_JOB,
    RequiredCheckCache,
    refresh_required_checks,
)
from openswe.jobs import queue
from openswe.jobs.worker import JobWorker
from openswe.ui_invalidations.topics import Topic
from openswe.users.models import User

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
    assert await cache.read() == {RequiredCheck("lint", 42)}
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


async def test_refresh_uses_viewer_when_app_is_not_installed(monkeypatch):
    viewer = MagicMock(id=uuid4(), github_login="alice")
    monkeypatch.setattr(User, "for_session", AsyncMock(return_value=viewer))
    monkeypatch.setattr(User, "get", AsyncMock(return_value=viewer))

    @asynccontextmanager
    async def app(*args):
        raise GitHubAppUnavailable("Not installed")
        yield

    @asynccontextmanager
    async def user(login):
        assert login == "alice"
        yield GitHubClient(MagicMock(), login=login)

    monkeypatch.setattr(GitHubClient, "as_app", app)
    monkeypatch.setattr(GitHubClient, "as_user", user)
    monkeypatch.setattr(
        RequiredCheck, "for_branch", AsyncMock(return_value={RequiredCheck("lint")})
    )
    cache = RequiredCheckCache("acme/app", "main")
    assert await cache.read("alice") is None
    [job] = await queue.find({})
    assert job.payload["viewer_id"] == str(viewer.id)
    await refresh_required_checks(job)
    assert await cache.read() == {RequiredCheck("lint")}


async def test_required_check_notifications_authorize_only_accessible_repositories(monkeypatch):
    from openswe.github import repos

    monkeypatch.setattr(repos, "accessible_repo_full_names", AsyncMock(return_value={"acme/app"}))
    assert await Topic.audible(
        {"sub": "alice"}, {"required-checks/acme/app", "required-checks/private/repo"}
    ) == {"required-checks/acme/app"}
