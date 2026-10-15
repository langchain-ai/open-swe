"""Per-repository review style profiles.

Each record holds a custom reviewer prompt and the approval mode, both edited
in the dashboard.
"""

import logging
from datetime import datetime
from typing import Literal
from uuid import UUID

from langgraph_sdk import get_client
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import ForeignKey, Text, delete, func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column

from openswe.database import postgres
from openswe.database.orm import NOW, Base
from openswe.github.repo_names import RepoFullName, normalize_repo_full_name
from openswe.github.repositories import Repository
from openswe.ui_invalidations import Topic

logger = logging.getLogger(__name__)

# What a positive approval assessment does; a repository with no mode set is ``dry_run``.
ApprovalMode = Literal["off", "dry_run", "approve"]


class ReviewStyleCreate(BaseModel):
    full_name: RepoFullName = Field(..., description="GitHub repo in owner/name form")

    @field_validator("full_name", mode="before")
    @classmethod
    def _valid_full_name(cls, v: str) -> RepoFullName:
        return normalize_repo_full_name(v)


class ReviewStylePromptUpdate(BaseModel):
    custom_prompt: str | None = None
    approval_mode: ApprovalMode | None = None

    @field_validator("custom_prompt")
    @classmethod
    def _non_empty(cls, v: str | None) -> str | None:
        if v is not None and not v.strip():
            raise ValueError("custom_prompt cannot be empty")
        return v


class ReviewStyle(BaseModel):
    model_config = ConfigDict(extra="ignore")

    full_name: RepoFullName
    owner: str = ""
    name: str = ""
    custom_prompt: str | None = None
    approval_mode: ApprovalMode | None = None
    created_by: str = ""
    created_at: str = ""
    updated_at: str = ""


class ReviewStyleRow(Base):
    __tablename__ = "review_style"

    repository_id: Mapped[UUID] = mapped_column(
        ForeignKey("repository.id", ondelete="CASCADE"), primary_key=True
    )
    custom_prompt: Mapped[str | None] = mapped_column(default=None)
    approval_mode: Mapped[ApprovalMode | None] = mapped_column(Text, default=None)
    created_by: Mapped[str] = mapped_column(server_default="", default="")
    created_at: Mapped[datetime | None] = mapped_column(server_default=NOW, init=False)
    updated_at: Mapped[datetime | None] = mapped_column(server_default=NOW, init=False)

    def style(self, repository: Repository) -> ReviewStyle:
        return ReviewStyle(
            full_name=RepoFullName(repository.full_name),
            owner=repository.owner,
            name=repository.name,
            custom_prompt=self.custom_prompt,
            approval_mode=self.approval_mode,
            created_by=self.created_by,
            created_at=self.created_at.isoformat() if self.created_at else "",
            updated_at=self.updated_at.isoformat() if self.updated_at else "",
        )


def effective_approval_mode(record: ReviewStyle | None) -> ApprovalMode:
    return (record.approval_mode if record else None) or "dry_run"


class ReviewStyles:
    """Every repository's review style; a write marks the repository's dashboard topic stale."""

    async def get(self, full_name: str) -> ReviewStyle | None:
        async with postgres.session() as session:
            found = (
                await session.execute(
                    select(ReviewStyleRow, Repository)
                    .join(Repository, Repository.id == ReviewStyleRow.repository_id)
                    .where(Repository.key == normalize_repo_full_name(full_name).lower())
                )
            ).first()
        return None if found is None else found[0].style(found[1])

    async def list_all(self) -> list[ReviewStyle]:
        async with postgres.session() as session:
            rows = await session.execute(
                select(ReviewStyleRow, Repository)
                .join(Repository, Repository.id == ReviewStyleRow.repository_id)
                .order_by(Repository.key)
            )
            return [row.style(repository) for row, repository in rows.tuples()]

    async def create(self, full_name: RepoFullName, created_by: str) -> ReviewStyle:
        async with postgres.session() as session:
            await self._upsert(session, full_name, {}, created_by=created_by)
        return await self._require(full_name)

    async def update_prompts(
        self, full_name: RepoFullName, update: ReviewStylePromptUpdate
    ) -> ReviewStyle:
        changes: dict[str, str | None] = {}
        if update.custom_prompt is not None:
            changes["custom_prompt"] = update.custom_prompt
        if "approval_mode" in update.model_fields_set:
            changes["approval_mode"] = update.approval_mode
        async with postgres.session() as session:
            await self._upsert(session, full_name, changes)
        return await self._require(full_name)

    async def delete(self, full_name: RepoFullName) -> None:
        key = normalize_repo_full_name(full_name).lower()
        async with postgres.session() as session:
            await session.execute(
                delete(ReviewStyleRow).where(
                    ReviewStyleRow.repository_id
                    == select(Repository.id).where(Repository.key == key).scalar_subquery()
                )
            )
            await Topic.REVIEW_STYLES.invalidate(session, key=full_name)

    async def _upsert(
        self,
        session: AsyncSession,
        full_name: RepoFullName,
        changes: dict[str, str | None],
        *,
        created_by: str = "",
    ) -> None:
        """Create the style if it is missing, then apply ``changes`` (none keeps it as it is)."""
        repository_id = await Repository.ensure_id(session, full_name)
        upsert = insert(ReviewStyleRow).values(
            repository_id=repository_id, created_by=created_by, **changes
        )
        if changes:
            statement = upsert.on_conflict_do_update(
                index_elements=[ReviewStyleRow.repository_id],
                set_={**changes, "updated_at": func.clock_timestamp()},
            )
        else:
            statement = upsert.on_conflict_do_nothing()
        await session.execute(statement)
        await Topic.REVIEW_STYLES.invalidate(session, key=full_name)

    async def _require(self, full_name: RepoFullName) -> ReviewStyle:
        style = await self.get(full_name)
        if style is None:
            raise LookupError(f"review style for {full_name} vanished after it was written")
        return style


REVIEW_STYLES = ReviewStyles()


async def delete_analyzer_crons() -> None:
    """Delete the nightly crons left by the removed review-style analyzer."""
    client = get_client()
    for cron in await client.crons.search(metadata={"kind": "analyzer_continual"}, limit=100):
        await client.crons.delete(cron["cron_id"])


async def get_repo_custom_prompt(owner: str, repo: str) -> str | None:
    """Return the custom prompt supplement for a repo, if configured.

    Fail-soft on purpose: this runs while the reviewer assembles its system
    prompt, and a database blip should cost the run its style supplement, not
    the whole review.
    """
    full_name = RepoFullName(f"{owner}/{repo}")
    try:
        record = await REVIEW_STYLES.get(full_name)
    except Exception:
        logger.warning("review style lookup failed for %s", full_name, exc_info=True)
        return None
    if not record:
        return None
    return (record.custom_prompt or "").strip() or None
