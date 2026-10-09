"""Per-repository review style profiles in LangGraph Store.

Each record holds a synthesized custom prompt (editable in the web app),
analysis metadata, and the status of the background style-analysis run.
"""

import logging
from typing import Literal, NewType

from pydantic import BaseModel, ConfigDict, Field, field_validator

from openswe.store import TypedStore, now_iso
from openswe.ui_invalidations import Topic

logger = logging.getLogger(__name__)

REVIEW_STYLES_NAMESPACE: list[str] = ["review_styles"]

RepoFullName = NewType("RepoFullName", str)
"""A GitHub repository as ``owner/repo``, from ``normalize_repo_full_name``."""

AnalysisStatus = Literal["idle", "running", "completed", "failed"]


# What a positive approval assessment does; a repository with no mode set is ``dry_run``.
ApprovalMode = Literal["off", "dry_run", "approve"]


_TERMINAL_SUCCESS = frozenset({"success", "completed"})
TERMINAL_RUN_FAILURES = frozenset({"error", "failed", "timeout", "interrupted", "cancelled"})


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
    status: AnalysisStatus = "idle"
    custom_prompt: str | None = None
    approval_mode: ApprovalMode | None = None
    analysis_summary: str | None = None
    top_reviewers: list[str] = Field(default_factory=list)
    prs_sampled: int = 0
    reviews_sampled: int = 0
    analysis_thread_id: str | None = None
    analysis_run_id: str | None = None
    continual_cron_id: str | None = None
    error: str | None = None
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

    @property
    def has_saved_prompt(self) -> bool:
        return bool(self.custom_prompt and self.custom_prompt.strip())


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
            if record.status == "running":
                record.status = "completed"
                record.error = None
        if "approval_mode" in update.model_fields_set:
            record.approval_mode = update.approval_mode
        return await self.save(record)

    async def set_continual_cron(self, full_name: RepoFullName, cron_id: str | None) -> ReviewStyle:
        record = await self.get_or_seed(full_name)
        record.continual_cron_id = cron_id
        return await self.save(record)

    async def record_run_started(
        self, full_name: RepoFullName, *, run_id: str | None, created_by: str
    ) -> ReviewStyle:
        record = await self.get_or_seed(full_name, created_by)
        record.analysis_run_id = run_id
        record.created_by = created_by
        return await self.save(record)

    async def mark_running(
        self,
        full_name: RepoFullName,
        *,
        thread_id: str,
        run_id: str | None,
        top_reviewers: list[str],
        prs_sampled: int,
        reviews_sampled: int,
    ) -> ReviewStyle:
        record = await self.get_or_seed(full_name)
        record.status = "running"
        record.analysis_thread_id = thread_id
        record.analysis_run_id = run_id
        record.top_reviewers = top_reviewers
        record.prs_sampled = prs_sampled
        record.reviews_sampled = reviews_sampled
        record.error = None
        return await self.save(record)

    async def mark_completed(
        self,
        full_name: RepoFullName,
        *,
        custom_prompt: str | None = None,
        analysis_summary: str | None = None,
        top_reviewers: list[str] | None = None,
        prs_sampled: int | None = None,
        reviews_sampled: int | None = None,
    ) -> ReviewStyle:
        record = await self.get_or_seed(full_name)
        record.status = "completed"
        record.error = None
        if custom_prompt is not None:
            record.custom_prompt = custom_prompt
        if analysis_summary is not None:
            record.analysis_summary = analysis_summary
        if top_reviewers is not None:
            record.top_reviewers = top_reviewers
        if prs_sampled is not None:
            record.prs_sampled = prs_sampled
        if reviews_sampled is not None:
            record.reviews_sampled = reviews_sampled
        return await self.save(record)

    async def mark_failed(self, full_name: RepoFullName, error: str) -> ReviewStyle:
        record = await self.get_or_seed(full_name)
        record.status = "failed"
        record.error = error
        return await self.save(record)

    async def mark_idle(self, full_name: RepoFullName) -> ReviewStyle:
        record = await self.get_or_seed(full_name)
        record.status = "idle"
        record.error = None
        record.analysis_run_id = None
        return await self.save(record)


REVIEW_STYLES = ReviewStyleStore()


async def reconcile_running_status(
    full_name: RepoFullName,
    record: ReviewStyle,
    *,
    run_status: str | None,
    run_missing: bool = False,
    run_error: str | None = None,
) -> ReviewStyle:
    """Clear stale ``running`` when the analyzer run is done or unreachable."""
    if record.status != "running":
        return record

    if run_status in _TERMINAL_SUCCESS:
        if record.has_saved_prompt:
            return await REVIEW_STYLES.mark_completed(full_name)
        return await REVIEW_STYLES.mark_failed(
            full_name,
            "Analysis finished without saving a prompt. Please retry.",
        )

    if run_status in TERMINAL_RUN_FAILURES:
        if record.has_saved_prompt:
            return await REVIEW_STYLES.mark_completed(full_name)
        return await REVIEW_STYLES.mark_failed(
            full_name,
            f"Analysis run failed: {run_error}. Please retry."
            if run_error
            else "Analysis run ended. Please retry.",
        )

    if run_missing:
        if record.has_saved_prompt:
            return await REVIEW_STYLES.mark_completed(full_name)
        return await REVIEW_STYLES.mark_failed(
            full_name,
            "Analysis was interrupted or the run is no longer available. Please retry.",
        )

    return record


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
