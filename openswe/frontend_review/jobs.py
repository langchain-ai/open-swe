"""Durable dashboard captures on the shared PostgreSQL job worker."""

import logging
from uuid import UUID

from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from openswe.database import postgres
from openswe.github.pull_request_key import PullRequestKey
from openswe.jobs import queue
from openswe.jobs.queue import Job
from openswe.ui_invalidations.topics import Topic

from .capture import capture_run
from .launch import launch_assessment
from .models import FrontendReviewRun
from .schemas import FrontendReport
from .service import finish_review, get_review

logger = logging.getLogger(__name__)
CAPTURE_JOB = "frontend_capture"


class _CaptureRequest(BaseModel):
    owner: str
    repo: str
    pr_number: int
    run_id: UUID
    login: str


async def enqueue_capture(session: AsyncSession, run: FrontendReviewRun, login: str) -> None:
    request = _CaptureRequest(
        owner=run.owner,
        repo=run.repo,
        pr_number=run.pr_number,
        run_id=run.id,
        login=login,
    )
    await queue.schedule(
        CAPTURE_JOB,
        request.model_dump(mode="json"),
        key=f"{CAPTURE_JOB}:{run.id}",
        by=run.created_by,
        replace=False,
        connection=await session.connection(),
    )


async def process_capture(job: Job) -> None:
    request = _CaptureRequest.model_validate(job.payload)
    run = await get_review(request.owner, request.repo, request.pr_number, request.run_id)
    if run is None or run.auth_mode == "dummy" or run.status in {"blocked", "failed"}:
        return
    if run.status in {"pending", "running"}:
        async with postgres.session() as session:
            current = await session.get(FrontendReviewRun, run.id, with_for_update=True)
            if current is None or current.status not in {"pending", "running"}:
                return
            current.status = "running"
            current.updated_by = None
            await Topic.PULL_REQUESTS.invalidate(
                session, key=PullRequestKey.of(run.owner, run.repo, run.pr_number)
            )
        try:
            await capture_run(run)
        except Exception:
            logger.exception("Frontend capture failed", extra={"frontend_review_id": str(run.id)})
            await finish_review(
                run.id,
                owner=run.owner,
                repo=run.repo,
                pr_number=run.pr_number,
                status="failed",
                error="The capture failed. Check the server logs for details.",
            )
        run = await get_review(request.owner, request.repo, request.pr_number, request.run_id)
    if run is not None and run.status == "complete" and run.report:
        report = FrontendReport.model_validate(run.report)
        if report.ai_review_status == "not_run":
            await launch_assessment(run, request.login)
