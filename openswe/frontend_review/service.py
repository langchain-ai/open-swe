"""Reading frontend review evidence: a pull request's captures and their media.

Callers authorize the repository first; these queries only scope by pull request.
"""

from uuid import UUID

from sqlalchemy import Select, func, select, tuple_
from sqlalchemy.dialects.postgresql import distinct_on
from sqlalchemy.orm import undefer

from openswe.database import postgres
from openswe.github.pull_requests import PullRequest

from .models import FrontendReviewMedia, FrontendReviewRun
from .schemas import ASSET_NAME, FrontendReport, FrontendReviewView


def _scoped_query(owner: str, repo: str, pr_number: int) -> Select[FrontendReviewRun]:
    return (
        select(FrontendReviewRun)
        .join(PullRequest)
        .where(
            func.lower(PullRequest.owner) == owner.lower(),
            func.lower(PullRequest.repo) == repo.lower(),
            PullRequest.number == pr_number,
        )
    )


async def list_reviews(owner: str, repo: str, pr_number: int) -> list[FrontendReviewRun]:
    async with postgres.session() as session:
        return list(
            await session.scalars(
                _scoped_query(owner, repo, pr_number)
                .order_by(FrontendReviewRun.created_at.desc())
                .limit(20)
            )
        )


async def latest_reviews(
    identities: list[tuple[str, str, int]],
) -> list[FrontendReviewRun]:
    """Read only the latest evidence for PRs the caller has already authorized."""
    if not identities:
        return []
    async with postgres.session() as session:
        return list(
            await session.scalars(
                select(FrontendReviewRun)
                .join(PullRequest)
                .where(
                    tuple_(
                        func.lower(PullRequest.owner),
                        func.lower(PullRequest.repo),
                        PullRequest.number,
                    ).in_(
                        [
                            (owner.lower(), repo.lower(), number)
                            for owner, repo, number in identities
                        ]
                    )
                )
                .ext(distinct_on(FrontendReviewRun.pull_request_id))
                .order_by(
                    FrontendReviewRun.pull_request_id,
                    FrontendReviewRun.created_at.desc(),
                    FrontendReviewRun.id.desc(),
                )
            )
        )


async def get_review(
    owner: str, repo: str, pr_number: int, run_id: UUID
) -> FrontendReviewRun | None:
    async with postgres.session() as session:
        return await session.scalar(
            _scoped_query(owner, repo, pr_number).where(FrontendReviewRun.id == run_id)
        )


async def media_for_review(
    owner: str, repo: str, pr_number: int, run_id: UUID, name: str
) -> FrontendReviewMedia | None:
    if not ASSET_NAME.fullmatch(name):
        return None
    async with postgres.session() as session:
        return await session.scalar(
            select(FrontendReviewMedia)
            .join(FrontendReviewRun)
            .join(PullRequest)
            .where(
                func.lower(PullRequest.owner) == owner.lower(),
                func.lower(PullRequest.repo) == repo.lower(),
                PullRequest.number == pr_number,
                FrontendReviewRun.id == run_id,
                FrontendReviewMedia.name == name,
            )
            .options(undefer(FrontendReviewMedia.content))
        )


def review_view(run: FrontendReviewRun) -> FrontendReviewView:
    """The run as people read it; its scenarios name their media, read with ``media_for_review``."""
    report = FrontendReport.model_validate(run.report) if run.report else None
    return FrontendReviewView(
        id=run.id,
        status=run.status,
        auth_mode=run.auth_mode,
        base_url=run.base_url,
        head_url=run.head_url,
        paths=run.paths,
        base_sha=run.base_sha,
        head_sha=run.head_sha,
        created_at=run.created_at,
        updated_at=run.updated_at,
        error=run.error,
        report=report,
    )
