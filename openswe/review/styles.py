"""Per-repository review style profiles in LangGraph Store.

Each record holds a custom reviewer prompt and the approval mode, both edited
in the dashboard.
"""

import logging
from typing import Literal, NewType

from langgraph_sdk import get_client
from pydantic import BaseModel, ConfigDict, Field, field_validator

from openswe.store import TypedStore, now_iso
from openswe.ui_invalidations import Topic

logger = logging.getLogger(__name__)

REVIEW_STYLES_NAMESPACE: list[str] = ["review_styles"]

RepoFullName = NewType("RepoFullName", str)
"""A GitHub repository as ``owner/repo``, from ``normalize_repo_full_name``."""

# What a positive approval assessment does; a repository with no mode set is ``dry_run``.
ApprovalMode = Literal["off", "dry_run", "approve"]


def normalize_repo_full_name(raw: str) -> RepoFullName:
    """Normalize user input to ``owner/repo``."""
    v = raw.strip()
    for prefix in ("https://github.com/", "http://github.com/", "github.com/"):
        if v.lower().startswith(prefix):
            v = v[len(prefix) :]
    v = v.strip("/")
    if v.endswith(".git"):
        v = v[:-4]
    parts = [p for p in v.split("/") if p]
    if len(parts) != 2:
        raise ValueError("full_name must be owner/repo")
    return RepoFullName(f"{parts[0]}/{parts[1]}")


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

    @classmethod
    def seed(cls, full_name: RepoFullName, created_by: str = "") -> ReviewStyle:
        owner, _, name = full_name.partition("/")
        now = now_iso()
        return cls(
            full_name=full_name,
            owner=owner,
            name=name,
            created_by=created_by,
            created_at=now,
            updated_at=now,
        )


def effective_approval_mode(record: ReviewStyle | None) -> ApprovalMode:
    return (record.approval_mode if record else None) or "dry_run"


class ReviewStyleStore(TypedStore[ReviewStyle, RepoFullName]):
    def __init__(self) -> None:
        super().__init__(REVIEW_STYLES_NAMESPACE, ReviewStyle, invalidates=Topic.REVIEW_STYLES)

    async def list_all(self) -> list[ReviewStyle]:
        records = await self.search_all()
        records.sort(key=lambda record: record.full_name)
        return records

    async def save(self, record: ReviewStyle) -> ReviewStyle:
        record.updated_at = now_iso()
        return await self.put(record.full_name, record)

    async def get_or_seed(self, full_name: RepoFullName, created_by: str = "") -> ReviewStyle:
        return await self.get(full_name) or ReviewStyle.seed(full_name, created_by)

    async def create(self, full_name: RepoFullName, created_by: str) -> ReviewStyle:
        existing = await self.get(full_name)
        if existing:
            return existing
        return await self.put(full_name, ReviewStyle.seed(full_name, created_by))

    async def update_prompts(
        self, full_name: RepoFullName, update: ReviewStylePromptUpdate
    ) -> ReviewStyle:
        record = await self.get_or_seed(full_name)
        if update.custom_prompt is not None:
            record.custom_prompt = update.custom_prompt
        if "approval_mode" in update.model_fields_set:
            record.approval_mode = update.approval_mode
        return await self.save(record)


REVIEW_STYLES = ReviewStyleStore()


async def delete_analyzer_crons() -> None:
    """Delete the nightly crons left by the removed review-style analyzer."""
    client = get_client()
    for cron in await client.crons.search(metadata={"kind": "analyzer_continual"}, limit=100):
        await client.crons.delete(cron["cron_id"])


async def get_repo_custom_prompt(owner: str, repo: str) -> str | None:
    """Return the custom prompt supplement for a repo, if configured.

    Fail-soft on purpose: this runs while the reviewer assembles its system
    prompt, and a store blip should cost the run its style supplement, not the
    whole review.
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
