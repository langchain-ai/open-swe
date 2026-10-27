import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from uuid import UUID

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from openswe.dashboard.oauth import require_session
from openswe.database import postgres
from openswe.frontend_review import jobs, routes, service
from openswe.frontend_review.models import FrontendReviewRun
from openswe.frontend_review.schemas import StartFrontendReview
from openswe.github.http import GitHubClient
from openswe.jobs import queue
from openswe.jobs.worker import JobWorker


class _GitHub:
    def repo(self, owner: str, repo: str) -> _GitHub:
        return self

    def pull_request(self, number: int) -> _GitHub:
        return self

    async def pull(self) -> dict[str, object]:
        return {"base": {"sha": "a" * 40}, "head": {"sha": "b" * 40}}


@asynccontextmanager
async def _github(owner: str, repo: str) -> AsyncIterator[_GitHub]:
    yield _GitHub()


@pytest.mark.usefixtures("registry_db")
async def test_dashboard_returns_queued_run_before_capture_and_workers_process_it_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(GitHubClient, "as_app", _github)

    async def allowed(login: str, full_name: str) -> None:
        return None

    started, release = asyncio.Event(), asyncio.Event()
    captured: list[UUID] = []

    async def capture(run: FrontendReviewRun) -> None:
        captured.append(run.id)
        started.set()
        await release.wait()
        await service.finish_review(
            run.id,
            owner=run.owner,
            repo=run.repo,
            pr_number=run.pr_number,
            status="blocked",
            error="Preview requires authentication",
        )

    monkeypatch.setattr(routes, "require_repo_access_for_user", allowed)
    monkeypatch.setattr(jobs, "capture_run", capture)
    app = FastAPI()
    app.include_router(routes.router)
    app.dependency_overrides[require_session] = lambda: {"sub": "octocat"}
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="https://test"
    ) as client:
        async with asyncio.timeout(2):
            response = await client.post(
                "/reviews/acme/repo/7/frontend-reviews",
                json={
                    "base_url": "https://base.vercel.app",
                    "head_url": "https://head.vercel.app",
                    "auth_mode": "none",
                },
            )
        assert response.status_code == 201
        assert response.json()["status"] == "pending"
        run_id = UUID(response.json()["id"])
        [queued] = await queue.find({})
        assert queued.kind == jobs.CAPTURE_JOB and queued.payload["run_id"] == str(run_id)
        assert not started.is_set()

        workers = [JobWorker(), JobWorker()]
        for worker in workers:
            worker.register(jobs.CAPTURE_JOB)(jobs.process_capture)
        processing = asyncio.gather(*(worker.run_due() for worker in workers))
        try:
            async with asyncio.timeout(2):
                await started.wait()
            progress = await client.get(f"/reviews/acme/repo/7/frontend-reviews/{run_id}")
            assert progress.json()["status"] == "running"
        finally:
            release.set()
            await processing
        finished = await client.get(f"/reviews/acme/repo/7/frontend-reviews/{run_id}")
        assert finished.json()["status"] == "blocked"
        assert finished.json()["error"] == "Preview requires authentication"
        assert captured == [run_id]
        assert await queue.find({}) == []


@pytest.mark.usefixtures("registry_db")
async def test_capture_and_job_roll_back_together_if_enqueue_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(GitHubClient, "as_app", _github)
    original = jobs.enqueue_capture

    async def interrupted(session: AsyncSession, run: FrontendReviewRun, login: str) -> None:
        await original(session, run, login)
        raise RuntimeError("request interrupted before commit")

    monkeypatch.setattr(jobs, "enqueue_capture", interrupted)
    with pytest.raises(RuntimeError, match="interrupted"):
        await service.create_review(
            "acme",
            "repo",
            7,
            StartFrontendReview(
                base_url="https://base.vercel.app",
                head_url="https://head.vercel.app",
                auth_mode="none",
            ),
            queue_login="octocat",
        )
    assert await service.list_reviews("acme", "repo", 7) == []
    assert await queue.find({}) == []


@pytest.mark.usefixtures("registry_db")
async def test_a_queued_capture_remains_recoverable_until_its_job_is_removed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(GitHubClient, "as_app", _github)
    run = await service.create_review(
        "acme",
        "repo",
        7,
        StartFrontendReview(
            base_url="https://base.vercel.app",
            head_url="https://head.vercel.app",
            auth_mode="none",
        ),
        queue_login="octocat",
    )
    async with postgres.session() as session:
        await session.execute(
            update(FrontendReviewRun).values(created_at=datetime.now(UTC) - timedelta(hours=1))
        )
    recovered = await service.get_review("acme", "repo", 7, run.id)
    assert recovered is not None and recovered.status == "pending"
    await queue.cancel_key(f"{jobs.CAPTURE_JOB}:{run.id}")
    expired = await service.get_review("acme", "repo", 7, run.id)
    assert expired is not None and expired.status == "failed"
