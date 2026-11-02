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
from .schemas import ASSET_NAME


class FrontendReviews:
    """Evidence belonging to one pull request, after the caller authorizes its repository."""

    def __init__(self, owner: str, repo: str, number: int) -> None:
        self.owner = owner.lower()
        self.repo = repo.lower()
        self.number = number

    def _query(self) -> Select[FrontendReviewRun]:
        return (
            select(FrontendReviewRun)
            .join(PullRequest)
            .where(
                func.lower(PullRequest.owner) == self.owner,
                func.lower(PullRequest.repo) == self.repo,
                PullRequest.number == self.number,
            )
        )

    async def list_runs(self) -> list[FrontendReviewRun]:
        async with postgres.session() as session:
            return list(
                await session.scalars(
                    self._query().order_by(FrontendReviewRun.created_at.desc()).limit(20)
                )
            )

    @staticmethod
    async def latest(
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

    async def get(self, run_id: UUID) -> FrontendReviewRun | None:
        async with postgres.session() as session:
            return await session.scalar(self._query().where(FrontendReviewRun.id == run_id))

    async def media(self, run_id: UUID, name: str) -> FrontendReviewMedia | None:
        if not ASSET_NAME.fullmatch(name):
            return None
        async with postgres.session() as session:
            return await session.scalar(
                select(FrontendReviewMedia)
                .join(FrontendReviewRun)
                .join(PullRequest)
                .where(
                    func.lower(PullRequest.owner) == self.owner,
                    func.lower(PullRequest.repo) == self.repo,
                    PullRequest.number == self.number,
                    FrontendReviewRun.id == run_id,
                    FrontendReviewMedia.name == name,
                )
                .options(undefer(FrontendReviewMedia.content))
            )
