"""Per-repository review style profiles in LangGraph Store.

Each record holds a synthesized custom prompt (editable in the dashboard),
analysis metadata, and the status of the background style-analysis run.
"""

import logging
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

from ..store import KeyedRecordStore, now_iso

logger = logging.getLogger(__name__)

REVIEW_STYLES_NAMESPACE: list[str] = ["review_styles"]

AnalysisStatus = Literal["idle", "running", "completed", "failed"]


def normalize_repo_full_name(raw: str) -> str:
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
    return f"{parts[0]}/{parts[1]}"


class ReviewStyleCreate(BaseModel):
    full_name: str = Field(..., description="GitHub repo in owner/name form")

    @field_validator("full_name", mode="before")
    @classmethod
    def _valid_full_name(cls, v: str) -> str:
        return normalize_repo_full_name(v)


class ReviewStylePromptUpdate(BaseModel):
    custom_prompt: str

    @field_validator("custom_prompt")
    @classmethod
    def _non_empty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("custom_prompt cannot be empty")
        return v


def _default_record(full_name: str, created_by: str) -> dict[str, Any]:
    owner, name = full_name.split("/", 1)
    return {
        "full_name": full_name,
        "owner": owner,
        "name": name,
        "status": "idle",
        "custom_prompt": None,
        "analysis_summary": None,
        "top_reviewers": [],
        "prs_sampled": 0,
        "reviews_sampled": 0,
        "analysis_thread_id": None,
        "analysis_run_id": None,
        "continual_cron_id": None,
        "error": None,
        "created_by": created_by,
        "created_at": now_iso(),
        "updated_at": now_iso(),
    }


_RECORDS = KeyedRecordStore(
    REVIEW_STYLES_NAMESPACE,
    sort_key="full_name",
    default_factory=_default_record,
)


async def get_review_style(full_name: str) -> dict[str, Any] | None:
    return await _RECORDS.get(full_name)


async def list_review_styles() -> list[dict[str, Any]]:
    return await _RECORDS.list()


async def create_review_style(full_name: str, created_by: str) -> dict[str, Any]:
    return await _RECORDS.create(full_name, created_by)


async def update_review_style(full_name: str, patch: dict[str, Any]) -> dict[str, Any]:
    return await _RECORDS.update(full_name, patch)


def has_saved_prompt(record: dict[str, Any]) -> bool:
    prompt = record.get("custom_prompt")
    return isinstance(prompt, str) and bool(prompt.strip())


async def set_custom_prompt(full_name: str, custom_prompt: str) -> dict[str, Any]:
    existing = await get_review_style(full_name)
    patch: dict[str, Any] = {"custom_prompt": custom_prompt}
    if existing and existing.get("status") == "running":
        patch["status"] = "completed"
        patch["error"] = None
    return await update_review_style(full_name, patch)


async def reconcile_running_status(
    full_name: str,
    record: dict[str, Any],
    *,
    run_status: str | None,
    run_missing: bool = False,
) -> dict[str, Any]:
    """Clear stale ``running`` when the analyzer run is done or unreachable."""
    if record.get("status") != "running":
        return record

    terminal_success = frozenset({"success", "completed"})
    terminal_failure = frozenset({"error", "failed", "timeout", "interrupted", "cancelled"})

    if run_status in terminal_success:
        if has_saved_prompt(record):
            return await update_review_style(full_name, {"status": "completed", "error": None})
        return await mark_analysis_failed(
            full_name,
            "Analysis finished without saving a prompt. Please retry.",
        )

    if run_status in terminal_failure:
        if has_saved_prompt(record):
            return await update_review_style(full_name, {"status": "completed", "error": None})
        return await mark_analysis_failed(full_name, "Analysis run ended. Please retry.")

    if run_missing:
        if has_saved_prompt(record):
            return await update_review_style(full_name, {"status": "completed", "error": None})
        return await mark_analysis_failed(
            full_name,
            "Analysis was interrupted or the run is no longer available. Please retry.",
        )

    return record


async def delete_review_style(full_name: str) -> None:
    await _RECORDS.delete(full_name)


async def mark_analysis_running(
    full_name: str,
    *,
    thread_id: str,
    run_id: str | None,
    top_reviewers: list[str],
    prs_sampled: int,
    reviews_sampled: int,
) -> dict[str, Any]:
    return await update_review_style(
        full_name,
        {
            "status": "running",
            "analysis_thread_id": thread_id,
            "analysis_run_id": run_id,
            "top_reviewers": top_reviewers,
            "prs_sampled": prs_sampled,
            "reviews_sampled": reviews_sampled,
            "error": None,
        },
    )


async def mark_analysis_completed(
    full_name: str,
    *,
    custom_prompt: str,
    analysis_summary: str,
    top_reviewers: list[str],
    prs_sampled: int,
    reviews_sampled: int,
) -> dict[str, Any]:
    return await update_review_style(
        full_name,
        {
            "status": "completed",
            "custom_prompt": custom_prompt,
            "analysis_summary": analysis_summary,
            "top_reviewers": top_reviewers,
            "prs_sampled": prs_sampled,
            "reviews_sampled": reviews_sampled,
            "error": None,
        },
    )


async def mark_analysis_failed(full_name: str, error: str) -> dict[str, Any]:
    return await update_review_style(full_name, {"status": "failed", "error": error})


async def get_repo_custom_prompt(owner: str, repo: str) -> str | None:
    """Return the custom prompt supplement for a repo, if configured.

    Fail-soft on purpose: this runs while the reviewer assembles its system
    prompt, and a store blip should cost the run its style supplement, not the
    whole review.
    """
    full_name = f"{owner}/{repo}"
    try:
        record = await get_review_style(full_name)
    except Exception:
        logger.warning("review style lookup failed for %s", full_name, exc_info=True)
        return None
    if not record:
        return None
    prompt = record.get("custom_prompt")
    if isinstance(prompt, str) and prompt.strip():
        return prompt.strip()
    return None
