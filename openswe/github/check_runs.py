"""Check runs mirrored from GitHub for the commits pull requests point at.

Rows are keyed by GitHub's check run id and grouped by ``(repository, head_sha)``.
A run's status only moves forward (queued → in progress → completed): GitHub
delivers webhooks out of order, and a re-run is a new check run with a new id.
"""

import logging
from collections.abc import Sequence
from datetime import datetime
from typing import Self
from uuid import UUID

import httpx2
from pydantic import AliasPath, BaseModel, Field
from sqlalchemy import BigInteger, ColumnElement, ForeignKey, case, delete, func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import InstrumentedAttribute, Mapped, mapped_column

from openswe.database import postgres
from openswe.database.orm import NOW, Base
from openswe.github.http import GITHUB_API_BASE, github_request

logger = logging.getLogger(__name__)

_PAGE_SIZE = 100
_MAX_PAGES = 10


class CheckRunPayload(BaseModel):
    """A check run as GitHub's REST API and ``check_run`` webhooks describe it."""

    id: int
    head_sha: str
    name: str = ""
    status: str = "queued"
    conclusion: str | None = None
    html_url: str | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None


class CheckRunEvent(BaseModel):
    check_run: CheckRunPayload
    repo_full_name: str = Field(validation_alias=AliasPath("repository", "full_name"))


class CheckRun(Base):
    __tablename__ = "check_run"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    repository_id: Mapped[UUID] = mapped_column(ForeignKey("repository.id", ondelete="CASCADE"))
    head_sha: Mapped[str]
    name: Mapped[str] = mapped_column(server_default="", default="")
    status: Mapped[str] = mapped_column(server_default="queued", default="queued")
    conclusion: Mapped[str | None] = mapped_column(default=None)
    html_url: Mapped[str] = mapped_column(server_default="", default="")
    started_at: Mapped[datetime | None] = mapped_column(default=None)
    completed_at: Mapped[datetime | None] = mapped_column(default=None)
    updated_at: Mapped[datetime | None] = mapped_column(server_default=NOW, init=False)

    @classmethod
    def from_payload(cls, payload: CheckRunPayload, repository_id: UUID) -> Self:
        return cls(
            id=payload.id,
            repository_id=repository_id,
            head_sha=payload.head_sha,
            name=payload.name,
            status=payload.status,
            conclusion=payload.conclusion,
            html_url=payload.html_url or "",
            started_at=payload.started_at,
            completed_at=payload.completed_at,
        )

    @classmethod
    async def fetch_for_commit(
        cls, client: httpx2.AsyncClient, full_name: str, sha: str
    ) -> list[CheckRunPayload]:
        """Every check run GitHub lists for ``sha``; none when the App may not read checks."""
        runs: list[CheckRunPayload] = []
        for page in range(1, _MAX_PAGES + 1):
            response = await github_request(
                client,
                "GET",
                f"{GITHUB_API_BASE}/repos/{full_name}/commits/{sha}/check-runs",
                params={"per_page": _PAGE_SIZE, "page": page},
            )
            if response.status_code in (403, 404):
                logger.info(
                    "GitHub listed no check runs for a commit",
                    extra={"repository": full_name, "status_code": response.status_code},
                )
                return runs
            response.raise_for_status()
            batch = _CheckRunPage.model_validate(response.json()).check_runs
            runs.extend(batch)
            if len(batch) < _PAGE_SIZE:
                break
        return runs

    @classmethod
    async def for_commit(cls, repository_id: UUID, head_sha: str) -> list[Self]:
        async with postgres.session() as session:
            rows = await session.scalars(
                select(cls)
                .where(cls.repository_id == repository_id, cls.head_sha == head_sha)
                .order_by(cls.name, cls.id)
            )
            return list(rows)

    @classmethod
    async def store(cls, session: AsyncSession, runs: Sequence[Self]) -> None:
        """Upsert ``runs``; a run already further along keeps its stored state."""
        if not runs:
            return
        upsert = insert(cls).values(
            [
                {
                    "id": run.id,
                    "repository_id": run.repository_id,
                    "head_sha": run.head_sha,
                    "name": run.name,
                    "status": run.status,
                    "conclusion": run.conclusion,
                    "html_url": run.html_url,
                    "started_at": run.started_at,
                    "completed_at": run.completed_at,
                }
                for run in {run.id: run for run in runs}.values()
            ]
        )
        incoming, stored = _progress(upsert.excluded.status), _progress(cls.status)
        await session.execute(
            upsert.on_conflict_do_update(
                index_elements=[cls.id],
                set_={
                    column: case(
                        (incoming >= stored, getattr(upsert.excluded, column)),
                        else_=getattr(cls, column),
                    )
                    for column in (
                        "name",
                        "status",
                        "conclusion",
                        "html_url",
                        "started_at",
                        "completed_at",
                    )
                }
                | {"updated_at": func.clock_timestamp()},
            )
        )

    @classmethod
    async def forget_commit(cls, session: AsyncSession, repository_id: UUID, head_sha: str) -> None:
        await session.execute(
            delete(cls).where(cls.repository_id == repository_id, cls.head_sha == head_sha)
        )


class _CheckRunPage(BaseModel):
    check_runs: list[CheckRunPayload] = []


def _progress(status: ColumnElement[str] | InstrumentedAttribute[str]) -> ColumnElement[int]:
    return case((status == "completed", 2), (status == "in_progress", 1), else_=0)
